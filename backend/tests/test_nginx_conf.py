import dataclasses
import re

import pytest

from moni.config import Settings
from moni.manager import nginx
from moni.secretbox import generate_key


def _s(**env):
    return Settings.from_env({"MONI_ROLE": "manager", "MONI_SECRET_KEY": generate_key(), **env})


def _servers(conf: str) -> list[str]:
    """Die `server { … }`-Blöcke auf oberster Ebene."""
    out, depth, start = [], 0, None
    for m in re.finditer(r"[{}]", conf):
        if m.group() == "{":
            if depth == 0:
                start = conf.rfind("server", 0, m.start())
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                out.append(conf[start:m.end()])
    assert depth == 0
    return out


def _location(conf: str, path: str) -> str:
    m = re.search(r"location " + re.escape(path) + r" \{(.*?)\}", conf, re.S)
    assert m, path
    return m.group(1)


def test_render_tls_on_has_443_redirect_and_no_realip():
    conf = nginx.render(_s(MONI_TLS="on", MONI_TRUSTED_PROXIES="10.0.0.0/8"))  # Proxys gelten nur bei tls=off
    main, redirect = _servers(conf)
    assert "listen 443 ssl;" in main and "listen 80;" not in main
    for line in ("ssl_certificate /data/tls/https.crt;", "ssl_certificate_key /data/tls/https.key;",
                 "ssl_protocols TLSv1.2 TLSv1.3;"):
        assert line in main
    assert "location /api {" in main and "location / {" in main
    assert [ln.strip() for ln in redirect.splitlines()[1:-1] if ln.strip() and "log" not in ln] == [
        "listen 80;", "return 301 https://$host$request_uri;"]
    assert "real_ip" not in conf and "set_real_ip_from" not in conf


def test_render_tls_off_with_trusted_proxies_sets_realip():
    conf = nginx.render(_s(MONI_TLS="off", MONI_TRUSTED_PROXIES="10.0.0.0/8, 192.168.1.10, fd00::/8"))
    (main,) = _servers(conf)
    assert "listen 80;" in main and "443" not in conf and "ssl" not in conf and "return 301" not in conf
    lines = [ln.strip() for ln in main.splitlines()]
    assert [ln for ln in lines if "real_ip" in ln] == [
        "set_real_ip_from 10.0.0.0/8;", "set_real_ip_from 192.168.1.10/32;", "set_real_ip_from fd00::/8;",
        "real_ip_header X-Forwarded-For;", "real_ip_recursive on;"]
    # das Backend bekommt die von realip ermittelte Adresse, nie die vom Client gesendete Kette
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in _location(conf, "/api")


def test_render_tls_off_without_proxies_has_no_realip():
    conf = nginx.render(_s(MONI_TLS="off"))
    assert "real_ip" not in conf and len(_servers(conf)) == 1


@pytest.mark.parametrize("tls", ["on", "off"])
def test_render_oidc_callback_error_log_crit(tls):
    conf = nginx.render(_s(MONI_TLS=tls))
    block = _location(conf, "/api/v1/auth/oidc/callback/")
    assert "error_log /dev/stderr crit;" in block
    assert "proxy_pass http://127.0.0.1:8001;" in block
    assert block.replace("error_log /dev/stderr crit;", "").split() == _location(conf, "/api").split()


# Aus dem bisherigen statischen nginx-Default im Dockerfile, 1:1
_KEPT = [
    "# P3-R6: $uri statt $request, damit der OIDC-code (Query-String) nie im Zugriffslog landet",
    """log_format moni '$remote_addr [$time_iso8601] "$request_method $uri" $status $body_bytes_sent "$http_user_agent"';""",
    "server_name localhost;",
    "access_log /dev/stdout moni;",
    "error_log /dev/stderr warn;",
    "client_max_body_size 1m;",
    "client_body_temp_path /tmp/nginx-client-body;",
    "proxy_temp_path /tmp/nginx-proxy;",
    "fastcgi_temp_path /tmp/nginx-fastcgi;",
    "uwsgi_temp_path /tmp/nginx-uwsgi;",
    "scgi_temp_path /tmp/nginx-scgi;",
    """add_header Content-Security-Policy "default-src 'self'; font-src 'self' https://fonts.gstatic.com; """
    """style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; script-src 'self'; connect-src 'self'; """
    """img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;""",
    'add_header X-Content-Type-Options "nosniff" always;',
    'add_header Referrer-Policy "no-referrer" always;',
    'add_header X-Frame-Options "DENY" always;',
    'add_header Permissions-Policy "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), '
    'microphone=(), payment=(), usb=()" always;',
]
_HTTP_LEVEL = ("client_body_temp_path", "proxy_temp_path", "fastcgi_temp_path", "uwsgi_temp_path", "scgi_temp_path")
_KEPT_ROOT = ["root /app/frontend/build;", "try_files $uri $uri/ /index.html;"]
_KEPT_API = ["proxy_pass http://127.0.0.1:8001;", "proxy_set_header Host $host;",
             "proxy_set_header X-Real-IP $remote_addr;", "proxy_set_header X-Forwarded-For $remote_addr;",
             "proxy_set_header X-Forwarded-Proto $scheme;", "proxy_read_timeout 30s;"]


@pytest.mark.parametrize("tls", ["on", "off"])
def test_render_keeps_security_headers_and_log_format(tls):
    conf = nginx.render(_s(MONI_TLS=tls))
    lines = {ln.strip() for ln in conf.splitlines()}
    main = _servers(conf)[0]
    for line in _KEPT:
        assert line in lines, line
        assert line.startswith(("#", "log_format", *_HTTP_LEVEL)) or line in main, line
    for line in _KEPT:  # auf http-Ebene, damit auch der Umleitungs-server sie erbt (schreibgeschütztes Dateisystem)
        if line.startswith(_HTTP_LEVEL):
            assert line + "\n" in conf.split("server {")[0], line
    assert [ln.strip() for ln in _location(conf, "/").splitlines() if ln.strip()] == _KEPT_ROOT
    assert [ln.strip() for ln in _location(conf, "/api").splitlines() if ln.strip()] == _KEPT_API


@pytest.mark.parametrize("bad", ["10.0.0.0/8;\n    return 200", "10.0.0.0/8 ", "evil", "10.0.0.0/8\n", "1.2.3.4/8/8"])
def test_render_rejects_injected_tokens(bad):
    s = dataclasses.replace(_s(MONI_TLS="off"), trusted_proxies=("10.0.0.0/8", bad))
    with pytest.raises(ValueError):
        nginx.render(s)
