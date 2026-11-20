"""Arquivos: navegação e transferência restritas às pastas liberadas pelo dono do PC.

Regras de segurança (aplicadas AQUI, no próprio computador — o servidor não é confiável
para isso):
* Nada é acessível até o dono liberar pastas (bandeja › "Pastas liberadas…" ou
  ``einstein-agent folders add``). Cada pasta é somente leitura ou leitura e escrita.
* Caminhos chegam como (índice da pasta, partes relativas). Cada parte é validada
  (sem ``..``, separadores, ``:`` de fluxo alternativo, nomes reservados do Windows,
  caracteres de controle) e o caminho final é resolvido com ``realpath``: links
  simbólicos/junções que apontem para fora da pasta liberada são recusados.
* Upload grava em arquivo temporário e só renomeia após conferir tamanho e SHA-256.
  Nunca sobrescreve sem pedido explícito (gera "nome (1).ext").
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import AgentConfig

log = logging.getLogger("einstein.files")
WINDOWS = sys.platform == "win32"
CHUNK = 256 * 1024
MAX_ENTRIES = 5000
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(\..*)?$", re.I)
_BAD_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


class FileError(Exception):
    pass


# ----------------------------------------------------------------- pastas liberadas
def normalize_roots(raw) -> list[dict]:
    out = []
    for r in raw or []:
        if isinstance(r, str):
            r = {"path": r, "write": False}
        if isinstance(r, dict) and r.get("path"):
            out.append({"path": str(r["path"]), "write": bool(r.get("write"))})
    return out


def roots() -> list[dict]:
    return normalize_roots(AgentConfig.load().allowed_roots)


def add_root(path: str, write: bool) -> dict:
    p = Path(path).expanduser()
    try:
        real = Path(os.path.realpath(p))
    except OSError as exc:
        raise FileError(f"Pasta inválida: {exc}") from None
    if not real.is_dir():
        raise FileError("A pasta não existe.")
    if real.parent == real:
        raise FileError("Por segurança, não é possível liberar a raiz de um disco inteiro. Escolha uma pasta.")
    home = Path.home().resolve()
    if real == home:
        raise FileError("Por segurança, libere pastas específicas (ex.: Documentos), não a pasta do usuário inteira.")
    cfg = AgentConfig.load()
    current = normalize_roots(cfg.allowed_roots)
    current = [r for r in current if os.path.normcase(os.path.realpath(r["path"])) != os.path.normcase(str(real))]
    entry = {"path": str(real), "write": bool(write)}
    current.append(entry)
    cfg.allowed_roots = current
    cfg.save()
    log.warning("Pasta liberada para o painel: %s (%s)", real, "leitura e escrita" if write else "somente leitura")
    return entry


def remove_root(path_or_index) -> bool:
    cfg = AgentConfig.load()
    current = normalize_roots(cfg.allowed_roots)
    if isinstance(path_or_index, int) or str(path_or_index).isdigit():
        i = int(path_or_index)
        if not 0 <= i < len(current):
            return False
        removed = current.pop(i)
    else:
        key = os.path.normcase(os.path.realpath(str(path_or_index)))
        keep = [r for r in current if os.path.normcase(os.path.realpath(r["path"])) != key]
        if len(keep) == len(current):
            return False
        removed, current = {"path": path_or_index}, keep
    cfg.allowed_roots = current
    cfg.save()
    log.warning("Pasta removida do painel: %s", removed["path"])
    return True


# ----------------------------------------------------------------- validação de caminhos
def check_name(name: str) -> str:
    if not isinstance(name, str):
        raise FileError("Nome inválido.")
    if name in ("", ".", "..") or len(name) > 255:
        raise FileError("Nome inválido.")
    if _BAD_CHARS.search(name):
        raise FileError(f"Nome com caractere não permitido: {name!r}")
    if name.endswith((" ", ".")) or _RESERVED.match(name):
        raise FileError(f"Nome reservado pelo Windows: {name!r}")
    return name


def split_rel(rel) -> list[str]:
    if rel in (None, "", "/"):
        return []
    if not isinstance(rel, str) or len(rel) > 4096:
        raise FileError("Caminho inválido.")
    if rel.startswith(("/", "\\")) or re.match(r"^[a-zA-Z]:", rel) or rel.startswith("\\\\"):
        raise FileError("Caminho absoluto não é permitido.")
    return [check_name(p) for p in rel.replace("\\", "/").split("/") if p != ""]


def _within(base: str, target: str) -> bool:
    b, t = os.path.normcase(base), os.path.normcase(target)
    try:
        return os.path.commonpath([b, t]) == b
    except ValueError:  # discos diferentes no Windows
        return False


@dataclass
class Resolved:
    root_index: int
    root: dict
    base: str  # raiz real
    path: str  # caminho real resolvido
    parts: list[str] = field(default_factory=list)

    @property
    def rel(self) -> str:
        return "/".join(self.parts)


def resolve(root_index, rel) -> Resolved:
    rs = roots()
    try:
        i = int(root_index)
    except (TypeError, ValueError):
        raise FileError("Pasta liberada inválida.") from None
    if not 0 <= i < len(rs):
        raise FileError("Esta pasta não está (mais) liberada no computador.")
    root = rs[i]
    base = os.path.realpath(root["path"])
    if not os.path.isdir(base):
        raise FileError("A pasta liberada não existe mais no computador.")
    parts = split_rel(rel)
    target = os.path.realpath(os.path.join(base, *parts))
    if not _within(base, target):
        raise FileError("Acesso negado: o caminho sai da pasta liberada (link simbólico ou junção).")
    return Resolved(i, root, base, target, parts)


# ----------------------------------------------------------------- consultas
def _hidden(entry: os.DirEntry, st) -> bool:
    if entry.name.startswith("."):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & (getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2) | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 4)))


def list_roots() -> dict:
    out = []
    for i, r in enumerate(roots()):
        exists = os.path.isdir(r["path"])
        free = shutil.disk_usage(r["path"]).free if exists else None
        out.append({"index": i, "name": Path(r["path"]).name or r["path"], "path": r["path"], "write": r["write"],
                    "exists": exists, "free": free})
    return {"roots": out}


def list_dir(root_index, rel) -> dict:
    res = resolve(root_index, rel)
    if not os.path.isdir(res.path):
        raise FileError("Não é uma pasta.")
    entries, truncated = [], False
    try:
        with os.scandir(res.path) as it:
            for e in it:
                if len(entries) >= MAX_ENTRIES:
                    truncated = True
                    break
                try:
                    st = e.stat(follow_symlinks=True)
                    real = os.path.realpath(e.path)
                    if not _within(res.base, real):
                        continue  # link que aponta para fora: nem aparece
                    is_dir = stat.S_ISDIR(st.st_mode)
                    entries.append({"name": e.name, "dir": is_dir, "size": None if is_dir else st.st_size,
                                    "mtime": st.st_mtime, "hidden": _hidden(e, st)})
                except OSError:
                    continue
    except PermissionError:
        raise FileError("Sem permissão para ler esta pasta.") from None
    entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return {"root": res.root_index, "path": res.rel, "write": res.root["write"], "entries": entries, "truncated": truncated}


def mkdir(root_index, rel, name) -> dict:
    res = resolve(root_index, rel)
    if not res.root["write"]:
        raise FileError("Esta pasta foi liberada somente para leitura.")
    target = os.path.join(res.path, check_name(name))
    if os.path.exists(target):
        raise FileError("Já existe um item com esse nome.")
    os.mkdir(target)
    log.warning("Pasta criada pelo painel: %s", target)
    return {"path": "/".join(res.parts + [name])}


def unique_name(directory: str, name: str) -> str:
    if not os.path.exists(os.path.join(directory, name)):
        return name
    stem, ext = os.path.splitext(name)
    for n in range(1, 10000):
        cand = f"{stem} ({n}){ext}"
        if not os.path.exists(os.path.join(directory, cand)):
            return cand
    raise FileError("Não foi possível escolher um nome livre.")


# ----------------------------------------------------------------- transferências
class Download:
    """Lê um arquivo em blocos; o hash é calculado durante a leitura."""

    def __init__(self, root_index, rel):
        res = resolve(root_index, rel)
        if not os.path.isfile(res.path):
            raise FileError("Não é um arquivo.")
        self.path = res.path
        self.name = os.path.basename(res.path)
        self.size = os.path.getsize(res.path)
        self.sha = hashlib.sha256()
        try:
            self.fh = open(res.path, "rb")
        except PermissionError:
            raise FileError("Sem permissão para ler o arquivo (pode estar aberto por outro programa).") from None

    def read(self) -> bytes:
        data = self.fh.read(CHUNK)
        self.sha.update(data)
        return data

    def close(self) -> None:
        self.fh.close()


class Upload:
    """Grava em arquivo temporário; confirma tamanho e SHA-256 antes de colocar no lugar."""

    def __init__(self, root_index, rel, name, size, overwrite, tid: str):
        res = resolve(root_index, rel)
        if not res.root["write"]:
            raise FileError("Esta pasta foi liberada somente para leitura.")
        if not os.path.isdir(res.path):
            raise FileError("A pasta de destino não existe.")
        name = check_name(name)
        try:
            self.expected = int(size)
        except (TypeError, ValueError):
            raise FileError("Tamanho inválido.") from None
        free = shutil.disk_usage(res.path).free
        if self.expected > free - 50 * 2**20:
            raise FileError("Espaço insuficiente no disco do computador.")
        self.dir = res.path
        self.final = name if overwrite else unique_name(res.path, name)
        self.overwrite = bool(overwrite)
        self.tmp = os.path.join(res.path, f".{self.final}.einstein-part-{tid[:8]}")
        self.fh = open(self.tmp, "wb")
        self.sha = hashlib.sha256()
        self.received = 0
        self.rel_dir = res.rel

    def write(self, data: bytes) -> None:
        self.received += len(data)
        if self.received > self.expected:
            raise FileError("Recebido mais dados que o anunciado.")
        self.fh.write(data)
        self.sha.update(data)

    def finish(self, sha256: str) -> dict:
        self.fh.flush()
        os.fsync(self.fh.fileno())
        self.fh.close()
        digest = self.sha.hexdigest()
        if self.received != self.expected:
            self.abort()
            raise FileError(f"Arquivo incompleto ({self.received} de {self.expected} bytes).")
        if sha256 and digest != sha256:
            self.abort()
            raise FileError("Falha na verificação de integridade (SHA-256 diferente). O arquivo foi descartado.")
        final_path = os.path.join(self.dir, self.final)
        if os.path.exists(final_path) and not self.overwrite:
            self.final = unique_name(self.dir, self.final)
            final_path = os.path.join(self.dir, self.final)
        os.replace(self.tmp, final_path)
        log.warning("Arquivo recebido pelo painel: %s (%d bytes, sha256 %s)", final_path, self.received, digest[:16])
        return {"name": self.final, "size": self.received, "sha256": digest,
                "path": "/".join(p for p in (self.rel_dir, self.final) if p)}

    def abort(self) -> None:
        try:
            self.fh.close()
        except Exception:
            pass
        try:
            os.remove(self.tmp)
        except OSError:
            pass


def cleanup_partials(max_age: float = 24 * 3600) -> int:
    """Remove temporários esquecidos (transferência interrompida) nas pastas liberadas."""
    removed = 0
    for r in roots():
        if not r["write"] or not os.path.isdir(r["path"]):
            continue
        for dirpath, _dirs, files in os.walk(r["path"]):
            for f in files:
                if ".einstein-part-" in f:
                    p = os.path.join(dirpath, f)
                    try:
                        if time.time() - os.path.getmtime(p) > max_age:
                            os.remove(p)
                            removed += 1
                    except OSError:
                        pass
    return removed
