"""Pet pages: timers, logging for several pets, permissions, charts, delete buttons and forms (PUP-23 to 25)."""
import datetime
import re

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from pets import insights
from pets.households import add_member
from pets.models import (
    Container, Fill, Household, Meal, Membership, Pet, PottyBreak, Product, Sleep, SupplyItem, Walk,
)


def local(dt):
    return timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M:%S")


@override_settings(BABY_MODE=False)
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
        self.assertIn("Walking", page)
        self.assertEqual(len(set(re.findall(r"/log/sleep/(\d+)/end/", page))), 1)  # one bar for the group

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

    def test_household_needs_an_owner(self):
        self.client.post("/pets/setup/members/", {"user": self.owner.pk, "role": "member"})
        self.assertEqual(Membership.objects.get(user=self.owner).role, Membership.OWNER)


class HomeTests(PetTestCase):
    def test_home_goes_to_pets(self):
        self.assertRedirects(self.client.get("/"), "/pets/", fetch_redirect_response=False)

    def test_home_goes_to_the_only_pet(self):
        Pet.objects.exclude(pk=self.ruby.pk).update(active=False)
        self.assertRedirects(self.client.get("/"), "/pets/ruby/", fetch_redirect_response=False)

    @override_settings(BABY_MODE=True)
    def test_baby_mode_home_is_still_the_family_page(self):
        self.assertRedirects(self.client.get("/"), "/pets/", fetch_redirect_response=False)


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

    def test_charts_use_meals_and_accidents(self):
        now = timezone.now()
        Meal.objects.create(pet=self.ruby, time=now, amount=60)
        Meal.objects.create(pet=self.ruby, time=now, food=False, water=True)  # water isn't a meal dot
        PottyBreak.objects.create(pet=self.ruby, time=now, pee=True, location=PottyBreak.INSIDE)
        PottyBreak.objects.create(pet=self.ruby, time=now, pee=True, location=PottyBreak.OUTSIDE)
        self.assertEqual(len(insights.feedings_chart(self.ruby)["dots"]), 1)
        self.assertEqual(len(insights.accidents_chart(self.ruby)["dots"]), 1)

    def test_pet_page_cards_render(self):
        PottyBreak.objects.create(pet=self.ruby, time=timezone.now(), pee=True)
        page = self.client.get("/pets/ruby/")
        for title in ("Next Walk", "Statistics", "Walks", "Last Potty Break", "Potty Breaks", "Accident Times",
                      "Last Meal", "Meal Times", "Sleep", "not veterinary advice"):
            self.assertContains(page, title)


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
