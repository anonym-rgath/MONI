"""uvicorn-h11 mit Peer-Zertifikat im Scope (ASGI-TLS-Erweiterung); hängt an uvicorn 0.48.0 (D3)."""
import ssl

from uvicorn.protocols.http.h11_impl import H11Protocol


class MoniH11Protocol(H11Protocol):
    def connection_made(self, transport) -> None:
        super().connection_made(transport)
        ssl_object = transport.get_extra_info("ssl_object")
        der = ssl_object.getpeercert(binary_form=True) if ssl_object is not None else None
        if not der:
            return
        chain = [ssl.DER_cert_to_PEM_cert(der)]
        app = self.app

        async def with_peer(scope, receive, send):
            ext = {**scope.get("extensions", {}), "tls": {"client_cert_chain": chain}}
            await app({**scope, "extensions": ext}, receive, send)

        self.app = with_peer
