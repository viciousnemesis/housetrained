"""Security review (PUP-29): nobody reaches another household's data, through pages, forms or the API."""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from pets.models import (
    Container, Fill, Household, Meal, Measurement, Membership, Note, Pet, PottyBreak, Product, Sleep, SupplyItem, Walk,
)


def make_household(name, username):
    user = get_user_model().objects.create_user(username, password="x")
    household = Household.objects.create(name=name)
    Membership.objects.create(household=household, user=user, role=Membership.OWNER)
    pet = Pet.objects.create(household=household, name=f"{name} dog", type=Pet.DOG)
    product = Product.objects.create(household=household, name="Kibble", category=Product.DRY_FOOD)
    bag = SupplyItem.objects.create(product=product, size=1000)
    bowl = Container.objects.create(household=household, name="Bowl", kind=Container.FOOD_BOWL, default_supply=bag)
    bowl.pets.set([pet])
    now = timezone.now()
    walk = Walk.objects.create(household=household, start=now - datetime.timedelta(hours=1), end=now)
    walk.pets.set([pet])
    fill = Fill.objects.create(container=bowl, time=now, contents=Fill.FOOD, amount=50, supply_item=bag)
    return {
        "user": user, "household": household, "pet": pet, "product": product, "supply": bag, "container": bowl,
        "walk": walk, "fill": fill,
        "potty": PottyBreak.objects.create(pet=pet, time=now, pee=True, walk=walk),
        "sleep": Sleep.objects.create(pet=pet, start=now - datetime.timedelta(hours=2), end=now),
        "meal": Meal.objects.create(pet=pet, time=now, fill=fill),
        "measurement": Measurement.objects.create(pet=pet, kind=Measurement.WEIGHT, value=5),
        "note": Note.objects.create(household=household, pet=pet, text="secret"),
    }


@override_settings(BABY_MODE=False)
class HouseholdIsolationTests(TestCase):
    KINDS = ["potty", "walk", "sleep", "fill", "meal", "measurement", "note", "pet", "container", "product", "supply"]

    def setUp(self):
        self.mine = make_household("Mine", "me")
        self.theirs = make_household("Theirs", "them")
        self.client.force_login(self.mine["user"])

    def test_cannot_open_edit_or_delete_their_entries(self):
        for kind in self.KINDS:
            pk = self.theirs[kind].pk
            self.assertEqual(self.client.get(f"/log/{kind}/{pk}/").status_code, 404, kind)
            self.assertEqual(self.client.post(f"/log/{kind}/{pk}/", {}).status_code, 404, kind)
            self.assertEqual(self.client.post(f"/log/{kind}/{pk}/delete/").status_code, 404, kind)
        self.assertTrue(PottyBreak.objects.filter(pk=self.theirs["potty"].pk).exists())

    def test_cannot_stop_their_timers(self):
        walk = Walk.objects.create(household=self.theirs["household"], start=timezone.now())
        sleep = Sleep.objects.create(pet=self.theirs["pet"], start=timezone.now())
        for path in (f"/log/walk/{walk.pk}/end/", f"/log/walk/{walk.pk}/cancel/",
                     f"/log/sleep/{sleep.pk}/end/", f"/log/sleep/{sleep.pk}/cancel/"):
            self.assertEqual(self.client.post(path).status_code, 404, path)
        self.assertTrue(Walk.objects.filter(pk=walk.pk, end=None).exists())

    def test_their_data_is_not_listed(self):
        for kind in ["potty", "walk", "sleep", "fill", "meal", "measurement", "note"]:
            self.assertNotContains(self.client.get(f"/log/{kind}/"), "Theirs dog", msg_prefix=kind)
        self.assertNotContains(self.client.get("/pets/timeline/"), "Theirs dog")
        self.assertNotContains(self.client.get("/pets/setup/"), "Theirs dog")
        self.assertEqual(self.client.get(f"/pets/{self.theirs['pet'].slug}/").status_code, 404)

    def test_forms_reject_their_ids(self):
        now = timezone.localtime().strftime("%Y-%m-%dT%H:%M:%S")
        posts = {
            "potty": {"pets": [self.theirs["pet"].pk], "time": now, "pee": "on", "location": "outside"},
            "meal": {"pets": [self.theirs["pet"].pk], "time": now, "food": "on"},
            "fill": {"container": self.theirs["container"].pk, "time": now, "amount": "5", "unit": "g"},
            "walk": {"pets": [self.theirs["pet"].pk], "start": now, "walker_choice": str(self.theirs["user"].pk),
                     "distance_unit": "km"},
            "note": {"pet": self.theirs["pet"].pk, "time": now, "text": "x"},
        }
        before = {m: m.objects.count() for m in (PottyBreak, Meal, Fill, Walk, Note)}
        for kind, data in posts.items():
            self.assertEqual(self.client.post(f"/log/{kind}/add/", data).status_code, 200, kind)  # form errors
        self.assertEqual(before, {m: m.objects.count() for m in (PottyBreak, Meal, Fill, Walk, Note)})

    def test_own_fill_cannot_use_their_bag(self):
        now = timezone.localtime().strftime("%Y-%m-%dT%H:%M:%S")
        self.client.post("/log/fill/add/", {"container": self.mine["container"].pk, "time": now, "amount": "5",
                                            "unit": "g", "supply_item": self.theirs["supply"].pk})
        self.assertFalse(Fill.objects.filter(supply_item=self.theirs["supply"], container=self.mine["container"]).exists())

    def test_next_cannot_redirect_off_site(self):
        now = timezone.localtime().strftime("%Y-%m-%dT%H:%M:%S")
        response = self.client.post("/log/potty/add/", {"pets": [self.mine["pet"].pk], "time": now, "pee": "on",
                                                        "location": "outside", "next": "https://evil.example/"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(response["Location"].startswith("http"))

    def test_names_are_escaped(self):
        self.mine["pet"].name = '<script>alert("x")</script>'
        self.mine["pet"].save()
        PottyBreak.objects.create(pet=self.mine["pet"], time=timezone.now(), pee=True, notes="<b>hi</b>")
        for path in ("/pets/", "/pets/timeline/", "/log/potty/", "/log/potty/add/", "/pets/setup/"):
            body = self.client.get(path).content.decode()
            self.assertNotIn('<script>alert("x")</script>', body, path)

    def test_bad_timeline_date_is_harmless(self):
        self.assertEqual(self.client.get("/pets/timeline/?date=not-a-date").status_code, 200)

    def test_logged_out_users_are_sent_to_login(self):
        self.client.logout()
        for path in ("/pets/", "/log/potty/", "/log/potty/add/", "/pets/setup/"):
            self.assertEqual(self.client.get(path).status_code, 302, path)
        self.assertIn(APIClient().get("/api/v1/pets/").status_code, (401, 403))


class ApiIsolationTests(TestCase):
    def setUp(self):
        self.mine = make_household("Mine", "me")
        self.theirs = make_household("Theirs", "them")
        self.api = APIClient()
        self.api.force_authenticate(self.mine["user"])

    def test_lists_only_show_mine(self):
        for resource in ("pets", "potty-breaks", "walks", "sleeps", "fills", "meals", "measurements", "notes",
                         "containers", "products", "supplies", "households"):
            data = self.api.get(f"/api/v1/{resource}/").json()
            self.assertEqual(data["count"], 1, resource)

    def test_their_objects_are_not_found(self):
        pairs = {"pets": "pet", "potty-breaks": "potty", "walks": "walk", "fills": "fill", "meals": "meal",
                 "notes": "note", "containers": "container", "supplies": "supply"}
        for resource, key in pairs.items():
            url = f"/api/v1/{resource}/{self.theirs[key].pk}/"
            self.assertEqual(self.api.get(url).status_code, 404, resource)
            self.assertEqual(self.api.delete(url).status_code, 404, resource)

    def test_cannot_link_to_their_objects(self):
        attempts = [
            ("potty-breaks", {"pet": self.mine["pet"].pk, "pee": True, "walk": self.theirs["walk"].pk}),
            ("potty-breaks", {"pet": self.theirs["pet"].pk, "pee": True}),
            ("meals", {"pet": self.mine["pet"].pk, "fill": self.theirs["fill"].pk}),
            ("meals", {"pet": self.mine["pet"].pk, "product": self.theirs["product"].pk}),
            ("fills", {"container": self.mine["container"].pk, "amount": 5, "supply_item": self.theirs["supply"].pk}),
            ("walks", {"pets": [self.theirs["pet"].pk]}),
            ("supplies", {"product": self.theirs["product"].pk, "size": 10}),
            ("containers", {"name": "x", "kind": "food_bowl", "default_supply": self.theirs["supply"].pk}),
        ]
        for resource, payload in attempts:
            response = self.api.post(f"/api/v1/{resource}/", payload, format="json")
            self.assertEqual(response.status_code, 400, (resource, payload, response.content[:200]))

    def test_cannot_rename_their_household(self):
        response = self.api.patch(f"/api/v1/households/{self.theirs['household'].pk}/", {"name": "pwned"}, format="json")
        self.assertEqual(response.status_code, 404)


@override_settings(BABY_MODE=False)
class MemberTests(TestCase):
    def setUp(self):
        self.mine = make_household("Mine", "me")
        self.theirs = make_household("Theirs", "them")

    def test_owner_cannot_pull_in_a_stranger(self):
        self.client.force_login(self.mine["user"])
        response = self.client.post("/pets/setup/members/", {"user": self.theirs["user"].pk, "role": "member"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Membership.objects.filter(household=self.mine["household"], user=self.theirs["user"]).exists())
        self.assertNotContains(self.client.get("/pets/setup/members/"), f'<option value="{self.theirs["user"].pk}">')

    def test_site_admin_can_add_people(self):
        admin = get_user_model().objects.create_superuser("siteadmin", password="x")
        Membership.objects.create(household=self.mine["household"], user=admin, role=Membership.OWNER)
        newcomer = get_user_model().objects.create_user("newcomer", password="x")
        self.client.force_login(admin)
        self.client.post("/pets/setup/members/", {"user": newcomer.pk, "role": "viewer"})
        self.assertEqual(Membership.objects.get(user=newcomer).household, self.mine["household"])

    def test_owner_can_change_and_remove_members(self):
        helper = get_user_model().objects.create_user("helper", password="x")
        Membership.objects.create(household=self.mine["household"], user=helper, role=Membership.MEMBER)
        self.client.force_login(self.mine["user"])
        self.client.post("/pets/setup/members/", {"user": helper.pk, "role": "viewer"})
        self.assertEqual(Membership.objects.get(user=helper).role, Membership.VIEWER)
        self.client.post("/pets/setup/members/", {"user": helper.pk, "role": "remove"})
        self.assertFalse(Membership.objects.filter(user=helper, household=self.mine["household"]).exists())

    def test_joining_keeps_a_household_with_data(self):
        from pets.households import add_member

        user = get_user_model().objects.create_user("joiner", password="x")
        own = Household.objects.create(name="Joiner's")
        Membership.objects.create(household=own, user=user, role=Membership.OWNER)
        Container.objects.create(household=own, name="Bowl", kind=Container.FOOD_BOWL)
        add_member(self.mine["household"], user, Membership.MEMBER)
        self.assertTrue(Household.objects.filter(pk=own.pk).exists())

    def test_joining_removes_an_untouched_household(self):
        from pets.households import add_member

        user = get_user_model().objects.create_user("joiner", password="x")
        own = Household.objects.create(name="Home")
        Membership.objects.create(household=own, user=user, role=Membership.OWNER)
        add_member(self.mine["household"], user, Membership.MEMBER)
        self.assertFalse(Household.objects.filter(pk=own.pk).exists())


class BabyApiGateTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(get_user_model().objects.create_superuser("siteadmin", password="x"))

    @override_settings(BABY_MODE=False)
    def test_baby_api_is_off_in_pet_mode(self):
        for path in ("/api/", "/api/children/", "/api/changes/", "/api/schema"):
            self.assertEqual(self.api.get(path).status_code, 404, path)
        self.assertEqual(self.api.get("/api/v1/").status_code, 200)

    @override_settings(BABY_MODE=True)
    def test_baby_api_works_in_baby_mode(self):
        self.assertEqual(self.api.get("/api/children/").status_code, 200)


@override_settings(BABY_HOUSEHOLDS=True)
class UserFormTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("siteadmin", password="x")
        self.client.force_login(self.admin)

    def test_new_users_are_not_admins_by_default(self):
        self.client.post("/users/add/", {"username": "helper", "first_name": "Hel", "password1": "Sup3r-secret-pw",
                                         "password2": "Sup3r-secret-pw", "is_active": "on"})
        helper = get_user_model().objects.get(username="helper")
        self.assertFalse(helper.is_superuser)

    def test_site_admin_is_explicit(self):
        page = self.client.get("/users/add/")
        self.assertContains(page, "Site admin")
        self.assertNotContains(page, "Caregiver")
        self.client.post("/users/add/", {"username": "boss", "password1": "Sup3r-secret-pw",
                                         "password2": "Sup3r-secret-pw", "is_staff": "on", "is_active": "on"})
        self.assertTrue(get_user_model().objects.get(username="boss").is_superuser)
