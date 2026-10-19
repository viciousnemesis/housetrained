# -*- coding: utf-8 -*-
import os
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.commands import migrate


class Command(migrate.Command):
    help = "Migrates, then creates the first admin user if there is none."

    def handle(self, *args, **kwargs):
        super(Command, self).handle(*args, **kwargs)

        superusers = get_user_model().objects.filter(is_superuser=True)
        if len(superusers) == 0:
            # Housetrained: no well-known admin/admin. Use ADMIN_PASSWORD if set, otherwise a random password that
            # is shown once in the log. Test settings keep "admin" (DEFAULT_ADMIN_PASSWORD) for the tests.
            chosen = os.environ.get("ADMIN_PASSWORD") or getattr(settings, "DEFAULT_ADMIN_PASSWORD", None)
            password = chosen or secrets.token_urlsafe(12)
            default_user = get_user_model().objects.create_user("admin", password=password)
            default_user.is_superuser = True
            default_user.is_staff = True
            default_user.save()
            if not chosen:
                self.stdout.write(self.style.WARNING(
                    "\n  Housetrained first run: created user 'admin' with password: %s\n"
                    "  Sign in and change it (user menu > Password). This is shown only once.\n" % password
                ))
