from pathlib import Path

import uvicorn

from moni.agent.h11tls import MoniH11Protocol
from moni.agent.tls import TlsState
from moni.app import create_app
from moni.collector import Collector
from moni.config import Settings
from moni.timeutil import utcnow


def server_args(s: Settings) -> dict:
    # Weiterleitungs-Header nur vom nginx im Manager-Image (127.0.0.1) oder, beim Agent mit tls=off, von den
    # vertrauenswürdigen Proxys (D8): Rate-Limit pro echter IP darf nicht per X-Forwarded-For umgehbar sein.
    # uvicorn 0.48 nimmt in forwarded_allow_ips auch CIDRs an.
    proxied = s.role == "agent" and s.tls == "off" and bool(s.trusted_proxies)
    return dict(host=s.bind_host, port=s.port, proxy_headers=proxied or s.trust_local_proxy,
                forwarded_allow_ips=",".join(s.trusted_proxies) if proxied else "127.0.0.1",
                access_log=s.role != "manager")  # P3-R6: nginx loggt, ohne Query-String (OIDC-code)


def build_agent_server(s: Settings, state: TlsState, collector: Collector | None = None) -> uvicorn.Server:
    """Agent mit TLS: Server und App teilen denselben Kontext, damit install() neue Verbindungen sofort betrifft."""
    app = create_app(s, collector, tls_state=state)
    config = uvicorn.Config(app, http=MoniH11Protocol, log_config=None,
                            ssl_context_factory=lambda _config, _default: state.context, **server_args(s))
    return uvicorn.Server(config)


def main() -> None:
    s = Settings.from_env()
    if s.role == "agent" and s.tls == "on":
        state = TlsState(Path(s.data_dir))
        state.ensure(utcnow())
        build_agent_server(s, state).run()
    else:  # Manager: TLS macht nginx davor
        uvicorn.run("moni.app:create_app", factory=True, log_config=None, **server_args(s))


if __name__ == "__main__":
    main()
