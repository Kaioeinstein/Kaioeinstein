"""Teste de carga do servidor EINSTEIN REMOTE.

    python tests/load/loadtest.py --agents 100 --clients 10 --seconds 30

Sobe um servidor real (uvicorn, processo separado), cria N computadores simulados que
enviam métricas completas a cada segundo e M painéis conectados recebendo tudo em tempo
real. Durante o teste, os painéis enviam comandos "ping" e o script mede:
  * latência de comando ida e volta (p50 / p95 / máx)
  * atraso de entrega de métricas ao painel (p50 / p95)
  * mensagens entregues por segundo
  * CPU e memória do processo do servidor
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import psutil
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[2]
XHR = {"X-Requested-With": "EinsteinRemote", "Content-Type": "application/json"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http(base, path, body=None, token=None):
    h = dict(XHR)
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=h,
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fake_metrics(i: int) -> dict:
    """Amostra no mesmo formato e tamanho do agente real (8 núcleos, 3 discos)."""
    cpu = random.uniform(2, 60)
    return {"ts": time.time(), "cpu": {"percent": cpu, "per_core": [random.uniform(0, 100) for _ in range(8)], "freq_mhz": 3600},
            "memory": {"total": 16 * 2**30, "used": 8 * 2**30, "available": 8 * 2**30, "percent": random.uniform(30, 70), "swap_percent": 3},
            "disks": [{"mount": f"{d}:\\", "fstype": "NTFS", "total": 2**40, "used": 2**39, "free": 2**39, "percent": 50.0, "system": d == "C"} for d in "CDE"],
            "network": {"down_bps": random.uniform(0, 1e6), "up_bps": random.uniform(0, 1e5), "total_recv": 10**10, "total_sent": 10**9},
            "gpu": [], "temperatures": {"max": None, "sensors": []}, "uptime": 3600 + i, "process_count": 250}


async def agent(base_ws, creds, stop: asyncio.Event, stats: dict):
    async with connect(base_ws + "/ws/agent", max_size=2**24) as ws:
        await ws.send(json.dumps({"type": "auth", "device_id": creds["device_id"], "secret": creds["device_secret"],
                                  "info": {"hostname": creds["name"], "os": "Windows 11", "agent_version": "load"}}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"

        async def sender():
            i = 0
            while not stop.is_set():
                await ws.send(json.dumps({"type": "metrics", "data": fake_metrics(i)}))
                stats["sent"] += 1
                i += 1
                await asyncio.sleep(1 + random.uniform(-0.05, 0.05))

        async def receiver():
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "command":  # responde como o agente real
                    await ws.send(json.dumps({"type": "result", "id": msg["id"], "ok": True, "data": {"pong": time.time()}}))

        t = asyncio.create_task(receiver())
        await sender()
        t.cancel()


async def panel(base_ws, token, device_ids, stop: asyncio.Event, stats: dict):
    async with connect(base_ws + "/ws/client", max_size=2**26) as ws:
        await ws.send(json.dumps({"type": "auth", "token": token}))
        pending = {}

        async def pinger():
            n = 0
            while not stop.is_set():
                await asyncio.sleep(0.5)
                n += 1
                rid = f"p{n}"
                pending[rid] = time.perf_counter()
                await ws.send(json.dumps({"type": "command", "request_id": rid, "device_id": random.choice(device_ids), "action": "ping"}))

        acks = {}
        t = asyncio.create_task(pinger())
        try:
            while not stop.is_set():
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 1))
                except asyncio.TimeoutError:
                    continue
                kind = msg.get("type")
                if kind == "metrics":
                    stats["delivered"] += 1
                    stats["lag"].append(time.time() - msg["data"]["ts"])
                elif kind == "ack":
                    acks[msg["id"]] = msg["request_id"]
                elif kind == "result":
                    rid = acks.pop(msg.get("id"), None) or msg.get("request_id")
                    if rid in pending:
                        stats["rtt"].append((time.perf_counter() - pending.pop(rid)) * 1000)
                    if not msg.get("ok"):
                        stats["errors"] += 1
        finally:
            t.cancel()


async def stalled_panel(base_ws, token, stop: asyncio.Event):
    """Painel que conecta e PARA de ler (celular com rede travada): o buffer TCP enche."""
    import base64 as b64

    host = base_ws[5:]
    reader, writer = await asyncio.open_connection(*host.split(":"))
    key = b64.b64encode(os.urandom(16)).decode()
    writer.write((f"GET /ws/client HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    await writer.drain()
    await reader.readuntil(b"\r\n\r\n")
    payload = json.dumps({"type": "auth", "token": token}).encode()
    mask = os.urandom(4)
    header = bytes([0x81, 0x80 | 126]) + len(payload).to_bytes(2, "big") + mask
    writer.write(header + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
    await writer.drain()
    writer.transport.pause_reading()  # nunca mais lê nada
    await stop.wait()
    writer.close()


def pct(values, p):
    if not values:
        return float("nan")
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))]


async def main(n_agents: int, n_clients: int, seconds: int, stalled: int = 0) -> dict:
    tmp = tempfile.mkdtemp(prefix="einstein-load-")
    port = free_port()
    base, base_ws = f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}"
    env = dict(os.environ, EINSTEIN_DATA_DIR=tmp)
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT / "server"),
                               "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"], env=env)
    try:
        for _ in range(100):
            try:
                http(base, "/api/health")
                break
            except OSError:
                await asyncio.sleep(0.1)
        token = http(base, "/api/auth/setup", {"username": "carga", "password": "Carga#Teste2026"})["access_token"]
        devices = []
        for i in range(n_agents):
            code = http(base, "/api/pairing-codes", {"device_name": f"PC {i:03d}"}, token)["code"]
            creds = http(base, "/api/agent/pair", {"code": code, "hostname": f"pc{i}", "os": "Windows 11"})
            devices.append({**creds, "name": f"pc{i}"})

        proc = psutil.Process(server.pid)
        proc.cpu_percent(None)
        stop = asyncio.Event()
        astats = {"sent": 0}
        cstats = {"delivered": 0, "lag": [], "rtt": [], "errors": 0}
        tasks = [asyncio.create_task(agent(base_ws, d, stop, astats)) for d in devices]
        await asyncio.sleep(2)
        ids = [d["device_id"] for d in devices]
        tasks += [asyncio.create_task(stalled_panel(base_ws, token, stop)) for _ in range(stalled)]
        tasks += [asyncio.create_task(panel(base_ws, token, ids, stop, cstats)) for _ in range(n_clients)]
        cpu_samples, rss = [], []
        t0 = time.time()
        while time.time() - t0 < seconds:
            await asyncio.sleep(1)
            cpu_samples.append(proc.cpu_percent(None))
            rss.append(proc.memory_info().rss)
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.time() - t0
        online = len(http(base, "/api/devices", token=token))
        return {
            "agents": n_agents, "clients": n_clients, "stalled_clients": stalled, "seconds": seconds, "devices_listed": online,
            "metrics_sent_per_s": round(astats["sent"] / (elapsed + 2), 1),
            "metrics_delivered_per_s": round(cstats["delivered"] / elapsed, 1),
            "expected_delivered_per_s": n_agents * n_clients,
            "delivery_lag_ms_p50": round(pct(cstats["lag"], .5) * 1000, 1), "delivery_lag_ms_p95": round(pct(cstats["lag"], .95) * 1000, 1),
            "command_rtt_ms_p50": round(pct(cstats["rtt"], .5), 1), "command_rtt_ms_p95": round(pct(cstats["rtt"], .95), 1),
            "command_rtt_ms_max": round(max(cstats["rtt"] or [0]), 1), "commands": len(cstats["rtt"]), "command_errors": cstats["errors"],
            "server_cpu_avg_pct": round(statistics.mean(cpu_samples[2:] or cpu_samples), 1),
            "server_rss_mb_max": round(max(rss) / 2**20, 1),
        }
    finally:
        server.terminate()
        server.wait(10)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", type=int, default=50)
    ap.add_argument("--clients", type=int, default=5)
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--stalled", type=int, default=0, help="painéis que conectam e param de ler")
    a = ap.parse_args()
    print(json.dumps(asyncio.run(main(a.agents, a.clients, a.seconds, a.stalled)), indent=2, ensure_ascii=False))
