import argparse
import sys
from datetime import timedelta
from pathlib import Path

from .auth.tokens import TokenStore
from .auth.users import UserStore
from .config import Settings
from .manager import db
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
    a.add_argument("--url", required=True)
    a.add_argument("--token-file", required=True, help="Datei mit dem Agent-Token, '-' für stdin")
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
    return p


def _read_token(path: str) -> str:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    # genau ein abschließender Zeilenumbruch; alles weitere prüft ServerStore.add
    return text.removesuffix("\n").removesuffix("\r") if text.endswith("\n") else text


def _manager(args) -> int:
    try:
        s = Settings.from_env()
        if not s.secret_key:
            raise ValueError("MONI_SECRET_KEY fehlt – erzeugen mit: python -m moni.cli secret generate")
        db.migrate(s.db_path)
        conn = db.connect(s.db_path)
        try:
            box = SecretBox(s.secret_key)
            store = ServerStore(conn, box)
            if args.group == "secret":
                conn.execute("BEGIN IMMEDIATE")  # eine Transaktion für beide Stores: scheitert eine Rotation, bleibt alles unverändert
                try:
                    n = store.reencrypt_all() + UserStore(conn, box).reencrypt_all()
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                print(f"Neu verschlüsselt: {n}")
            elif args.cmd == "add":
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
                print(f"Server {srv.id} ({srv.name}) angelegt.")
            elif args.cmd == "list":
                for v in store.list():
                    print(f"{v.id}  {v.name}  {v.url or 'lokal'}  {v.status}  {','.join(v.header_names) or '-'}")
            elif not store.delete(args.id):
                print(f"Kein Server mit der ID {args.id}.", file=sys.stderr)
                return 1
            else:
                print(f"Server {args.id} entfernt.")
        finally:
            conn.close()
    except (OSError, RuntimeError, ValueError, SecretError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.group == "secret" and args.cmd == "generate":  # braucht weder Konfiguration noch Datenverzeichnis
        print(generate_key())
        return 0
    if args.group in ("server", "secret"):
        return _manager(args)
    try:
        store = TokenStore(Path(Settings.from_env().data_dir) / "tokens.json")
        if args.cmd == "create":
            if args.expires_days is not None and args.expires_days < 1:
                print("Fehler: --expires-days muss mindestens 1 sein.", file=sys.stderr)
                return 2
            exp = utcnow() + timedelta(days=args.expires_days) if args.expires_days is not None else None
            _, plain = store.create(args.name, args.scope, expires_at=exp)
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
    except (OSError, RuntimeError, ValueError) as e:
        print(f"Fehler: Token-Datei oder Konfiguration nicht nutzbar ({e}). Ist das Datenverzeichnis vorhanden und beschreibbar?", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
