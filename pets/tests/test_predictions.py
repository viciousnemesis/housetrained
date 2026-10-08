"""Next Walk prediction edge cases (PUP-18)."""
import datetime
import random
import zoneinfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from pets import insights
from pets.insights import Night, Rules, next_walk_card
from pets.models import Container, Fill, Household, Meal, Membership, Pet, PottyBreak

LA = zoneinfo.ZoneInfo("America/Los_Angeles")


# Tests run "at" midday today, so the time of day they run doesn't push predictions into the night.
NOW = None


def ago(**kwargs):
    return NOW - datetime.timedelta(**kwargs)


@override_settings(TIME_ZONE="America/Los_Angeles")
class PredictionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="x")
        self.household = Household.objects.create(name="Home")
        Membership.objects.create(household=self.household, user=self.user, role=Membership.OWNER)
        timezone.activate(LA)
        global NOW
        NOW = timezone.make_aware(datetime.datetime.combine(timezone.localdate(), datetime.time(12)), LA)

    def tearDown(self):
        timezone.deactivate()

    def pet(self, name="Ruby", months=None, type=Pet.DOG):
        birth = timezone.localdate() - datetime.timedelta(days=round(months * 30.44)) if months is not None else None
        return Pet.objects.create(household=self.household, name=name, type=type, birth_date=birth)

    def potty(self, pet, when, pee=True, poop=False, location=PottyBreak.OUTSIDE):
        return PottyBreak.objects.create(pet=pet, time=when, pee=pee, poop=poop, location=location)

    def routine(self, pet, days, hours, gap_hours, poop_every=2):
        """A regular day: potty breaks every `gap_hours` across `hours`, for `days` days."""
        today = timezone.localdate()
        for d in range(days, 0, -1):
            day = today - datetime.timedelta(days=d)
            for i, hour in enumerate(range(hours[0], hours[1], gap_hours)):
                when = timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour)), LA)
                self.potty(pet, when, poop=i % poop_every == 0)

    # --- no or little data -----------------------------------------------------------------
    def test_pet_with_no_data(self):
        card = next_walk_card(self.pet(), now=NOW)
        self.assertEqual(card["predictions"], [])
        self.assertIsNone(card["due"])

    def test_pet_page_renders_without_data(self):
        pet = self.pet()
        self.client.force_login(self.user)
        response = self.client.get(f"/pets/{pet.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not enough data")

    def test_young_puppy_uses_age_rule(self):
        pet = self.pet(months=2)  # about 9 weeks: can hold it ~3 hours
        last = ago(minutes=30)
        self.potty(pet, last)
        pee = next_walk_card(pet, now=NOW)["predictions"][0]
        self.assertIn("week-old", pee["reason"])
        self.assertAlmostEqual((pee["time"] - last).total_seconds() / 3600, 3, delta=0.2)

    def test_age_rule_is_capped(self):
        pet = self.pet(months=36)
        last = ago(minutes=30)
        self.potty(pet, last)
        pee = next_walk_card(pet, now=NOW)["predictions"][0]
        self.assertAlmostEqual((pee["time"] - last).total_seconds() / 3600, 8, delta=0.01)
        pee = next_walk_card(pet, now=NOW, rules=Rules(max_hold_hours=5))["predictions"][0]
        self.assertAlmostEqual((pee["time"] - last).total_seconds() / 3600, 5, delta=0.01)

    def test_unknown_birth_date_assumes_adult(self):
        pet = self.pet(months=None)
        self.potty(pet, ago(minutes=10))
        self.assertIn("month-old", next_walk_card(pet, now=NOW)["predictions"][0]["reason"])

    # --- learning from history ---------------------------------------------------------------
    def test_learns_regular_gaps(self):
        pet = self.pet(months=6)
        self.routine(pet, days=6, hours=(6, 22), gap_hours=3)
        last = self.potty(pet, ago(minutes=20)).time
        pee = [p for p in next_walk_card(pet, now=NOW)["predictions"] if p["kind"] == "Pee"][0]
        self.assertIn("usually", pee["reason"])
        self.assertLess(abs((pee["time"] - last) - datetime.timedelta(hours=3)), datetime.timedelta(minutes=5))

    def test_very_irregular_schedule_still_predicts(self):
        pet = self.pet(months=6)
        rnd = random.Random(7)
        when = ago(days=10)
        while when < ago(minutes=30):
            self.potty(pet, when, poop=rnd.random() < 0.3)
            when += datetime.timedelta(minutes=rnd.randint(20, 14 * 60))  # some gaps are longer than max_gap
        card = next_walk_card(pet, now=NOW)
        self.assertTrue(card["predictions"])
        for p in card["predictions"]:
            self.assertGreater(p["time"], p["last"])

    def test_gaps_longer_than_max_gap_are_ignored(self):
        pet = self.pet(months=6)
        self.routine(pet, days=6, hours=(8, 9), gap_hours=1)  # one break a day: 24h gaps only
        self.potty(pet, ago(minutes=5))
        pee = next_walk_card(pet, now=NOW)["predictions"][0]
        self.assertNotIn("apart", pee["reason"])  # nothing learnable, falls back

    # --- meals ---------------------------------------------------------------------------------
    def test_meal_after_last_potty_brings_prediction_forward(self):
        pet = self.pet(months=36)
        self.potty(pet, ago(hours=1))
        meal = Meal.objects.create(pet=pet, time=ago(minutes=10))
        pee = next_walk_card(pet, now=NOW)["predictions"][0]
        self.assertIn("meal at", pee["reason"])
        self.assertEqual(pee["time"], meal.time + Rules().meal_delay["pee"])

    def test_bowl_fill_is_not_a_meal(self):
        pet = self.pet(months=36)
        self.potty(pet, ago(hours=1))
        bowl = Container.objects.create(household=self.household, name="Bowl", kind=Container.FOOD_BOWL)
        bowl.pets.set([pet])
        Fill.objects.create(container=bowl, time=ago(minutes=10), contents=Fill.FOOD, amount=60)
        self.assertNotIn("meal at", next_walk_card(pet, now=NOW)["predictions"][0]["reason"])

    def test_water_drives_pee_not_poop(self):
        pet = self.pet(months=36)
        self.potty(pet, ago(hours=1), pee=True, poop=True)
        Meal.objects.create(pet=pet, time=ago(minutes=10), food=False, water=True)
        by_kind = {p["kind"]: p for p in next_walk_card(pet, now=NOW)["predictions"]}
        self.assertIn("meal at", by_kind["Pee"]["reason"])
        self.assertNotIn("meal at", by_kind["Poop"]["reason"])

    def test_learned_meal_delay(self):
        pet = self.pet(months=12)
        for d in range(1, 5):
            meal = Meal.objects.create(pet=pet, time=ago(days=d, hours=2))
            self.potty(pet, meal.time + datetime.timedelta(minutes=45))
        self.potty(pet, ago(hours=1))
        meal = Meal.objects.create(pet=pet, time=ago(minutes=5))
        pee = next_walk_card(pet, now=NOW)["predictions"][0]
        self.assertEqual(pee["time"], meal.time + datetime.timedelta(minutes=45))

    # --- several pets ----------------------------------------------------------------------
    def test_pets_keep_their_own_routines(self):
        fast, slow = self.pet("Fast", months=6), self.pet("Slow", months=6)
        self.routine(fast, days=6, hours=(6, 22), gap_hours=2)
        self.routine(slow, days=6, hours=(6, 22), gap_hours=5)
        last = ago(minutes=20)
        self.potty(fast, last)
        self.potty(slow, last)
        fast_pee = [p for p in next_walk_card(fast, now=NOW)["predictions"] if p["kind"] == "Pee"][0]
        slow_pee = [p for p in next_walk_card(slow, now=NOW)["predictions"] if p["kind"] == "Pee"][0]
        self.assertLess(fast_pee["time"], slow_pee["time"])

    # --- the night and daylight saving --------------------------------------------------------
    def nights(self, dates, bed=22, wake=6):
        """Potty breaks every 4 hours from wake-up to bedtime on each date."""
        times = []
        for day in dates:
            for hour in range(wake, bed + 1, 4):
                times.append(timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour)), LA))
        return sorted(times)

    def test_night_learned_in_local_time_across_dst_end(self):
        # US daylight saving ends 2026-11-01 at 02:00: that night is an hour longer in real time.
        dates = [datetime.date(2026, 10, 29) + datetime.timedelta(days=i) for i in range(5)]
        times = self.nights(dates)
        night = Night(times, times[-1])
        self.assertEqual(round(night.bed), 22 * 60)
        self.assertEqual(round(night.wake), 6 * 60)

    def test_night_learned_across_dst_start(self):
        dates = [datetime.date(2026, 3, 5) + datetime.timedelta(days=i) for i in range(5)]  # DST starts Mar 8
        times = self.nights(dates)
        night = Night(times, times[-1])
        self.assertEqual(round(night.bed), 22 * 60)
        self.assertEqual(round(night.wake), 6 * 60)

    def test_next_wake_lands_on_local_wake_time_over_dst(self):
        night = Night([], timezone.now())  # defaults: 23:00 to 06:30
        bedtime = timezone.make_aware(datetime.datetime(2026, 10, 31, 23, 0), LA)
        wake = night.next_wake(bedtime)
        self.assertEqual((wake.date(), wake.hour, wake.minute), (datetime.date(2026, 11, 1), 6, 30))
        utc = datetime.timezone.utc
        self.assertEqual(wake.astimezone(utc) - bedtime.astimezone(utc), datetime.timedelta(hours=8, minutes=30))

    def test_gaps_across_dst_use_real_time(self):
        # 01:30 PDT and 01:30 PST on 2026-11-01 are an hour apart, not zero.
        first = datetime.datetime(2026, 11, 1, 1, 30, tzinfo=LA)
        second = datetime.datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=LA)
        events = [first, second]
        result = insights._predict("pee", events, [], [], None, second, Night([], second), learned=False)
        self.assertIsNone(result)  # no fallback, no learned gaps: nothing to say, and no crash

    # --- settings ----------------------------------------------------------------------------
    def test_rules_load_defaults(self):
        self.assertEqual(Rules.load(), Rules())

    def test_rules_load_site_settings(self):
        from dbsettings.loading import set_setting_value

        set_setting_value("pets.models", "PottyBreak", "max_hold_hours", 6)
        self.assertEqual(Rules.load().max_hold_hours, 6)

    def test_needs_close_together_share_a_walk(self):
        pet = self.pet(months=36)
        self.potty(pet, ago(hours=1), pee=True, poop=True)
        Meal.objects.create(pet=pet, time=ago(minutes=5))
        card = next_walk_card(pet, now=NOW)
        self.assertTrue(all(p["this_walk"] for p in card["predictions"]))  # pee +20m, poop +30m: one walk
        self.assertEqual(card["expect"], "pee + poop")
