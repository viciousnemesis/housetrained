# Gunicorn, as started by etc/entrypoint.sh. Static files are served by WhiteNoise, photos by Django (signed in only).
bind = "0.0.0.0:8000"
workers = 2  # each runs the reminder checker; reminders are claimed in the database, so each goes out once
worker_class = "gthread"
threads = 4
timeout = 30
worker_tmp_dir = "/dev/shm"
accesslog = "-"
errorlog = "-"
loglevel = "info"
