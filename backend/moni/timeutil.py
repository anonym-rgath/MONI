from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)  # wirft ValueError bei Unsinn
    if dt.tzinfo is None:
        raise ValueError("Zeitstempel ohne Zeitzone")
    return dt.astimezone(timezone.utc)
