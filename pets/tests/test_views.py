"""Pet pages: timers, logging for several pets, permissions, charts, delete buttons and forms (PUP-23 to 25)."""
import datetime
from unittest import mock
import re

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import formats, timezone

from pets import insights
from pets.households import add_member
from pets.models import (
    Container, Fill, Household, Meal, Membership, Pet, PottyBreak, Product, Sleep, SupplyItem, Walk,
)


def local(dt):
    return timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M:%S")


class PetTestCase(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_user("owner", password="x", first_name="Olive")
        self.member = users.create_user("member", password="x", first_name="Max")
        self.viewer = users.create_user("viewer", password="x")
        self.household = Household.objects.create(name="Home")
        Membership.objects.create(household=self.household, user=self.owner, role=Membership.OWNER)
        add_member(self.household, self.member, Membership.MEMBER)
        add_member(self.household, self.viewer, Membership.VIEWER)
        self.ruby = Pet.objects.create(household=self.household, name="Ruby", type=Pet.DOG,
                                       birth_date=timezone.localdate() - datetime.timedelta(days=70))
        self.rex = Pet.objects.create(household=self.household, name="Rex", type=Pet.DOG)
        self.tom = Pet.objects.create(household=self.household, name="Tom", type=Pet.CAT)
        self.pets = [self.ruby, self.rex, self.tom]
        self.client.force_login(self.owner)
        self.now = local(timezone.now())

    def login(self, user):
        self.client.force_login(user)


class TimerTests(PetTestCase):
    def test_start_walk_for_all_pets(self):
        self.client.post("/log/walk/start/")
        walk = Walk.objects.get(end=None)
        self.assertEqual(set(walk.pets.all()), set(self.pets))
        self.assertEqual(walk.walker, self.owner)

    def test_start_walk_for_one_pet(self):
        self.client.post("/log/walk/start/", {"pet": "ruby"})
        self.assertEqual(list(Walk.objects.get(end=None).pets.all()), [self.ruby])

    def test_end_walk_opens_it_for_editing(self):
        self.client.post("/log/walk/start/")
        walk = Walk.objects.get(end=None)
        response = self.client.post(f"/log/walk/{walk.pk}/end/")
        self.assertRedirects(response, f"/log/walk/{walk.pk}/?ended=1", fetch_redirect_response=False)
        walk.refresh_from_db()
        self.assertIsNotNone(walk.end)
        page = self.client.get(f"/log/walk/{walk.pk}/?ended=1")
        self.assertContains(page, "Cancel")

    def test_cancel_walk_discards_it(self):
        self.client.post("/log/walk/start/")
        walk = Walk.objects.get(end=None)
        self.client.post(f"/log/walk/{walk.pk}/cancel/")
        self.assertFalse(Walk.objects.exists())

    def test_shared_sleep_starts_together_and_ends_as_a_group(self):
        self.client.post("/log/sleep/start/")
        sleeps = Sleep.objects.filter(end=None)
        self.assertEqual(sleeps.count(), 3)
        self.assertEqual(len(set(sleeps.values_list("start", flat=True))), 1)
        self.client.post(f"/log/sleep/{sleeps.first().pk}/end/")
        self.assertFalse(Sleep.objects.filter(end=None).exists())

    def test_starting_sleep_twice_does_not_duplicate(self):
        self.client.post("/log/sleep/start/", {"pet": "ruby"})
        self.client.post("/log/sleep/start/", {"pet": "ruby"})
        self.assertEqual(Sleep.objects.filter(pet=self.ruby, end=None).count(), 1)

    def test_cancel_shared_sleep_discards_the_group(self):
        self.client.post("/log/sleep/start/")
        self.client.post(f"/log/sleep/{Sleep.objects.first().pk}/cancel/")
        self.assertFalse(Sleep.objects.exists())

    def test_running_timers_show_in_the_menu_bar(self):
        self.client.post("/log/walk/start/")
        self.client.post("/log/sleep/start/")
        page = self.client.get("/pets/").content.decode()
        bar = page.split("</nav>")[0]  # the running bar lives in the top bar
        self.assertIn("Walking", bar)
        self.assertEqual(len(set(re.findall(r"/log/sleep/(\d+)/end/", bar))), 1)  # one bar for the group

    def test_edit_running_walk_start(self):
        self.client.post("/log/walk/start/")
        walk = Walk.objects.get(end=None)
        earlier = timezone.now() - datetime.timedelta(minutes=20)
        self.client.post(f"/log/walk/{walk.pk}/", {
            "pets": [p.pk for p in self.pets], "start": local(earlier), "walker_choice": str(self.owner.pk),
            "distance_unit": "km",
        })
        walk.refresh_from_db()
        self.assertIsNone(walk.end)
        self.assertLess(abs(walk.start - earlier), datetime.timedelta(seconds=1))

    def test_viewer_cannot_start_or_stop(self):
        self.client.post("/log/walk/start/")
        walk = Walk.objects.get(end=None)
        self.login(self.viewer)
        self.assertEqual(self.client.post("/log/walk/start/").status_code, 403)
        self.assertEqual(self.client.post(f"/log/walk/{walk.pk}/end/").status_code, 403)


class AllPetsLoggingTests(PetTestCase):
    def test_potty_for_every_pet(self):
        self.client.post("/log/potty/add/", {"pets": [p.pk for p in self.pets], "time": self.now, "pee": "on",
                                             "location": "outside"})
        self.assertEqual(PottyBreak.objects.count(), 3)

    def test_potty_needs_pee_or_poop(self):
        response = self.client.post("/log/potty/add/", {"pets": [self.ruby.pk], "time": self.now, "location": "outside"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PottyBreak.objects.exists())

    def test_meal_for_every_pet(self):
        self.client.post("/log/meal/add/", {"pets": [p.pk for p in self.pets], "time": self.now, "food": "on"})
        self.assertEqual(Meal.objects.count(), 3)

    def test_shared_bowl_eaten_right_away_logs_meals_without_amounts(self):
        bowl = Container.objects.create(household=self.household, name="Bowl", kind=Container.FOOD_BOWL,
                                        amount_per_bowl=100)
        bowl.pets.set(self.pets)
        self.client.post("/log/fill/add/", {"container": bowl.pk, "time": self.now, "amount": "1", "unit": "bowls",
                                            "eaten_now": "on"})
        fill = Fill.objects.get()
        self.assertEqual(fill.amount, 100)
        self.assertEqual(fill.meals.count(), 3)
        self.assertFalse(fill.meals.exclude(amount=None).exists())

    def test_one_pet_bowl_meal_gets_the_amount(self):
        bowl = Container.objects.create(household=self.household, name="Ruby's bowl", kind=Container.FOOD_BOWL)
        bowl.pets.set([self.ruby])
        self.client.post("/log/fill/add/", {"container": bowl.pk, "time": self.now, "amount": "60", "unit": "g",
                                            "eaten_now": "on"})
        self.assertEqual(Meal.objects.get().amount, 60)

    def test_meal_portion_of_the_bowl_becomes_grams(self):
        """Filled 60 g, she ate a quarter of it: 15 g. Water the same way, from the water bowl's size."""
        food = Container.objects.create(household=self.household, name="Food bowl", kind=Container.FOOD_BOWL)
        water = Container.objects.create(household=self.household, name="Water bowl", kind=Container.WATER_BOWL,
                                         amount_per_bowl=500)
        food.pets.set([self.ruby])
        water.pets.set([self.ruby])
        Fill.objects.create(container=food, time=timezone.now() - datetime.timedelta(minutes=10), contents=Fill.FOOD,
                            amount=60)
        self.client.post("/log/meal/add/", {"pets": [self.ruby.pk], "time": self.now, "food": "on", "water": "on",
                                            "portion": "0.25", "water_portion": "0.5"})
        meal = Meal.objects.get()
        self.assertEqual((meal.portion, meal.amount, meal.water_amount), (0.25, 15, 250))
        from pets.entries import _meal
        self.assertEqual(_meal(meal), "Ate ¼ bowl (15 g), Drank ½ bowl (250 ml)")

    def test_exact_amount_wins_over_the_portion(self):
        food = Container.objects.create(household=self.household, name="Food bowl", kind=Container.FOOD_BOWL,
                                        amount_per_bowl=60)
        food.pets.set([self.ruby])
        self.client.post("/log/meal/add/", {"pets": [self.ruby.pk], "time": self.now, "food": "on",
                                            "portion": "1.0", "amount": "52", "unit": "g"})
        self.assertEqual(Meal.objects.get().amount, 52)

    def test_meal_form_warns_when_a_bowl_size_is_missing(self):
        food = Container.objects.create(household=self.household, name="Ruby's bowl", kind=Container.FOOD_BOWL)
        food.pets.set([self.ruby])
        page = self.client.get("/log/meal/add/?pet=ruby")
        self.assertContains(page, "Ruby&#x27;s bowl: how much a full bowl holds isn&#x27;t set.")
        self.assertContains(page, f'href="/log/container/{food.pk}/">Set it</a>')
        self.assertContains(page, "Ruby has no water bowl set up")
        self.assertContains(page, 'href="/log/container/add/">Add one</a>')

    def test_grams_and_millilitres_show_whole(self):
        from pets import units
        self.assertEqual(units.show(15.4, "mass", self.household), "15 g")
        self.assertEqual(units.show(249.6, "volume", self.household), "250 ml")

    def test_potty_locations_follow_each_pet(self):
        page = self.client.get("/log/potty/add/").content.decode()
        found = re.search(r'<script id="potty-locations"[^>]*>([^<]*)', page).group(1)
        self.assertIn(f'"{self.tom.pk}": ["litter_box", "inside"]', found)
        self.assertIn(f'"{self.ruby.pk}": ["outside", "inside", "pad"]', found)

    def test_member_can_log(self):
        self.login(self.member)
        self.client.post("/log/potty/add/", {"pets": [self.ruby.pk], "time": self.now, "pee": "on", "location": "outside"})
        self.assertEqual(PottyBreak.objects.get().logged_by, self.member)

    def test_viewer_can_look_but_not_log(self):
        self.login(self.viewer)
        self.assertEqual(self.client.get("/pets/ruby/").status_code, 200)
        self.assertEqual(self.client.get("/log/potty/").status_code, 200)
        self.assertEqual(self.client.get("/log/potty/add/").status_code, 403)
        self.assertNotContains(self.client.get("/pets/"), "/log/potty/add/")

    def test_only_owners_manage_members_and_settings(self):
        self.login(self.member)
        self.assertEqual(self.client.get("/pets/setup/members/").status_code, 403)
        self.assertEqual(self.client.get("/pets/setup/household/").status_code, 403)
        self.login(self.owner)
        self.assertEqual(self.client.get("/pets/setup/members/").status_code, 200)

    def test_other_households_are_invisible(self):
        stranger = get_user_model().objects.create_user("stranger", password="x")
        self.login(stranger)
        self.assertEqual(self.client.get("/pets/ruby/").status_code, 404)
        potty = PottyBreak.objects.create(pet=self.ruby, time=timezone.now(), pee=True)
        self.assertEqual(self.client.get(f"/log/potty/{potty.pk}/").status_code, 404)

    def test_owner_sets_time_to_get_outside(self):
        self.client.post("/pets/setup/household/", {"name": "Home", "units": "metric", "time_zone": "",
                                                    "minutes_to_outside": "20", "water_cutoff_hours": "2"})
        self.household.refresh_from_db()
        self.assertEqual(self.household.minutes_to_outside, 20)

    def test_household_needs_an_owner(self):
        self.client.post("/pets/setup/members/", {"user": self.owner.pk, "role": "member"})
        self.assertEqual(Membership.objects.get(user=self.owner).role, Membership.OWNER)


class HomeTests(PetTestCase):
    def test_home_goes_to_pets(self):
        self.assertRedirects(self.client.get("/"), "/pets/", fetch_redirect_response=False)

    def test_home_goes_to_the_only_pet(self):
        Pet.objects.exclude(pk=self.ruby.pk).update(active=False)
        self.assertRedirects(self.client.get("/"), "/pets/ruby/", fetch_redirect_response=False)

    
class CardTests(PetTestCase):
    def test_age_format(self):
        today = timezone.localdate()
        cases = {
            today - datetime.timedelta(days=20): "2 weeks",
            today - datetime.timedelta(days=200): "6 months",
            today - datetime.timedelta(days=500): "1 year, 4 months",
            today - datetime.timedelta(days=1100): "3 years",
        }
        for birth, expected in cases.items():
            self.assertEqual(insights.pet_age(birth), expected)
        self.assertIn("weeks (2 months", insights.pet_age(today - datetime.timedelta(days=70)))

    def test_potty_bars_count_accidents_separately(self):
        now = timezone.now()
        PottyBreak.objects.create(pet=self.ruby, time=now, pee=True, location=PottyBreak.OUTSIDE)
        PottyBreak.objects.create(pet=self.ruby, time=now, poop=True, location=PottyBreak.INSIDE)
        today = insights.potty_bars_card(self.ruby)["days"][0]
        self.assertEqual((today["pee_ok"], today["poop_ok"], today["pee_in"], today["poop_in"]), (1, 0, 0, 1))

    @mock.patch("django.utils.timezone.now", lambda: datetime.datetime.combine(
        datetime.date.today(), datetime.time(12), tzinfo=datetime.timezone.utc))  # not near midnight or bedtime
    def test_potty_bars_show_walks_per_day(self):
        now = timezone.now()
        for minutes in (20, 25):
            walk = Walk.objects.create(household=self.household, start=now - datetime.timedelta(minutes=minutes + 60),
                                       end=now - datetime.timedelta(minutes=60))
            walk.pets.set([self.ruby])
        card = insights.potty_bars_card(self.ruby)
        self.assertTrue(card["show_walks"])
        self.assertEqual((card["days"][0]["walks"], card["days"][0]["walk_time"]), (2, "45m"))
        self.assertFalse(insights.potty_bars_card(self.tom)["show_walks"])  # no walks, no column

    def test_accident_chart_shows_accidents_and_meals(self):
        now = timezone.now()
        Meal.objects.create(pet=self.ruby, time=now, amount=60)
        Meal.objects.create(pet=self.ruby, time=now, food=False, water=True)  # water isn't a meal dot
        PottyBreak.objects.create(pet=self.ruby, time=now, pee=True, location=PottyBreak.INSIDE)
        PottyBreak.objects.create(pet=self.ruby, time=now, pee=True, location=PottyBreak.OUTSIDE)
        card = insights.accidents_chart(self.ruby)
        self.assertEqual((len(card["dots"]), len(card["meals"])), (1, 1))

    def test_pet_page_cards_render(self):
        PottyBreak.objects.create(pet=self.ruby, time=timezone.now(), pee=True)
        page = self.client.get("/pets/ruby/")
        for title in ("Status", "Today", "Potty Breaks", "All activity", "Not veterinary advice", "Most recent"):
            self.assertContains(page, title)
        for gone in ("Statistics", "Last Potty Break", "Last Meal", "+ Measurement", "Meal Times"):
            self.assertNotContains(page, gone)
        self.assertNotContains(page, "{#")  # a template comment that leaked onto the page
        self.assertContains(page, "restored from the back/forward cache")  # refreshes itself when stale
        for path in ("/pets/", "/pets/ruby/progress/"):
            self.assertContains(self.client.get(path), "restored from the back/forward cache", msg_prefix=path)

    def test_empty_cards_hide(self):
        page = self.client.get("/pets/rex/")  # nothing logged for Rex
        self.assertNotContains(page, "Accident Times")
        self.assertNotContains(page, "Running low")

    def test_today_tiles(self):
        now = timezone.now()
        PottyBreak.objects.create(pet=self.ruby, time=now, pee=True, poop=True)
        PottyBreak.objects.create(pet=self.ruby, time=now, pee=True, location=PottyBreak.INSIDE)
        tiles = {label: value for label, value, _sub in insights.today_card(self.ruby)["tiles"]}
        self.assertEqual((tiles["Pees"], tiles["Poop"], tiles["Accident"]), ("2", "1", "1"))  # 1 Poop, 2 Pees
        self.assertIn("Meals", tiles)  # 0 Meals

    @mock.patch("django.utils.timezone.now", lambda: datetime.datetime.combine(
        datetime.date.today(), datetime.time(12), tzinfo=datetime.timezone.utc))
    def test_recent_activity_nests_potties_on_their_walk_and_has_filters(self):
        now = timezone.now()
        walk = Walk.objects.create(household=self.household, start=now - datetime.timedelta(minutes=20),
                                   end=now - datetime.timedelta(minutes=5))
        walk.pets.set([self.ruby])
        PottyBreak.objects.create(pet=self.ruby, time=now - datetime.timedelta(minutes=15), pee=True, walk=walk)
        PottyBreak.objects.create(pet=self.ruby, time=now - datetime.timedelta(minutes=2), pee=True,
                                  location=PottyBreak.INSIDE)
        from pets.entries import recent_activity
        rows = recent_activity(self.ruby)
        walk_row = next(r for r in rows if r["kind"] == "walk")
        self.assertEqual([c["summary"] for c in walk_row["children"]], ["Pee, outside"])
        self.assertIn("pee", walk_row["tags"])
        self.assertEqual(sum(1 for r in rows if r["kind"] == "potty"), 1)  # the walk's pee is only inside it
        accident = next(r for r in rows if r["kind"] == "potty")
        self.assertEqual((accident["colour"], accident["tags"]), ("245,211,63", ["pee", "accident"]))
        page = self.client.get("/pets/ruby/")
        self.assertContains(page, 'data-filter="accident"')
        self.assertContains(page, "data-child")


class DeleteTests(PetTestCase):
    def test_edit_page_has_delete_with_confirmation(self):
        potty = PottyBreak.objects.create(pet=self.ruby, time=timezone.now(), pee=True)
        page = self.client.get(f"/log/potty/{potty.pk}/")
        self.assertContains(page, "Delete")
        self.assertContains(page, "cannot be undone")

    def test_delete(self):
        potty = PottyBreak.objects.create(pet=self.ruby, time=timezone.now(), pee=True)
        self.client.post(f"/log/potty/{potty.pk}/delete/")
        self.assertFalse(PottyBreak.objects.exists())

    def test_deleting_a_fill_removes_its_meals(self):
        bowl = Container.objects.create(household=self.household, name="Bowl", kind=Container.FOOD_BOWL)
        bowl.pets.set([self.ruby])
        self.client.post("/log/fill/add/", {"container": bowl.pk, "time": self.now, "amount": "60", "unit": "g",
                                            "eaten_now": "on"})
        fill = Fill.objects.get()
        self.client.post(f"/log/fill/{fill.pk}/delete/")
        self.assertFalse(Meal.objects.exists())

    def test_viewer_cannot_delete(self):
        potty = PottyBreak.objects.create(pet=self.ruby, time=timezone.now(), pee=True)
        self.login(self.viewer)
        self.assertEqual(self.client.post(f"/log/potty/{potty.pk}/delete/").status_code, 403)
        self.assertTrue(PottyBreak.objects.exists())


class FormTests(PetTestCase):
    """Fields that don't apply are hidden in the page and never saved, even if a browser sends them."""

    def test_water_only_meal_drops_food_fields(self):
        product = Product.objects.create(household=self.household, name="Kibble", category=Product.DRY_FOOD)
        self.client.post("/log/meal/add/", {"pets": [self.ruby.pk], "time": self.now, "water": "on",
                                            "water_amount": "100", "water_unit": "ml", "amount": "50", "unit": "g",
                                            "product": product.pk})
        meal = Meal.objects.get()
        self.assertEqual((meal.food, meal.amount, meal.product, meal.water_amount), (False, None, None, 100))

    def test_outside_potty_drops_litter_box(self):
        box = Container.objects.create(household=self.household, name="Box", kind=Container.LITTER_BOX)
        self.client.post("/log/potty/add/", {"pets": [self.tom.pk], "time": self.now, "pee": "on",
                                             "location": "inside", "container": box.pk})
        self.assertIsNone(PottyBreak.objects.get().container)

    def test_walker_name_only_for_other(self):
        base = {"pets": [self.ruby.pk], "start": self.now, "end": self.now, "distance_unit": "km"}
        self.client.post("/log/walk/add/", {**base, "walker_choice": str(self.member.pk), "walker_name": "Grandma"})
        self.client.post("/log/walk/add/", {**base, "walker_choice": "other", "walker_name": "Grandma"})
        first, second = Walk.objects.order_by("pk")
        self.assertEqual((first.walker, first.walker_name), (self.member, ""))
        self.assertEqual((second.walker, second.walker_name), (None, "Grandma"))

    def test_water_fill_has_no_bag(self):
        bag = SupplyItem.objects.create(product=Product.objects.create(household=self.household, name="Kibble",
                                                                       category=Product.DRY_FOOD), size=1000)
        bowl = Container.objects.create(household=self.household, name="Water", kind=Container.WATER_BOWL)
        self.client.post("/log/fill/add/", {"container": bowl.pk, "time": self.now, "amount": "200", "unit": "ml",
                                            "supply_item": bag.pk})
        fill = Fill.objects.get()
        self.assertEqual((fill.contents, fill.supply_item), (Fill.WATER, None))

    def test_units_convert_on_the_way_in(self):
        self.client.post("/log/measurement/add/", {"pets": [self.ruby.pk], "date": "2026-10-01", "kind": "weight",
                                                   "value": "10", "weight_unit": "lb", "length_unit": "in"})
        self.assertAlmostEqual(self.ruby.measurements.get().value, 4.536, places=3)

    def test_unit_pairs_and_rules_reach_the_page(self):
        page = self.client.get("/log/meal/add/").content.decode()
        self.assertIn('id="show-when"', page)
        self.assertIn('id="unit-pairs"', page)


class ThemeTests(PetTestCase):
    def theme_attr(self):
        return re.search(r'data-bs-theme="(\w+)"\s+dir=', self.client.get("/pets/").content.decode()).group(1)

    def test_light_and_dark(self):
        for theme in ("light", "dark"):
            self.owner.settings.theme = theme
            self.owner.settings.save()
            self.assertEqual(self.theme_attr(), theme)

    def test_automatic_follows_the_device(self):
        self.owner.settings.theme = "auto"
        self.owner.settings.save()
        self.assertContains(self.client.get("/pets/"), "prefers-color-scheme: light")

    def test_menu_bar_stays_dark(self):
        self.owner.settings.theme = "light"
        self.owner.settings.save()
        self.assertRegex(self.client.get("/pets/").content.decode(), r'<nav class="navbar[^"]*" data-bs-theme="dark">')


class QuickPottyTests(PetTestCase):
    def quick(self, what, pets=("ruby",), **extra):
        return self.client.post("/log/potty/quick/", {"pet": list(pets), "what": what, **extra})

    def test_one_row_of_three(self):
        page = self.client.get("/pets/ruby/").content.decode()
        self.assertIn('data-bs-target="#potty-panel"', page)
        self.assertIn(">Walk<", page)
        self.assertIn(">More<", page)
        self.assertNotIn(">Start Sleep<", page)  # tucked into More

    def test_pee_now_at_the_usual_spot(self):
        self.quick("pee")
        b = PottyBreak.objects.get()
        self.assertEqual((b.pet, b.pee, b.poop, b.location), (self.ruby, True, False, "outside"))
        self.assertLess(abs(b.time - timezone.now()), datetime.timedelta(seconds=5))

    def test_both_at_a_chosen_spot(self):
        self.quick("both", location="inside")
        b = PottyBreak.objects.get()
        self.assertEqual((b.pee, b.poop, b.location), (True, True, "inside"))

    def test_spot_must_suit_the_pet(self):
        self.quick("pee", pets=("tom",), location="outside")  # Tom the cat doesn't go outside
        self.assertEqual(PottyBreak.objects.get().location, "litter_box")

    def test_returns_to_the_page_it_was_used_on(self):
        self.assertIn('name="next" value="/pets/ruby/"', self.client.get("/pets/ruby/").content.decode())
        response = self.quick("pee", next="/pets/ruby/")
        self.assertEqual(response.url, "/pets/ruby/")

    def test_missing_choice_says_so_and_stays_on_the_pet(self):
        response = self.quick("")
        self.assertEqual(response.url, "/pets/ruby/")
        self.assertContains(self.client.get(response.url), "Pick pee, poop, both or nothing.")
        self.assertFalse(PottyBreak.objects.exists())

    def test_buttons_still_send_their_choice_when_disabled(self):
        # The double-submit guard disables the tapped button, which drops it from the form; the page script
        # must carry its name/value in a hidden field.
        import os

        from django.conf import settings
        with open(os.path.join(settings.STATIC_ROOT, "housetrained", "js", "app.js"), encoding="utf-8") as f:
            self.assertIn('type:"hidden",name:n.name,value:n.value', f.read())

    @mock.patch("django.utils.timezone.now", lambda: datetime.datetime.combine(
        datetime.date.today(), datetime.time(12), tzinfo=datetime.timezone.utc))  # not near midnight or bedtime
    def test_disregard_button_clears_the_trigger(self):
        now = timezone.now()
        PottyBreak.objects.create(pet=self.ruby, time=now - datetime.timedelta(hours=2), pee=True)
        Meal.objects.create(pet=self.ruby, time=now - datetime.timedelta(minutes=90))
        page = self.client.get("/pets/ruby/")
        self.assertContains(page, "Disregard meal")
        response = self.client.post("/pets/ruby/disregard/", {"next": "/pets/ruby/"})
        self.assertEqual(response.url, "/pets/ruby/")
        self.ruby.refresh_from_db()
        self.assertIsNotNone(self.ruby.triggers_cleared_at)
        self.assertNotContains(self.client.get("/pets/ruby/"), "Disregard meal")

    def test_disregard_needs_a_pet_in_this_household(self):
        self.assertEqual(self.client.post("/pets/nobody/disregard/").status_code, 404)

    def test_several_pets(self):
        self.quick("pee", pets=("ruby", "rex"))
        self.assertEqual(PottyBreak.objects.count(), 2)

    def test_nothing_starts_a_retry_that_the_next_break_clears(self):
        self.quick("nothing")
        self.ruby.refresh_from_db()
        self.assertIsNotNone(self.ruby.retry_at)
        self.assertContains(self.client.get("/pets/ruby/"), "Try Ruby again")
        self.quick("pee")
        self.ruby.refresh_from_db()
        self.assertIsNone(self.ruby.retry_at)

    def test_nothing_is_logged_but_never_counts(self):
        from pets import insights

        self.quick("nothing", notes="sniffed around")
        entry = PottyBreak.everything.get()
        self.assertEqual((entry.pee, entry.poop, entry.notes), (False, False, "sniffed around"))
        self.assertFalse(PottyBreak.objects.exists())  # not in stats, charts or predictions
        self.assertFalse(self.ruby.potty_breaks.exists())
        tiles = {label: value for label, value, _sub in insights.today_card(self.ruby)["tiles"]}
        self.assertEqual((tiles["Pees"], tiles["Poops"], tiles["Accidents"]), ("0", "0", "0"))
        page = self.client.get("/pets/ruby/")
        self.assertContains(page, "Nothing, outside")  # but it shows in Recent activity
        self.assertContains(self.client.get("/log/potty/"), "Nothing, outside")
        self.assertEqual(self.client.get(f"/log/potty/{entry.pk}/").status_code, 200)  # and can be edited
        response = self.client.post(f"/log/potty/{entry.pk}/", {
            "pet": self.ruby.pk, "time": "2026-10-08 10:00", "location": "outside", "notes": "edited"})
        entry.refresh_from_db()
        self.assertEqual(entry.notes, "edited", getattr(response, "context", None) and response.context["form"].errors)

    def test_notes_from_the_panel(self):
        self.assertContains(self.client.get("/pets/ruby/"), 'name="notes"')
        self.quick("pee", notes="by the oak")
        self.assertEqual(PottyBreak.objects.get().notes, "by the oak")

    def test_panel_buttons_match_and_no_details_link(self):
        page = self.client.get("/pets/ruby/").content.decode()
        self.assertNotIn("Potty break (details)", page)
        self.assertNotIn('btn-outline-light py-2 px-1 text-truncate" type="submit" name="what"', page)

    def test_add_forms_have_cancel(self):
        for kind in ("meal", "fill", "potty", "walk"):
            page = self.client.get(f"/log/{kind}/add/?pet=ruby")
            self.assertContains(page, 'href="/pets/ruby/">Cancel</a>', msg_prefix=kind)

    def test_dismiss_retry(self):
        self.quick("nothing")
        self.quick("dismiss")
        self.ruby.refresh_from_db()
        self.assertIsNone(self.ruby.retry_at)

    def test_back_dated_walk_takes_in_the_pee_logged_first(self):
        now = timezone.now().replace(second=0, microsecond=0)
        pee = PottyBreak.objects.create(pet=self.ruby, time=now - datetime.timedelta(minutes=10), pee=True)
        walk = Walk.objects.create(household=self.household, start=now - datetime.timedelta(minutes=12),
                                   end=now - datetime.timedelta(minutes=2))
        walk.pets.set([self.ruby])
        pee.refresh_from_db()
        self.assertEqual(pee.walk, walk)  # nested in Recent activity
        walk.end = now - datetime.timedelta(minutes=11)  # shortened: the pee was after it
        walk.save()
        pee.refresh_from_db()
        self.assertIsNone(pee.walk)
        other = Walk.objects.create(household=self.household, start=now - datetime.timedelta(minutes=12),
                                    end=now - datetime.timedelta(minutes=2))
        other.pets.set([self.rex])  # another pet's walk doesn't take Ruby's pee
        pee.refresh_from_db()
        self.assertIsNone(pee.walk)

    def test_quick_break_joins_a_running_walk(self):
        self.client.post("/log/walk/start/", {"pet": "ruby"})
        self.assertContains(self.client.get("/pets/ruby/"), ">End walk<")
        self.quick("pee")
        self.assertIsNotNone(PottyBreak.objects.get().walk)

    def test_viewer_cannot(self):
        self.login(self.viewer)
        self.assertEqual(self.quick("pee").status_code, 403)
        self.assertNotContains(self.client.get("/pets/ruby/"), "potty-panel")


class WalkBannerTests(PetTestCase):
    """PUP-133/134/136: during a walk the banner holds everything; date and time are picked separately."""

    def test_row_order_and_add_measurement(self):
        page = self.client.get("/pets/ruby/").content.decode()
        self.assertLess(page.index(">Walk<"), page.index('data-bs-target="#potty-panel"'))
        self.assertLess(page.index('data-bs-target="#potty-panel"'), page.index(">More<"))
        self.assertIn("/log/measurement/add/?pet=ruby", page)

    def test_walk_banner_replaces_the_row(self):
        PottyBreak.objects.create(pet=self.ruby, time=timezone.now() - datetime.timedelta(hours=3), pee=True,
                                  location="outside")
        self.client.post("/log/walk/start/", {"pet": "ruby"})
        page = self.client.get("/pets/ruby/").content.decode()
        self.assertNotIn('data-bs-target="#potty-panel"', page)
        self.assertNotIn(">More<", page)
        self.assertIn("Expecting", page)
        self.assertIn(">End walk<", page)
        self.assertIn("btn-outline-danger", page)  # the trash can
        self.assertIn('name="what" value="pee"', page)
        self.assertIn('name="what" value="poop"', page)

    def test_date_and_time_are_separate_fields(self):
        page = self.client.get("/log/potty/add/").content.decode()
        self.assertIn('name="time_date"', page)
        self.assertIn('name="time_time"', page)
        self.client.post("/log/potty/add/", {"pets": [self.ruby.pk], "time_date": "2026-10-01", "time_time": "07:15",
                                             "pee": "on", "location": "outside"})
        b = PottyBreak.objects.get()
        self.assertEqual(timezone.localtime(b.time).strftime("%Y-%m-%d %H:%M"), "2026-10-01 07:15")

    def test_a_time_alone_is_today(self):
        self.client.post("/log/potty/add/", {"pets": [self.ruby.pk], "time_date": "", "time_time": "07:15",
                                             "pee": "on", "location": "outside"})
        self.assertEqual(timezone.localtime(PottyBreak.objects.get().time).date(), timezone.localdate())


class MeasurementTests(PetTestCase):
    """PUP-135/137/138: two heights, a chart that links to each entry, and a Recent Activity filter."""

    def add(self, kind, value, date):
        from pets.models import Measurement
        return Measurement.objects.create(pet=self.ruby, kind=kind, value=value, date=date)

    def test_two_heights(self):
        page = self.client.get("/log/measurement/add/").content.decode()
        self.assertIn("Height (shoulder)", page)
        self.assertIn("Height (head)", page)

    def test_chart_hidden_until_logged(self):
        self.assertNotIn('id="measure-pick"', self.client.get("/pets/ruby/").content.decode())
        self.assertNotIn("measure-series", self.client.get("/pets/ruby/").content.decode())

    def test_chart_points_link_to_entries(self):
        today = timezone.localdate()
        w1 = self.add("weight", 5, today - datetime.timedelta(days=14))
        w2 = self.add("weight", 6, today)
        self.add("height_head", 30, today)
        card = insights.measurements_card(self.ruby)
        self.assertEqual([s["key"] for s in card["series"]], ["weight", "height_head"])  # weight first, unlogged left out
        page = self.client.get("/pets/ruby/").content.decode()
        self.assertIn('id="measure-pick"', page)
        self.assertIn(f"/log/measurement/{w1.pk}/?next=/pets/ruby/", page)
        self.assertIn(f"/log/measurement/{w2.pk}/?next=/pets/ruby/", page)

    def test_recent_activity_filter(self):
        self.add("weight", 5, timezone.localdate())
        page = self.client.get("/pets/ruby/").content.decode()
        self.assertIn("Measurements", page)
        self.assertIn('data-tags="measurement"', page)


class UnitsAndZoneTests(PetTestCase):
    """PUP-140/141: one system or a unit per kind; time zones by UTC offset."""

    def save(self, **data):
        return self.client.post("/pets/setup/household/", {"name": "Home", "minutes_to_outside": "10",
                                                           "water_cutoff_hours": "2", **data})

    def test_one_system(self):
        self.save(same_units="on", units="imperial", time_zone="America/New_York")
        self.household.refresh_from_db()
        from pets import units
        self.assertEqual([units.unit_for(self.household, k) for k in units.KINDS], ["oz", "fl oz", "lb", "in", "mi", "lb"])

    def test_a_unit_per_kind_and_the_rest_follow_the_system(self):
        self.household.units = "imperial"
        self.household.save()
        self.save(unit_weight="kg", unit_length="cm", unit_mass="oz", unit_volume="fl oz", unit_distance="mi",
                  unit_bag="lb", time_zone="America/New_York")
        self.household.refresh_from_db()
        self.assertFalse(self.household.same_units)
        self.assertEqual(self.household.units, "imperial")  # kept while hidden
        self.assertEqual((self.household.unit_weight, self.household.unit_length), ("kg", "cm"))

    def test_units_reach_entries_and_displays(self):
        from pets import units
        from pets.models import Measurement
        self.household.same_units, self.household.unit_weight = False, "lb"
        self.household.save()
        Measurement.objects.create(pet=self.ruby, kind="weight", value=4.5359237, date=timezone.localdate())
        self.assertEqual(units.show(4.5359237, "weight", self.household), "10 lb")
        self.assertIn("10 lb", self.client.get("/pets/ruby/").content.decode())
        self.assertIn('value="lb" selected', self.client.get("/log/measurement/add/").content.decode().replace("\n", ""))

    def test_switching_back_keeps_the_per_kind_picks(self):
        self.save(unit_weight="lb", unit_mass="g", unit_volume="ml", unit_length="cm", unit_distance="km",
                  unit_bag="kg", time_zone="America/New_York")
        self.save(same_units="on", units="metric", time_zone="America/New_York")
        self.household.refresh_from_db()
        self.assertTrue(self.household.same_units)
        self.assertEqual(self.household.unit_weight, "lb")  # remembered for next time
        page = self.client.get("/pets/setup/household/").content.decode()
        self.assertIn('id="unit-defaults"', page)

    def test_time_zones_west_to_east_with_a_guess(self):
        from housetrained import time_zones
        names = [name for name, _label in time_zones.choices()]
        self.assertLess(names.index("Pacific/Honolulu"), names.index("America/New_York"))
        self.assertLess(names.index("America/New_York"), names.index("UTC"))
        self.assertLess(names.index("UTC"), names.index("Asia/Tokyo"))
        self.assertNotIn("US/Pacific", names)
        self.household.time_zone = ""
        self.household.save()
        page = self.client.get("/pets/setup/household/").content.decode()
        self.assertIn("start from this device", page)  # empty: the device's zone is filled in
        self.save(same_units="on", units="metric", time_zone="Europe/London")
        self.household.refresh_from_db()
        self.assertEqual(self.household.time_zone, "Europe/London")
        self.assertNotIn("start from this device", self.client.get("/pets/setup/household/").content.decode())

    def test_fallback_zone(self):
        from housetrained import time_zones
        with override_settings(TIME_ZONE="UTC"):
            self.assertEqual(time_zones.default(), "America/New_York")


class PhoneOrderTests(PetTestCase):
    def test_status_activity_today_first_and_measurements_last(self):
        """PUP-142: on a phone the cards read in their order styles."""
        from pets.models import Measurement
        Measurement.objects.create(pet=self.ruby, kind="weight", value=5, date=timezone.localdate())
        page = self.client.get("/pets/ruby/").content.decode()
        blocks = {n: page[page.index(f'style="order: {n}"'):] for n in "123489"}
        for n, title in (("1", "Status"), ("2", "All activity"), ("3", "Training"), ("4", "Today"),
                         ("8", "Measurements"), ("9", "Notes")):
            self.assertIn(title, blocks[n][:blocks[n].index("card-body") if "card-body" in blocks[n] else 400])


class AllActivityTests(PetTestCase):
    """PUP-143: the whole history, a page at a time; a filter looks through everything."""

    def test_pages_and_filters_reach_old_entries(self):
        from pets.entries import activity
        from pets.models import Measurement
        now = timezone.now()
        Measurement.objects.create(pet=self.ruby, kind="weight", value=5, date=timezone.localdate() - datetime.timedelta(days=60))
        for i in range(90):
            PottyBreak.objects.create(pet=self.ruby, time=now - datetime.timedelta(hours=i), pee=True, location="outside")
        first, more = activity(self.ruby)
        self.assertEqual((len(first), more), (40, True))
        second, more = activity(self.ruby, page=1)
        third, more = activity(self.ruby, page=2)
        self.assertEqual((len(second), len(third), more), (40, 11, False))  # 90 pees + the measurement
        self.assertFalse({r["edit_url"] for r in first} & {r["edit_url"] for r in second})
        self.assertEqual(third[-1]["kind"], "measurement")
        rows, more = activity(self.ruby, "measurement")
        self.assertEqual((len(rows), more), (1, False))

    def test_potty_filters_show_walks_with_just_the_matching_potties(self):
        from pets.entries import activity
        now = timezone.now()
        walk = Walk.objects.create(household=self.household, start=now - datetime.timedelta(minutes=30),
                                   end=now - datetime.timedelta(minutes=10))
        walk.pets.set([self.ruby])
        PottyBreak.objects.create(pet=self.ruby, time=now - datetime.timedelta(minutes=25), pee=True, walk=walk)
        PottyBreak.objects.create(pet=self.ruby, time=now - datetime.timedelta(minutes=20), poop=True, walk=walk)
        rows, _more = activity(self.ruby, "poop")
        self.assertEqual([(r["kind"], [c["summary"] for c in r["children"]]) for r in rows], [("walk", ["Poop, outside"])])
        rows, _more = activity(self.ruby, "accident")
        self.assertEqual(rows, [])

    def test_page_endpoint(self):
        PottyBreak.objects.create(pet=self.ruby, time=timezone.now(), pee=True, location="outside")
        page = self.client.get("/pets/ruby/activity/?filter=pee&page=0")
        self.assertContains(page, "Pee, outside")
        self.assertContains(page, "?next=/pets/ruby/")
        self.assertContains(self.client.get("/pets/ruby/activity/?filter=meal"), "Nothing here yet.")
        self.assertContains(self.client.get("/pets/ruby/"), "All activity")
        self.client.logout()
        self.assertNotEqual(self.client.get("/pets/ruby/activity/").status_code, 200)


class WholeHistoryCardTests(PetTestCase):
    """PUP-160: Potty Breaks and Accident Times go back to the first potty break, newest first."""

    def test_both_cards_cover_the_whole_history(self):
        long_ago = timezone.now() - datetime.timedelta(days=40)
        PottyBreak.objects.create(pet=self.ruby, time=long_ago, pee=True, location=PottyBreak.INSIDE)
        bars = insights.potty_bars_card(self.ruby)
        self.assertEqual(len(bars["days"]), 41)
        self.assertEqual(bars["days"][-1]["pee_in"], 1)
        self.assertEqual(bars["days"][-1]["label"], formats.date_format(timezone.localdate(long_ago), "D M j"))
        self.assertEqual(bars["days"][0]["label"], formats.date_format(timezone.localdate(), "D M j"))  # same format
        chart = insights.accidents_chart(self.ruby)
        self.assertEqual((len(chart["chart"]["rows"]), len(chart["dots"])), (41, 1))
        self.assertFalse(chart["recent_accidents"])  # the by-hour bars stay on the past 2 weeks
        page = self.client.get("/pets/ruby/").content.decode()  # 14 days at once, older ones by scrolling
        self.assertIn('style="max-height: calc(14 * 1.9rem)"', page)
        self.assertIn('style="aspect-ratio: 300 / 196"', page)


class CardLayoutTests(PetTestCase):
    """PUP-161/162: each card can be moved; Today sits above All activity on a computer by default."""

    def test_cards_are_movable_with_a_computer_default(self):
        page = self.client.get("/pets/ruby/").content.decode()
        for card in ("status", "training", "potty_bars", "accidents", "measurements", "supplies", "today", "activity",
                     "notes"):
            self.assertIn(f'data-card="{card}"', page)
        self.assertLess(page.index('data-card="today"'), page.index('data-card="activity"'))
        self.assertIn(f'data-layout-key="housetrained:layout:{self.owner.pk}"', page)  # per person (and device)
        self.assertIn("data-arrange", page)
        self.assertIn("pets/vendor/sortable.min", page)
