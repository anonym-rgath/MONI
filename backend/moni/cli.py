import argparse
import sys
from datetime import timedelta
from pathlib import Path

from .auth.tokens import TokenStore
from .config import Settings
from .timeutil import utcnow


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="moni.cli")
    t = p.add_subparsers(dest="group", required=True).add_parser("token").add_subparsers(dest="cmd", required=True)
    c = t.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--expires-days", type=int)
    t.add_parser("list")
    t.add_parser("revoke").add_argument("id")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    store = TokenStore(Path(Settings.from_env().data_dir) / "tokens.json")
    try:
        if args.cmd == "create":
            exp = utcnow() + timedelta(days=args.expires_days) if args.expires_days else None
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
    except (OSError, RuntimeError) as e:
        print(f"Fehler: Token-Datei nicht nutzbar ({e}). Ist das Datenverzeichnis vorhanden und beschreibbar?", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
