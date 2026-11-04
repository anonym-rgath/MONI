import argparse
import sys
from datetime import timedelta
from pathlib import Path

from .auth.tokens import TokenStore
from .config import Settings
from .secretbox import generate_key
from .timeutil import utcnow


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="moni.cli")
    g = p.add_subparsers(dest="group", required=True)
    g.add_parser("secret").add_subparsers(dest="cmd", required=True).add_parser("generate")
    t = g.add_parser("token").add_subparsers(dest="cmd", required=True)
    c = t.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--expires-days", type=int)
    t.add_parser("list")
    t.add_parser("revoke").add_argument("id")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.group == "secret":  # braucht weder Konfiguration noch Datenverzeichnis
        print(generate_key())
        return 0
    try:
        store = TokenStore(Path(Settings.from_env().data_dir) / "tokens.json")
        if args.cmd == "create":
            if args.expires_days is not None and args.expires_days < 1:
                print("Fehler: --expires-days muss mindestens 1 sein.", file=sys.stderr)
                return 2
            exp = utcnow() + timedelta(days=args.expires_days) if args.expires_days is not None else None
            _, plain = store.create(args.name, expires_at=exp)
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
