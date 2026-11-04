import uvicorn

from moni.config import Settings


def server_args(s: Settings) -> dict:
    # Nur hinter dem nginx im Manager-Image (127.0.0.1) werden Weiterleitungs-Header vertraut, sonst nie:
    # Rate-Limit pro echter IP darf nicht per X-Forwarded-For umgehbar sein.
    return dict(host=s.bind_host, port=s.port, proxy_headers=s.trust_local_proxy, forwarded_allow_ips="127.0.0.1")


if __name__ == "__main__":
    uvicorn.run("moni.app:create_app", factory=True, log_config=None, **server_args(Settings.from_env()))
