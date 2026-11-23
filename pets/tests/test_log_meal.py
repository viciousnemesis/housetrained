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
        self.log(food_added="1", food_unit="bowls", food=self.bag.product_id, portion="1.0")
        meal = Meal.objects.get()
        self.assertEqual((meal.fill.amount, meal.fill.supply_item, meal.amount, meal.food), (80, self.bag, 80, True))
        self.assertEqual(_meal(meal), "Meal: Food – Ate all")
        kinds = [r["kind"] for r in activity(self.ruby)[0]]
        self.assertEqual(kinds, ["meal"])  # the bowl fill is part of the meal's row

    def test_the_food_starts_on_the_bag_used_last_time(self):
        self.log(food_added="60", food_unit="g", food=self.bag.product_id)
        form = self.client.get("/log/meal/add/?pet=ruby").context["form"]
        self.assertEqual(form.initial["food"], str(self.bag.product_id))
        self.assertNotIn("food_added", form.initial)  # nothing is added unless typed

    def test_added_only_doesnt_move_the_walk(self):
        self.log(food_added="60", food_unit="g", food=self.bag.product_id)
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
        self.log(food_added="60", food_unit="g", portion="0.5", food=self.bag.product_id)
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


class SourceTests(PetTestCase):
    """Bags, boxes and cans are food sources or litter sources (PUP-210)."""

    def test_food_and_litter_sources_are_apart(self):
        food = Product.objects.create(household=self.household, name="Kibble", category=Product.DRY_FOOD)
        litter = Product.objects.create(household=self.household, name="Clay", category=Product.LITTER)
        page = self.client.get("/pets/setup/")
        self.assertContains(page, "Food sources")
        self.assertContains(page, "Litter sources")  # Tom is a cat
        form = self.client.get("/log/supply/add/?for=litter")
        self.assertEqual(list(form.context["form"].fields["product"].queryset), [litter])
        self.assertContains(form, "Add litter source")
        form = self.client.get("/log/supply/add/")
        self.assertEqual(list(form.context["form"].fields["product"].queryset), [food])
        self.assertContains(form, "Add food source")


class PantryTests(PetTestCase):
    """The pantry (PUP-213): bags and cans bought ahead, which food a meal takes from, and when to warn."""

    def setUp(self):
        super().setUp()
        self.bowl = Container.objects.create(household=self.household, name="Food bowl", kind=Container.FOOD_BOWL,
                                             amount_per_bowl=80)
        self.bowl.pets.set([self.ruby])
        self.cans = Product.objects.create(household=self.household, name="Wet Food", category=Product.WET_FOOD,
                                           package_size=300)

    def meal(self, grams, **data):
        return self.client.post("/log/meal/add/", {"pets": [self.ruby.pk], "time": self.now, "food_added": grams,
                                                   "food_unit": "g", **data})

    def test_bought_ahead_goes_into_the_pantry(self):
        self.client.post("/log/supply/add/", {"product": self.cans.pk, "count": 3, "size": "", "size_unit": "g",
                                              "pets": [self.ruby.pk]})
        items = list(self.cans.items.order_by("pk"))
        self.assertEqual([(i.size, i.opened) for i in items], [(300, None)] * 3)  # opened when a meal takes from it

    def test_a_meal_takes_from_the_open_can_then_the_next(self):
        first, second = (SupplyItem.objects.create(product=self.cans, size=300) for _n in range(2))
        self.meal("200", food=self.cans.pk)
        first.refresh_from_db()
        self.assertEqual((Fill.objects.get().supply_item, first.opened), (first, timezone.localdate()))
        self.meal("100", food=self.cans.pk)  # finishes the first
        self.meal("50", food=self.cans.pk)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(Fill.objects.order_by("-pk").first().supply_item, second)
        self.assertEqual((first.finished, second.opened), (timezone.localdate(), timezone.localdate()))

    def test_the_picker_lists_foods_in_stock_with_whats_left(self):
        open_can = SupplyItem.objects.create(product=self.cans, size=300, opened=timezone.localdate())
        SupplyItem.objects.create(product=self.cans, size=300)
        Fill.objects.create(container=self.bowl, contents=Fill.FOOD, amount=100, supply_item=open_can)
        gone = Product.objects.create(household=self.household, name="Old kibble", category=Product.DRY_FOOD)
        SupplyItem.objects.create(product=gone, size=100, finished=timezone.localdate())
        field = self.client.get("/log/meal/add/?pet=ruby").context["form"].fields["food"]
        self.assertEqual([label for _v, label in field.choices],
                         ["Pick a food", "Wet Food", "+ Add food to the pantry"])  # nothing left of the old kibble
        self.assertIn("67% · +1 in pantry", field.widget.details[str(self.cans.pk)])

    def test_a_new_food_is_added_to_the_pantry_from_the_meal(self):
        self.meal("60", food="new", new_food_name="Kibble", new_food_category=Product.DRY_FOOD, new_food_size="2",
                  new_food_size_unit="kg", new_food_count="2")
        kibble = Product.objects.get(name="Kibble")
        self.assertEqual((kibble.package_size, kibble.items.count()), (2000, 2))
        self.assertEqual(Fill.objects.get().supply_item, kibble.items.get(opened=timezone.localdate()))

    def test_restocking_by_name_doesnt_add_the_food_twice(self):
        self.meal("60", food="new", new_food_name="wet food", new_food_category=Product.WET_FOOD, new_food_count="4")
        self.assertEqual((Product.objects.count(), self.cans.items.count()), (1, 4))  # its usual size

    def test_the_food_is_asked_for_once_theres_a_pantry(self):
        SupplyItem.objects.create(product=self.cans, size=300)
        self.assertContains(self.meal("60"), "Pick the food, or add it to the pantry.")

    def test_running_low_by_packages_or_amount(self):
        from pets.supplies import product_status

        for _n in range(3):
            SupplyItem.objects.create(product=self.cans, size=300)
        self.cans.low_at = 2
        self.assertFalse(product_status(self.cans)["low"])  # 3 cans left
        self.meal("300", food=self.cans.pk)
        self.meal("10", food=self.cans.pk)  # the first is finished: 2 left, one of them open
        self.assertTrue(product_status(self.cans)["low"])
        self.cans.low_by, self.cans.low_at = Product.AMOUNT, 500
        self.assertFalse(product_status(self.cans)["low"])  # 590 g left
        self.meal("100", food=self.cans.pk)
        self.assertTrue(product_status(self.cans)["low"])
