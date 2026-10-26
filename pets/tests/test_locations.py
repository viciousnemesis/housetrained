"""A household's own potty locations, each an accident or a right place (PUP-181)."""
from django.test import override_settings

from pets import insights, progress
from pets.entries import recent_activity
from pets.models import PottyBreak, PottyLocation
from pets.tests.test_views import PetTestCase


@override_settings(TIME_ZONE="UTC")
class PottyLocationTests(PetTestCase):
    def setUp(self):
        super().setUp()
        self.garage = PottyLocation.objects.create(household=self.household, name="Garage", accident=True)
        self.balcony = PottyLocation.objects.create(household=self.household, name="Balcony", accident=False)

    def add(self, location, **data):
        base = {"pets": [self.ruby.pk], "time": self.now, "pee": "on", "location": location}
        return self.client.post("/log/potty/add/", {**base, **data})

    def test_logged_like_the_built_in_ones(self):
        self.add(self.garage.code, spot_new="Workbench")
        self.add(self.balcony.code)
        garage, balcony = PottyBreak.objects.order_by("pk")
        self.assertEqual((garage.accident, balcony.accident), (True, False))
        self.assertEqual((garage.get_location_display(), garage.spot.name), ("Garage", "Workbench"))
        self.assertIsNone(balcony.spot)  # a right place has no Where
        rows = recent_activity(self.ruby)
        self.assertEqual([r["summary"] for r in rows], ["Pee, balcony", "Pee, garage (Workbench)"])
        self.assertEqual(["accident" in r["tags"] for r in rows], [False, True])
        self.assertEqual(progress.patterns(self.ruby)["spots"], [("Workbench", 1)])  # counted with the accidents
        self.assertTrue(insights.is_accident(garage))

    def test_the_form_offers_them_and_shows_accident_rows_for_accident_ones(self):
        form = self.client.get("/log/potty/add/").context["form"]
        self.assertIn((self.garage.code, "Garage"), form.fields["location"].choices)
        self.assertIn(self.balcony.code, dict(form.fields["location"].choices))
        rules = form.show_when_json
        self.assertIn(self.garage.code, rules["spot"][0][1])
        self.assertNotIn(self.balcony.code, rules["spot"][0][1])
        page = self.client.get("/log/potty/add/")
        self.assertContains(page, 'data-add-to="location_new"')
        for code in ("outside", "inside", self.garage.code, self.balcony.code):
            self.assertContains(page, f'name="location" value="{code}"')
        self.add("somewhere-else")
        self.assertFalse(PottyBreak.objects.exists())  # only this household's locations

    def test_changing_accident_applies_to_logged_ones(self):
        self.add(self.balcony.code)
        self.client.post(f"/log/location/{self.balcony.pk}/", {"name": "Balcony", "accident": "on"})
        self.assertTrue(PottyBreak.objects.get().accident)
        self.assertContains(self.client.get("/pets/setup/"), "Balcony")

    def test_new_from_the_form(self):
        made = self.client.post("/log/potty/location/", {"name": "Car", "accident": "true"}).json()
        car = PottyLocation.objects.get(name="Car")
        self.assertEqual(made, {"ok": True, "code": car.code, "name": "Car", "accident": True})
        again = self.client.post("/log/potty/location/", {"name": "car", "accident": "false"}).json()
        self.assertEqual(again["code"], car.code)  # the same one, still an accident
        built_in = self.client.post("/log/potty/location/", {"name": "outside", "accident": "true"}).json()
        self.assertEqual((built_in["code"], built_in["accident"]), ("outside", False))
        self.assertEqual(self.client.post("/log/potty/location/", {"name": " "}).status_code, 400)

    def test_a_removed_location_reads_other(self):
        self.add(self.balcony.code)
        self.balcony.delete()
        self.assertEqual(PottyBreak.objects.get().get_location_display(), "Other")
