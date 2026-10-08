"""Next Walk behaviour that must hold, so the estimate never quietly slips later and leads to an accident.

The rules:
1. Logging a meal or a drink never makes the estimate later (it can only bring it forward).
2. Logging a wake-up never makes it later.
3. With nothing new logged, time passing never makes it later. A meal or wake-up stays due until a potty
   break is logged or someone taps Disregard on the Now card (then: back to the usual gap).
4. Once the usual gap has passed, the pet stays overdue until a potty break is logged.
5. Learning from accidents only ever brings the estimate forward.
6. Logging a pee is what moves the next pee later.
7. A meal or wake-up can't be due before it was logged, and a wake-up not before waking.
"""
import datetime
import random
import zoneinfo

from django.test import TestCase, override_settings
from django.utils import timezone

from pets.insights import Rules, next_walk_card
from pets.models import Household, Meal, Pet, PottyBreak, Sleep

LA = zoneinfo.ZoneInfo("America/Los_Angeles")
MIN = datetime.timedelta(minutes=1)


@override_settings(TIME_ZONE="America/Los_Angeles", BABY_MODE=False)
class PredictionScenarioTests(TestCase):
    def setUp(self):
        timezone.activate(LA)
        self.household = Household.objects.create(name="Home")
        self.today = timezone.localdate()
        self.rules = Rules()

    def tearDown(self):
        timezone.deactivate()

    # --- helpers ----------------------------------------------------------------------------------
    def at(self, hour, minute=0, days=0):
        day = self.today - datetime.timedelta(days=days)
        return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hour, minute)), LA)

    def pet(self, months=6):
        birth = self.today - datetime.timedelta(days=round(months * 30.44))
        return Pet.objects.create(household=self.household, name=f"Pet{Pet.objects.count()}", birth_date=birth)

    def potty(self, pet, when, pee=True, poop=False, inside=False):
        return PottyBreak.objects.create(pet=pet, time=when, pee=pee, poop=poop,
                                         location=PottyBreak.INSIDE if inside else PottyBreak.OUTSIDE)

    def meal(self, pet, when, food=True, water=False):
        meal = Meal.objects.create(pet=pet, time=when, food=food, water=water)
        Meal.objects.filter(pk=meal.pk).update(created=when)  # logged live
        meal.created = when
        return meal

    def history(self, pet, days=5, wake=7, bed=23, gap=3, rng=None, meals=True):
        """A regular routine for the past `days` days: up at `wake`, a pee every `gap` hours, a poop on every
        other one, breakfast and dinner, bedtime pee at `bed`. `rng` jitters each time by up to 20 minutes."""
        jitter = (lambda: datetime.timedelta(minutes=rng.randint(-20, 20))) if rng else (lambda: datetime.timedelta())
        for d in range(days, 0, -1):
            for i, hour in enumerate(list(range(wake, bed, gap)) + [bed]):
                self.potty(pet, self.at(hour, days=d) + jitter(), poop=i % 2 == 0)
            if meals:
                self.meal(pet, self.at(wake, 15, days=d) + jitter())
                self.meal(pet, self.at(17, 30, days=d) + jitter())

    def head_out(self, pet, now, **kwargs):
        return next_walk_card(pet, now=now, rules=self.rules, **kwargs)["head_out"]

    def pee(self, pet, now):
        return next(p for p in next_walk_card(pet, now=now, rules=self.rules)["predictions"] if p["key"] == "pee")

    def times(self, pet, now, **kwargs):
        return {p["key"]: p["time"] for p in next_walk_card(pet, now=now, rules=self.rules, **kwargs)["predictions"]}

    # --- the morning that found the bug --------------------------------------------------------------
    def test_water_at_the_start_of_a_walk_does_not_postpone_an_overdue_pee(self):
        """Last pee at bedtime, breakfast, a poop accident, then water logged at the start of a walk: the
        estimate jumped two hours later. It must stay overdue."""
        pet = self.pet()
        self.history(pet)
        self.potty(pet, self.at(0, 4))
        self.meal(pet, self.at(7, 55), water=True)
        self.potty(pet, self.at(8, 0), pee=False, poop=True, inside=True)
        now = self.at(9, 56)
        before = self.times(pet, now)["pee"]
        self.meal(pet, self.at(9, 53), food=False, water=True)
        after = self.times(pet, now)["pee"]
        self.assertLessEqual(after, before)
        self.assertLess(after, now)  # still overdue: nothing since 12:04 AM

    # --- rule 1: intake never postpones ---------------------------------------------------------------
    def test_second_meal_does_not_postpone_the_first(self):
        pet = self.pet()
        self.history(pet)
        self.potty(pet, self.at(11))
        self.meal(pet, self.at(11, 10))
        first = self.times(pet, self.at(11, 20))["pee"]
        self.meal(pet, self.at(11, 15), food=False, water=True)
        self.assertEqual(self.times(pet, self.at(11, 20))["pee"], first)

    def test_drink_right_after_a_pee_brings_the_next_one_forward(self):
        pet = self.pet()
        self.history(pet)
        self.potty(pet, self.at(11))
        without = self.times(pet, self.at(11, 5))["pee"]
        self.meal(pet, self.at(11, 2), food=False, water=True)
        self.assertLess(self.times(pet, self.at(11, 5))["pee"], without)

    def test_intake_never_postpones_randomised(self):
        """Many routines, times of day and meals or drinks: adding one never moves any estimate later."""
        rng = random.Random(80)
        for case in range(60):
            Pet.objects.all().delete()
            pet = self.pet(months=rng.choice([3, 5, 9, 24]))
            self.history(pet, gap=rng.choice([2, 3, 4]), wake=rng.choice([6, 7, 8]), rng=rng)
            # Today so far: a few potty breaks and maybe breakfast.
            hour = rng.randint(7, 20)
            for h in range(rng.choice([6, 7, 8]), hour, rng.choice([2, 3])):
                self.potty(pet, self.at(h, rng.randint(0, 59)), poop=rng.random() < 0.4)
            now = self.at(hour, rng.randint(0, 59))
            last = PottyBreak.objects.filter(pet=pet, time__lte=now).latest("time").time
            before = self.times(pet, now)
            when = last + (now - last) * rng.random()
            self.meal(pet, when, food=rng.random() < 0.5, water=True)
            after = self.times(pet, now)
            for kind, time in before.items():
                with self.subTest(case=case, kind=kind, now=now, meal=when):
                    self.assertLessEqual(after[kind], time)

    # --- rule 2: wake-ups never postpone -------------------------------------------------------------
    def test_wake_up_never_postpones(self):
        rng = random.Random(81)
        for case in range(30):
            Pet.objects.all().delete()
            pet = self.pet()
            self.history(pet, rng=rng)
            last = self.at(rng.randint(9, 18))
            self.potty(pet, last)
            now = last + datetime.timedelta(minutes=rng.randint(30, 240))
            before = self.times(pet, now)
            nap = Sleep.objects.create(pet=pet, start=last + MIN, end=last + (now - last) * rng.random() + 2 * MIN)
            Sleep.objects.filter(pk=nap.pk).update(created=nap.end)  # logged live
            after = self.times(pet, now)
            for kind, time in before.items():
                with self.subTest(case=case, kind=kind, woke=nap.end):
                    self.assertLessEqual(after[kind], time)

    def test_nap_logged_afterwards_is_due_now_not_during_the_nap(self):
        """A nap from 3:10 to 3:38 logged at 4:01: head out now, not "33m overdue" at 3:28 while she slept."""
        pet = self.pet()
        self.household.minutes_to_outside = 10
        self.household.save()
        self.history(pet)
        self.potty(pet, self.at(15, 4), poop=True)
        nap = Sleep.objects.create(pet=pet, start=self.at(15, 10), end=self.at(15, 38))
        Sleep.objects.filter(pk=nap.pk).update(created=self.at(16, 1))
        card = next_walk_card(pet, now=self.at(16, 1), rules=self.rules)
        self.assertEqual(card["head_out"], self.at(16, 1))
        self.assertEqual(card["due"], "now")

    def test_head_out_after_a_nap_is_not_before_waking(self):
        pet = self.pet()
        self.household.minutes_to_outside = 10
        self.household.save()
        self.history(pet)
        self.potty(pet, self.at(15, 4), poop=True)
        nap = Sleep.objects.create(pet=pet, start=self.at(15, 10), end=self.at(15, 38))
        Sleep.objects.filter(pk=nap.pk).update(created=self.at(15, 10))  # started live, ended live at 3:38
        self.assertEqual(self.head_out(pet, self.at(15, 39)), self.at(15, 38))

    def test_meal_logged_afterwards_is_due_from_when_it_was_logged(self):
        pet = self.pet()
        self.history(pet, meals=False)  # default 20 minutes from a meal to a pee
        self.potty(pet, self.at(11), poop=True)
        meal = self.meal(pet, self.at(11, 5), food=False, water=True)  # water: drives pees only
        Meal.objects.filter(pk=meal.pk).update(created=self.at(12))
        card = next_walk_card(pet, now=self.at(12), rules=self.rules)
        pee = next(p for p in card["predictions"] if p["key"] == "pee")
        self.assertEqual(pee["source"], "meal")
        self.assertEqual(card["head_out"], self.at(12))  # due now, not 35 minutes overdue

    # --- rules 3 and 4: time passing ------------------------------------------------------------------
    def test_overdue_stays_put_as_time_passes(self):
        pet = self.pet()
        self.history(pet)
        self.potty(pet, self.at(9))
        due = self.head_out(pet, self.at(9, 5))
        now = self.at(9, 5)
        while now < due + datetime.timedelta(hours=3) and now < self.at(21):
            with self.subTest(now=now):
                self.assertEqual(self.head_out(pet, now), due)
            now += datetime.timedelta(minutes=10)

    def test_missed_meal_stays_due_until_disregarded(self):
        pet = self.pet()
        self.history(pet)
        self.potty(pet, self.at(11))
        usual = self.times(pet, self.at(11, 1))["pee"]  # before the meal: the usual gap
        self.meal(pet, self.at(11, 5))
        pee = self.pee(pet, self.at(11, 10))
        trigger = pee["time"]
        self.assertEqual(pee["source"], "meal")
        self.assertEqual(self.times(pet, trigger + datetime.timedelta(hours=1))["pee"], trigger)  # still due
        pet.triggers_cleared_at = trigger + datetime.timedelta(minutes=40)
        pet.save()
        self.assertEqual(self.times(pet, trigger + datetime.timedelta(hours=1))["pee"], usual)  # disregarded
        self.assertEqual(self.times(pet, trigger)["pee"], trigger)  # earlier views of the day are unchanged

    def test_disregard_only_covers_triggers_before_it(self):
        pet = self.pet()
        self.history(pet, meals=False)  # default 20 minutes from a drink to a pee
        self.potty(pet, self.at(11))
        self.meal(pet, self.at(11, 5))
        pet.triggers_cleared_at = self.at(11, 40)
        pet.save()
        self.meal(pet, self.at(12), food=False, water=True)
        pee = self.pee(pet, self.at(12, 5))
        self.assertEqual(pee["source"], "meal")
        self.assertIn("12:00", pee["reason"])

    def test_time_passing_never_postpones_randomised(self):
        """With nothing new logged, the estimate never slips later than it was."""
        rng = random.Random(82)
        for case in range(40):
            Pet.objects.all().delete()
            pet = self.pet(months=rng.choice([3, 6, 12]))
            self.history(pet, gap=rng.choice([2, 3]), rng=rng)
            last = self.at(rng.randint(8, 17), rng.randint(0, 59))
            self.potty(pet, last, poop=rng.random() < 0.5)
            if rng.random() < 0.5:
                self.meal(pet, last + datetime.timedelta(minutes=rng.randint(2, 60)))
            start = last + datetime.timedelta(minutes=61)
            previous = self.head_out(pet, start)
            now = start
            while now < start + datetime.timedelta(hours=4) and now < self.at(21):
                now += datetime.timedelta(minutes=10)
                current = self.head_out(pet, now)
                with self.subTest(case=case, now=now):
                    self.assertLessEqual(current, previous)
                previous = current

    # --- rule 5: accidents only bring it forward -------------------------------------------------------
    def test_accident_learning_never_postpones(self):
        pet = self.pet()
        self.history(pet)
        for hours in (30, 54, 78):  # accidents that came early, with the prediction that missed them
            accident = self.potty(pet, timezone.now() - datetime.timedelta(hours=hours), inside=True)
            accident.expected_raw_at = accident.time + datetime.timedelta(minutes=40)
            accident.save()
        self.potty(pet, self.at(10))
        now = self.at(10, 30)
        learned, plain = self.times(pet, now), self.times(pet, now, learn=False)
        for kind in plain:
            self.assertLessEqual(learned[kind], plain[kind])

    # --- rule 6: a pee moves the next pee ------------------------------------------------------------
    def test_logging_a_pee_moves_the_estimate_past_it(self):
        pet = self.pet()
        self.history(pet)
        self.potty(pet, self.at(8))
        self.potty(pet, self.at(12))
        self.assertGreater(self.times(pet, self.at(12, 5))["pee"], self.at(12, 5))
