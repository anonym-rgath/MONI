# Multi-stage build for Moni
# Frontend-Build läuft auf der Build-Plattform (Ergebnis ist architekturunabhängig, schneller bei Multi-Arch).
FROM --platform=$BUILDPLATFORM node:26-alpine@sha256:7c6af15abe4e3de859690e7db171d0d711bf37d27528eddfe625b2fe89e097f8 AS frontend-builder

WORKDIR /app/frontend
# --ignore-scripts: keine Install-Skripte der Abhängigkeiten im Build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ .
RUN npm run build

# Agent-Image: nur der Messdienst (kein nginx, kein Frontend)
FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS agent

WORKDIR /app

COPY backend/requirements-docker.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements-docker.txt

COPY backend/moni /app/moni

RUN addgroup --system app \
    && adduser --system --ingroup app --home /app app \
    && mkdir -p /data \
    && chown app:app /data

ENV MONI_DATA_DIR=/data PYTHONUNBUFFERED=1

USER app

EXPOSE 9810

# Nur Lebenszeichen: https ohne Zertifikatsprüfung (MONI_TLS=on), sonst http (MONI_TLS=off).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import ssl, urllib.request as u; u.urlopen('https://127.0.0.1:9810/api/v1/health', timeout=3, context=ssl._create_unverified_context())" \
        || python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:9810/api/v1/health', timeout=3)"

CMD ["python", "-m", "moni"]

# Manager-Image: nginx (Frontend + /api-Proxy) und Manager-Backend unter supervisord
FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS manager

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    supervisor \
    curl \
    && sed -i 's|^user .*|# user directive disabled for non-root runtime;|' /etc/nginx/nginx.conf \
    && sed -i 's|^pid .*|pid /tmp/nginx.pid;|' /etc/nginx/nginx.conf \
    && sed -i 's|^[[:space:]]*error_log .*|error_log /dev/stderr warn;|' /etc/nginx/nginx.conf \
    && sed -i 's|^[[:space:]]*access_log .*|    access_log /dev/stdout;|' /etc/nginx/nginx.conf \
    && sed -i 's|include /etc/nginx/sites-enabled/\*;|include /tmp/moni-nginx/*.conf;|' /etc/nginx/nginx.conf \
    && grep -q 'include /tmp/moni-nginx/\*.conf;' /etc/nginx/nginx.conf \
    && rm -f /etc/nginx/sites-enabled/default \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements-docker.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements-docker.txt

COPY backend/moni ./moni

COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

RUN addgroup --system app \
    && adduser --system --ingroup app --home /app app \
    && mkdir -p /data \
    && chown app:app /data

# Supervisor config with logging
RUN echo '[supervisord] \n\
nodaemon=true \n\
logfile=/tmp/supervisord.log \n\
pidfile=/tmp/supervisord.pid \n\
childlogdir=/tmp \n\
\n\
[program:nginx] \n\
command=nginx -g "daemon off;" \n\
autostart=true \n\
autorestart=true \n\
stdout_logfile=/dev/stdout \n\
stdout_logfile_maxbytes=0 \n\
stderr_logfile=/dev/stderr \n\
stderr_logfile_maxbytes=0 \n\
\n\
[program:backend] \n\
command=python -m moni \n\
directory=/app \n\
environment=MONI_ROLE="manager",MONI_PORT="8001",MONI_DATA_DIR="/data",MONI_BIND_HOST="127.0.0.1",MONI_TRUST_LOCAL_PROXY="1" \n\
autostart=true \n\
autorestart=true \n\
stdout_logfile=/dev/stdout \n\
stdout_logfile_maxbytes=0 \n\
stderr_logfile=/dev/stderr \n\
stderr_logfile_maxbytes=0 \n\
' > /etc/supervisor/conf.d/supervisord.conf

RUN mkdir -p /var/log/supervisor

USER app

EXPOSE 80 443

# Ein Befehl für tls=on und off: -L folgt der Umleitung von Port 80; deren Ziel ist der veröffentlichte Port
# (MONI_HTTPS_PORT), im Container lauscht nginx aber auf 443. Ohne Backend liefert nginx 502, die Prüfung schlägt fehl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsSkL --connect-to "127.0.0.1:${MONI_HTTPS_PORT:-8443}:127.0.0.1:443" http://127.0.0.1/api/v1/health

# nginx-Konfiguration und HTTPS-Zertifikat entstehen beim Start aus den Settings (moni/manager/nginx.py)
CMD ["sh", "-c", "python -m moni.cli tls init && exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf"]
