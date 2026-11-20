// Cliente da API REST + canal em tempo real (WebSocket).
// O access token fica apenas em memória; o refresh token vive em cookie httpOnly.

let accessToken = null;
let refreshTimer = null;
export let currentUser = null;

const HEADERS = { "X-Requested-With": "EinsteinRemote" };

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function raw(path, { method = "GET", body, auth = true } = {}) {
  const headers = { ...HEADERS };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const res = await fetch(path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined, credentials: "same-origin" });
  let data = null;
  try { data = await res.json(); } catch { /* sem corpo */ }
  if (!res.ok) {
    let msg = data?.detail ?? res.statusText;
    if (Array.isArray(msg)) msg = msg.map((d) => d.msg).join("; ");
    throw new ApiError(res.status, msg);
  }
  return data;
}

function setSession(data) {
  accessToken = data.access_token;
  currentUser = data.user;
  clearTimeout(refreshTimer);
  // renova 60 s antes de expirar
  refreshTimer = setTimeout(() => refresh().catch(() => onSessionLost()), Math.max(10, data.expires_in - 60) * 1000);
}

let sessionLostHandler = () => {};
export function onSessionLostHandler(fn) { sessionLostHandler = fn; }
function onSessionLost() { accessToken = null; currentUser = null; sessionLostHandler(); }

export async function refresh() {
  const data = await raw("/api/auth/refresh", { method: "POST", auth: false });
  setSession(data);
  return data;
}

export async function api(path, opts = {}) {
  try {
    return await raw(path, opts);
  } catch (e) {
    if (e.status === 401 && opts.auth !== false) {
      try { await refresh(); } catch { onSessionLost(); throw e; }
      return raw(path, opts);
    }
    throw e;
  }
}

export const authStatus = () => raw("/api/auth/status", { auth: false });
export async function login(username, password) { setSession(await raw("/api/auth/login", { method: "POST", body: { username, password }, auth: false })); }
export async function setup(username, password) { setSession(await raw("/api/auth/setup", { method: "POST", body: { username, password }, auth: false })); }
export async function logout() {
  try { await api("/api/auth/logout", { method: "POST" }); } finally { accessToken = null; currentUser = null; clearTimeout(refreshTimer); }
}
export const getToken = () => accessToken;

// ---------------------------------------------------------------- tempo real
export class Live extends EventTarget {
  constructor() {
    super();
    this.ws = null;
    this.backoff = 1000;
    this.closed = false;
    this.reqSeq = 0;
    this.byRequest = new Map(); // request_id -> {resolve, reject, timer}
    this.byId = new Map();      // command id -> request_id
  }

  connect() {
    this.closed = false;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/client`);
    this.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: "auth", token: accessToken }));
    ws.onmessage = (ev) => {
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === "hello") { this.backoff = 1000; this.emit("status", { online: true }); }
      if (msg.type === "ack") {
        const p = this.byRequest.get(msg.request_id);
        if (p) this.byId.set(msg.id, msg.request_id);
      }
      if (msg.type === "result") {
        const reqId = msg.request_id || this.byId.get(msg.id);
        const p = this.byRequest.get(reqId);
        if (p) {
          clearTimeout(p.timer);
          this.byRequest.delete(reqId);
          this.byId.delete(msg.id);
          msg.ok ? p.resolve(msg.data) : p.reject(new Error(msg.error || "Falha no comando"));
        }
      }
      this.emit(msg.type, msg);
    };
    ws.onclose = async (ev) => {
      this.emit("status", { online: false });
      for (const [, p] of this.byRequest) { clearTimeout(p.timer); p.reject(new Error("Conexão perdida")); }
      this.byRequest.clear();
      if (this.closed) return;
      if (ev.code === 4401) {
        try { await refresh(); } catch { onSessionLost(); return; }
      }
      setTimeout(() => this.connect(), this.backoff);
      this.backoff = Math.min(this.backoff * 2, 15000);
    };
  }

  close() { this.closed = true; this.ws?.close(); }

  emit(type, detail) { this.dispatchEvent(new CustomEvent(type, { detail })); }

  command(deviceId, action, params = {}, { confirm = false, timeout = 35000 } = {}) {
    return new Promise((resolve, reject) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return reject(new Error("Sem conexão com o servidor"));
      const request_id = `r${++this.reqSeq}`;
      const timer = setTimeout(() => { this.byRequest.delete(request_id); reject(new Error("Tempo esgotado")); }, timeout);
      this.byRequest.set(request_id, { resolve, reject, timer });
      this.ws.send(JSON.stringify({ type: "command", request_id, device_id: deviceId, action, params, confirm }));
    });
  }
}
