"""The pet's settings form (PUP-205): hours and minutes, and no stored file path."""
from django.core.files.base import ContentFile
from django.test import override_settings

from pets.models import Pet
from pets.tests.test_pictures import MEDIA
from pets.tests.test_views import PetTestCase


@override_settings(MEDIA_ROOT=MEDIA)
class PetFormTests(PetTestCase):
    def save(self, **extra):
        return self.client.post(f"/log/pet/{self.ruby.pk}/", {"name": "Ruby", "type": "dog", "active": "on", **extra})

    def test_pee_and_poop_every_in_hours_and_minutes(self):
        self.save(pee_every_0="2", pee_every_1="45", poop_every_0="5", poop_every_1="0")
        ruby = Pet.objects.get(pk=self.ruby.pk)
        self.assertEqual((ruby.pee_every, ruby.poop_every), (165, 300))
        page = self.client.get(f"/log/pet/{self.ruby.pk}/").content.decode()
        self.assertIn('<option value="45" selected>45</option>', page)

    def test_both_blank_is_no_value(self):
        self.save(pee_every_0="", pee_every_1="", poop_every_0="", poop_every_1="")
        self.assertIsNone(Pet.objects.get(pk=self.ruby.pk).poop_every)

    def test_the_photo_shows_without_its_file_path(self):
        self.ruby.picture.save("secret-name.jpg", ContentFile(b"\xff\xd8\xff\xe0x"))
        page = self.client.get(f"/log/pet/{self.ruby.pk}/").content.decode()
        self.assertNotIn("secret-name", page.split('id="crop-box"')[0])
        self.assertIn("Remove photo", page)
