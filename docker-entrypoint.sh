#!/usr/bin/env bash

set -Eeuo pipefail

AUTH_USER="${PI_MONITOR_BASIC_AUTH_USER:-}"
AUTH_PASSWORD="${PI_MONITOR_BASIC_AUTH_PASSWORD:-}"
AUTH_SNIPPET="/tmp/nginx-auth.conf"
AUTH_FILE="/tmp/nginx.htpasswd"

if [ -n "$AUTH_USER" ] || [ -n "$AUTH_PASSWORD" ]; then
    if [ -z "$AUTH_USER" ] || [ -z "$AUTH_PASSWORD" ]; then
        echo "PI_MONITOR_BASIC_AUTH_USER und PI_MONITOR_BASIC_AUTH_PASSWORD muessen beide gesetzt sein." >&2
        exit 1
    fi

    python3 - "$AUTH_USER" "$AUTH_PASSWORD" "$AUTH_FILE" <<'PY'
import crypt
import secrets
import sys

user, password, path = sys.argv[1], sys.argv[2], sys.argv[3]
salt = "$6$" + secrets.token_urlsafe(16)
hashed_password = crypt.crypt(password, salt)

with open(path, "w", encoding="utf-8") as htpasswd:
    htpasswd.write(f"{user}:{hashed_password}\n")
PY

    chmod 600 "$AUTH_FILE"
    cat > "$AUTH_SNIPPET" <<'EOF'
satisfy any;
allow 127.0.0.1;
deny all;
auth_basic "Pi Monitor";
auth_basic_user_file /tmp/nginx.htpasswd;
EOF
else
    : > "$AUTH_SNIPPET"
fi

exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
