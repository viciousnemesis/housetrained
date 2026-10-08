"""Babies alongside pets, per household (PUP-71)."""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Child, DiaperChange, Timer
from pets.models import BabyHousehold, Household, Membership, Pet


@override_settings(BABY_MODE=True, BABY_HOUSEHOLDS=True)
class BabyHouseholdTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_user("owner", password="x")
        self.helper = users.create_user("helper", password="x")
        self.viewer = users.create_user("viewer", password="x")
        self.stranger = users.create_user("stranger", password="x")
        self.home = Household.objects.create(name="Home")
        Membership.objects.create(household=self.home, user=self.owner, role=Membership.OWNER)
        Membership.objects.create(household=self.home, user=self.helper, role=Membership.MEMBER)
        Membership.objects.create(household=self.home, user=self.viewer, role=Membership.VIEWER)
        self.pet = Pet.objects.create(household=self.home, name="Rex", type=Pet.DOG)
        self.client.force_login(self.owner)
        self.client.post("/children/add/", {"first_name": "Ada", "last_name": "L", "birth_date": "2026-09-01"})
        self.baby = Child.objects.get(first_name="Ada")

    def test_new_baby_joins_the_household(self):
        self.assertEqual(BabyHousehold.objects.get(child=self.baby).household, self.home)

    def test_family_home_shows_pets_and_babies(self):
        self.assertRedirects(self.client.get("/"), "/pets/", fetch_redirect_response=False)
        page = self.client.get("/pets/")
        self.assertContains(page, "Rex")
        self.assertContains(page, "Ada")
        self.assertContains(page, f"/children/{self.baby.slug}/dashboard/")

    def test_baby_menu_beside_pet_menus(self):
        page = self.client.get("/pets/").content.decode()
        self.assertIn('href="/log/potty/"', page)
        self.assertIn("Baby dashboard", page)

    @override_settings(BABY_MODE=False)
    def test_no_baby_menu_without_baby_mode(self):
        page = self.client.get("/pets/")
        self.assertNotContains(page, "Baby dashboard")
        self.assertNotContains(page, "Ada")

    def test_other_households_cannot_see_the_baby(self):
        DiaperChange.objects.create(child=self.baby, time=timezone.now(), wet=True, solid=False)
        self.client.force_login(self.stranger)
        self.assertNotContains(self.client.get("/children/"), "Ada")
        self.assertEqual(self.client.get(f"/children/{self.baby.slug}/").status_code, 404)
        self.assertNotContains(self.client.get("/changes/"), "Ada")
        api = APIClient()
        api.force_authenticate(self.stranger)
        self.assertEqual(api.get("/api/children/").json()["count"], 0)
        self.assertEqual(api.get("/api/changes/").json()["count"], 0)

    def test_stranger_cannot_open_an_entry_by_number(self):
        with self.settings(BABY_HOUSEHOLDS=False):
            change = DiaperChange.objects.create(child=self.baby, time=timezone.now(), wet=True, solid=False)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(f"/changes/{change.pk}/").status_code, 404)

    def test_roles_apply_to_babies(self):
        self.client.force_login(self.helper)
        response = self.client.post("/changes/add/", {"child": self.baby.pk, "time": timezone.localtime().strftime("%Y-%m-%d %H:%M:%S"), "wet": "on"})
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get("/changes/").status_code, 200)
        self.assertEqual(self.client.get("/changes/add/").status_code, 403)

    def test_children_from_an_old_install_stay_hidden(self):
        with self.settings(BABY_HOUSEHOLDS=False):
            Child.objects.create(first_name="Ruby", last_name="", birth_date=datetime.date(2026, 7, 31))
        self.assertNotContains(self.client.get("/pets/"), "Ruby")
        self.assertNotContains(self.client.get("/children/"), "Ruby")

    def test_timers_follow_the_household(self):
        Timer.objects.create(user=self.owner, child=self.baby, start=timezone.now())
        Timer.objects.create(user=self.stranger, start=timezone.now())
        self.assertEqual(self.client.get("/timers/").context["object_list"].count(), 1)


@override_settings(BABY_MODE=True, BABY_HOUSEHOLDS=True)
class BabyLeakTests(TestCase):
    """Choices and lists Baby Buddy builds at startup must still only offer the household's babies."""

    def setUp(self):
        users = get_user_model().objects
        self.parent = users.create_user("parent", password="x")
        self.stranger = users.create_user("stranger", password="x")
        self.home = Household.objects.create(name="Home")
        Membership.objects.create(household=self.home, user=self.parent, role=Membership.OWNER)
        with self.settings(BABY_HOUSEHOLDS=False):
            self.baby = Child.objects.create(first_name="Ada", last_name="L", birth_date=datetime.date(2026, 9, 1))
        BabyHousehold.objects.create(child=self.baby, household=self.home)
        self.client.force_login(self.stranger)

    def test_choices_do_not_offer_the_baby(self):
        for path in ("/changes/", "/changes/add/", "/feedings/add/", "/timers/add/", "/notes/add/"):
            self.assertNotContains(self.client.get(path), "Ada", msg_prefix=path)

    def test_forms_refuse_the_baby(self):
        now = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
        self.client.post("/changes/add/", {"child": self.baby.pk, "time": now, "wet": "on"})
        self.client.post("/notes/add/", {"child": self.baby.pk, "time": now, "note": "x"})
        with self.settings(BABY_HOUSEHOLDS=False):
            self.assertFalse(DiaperChange.objects.exists())
            self.assertEqual(self.baby.note.count(), 0)

    def test_api_refuses_the_baby(self):
        api = APIClient()
        api.force_authenticate(self.stranger)
        self.assertEqual(api.get("/api/children/").json()["count"], 0)
        self.assertEqual(api.get(f"/api/children/{self.baby.slug}/").status_code, 404)
        response = api.post("/api/changes/", {"child": self.baby.pk, "time": timezone.now().isoformat(),
                                              "wet": True, "solid": False}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_parent_still_sees_the_baby(self):
        self.client.force_login(self.parent)
        self.assertContains(self.client.get("/changes/add/"), "Ada")
        api = APIClient()
        api.force_authenticate(self.parent)
        self.assertEqual(api.get("/api/children/").json()["count"], 1)
