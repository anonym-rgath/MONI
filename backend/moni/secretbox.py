from cryptography.fernet import Fernet, MultiFernet

_BAD_KEY = "MONI_SECRET_KEY ist ungültig (erwartet Fernet-Schlüssel, ggf. kommagetrennt)"


class SecretError(Exception):
    pass


def generate_key() -> str:
    return Fernet.generate_key().decode()


class SecretBox:
    """Erster Schlüssel verschlüsselt, alle entschlüsseln (Rotation)."""

    def __init__(self, key_spec: str):
        try:
            self._f = MultiFernet([Fernet(k.strip()) for k in key_spec.split(",")])
        except Exception:  # Fehlermeldungen der Bibliothek könnten den Schlüssel enthalten
            raise ValueError(_BAD_KEY) from None

    def encrypt(self, plaintext: str) -> str:
        try:
            data = plaintext.encode()
        except Exception:
            data = None
        if data is None:  # außerhalb von except: sonst hängt die Ausnahme mit dem Klartext als __context__ dran
            raise ValueError("Geheimnis kann nicht gespeichert werden") from None
        return self._f.encrypt(data).decode()

    def decrypt(self, token: str) -> str:
        try:
            text = self._f.decrypt(token.encode()).decode()
        except Exception:  # InvalidToken, falscher Typ, kaputtes UTF-8
            text = None
        if text is None:  # außerhalb von except: __context__ bleibt leer
            raise SecretError("Geheimnis nicht entschlüsselbar") from None
        return text

    def rotate(self, token: str) -> str:
        try:
            return self._f.rotate(token.encode()).decode()
        except Exception:
            raise SecretError("Geheimnis nicht entschlüsselbar") from None
