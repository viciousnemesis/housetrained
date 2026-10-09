"""Export (PUP-117): everything as CSV, and a file goes back in through the importer."""
import csv
import datetime
import io
import zipfile

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from pets import importer
from pets.models import Household, Meal, Membership, Pet, PottyBreak, Walk


class ExportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="x")
        self.home = Household.objects.create(name="Home")
        Membership.objects.create(household=self.home, user=self.user, role=Membership.OWNER)
        self.ruby = Pet.objects.create(household=self.home, name="Ruby", type=Pet.DOG)
        self.client.force_login(self.user)
        now = timezone.now().replace(second=0, microsecond=0)
        PottyBreak.objects.create(pet=self.ruby, time=now - datetime.timedelta(hours=2), pee=True, notes="by the oak")
        PottyBreak.objects.create(pet=self.ruby, time=now - datetime.timedelta(hours=1), poop=True,
                                  location=PottyBreak.INSIDE)
        Meal.objects.create(pet=self.ruby, time=now, amount=60, portion=0.25)
        walk = Walk.objects.create(household=self.home, start=now - datetime.timedelta(hours=3),
                                   end=now - datetime.timedelta(hours=2), distance=1200)
        walk.pets.set([self.ruby])

    def files(self):
        response = self.client.get("/pets/setup/export/")
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("housetrained-export-", response["Content-Disposition"])
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        return {name: list(csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig"))))
                for name in archive.namelist()}

    def test_everything_is_in_it(self):
        files = self.files()
        self.assertEqual(len(files["potty.csv"]), 2)
        self.assertEqual(files["potty.csv"][0]["notes"], "by the oak")
        self.assertEqual((files["meal.csv"][0]["amount"], files["meal.csv"][0]["portion"]), ("60", "0.25"))
        self.assertEqual((files["walk.csv"][0]["distance"], files["walk.csv"][0]["distance_unit"]), ("1200", "m"))
        self.assertEqual(files["pets.csv"][0]["name"], "Ruby")
        self.assertIn("notes.csv", files)

    def test_it_imports_back(self):
        response = self.client.get("/pets/setup/export/")
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        text = archive.read("potty.csv").decode("utf-8-sig")
        PottyBreak.everything.all().delete()
        job = importer.Importer(self.home, self.user, "potty")
        rows, problems = job.read(text)
        self.assertEqual(problems, [])
        self.assertTrue(all(not r.errors for r in rows), [r.errors for r in rows])
        job.save(rows)
        self.assertEqual(PottyBreak.objects.count(), 2)

    def test_only_this_household(self):
        other = Household.objects.create(name="Other")
        stranger = Pet.objects.create(household=other, name="Theirs", type=Pet.DOG)
        PottyBreak.objects.create(pet=stranger, time=timezone.now(), pee=True)
        names = {r["pet"] for r in self.files()["potty.csv"]}
        self.assertEqual(names, {"Ruby"})

    def test_needs_sign_in(self):
        self.client.logout()
        self.assertEqual(self.client.get("/pets/setup/export/").status_code, 302)
