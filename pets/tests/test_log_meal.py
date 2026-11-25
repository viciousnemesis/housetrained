"""Log meal (PUP-207, PUP-216): each food and the water is its own entry, from one form; the pantry's bags and cans
(PUP-210); litter on its own."""
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
        self.water = Container.objects.create(household=self.household, name="Water bowl",
                                              kind=Container.WATER_BOWL, amount_per_bowl=500)
        for bowl in (self.bowl, self.water):
            bowl.pets.set([self.ruby])
        product = Product.objects.create(household=self.household, name="Wet Food", category=Product.WET_FOOD,
                                         package_size=300, unit="g")
        self.bag = SupplyItem.objects.create(product=product, size=300, opened=timezone.localdate())

    def log(self, **data):
        return self.client.post("/log/meal/add/", {"pets": [self.ruby.pk], "time": self.now, **data})

    def test_added_and_eaten_is_one_entry(self):
        self.log(food_added="1", food_unit="bowls", food=self.bag.product_id, portion="1.0")
        meal = Meal.objects.get()
        self.assertEqual((meal.fill.amount, meal.fill.supply_item, meal.amount, meal.food), (80, self.bag, 80, True))
        self.assertEqual(_meal(meal), "Wet Food: 80 g, ate all")
        kinds = [r["kind"] for r in activity(self.ruby.household, self.ruby)[0]]
        self.assertEqual(kinds, ["meal"])  # the bowl fill is part of the entry's row

    def test_food_and_water_are_their_own_entries(self):
        self.log(food_added="60", food_unit="g", food=self.bag.product_id, portion="0.25", water_added="500",
                 water_unit="ml", water_portion="0.5", notes="Slow")
        food, water = Meal.objects.get(kind=Meal.FOOD), Meal.objects.get(kind=Meal.WATER)
        self.assertEqual((food.amount, water.water_amount, food.notes, water.notes), (15, 250, "Slow", ""))
        self.assertEqual((_meal(food), _meal(water)), ("Wet Food: 60 g, ate ¼ · Slow", "Water: 500 ml, drank ½"))
        food_form = self.client.get(f"/log/meal/{food.pk}/").context["form"]
        self.assertNotIn("water_added", food_form.fields)  # a row edits on its own
        self.assertNotIn("food", self.client.get(f"/log/meal/{water.pk}/").context["form"].fields)

    def test_another_food_in_the_same_meal(self):
        kibble = Product.objects.create(household=self.household, name="Kibble", category=Product.DRY_FOOD,
                                        package_size=2000)
        bag = SupplyItem.objects.create(product=kibble, size=2000, opened=timezone.localdate())
        self.log(food_added="60", food_unit="g", food=self.bag.product_id, another="1", food_second=kibble.pk,
                 food_added_second="40", food_unit_second="g", portion_second="1.0")
        self.assertEqual(sorted((m.fill.supply_item.pk, m.fill.amount) for m in Meal.objects.all()),
                         sorted([(self.bag.pk, 60), (bag.pk, 40)]))
        self.log(food_added="60", food_unit="g", food=self.bag.product_id, food_second=kibble.pk, food_added_second="40")
        self.assertEqual(Meal.objects.count(), 3)  # no second food unless + Another food was pressed

    def test_the_food_starts_on_the_bag_used_last_time(self):
        self.log(food_added="60", food_unit="g", food=self.bag.product_id)
        form = self.client.get("/log/meal/add/?pet=ruby").context["form"]
        self.assertEqual(form.initial["food"], str(self.bag.product_id))
        self.assertNotIn("food_added", form.initial)  # nothing is added unless typed

    def test_added_only_doesnt_move_the_walk(self):
        self.log(food_added="60", food_unit="g", food=self.bag.product_id)
        meal = Meal.objects.get()
        self.assertEqual((meal.food, meal.water), (False, False))
        self.assertEqual(_meal(meal), "Wet Food: 60 g")

    def test_nothing_entered_is_an_error(self):
        self.assertContains(self.log(), "Fill in what went into a bowl.")
        self.assertFalse(Meal.objects.exists())

    def test_eaten_needs_what_went_in(self):
        self.assertContains(self.log(portion="0.5"), "Enter what went into the bowl.")
        self.assertContains(self.log(food_added="60", food_unit="g"), "Pick the food, or add it to the pantry.")
        self.assertFalse(Meal.objects.exists())

    def test_food_added_needs_a_bowl(self):
        response = self.client.post("/log/meal/add/", {"pets": [self.rex.pk], "time": self.now, "food_added": "50",
                                                       "food": self.bag.product_id})
        self.assertContains(response, "Rex has no food bowl set up.")

    def test_editing_takes_the_fill_away_only_with_the_entry(self):
        self.log(food_added="60", food_unit="g", portion="0.5", food=self.bag.product_id)
        meal = Meal.objects.get()
        response = self.client.post(f"/log/meal/{meal.pk}/", {"pet": self.ruby.pk, "time": self.now, "portion": "0.5",
                                                               "food": self.bag.product_id})
        self.assertContains(response, "Enter what went into the bowl.")
        self.client.post(f"/log/meal/{meal.pk}/delete/")
        self.assertFalse(Fill.objects.exists())

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

    def test_litter_has_the_same_bag_buttons(self):
        box = Container.objects.create(household=self.household, name="Box", kind=Container.LITTER_BOX)
        clay = Product.objects.create(household=self.household, name="Clay", category=Product.LITTER, package_size=9000)
        bag = SupplyItem.objects.create(product=clay, size=9000, opened=timezone.localdate())
        self.client.post("/log/litter/add/", {"container": box.pk, "time": self.now, "amount": "500", "unit": "g",
                                              "litter": clay.pk, "can": ["finished"]})
        bag.refresh_from_db()
        self.assertEqual((Fill.objects.get().supply_item, bag.finished), (bag, timezone.localdate()))


class ActivitiesTests(PetTestCase):
    """Activities (PUP-217): every pet's activity, All first, the same filters as the pet page."""

    def test_all_first_then_food_or_water(self):
        Meal.objects.create(pet=self.ruby, time=timezone.now(), amount=60)
        Meal.objects.create(pet=self.rex, time=timezone.now(), kind=Meal.WATER, food=False, water_amount=100)
        page = self.client.get("/pets/activities/")
        self.assertContains(page, 'btn-primary py-0 px-2" data-filter="all"')
        self.assertContains(page, "Ruby · ")
        water = self.client.get("/pets/activities/page/?filter=water")
        self.assertContains(water, "Water")
        self.assertNotContains(water, "Food")
        self.assertRedirects(self.client.get("/log/meal/?pet=ruby"), "/pets/activities/?filter=food&pet=ruby",
                             fetch_redirect_response=False)


class SettingsTests(PetTestCase):
    """Settings (PUP-217): the household's own right on the page; food and litter in the Pantry, not here."""

    def test_household_settings_are_on_the_page(self):
        page = self.client.get("/pets/settings/")
        self.assertContains(page, 'name="units"')
        self.assertContains(page, 'href="/pets/pantry/"')
        self.assertNotContains(page, "Food sources")
        self.client.post("/pets/settings/", {"name": "Our place", "same_units": "on", "units": "metric",
                                             "minutes_to_outside": 2, "water_cutoff_hours": 2})
        self.household.refresh_from_db()
        self.assertEqual(self.household.name, "Our place")
        self.assertRedirects(self.client.get("/pets/setup/"), "/pets/settings/", fetch_redirect_response=False)

    def test_header_and_home(self):
        home = self.client.get("/pets/")
        for link in ('href="/pets/pantry/"', 'href="/pets/activities/"', 'href="/pets/settings/"'):
            self.assertContains(home, link)
        self.assertNotContains(home, "Food and litter")
        html = home.content.decode()
        self.assertLess(html.index(">Add<"), html.index(">Pantry<"))
        self.assertLess(html.index(">Activities<"), html.index(">Settings<"))


class PantryTests(PetTestCase):
    """The pantry (PUP-210): bags and cans bought ahead, which one a meal takes from, Finished / Opened, and when to
    warn. Nothing is finished or opened unless someone says so."""

    def setUp(self):
        super().setUp()
        self.bowl = Container.objects.create(household=self.household, name="Food bowl", kind=Container.FOOD_BOWL,
                                             amount_per_bowl=80)
        self.bowl.pets.set([self.ruby])
        self.cans = Product.objects.create(household=self.household, name="Wet Food", category=Product.WET_FOOD,
                                           package_size=300, unit="g")

    def meal(self, grams, can=(), **data):
        return self.client.post("/log/meal/add/", {"pets": [self.ruby.pk], "time": self.now, "food_added": grams,
                                                   "food_unit": "g", "food": self.cans.pk, "can": list(can), **data})

    def stock(self, n, opened=True):
        items = [SupplyItem.objects.create(product=self.cans, size=300) for _n in range(n)]
        if opened:
            items[0].opened = timezone.localdate()
            items[0].save()
        return items

    def test_edit_or_delete_a_food_from_the_pantry(self):
        items = self.stock(2)
        meal = Meal.objects.create(pet=self.ruby, time=timezone.now(), amount=60, product=self.cans)
        page = self.client.get("/pets/pantry/")
        self.assertContains(page, f'href="/log/product/{self.cans.pk}/?next=/pets/pantry/"')
        self.assertContains(page, ">Edit</a>")
        self.assertContains(self.client.get(f"/log/product/{self.cans.pk}/"), "Delete Wet Food and its 2 cans?")
        self.client.post(f"/log/product/{self.cans.pk}/delete/", {"next": "/pets/pantry/"})
        self.assertFalse(Product.objects.exists() or SupplyItem.objects.filter(pk=items[0].pk).exists())
        meal.refresh_from_db()  # the meal stays, just without its food
        self.assertIsNone(meal.product)

    def test_running_low_says_how_many(self):
        self.cans.low_by, self.cans.low_at = Product.PACKAGES, 1
        self.cans.save()
        self.stock(1, opened=False)
        page = self.client.get("/pets/pantry/").content.decode()
        self.assertRegex(page, r"Running low:</strong>\s+1 unopened")

    def test_litter_is_not_a_food(self):
        page = self.client.get("/log/product/add/")
        self.assertContains(page, "Wet food")
        self.assertNotContains(page, 'value="litter"')
        self.assertNotContains(self.client.get("/log/product/add/?for=litter"), 'name="category"')
        self.client.post("/log/product/add/?for=litter", {"name": "Clay", "package_size": "9", "size_unit": "kg",
                                                          "count": "1", "low_by": Product.PACKAGES, "low_at": "1"})
        self.assertEqual(Product.objects.get(name="Clay").category, Product.LITTER)
        self.assertEqual(self.client.post("/log/product/add/", {"name": "X", "category": Product.LITTER, "package_size": "1",
                         "size_unit": "kg", "count": "1", "low_by": Product.PACKAGES, "low_at": "1"}).status_code, 200)

    def test_a_new_food_with_how_many(self):
        self.client.post("/log/product/add/", {"name": "Kibble", "category": Product.DRY_FOOD, "package_size": "2",
                                               "size_unit": "kg", "count": "3", "low_by": Product.PACKAGES,
                                               "low_at": "1"})
        kibble = Product.objects.get(name="Kibble")
        self.assertEqual((kibble.unit, kibble.package_size, kibble.items.filter(opened=None).count()), ("kg", 2000, 3))

    def test_nothing_is_finished_unless_said(self):
        first, second = self.stock(2)
        self.meal("400")  # more than the label says: still the same can
        first.refresh_from_db()
        self.assertEqual((first.finished, Fill.objects.get().supply_item), (None, first))
        self.meal("50")
        self.assertEqual(Fill.objects.order_by("-pk").first().supply_item, first)

    def test_finished_this_can(self):
        first, second = self.stock(2)
        self.meal("300", can=["finished"])
        first.refresh_from_db()
        self.assertEqual(first.finished, timezone.localdate())
        self.meal("60", can=["opened"])  # the next meal opens the next one
        second.refresh_from_db()
        self.assertEqual((second.opened, Fill.objects.order_by("-pk").first().supply_item), (timezone.localdate(), second))

    def test_no_can_open_asks_to_open_one(self):
        self.stock(1, opened=False)
        self.assertContains(self.meal("60"), "No can is open: tick Opened a new can.")

    def test_more_than_whats_left_takes_the_rest_then_the_new_one(self):
        first, second = self.stock(2)
        Fill.objects.create(container=self.bowl, contents=Fill.FOOD, amount=266, supply_item=first)  # 34 g left
        self.meal("67", can=["finished", "opened"])
        fill = Fill.objects.order_by("-pk").first()
        first.refresh_from_db()
        from pets.supplies import left_in
        self.assertEqual((fill.supply_item, fill.finished_item, fill.spill), (second, first, 34))
        self.assertEqual((first.finished, left_in(second)), (timezone.localdate(), 267))  # 33 g from the new can

    def test_opened_with_none_in_the_pantry_asks_how_many_were_bought(self):
        self.stock(1)
        self.assertContains(self.meal("60", can=["opened"]), "None left in the pantry: how many did you buy?")
        self.meal("60", can=["opened"], bought="4")
        self.assertEqual((self.cans.items.filter(finished=None).count(), self.cans.items.filter(opened=None).count()),
                         (4, 3))  # the old can finished, one of the four opened

    def test_deleting_or_editing_gives_the_cans_back(self):
        first, second = self.stock(2)
        self.meal("60", can=["finished", "opened"])
        meal = Meal.objects.get()
        self.client.post(f"/log/meal/{meal.pk}/delete/")
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.finished, second.opened), (None, None))

    def test_opening_on_an_older_meal_moves_the_later_ones(self):
        first, second = self.stock(2)
        self.meal("60")
        later = self.client.post("/log/meal/add/", {"pets": [self.ruby.pk], "time": local_later(self.now),
                                                    "food_added": "50", "food_unit": "g", "food": self.cans.pk})
        older = Meal.objects.order_by("time").first()
        self.client.post(f"/log/meal/{older.pk}/", {"pet": self.ruby.pk, "time": self.now, "food_added": "60",
                                                    "food_unit": "g", "food": self.cans.pk, "can": ["opened"]})
        self.assertEqual(set(Fill.objects.values_list("supply_item", flat=True)), {second.pk})

    def test_untick_a_guessed_finished_and_it_stops_guessing(self):
        first, _second = self.stock(2)
        self.meal("300", guessed="1")  # the page picked Finished; it was unticked
        first.refresh_from_db()
        self.assertTrue(first.no_guess)
        info = self.client.get("/log/meal/add/?pet=ruby").context["form"].food_info_json
        self.assertTrue(info["foods"][str(self.cans.pk)]["no_guess"])

    def test_the_picker_lists_foods_in_stock_with_whats_left(self):
        open_can, _rest = self.stock(2)
        Fill.objects.create(container=self.bowl, contents=Fill.FOOD, amount=266, supply_item=open_can)
        gone = Product.objects.create(household=self.household, name="Old kibble", category=Product.DRY_FOOD)
        SupplyItem.objects.create(product=gone, size=100, finished=timezone.localdate())
        field = self.client.get("/log/meal/add/?pet=ruby").context["form"].fields["food"]
        self.assertEqual([label for _v, label in field.choices],
                         ["Pick a food", "Wet Food", "+ Add food to the pantry"])  # nothing left of the old kibble
        self.assertEqual(field.widget.details[str(self.cans.pk)], "34 g left · 11% · +1 in pantry")

    def test_amounts_show_in_the_unit_it_was_added_in(self):
        from pets.supplies import show
        self.cans.unit = "kg"
        self.assertEqual((show(self.cans, 34), show(self.cans, 1250), show(self.cans, 345)), ("34 g", "1.25 kg", "345 g"))

    def test_a_new_food_is_added_to_the_pantry_from_the_meal(self):
        self.meal("60", food="new", new_food_name="Kibble", new_food_category=Product.DRY_FOOD, new_food_size="2",
                  new_food_size_unit="kg", new_food_count="2")
        kibble = Product.objects.get(name="Kibble")
        self.assertEqual((kibble.package_size, kibble.unit, kibble.items.count()), (2000, "kg", 2))
        self.assertEqual(Fill.objects.get().supply_item, kibble.items.get(opened=timezone.localdate()))

    def test_restocking_by_name_doesnt_add_the_food_twice(self):
        self.meal("60", food="new", new_food_name="wet food", new_food_category=Product.WET_FOOD, new_food_count="4")
        self.assertEqual((Product.objects.count(), self.cans.items.count()), (1, 4))  # its usual size

    def test_pantry_count_up_down_or_typed(self):
        self.stock(1)
        self.client.post(f"/pets/pantry/{self.cans.pk}/count/", {"set": "2"})
        self.client.post(f"/pets/pantry/{self.cans.pk}/count/", {"count": "12"})
        self.assertEqual(self.cans.items.filter(opened=None).count(), 12)
        self.client.post(f"/pets/pantry/{self.cans.pk}/count/", {"set": "11"})
        self.assertEqual(self.cans.items.filter(opened=None).count(), 11)
        page = self.client.get("/pets/pantry/")
        self.assertContains(page, "Wet Food")
        self.assertContains(page, 'value="11"')

    def test_running_low_by_packages_or_amount(self):
        from pets.supplies import product_status

        self.stock(3)
        self.cans.low_at = 2
        self.assertFalse(product_status(self.cans)["low"])  # 3 cans left
        self.meal("300", can=["finished"])
        self.meal("10", can=["opened"])  # 2 left, one of them open
        self.assertTrue(product_status(self.cans)["low"])
        self.cans.low_by, self.cans.low_at = Product.AMOUNT, 500
        self.assertFalse(product_status(self.cans)["low"])  # 590 g left
        self.meal("100")
        self.assertTrue(product_status(self.cans)["low"])


def local_later(text):
    """A datetime-local value an hour on."""
    return (datetime.datetime.fromisoformat(text) + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")


class IntakeTests(PetTestCase):
    """Food & Water per day (PUP-218): what was marked eaten, else the whole fill shared between its pets."""

    def test_eaten_and_drunk_per_day(self):
        from pets.insights import intake_card

        bowl = Container.objects.create(household=self.household, name="Bowl", kind=Container.FOOD_BOWL)
        now = timezone.now()
        fill = Fill.objects.create(container=bowl, contents=Fill.FOOD, amount=100, time=now)
        Meal.objects.create(pet=self.ruby, time=now, fill=fill)  # not marked: the bowl, shared with Rex
        Meal.objects.create(pet=self.rex, time=now, fill=fill)
        Meal.objects.create(pet=self.ruby, time=now, amount=30)  # marked eaten
        water = Fill.objects.create(container=bowl, contents=Fill.WATER, amount=200, time=now)
        Meal.objects.create(pet=self.ruby, time=now, kind=Meal.WATER, food=False, water_fill=water)
        today = intake_card(self.ruby)["days"][0]
        self.assertEqual((today["dry"], today["water"], today["food_text"], today["water_text"]),
                         (80, 200, "80 g", "200 ml"))
        self.assertContains(self.client.get("/pets/ruby/"), "Food & Water")
        from pets.insights import today_card
        self.assertIn(("Eaten", "80 g", ""), today_card(self.ruby)["tiles"])  # Today agrees with the card


class FormIdTests(PetTestCase):
    """No two elements on a form share an id: the page's scripts find fields by id, and a clash hid the first food's
    Eaten buttons (portion_2 against portion's third option)."""

    def test_every_add_form_has_unique_ids(self):
        import re
        from collections import Counter

        from pets.views import ENTRIES

        for kind in ENTRIES:
            page = self.client.get(f"/log/{kind}/add/")
            if page.status_code != 200:
                continue
            ids = Counter(re.findall(r'\sid="([^"]+)"', page.content.decode()))
            self.assertEqual([i for i, n in ids.items() if n > 1], [], kind)
