// Arquivos: navegação nas pastas liberadas no PC, download, upload e cópia entre computadores.
import * as API from "./api.js";
import { $, $$, bytes, dateTime, esc, toast } from "./util.js";

const HASH_LIMIT = 256 * 2 ** 20; // acima disso o navegador não calcula o SHA-256 (memória); servidor↔PC sempre verifica

async function sha256Hex(file) {
  if (!crypto?.subtle || file.size > HASH_LIMIT) return null; // crypto.subtle só existe em HTTPS/localhost
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export class FileBrowser {
  /** @param {{device: () => any, devices: () => any[], live: () => any}} ctx */
  constructor(ctx) {
    this.ctx = ctx;
    this.root = 0;
    this.path = "";
    this.roots = [];
    this.data = null;
    this.bind();
  }

  bind() {
    $("#fl-root").addEventListener("change", (e) => { this.root = Number(e.target.value); this.path = ""; this.load(); });
    $("#fl-refresh").addEventListener("click", () => this.load());
    $("#fl-mkdir").addEventListener("click", () => this.mkdir());
    $("#fl-upload").addEventListener("change", (e) => { this.upload([...e.target.files]); e.target.value = ""; });
    $("#fl-crumbs").addEventListener("click", (e) => {
      const a = e.target.closest("[data-path]");
      if (a) { this.path = a.dataset.path; this.load(); }
    });
    $("#fl-list").addEventListener("click", (e) => this.onClick(e));
    const drop = $("#fl-drop");
    drop.addEventListener("dragover", (e) => { if (this.canWrite()) { e.preventDefault(); drop.classList.add("over"); } });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => {
      drop.classList.remove("over");
      if (!this.canWrite()) return;
      e.preventDefault();
      this.upload([...e.dataTransfer.files]);
    });
    $("#copy-form").addEventListener("submit", (e) => { e.preventDefault(); this.doCopy(); });
    $("#copy-device").addEventListener("change", () => this.loadCopyRoots());
    $("#copy-cancel").addEventListener("click", () => $("#copy-dialog").close());
  }

  canWrite() { return !!this.roots[this.root]?.write; }

  // ------------------------------------------------------------ navegação
  async show() {
    const d = this.ctx.device();
    if (this.deviceId !== d?.id) { this.deviceId = d?.id; this.root = 0; this.path = ""; this.roots = []; }
    await this.load(true);
  }

  async load(withRoots = false) {
    const d = this.ctx.device();
    const list = $("#fl-list");
    if (!d) { list.innerHTML = `<p class="muted">Nenhum computador selecionado.</p>`; return; }
    if (!d.online) { this.renderEmpty(`${esc(d.name)} está offline. Os arquivos ficam acessíveis quando o agente reconectar.`); return; }
    try {
      if (withRoots || !this.roots.length) {
        this.roots = (await this.ctx.live().command(d.id, "files.roots")).roots;
        $("#fl-root").innerHTML = this.roots.map((r) => `<option value="${r.index}">${esc(r.name)}${r.write ? "" : " (somente leitura)"}</option>`).join("");
        $("#fl-root").value = String(this.root);
      }
      if (!this.roots.length) {
        this.renderEmpty(`Nenhuma pasta foi liberada em <b>${esc(d.name)}</b>, então o painel não acessa nenhum arquivo.<br>
          No computador: ícone do agente › <b>Pastas liberadas para o painel…</b><br>
          ou no terminal: <code>einstein-agent folders add "C:\\Users\\voce\\Documents" --write</code>`);
        return;
      }
      list.innerHTML = `<p class="muted">Carregando…</p>`;
      this.data = await this.ctx.live().command(d.id, "files.list", { root: this.root, path: this.path });
      this.render();
    } catch (err) {
      list.innerHTML = `<p class="error">${esc(err.message)}</p>`;
    }
  }

  renderEmpty(html) {
    $("#fl-list").innerHTML = `<div class="empty-mon">${html}</div>`;
    $("#fl-crumbs").innerHTML = "";
    $("#fl-meta").textContent = "";
    $$(".write-only").forEach((b) => b.classList.add("hidden"));
    $("#fl-root").classList.toggle("hidden", !this.roots.length);
  }

  render() {
    const { entries, truncated } = this.data;
    const r = this.roots[this.root];
    $("#fl-root").classList.remove("hidden");
    $$(".write-only").forEach((b) => b.classList.toggle("hidden", !r.write));
    const parts = this.path ? this.path.split("/") : [];
    $("#fl-crumbs").innerHTML = [`<a href="#" data-path="">${esc(r.name)}</a>`,
      ...parts.map((p, i) => `<a href="#" data-path="${esc(parts.slice(0, i + 1).join("/"))}">${esc(p)}</a>`)].join(`<span>›</span>`);
    const files = entries.filter((e) => !e.dir);
    $("#fl-meta").textContent = `${entries.length - files.length} pasta(s) · ${files.length} arquivo(s) · ${bytes(files.reduce((s, f) => s + f.size, 0))}`
      + (r.free != null ? ` · livre no disco: ${bytes(r.free)}` : "") + (truncated ? " · lista truncada" : "");
    const others = this.ctx.devices().filter((x) => x.online && x.id !== this.deviceId);
    const up = this.path ? `<div class="fl-row dir" data-dir="${esc(parts.slice(0, -1).join("/"))}"><i class="fl-ic up"></i><b>..</b><span></span><span></span><span></span></div>` : "";
    $("#fl-list").innerHTML = (up + entries.map((e) => {
      const p = [this.path, e.name].filter(Boolean).join("/");
      if (e.dir) return `<div class="fl-row dir ${e.hidden ? "dim" : ""}" data-dir="${esc(p)}"><i class="fl-ic folder"></i><b>${esc(e.name)}</b><span class="hide-sm">—</span><span class="hide-sm">${dateTime(e.mtime)}</span><span></span></div>`;
      return `<div class="fl-row ${e.hidden ? "dim" : ""}"><i class="fl-ic file" data-ext="${esc((e.name.split(".").pop() || "").slice(0, 4).toUpperCase())}"></i>
        <b title="${esc(e.name)}">${esc(e.name)}</b><span class="num">${bytes(e.size)}</span><span class="hide-sm">${dateTime(e.mtime)}</span>
        <span class="fl-act"><button class="btn ghost sm" data-dl="${esc(p)}">Baixar</button>${others.length ? `<button class="btn ghost sm hide-sm" data-copy="${esc(p)}">Copiar para…</button>` : ""}</span></div>`;
    }).join("")) || `<p class="muted">Pasta vazia${r.write ? " — arraste arquivos para cá ou use “Enviar arquivos”." : "."}</p>`;
  }

  onClick(e) {
    const dl = e.target.closest("[data-dl]");
    if (dl) return this.download(dl.dataset.dl);
    const cp = e.target.closest("[data-copy]");
    if (cp) return this.openCopy(cp.dataset.copy);
    const dir = e.target.closest("[data-dir]");
    if (dir) { this.path = dir.dataset.dir; this.load(); }
  }

  async mkdir() {
    const name = prompt("Nome da nova pasta:");
    if (!name) return;
    try {
      await this.ctx.live().command(this.deviceId, "files.mkdir", { root: this.root, path: this.path, name: name.trim() });
      this.load();
    } catch (err) { toast(err.message, "error"); }
  }

  // ------------------------------------------------------------ transferências
  async download(path) {
    try {
      const r = await API.api(`/api/devices/${encodeURIComponent(this.deviceId)}/files/download`, { method: "POST", body: { root: this.root, path } });
      const a = document.createElement("a");
      a.href = r.url; // link de uso único: o navegador baixa com o gerenciador nativo
      a.download = r.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      toast(`Baixando ${r.name} (${bytes(r.size)}). A integridade é conferida durante a transferência.`);
    } catch (err) { toast(err.message, "error"); }
  }

  transferItem(name, size) {
    $("#fl-transfers").classList.remove("hidden");
    const el = document.createElement("div");
    el.className = "fl-tr";
    el.innerHTML = `<div class="fl-tr-top"><b>${esc(name)}</b><span class="muted small">${size ? bytes(size) : ""}</span></div>
      <div class="bar"><i></i></div><small class="muted fl-tr-state">Na fila…</small>`;
    $("#fl-tlist").prepend(el);
    let finished = false; // avisos de progresso atrasados não podem "desfazer" o resultado final
    return {
      progress: (frac, text) => {
        if (finished) return;
        el.querySelector(".bar i").style.width = `${Math.round(frac * 100)}%`;
        el.querySelector(".fl-tr-state").textContent = text;
      },
      done: (text) => { finished = true; el.classList.add("ok"); el.querySelector(".bar i").style.width = "100%"; el.querySelector(".fl-tr-state").textContent = text; },
      fail: (text) => { finished = true; el.classList.add("fail"); el.querySelector(".fl-tr-state").textContent = text; },
    };
  }

  async upload(fileList) {
    if (!this.canWrite()) return toast("Esta pasta foi liberada somente para leitura.", "warning");
    const target = { device: this.deviceId, root: this.root, path: this.path };
    for (const file of fileList) {
      const ui = this.transferItem(file.name, file.size);
      try {
        ui.progress(0, "Calculando verificação de integridade…");
        const sha = await sha256Hex(file);
        const result = await this.send(file, target, sha, ui);
        ui.done(`Enviado e verificado (SHA-256 ${result.sha256.slice(0, 12)}…)${result.name !== file.name ? ` como “${result.name}”` : ""}`);
      } catch (err) {
        ui.fail(err.message);
      }
    }
    if (this.deviceId === target.device && this.root === target.root && this.path === target.path) this.load();
  }

  send(file, target, sha, ui) {
    return new Promise((resolve, reject) => {
      const q = new URLSearchParams({ root: target.root, path: target.path, name: file.name });
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/devices/${encodeURIComponent(target.device)}/files/upload?${q}`);
      xhr.setRequestHeader("Authorization", `Bearer ${API.getToken()}`);
      xhr.setRequestHeader("X-Requested-With", "EinsteinRemote");
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      if (sha) xhr.setRequestHeader("X-Content-SHA256", sha);
      const t0 = performance.now();
      xhr.upload.onprogress = (e) => {
        if (!e.lengthComputable) return;
        const speed = e.loaded / Math.max(0.001, (performance.now() - t0) / 1000);
        ui.progress(e.loaded / e.total, e.loaded < e.total ? `Enviando ${Math.round((e.loaded / e.total) * 100)}% · ${bytes(speed)}/s` : "Gravando e verificando no computador…");
      };
      xhr.onload = () => {
        let body = {};
        try { body = JSON.parse(xhr.responseText); } catch { /* resposta vazia */ }
        if (xhr.status === 200) resolve(body);
        else reject(new Error(body.detail || `Falha no envio (${xhr.status})`));
      };
      xhr.onerror = () => reject(new Error("Conexão perdida durante o envio."));
      xhr.send(file);
    });
  }

  // ------------------------------------------------------------ cópia entre computadores
  openCopy(path) {
    this.copyPath = path;
    const others = this.ctx.devices().filter((x) => x.online && x.id !== this.deviceId);
    $("#copy-title").textContent = `Copiar “${path.split("/").pop()}”`;
    $("#copy-device").innerHTML = others.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("");
    $("#copy-error").textContent = "";
    $("#copy-dialog").showModal();
    this.loadCopyRoots();
  }

  async loadCopyRoots() {
    const sel = $("#copy-root");
    sel.innerHTML = `<option>Carregando…</option>`;
    try {
      const r = await this.ctx.live().command($("#copy-device").value, "files.roots");
      const writable = r.roots.filter((x) => x.write);
      sel.innerHTML = writable.map((x) => `<option value="${x.index}">${esc(x.name)}</option>`).join("");
      $("#copy-error").textContent = writable.length ? "" : "Esse computador não liberou nenhuma pasta com escrita.";
      $("#copy-ok").disabled = !writable.length;
    } catch (err) {
      sel.innerHTML = "";
      $("#copy-error").textContent = err.message;
      $("#copy-ok").disabled = true;
    }
  }

  async doCopy() {
    const to = $("#copy-device").value;
    const toName = $("#copy-device").selectedOptions[0]?.textContent;
    const name = this.copyPath.split("/").pop();
    $("#copy-dialog").close();
    const ui = this.transferItem(`${name} → ${toName}`, 0);
    ui.progress(0.05, "Copiando entre os computadores…");
    const onProgress = (e) => {
      const m = e.detail;
      if (m.kind === "upload" && m.device_id === to && m.size) ui.progress(m.done / m.size, `Copiando ${Math.round((m.done / m.size) * 100)}% · ${bytes(m.done)} de ${bytes(m.size)}`);
    };
    this.ctx.live().addEventListener("transfer", onProgress);
    try {
      const r = await API.api("/api/files/copy", { method: "POST", body: {
        from_device: this.deviceId, from_root: this.root, from_path: this.copyPath, to_device: to, to_root: Number($("#copy-root").value), to_path: "" } });
      ui.done(`Copiado e verificado para ${toName} como “${r.name}”`);
    } catch (err) {
      ui.fail(err.message);
    } finally {
      this.ctx.live().removeEventListener("transfer", onProgress);
    }
  }
}
