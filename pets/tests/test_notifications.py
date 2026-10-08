"""Browser push reminders (PUP-58). Sending is faked; everything around it is real."""
import datetime
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from pets import reminders
from pets.models import (
    Container, Fill, Household, Membership, NotificationPreference, Pet, PottyBreak, Product, PushKey,
    PushSubscription, ReminderLog, SupplyItem,
)

SUB = {"endpoint": "https://push.example.com/abc", "keys": {"p256dh": "BKey", "auth": "secret"}}


@override_settings(BABY_MODE=False, TIME_ZONE="UTC")
class NotificationTests(TestCase):
    def setUp(self):
        from dbsettings.loading import set_setting_value

        set_setting_value("pets.models", "Pet", "push_notifications", True)  # the site admin allowed them
        self.user = get_user_model().objects.create_user("owner", password="x")
        self.home = Household.objects.create(name="Home", time_zone="UTC")
        Membership.objects.create(household=self.home, user=self.user, role=Membership.OWNER)
        self.pet = Pet.objects.create(household=self.home, name="Biscuit", type=Pet.DOG,
                                      birth_date=timezone.localdate() - datetime.timedelta(weeks=12))
        self.client.force_login(self.user)

    def subscribe(self):
        return self.client.post("/pets/notifications/?action=subscribe", json.dumps(SUB),
                                content_type="application/json")

    def test_page_and_service_worker(self):
        page = self.client.get("/pets/notifications/")
        self.assertContains(page, "Turn on notifications here")
        self.assertTrue(PushKey.objects.exists())  # keys made on first use
        worker = self.client.get("/sw.js")
        self.assertEqual(worker["Content-Type"], "application/javascript")
        self.assertEqual(worker["Service-Worker-Allowed"], "/")

    def test_subscribe_and_unsubscribe(self):
        self.assertEqual(self.subscribe().json(), {"ok": True})
        self.assertEqual(PushSubscription.objects.get().user, self.user)
        self.subscribe()  # the same browser again doesn't duplicate
        self.assertEqual(PushSubscription.objects.count(), 1)
        self.client.post("/pets/notifications/?action=unsubscribe", json.dumps({"endpoint": SUB["endpoint"]}),
                         content_type="application/json")
        self.assertFalse(PushSubscription.objects.exists())

    def test_rejects_bad_subscriptions(self):
        bad = {"endpoint": "http://insecure.example.com/x", "keys": {"p256dh": "a", "auth": "b"}}
        response = self.client.post("/pets/notifications/?action=subscribe", json.dumps(bad),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_preferences(self):
        self.client.post("/pets/notifications/", {"action": "prefs", "walk_due": "on"})
        prefs = NotificationPreference.objects.get(user=self.user)
        self.assertEqual((prefs.walk_due, prefs.low_supply), (True, False))

    @mock.patch("pywebpush.webpush")
    def test_send_a_test(self, webpush):
        self.subscribe()
        self.client.post("/pets/notifications/", {"action": "test"})
        self.assertEqual(webpush.call_count, 1)
        sent = webpush.call_args.kwargs
        self.assertEqual(sent["subscription_info"]["endpoint"], SUB["endpoint"])
        self.assertIn("Notifications work", json.loads(sent["data"])["body"])

    @mock.patch("pywebpush.webpush")
    def test_dead_devices_are_removed(self, webpush):
        from pywebpush import WebPushException

        self.subscribe()
        webpush.side_effect = WebPushException("gone", response=mock.Mock(status_code=410))
        self.client.post("/pets/notifications/", {"action": "test"})
        self.assertFalse(PushSubscription.objects.exists())

    @mock.patch("pywebpush.webpush")
    def test_walk_due_once(self, webpush):
        self.subscribe()
        noon = timezone.make_aware(datetime.datetime.combine(timezone.localdate(), datetime.time(12)))
        last = noon - datetime.timedelta(hours=4, minutes=30)  # a 12-week-old holds ~3h45m: due before noon
        PottyBreak.objects.create(pet=self.pet, time=last, pee=True)
        reminders.check(now=noon)
        reminders.check(now=noon)
        self.assertEqual(webpush.call_count, 1)
        message = json.loads(webpush.call_args.kwargs["data"])
        self.assertEqual(message["title"], "Biscuit: time for a walk")
        self.assertEqual(message["url"], "/pets/biscuit/")

    @mock.patch("pywebpush.webpush")
    def test_no_walk_reminder_when_not_due_or_turned_off(self, webpush):
        self.subscribe()
        noon = timezone.make_aware(datetime.datetime.combine(timezone.localdate(), datetime.time(12)))
        PottyBreak.objects.create(pet=self.pet, time=noon - datetime.timedelta(minutes=20), pee=True)
        reminders.check(now=noon)
        self.assertEqual(webpush.call_count, 0)  # not due yet
        PottyBreak.objects.all().delete()
        PottyBreak.objects.create(pet=self.pet, time=noon - datetime.timedelta(hours=4), pee=True)
        NotificationPreference.objects.create(user=self.user, walk_due=False)
        reminders.check(now=noon)
        self.assertEqual(webpush.call_count, 0)  # turned off

    @mock.patch("pywebpush.webpush")
    def test_buy_more_once_a_day(self, webpush):
        self.subscribe()
        NotificationPreference.objects.create(user=self.user, walk_due=False)
        kibble = Product.objects.create(household=self.home, name="Kibble", category=Product.DRY_FOOD, reorder_days=7)
        bag = SupplyItem.objects.create(product=kibble, size=1000, opened=timezone.localdate() - datetime.timedelta(days=5))
        bowl = Container.objects.create(household=self.home, name="Bowl", kind=Container.FOOD_BOWL, default_supply=bag)
        for day in range(5):
            Fill.objects.create(container=bowl, time=timezone.now() - datetime.timedelta(days=day, hours=1),
                                contents=Fill.FOOD, amount=170, supply_item=bag)
        reminders.check()
        reminders.check()
        self.assertEqual(webpush.call_count, 1)
        self.assertEqual(json.loads(webpush.call_args.kwargs["data"])["title"], "Buy more Kibble")

    @mock.patch("pywebpush.webpush")
    def test_only_the_households_people(self, webpush):
        stranger = get_user_model().objects.create_user("stranger", password="x")
        PushSubscription.objects.create(user=stranger, endpoint="https://push.example.com/other", p256dh="k", auth="a")
        noon = timezone.make_aware(datetime.datetime.combine(timezone.localdate(), datetime.time(12)))
        PottyBreak.objects.create(pet=self.pet, time=noon - datetime.timedelta(hours=4), pee=True)
        reminders.check(now=noon)
        self.assertEqual(webpush.call_count, 0)  # nobody in Biscuit's household signed up

    def test_old_log_entries_are_pruned(self):
        old = ReminderLog.objects.create(key="old")
        ReminderLog.objects.filter(pk=old.pk).update(created=timezone.now() - datetime.timedelta(days=40))
        reminders.check()
        self.assertFalse(ReminderLog.objects.filter(key="old").exists())

    @mock.patch("pywebpush.webpush")
    def test_water_bowl_before_bed_and_in_the_morning(self, webpush):
        self.subscribe()
        NotificationPreference.objects.create(user=self.user, walk_due=False, low_supply=False, water_cutoff=True)
        # No nights logged yet, so bedtime is the default 23:00 and wake-up 06:30; 2 hours before bed is 21:00.
        today = timezone.localdate()
        at = lambda h, m=0: timezone.make_aware(datetime.datetime.combine(today, datetime.time(h, m)))
        reminders.check(now=at(20, 30))
        self.assertEqual(webpush.call_count, 0)  # too early
        reminders.check(now=at(21, 10))
        reminders.check(now=at(21, 20))
        self.assertEqual(webpush.call_count, 1)
        self.assertEqual(json.loads(webpush.call_args.kwargs["data"])["title"], "Pick up the water bowl")
        reminders.check(now=at(6, 40))
        self.assertEqual(json.loads(webpush.call_args.kwargs["data"])["title"], "Water bowl back down")

    @mock.patch("pywebpush.webpush")
    def test_no_water_reminder_unless_asked(self, webpush):
        self.subscribe()
        NotificationPreference.objects.create(user=self.user, walk_due=False, low_supply=False)
        today = timezone.localdate()
        reminders.check(now=timezone.make_aware(datetime.datetime.combine(today, datetime.time(21, 10))))
        self.assertEqual(webpush.call_count, 0)


@override_settings(BABY_MODE=False, TIME_ZONE="UTC")
class NotificationsOffTests(TestCase):
    """Out of the box nothing leaves the server: push is off until a site admin allows it."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="x")
        self.client.force_login(self.user)

    def test_off_by_default(self):
        page = self.client.get("/pets/notifications/")
        self.assertContains(page, "turned off for this site")
        self.assertNotContains(page, "Turn on notifications here")
        self.assertFalse(PushKey.objects.exists())

    def test_cannot_subscribe_or_send(self):
        response = self.client.post("/pets/notifications/?action=subscribe", json.dumps(SUB),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PushSubscription.objects.exists())

    @mock.patch("pywebpush.webpush")
    def test_existing_devices_get_nothing_once_switched_off(self, webpush):
        PushSubscription.objects.create(user=self.user, endpoint=SUB["endpoint"], p256dh="k", auth="a")
        self.client.post("/pets/notifications/", {"action": "test"})
        reminders.check()
        self.assertEqual(webpush.call_count, 0)

    @override_settings(HOUSETRAINED_REMINDERS=False)
    def test_reminders_setting_overrides_the_site_switch(self):
        from dbsettings.loading import set_setting_value
        from pets import push

        set_setting_value("pets.models", "Pet", "push_notifications", True)
        self.assertFalse(push.allowed())

    def test_admin_sees_how_to_turn_them_on(self):
        self.user.is_superuser = True
        self.user.save()
        self.assertContains(self.client.get("/pets/notifications/"), "Site Settings")

