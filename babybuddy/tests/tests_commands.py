# -*- coding: utf-8 -*-
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase


class CommandsTestCase(TransactionTestCase):
    def test_migrate(self):
        call_command("migrate", verbosity=0)
        self.assertIsInstance(get_user_model().objects.get(username="admin"), get_user_model())

    def test_createuser(self):
        call_command("migrate", verbosity=0)
        call_command("createuser", username="regularuser", email="regularuser@test.test", password="test",
                     verbosity=0)
        user = get_user_model().objects.get(username="regularuser")
        self.assertEqual(user.email, "regularuser@test.test")
        self.assertFalse(user.is_staff or user.is_superuser)  # what they can do is set by their household role

        call_command("createuser", "--is-staff", username="staffuser", password="test", verbosity=0)
        user = get_user_model().objects.get(username="staffuser")
        self.assertTrue(user.is_staff and user.is_superuser)  # a site admin
