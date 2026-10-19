"""Notes card (PUP-148), training (PUP-150) and Undo on quick logs (PUP-151)."""
import datetime
from unittest import mock

from django.utils import timezone

from pets import insights, undo
from pets.models import Command, Note, PottyBreak, Sleep, TrainingLog, Walk
from pets.tests.test_views import PetTestCase


class NotesTests(PetTestCase):
    def test_add_note_in_more_and_a_notes_card(self):
        page = self.client.get("/pets/ruby/").content.decode()
        self.assertIn("/log/note/add/?pet=ruby", page)
        self.assertIn("No notes yet.", page)
        Note.objects.create(household=self.household, pet=self.ruby, text="Loves the blue ball")
        Note.objects.create(household=self.household, text="Vet on Friday")  # household note shows too
        Note.objects.create(household=self.household, pet=self.rex, text="Rex only")
        page = self.client.get("/pets/ruby/").content.decode()
        self.assertIn("Loves the blue ball", page)
        self.assertIn("Vet on Friday", page)
        self.assertNotIn("Rex only", page)

    def test_notes_are_last_on_a_phone(self):
        page = self.client.get("/pets/ruby/").content.decode()
        self.assertIn('style="order: 9">', page)
        self.assertLess(page.index('style="order: 9">'), page.index("No notes yet."))


class TrainingTests(PetTestCase):
    def setUp(self):
        super().setUp()
        self.sit = Command.objects.create(pet=self.ruby, name="Sit", how_to="Hand up, say sit")

    def test_add_a_command(self):
        self.client.post("/log/command/add/", {"pet": self.ruby.pk, "name": "Leave it", "status": "working",
                                                "how_to": "", "when_to_use": "Dropped food"})
        self.assertTrue(Command.objects.filter(pet=self.ruby, name="Leave it", when_to_use="Dropped food").exists())

    def test_one_tap_logs_a_score_with_undo(self):
        page = self.client.get("/pets/ruby/").content.decode()
        self.assertIn("/log/training/quick/", page)
        self.assertIn('title="2 Needs help: Needs repeated cues', page)
        response = self.client.post("/log/training/quick/", {"command": self.sit.pk, "score": "4",
                                                              "next": "/pets/ruby/"}, follow=True)
        log = TrainingLog.objects.get()
        self.assertEqual((log.command, log.score, log.logged_by), (self.sit, 4, self.owner))
        self.assertContains(response, "Training logged: Sit, 4 Good.")
        self.assertContains(response, 'action="/undo/"')

    def test_logging_in_place_answers_with_the_undo_token(self):
        reply = self.client.post("/log/training/quick/", {"command": self.sit.pk, "score": "2"},
                                 HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertEqual((reply["ok"], reply["score"]), (True, 2))
        self.assertIn("Sit, 2 Needs help", reply["text"])
        self.assertNotContains(self.client.get("/pets/ruby/"), "Training logged")  # no banner on the next page too
        undone = self.client.post("/undo/", {"token": reply["token"]}, HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertEqual(undone, {"ok": True, "text": "Undone."})
        self.assertFalse(TrainingLog.objects.exists())

    def test_scores_are_1_to_5_and_commands_stay_in_the_household(self):
        self.client.post("/log/training/quick/", {"command": self.sit.pk, "score": "9"})
        other = Command.objects.create(pet=self._other_pet(), name="Stay")
        self.assertEqual(self.client.post("/log/training/quick/", {"command": other.pk, "score": "3"}).status_code, 404)
        self.assertFalse(TrainingLog.objects.exists())

    def _other_pet(self):
        from pets.models import Household, Pet
        return Pet.objects.create(household=Household.objects.create(name="Next door"), name="Max", type=Pet.DOG)

    def test_viewers_cannot_log(self):
        self.login(self.viewer)
        self.assertEqual(self.client.post("/log/training/quick/", {"command": self.sit.pk, "score": "3"}).status_code, 403)
        self.assertNotIn("/log/training/quick/", self.client.get("/pets/ruby/").content.decode())

    def test_card_week_average_and_known_tucked_away(self):
        TrainingLog.objects.create(command=self.sit, score=3)
        TrainingLog.objects.create(command=self.sit, score=4)
        TrainingLog.objects.create(command=self.sit, score=5, time=timezone.now() - datetime.timedelta(days=10))
        Command.objects.create(pet=self.ruby, name="Down", status=Command.KNOWN)
        Command.objects.create(pet=self.ruby, name="Shake", status=Command.RETIRED)
        card = insights.training_card(self.ruby)
        self.assertEqual([r["command"].name for r in card["working"]], ["Sit"])
        self.assertEqual(card["working"][0]["week"], "3.5")
        self.assertEqual([r["command"].name for r in card["known"]], ["Down"])

    def test_progress_page_charts_each_day(self):
        now = timezone.now()
        TrainingLog.objects.create(command=self.sit, score=2, time=now - datetime.timedelta(days=3))
        TrainingLog.objects.create(command=self.sit, score=4, time=now)
        TrainingLog.objects.create(command=self.sit, score=5, time=now)
        chart = insights.training_chart(self.sit)
        self.assertEqual((len(chart["points"]), chart["count"]), (2, 3))
        self.assertLess(chart["points"][1]["y"], chart["points"][0]["y"])  # 4.5 sits higher than 2
        page = self.client.get("/pets/ruby/training/").content.decode()
        self.assertIn("Hand up, say sit", page)
        self.assertIn("<polyline", page)

    def test_training_stays_out_of_all_activity_but_has_its_own_list(self):
        TrainingLog.objects.create(command=self.sit, score=3)
        from pets.entries import activity
        self.assertEqual(activity(self.ruby)[0], [])
        self.assertContains(self.client.get("/log/training/"), "Sit: 3 Slow")


class UndoTests(PetTestCase):
    def token(self, response):
        import re
        return re.search(r'name="token" value="([0-9a-f]+)"', response.content.decode()).group(1)

    def test_undo_a_quick_potty(self):
        response = self.client.post("/log/potty/quick/", {"pet": "ruby", "what": "pee", "next": "/pets/ruby/"}, follow=True)
        self.assertEqual(PottyBreak.objects.count(), 1)
        response = self.client.post("/undo/", {"token": self.token(response)}, follow=True)
        self.assertFalse(PottyBreak.objects.exists())
        self.assertContains(response, "Undone.")

    def test_undo_nothing_clears_the_retry(self):
        response = self.client.post("/log/potty/quick/", {"pet": "ruby", "what": "nothing"}, follow=True)
        self.client.post("/undo/", {"token": self.token(response)})
        self.ruby.refresh_from_db()
        self.assertFalse(PottyBreak.everything.exists())
        self.assertIsNone(self.ruby.retry_at)

    def test_undo_starting_a_walk_or_sleep(self):
        response = self.client.post("/log/walk/start/", {"pet": "ruby"}, follow=True)
        self.client.post("/undo/", {"token": self.token(response)})
        self.assertFalse(Walk.objects.exists())
        response = self.client.post("/log/sleep/start/", follow=True)
        self.assertEqual(Sleep.objects.count(), 3)
        self.client.post("/undo/", {"token": self.token(response)})
        self.assertFalse(Sleep.objects.exists())

    def test_undo_a_form_entry(self):
        response = self.client.post("/log/note/add/", {"pet": self.ruby.pk, "time_date": "2026-10-01",
                                                       "time_time": "08:00", "text": "Oops"}, follow=True)
        self.client.post("/undo/", {"token": self.token(response)})
        self.assertFalse(Note.objects.exists())

    def test_only_for_ten_seconds(self):
        response = self.client.post("/log/potty/quick/", {"pet": "ruby", "what": "pee"}, follow=True)
        token = self.token(response)
        later = undo.time.time() + undo.SECONDS + undo.GRACE + 1
        with mock.patch("pets.undo.time.time", return_value=later):
            response = self.client.post("/undo/", {"token": token}, follow=True)
        self.assertEqual(PottyBreak.objects.count(), 1)
        self.assertContains(response, "Too late to undo")
        self.assertIn("setTimeout", response.content.decode())

    def test_only_the_person_who_logged_it(self):
        response = self.client.post("/log/potty/quick/", {"pet": "ruby", "what": "pee"}, follow=True)
        token = self.token(response)
        self.login(self.member)
        self.client.post("/undo/", {"token": token})
        self.assertEqual(PottyBreak.objects.count(), 1)

    def test_setup_entries_have_no_undo(self):
        response = self.client.post("/log/spot/add/", {"name": "Rug"}, follow=True)
        self.assertNotContains(response, 'action="/undo/"')
