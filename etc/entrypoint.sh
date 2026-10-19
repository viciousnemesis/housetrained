#!/bin/sh
# Starts Housetrained in its container. Everything kept lives in /config: data/db.sqlite3 (the database),
# media/ (pet photos) and .secretkey. Runs as PUID:PGID, the user that owns /config.
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}
mkdir -p /config/data /config/media

if [ -z "$SECRET_KEY" ]; then
    if [ ! -s /config/.secretkey ]; then
        python -c "import secrets; print(secrets.token_urlsafe(50), end='')" > /config/.secretkey
        chmod 600 /config/.secretkey
    fi
    SECRET_KEY=$(cat /config/.secretkey)
    export SECRET_KEY
fi

AS_USER=""
if [ "$(id -u)" = "0" ]; then
    chown "$PUID:$PGID" /config /config/data /config/media
    [ ! -e /config/.secretkey ] || chown "$PUID:$PGID" /config/.secretkey
    [ ! -e /config/data/db.sqlite3 ] || chown "$PUID:$PGID" /config/data/db.sqlite3
    AS_USER="setpriv --reuid=$PUID --regid=$PGID --clear-groups"
fi

$AS_USER python manage.py migrate --noinput
exec $AS_USER gunicorn housetrained.wsgi --config etc/gunicorn.py
