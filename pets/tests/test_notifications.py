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


@override_settings(TIME_ZONE="UTC")
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
    def test_links_open_at_the_configured_address(self, webpush):
        """PUP-147: HOUSETRAINED_URL decides where a notification opens; without it, the device's own address."""
        from pets import push
        self.subscribe()
        with self.settings(HOUSETRAINED_URL="https://pets.example.com"):
            push.send(self.user, "Walk", "Time to go out", url="/pets/biscuit/")
        self.assertEqual(json.loads(webpush.call_args.kwargs["data"])["url"], "https://pets.example.com/pets/biscuit/")
        with self.settings(HOUSETRAINED_URL=""):
            push.send(self.user, "Walk", "Time to go out", url="/pets/biscuit/")
        self.assertEqual(json.loads(webpush.call_args.kwargs["data"])["url"], "/pets/biscuit/")

    @mock.patch("pywebpush.webpush")
    def test_dead_devices_are_removed(self, webpush):
        from pywebpush import WebPushException

        self.subscribe()
        webpush.side_effect = WebPushException("gone", response=mock.Mock(status_code=410))
        self.client.post("/pets/notifications/", {"action": "test"})
        self.assertFalse(PushSubscription.objects.exists())

    @mock.patch("pywebpush.webpush")
    def test_reminders_that_default_off_stay_off_until_chosen(self, webpush):
        self.subscribe()
        self.assertEqual(reminders._people(self.home, "walk_due"), [self.user])  # on by default
        self.assertEqual(reminders._people(self.home, "weekly_progress"), [])  # off by default
        self.assertEqual(reminders._people(self.home, "water_cutoff"), [])

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
    def test_walk_reminder_comes_minutes_before_head_out(self, webpush):
        from pets.schedule import next_walk

        self.subscribe()
        noon = timezone.make_aware(datetime.datetime.combine(timezone.localdate(), datetime.time(12)))
        PottyBreak.objects.create(pet=self.pet, time=noon - datetime.timedelta(hours=3), pee=True)
        head_out = next_walk(self.pet, now=noon)["head_out"]
        self.assertGreater(head_out, noon)
        prefs = NotificationPreference.objects.create(user=self.user, walk_lead_minutes=0)
        reminders.check(now=head_out - datetime.timedelta(minutes=4))
        self.assertEqual(webpush.call_count, 0)  # 0 minutes before: not yet
        prefs.walk_lead_minutes = 5  # the default
        prefs.save()
        reminders.check(now=head_out - datetime.timedelta(minutes=6))
        self.assertEqual(webpush.call_count, 0)  # too early
        reminders.check(now=head_out - datetime.timedelta(minutes=4))
        self.assertEqual(webpush.call_count, 1)
        self.assertIn("Head out ~", json.loads(webpush.call_args.kwargs["data"])["body"])
        reminders.check(now=head_out)
        self.assertEqual(webpush.call_count, 1)  # once per walk

    @mock.patch("pywebpush.webpush")
    def test_still_not_out_reminder(self, webpush):
        from pets.schedule import next_walk

        self.subscribe()
        noon = timezone.make_aware(datetime.datetime.combine(timezone.localdate(), datetime.time(12)))
        PottyBreak.objects.create(pet=self.pet, time=noon - datetime.timedelta(hours=3), pee=True)
        head_out = next_walk(self.pet, now=noon)["head_out"]
        reminders.check(now=head_out)
        self.assertEqual(webpush.call_count, 1)  # time for a walk
        reminders.check(now=head_out + datetime.timedelta(minutes=9))
        self.assertEqual(webpush.call_count, 1)  # not yet
        reminders.check(now=head_out + datetime.timedelta(minutes=11))
        self.assertEqual(webpush.call_count, 2)
        message = json.loads(webpush.call_args.kwargs["data"])
        self.assertEqual(message["title"], "Biscuit: still not out")
        self.assertEqual(message["tag"], f"walk-{self.pet.pk}")  # replaces the first on the device
        reminders.check(now=head_out + datetime.timedelta(minutes=20))
        self.assertEqual(webpush.call_count, 2)  # once

    @mock.patch("pywebpush.webpush")
    def test_still_not_out_can_be_turned_off(self, webpush):
        from pets.schedule import next_walk

        self.subscribe()
        NotificationPreference.objects.create(user=self.user, walk_overdue=False)
        noon = timezone.make_aware(datetime.datetime.combine(timezone.localdate(), datetime.time(12)))
        PottyBreak.objects.create(pet=self.pet, time=noon - datetime.timedelta(hours=3), pee=True)
        head_out = next_walk(self.pet, now=noon)["head_out"]
        reminders.check(now=head_out + datetime.timedelta(minutes=11))
        self.assertEqual(webpush.call_count, 1)  # only the first one
        self.client.post("/pets/notifications/", {"action": "prefs", "walk_due": "on", "walk_overdue": "on"})
        self.assertTrue(NotificationPreference.objects.get(user=self.user).walk_overdue)

    @mock.patch("pywebpush.webpush")
    def test_still_not_out_minutes_and_late_limit_are_each_persons(self, webpush):
        """PUP-149: the still-not-out wait and the late limit are settings, not fixed 10 minutes and 2 hours."""
        from pets.schedule import next_walk

        self.subscribe()
        NotificationPreference.objects.create(user=self.user, walk_due=False, overdue_minutes=25, late_limit_hours=1)
        noon = timezone.make_aware(datetime.datetime.combine(timezone.localdate(), datetime.time(12)))
        PottyBreak.objects.create(pet=self.pet, time=noon - datetime.timedelta(hours=3), pee=True)
        head_out = next_walk(self.pet, now=noon)["head_out"]
        reminders.check(now=head_out + datetime.timedelta(minutes=11))
        self.assertEqual(webpush.call_count, 0)  # 10 minutes was the old fixed wait
        reminders.check(now=head_out + datetime.timedelta(minutes=26))
        self.assertEqual(webpush.call_count, 1)
        PottyBreak.objects.create(pet=self.pet, time=head_out, pee=True)
        later = next_walk(self.pet, now=head_out + datetime.timedelta(hours=1))["head_out"]
        reminders.check(now=later + datetime.timedelta(minutes=70))
        self.assertEqual(webpush.call_count, 1)  # over an hour late: not announced any more

    def test_timing_settings_are_saved_and_kept_in_range(self):
        self.client.post("/pets/notifications/", {
            "action": "prefs", "walk_due": "on", "walk_overdue": "on", "weekly_progress": "on",
            "overdue_minutes": "15", "late_limit_hours": "99", "weekly_day": "6", "weekly_hour": "20",
            "water_cutoff_hours": "3", "water_back_minutes": "30"})
        prefs = NotificationPreference.objects.get(user=self.user)
        self.assertEqual((prefs.overdue_minutes, prefs.late_limit_hours, prefs.weekly_day, prefs.weekly_hour),
                         (15, 12, 6, 20))
        self.home.refresh_from_db()
        self.assertEqual((self.home.water_cutoff_hours, self.home.water_back_minutes), (3, 30))
        page = self.client.get("/pets/notifications/").content.decode()
        for name in ("overdue_minutes", "late_limit_hours", "weekly_day", "weekly_hour", "water_back_minutes"):
            self.assertIn(f'name="{name}"', page)

    def test_only_owners_change_the_water_times(self):
        from pets.households import add_member

        helper = get_user_model().objects.create_user("helper", password="x")
        add_member(self.home, helper, Membership.MEMBER)
        self.client.force_login(helper)
        self.client.post("/pets/notifications/", {"action": "prefs", "water_cutoff_hours": "5", "water_back_minutes": "60"})
        self.home.refresh_from_db()
        self.assertEqual((self.home.water_cutoff_hours, self.home.water_back_minutes), (2, 0))
        self.assertNotContains(self.client.get("/pets/notifications/"), 'name="water_back_minutes"')

    @mock.patch("pywebpush.webpush")
    def test_weekly_progress_on_the_chosen_day_and_hour(self, webpush):
        self.subscribe()
        NotificationPreference.objects.create(user=self.user, walk_due=False, low_supply=False, weekly_progress=True,
                                              weekly_day=4, weekly_hour=18)
        today = timezone.localdate()
        friday = today + datetime.timedelta(days=(4 - today.weekday()) % 7)
        monday = friday - datetime.timedelta(days=4)
        at = lambda day, h: timezone.make_aware(datetime.datetime.combine(day, datetime.time(h)))
        # Something to report in the week before that Friday (not before today: on a weekend that's 6 days off).
        PottyBreak.objects.create(pet=self.pet, time=at(friday - datetime.timedelta(days=2), 12), pee=True,
                                  location=PottyBreak.INSIDE)
        reminders.check(now=at(monday, 10))
        reminders.check(now=at(friday, 17))
        self.assertEqual(webpush.call_count, 0)
        reminders.check(now=at(friday, 18))
        reminders.check(now=at(friday, 19))
        self.assertEqual(webpush.call_count, 1)

    @mock.patch("pywebpush.webpush")
    def test_water_back_after_the_chosen_minutes(self, webpush):
        self.subscribe()
        NotificationPreference.objects.create(user=self.user, walk_due=False, low_supply=False, water_cutoff=True)
        self.home.water_back_minutes = 60
        self.home.save()
        today = timezone.localdate()
        at = lambda h, m=0: timezone.make_aware(datetime.datetime.combine(today, datetime.time(h, m)))
        reminders.check(now=at(6, 40))
        self.assertEqual(webpush.call_count, 0)  # wake-up 06:30, back at 07:30
        reminders.check(now=at(7, 35))
        self.assertEqual(json.loads(webpush.call_args.kwargs["data"])["title"], "Water bowl back down")

    def test_lead_minutes_setting(self):
        self.assertEqual(NotificationPreference(user=self.user).walk_lead_minutes, 5)
        self.client.post("/pets/notifications/", {"action": "prefs", "walk_due": "on", "walk_lead_minutes": "10"})
        self.assertEqual(NotificationPreference.objects.get(user=self.user).walk_lead_minutes, 10)
        self.assertContains(self.client.get("/pets/notifications/"), 'name="walk_lead_minutes"')

    @mock.patch("pywebpush.webpush")
    def test_starting_the_walk_takes_the_reminder_away(self, webpush):
        self.subscribe()
        noon = timezone.make_aware(datetime.datetime.combine(timezone.localdate(), datetime.time(12)))
        PottyBreak.objects.create(pet=self.pet, time=noon - datetime.timedelta(hours=4, minutes=30), pee=True)
        with mock.patch("django.utils.timezone.now", return_value=noon):
            reminders.check(now=noon)
            self.assertEqual(webpush.call_count, 1)
            self.client.post("/log/walk/start/", {"pet": self.pet.slug})
        self.assertEqual(webpush.call_count, 2)
        self.assertEqual(json.loads(webpush.call_args.kwargs["data"]), {"close": f"walk-{self.pet.pk}"})
        page = self.client.get(f"/pets/{self.pet.slug}/").content.decode()
        self.assertIn(f'getNotifications({{tag: "walk-{self.pet.pk}"}})', page)  # and this device closes it too
        self.assertIn("data.close", self.client.get("/sw.js").content.decode())

    @mock.patch("pywebpush.webpush")
    def test_starting_a_walk_without_a_reminder_sends_nothing(self, webpush):
        self.subscribe()
        self.client.post("/log/walk/start/", {"pet": self.pet.slug})
        self.assertEqual(webpush.call_count, 0)

    @mock.patch("pywebpush.webpush")
    def test_no_walk_reminder_while_out_on_a_walk(self, webpush):
        from pets.models import Walk

        self.subscribe()
        noon = timezone.make_aware(datetime.datetime.combine(timezone.localdate(), datetime.time(12)))
        PottyBreak.objects.create(pet=self.pet, time=noon - datetime.timedelta(hours=4, minutes=30), pee=True)
        walk = Walk.objects.create(household=self.pet.household, start=noon - datetime.timedelta(minutes=5))
        walk.pets.set([self.pet])
        reminders.check(now=noon)
        self.assertEqual(webpush.call_count, 0)  # already out
        walk.end = noon
        walk.save()
        reminders.check(now=noon + datetime.timedelta(minutes=1))
        self.assertEqual(webpush.call_count, 0)  # back without a pee: not straight away...
        reminders.check(now=noon + datetime.timedelta(minutes=11))
        self.assertEqual(webpush.call_count, 1)  # ...but 5 minutes before trying again, 15 minutes after

    def test_checker_starts_with_the_server_and_checks_right_away(self):
        import threading

        with mock.patch.object(reminders, "_started", False), \
                mock.patch.object(reminders.settings, "HOUSETRAINED_REMINDER_THREAD", True), \
                mock.patch.object(reminders, "check") as check, \
                mock.patch.object(reminders.time, "sleep", side_effect=SystemExit), \
                mock.patch.object(threading, "Thread") as thread:
            reminders.start()
            reminders.start()  # once per process
            self.assertEqual(thread.call_count, 1)
            self.subscribe()
            with self.assertRaises(SystemExit):
                reminders._loop()  # checks first, then sleeps
            check.assert_called_once()
        self.assertEqual(reminders.INTERVAL, 60)
        with open(reminders.__file__.replace("pets\\reminders.py", "housetrained\\wsgi.py")
                  .replace("pets/reminders.py", "housetrained/wsgi.py"), encoding="utf-8") as f:
            self.assertIn("start_reminders()", f.read())

    @mock.patch("pywebpush.webpush")
    def test_no_walk_reminder_when_not_due_or_turned_off(self, webpush):
        self.subscribe()
        noon = timezone.make_aware(datetime.datetime.combine(timezone.localdate(), datetime.time(12)))
        PottyBreak.objects.create(pet=self.pet, time=noon - datetime.timedelta(minutes=20), pee=True)
        reminders.check(now=noon)
        self.assertEqual(webpush.call_count, 0)  # not due yet
        PottyBreak.objects.all().delete()
        PottyBreak.objects.create(pet=self.pet, time=noon - datetime.timedelta(hours=4), pee=True)
        NotificationPreference.objects.create(user=self.user, walk_due=False, walk_overdue=False)
        reminders.check(now=noon)
        self.assertEqual(webpush.call_count, 0)  # both walk reminders turned off

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


@override_settings(TIME_ZONE="UTC")
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

