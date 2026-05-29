# Multi-stage build for Pi Monitor
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package.json frontend/yarn.lock* ./
RUN yarn install --frozen-lockfile --non-interactive
COPY frontend/ .
RUN yarn build

# Final Image
FROM python:3.11-slim@sha256:a3ab0b966bc4e91546a033e22093cb840908979487a9fc0e6e38295747e49ac0

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

COPY backend/server.py ./backend/

COPY --from=frontend-builder /app/frontend/build ./frontend/build

RUN addgroup --system app \
    && adduser --system --ingroup app --home /app app \
    && chown -R app:app /app

# Nginx config
RUN echo 'server { \n\
    listen 80; \n\
    server_name localhost; \n\
    access_log /dev/stdout; \n\
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
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; \n\
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
stdout_logfile=/tmp/nginx.log \n\
stderr_logfile=/tmp/nginx.err.log \n\
\n\
[program:backend] \n\
command=uvicorn server:app --host 0.0.0.0 --port 8001 \n\
directory=/app/backend \n\
autostart=true \n\
autorestart=true \n\
stdout_logfile=/tmp/backend.log \n\
stderr_logfile=/tmp/backend.err.log \n\
' > /etc/supervisor/conf.d/supervisord.conf

RUN mkdir -p /var/log/supervisor

USER app

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1/api/ || exit 1

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
