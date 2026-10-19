"""Progress (PUP-73)."""
import datetime
import json
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from pets import progress, reminders
from pets.models import NotificationPreference, PottyBreak, PushSubscription
from pets.tests.test_views import PetTestCase


@override_settings(TIME_ZONE="UTC")
class ProgressTests(PetTestCase):
    def potty(self, days_ago, hours=0, inside=False):
        return PottyBreak.objects.create(pet=self.ruby, pee=True, time=timezone.now() - datetime.timedelta(days=days_ago, hours=hours),
                                         location=PottyBreak.INSIDE if inside else PottyBreak.OUTSIDE)

    def test_streak_and_best(self):
        self.potty(10)
        self.potty(9, inside=True)  # then 6 days clean
        self.potty(3, inside=True)  # then 3 days clean (now)
        self.potty(1)
        s = progress.streak(self.ruby)
        self.assertEqual(s["current"].days, 3)
        self.assertEqual(s["best"].days, 6)
        self.assertFalse(s["is_best"])
        self.assertEqual(s["title"], "3 days accident-free")

    def test_no_accidents_counts_from_the_first_log(self):
        self.potty(5)
        self.assertEqual(progress.streak(self.ruby)["current"].days, 5)

    def test_weeks(self):
        for i in range(4):
            self.potty(1, hours=i)
        self.potty(1, hours=5, inside=True)
        self.potty(8)
        self.potty(8, hours=1, inside=True)
        this, last = progress.weeks(self.ruby, count=2)
        self.assertEqual((this["breaks"], this["accidents"], this["right_place"]), (5, 1, 80))
        self.assertEqual((last["breaks"], last["accidents"], last["right_place"]), (2, 1, 50))
        self.assertEqual(progress.summary(self.ruby), "1 accident this week (1 the week before) · 80% in the right place")

    def test_pages(self):
        self.potty(2)
        self.potty(1, inside=True)
        page = self.client.get("/pets/ruby/progress/")
        self.assertContains(page, "accident-free")
        self.assertContains(page, "<rect")
        card = self.client.get("/pets/ruby/")
        self.assertContains(card, "/pets/ruby/progress/")
        self.assertNotContains(card, "Past Week")
        self.assertEqual(self.client.get("/pets/rex/progress/").status_code, 200)  # no data yet

    @mock.patch("pywebpush.webpush")
    def test_weekly_notification_on_monday_morning(self, webpush):
        from dbsettings.loading import set_setting_value

        set_setting_value("pets.models", "Pet", "push_notifications", True)
        PushSubscription.objects.create(user=self.owner, endpoint="https://push.example.com/x", p256dh="k", auth="a")
        NotificationPreference.objects.create(user=self.owner, walk_due=False, low_supply=False, weekly_progress=True)
        monday = timezone.now()
        while monday.weekday() != 0:
            monday -= datetime.timedelta(days=1)
        monday = monday.replace(hour=10, minute=0)
        PottyBreak.objects.create(pet=self.ruby, pee=True, time=monday - datetime.timedelta(days=1))
        reminders.check(now=monday)
        reminders.check(now=monday)
        self.assertEqual(webpush.call_count, 1)
        message = json.loads(webpush.call_args.kwargs["data"])
        self.assertEqual(message["title"], "Ruby's week")
        reminders.check(now=monday + datetime.timedelta(days=1))  # not on Tuesday
        self.assertEqual(webpush.call_count, 1)
