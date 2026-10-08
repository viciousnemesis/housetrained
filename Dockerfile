FROM lscr.io/linuxserver/babybuddy:2.11.0
# Browser push notifications (reminders).
RUN /lsiopy/bin/pip install --no-cache-dir "pywebpush>=2.0,<3"
RUN rm -rf /app/www/public
COPY . /app/www/public
