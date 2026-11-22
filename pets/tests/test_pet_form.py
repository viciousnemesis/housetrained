"""The pet's settings form (PUP-205): hours and minutes, and no stored file path."""
import json
import os

from django.conf import settings
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


class PetOrderTests(PetTestCase):
    def test_arrows_in_setup_set_the_order_everywhere(self):
        self.assertEqual([p.name for p in Pet.objects.all()], ["Ruby", "Rex", "Tom"])  # new pets go last
        self.client.post("/pets/setup/", {"move": self.tom.pk, "direction": "up"})
        self.assertEqual([p.name for p in Pet.objects.all()], ["Ruby", "Tom", "Rex"])
        form = self.client.get("/log/meal/add/").context["form"]
        self.assertEqual([p.name for p in form.fields["pets"].queryset], ["Ruby", "Tom", "Rex"])

    def test_viewers_cant_reorder(self):
        self.login(self.viewer)
        self.assertEqual(self.client.post("/pets/setup/", {"move": self.tom.pk, "direction": "up"}).status_code, 403)


class ResumeTests(PetTestCase):
    """The installed app reopens on the last screen (PUP-208)."""

    def last(self, url):
        page = self.client.get(url).content.decode()
        return page.split('localStorage.setItem("housetrained:last", "')[1].split('"')[0]

    def test_a_page_is_remembered_as_it_is(self):
        self.assertEqual(self.last("/pets/ruby/progress/"), "/pets/ruby/progress/")

    def test_a_form_is_remembered_as_the_page_it_came_from(self):
        self.assertEqual(self.last("/log/meal/add/?pet=ruby"), "/pets/ruby/")
        self.assertEqual(self.last(f"/log/pet/{self.ruby.pk}/?next=/pets/setup/"), "/pets/setup/")

    def test_every_page_is_part_of_the_installed_app(self):
        # Without "scope", Android scopes the app to the start page's folder and shows a browser bar everywhere else.
        path = os.path.join(settings.BASE_DIR, "housetrained/static_src/root/site.webmanifest")
        with open(path, encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual((manifest["scope"], manifest["start_url"]), ("/", "/resume/"))

    def test_the_start_page_goes_on_from_there(self):
        page = self.client.get("/resume/")
        self.assertContains(page, 'localStorage.getItem("housetrained:last")')
        self.client.logout()
        self.assertEqual(self.client.get("/resume/").status_code, 302)  # signed out: the login page
