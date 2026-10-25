"""Pet photos: framed and resized in the browser, only stored here (PUP-169)."""
import base64
import json
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from pets.models import Pet
from pets.tests.test_views import PetTestCase

MEDIA = tempfile.mkdtemp()
JPEG = b"\xff\xd8\xff\xe0" + b"photo bytes" * 20


def photo(name="photo.jpg", data=JPEG):
    return SimpleUploadedFile(name, data, content_type="image/jpeg")


def square(data=b"\xff\xd8\xff\xe0square"):
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


BOX = {"x": 0.5, "y": 0.25, "w": 0.25, "h": 0.375}


@override_settings(MEDIA_ROOT=MEDIA)
class PictureTests(PetTestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def save(self, **extra):
        data = {"name": "Ruby", "type": "dog", "active": "on", "potty_locations": ["outside"], **extra}
        return self.client.post(f"/log/pet/{self.ruby.pk}/", data)

    def pet(self):
        return Pet.objects.get(pk=self.ruby.pk)

    def test_photo_and_square_are_stored_as_sent(self):
        self.save(picture=photo(), picture_crop=json.dumps(BOX), picture_square=square())
        pet = self.pet()
        with pet.picture.open("rb") as f:
            self.assertEqual(f.read(), JPEG)  # the original is kept, for framing again
        with pet.picture_cropped.open("rb") as f:
            self.assertEqual(f.read(), b"\xff\xd8\xff\xe0square")
        self.assertEqual(pet.picture_crop, BOX)
        self.assertEqual(pet.card_picture, pet.picture_cropped)

    def test_only_jpegs_and_always_a_jpg_name(self):
        response = self.save(picture=photo("dog.png", b"\x89PNG\r\n\x1a\nnot a jpeg"))
        self.assertContains(response, "That photo didn&#x27;t come through")
        self.assertFalse(self.pet().picture)
        self.save(picture=photo("page.html", JPEG + b"<script>alert(1)</script>"))
        self.assertTrue(self.pet().picture.name.endswith(".jpg"))

    def test_new_picture_without_a_square_shows_the_whole_photo(self):
        self.save(picture=photo(), picture_crop=json.dumps(BOX), picture_square=square())
        self.save(picture=photo())
        pet = self.pet()
        self.assertFalse(pet.picture_cropped)
        self.assertIsNone(pet.picture_crop)
        self.assertEqual(pet.card_picture, pet.picture)

    def test_framing_again_without_a_new_upload(self):
        self.save(picture=photo())
        self.save(picture_crop=json.dumps(BOX), picture_square=square(b"\xff\xd8\xff\xe0again"))
        pet = self.pet()
        self.assertEqual(pet.picture_crop, BOX)
        with pet.picture_cropped.open("rb") as f:
            self.assertEqual(f.read(), b"\xff\xd8\xff\xe0again")

    def test_bad_squares_and_boxes_are_ignored(self):
        for bad in ("nonsense", "data:image/png;base64," + base64.b64encode(b"png").decode(),
                    square(b"<html>"), "data:image/jpeg;base64,!!!"):
            self.save(picture=photo(), picture_square=bad)
            self.assertFalse(self.pet().picture_cropped)
        self.save(picture=photo(), picture_crop=json.dumps({"x": 2, "y": 0, "w": 1, "h": 1}), picture_square=square())
        self.assertIsNone(self.pet().picture_crop)
        self.assertTrue(self.pet().picture_cropped)

    def test_removing_the_picture_removes_the_crop(self):
        self.save(picture=photo(), picture_crop=json.dumps(BOX), picture_square=square())
        self.save(**{"picture-clear": "on"})
        pet = self.pet()
        self.assertFalse(pet.picture)
        self.assertFalse(pet.picture_cropped)
        self.assertIsNone(pet.picture_crop)
