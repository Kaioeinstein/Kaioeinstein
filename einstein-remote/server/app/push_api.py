"""Notificações push (app fechado) — inscrições por aparelho e envio a cada notificação."""
from __future__ import annotations

import asyncio
import logging
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import webpush
from .auth import client_ip, current_user

log = logging.getLogger("einstein.push")
router = APIRouter(prefix="/api/push", tags=["push"])
LEVELS = {"info": 0, "warning": 1, "critical": 2}
ALLOW_HTTP = os.environ.get("EINSTEIN_PUSH_ALLOW_HTTP") == "1"  # somente testes (serviço de push local)


class Keys(BaseModel):
    p256dh: str = Field(min_length=80, max_length=120)
    auth: str = Field(min_length=16, max_length=40)


class Subscription(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2048)
    keys: Keys
    min_level: str = Field(default="warning", pattern="^(info|warning|critical)$")


class Unsubscribe(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2048)


class PushDispatcher:
    def __init__(self, db, vapid: webpush.Vapid):
        self.db = db
        self.vapid = vapid
        self.sent: list[tuple[str, int]] = []  # (endpoint, status) — diagnóstico/testes

    def _visible(self, user: dict, note: dict) -> bool:
        from .hub import role_at_least

        if note.get("device_id"):
            access = self.db.accessible_device_ids(user)
            return access is None or note["device_id"] in access
        return role_at_least(user, "admin")

    def dispatch(self, note: dict) -> None:
        """Agenda o envio sem atrasar quem gerou a notificação."""
        subs = self.db.push_subscriptions()
        if not subs:
            return
        users = {}
        targets = []
        for s in subs:
            u = users.get(s["user_id"]) or users.setdefault(s["user_id"], self.db.get_user(s["user_id"]))
            if u and LEVELS.get(note["level"], 0) >= LEVELS.get(s["min_level"], 1) and self._visible(u, note):
                targets.append(s)
        if targets:
            asyncio.create_task(self._send_all(targets, note))

    async def _send_all(self, subs: list[dict], note: dict) -> list[int]:
        message = {"title": "EINSTEIN REMOTE", "body": note["message"], "level": note["level"], "kind": note.get("kind"),
                   "device_id": note.get("device_id"), "ts": note.get("ts", time.time()), "url": "/", "tag": f"er-{note.get('id', '')}"}
        urgency = "high" if note["level"] == "critical" else "normal"
        results = await asyncio.gather(*(self._send_one(s, message, urgency) for s in subs))
        return results

    async def _send_one(self, sub: dict, message: dict, urgency: str) -> int:
        try:
            status = await asyncio.to_thread(webpush.send, self.vapid, sub, message, 24 * 3600, urgency)
        except Exception as exc:
            log.warning("Falha ao enviar push: %s", exc)
            status = 0
        self.sent.append((sub["endpoint"], status))
        if status in (404, 410):  # inscrição expirada/cancelada no navegador
            self.db.delete_push(sub["endpoint"])
        else:
            self.db.push_result(sub["endpoint"], 200 <= status < 300)
        return status


@router.get("/key")
def public_key(request: Request, user: dict = Depends(current_user)):
    return {"key": request.app.state.push.vapid.public_b64}


@router.post("/subscribe")
def subscribe(body: Subscription, request: Request, user: dict = Depends(current_user)):
    st = request.app.state
    try:
        webpush.check_endpoint(body.endpoint, allow_http=ALLOW_HTTP)
        webpush.b64u_dec(body.keys.p256dh)
        webpush.b64u_dec(body.keys.auth)
    except (ValueError, Exception) as exc:
        raise HTTPException(422, str(exc) or "Inscrição inválida.")
    st.db.save_push(user["id"], body.endpoint, body.keys.p256dh, body.keys.auth, body.min_level, request.headers.get("user-agent"))
    st.db.audit("push.subscribed", user=user, ip=client_ip(request), detail={"min_level": body.min_level})
    return {"ok": True}


@router.post("/unsubscribe")
def unsubscribe(body: Unsubscribe, request: Request, user: dict = Depends(current_user)):
    removed = request.app.state.db.delete_push(body.endpoint, user["id"])
    return {"removed": removed}


@router.get("/subscriptions")
def subscriptions(request: Request, user: dict = Depends(current_user)):
    return request.app.state.db.push_subscriptions(user["id"])


@router.post("/test")
async def test_push(request: Request, user: dict = Depends(current_user)):
    st = request.app.state
    subs = [s for s in st.db.push_subscriptions() if s["user_id"] == user["id"]]
    if not subs:
        raise HTTPException(409, "Nenhum aparelho com notificações push ativadas nesta conta.")
    note = {"id": 0, "level": "critical", "kind": "test", "message": f"Teste de notificação para {user['username']} ✔", "ts": time.time()}
    results = await st.push._send_all(subs, note)
    return {"sent": len(results), "accepted": sum(1 for r in results if 200 <= r < 300), "statuses": results}
