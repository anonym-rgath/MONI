"""Benutzerverwaltung des Managers. Hashes und Secrets liegen nur in der DB, nie im User-Objekt."""
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass

from ..secretbox import SecretBox
from . import passwords, totp

LOCK_AFTER = 5
LOCK_SECONDS = 900
_LAST_ADMIN = "Der letzte aktive Admin kann nicht entfernt, herabgestuft oder deaktiviert werden"
_EMAIL_TAKEN = "E-Mail-Adresse ist bereits vergeben"
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_TOTP_CODE = re.compile(r"[0-9]{6}")
_ROLES, _KINDS = ("admin", "viewer"), ("local", "oidc")


class Conflict(Exception):
    pass


@dataclass(frozen=True)
class User:
    id: int
    email: str
    name: str
    role: str
    kind: str
    totp_enabled: bool
    disabled: bool
    failed_logins: int
    locked_until: int | None
    created_at: int
    last_login: int | None
    oidc_bound: bool


def _user(r: sqlite3.Row) -> User:
    return User(r["id"], r["email"], r["name"], r["role"], r["kind"], r["totp_enc"] is not None, bool(r["disabled"]),
                r["failed_logins"], r["locked_until"], r["created_at"], r["last_login"], r["oidc_sub"] is not None)


def normalize_email(email: str) -> str:
    # Steuerzeichen vor dem Trimmen prüfen, sonst verschwindet ein angehängtes "\n" stillschweigend
    if (not isinstance(email, str) or any(not c.isprintable() for c in email) or len(email.strip()) > 254
            or not _EMAIL_RE.fullmatch(email.strip())):
        raise ValueError("Ungültige E-Mail-Adresse")
    return email.strip().lower()


def _check_name(name) -> str:
    if not isinstance(name, str) or len(name) > 100 or any(not c.isprintable() for c in name):
        raise ValueError("Name darf höchstens 100 Zeichen lang sein und keine Steuerzeichen enthalten")
    return name


class UserStore:
    def __init__(self, conn: sqlite3.Connection, box: SecretBox):
        self._conn = conn
        self._box = box

    @contextmanager
    def _tx(self, immediate: bool = False):
        """Committet nur, wenn die Transaktion hier begonnen wurde (wie ServerStore, P2-R1)."""
        if self._conn.in_transaction:
            yield
            return
        if immediate:
            self._conn.execute("BEGIN IMMEDIATE")
        with self._conn:
            yield

    def _row(self, user_id: int) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    def _need(self, user_id: int) -> sqlite3.Row:
        r = self._row(user_id)
        if r is None:
            raise ValueError("Benutzer nicht gefunden")
        return r

    def _require_other_admin(self, user_id: int) -> None:
        if not self._conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0 AND id != ?",
                                  (user_id,)).fetchone()[0]:
            raise Conflict(_LAST_ADMIN)

    def create(self, email: str, name: str, role: str, kind: str, password: str | None = None, now: int | None = None) -> User:
        email, name = normalize_email(email), _check_name(name)
        if role not in _ROLES or kind not in _KINDS:
            raise ValueError("Rolle oder Art des Kontos ist ungültig")
        if kind == "oidc" and password is not None:
            raise ValueError("OIDC-Konten haben kein Passwort")
        pw_hash = None
        if kind == "local":
            if password is None:
                raise ValueError("Passwort fehlt")
            passwords.check_policy(password, email)
            pw_hash = passwords.hash_password(password)
        try:
            with self._tx():
                cur = self._conn.execute(
                    "INSERT INTO users (email, name, role, kind, password_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (email, name, role, kind, pw_hash, int(time.time()) if now is None else now))
        except sqlite3.IntegrityError:
            raise Conflict(_EMAIL_TAKEN) from None
        return _user(self._row(cur.lastrowid))

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def get(self, user_id: int) -> User | None:
        r = self._row(user_id)
        return _user(r) if r else None

    def by_email(self, email: str) -> User | None:
        try:
            email = normalize_email(email)
        except ValueError:
            return None
        r = self._conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        return _user(r) if r else None

    def list(self) -> list[User]:
        return [_user(r) for r in self._conn.execute("SELECT * FROM users ORDER BY id")]

    def update(self, user_id: int, *, name: str | None = None, role: str | None = None, disabled: bool | None = None) -> User | None:
        if name is not None:
            _check_name(name)
        if role is not None and role not in _ROLES:
            raise ValueError("Rolle ist ungültig")
        with self._tx(immediate=True):
            r = self._row(user_id)
            if r is None:
                return None
            new_role = role or r["role"]
            new_disabled = r["disabled"] if disabled is None else int(disabled)
            if r["role"] == "admin" and not r["disabled"] and (new_role != "admin" or new_disabled):
                self._require_other_admin(user_id)
            self._conn.execute("UPDATE users SET name=?, role=?, disabled=? WHERE id=?",
                               (r["name"] if name is None else name, new_role, new_disabled, user_id))
        return self.get(user_id)

    def delete(self, user_id: int) -> bool:
        with self._tx(immediate=True):
            r = self._row(user_id)
            if r is None:
                return False
            if r["role"] == "admin" and not r["disabled"]:
                self._require_other_admin(user_id)
            self._conn.execute("DELETE FROM users WHERE id=?", (user_id,))
            return True

    def set_password(self, user_id: int, password: str) -> None:
        r = self._need(user_id)
        if r["kind"] != "local":
            raise ValueError("OIDC-Konten haben kein Passwort")
        passwords.check_policy(password, r["email"])
        h = passwords.hash_password(password)
        with self._tx():
            self._conn.execute("UPDATE users SET password_hash=? WHERE id=?", (h, user_id))

    def check_password(self, user_id: int, password: str) -> bool:
        r = self._row(user_id)
        if r is None or r["password_hash"] is None:
            return False
        try:
            return passwords.verify_password(password, r["password_hash"])
        except (UnicodeEncodeError, AttributeError):  # Surrogate aus JSON; Text der Exception würde Zeichen verraten
            return False

    @staticmethod
    def is_locked(user: User, now: int) -> bool:
        return user.locked_until is not None and now < user.locked_until

    def register_failure(self, user_id: int, now: int) -> bool:
        with self._tx(immediate=True):
            row = self._conn.execute(
                "UPDATE users SET failed_logins = CASE WHEN failed_logins + 1 >= ?1 THEN 0 ELSE failed_logins + 1 END, "
                "locked_until = CASE WHEN failed_logins + 1 >= ?1 THEN ?2 ELSE locked_until END WHERE id=?3 "
                "RETURNING failed_logins", (LOCK_AFTER, now + LOCK_SECONDS, user_id)).fetchone()
        return row is not None and row[0] == 0

    def register_success(self, user_id: int, now: int) -> None:
        with self._tx():
            self._conn.execute("UPDATE users SET failed_logins=0, locked_until=NULL, last_login=? WHERE id=?", (now, user_id))

    def unlock(self, user_id: int) -> bool:
        with self._tx():
            return self._conn.execute("UPDATE users SET failed_logins=0, locked_until=NULL WHERE id=?", (user_id,)).rowcount > 0

    def begin_totp(self, user_id: int) -> tuple[str, str]:
        r = self._need(user_id)
        secret = totp.new_secret()
        with self._tx():
            self._conn.execute("UPDATE users SET totp_pending_enc=? WHERE id=?", (self._box.encrypt(secret), user_id))
        return secret, totp.provisioning_uri(secret, r["email"])

    def _store_codes(self, user_id: int) -> list[str]:
        codes = totp.new_recovery_codes()
        self._conn.execute("DELETE FROM recovery_codes WHERE user_id=?", (user_id,))
        self._conn.executemany("INSERT INTO recovery_codes (user_id, code_hash) VALUES (?, ?)",
                               [(user_id, totp.hash_code(c)) for c in codes])
        return codes

    def confirm_totp(self, user_id: int, code: str, now: int) -> list[str] | None:
        with self._tx(immediate=True):
            r = self._row(user_id)
            if r is None or r["totp_pending_enc"] is None:
                return None
            step = totp.match_step(self._box.decrypt(r["totp_pending_enc"]), code, now, None)
            if step is None:
                return None
            self._conn.execute("UPDATE users SET totp_enc=totp_pending_enc, totp_pending_enc=NULL, totp_last_step=? WHERE id=?",
                               (step, user_id))
            return self._store_codes(user_id)

    def verify_second_factor(self, user_id: int, code: str, now: int) -> str | None:
        if not isinstance(code, str):
            return None
        with self._tx(immediate=True):
            r = self._row(user_id)
            if r is None or r["totp_enc"] is None:
                return None
            if _TOTP_CODE.fullmatch("".join(code.split())):
                step = totp.match_step(self._box.decrypt(r["totp_enc"]), code, now, r["totp_last_step"])
                if step is None:
                    return None
                self._conn.execute("UPDATE users SET totp_last_step=? WHERE id=?", (step, user_id))
                return "totp"
            try:
                h = totp.hash_code(code)
            except UnicodeEncodeError:
                return None
            used = self._conn.execute("UPDATE recovery_codes SET used_at=? WHERE user_id=? AND code_hash=? AND used_at IS NULL",
                                      (now, user_id, h)).rowcount
            return "recovery" if used else None

    def regenerate_recovery_codes(self, user_id: int) -> list[str]:
        with self._tx(immediate=True):
            if self._need(user_id)["totp_enc"] is None:
                raise ValueError("Zwei-Faktor-Anmeldung ist nicht aktiv")
            return self._store_codes(user_id)

    def reset_totp(self, user_id: int) -> None:
        with self._tx():
            self._conn.execute("UPDATE users SET totp_enc=NULL, totp_pending_enc=NULL, totp_last_step=NULL WHERE id=?", (user_id,))
            self._conn.execute("DELETE FROM recovery_codes WHERE user_id=?", (user_id,))

    def by_oidc(self, iss: str, sub: str) -> User | None:
        r = self._conn.execute("SELECT * FROM users WHERE oidc_iss=? AND oidc_sub=?", (iss, sub)).fetchone()
        return _user(r) if r else None

    def bind_oidc(self, user_id: int, iss: str, sub: str) -> None:
        try:
            with self._tx():
                n = self._conn.execute("UPDATE users SET oidc_iss=?, oidc_sub=? WHERE id=?", (iss, sub, user_id)).rowcount
        except sqlite3.IntegrityError:
            raise Conflict("OIDC-Identität ist bereits einem Konto zugeordnet") from None
        if not n:
            raise ValueError("Benutzer nicht gefunden")

    def reencrypt_all(self) -> int:
        n = 0
        with self._tx():
            for r in self._conn.execute("SELECT id, totp_enc, totp_pending_enc FROM users "
                                        "WHERE totp_enc IS NOT NULL OR totp_pending_enc IS NOT NULL").fetchall():
                rot = lambda v: None if v is None else self._box.rotate(v)  # noqa: E731
                self._conn.execute("UPDATE users SET totp_enc=?, totp_pending_enc=? WHERE id=?",
                                   (rot(r["totp_enc"]), rot(r["totp_pending_enc"]), r["id"]))
                n += 1
        return n
