"""Body-Grenze am Agent: nichts über 64 KiB wird gelesen oder gepuffert."""
import asyncio
import json

from starlette.requests import Request

from moni.bodylimit import MAX_BODY, BodyLimitMiddleware


def _run(app, method="POST", headers=(), chunks=None):
    """Ruft die Middleware mit einer Anfrage auf; chunks=None heißt endloser Strom aus 32-KiB-Stücken."""
    sent, received = [], []

    async def receive():
        i = len(received)
        received.append(i)
        if chunks is None:
            return {"type": "http.request", "body": b"x" * 32768, "more_body": True}
        return {"type": "http.request", "body": chunks[i], "more_body": i + 1 < len(chunks)}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": method, "path": "/", "headers": list(headers), "query_string": b""}
    asyncio.run(BodyLimitMiddleware(app)(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body, received


def _reader(calls):
    async def app(scope, receive, send):
        calls.append(scope["path"])
        body = await Request(scope, receive).body()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": str(len(body)).encode()})
    return app


def test_oversized_content_length_is_413_without_reading():
    calls = []
    status, body, received = _run(_reader(calls), headers=[(b"content-length", str(MAX_BODY + 1).encode())])
    assert status == 413 and json.loads(body)["detail"] == "Anfrage zu groß"
    assert calls == [] and received == []  # weder App aufgerufen noch Body gelesen


def test_garbage_content_length_is_413():
    calls = []
    assert _run(_reader(calls), headers=[(b"content-length", b"-1")])[0] == 413 and calls == []


def test_stream_is_cut_after_limit():
    status, body, received = _run(_reader([]))  # ohne Content-Length, endlos
    assert status == 413 and json.loads(body)["detail"] == "Anfrage zu groß"
    assert len(received) == MAX_BODY // 32768 + 1  # ein Stück über der Grenze, dann Schluss


def test_body_at_limit_passes():
    status, body, _ = _run(_reader([]), chunks=[b"x" * 32768, b"x" * 32768])
    assert (status, body) == (200, str(MAX_BODY).encode())


def test_get_is_untouched():
    calls = []
    status, _, _ = _run(_reader(calls), method="GET", headers=[(b"content-length", b"999999999")], chunks=[b""])
    assert status == 200 and calls == ["/"]
