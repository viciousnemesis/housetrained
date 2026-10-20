"""Accident context (PUP-76) and health flags (PUP-79)."""
import datetime

from django.test import override_settings
from django.utils import timezone

from pets import insights, progress
from pets.models import AccidentSpot, PottyBreak
from pets.tests.test_views import PetTestCase


@override_settings(TIME_ZONE="UTC")
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


@override_settings(TIME_ZONE="UTC")
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

    def usual_week(self, start_today, per_day=5):
        for back in range(1, 8):
            day = start_today - datetime.timedelta(days=back) + datetime.timedelta(hours=8)
            for i in range(per_day):
                PottyBreak.objects.create(pet=self.ruby, pee=True, time=day + datetime.timedelta(hours=i))

    def test_many_more_pees_than_usual(self):
        start_today = insights.window_start(1)
        now = start_today + datetime.timedelta(hours=20)
        self.usual_week(start_today)
        for i in range(9):  # 9 separate times, 40 minutes apart
            PottyBreak.objects.create(pet=self.ruby, pee=True, time=start_today + datetime.timedelta(hours=8, minutes=40 * i))
        texts = [t for _level, t in insights.health_alerts(self.ruby, now)]
        self.assertTrue(any("9 separate times today, usually about 5" in t for t in texts), texts)

    def test_top_ups_on_one_walk_count_once(self):
        """PUP-159: three pees on one walk are one time, so a busy day of walks doesn't look like an infection."""
        start_today = insights.window_start(1)
        now = start_today + datetime.timedelta(hours=20)
        self.usual_week(start_today)
        for walk in range(4):
            for i in range(3):
                PottyBreak.objects.create(pet=self.ruby, pee=True,
                                          time=start_today + datetime.timedelta(hours=8 + 3 * walk, minutes=5 * i))
        self.assertEqual(insights.health_alerts(self.ruby, now), [])  # 12 pees logged, but 4 times

    def test_details_show_in_logs(self):
        self.add(poop="on", unusual="on", poop_consistency="soft")
        self.assertContains(self.client.get("/log/potty/"), "too soft")


class HealthCheckSettingsTests(PetTestCase):
    """PUP-159: Setup > Health checks lists every check, with its numbers and history; owners change them."""

    def soft_poops(self, n):
        for i in range(n):
            PottyBreak.objects.create(pet=self.ruby, poop=True, poop_consistency="soft",
                                      time=timezone.now() - datetime.timedelta(hours=i + 1))

    def test_every_check_is_listed(self):
        page = self.client.get("/pets/setup/health/").content.decode()
        for label in ("Black or bloody poop", "Grey, white or yellow poop", "Soft poop", "Dark pee",
                      "Peeing more than usual"):
            self.assertIn(label, page)
        self.assertIn('name="usual_days" value="7"', page)
        self.assertIn("/pets/setup/health/", self.client.get("/pets/setup/").content.decode())

    def test_switching_a_check_off_hides_it(self):
        self.soft_poops(2)
        self.assertTrue(insights.health_alerts(self.ruby))
        on = {"bloody_poop": "on", "pale_poop": "on", "dark_pee": "on", "more_pees": "on"}  # soft_poop left off
        self.client.post("/pets/setup/health/", {**on, "soft_count": "2", "dark_count": "2", "pee_factor": "1.5",
                                                 "pee_extra": "3", "usual_days": "7"})
        self.ruby.household.refresh_from_db()
        self.assertEqual(insights.health_alerts(self.ruby), [])

    def test_numbers_change_when_it_goes_off_and_are_kept_in_range(self):
        self.soft_poops(2)
        self.client.post("/pets/setup/health/", {"soft_poop": "on", "soft_count": "3", "dark_count": "99",
                                                 "pee_factor": "abc", "pee_extra": "3", "usual_days": "1"})
        self.household.refresh_from_db()
        saved = self.household.health_checks
        self.assertEqual((saved["soft_count"], saved["dark_count"], saved["pee_factor"], saved["usual_days"]),
                         (3, 10, 1.5, 3))
        self.ruby.refresh_from_db()
        self.assertEqual(insights.health_alerts(self.ruby), [])  # 2 soft poops, goes off at 3
        self.soft_poops(1)
        self.assertIn("Soft poop 3 times", insights.health_alerts(self.ruby)[0][1])

    def test_shows_when_it_last_went_off(self):
        PottyBreak.objects.create(pet=self.ruby, poop=True, poop_colour="red",
                                  time=timezone.now() - datetime.timedelta(days=3))
        page = self.client.get("/pets/setup/health/").content.decode()
        self.assertIn("last went off", page)
        self.assertIn("hasn't gone off in the past 90 days", page)

    def test_only_owners_change_them(self):
        self.login(self.member)
        self.assertEqual(self.client.post("/pets/setup/health/", {"soft_count": "5"}).status_code, 403)
        self.assertContains(self.client.get("/pets/setup/health/"), "An owner can change these.")
