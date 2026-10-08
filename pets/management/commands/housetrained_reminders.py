"""Send any due reminders once (the web server also does this every few minutes on its own)."""
from django.core.management.base import BaseCommand

from pets import reminders


class Command(BaseCommand):
    help = "Sends due walk and buy-more reminders to devices with notifications turned on."

    def handle(self, *args, **options):
        sent = reminders.check()
        self.stdout.write(f"Delivered {sent} notification(s).")
