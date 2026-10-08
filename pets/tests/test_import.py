"""CSV import (PUP-35)."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from pets.models import Container, Fill, Meal, Measurement, PottyBreak, Sleep, Walk
from pets.tests.test_views import PetTestCase


@override_settings(BABY_MODE=False, TIME_ZONE="UTC")
class ImportTests(PetTestCase):
    def upload(self, kind, text, confirm=False):
        response = self.client.post("/pets/setup/import/", {"kind": kind, "file": SimpleUploadedFile("x.csv", text.encode())})
        if confirm:
            response = self.client.post("/pets/setup/import/", {"kind": kind, "csv_text": text, "confirm": "1"})
        return response

    def test_template_download(self):
        response = self.client.get("/pets/setup/import/?template=potty")
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertTrue(response.content.decode().startswith("pet,time,pee,poop,location,notes"))

    def test_potty_preview_then_import(self):
        text = "pet,time,pee,poop,location\nRuby,2026-10-01 07:10,yes,no,outside\nruby,2026-10-01 09:00,yes,yes,inside\n"
        preview = self.upload("potty", text)
        self.assertContains(preview, "Ready: 2 new rows")
        self.assertFalse(PottyBreak.objects.exists())
        self.upload("potty", text, confirm=True)
        self.assertEqual(PottyBreak.objects.filter(pet=self.ruby).count(), 2)
        self.assertEqual(PottyBreak.objects.filter(poop=True, location="inside").count(), 1)

    def test_errors_block_the_import(self):
        text = "pet,time,pee,poop,location\nRuby,2026-10-01 07:10,yes,no,outside\nGhost,2026-10-01 08:00,yes,no,outside\n"
        preview = self.upload("potty", text, confirm=True)
        self.assertContains(preview, "Unknown pet")
        self.assertFalse(PottyBreak.objects.exists())

    def test_rows_already_logged_are_skipped(self):
        text = "pet,time,pee,poop,location\nRuby,2026-10-01 07:10,yes,no,outside\nRuby,2026-10-01 07:10,yes,no,outside\n"
        self.upload("potty", text, confirm=True)
        self.upload("potty", text, confirm=True)
        self.assertEqual(PottyBreak.objects.count(), 1)

    def test_missing_columns(self):
        self.assertContains(self.upload("potty", "pet,time\nRuby,2026-10-01 07:10\n"), "Missing columns")

    def test_walks_with_several_pets_and_walkers(self):
        text = ("pets,start,end,walker,distance,distance_unit\n"
                "Ruby; Rex,2026-10-01 07:00,2026-10-01 07:30,Max,1.5,mi\n"
                "Ruby,2026-10-01 12:00,2026-10-01 12:20,Grandma,,\n")
        self.upload("walk", text, confirm=True)
        first, second = Walk.objects.order_by("start")
        self.assertEqual(set(first.pets.all()), {self.ruby, self.rex})
        self.assertEqual((first.walker, round(first.distance)), (self.member, 2414))
        self.assertEqual((second.walker, second.walker_name), (None, "Grandma"))

    def test_meals_sleep_measurements_and_fills(self):
        self.upload("meal", "pet,time,food,amount,unit,water,water_amount,water_unit\nRuby,2026-10-01 07:30,yes,2,oz,no,,\n", confirm=True)
        self.assertAlmostEqual(Meal.objects.get().amount, 56.7, places=1)
        self.upload("sleep", "pet,start,end,kind\nTom,2026-10-01 13:00,2026-10-01 15:00,nap\n", confirm=True)
        self.assertEqual(Sleep.objects.get().pet, self.tom)
        self.upload("measurement", "pet,date,kind,value,unit\nRuby,2026-10-01,weight,10,lb\n", confirm=True)
        self.assertAlmostEqual(Measurement.objects.get().value, 4.536, places=3)
        bowl = Container.objects.create(household=self.household, name="Food bowl", kind=Container.FOOD_BOWL,
                                        amount_per_bowl=120)
        bowl.pets.set([self.ruby])
        self.upload("fill", "bowl,time,amount,unit,eaten_right_away\nfood bowl,2026-10-01 18:00,0.5,bowls,yes\n", confirm=True)
        fill = Fill.objects.get()
        self.assertEqual((fill.amount, fill.meals.count()), (60, 1))

    def test_viewer_cannot_import(self):
        self.login(self.viewer)
        self.assertEqual(self.client.get("/pets/setup/import/").status_code, 403)
        self.assertEqual(self.upload("potty", "pet,time,pee,poop,location\nRuby,2026-10-01 07:10,yes,no,outside\n").status_code, 403)
