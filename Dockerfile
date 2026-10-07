FROM lscr.io/linuxserver/babybuddy:2.11.0
RUN rm -rf /app/www/public
COPY . /app/www/public
