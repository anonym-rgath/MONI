import argparse
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

from .agent.tls import TlsError, TlsState
from .auth import audit, oidc
from .auth.sessions import SessionStore
from .auth.tokens import TokenStore
from .auth.users import UserStore
from .config import Settings
from .fsutil import write_private
from .manager import db, nginx, pairing
from .manager.alert_channels import ChannelStore
from .manager.ca import ManagerPki
from .manager.pairing import PairingError
from .manager.servers import ServerStore
from .secretbox import SecretBox, SecretError, generate_key
from .timeutil import utcnow


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="moni.cli")
    g = p.add_subparsers(dest="group", required=True)
    sec = g.add_parser("secret").add_subparsers(dest="cmd", required=True)
    sec.add_parser("generate")
    sec.add_parser("reencrypt")
    sv = g.add_parser("server").add_subparsers(dest="cmd", required=True)
    a = sv.add_parser("add", allow_abbrev=False)  # sonst würde --token als --token-file gelten
    a.add_argument("--name", required=True)
    a.add_argument("--url", help="beim Kopplungscode optional, wenn er die URL enthält")
    src = a.add_mutually_exclusive_group(required=True)
    src.add_argument("--token-file", help="Datei mit dem Agent-Token, '-' für stdin (Token-Modus)")
    src.add_argument("--pairing-code-file", help="Datei mit dem Kopplungscode, '-' für stdin (mTLS)")
    a.add_argument("--header", action="append", default=[], metavar="NAME=WERT")
    sv.add_parser("list")
    sv.add_parser("remove").add_argument("id", type=int)
    t = g.add_parser("token").add_subparsers(dest="cmd", required=True)
    c = t.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--scope", choices=["read", "admin"], default="read")
    c.add_argument("--expires-days", type=int)
    t.add_parser("list")
    t.add_parser("revoke").add_argument("id")
    u = g.add_parser("user").add_subparsers(dest="cmd", required=True)
    u.add_parser("list")
    rp = u.add_parser("reset-password", allow_abbrev=False)  # Passwort nie als Argument (Shell-History)
    rp.add_argument("email")
    rp.add_argument("--password-file", required=True, help="Datei mit dem neuen Passwort, '-' für stdin")
    u.add_parser("reset-2fa").add_argument("email")
    u.add_parser("unlock").add_argument("email")
    g.add_parser("openapi").add_argument("--role", choices=["agent", "manager"], required=True)
    g.add_parser("tls").add_subparsers(dest="cmd", required=True).add_parser("init")
    pr = g.add_parser("pair").add_subparsers(dest="cmd", required=True)
    pr.add_parser("code")
    pr.add_parser("reset")
    return p


def _read_token(path: str) -> str:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    # genau ein abschließender Zeilenumbruch; alles weitere prüft ServerStore.add
    return text.removesuffix("\n").removesuffix("\r") if text.endswith("\n") else text


_ACTOR = "cli"


def _user(args, settings: Settings, conn, box) -> int:
    users = UserStore(conn, box)
    if args.cmd == "list":
        for u in users.list():
            print(f"{u.id}  {u.email}  {u.role}  {u.kind}  {'deaktiviert' if u.disabled else 'aktiv'}")
        return 0
    user = users.by_email(args.email)
    if user is None:
        print(f"Kein Benutzer mit der E-Mail {args.email}.", file=sys.stderr)
        return 1
    if args.cmd != "unlock" and user.kind != "local":
        raise ValueError("Nur für lokale Konten möglich")
    if args.cmd == "reset-password":  # vor der Transaktion lesen und hashen: stdin und Argon2 dürfen die Schreibsperre nicht halten
        try:
            pw = _read_token(args.password_file)
        except (OSError, UnicodeDecodeError):
            raise ValueError("Passwort-Datei nicht lesbar") from None
        pw_hash = users.prepare_password(user.id, pw)  # Regeltexte nennen nie das Passwort
    with users._tx(immediate=True):  # Änderung, Sessions und Audit gemeinsam oder gar nicht
        if args.cmd == "unlock":
            users.unlock(user.id)
            action = "user.unlocked"
        else:
            if args.cmd == "reset-password":
                users.store_password(user.id, pw_hash)
                action = "password.reset"
            else:
                users.reset_totp(user.id)
                action = "totp.reset"
            SessionStore(conn, settings).delete_for_user(user.id)
        audit.record(conn, _ACTOR, action, target=user.email)
    print(f"{action}: {user.email}")
    return 0


def _cli_audit(settings: Settings, action: str, target: str, details: dict | None = None) -> None:
    """Audit für Token-Befehle; nur in der Manager-Rolle, der Agent hat keine DB."""
    if settings.role == "manager":
        db.migrate(settings.db_path)
        conn = db.connect(settings.db_path)
        try:
            audit.record(conn, _ACTOR, action, target=target, details=details)
        finally:
            conn.close()


def _tls_init(s: Settings, mpki: ManagerPki) -> int:
    """Beim Containerstart vor nginx: CA, Client- und HTTPS-Zertifikat, dann die nginx-Konfiguration (D7, D8)."""
    conf = nginx.render(s)
    now = utcnow()
    mpki.ensure(now)
    if s.tls == "on":
        mpki.ensure_https(s.manager_hostnames, now)
    nginx.CONF_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_private(nginx.CONF_PATH, conf.encode())
    print("CA, Zertifikate und nginx-Konfiguration des Managers sind bereit.")
    return 0


def _manager(args) -> int:
    try:
        s = Settings.from_env()
        if not s.secret_key:
            raise ValueError("MONI_SECRET_KEY fehlt – erzeugen mit: python -m moni.cli secret generate")
        if args.group == "user" and s.role != "manager":  # vor db.migrate: sonst entsteht in der Agent-Rolle eine moni.db
            raise ValueError("Benutzer gibt es nur in der Manager-Rolle")
        db.migrate(s.db_path)
        conn = db.connect(s.db_path)
        try:
            box = SecretBox(s.secret_key)
            store = ServerStore(conn, box)
            mpki = ManagerPki(Path(s.data_dir), box)
            if args.group == "user":
                return _user(args, s, conn, box)
            if args.group == "tls":
                return _tls_init(s, mpki)
            if args.group == "secret":
                # TLS-Schlüssel erst als temporäre Dateien, eingesetzt erst nach dem DB-Commit (D4)
                n_tls, commit_files, abort_files = mpki.prepare_reencrypt()
                try:
                    conn.execute("BEGIN IMMEDIATE")  # eine Transaktion für alle Secrets: scheitert eine Rotation, bleibt alles unverändert
                    n = store.reencrypt_all() + UserStore(conn, box).reencrypt_all() + oidc.reencrypt(conn, box) + ChannelStore(conn, box).reencrypt_all() + n_tls
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    abort_files()
                    raise
                commit_files()
                audit.record(conn, _ACTOR, "secret.reencrypted")
                print(f"Neu verschlüsselt: {n}")
            elif args.cmd == "add" and args.pairing_code_file is not None:
                if args.header:
                    raise ValueError("--header gibt es nur mit --token-file")
                try:
                    code = _read_token(args.pairing_code_file)
                except (OSError, UnicodeDecodeError):
                    raise ValueError("Kopplungscode-Datei nicht lesbar") from None
                mpki.ensure(utcnow())
                srv = pairing.add_server(store, mpki, args.name, code, args.url, utcnow(), s.poll_timeout_seconds)
                audit.record(conn, _ACTOR, "server.added", target=srv.name,
                             details={"url": srv.url, "security": srv.security, "header_names": srv.header_names})
                print(f"Server {srv.id} ({srv.name}) gekoppelt und angelegt.")
            elif args.cmd == "add":
                if not args.url:
                    raise ValueError("URL fehlt (--url)")
                headers = {}
                for h in args.header:
                    k, sep, v = h.partition("=")
                    if not sep:
                        raise ValueError("--header erwartet NAME=WERT")
                    headers[k] = v
                try:
                    token = _read_token(args.token_file)
                except (OSError, UnicodeDecodeError):
                    raise ValueError("Token-Datei nicht lesbar") from None
                srv = store.add(args.name, args.url, token, headers)
                audit.record(conn, _ACTOR, "server.added", target=srv.name,
                             details={"url": srv.url, "security": srv.security, "header_names": srv.header_names})
                print(f"Server {srv.id} ({srv.name}) angelegt.")
            elif args.cmd == "list":
                for v in store.list():
                    print(f"{v.id}  {v.name}  {v.url or 'lokal'}  {v.security}  {v.status}  {','.join(v.header_names) or '-'}")
            elif (old := store.get(args.id)) is None or not store.delete(args.id):
                print(f"Kein Server mit der ID {args.id}.", file=sys.stderr)
                return 1
            else:
                audit.record(conn, _ACTOR, "server.removed", target=old.name)
                print(f"Server {args.id} entfernt.")
        finally:
            conn.close()
    except (OSError, RuntimeError, ValueError, SecretError, PairingError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    return 0


def _pair(args) -> int:
    try:
        s = Settings.from_env()
        if s.role != "agent" or s.tls != "on":
            raise ValueError("Kopplung gibt es nur beim Agent mit MONI_TLS=on.")
        st = TlsState(Path(s.data_dir))
        if args.cmd == "reset":
            print(st.reset(utcnow(), s.public_url))
            print("Bitte den Agent neu starten: docker compose restart moni-agent")
            return 0
        st.ensure(utcnow())
        code = st.pairing_code(s.public_url)
    except (OSError, ValueError, TlsError) as e:  # ssl.SSLError ist ein OSError
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    if code is None:
        print("Agent ist bereits gekoppelt.", file=sys.stderr)
        return 1
    print(code)
    return 0


def openapi_json(role: str) -> str:
    """OpenAPI-Spezifikation der Rolle (D8): ohne Lifespan, ohne Netz, Daten nur in einem Wegwerfverzeichnis."""
    import json
    import logging
    import tempfile
    from types import SimpleNamespace

    from .app import create_app

    logging.disable(logging.CRITICAL)  # create_app loggt sonst einen Setup-Token nach stderr
    with tempfile.TemporaryDirectory() as tmp:
        env = {"MONI_ROLE": role, "MONI_DATA_DIR": tmp, "MONI_TLS": "on" if role == "agent" else "off",
               "MONI_SECRET_KEY": generate_key()}
        # Agent mit TLS, damit die Kopplungsrouten im Dokument stehen; die Attrappe ersetzt Zertifikate
        app = create_app(Settings.from_env(env), tls_state=SimpleNamespace(paired=True) if role == "agent" else None)
        return json.dumps(app.openapi(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.group == "secret" and args.cmd == "generate":  # braucht weder Konfiguration noch Datenverzeichnis
        print(generate_key())
        return 0
    if args.group == "openapi":
        sys.stdout.write(openapi_json(args.role))
        return 0
    if args.group in ("server", "secret", "user", "tls"):
        return _manager(args)
    if args.group == "pair":
        return _pair(args)
    try:
        settings = Settings.from_env()
        store = TokenStore(Path(settings.data_dir) / "tokens.json")
        if args.cmd == "create":
            if args.expires_days is not None and args.expires_days < 1:
                print("Fehler: --expires-days muss mindestens 1 sein.", file=sys.stderr)
                return 2
            exp = utcnow() + timedelta(days=args.expires_days) if args.expires_days is not None else None
            tok, plain = store.create(args.name, args.scope, expires_at=exp)
            try:
                _cli_audit(settings, "token.created", tok.id, {"scope": tok.scope})
            except (sqlite3.Error, OSError, RuntimeError, ValueError):  # kein Token ohne Audit: sonst bliebe ein verwaister, nie angezeigter Token gültig
                try:
                    store.revoke(tok.id)
                except (OSError, RuntimeError, ValueError):
                    print(f"Fehler: Audit-Eintrag nicht möglich, und der Token {tok.id} konnte nicht widerrufen werden. "
                          f"Bitte sofort widerrufen: python -m moni.cli token revoke {tok.id}", file=sys.stderr)
                    return 1
                print("Fehler: Audit-Eintrag nicht möglich, der Token wurde nicht angelegt.", file=sys.stderr)
                return 1
            print("Token wird nur jetzt angezeigt.")
            print(plain)
        elif args.cmd == "list":
            for t in store.list():
                print(f"{t.id}  {t.name}  {t.scope}  erstellt {t.created_at}  läuft ab {t.expires_at or 'nie'}")
        else:
            if not store.revoke(args.id):
                print(f"Kein Token mit der ID {args.id}.", file=sys.stderr)
                return 1
            print(f"Token {args.id} widerrufen.")
            try:
                _cli_audit(settings, "token.revoked", args.id)
            except (sqlite3.Error, OSError, ValueError):
                print("Fehler: Der Token ist widerrufen, der Audit-Eintrag fehlt aber.", file=sys.stderr)
                return 1
    except (OSError, RuntimeError, ValueError) as e:
        print(f"Fehler: Token-Datei oder Konfiguration nicht nutzbar ({e}). Ist das Datenverzeichnis vorhanden und beschreibbar?", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
