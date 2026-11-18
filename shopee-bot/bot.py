#!/usr/bin/env python3
"""
Bot de postagem automática de vídeos na Shopee Vídeo (afiliados)
controlando o celular Android pelo cabo USB (ADB + uiautomator2).

Uso:
    python bot.py testar        -> verifica conexão com o celular
    python bot.py inspecionar   -> lista os botões/textos da tela atual (para ajustar o fluxo)
    python bot.py postar        -> posta UM vídeo agora
    python bot.py rodar         -> modo automático (posta vários no dia, com intervalos)
"""
import csv
import datetime as dt
import json
import logging
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))
HISTORICO = BASE / "postados.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(BASE / "bot.log", encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("shopee-bot")


# ---------------------------------------------------------------- utilitários
def adb(*args):
    cmd = ["adb"]
    if CONFIG.get("serial"):
        cmd += ["-s", CONFIG["serial"]]
    cmd += list(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


def conectar():
    import uiautomator2 as u2
    d = u2.connect(CONFIG.get("serial") or None)
    d.implicitly_wait(CONFIG.get("timeout_elemento", 15))
    log.info("Conectado: %s %s (Android %s)",
             d.info.get("productName"), d.device_info.get("model", ""), d.device_info.get("version", ""))
    return d


def carregar_historico():
    if HISTORICO.exists():
        return json.loads(HISTORICO.read_text(encoding="utf-8"))
    return []


def salvar_historico(h):
    HISTORICO.write_text(json.dumps(h, ensure_ascii=False, indent=2), encoding="utf-8")


def carregar_fila():
    """Lê a planilha e devolve os vídeos ainda não postados."""
    postados = {p["arquivo"] for p in carregar_historico()}
    pasta = BASE / CONFIG["pasta_videos"]
    fila = []
    with open(BASE / CONFIG["planilha"], encoding="utf-8-sig") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            arq = (linha.get("arquivo") or "").strip()
            if not arq or arq in postados:
                continue
            if not (pasta / arq).exists():
                log.warning("Vídeo não encontrado, pulando: %s", arq)
                continue
            fila.append({"arquivo": arq,
                         "legenda": (linha.get("legenda") or "").strip(),
                         "produto": (linha.get("produto") or "").strip()})
    return fila


def postados_hoje():
    hoje = dt.date.today().isoformat()
    return sum(1 for p in carregar_historico() if p["data"].startswith(hoje))


def dentro_do_horario():
    agora = dt.datetime.now().strftime("%H:%M")
    return CONFIG["horario_inicio"] <= agora <= CONFIG["horario_fim"]


# ---------------------------------------------------------------- celular
def enviar_video(d, arquivo):
    """Copia o vídeo pro celular e força ele aparecer como o mais recente na galeria."""
    local = BASE / CONFIG["pasta_videos"] / arquivo
    destino_dir = CONFIG["pasta_celular"]
    # nome com timestamp para ser o mais novo da galeria
    nome = f"shopee_{int(time.time())}{local.suffix}"
    destino = f"{destino_dir}/{nome}"
    d.shell(f"mkdir -p {destino_dir}")
    log.info("Enviando %s -> %s", arquivo, destino)
    d.push(str(local), destino)
    d.shell(f"touch {destino}")
    d.shell(f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://{destino}")
    time.sleep(3)
    return destino


def limpar_video_celular(d, caminho):
    d.shell(f"rm -f {caminho}")
    d.shell(f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://{caminho}")


def pausa_humana(a=0.6, b=1.8):
    time.sleep(random.uniform(a, b))


def selecionar_primeiro_da_galeria(d):
    """Tenta clicar no primeiro item (mais recente) da grade de mídia."""
    for sel in (dict(className="androidx.recyclerview.widget.RecyclerView"),
                dict(className="android.widget.GridView"),
                dict(className="android.support.v7.widget.RecyclerView")):
        grade = d(**sel)
        if grade.exists(timeout=3):
            filho = grade.child(clickable=True)
            if filho.exists:
                filho[0].click()
                return
            grade.child(index=0).click()
            return
    # fallback: canto superior esquerdo da área de mídia
    w, h = d.window_size()
    d.click(int(w * 0.17), int(h * 0.30))


def digitar(d, texto):
    campo = d(focused=True)
    if campo.exists(timeout=3):
        campo.set_text(texto)
    else:
        edit = d(className="android.widget.EditText")
        if edit.exists(timeout=5):
            edit[0].click()
            pausa_humana()
            edit[0].set_text(texto)
        else:
            d.send_keys(texto, clear=True)


def executar_fluxo(d, item):
    timeout = CONFIG.get("timeout_elemento", 15)
    for i, passo in enumerate(CONFIG["fluxo"], 1):
        opcional = passo.get("opcional", False)
        acao = {k: v for k, v in passo.items() if k != "opcional"}
        nome, valor = next(iter(acao.items()))
        log.info("Passo %d: %s = %s", i, nome, valor)
        ok = True
        if nome == "abrir_app":
            d.app_stop(CONFIG["pacote_shopee"])
            d.app_start(CONFIG["pacote_shopee"])
        elif nome == "esperar":
            time.sleep(float(valor))
        elif nome == "voltar":
            d.press("back")
        elif nome == "clicar_xy":
            d.click(*valor)
        elif nome == "primeiro_da_galeria":
            selecionar_primeiro_da_galeria(d)
        elif nome == "digitar":
            digitar(d, str(valor).format(**item))
        elif nome in ("clicar_texto", "clicar_desc", "clicar_id"):
            if nome == "clicar_texto":
                el = d(text=valor)
                if not el.exists(timeout=timeout if not opcional else 4):
                    el = d(textContains=valor)
            elif nome == "clicar_desc":
                el = d(description=valor)
                if not el.exists(timeout=timeout if not opcional else 4):
                    el = d(descriptionContains=valor)
            else:
                el = d(resourceId=valor)
            if el.exists(timeout=2 if opcional else timeout):
                el.click()
            else:
                ok = False
        else:
            raise ValueError(f"Ação desconhecida no config: {nome}")

        if not ok:
            if opcional:
                log.info("  (opcional, não encontrado — seguindo)")
            else:
                print_tela(d, f"falha_passo_{i}")
                raise RuntimeError(f"Não encontrei '{valor}' no passo {i}. Rode 'python bot.py inspecionar'.")
        pausa_humana()


def print_tela(d, nome):
    pasta = BASE / "erros"
    pasta.mkdir(exist_ok=True)
    arq = pasta / f"{dt.datetime.now():%Y%m%d_%H%M%S}_{nome}.png"
    try:
        d.screenshot(str(arq))
        log.info("Print salvo em %s", arq)
    except Exception as e:  # noqa: BLE001
        log.warning("Não consegui tirar print: %s", e)


# ---------------------------------------------------------------- comandos
def cmd_testar():
    print(adb("devices"))
    d = conectar()
    print("Tela:", d.window_size(), "| App atual:", d.app_current())
    print("OK! Celular pronto.")


def cmd_inspecionar():
    d = conectar()
    import xml.etree.ElementTree as ET
    root = ET.fromstring(d.dump_hierarchy())
    print(f"\nApp: {d.app_current().get('package')}\n")
    print(f"{'TEXTO':35} | {'DESCRIÇÃO':30} | {'ID':45} | CENTRO")
    print("-" * 130)
    for n in root.iter("node"):
        t, desc, rid = n.get("text", ""), n.get("content-desc", ""), n.get("resource-id", "")
        if not (t or desc) and n.get("clickable") != "true":
            continue
        b = n.get("bounds", "[0,0][0,0]").replace("][", ",").strip("[]").split(",")
        x1, y1, x2, y2 = map(int, b)
        print(f"{t[:35]:35} | {desc[:30]:30} | {rid[-45:]:45} | [{(x1+x2)//2}, {(y1+y2)//2}]")
    print("\nUse esses textos/IDs/coordenadas no 'fluxo' do config.yaml.")


def postar_um(d=None):
    fila = carregar_fila()
    if not fila:
        log.info("Nenhum vídeo novo na fila. Adicione vídeos em '%s' e linhas em '%s'.",
                 CONFIG["pasta_videos"], CONFIG["planilha"])
        return False
    item = fila[0]
    d = d or conectar()
    d.screen_on()
    d.unlock()
    caminho = enviar_video(d, item["arquivo"])
    try:
        executar_fluxo(d, item)
    except Exception:
        log.exception("Falha ao postar %s", item["arquivo"])
        print_tela(d, "erro")
        d.press("home")
        limpar_video_celular(d, caminho)
        return False
    limpar_video_celular(d, caminho)
    h = carregar_historico()
    h.append({**item, "data": dt.datetime.now().isoformat(timespec="seconds")})
    salvar_historico(h)
    postados_dir = BASE / CONFIG["pasta_videos"] / "postados"
    postados_dir.mkdir(exist_ok=True)
    shutil.move(str(BASE / CONFIG["pasta_videos"] / item["arquivo"]), postados_dir / item["arquivo"])
    log.info("✅ Postado: %s", item["arquivo"])
    d.press("home")
    return True


def cmd_rodar():
    log.info("Modo automático iniciado. Ctrl+C para parar.")
    falhas = 0
    while True:
        if not dentro_do_horario():
            log.info("Fora do horário (%s-%s). Aguardando...", CONFIG["horario_inicio"], CONFIG["horario_fim"])
            time.sleep(600)
            continue
        if postados_hoje() >= CONFIG["limite_por_dia"]:
            log.info("Limite diário atingido (%d). Aguardando amanhã...", CONFIG["limite_por_dia"])
            time.sleep(1800)
            continue
        try:
            ok = postar_um()
        except Exception:
            log.exception("Erro geral")
            ok = False
        if ok is False and not carregar_fila():
            time.sleep(900)
            continue
        falhas = 0 if ok else falhas + 1
        if falhas >= 3:
            log.error("3 falhas seguidas. Parando para você conferir os prints em 'erros/'.")
            break
        espera = random.randint(CONFIG["intervalo_min_minutos"], CONFIG["intervalo_max_minutos"])
        log.info("Próxima postagem em %d minutos.", espera)
        time.sleep(espera * 60)


if __name__ == "__main__":
    comandos = {"testar": cmd_testar, "inspecionar": cmd_inspecionar,
                "postar": lambda: postar_um(), "rodar": cmd_rodar}
    if len(sys.argv) < 2 or sys.argv[1] not in comandos:
        print(__doc__)
        sys.exit(1)
    comandos[sys.argv[1]]()
