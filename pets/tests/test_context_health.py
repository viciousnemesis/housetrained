"""Accident context (PUP-76) and health flags (PUP-79)."""
import datetime

from django.test import override_settings
from django.utils import timezone

from pets import insights, progress
from pets.models import AccidentSpot, PottyBreak
from pets.tests.test_views import PetTestCase


@override_settings(BABY_MODE=False, TIME_ZONE="UTC")
class AccidentContextTests(PetTestCase):
    def setUp(self):
        super().setUp()
        self.rug = AccidentSpot.objects.create(household=self.household, name="Dining rug")

    def add(self, **data):
        base = {"pets": [self.ruby.pk], "time": self.now, "pee": "on", "location": "inside"}
        return self.client.post("/log/potty/add/", {**base, **data})

    def test_context_and_spot_saved_for_accidents(self):
        self.add(spot=self.rug.pk, context=["unsupervised", "after_play"])
        b = PottyBreak.objects.get()
        self.assertEqual((b.spot, b.context), (self.rug, ["unsupervised", "after_play"]))

    def test_not_kept_for_outside_breaks(self):
        self.add(location="outside", spot=self.rug.pk, context=["unsupervised"])
        b = PottyBreak.objects.get()
        self.assertEqual((b.spot, b.context), (None, []))

    def test_spots_are_managed_in_setup(self):
        self.client.post("/log/spot/add/", {"name": "Back door"})
        self.assertTrue(AccidentSpot.objects.filter(household=self.household, name="Back door").exists())
        self.assertContains(self.client.get("/pets/setup/"), "Back door")

    def test_quick_accident_goes_on_to_details(self):
        response = self.client.post("/log/potty/quick/", {"pet": ["ruby"], "what": "pee", "location": "inside"})
        b = PottyBreak.objects.get()
        self.assertTrue(response["Location"].startswith(f"/log/potty/{b.pk}/"))
        outside = self.client.post("/log/potty/quick/", {"pet": ["ruby"], "what": "pee", "location": "outside"})
        self.assertFalse(outside["Location"].startswith("/log/potty/"))

    def test_progress_shows_patterns(self):
        for _i in range(2):
            self.add(spot=self.rug.pk, context=["after_play"])
        found = progress.patterns(self.ruby)
        self.assertEqual(found["spots"], [("Dining rug", 2)])
        self.assertEqual(found["contexts"], [("After play", 2)])
        self.assertContains(self.client.get("/pets/ruby/progress/"), "Dining rug")


@override_settings(BABY_MODE=False, TIME_ZONE="UTC")
class HealthTests(PetTestCase):
    def add(self, **data):
        base = {"pets": [self.ruby.pk], "time": self.now, "location": "outside"}
        return self.client.post("/log/potty/add/", {**base, **data})

    def test_details_only_when_unusual(self):
        self.add(poop="on", poop_consistency="soft")  # "Something unusual?" not ticked
        self.assertEqual(PottyBreak.objects.get().poop_consistency, "")

    def test_unusual_details_saved_for_what_happened(self):
        self.add(poop="on", unusual="on", poop_consistency="soft", poop_colour="green", pee_colour="dark")
        b = PottyBreak.objects.get()
        self.assertEqual((b.poop_consistency, b.poop_colour, b.pee_colour), ("soft", "green", ""))  # no pee logged

    def test_black_poop_warns_on_the_pet_page(self):
        self.add(poop="on", unusual="on", poop_colour="black")
        self.assertContains(self.client.get("/pets/ruby/"), "call your vet")

    def test_no_warning_when_all_is_normal(self):
        self.add(pee="on")
        self.assertEqual(insights.health_alerts(self.ruby), [])
        page = self.client.get("/pets/ruby/")
        self.assertNotContains(page, "alert-warning")
        self.assertNotContains(page, "alert-danger")

    def test_many_more_pees_than_usual(self):
        now = timezone.now()
        start_today = insights.window_start(1)
        for back in range(1, 8):
            day = start_today - datetime.timedelta(days=back) + datetime.timedelta(hours=8)
            for i in range(5):
                PottyBreak.objects.create(pet=self.ruby, pee=True, time=day + datetime.timedelta(hours=i))
        for i in range(9):
            PottyBreak.objects.create(pet=self.ruby, pee=True,
                                      time=max(start_today, now - datetime.timedelta(minutes=i + 1)))
        texts = [t for _level, t in insights.health_alerts(self.ruby, now)]
        self.assertTrue(any("usually about 5" in t for t in texts), texts)

    def test_details_show_in_logs(self):
        self.add(poop="on", unusual="on", poop_consistency="soft")
        self.assertContains(self.client.get("/log/potty/"), "too soft")
