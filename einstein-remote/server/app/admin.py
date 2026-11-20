"""Ferramentas de recuperação executadas NO SERVIDOR (acesso local = confiável).

    python -m app.admin --help                    (rodar com --app-dir/PYTHONPATH em server/)
    python -m app.admin reset-2fa <usuario>       desativa o 2FA (perdeu celular e códigos)
    python -m app.admin reset-password <usuario>  define uma senha temporária e encerra as sessões
    python -m app.admin users                     lista usuários
"""
from __future__ import annotations

import argparse
import getpass
import sys

from . import security
from .config import load_settings
from .db import Database


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.admin")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("users")
    r = sub.add_parser("reset-2fa")
    r.add_argument("username")
    rp = sub.add_parser("reset-password")
    rp.add_argument("username")
    args = p.parse_args(argv)
    db = Database(load_settings().db_path)
    if args.cmd == "users":
        for u in db.list_users():
            print(f"{u['id']:>3}  {u['username']:<20} {u['role']:<7} 2FA: {'sim' if u['totp_enabled'] else 'não'}")
        return 0
    user = db.get_user_by_name(args.username)
    if not user:
        print("Usuário não encontrado.")
        return 1
    if args.cmd == "reset-2fa":
        db.update_user(user["id"], totp_enabled=0, totp_secret=None, totp_last_step=0, recovery_hashes=None)
        n = len(db.revoke_user_sessions(user["id"]))
        db.audit("admin.reset_2fa", detail={"username": user["username"], "via": "console do servidor", "sessions": n})
        print(f"2FA de {user['username']} desativado; {n} sessão(ões) encerrada(s). Ative novamente em Ajustes › Minha conta.")
    else:
        pw = getpass.getpass("Nova senha: ")
        err = security.validate_password_strength(pw)
        if err:
            print(err)
            return 1
        db.update_user(user["id"], password_hash=security.hash_password(pw))
        n = len(db.revoke_user_sessions(user["id"]))
        db.audit("admin.reset_password", detail={"username": user["username"], "via": "console do servidor", "sessions": n})
        print(f"Senha de {user['username']} redefinida; {n} sessão(ões) encerrada(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
