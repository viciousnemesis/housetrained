"""Next Walk prediction edge cases (PUP-18)."""
import datetime
import random
import re
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


@override_settings(TIME_ZONE="America/Los_Angeles", BABY_MODE=False)
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


class PredictionSettingsPageTests(TestCase):
    def setUp(self):
        admin = get_user_model().objects.create_superuser("siteadmin", password="x")
        self.client.force_login(admin)

    def page_fields(self):
        """Every field on the Site Settings page with its current value, as a browser would submit it."""
        page = self.client.get("/settings/").content.decode()
        fields = {}
        for tag in re.findall(r"<input[^>]*>", page):
            name = re.search(r'name="([^"]+)"', tag)
            if not name or 'type="checkbox"' in tag and "checked" not in tag:
                continue
            value = re.search(r'value="([^"]*)"', tag)
            fields[name.group(1)] = value.group(1) if value else ""
        return fields

    def test_blank_settings_fall_back_to_defaults(self):
        fields = self.page_fields()
        self.assertEqual(fields["pets.models__PottyBreak__max_hold_hours"], "8")
        mine = {k: "" for k in fields if k.startswith("pets.models__PottyBreak__") and "learn_from" not in k}
        mine["pets.models__PottyBreak__learn_from_accidents"] = "on"  # a checkbox: unticked means off
        response = self.client.post("/settings/", {**fields, **mine}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Rules.load(), Rules())

    def test_saved_setting_is_used(self):
        fields = self.page_fields()
        fields.update({"pets.models__PottyBreak__merge_slack": "45",
                       "pets.models__PottyBreak__default_bedtime": "22:15:00"})
        self.client.post("/settings/", fields)
        rules = Rules.load()
        self.assertEqual(rules.merge_slack, datetime.timedelta(minutes=45))
        self.assertEqual(rules.default_bed, 22 * 60 + 15)


@override_settings(TIME_ZONE="America/Los_Angeles", BABY_MODE=False)
class AccidentLearningTests(TestCase):
    """Accidents remember what Next Walk expected, the card compares them, and predictions learn (PUP-72)."""

    def setUp(self):
        timezone.activate(LA)
        self.user = get_user_model().objects.create_user("owner", password="x")
        self.home = Household.objects.create(name="Home")
        Membership.objects.create(household=self.home, user=self.user, role=Membership.OWNER)
        self.pet = Pet.objects.create(household=self.home, name="Ruby", type=Pet.DOG,
                                      birth_date=timezone.localdate() - datetime.timedelta(weeks=12))
        today = timezone.localdate()
        self.noon = timezone.make_aware(datetime.datetime.combine(today, datetime.time(12)), LA)

    def tearDown(self):
        timezone.deactivate()

    def test_accident_remembers_the_prediction_from_before_it(self):
        PottyBreak.objects.create(pet=self.pet, time=self.noon - datetime.timedelta(hours=3), pee=True)
        accident = PottyBreak.objects.create(pet=self.pet, time=self.noon - datetime.timedelta(hours=2), pee=True,
                                             location=PottyBreak.INSIDE)
        accident.refresh_from_db()
        # Age rule: a 12-week-old holds it ~(2.76 months + 1) hours, so the walk was due ~12:45, after the accident.
        hold = datetime.timedelta(hours=(timezone.localdate() - self.pet.birth_date).days / 30.44 + 1)
        self.assertLess(abs(accident.expected_raw_at - (self.noon - datetime.timedelta(hours=3) + hold)),
                        datetime.timedelta(seconds=1))
        self.assertEqual(accident.expected_at, accident.expected_raw_at)  # no accident history to learn from yet
        self.assertIn("week-old", accident.expected_reason)

    def test_outside_breaks_have_no_snapshot(self):
        b = PottyBreak.objects.create(pet=self.pet, time=self.noon, pee=True)
        b.refresh_from_db()
        self.assertIsNone(b.expected_at)

    def test_moving_an_accident_outside_clears_it(self):
        PottyBreak.objects.create(pet=self.pet, time=self.noon - datetime.timedelta(hours=3), pee=True)
        accident = PottyBreak.objects.create(pet=self.pet, time=self.noon - datetime.timedelta(hours=2), pee=True,
                                             location=PottyBreak.INSIDE)
        accident.location = PottyBreak.OUTSIDE
        accident.save()
        accident.refresh_from_db()
        self.assertIsNone(accident.expected_at)

    def early_accidents(self, count):
        """Each day: a 9 am break, then an accident an hour before the 12 pm prediction."""
        for back in range(count, 0, -1):
            day = self.noon - datetime.timedelta(days=back)
            PottyBreak.objects.create(pet=self.pet, time=day - datetime.timedelta(hours=3), pee=True)
            PottyBreak.objects.create(pet=self.pet, time=day - datetime.timedelta(hours=1), pee=True,
                                      location=PottyBreak.INSIDE)

    def test_card_compares_accidents_with_the_prediction(self):
        self.early_accidents(2)
        review = insights.accident_review(self.pet)
        self.assertEqual(len(review["accident_rows"]), 2)
        self.assertTrue(all(r["early"] for r in review["accident_rows"]))
        self.assertIn("earlier than expected", review["accident_rows"][0]["relation"])
        self.assertIn("2 of 2", review["accident_summary"])

    def test_predictions_move_earlier_after_early_accidents(self):
        self.early_accidents(3)
        PottyBreak.objects.create(pet=self.pet, time=self.noon - datetime.timedelta(minutes=30), pee=True)
        pee = [p for p in next_walk_card(self.pet, now=self.noon)["predictions"] if p["key"] == "pee"][0]
        self.assertLess(pee["time"], pee["raw_time"])
        self.assertIn("recent accidents came before the prediction", pee["reason"])

    def test_learning_can_be_switched_off(self):
        self.early_accidents(3)
        PottyBreak.objects.create(pet=self.pet, time=self.noon - datetime.timedelta(minutes=30), pee=True)
        card = next_walk_card(self.pet, now=self.noon, rules=Rules(learn_from_accidents=False))
        self.assertTrue(all(p["time"] == p["raw_time"] for p in card["predictions"]))

    def test_one_early_accident_is_not_enough(self):
        self.early_accidents(1)
        PottyBreak.objects.create(pet=self.pet, time=self.noon - datetime.timedelta(minutes=30), pee=True)
        card = next_walk_card(self.pet, now=self.noon)
        self.assertTrue(all(p["time"] == p["raw_time"] for p in card["predictions"]))


    def test_head_out_leaves_time_to_get_outside(self):
        PottyBreak.objects.create(pet=self.pet, time=self.noon - datetime.timedelta(minutes=30), pee=True)
        self.home.minutes_to_outside = 15
        self.home.save()
        card = next_walk_card(self.pet, now=self.noon)
        self.assertEqual(card["head_out"], card["predictions"][0]["time"] - datetime.timedelta(minutes=15))
        self.assertEqual(card["to_outside"], "15m")

    def test_no_time_to_get_outside(self):
        PottyBreak.objects.create(pet=self.pet, time=self.noon - datetime.timedelta(minutes=30), pee=True)
        self.home.minutes_to_outside = 0
        self.home.save()
        card = next_walk_card(self.pet, now=self.noon)
        self.assertEqual(card["head_out"], card["predictions"][0]["time"])
        self.assertIsNone(card["to_outside"])

    def test_found_later_accidents_are_not_learned_from(self):
        for back in (3, 2, 1):
            day = self.noon - datetime.timedelta(days=back)
            PottyBreak.objects.create(pet=self.pet, time=day - datetime.timedelta(hours=3), pee=True)
            PottyBreak.objects.create(pet=self.pet, time=day - datetime.timedelta(hours=1), pee=True,
                                      location=PottyBreak.INSIDE, found_later=True)
        PottyBreak.objects.create(pet=self.pet, time=self.noon - datetime.timedelta(minutes=30), pee=True)
        card = next_walk_card(self.pet, now=self.noon)
        self.assertTrue(all(p["time"] == p["raw_time"] for p in card["predictions"]))
        review = insights.accident_review(self.pet)
        self.assertEqual(review["accident_rows"][0]["relation"], "found later, time unknown")
        self.assertIsNone(review["accident_summary"])
