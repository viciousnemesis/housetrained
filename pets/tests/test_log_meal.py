"""Log meal: filling the bowl and what was eaten, as one entry (PUP-207); litter on its own."""
import datetime
from unittest import mock

from django.utils import timezone

from pets.entries import _meal, activity
from pets.models import Container, Fill, Meal, Product, SupplyItem
from pets.schedule import next_walk
from pets.tests.test_views import PetTestCase

NOON = mock.patch("django.utils.timezone.now", lambda: datetime.datetime.combine(
    datetime.date.today(), datetime.time(12), tzinfo=datetime.timezone.utc))  # not near midnight or bedtime


class LogMealTests(PetTestCase):
    def setUp(self):
        super().setUp()
        self.bowl = Container.objects.create(household=self.household, name="Food bowl", kind=Container.FOOD_BOWL,
                                             amount_per_bowl=80)
        self.bowl.pets.set([self.ruby])
        product = Product.objects.create(household=self.household, name="Wet Food", category=Product.WET_FOOD)
        self.bag = SupplyItem.objects.create(product=product, size=300)

    def log(self, **data):
        return self.client.post("/log/meal/add/", {"pets": [self.ruby.pk], "time": self.now, **data})

    def test_added_and_eaten_is_one_entry(self):
        self.log(food_added="1", food_unit="bowls", supply_item=self.bag.pk, portion="1.0")
        meal = Meal.objects.get()
        self.assertEqual((meal.fill.amount, meal.fill.supply_item, meal.amount, meal.food), (80, self.bag, 80, True))
        self.assertEqual(_meal(meal), "Meal: Food – Ate all")
        kinds = [r["kind"] for r in activity(self.ruby)[0]]
        self.assertEqual(kinds, ["meal"])  # the bowl fill is part of the meal's row

    def test_the_food_starts_on_the_bag_used_last_time(self):
        self.log(food_added="60", food_unit="g", supply_item=self.bag.pk)
        form = self.client.get("/log/meal/add/?pet=ruby").context["form"]
        self.assertEqual(form.initial["supply_item"], self.bag)
        self.assertNotIn("food_added", form.initial)  # nothing is added unless typed

    def test_added_only_doesnt_move_the_walk(self):
        self.log(food_added="60", food_unit="g")
        meal = Meal.objects.get()
        self.assertEqual((meal.food, meal.water), (False, False))
        self.assertEqual(_meal(meal), "Meal: Food – Added 60 g")

    def test_nothing_entered_is_an_error(self):
        self.assertContains(self.log(), "Fill in what was added to a bowl, or eaten or drunk.")
        self.assertFalse(Meal.objects.exists())

    def test_food_added_needs_a_bowl(self):
        response = self.client.post("/log/meal/add/", {"pets": [self.rex.pk], "time": self.now, "food_added": "50"})
        self.assertContains(response, "Rex has no food bowl set up.")

    def test_editing_takes_the_fill_away_when_cleared(self):
        self.log(food_added="60", food_unit="g", portion="0.5")
        meal = Meal.objects.get()
        self.client.post(f"/log/meal/{meal.pk}/", {"pet": self.ruby.pk, "time": self.now, "portion": "0.5"})
        self.assertFalse(Fill.objects.exists())
        self.assertEqual(Meal.objects.get().portion, 0.5)

    @NOON
    def test_out_after_a_meal_can_be_off(self):
        now = timezone.now()
        from pets.models import PottyBreak
        PottyBreak.objects.create(pet=self.ruby, time=now - datetime.timedelta(hours=2), pee=True)
        Meal.objects.create(pet=self.ruby, time=now - datetime.timedelta(minutes=90))
        self.assertEqual(next_walk(self.ruby)["source"], "meal")
        self.ruby.after_meal = None
        self.ruby.save()
        self.assertIsNone(next_walk(self.ruby)["source"])


class MealDismissalTests(PetTestCase):
    def setUp(self):
        NOON.start()
        self.addCleanup(NOON.stop)
        super().setUp()
        from pets.models import PottyBreak
        now = timezone.now()
        PottyBreak.objects.create(pet=self.ruby, time=now - datetime.timedelta(hours=2), pee=True)
        Meal.objects.create(pet=self.ruby, time=now - datetime.timedelta(minutes=90))

    def test_twenty_in_a_row_offers_to_turn_it_off(self):
        self.ruby.meal_dismissals = 19
        self.ruby.save()
        self.client.post("/pets/ruby/disregard/")
        self.ruby.refresh_from_db()
        self.assertEqual(self.ruby.meal_dismissals, 20)
        self.assertContains(self.client.get("/pets/ruby/"), "disregarded the meal timing 20 times in a row")
        self.client.post("/pets/ruby/meal-timing/", {"action": "off"})
        self.ruby.refresh_from_db()
        self.assertEqual((self.ruby.after_meal, self.ruby.meal_dismissals), (None, 0))

    def test_keep_it_counts_again(self):
        self.ruby.meal_dismissals = 20
        self.ruby.save()
        self.client.post("/pets/ruby/meal-timing/", {"action": "keep"})
        self.ruby.refresh_from_db()
        self.assertEqual((self.ruby.after_meal, self.ruby.meal_dismissals), (15, 0))

    def test_a_followed_meal_starts_the_count_again(self):
        now = timezone.now()
        self.ruby.meal_dismissals = 5
        self.ruby.triggers_cleared_at = now - datetime.timedelta(hours=4)
        self.ruby.save()
        Meal.objects.create(pet=self.ruby, time=now - datetime.timedelta(hours=3))  # followed by the pee 2 h ago
        self.client.post("/pets/ruby/disregard/")
        self.ruby.refresh_from_db()
        self.assertEqual(self.ruby.meal_dismissals, 1)


class LitterTests(PetTestCase):
    def test_litter_is_its_own_entry_for_cats(self):
        box = Container.objects.create(household=self.household, name="Box", kind=Container.LITTER_BOX)
        box.pets.set([self.tom])
        self.client.post("/log/litter/add/", {"container": box.pk, "time": self.now, "amount": "500", "unit": "g"})
        fill = Fill.objects.get()
        self.assertEqual((fill.contents, fill.amount), (Fill.LITTER, 500))
        self.assertContains(self.client.get("/pets/tom/"), "Add litter")
        self.assertNotContains(self.client.get("/pets/ruby/"), "Add litter")
