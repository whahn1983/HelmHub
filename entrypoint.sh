#!/bin/bash
set -e

HTMX_PATH="app/static/js/htmx.min.js"
# Fetch the latest published release of htmx on every container startup.
# The committed file in the repo is a pinned dev fallback; production always
# pulls fresh so it stays current without requiring image rebuilds.
HTMX_URL="https://unpkg.com/htmx.org/dist/htmx.min.js"

echo "Downloading latest htmx..."
curl -fsSL "$HTMX_URL" -o "$HTMX_PATH" \
    || wget -qO "$HTMX_PATH" "$HTMX_URL" \
    || echo "Warning: Could not download htmx. Falling back to bundled version."

echo "Running database migrations..."
flask db upgrade

echo "Starting HelmHub..."
exec gunicorn --config gunicorn.conf.py "app:create_app()"
