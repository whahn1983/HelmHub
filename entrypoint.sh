#!/bin/bash
set -e

HTMX_PATH="app/static/js/htmx.min.js"
HTMX_VERSION="1.9.12"

# Download htmx if not present or placeholder
if [ ! -f "$HTMX_PATH" ] || grep -q "placeholder" "$HTMX_PATH" 2>/dev/null; then
    echo "Downloading htmx ${HTMX_VERSION}..."
    curl -fsSL "https://unpkg.com/htmx.org@${HTMX_VERSION}/dist/htmx.min.js" -o "$HTMX_PATH" \
        || wget -qO "$HTMX_PATH" "https://unpkg.com/htmx.org@${HTMX_VERSION}/dist/htmx.min.js" \
        || echo "Warning: Could not download htmx. Install manually."
fi

echo "Running database migrations..."
flask db upgrade

echo "Starting HelmHub..."
exec gunicorn --config gunicorn.conf.py "app:create_app()"
