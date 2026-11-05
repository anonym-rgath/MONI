# Multi-stage build for Linux Monitor
FROM node:26-alpine@sha256:7c6af15abe4e3de859690e7db171d0d711bf37d27528eddfe625b2fe89e097f8 AS frontend-builder

WORKDIR /app/frontend
# node:26-alpine bundelt weder Yarn noch corepack mehr - corepack via npm
# nachinstallieren und aktivieren; Yarn-Version kommt aus dem
# "packageManager"-Feld in package.json.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN npm install -g corepack && corepack enable
COPY frontend/package.json frontend/yarn.lock* ./
RUN yarn install --frozen-lockfile --non-interactive
COPY frontend/ .
RUN yarn build

# Agent-Image: nur der Messdienst (kein nginx, kein Frontend)
FROM python:3.14-slim@sha256:c845af9399020c7e562969a13689e929074a10fd057acd1b1fad06a2fb068e97 AS agent

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

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9810/api/v1/health', timeout=3)"]

CMD ["python", "-m", "moni"]

# Manager-Image: nginx (Frontend + /api-Proxy) und Manager-Backend unter supervisord
FROM python:3.14-slim@sha256:c845af9399020c7e562969a13689e929074a10fd057acd1b1fad06a2fb068e97 AS manager

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    supervisor \
    curl \
    && sed -i 's|^user .*|# user directive disabled for non-root runtime;|' /etc/nginx/nginx.conf \
    && sed -i 's|^pid .*|pid /tmp/nginx.pid;|' /etc/nginx/nginx.conf \
    && sed -i 's|^[[:space:]]*error_log .*|error_log /dev/stderr warn;|' /etc/nginx/nginx.conf \
    && sed -i 's|^[[:space:]]*access_log .*|    access_log /dev/stdout;|' /etc/nginx/nginx.conf \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements-docker.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements-docker.txt

COPY backend/moni ./moni

COPY --from=frontend-builder /app/frontend/build ./frontend/build

RUN addgroup --system app \
    && adduser --system --ingroup app --home /app app \
    && mkdir -p /data \
    && chown app:app /data

# Nginx config
RUN echo '# P3-R6: $uri statt $request, damit der OIDC-code (Query-String) nie im Zugriffslog landet \n\
log_format moni '\''$remote_addr [$time_iso8601] "$request_method $uri" $status $body_bytes_sent "$http_user_agent"'\''; \n\
server { \n\
    listen 80; \n\
    server_name localhost; \n\
    access_log /dev/stdout moni; \n\
    error_log /dev/stderr warn; \n\
    client_max_body_size 1m; \n\
    client_body_temp_path /tmp/nginx-client-body; \n\
    proxy_temp_path /tmp/nginx-proxy; \n\
    fastcgi_temp_path /tmp/nginx-fastcgi; \n\
    uwsgi_temp_path /tmp/nginx-uwsgi; \n\
    scgi_temp_path /tmp/nginx-scgi; \n\
    add_header Content-Security-Policy "default-src '\''self'\''; font-src '\''self'\'' https://fonts.gstatic.com; style-src '\''self'\'' '\''unsafe-inline'\'' https://fonts.googleapis.com; script-src '\''self'\''; connect-src '\''self'\''; img-src '\''self'\'' data:; frame-ancestors '\''none'\''; base-uri '\''self'\''; form-action '\''self'\''" always; \n\
    add_header X-Content-Type-Options "nosniff" always; \n\
    add_header Referrer-Policy "no-referrer" always; \n\
    add_header X-Frame-Options "DENY" always; \n\
    add_header Permissions-Policy "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()" always; \n\
    \n\
    location / { \n\
        root /app/frontend/build; \n\
        try_files $uri $uri/ /index.html; \n\
    } \n\
    \n\
    location /api { \n\
        proxy_pass http://127.0.0.1:8001; \n\
        proxy_set_header Host $host; \n\
        proxy_set_header X-Real-IP $remote_addr; \n\
        proxy_set_header X-Forwarded-For $remote_addr; \n\
        proxy_set_header X-Forwarded-Proto $scheme; \n\
        proxy_read_timeout 30s; \n\
    } \n\
}' > /etc/nginx/sites-available/default

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

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1/api/v1/health || exit 1

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
