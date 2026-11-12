import base64
import hashlib
import hmac
import re
import secrets
from urllib.parse import quote

from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.twofactor.totp import TOTP

PERIOD = 30
_CODE_RE = re.compile(r"[0-9]{6}")  # nicht isdigit: das ließe auch arabische Ziffern zu
_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def provisioning_uri(secret: str, email: str) -> str:
    return f"otpauth://totp/Moni:{quote(email, safe='')}?secret={secret}&issuer=Moni"


def match_step(secret: str, code: str, now: float, last_step: int | None) -> int | None:
    """Zeitschritt (now // 30 ±1), zu dem der Code passt und der noch nicht verbraucht ist, sonst None."""
    code = "".join(code.split())
    if not _CODE_RE.fullmatch(code):
        return None
    totp = TOTP(base64.b32decode(secret + "=" * (-len(secret) % 8)), 6, SHA1(), PERIOD, enforce_key_length=False)
    found = None
    for step in range(int(now // PERIOD) - 1, int(now // PERIOD) + 2):
        ok = hmac.compare_digest(totp.generate(step * PERIOD).decode(), code)
        if ok and found is None and (last_step is None or step > last_step):
            found = step
    return found


def new_recovery_codes() -> list[str]:
    # 10 Zeichen aus 32 = 50 Bit je Code
    codes: set[str] = set()
    while len(codes) < 10:
        c = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        codes.add(f"{c[:5]}-{c[5:]}")
    return list(codes)


def normalize_recovery(code: str) -> str:
    return code.lower().replace("-", "").replace(" ", "")


def hash_code(code: str) -> str:
    return hashlib.sha256(normalize_recovery(code).encode()).hexdigest()
