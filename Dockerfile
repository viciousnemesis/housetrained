# Housetrained: Python and its packages, nothing else. Data lives in /config (see etc/entrypoint.sh).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    DJANGO_SETTINGS_MODULE=babybuddy.settings.base \
    DB_NAME=/config/data/db.sqlite3 \
    MEDIA_ROOT=/config/media

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chmod +x etc/entrypoint.sh

EXPOSE 8000
VOLUME /config
ENTRYPOINT ["/app/etc/entrypoint.sh"]
