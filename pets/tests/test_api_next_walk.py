"""Next walk for Home Assistant (PUP-196): /api/v1/pets/<id>/next-walk/."""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from pets.models import Membership, PottyBreak, Walk

from .test_security import make_household


class NextWalkApiTests(TestCase):
    def setUp(self):
        self.mine = make_household("Mine", "me")
        self.pet = self.mine["pet"]
        Walk.objects.all().delete()
        PottyBreak.objects.all().delete()
        self.api = APIClient()
        self.api.force_authenticate(self.mine["user"])
        self.url = f"/api/v1/pets/{self.pet.pk}/next-walk/"

    def test_head_out_time(self):
        PottyBreak.objects.create(pet=self.pet, time=timezone.now() - datetime.timedelta(minutes=30), pee=True)
        data = self.api.get(self.url).json()
        self.assertTrue(data["state"].startswith("Head out ~"), data)
        self.assertIsNotNone(data["head_out"])
        self.assertIsNone(data["on_walk_since"])
        self.assertEqual(data["pet"], self.pet.name)

    def test_on_a_walk(self):
        walk = Walk.objects.create(household=self.mine["household"], start=timezone.now() - datetime.timedelta(minutes=5))
        walk.pets.set([self.pet])
        data = self.api.get(self.url).json()
        self.assertTrue(data["state"].startswith("On a walk since"), data)
        self.assertIsNotNone(data["on_walk_since"])

    def test_nothing_logged(self):
        data = self.api.get(self.url).json()
        self.assertEqual(data["state"], "Nothing logged yet")
        self.assertIsNone(data["head_out"])

    def test_read_only_member_with_an_api_key(self):
        viewer = get_user_model().objects.create_user("ha", password="x")
        Membership.objects.create(household=self.mine["household"], user=viewer, role=Membership.VIEWER)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=viewer).key}")
        self.assertEqual(api.get(self.url).status_code, 200)
        self.assertIn(api.post(self.url).status_code, (403, 405))

    def test_other_households_and_strangers(self):
        theirs = make_household("Theirs", "them")
        self.assertEqual(self.api.get(f"/api/v1/pets/{theirs['pet'].pk}/next-walk/").status_code, 404)
        self.assertIn(APIClient().get(self.url).status_code, (401, 403))
