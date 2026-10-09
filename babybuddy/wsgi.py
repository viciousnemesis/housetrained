from django.core.wsgi import get_wsgi_application

from dotenv import load_dotenv, find_dotenv

# Environment variables
# Check for and load environment variables from a .env file.
load_dotenv(find_dotenv())

application = get_wsgi_application()

# Housetrained: start the reminder checker with the server, not on the first page visit after a restart.
from pets.reminders import start as start_reminders  # noqa: E402

start_reminders()
