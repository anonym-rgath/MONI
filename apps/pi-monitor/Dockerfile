# Multi-stage build for Pi Monitor
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package.json frontend/yarn.lock* ./
RUN yarn install || yarn install --ignore-engines
COPY frontend/ .
RUN yarn build

# Final Image
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    supervisor \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements-docker.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements-docker.txt

COPY backend/server.py ./backend/

COPY --from=frontend-builder /app/frontend/build ./frontend/build

# Nginx config
RUN echo 'server { \n\
    listen 80; \n\
    server_name localhost; \n\
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
logfile=/var/log/supervisor/supervisord.log \n\
\n\
[program:nginx] \n\
command=nginx -g "daemon off;" \n\
autostart=true \n\
autorestart=true \n\
stdout_logfile=/var/log/supervisor/nginx.log \n\
stderr_logfile=/var/log/supervisor/nginx.err.log \n\
\n\
[program:backend] \n\
command=uvicorn server:app --host 0.0.0.0 --port 8001 \n\
directory=/app/backend \n\
autostart=true \n\
autorestart=true \n\
stdout_logfile=/var/log/supervisor/backend.log \n\
stderr_logfile=/var/log/supervisor/backend.err.log \n\
' > /etc/supervisor/conf.d/supervisord.conf

RUN mkdir -p /var/log/supervisor

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1/api/ || exit 1

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
