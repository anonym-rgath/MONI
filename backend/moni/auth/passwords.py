import os
import threading

from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

# OWASP-Mindestwerte für Argon2id (P3-D1); reicht auch für den Pi
_MEMORY_KIB, _ITERATIONS, _LANES = 19456, 2, 1
MIN_LEN, MAX_LEN = 12, 256

_dummy: str | None = None
_dummy_lock = threading.Lock()


def hash_password(password: str) -> str:
    kdf = Argon2id(salt=os.urandom(16), length=32, iterations=_ITERATIONS, lanes=_LANES, memory_cost=_MEMORY_KIB)
    return kdf.derive_phc_encoded(password.encode())


def verify_password(password: str, phc: str) -> bool:
    """Wirft nie: kaputte oder fremde Hashes gelten als falsches Passwort."""
    if not isinstance(phc, str) or not phc.startswith("$argon2id$"):
        return False
    try:
        Argon2id.verify_phc_encoded(password.encode(), phc)
        return True
    except (InvalidKey, ValueError, TypeError):
        return False


def dummy_verify(password: str) -> None:
    """Gleicher Zeitaufwand für unbekannte Konten wie für echte Prüfungen."""
    global _dummy
    with _dummy_lock:
        if _dummy is None:
            _dummy = hash_password(os.urandom(16).hex())
    verify_password(password, _dummy)


def check_policy(password: str, email: str) -> None:
    try:
        password.encode()
    except UnicodeEncodeError:  # lone surrogate aus JSON; die Original-Exception würde das Zeichen nennen
        raise ValueError("Passwort enthält ungültige Zeichen") from None
    if len(password) < MIN_LEN:
        raise ValueError(f"Passwort muss mindestens {MIN_LEN} Zeichen lang sein")
    if len(password) > MAX_LEN:
        raise ValueError(f"Passwort darf höchstens {MAX_LEN} Zeichen lang sein")
    if password.casefold() == email.casefold():
        raise ValueError("Passwort darf nicht der E-Mail-Adresse entsprechen")
