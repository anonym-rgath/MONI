"""nginx-Konfiguration des Manager-Images, beim Containerstart aus den Settings erzeugt (D7, D8)."""
import re
from pathlib import Path

from moni.config import Settings

CONF_PATH = Path("/tmp/moni-nginx/moni.conf")  # die Haupt-nginx.conf bindet /tmp/moni-nginx/*.conf ein (Dockerfile)
_CIDR = re.compile(r"[0-9A-Fa-f:.]+/[0-9]{1,3}")

_PROXY = """\
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 30s;
"""

_BODY = """\
    server_name localhost;
    access_log /dev/stdout moni;
    error_log /dev/stderr warn;
    client_max_body_size 1m;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; \
img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; \
form-action 'self'; require-trusted-types-for 'script'; trusted-types 'none'" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header X-Frame-Options "DENY" always;
    add_header Permissions-Policy "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), \
microphone=(), payment=(), usb=()" always;

    location / {
        root /app/frontend/dist;
        try_files $uri $uri/ /index.html;
    }

    location /api {
""" + _PROXY + """\
    }

    # Paket 3: bei einem Backend-Ausfall stünde die Anfrage samt OIDC-code im error_log
    location /api/v1/auth/oidc/callback/ {
        error_log /dev/stderr crit;
""" + _PROXY + """\
    }
}
"""

_HEAD = """\
# P3-R6: $uri statt $request, damit der OIDC-code (Query-String) nie im Zugriffslog landet
log_format moni '$remote_addr [$time_iso8601] "$request_method $uri" $status $body_bytes_sent "$http_user_agent"';
# auf http-Ebene, damit jeder server sie erbt: das Dateisystem ist schreibgeschützt, nur /tmp ist beschreibbar
client_body_temp_path /tmp/nginx-client-body;
proxy_temp_path /tmp/nginx-proxy;
fastcgi_temp_path /tmp/nginx-fastcgi;
uwsgi_temp_path /tmp/nginx-uwsgi;
scgi_temp_path /tmp/nginx-scgi;
"""

_TLS = """\
server {
    listen 443 ssl;
    ssl_certificate /data/tls/https.crt;
    ssl_certificate_key /data/tls/https.key;
    ssl_protocols TLSv1.2 TLSv1.3;
"""

_REDIRECT = """
server {{
    listen 80;
    access_log /dev/stdout moni;
    return 301 https://$host{port}$request_uri;
}}
"""


def render(s: Settings) -> str:
    if s.tls == "on":
        assert isinstance(s.https_port, int)  # gegen jede Einschleusung in die Konfiguration
        return _HEAD + _TLS + _BODY + _REDIRECT.format(port="" if s.https_port == 443 else f":{s.https_port}")
    realip = ""
    if s.trusted_proxies:  # nur Anfragen von diesen Adressen dürfen $remote_addr per X-Forwarded-For ersetzen (D8)
        for cidr in s.trusted_proxies:
            if not _CIDR.fullmatch(cidr):  # schon in Settings geprüft; hier gegen jede Einschleusung in die Konfiguration
                raise ValueError("Ungültiger Eintrag in MONI_TRUSTED_PROXIES")
            realip += f"    set_real_ip_from {cidr};\n"
        realip += "    real_ip_header X-Forwarded-For;\n    real_ip_recursive on;\n"
    return _HEAD + "server {\n    listen 80;\n" + realip + _BODY
