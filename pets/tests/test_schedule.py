"""The Next Walk schedule and the coach (PUP-93): simple rules you set, checked case by case and over random days.

Must always hold:
1. Logging a meal, a drink or a wake-up never makes the head-out time later.
2. With nothing new logged, time passing never makes it later.
3. Nothing is due before you could have headed out for it (before it was logged, or while she was asleep).
4. The coach only suggests; nothing changes until someone taps Apply.
"""
import datetime
import random
import zoneinfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from pets import insights
from pets.models import Household, Meal, Membership, Pet, PottyBreak, Sleep, Walk
from pets.schedule import age_rule, coach, next_walk

LA = zoneinfo.ZoneInfo("America/Los_Angeles")
MIN = datetime.timedelta(minutes=1)


@override_settings(TIME_ZONE="America/Los_Angeles", BABY_MODE=False)
class ScheduleTests(TestCase):
    def setUp(self):
        timezone.activate(LA)
        self.user = get_user_model().objects.create_user("owner", password="x")
        self.user.settings.timezone = "America/Los_Angeles"  # pages show the user's local time
        self.user.settings.save()
        self.home = Household.objects.create(name="Home", minutes_to_outside=10,
                                             bedtime=datetime.time(23), wake_time=datetime.time(6, 30))
        Membership.objects.create(household=self.home, user=self.user, role=Membership.OWNER)
        self.today = timezone.localdate()
        self.ruby = self.pet()

    def tearDown(self):
        timezone.deactivate()

    # --- helpers ----------------------------------------------------------------------------------
    def at(self, hour, minute=0, days=0):
        day = self.today - datetime.timedelta(days=days)
        return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)), LA)

    def pet(self, pee=150, poop=300, **kwargs):
        return Pet.objects.create(household=self.home, name=f"Pet{Pet.objects.count()}", type=Pet.DOG,
                                  pee_every=pee, poop_every=poop, **kwargs)

    def potty(self, when, pee=True, poop=False, inside=False, pet=None):
        return PottyBreak.objects.create(pet=pet or self.ruby, time=when, pee=pee, poop=poop,
                                         location=PottyBreak.INSIDE if inside else PottyBreak.OUTSIDE)

    def meal(self, when, food=True, water=False, logged=None, pet=None):
        meal = Meal.objects.create(pet=pet or self.ruby, time=when, food=food, water=water)
        Meal.objects.filter(pk=meal.pk).update(created=logged or when)
        return meal

    def nap(self, start, end, logged=None):
        nap = Sleep.objects.create(pet=self.ruby, start=start, end=end)
        Sleep.objects.filter(pk=nap.pk).update(created=logged or end)
        return nap

    def walk(self, start, end):
        walk = Walk.objects.create(household=self.home, start=start, end=end)
        walk.pets.set([self.ruby])
        return walk

    def card(self, now, pet=None):
        return next_walk(pet or self.ruby, now=now)

    def need(self, now, key, pet=None):
        return next((n for n in self.card(now, pet)["needs"] if n["key"] == key), None)

    # --- the rules ---------------------------------------------------------------------------------
    def test_nothing_logged_yet(self):
        card = self.card(self.at(12))
        self.assertEqual(card["needs"], [])
        self.assertIsNone(card["head_out"])

    def test_pee_every(self):
        self.potty(self.at(9))
        card = self.card(self.at(9, 5))
        self.assertEqual(card["needs"][0]["time"], self.at(11, 30))
        self.assertEqual(card["head_out"], self.at(11, 20))  # 10 minutes to get outside
        self.assertIn("every 2h 30m", card["needs"][0]["reason"])
        self.assertEqual(card["expect"], "pee")
        self.assertEqual(card["due"], "in 2h 15m")

    def test_every_other_walk_expects_a_poop(self):
        """Pee every 2h 30m, poop every 5h: walks alternate pee, pee + poop."""
        self.potty(self.at(7), poop=True)
        self.assertEqual(self.card(self.at(7, 5))["expect"], "pee")
        self.potty(self.at(9, 30))
        self.assertEqual(self.card(self.at(9, 35))["expect"], "pee + poop")
        self.potty(self.at(12), poop=True)
        self.assertEqual(self.card(self.at(12, 5))["expect"], "pee")

    def test_a_missed_poop_goes_on_the_next_walk(self):
        self.potty(self.at(7), poop=True)
        self.potty(self.at(12))  # poop was due; out, but only a pee
        card = self.card(self.at(12, 5))
        self.assertEqual(card["needs"][0]["time"], self.at(14, 30))  # no extra walk for it
        self.assertEqual(card["expect"], "pee + poop")

    def test_meal_brings_it_forward(self):
        self.potty(self.at(8, 30), poop=True)
        self.meal(self.at(10))
        card = self.card(self.at(10, 1))
        self.assertEqual(card["needs"][0]["time"], self.at(10, 15))
        self.assertEqual(card["source"], "meal")
        self.assertEqual(card["expect"], "pee + poop")

    def test_water_brings_a_pee_not_a_poop(self):
        self.potty(self.at(9), poop=True)
        self.meal(self.at(10), food=False, water=True)
        self.assertEqual(self.card(self.at(10, 1))["expect"], "pee")

    def test_no_poop_from_a_meal_right_after_one(self):
        """Pooped at 6:08 PM, ate at 6:12: no poop expected at 6:27."""
        self.potty(self.at(17, 38))
        self.potty(self.at(18, 8), pee=False, poop=True)
        self.meal(self.at(18, 12))
        self.assertEqual(self.need(self.at(18, 14), "poop")["time"], self.at(23))  # the bedtime walk

    def test_waking_up_brings_it_forward_but_not_while_asleep(self):
        self.potty(self.at(13))
        self.nap(self.at(13, 10), self.at(13, 40), logged=self.at(13, 10))  # ended with End sleep at 1:40
        self.assertEqual(self.card(self.at(13, 41))["head_out"], self.at(13, 40))

    def test_a_nap_logged_afterwards_is_due_now(self):
        """A nap from 3:10 to 3:38 logged at 4:01: head out now, not "33m overdue" while she slept."""
        self.potty(self.at(15, 4))
        self.nap(self.at(15, 10), self.at(15, 38), logged=self.at(16, 1))
        card = self.card(self.at(16, 1))
        self.assertEqual(card["head_out"], self.at(16, 1))
        self.assertEqual(card["due"], "now")

    def test_disregard_goes_back_to_the_schedule(self):
        self.potty(self.at(9))
        self.meal(self.at(9, 30), food=False, water=True)
        self.assertEqual(self.card(self.at(9, 35))["source"], "meal")
        self.ruby.triggers_cleared_at = self.at(10)
        self.ruby.save()
        self.assertEqual(self.need(self.at(10, 5), "pee")["time"], self.at(11, 30))

    def test_back_with_nothing_try_again_in_15(self):
        self.potty(self.at(9))
        self.walk(self.at(11, 20), self.at(11, 35))  # no pee on it
        card = self.card(self.at(11, 36))
        self.assertEqual(card["head_out"], self.at(11, 50))
        self.assertIn("try again", card["needs"][0]["reason"])

    def test_nothing_on_the_potty_panel_counts_too(self):
        self.potty(self.at(9))
        PottyBreak.objects.create(pet=self.ruby, time=self.at(11, 30), pee=False, poop=False)
        self.assertEqual(self.card(self.at(11, 31))["head_out"], self.at(11, 45))

    def test_an_early_walk_with_nothing_changes_nothing(self):
        self.potty(self.at(9))
        self.walk(self.at(9, 30), self.at(9, 40))
        self.assertEqual(self.card(self.at(9, 45))["head_out"], self.at(11, 20))

    def test_after_the_bedtime_pee_next_is_wake_up(self):
        self.potty(self.at(22, 30, days=1))
        card = self.card(self.at(23, 30, days=1))
        self.assertEqual(card["needs"][0]["time"], self.at(6, 30))
        self.assertIn("bedtime", card["needs"][0]["reason"])

    def test_due_overnight_goes_on_the_bedtime_walk(self):
        self.potty(self.at(21))  # + 2h 30m = 11:30 PM, in the quiet hours
        self.assertEqual(self.need(self.at(21, 5), "pee")["time"], self.at(23))

    def test_found_later_accidents_dont_count(self):
        self.potty(self.at(9))
        PottyBreak.objects.create(pet=self.ruby, time=self.at(11), pee=True, location=PottyBreak.INSIDE,
                                  found_later=True)
        self.assertEqual(self.need(self.at(11, 5), "pee")["time"], self.at(11, 30))

    def test_an_accident_counts_as_her_last_pee(self):
        self.potty(self.at(9))
        self.potty(self.at(10), inside=True)
        self.assertEqual(self.need(self.at(10, 5), "pee")["time"], self.at(12, 30))

    # --- accidents vs the schedule --------------------------------------------------------------------
    def test_an_accident_remembers_when_the_schedule_said_to_go(self):
        from pets.entries import _potty

        self.potty(self.at(9))
        accident = self.potty(self.at(11, 10), inside=True)  # due 11:30
        accident.refresh_from_db()
        self.assertEqual(accident.due_at, self.at(11, 30))
        self.assertEqual(_potty(accident), "Pee, inside · 20m before due")
        late = self.potty(self.at(14, 15), inside=True)  # due 1:40 PM (2h 30m after 11:10)
        late.refresh_from_db()
        self.assertEqual(_potty(late), "Pee, inside · 35m overdue")
        self.ruby.pee_every = 60
        self.ruby.save()
        accident.refresh_from_db()
        self.assertEqual(accident.due_at, self.at(11, 30))  # as the schedule stood then

    def test_moving_an_accident_outside_clears_it(self):
        self.potty(self.at(9))
        accident = self.potty(self.at(11), inside=True)
        accident.location = PottyBreak.OUTSIDE
        accident.save()
        accident.refresh_from_db()
        self.assertIsNone(accident.due_at)

    def test_older_accidents_are_filled_in_when_shown(self):
        from pets.entries import recent_activity

        self.potty(self.at(9))
        accident = self.potty(self.at(11, 10), inside=True)
        PottyBreak.objects.filter(pk=accident.pk).update(due_at=None)  # logged before this was kept
        row = next(r for r in recent_activity(self.ruby) if "inside" in r["summary"])
        self.assertIn("20m before due", row["summary"])

    # --- prefill and settings -------------------------------------------------------------------------
    def test_age_rule_prefills_pee_every(self):
        weeks = lambda n: self.today - datetime.timedelta(weeks=n)  # noqa: E731
        self.assertEqual(age_rule(weeks(9)), 180)  # ~2 months + 1 hour, in 15-minute steps
        self.assertEqual(age_rule(weeks(52)), 8 * 60)  # capped
        self.assertEqual(age_rule(weeks(1)), 60)  # at least an hour
        self.assertEqual(age_rule(None), 240)
        new = Pet.objects.create(household=self.home, name="New", type=Pet.DOG, birth_date=weeks(9))
        self.assertEqual((new.pee_every, new.poop_minutes, new.after_meal), (180, 360, 15))

    def test_schedule_is_set_on_the_pet_form_and_restarts_the_coach(self):
        self.client.force_login(self.user)
        response = self.client.post(f"/log/pet/{self.ruby.pk}/", {
            "name": self.ruby.name, "type": "dog", "pee_every": "165", "poop_every": "300", "after_meal": "20",
            "active": "on"})
        self.assertEqual(response.status_code, 302)
        self.ruby.refresh_from_db()
        self.assertEqual((self.ruby.pee_every, self.ruby.after_meal), (165, 20))
        self.assertIsNotNone(self.ruby.schedule_changed_at)

    def test_quiet_hours_on_the_household_form(self):
        self.client.force_login(self.user)
        self.client.post("/pets/setup/household/", {"name": "Home", "units": "metric", "time_zone": "",
                                                    "minutes_to_outside": "10", "bedtime": "22:30",
                                                    "wake_time": "06:00", "water_cutoff_hours": "2"})
        self.home.refresh_from_db()
        self.assertEqual((self.home.bedtime, self.home.wake_time), (datetime.time(22, 30), datetime.time(6)))

    def test_now_card_shows_the_schedule(self):
        self.potty(timezone.now() - datetime.timedelta(minutes=30))
        self.client.force_login(self.user)
        page = self.client.get(f"/pets/{self.ruby.slug}/")
        self.assertContains(page, "Schedule: pee every 2h 30m, poop every 5h, out 15 min after a meal.")

    # --- the coach -----------------------------------------------------------------------------------
    def test_coach_suggests_shorter_after_accidents(self):
        for days in (2, 3):  # pee accidents ~2h 10m after a pee
            self.potty(self.at(9, days=days))
            self.potty(self.at(11, 10, days=days), inside=True)
        tip = coach(self.ruby, now=self.at(12))
        self.assertEqual((tip["kind"], tip["suggested"]), ("pee", 105))  # 2h 10m - 15m, down to 15 minutes
        self.assertIn("2 pee accidents this week came before the walk was due, ~2h 10m after the last pee", tip["text"])

    def test_coach_ignores_accidents_after_the_walk_was_due(self):
        for days in (2, 3):  # due at 11:30, accidents at 11:50: missed walks, not a too-long interval
            self.potty(self.at(9, days=days))
            self.potty(self.at(11, 50, days=days), inside=True)
        tip = coach(self.ruby, now=self.at(12))
        self.assertIsNone(tip)

    def test_coach_suggests_longer_after_a_clean_week(self):
        for days in range(8, 0, -1):
            for hour in (8, 11, 14, 17, 20):
                self.potty(self.at(hour, days=days), poop=hour in (8, 17))
        tip = coach(self.ruby, now=self.at(9))
        self.assertEqual((tip["kind"], tip["suggested"]), ("pee", 165))
        self.assertIn("A week without a pee accident at every 2h 30m. Try 2h 45m?", tip["text"])

    def test_coach_waits_a_week_after_a_change(self):
        for days in range(8, 0, -1):
            self.potty(self.at(10, days=days))
        self.ruby.schedule_changed_at = self.at(8, days=2)
        self.ruby.save()
        self.assertIsNone(coach(self.ruby, now=self.at(9)))

    def test_coach_only_suggests_until_applied(self):
        for days in (2, 3):
            self.potty(self.at(9, days=days))
            self.potty(self.at(11, 10, days=days), inside=True)
        self.client.force_login(self.user)
        page = self.client.get(f"/pets/{self.ruby.slug}/")
        self.assertContains(page, "Try every 1h 45m?")
        self.ruby.refresh_from_db()
        self.assertEqual(self.ruby.pee_every, 150)  # unchanged until applied
        self.client.post(f"/pets/{self.ruby.slug}/coach/", {"action": "apply", "kind": "pee"})
        self.ruby.refresh_from_db()
        self.assertEqual(self.ruby.pee_every, 105)
        self.assertIsNotNone(self.ruby.schedule_changed_at)
        self.assertNotContains(self.client.get(f"/pets/{self.ruby.slug}/"), "Try every")  # a fresh week starts

    def test_coach_not_now(self):
        for days in (2, 3):
            self.potty(self.at(9, days=days))
            self.potty(self.at(11, 10, days=days), inside=True)
        self.client.force_login(self.user)
        self.client.post(f"/pets/{self.ruby.slug}/coach/", {"action": "snooze"})
        self.ruby.refresh_from_db()
        self.assertEqual(self.ruby.pee_every, 150)
        self.assertIsNone(coach(self.ruby))

    # --- Progress ------------------------------------------------------------------------------------
    def test_progress_shows_parts_of_the_day_and_time_since_the_last_pee(self):
        for days in (1, 2, 3):
            self.potty(self.at(17, days=days))
            self.potty(self.at(19, days=days), inside=True)
        parts = insights.by_part_of_day(self.ruby)
        self.assertIn("Most pee accidents are in the evening: 3 of 3", parts["headline"])
        hold = {r["label"]: (r["outside"], r["accidents"]) for r in insights.hold_table(self.ruby)}
        self.assertEqual(hold["2h 0m–2h 30m"], (0, 3))
        self.client.force_login(self.user)
        page = self.client.get(f"/pets/{self.ruby.slug}/progress/")
        self.assertContains(page, "Time since the last pee")
        self.assertContains(page, "3 pee · 0 poop")

    # --- must always hold (random days) ----------------------------------------------------------------
    def random_day(self, rng):
        Pet.objects.all().delete()
        self.ruby = self.pet(pee=rng.choice([90, 120, 150, 180]), poop=rng.choice([240, 300, 360]))
        hour = rng.randint(8, 20)
        for h in range(7, hour, rng.choice([2, 3])):
            self.potty(self.at(h, rng.randint(0, 59)), poop=rng.random() < 0.4, inside=rng.random() < 0.15)
        return self.at(hour, rng.randint(0, 59))

    def test_logging_intake_or_a_nap_never_makes_it_later(self):
        rng = random.Random(93)
        for case in range(80):
            now = self.random_day(rng)
            last = PottyBreak.objects.filter(pet=self.ruby, time__lte=now).order_by("-time").first()
            if not last:
                continue
            before = self.card(now)["head_out"]
            when = last.time + (now - last.time) * rng.random()
            if rng.random() < 0.5:
                self.meal(when, food=rng.random() < 0.5, water=True, logged=now)
            else:
                self.nap(when - 20 * MIN, when, logged=now)
            with self.subTest(case=case):
                self.assertLessEqual(self.card(now)["head_out"], before)

    def test_time_passing_never_makes_it_later(self):
        rng = random.Random(94)
        for case in range(40):
            now = self.random_day(rng)
            if rng.random() < 0.5:
                self.meal(now - 5 * MIN, logged=now - 5 * MIN)
            previous = self.card(now)["head_out"]
            for step in range(1, 25):
                t = now + step * 10 * MIN
                current = self.card(t)["head_out"]
                with self.subTest(case=case, t=t):
                    if previous:
                        self.assertLessEqual(current, previous)
                previous = current
