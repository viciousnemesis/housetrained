"""Demo data command (PUP-40)."""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from pets.models import Household, Pet, PottyBreak, Walk


class DemoTests(TestCase):
    def test_create_and_delete(self):
        user = get_user_model().objects.create_user("demo", password="x")
        call_command("housetrained_demo", user="demo", days=5)
        home = Household.objects.get(name="Demo home", memberships__user=user)
        self.assertEqual(set(Pet.objects.filter(household=home).values_list("name", flat=True)), {"Biscuit", "Miso"})
        self.assertTrue(PottyBreak.objects.filter(pet__household=home).exists())
        self.assertTrue(Walk.objects.filter(household=home).exists())
        self.client.force_login(user)
        for path in ("/pets/", "/pets/biscuit/", "/pets/miso/"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        call_command("housetrained_demo", user="demo", delete=True)
        self.assertFalse(Household.objects.filter(name="Demo home").exists())
