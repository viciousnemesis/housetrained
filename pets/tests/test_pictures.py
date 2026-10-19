"""Pet photo framing (crop box)."""
import io
import json
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from pets.models import Pet
from pets.tests.test_views import PetTestCase

MEDIA = tempfile.mkdtemp()


def photo(width=1200, height=800):
    image = Image.new("RGB", (width, height), (90, 140, 70))
    image.paste((220, 170, 90), (800, 150, 1100, 450))  # the "pet", on the right
    out = io.BytesIO()
    image.save(out, "JPEG")
    return SimpleUploadedFile("dog.jpg", out.getvalue(), content_type="image/jpeg")


@override_settings(MEDIA_ROOT=MEDIA)
class PictureTests(PetTestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def save(self, **extra):
        data = {"name": "Ruby", "type": "dog", "active": "on", "potty_locations": ["outside"], **extra}
        return self.client.post(f"/log/pet/{self.ruby.pk}/", data)

    def test_crop_box_makes_a_square_of_that_part(self):
        crop = {"x": 800 / 1200, "y": 150 / 800, "w": 300 / 1200, "h": 300 / 800}
        self.save(picture=photo(), picture_crop=json.dumps(crop))
        pet = Pet.objects.get(pk=self.ruby.pk)
        self.assertTrue(pet.picture)  # the original is kept
        cropped = Image.open(pet.picture_cropped.path)
        self.assertEqual(cropped.size, (300, 300))
        red, green, _blue = cropped.getpixel((150, 150))
        self.assertLess(abs(red - 220) + abs(green - 170), 8)  # the pet's colour, give or take JPEG
        self.assertEqual(pet.card_picture, pet.picture_cropped)

    def test_new_picture_without_a_box_uses_the_middle(self):
        self.save(picture=photo())
        pet = Pet.objects.get(pk=self.ruby.pk)
        self.assertEqual(Image.open(pet.picture_cropped.path).size, (800, 800))

    def test_recrop_without_new_upload(self):
        self.save(picture=photo())
        crop = {"x": 0.5, "y": 0.25, "w": 0.25, "h": 0.375}
        self.save(picture_crop=json.dumps(crop))
        pet = Pet.objects.get(pk=self.ruby.pk)
        self.assertEqual(pet.picture_crop, crop)
        self.assertEqual(Image.open(pet.picture_cropped.path).size, (300, 300))

    def test_bad_boxes_are_ignored(self):
        for bad in ("nonsense", json.dumps({"x": 2, "y": 0, "w": 1, "h": 1}), json.dumps({"x": 0})):
            self.save(picture=photo(), picture_crop=bad)
            self.assertEqual(Image.open(Pet.objects.get(pk=self.ruby.pk).picture_cropped.path).size, (800, 800))

    def test_removing_the_picture_removes_the_crop(self):
        self.save(picture=photo())
        self.save(**{"picture-clear": "on"})
        pet = Pet.objects.get(pk=self.ruby.pk)
        self.assertFalse(pet.picture)
        self.assertFalse(pet.picture_cropped)
