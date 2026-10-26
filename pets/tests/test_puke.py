"""Puke: its own log, with the potty break's options; never an accident (PUP-182)."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from pets import exporter, insights, progress
from pets.entries import activity
from pets.models import AccidentSpot, PottyLocation, Puke
from pets.tests.test_views import PetTestCase


@override_settings(TIME_ZONE="UTC")
class PukeTests(PetTestCase):
    def add(self, **data):
        base = {"pets": [self.ruby.pk], "time": self.now, "location": "inside"}
        return self.client.post("/log/puke/add/", {**base, **data})

    def test_logged_with_where_what_and_what_came_up(self):
        self.add(spot_new="Kitchen", context_new="Ate grass", contents="grass")
        p = Puke.objects.get()
        self.assertEqual((p.spot.name, p.context, p.contents), ("Kitchen", ["Ate grass"], "grass"))
        rows, _more = activity(self.ruby, "puke")
        self.assertEqual([r["summary"] for r in rows], ["Puke, inside (Kitchen) · grass"])
        self.assertEqual(rows[0]["tags"], ["puke"])

    def test_not_an_accident(self):
        self.add(spot_new="Kitchen")
        self.assertEqual(progress.patterns(self.ruby)["spots"], [])
        self.assertIsNone(progress.streak(self.ruby))  # no potty breaks at all
        tiles = {label: value for label, value, _sub in insights.today_card(self.ruby)["tiles"]}
        self.assertEqual((tiles["Accidents"], tiles["Puke"]), ("0", "1"))
        self.assertEqual(activity(self.ruby, "accident")[0], [])

    def test_the_form(self):
        form = self.client.get("/log/puke/add/").context["form"]
        self.assertEqual(form.initial["location"], "inside")
        PottyLocation.objects.create(household=self.household, name="Car", accident=True)
        page = self.client.get("/log/puke/add/")
        self.assertContains(page, 'data-add-to="spot_new"')
        self.assertContains(page, "What came up")
        self.add(location="outside", spot_new="Garden")
        self.assertIsNone(Puke.objects.get().spot)  # where is only for accident locations, as on potty breaks
        self.assertContains(self.client.get("/pets/ruby/"), "/log/puke/add/")

    def test_export_and_import(self):
        AccidentSpot.objects.create(household=self.household, name="Kitchen")
        text = "pet,time,location,found_later,spot,contents\nRuby,2026-10-01 07:10,inside,yes,kitchen,foam\n"
        upload = lambda confirm: self.client.post("/pets/setup/import/", {"kind": "puke", "csv_text": text,
                                                                          "confirm": "1"} if confirm else
                                                  {"kind": "puke", "file": SimpleUploadedFile("x.csv", text.encode())})
        self.assertContains(upload(False), "Ready: 1 new row")
        upload(True)
        p = Puke.objects.get()
        self.assertEqual((p.spot.name, p.found_later, p.contents), ("Kitchen", True, "foam"))
        columns, rows = exporter.tables(self.household)["puke.csv"]
        self.assertEqual(rows[0][2:6], ["inside", "yes", "Kitchen", "foam"])
        text = text.replace("kitchen", "Attic")
        self.assertContains(upload(False), "Unknown spot")
        self.assertFalse(AccidentSpot.objects.filter(name="Attic").exists())
