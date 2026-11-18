"""Pets walk together or alone (PUP-204)."""
from pets.models import Pet, Walk
from pets.tests.test_views import PetTestCase


class WalkCompanyTests(PetTestCase):
    def listed(self, url):
        form = self.client.get(url).context["form"]
        return set(form.fields["pets"].queryset)

    def test_a_together_pet_lists_the_others_that_walk_together(self):
        self.assertEqual(self.listed("/log/walk/add/?pet=ruby"), {self.ruby, self.rex})

    def test_a_pet_that_walks_alone_lists_only_itself(self):
        self.assertEqual(self.listed("/log/walk/add/?pet=tom"), {self.tom})

    def test_no_pet_picked_starts_with_the_together_pets(self):
        form = self.client.get("/log/walk/add/").context["form"]
        self.assertEqual(set(form.initial["pets"]), {self.ruby, self.rex})

    def test_a_pet_that_walks_alone_cant_be_logged_with_others(self):
        response = self.client.post("/log/walk/add/", {
            "pets": [self.ruby.pk, self.tom.pk], "start": self.now, "walker_choice": str(self.owner.pk),
            "distance_unit": "km"})
        self.assertContains(response, "Tom walks alone")
        self.assertFalse(Walk.objects.exists())

    def test_no_one_walks_together_so_the_walk_button_asks(self):
        Pet.objects.update(walks_together=False)
        response = self.client.post("/log/walk/start/")
        self.assertRedirects(response, "/log/walk/add/", fetch_redirect_response=False)
        self.assertFalse(Walk.objects.exists())

    def test_new_pets_walk_as_their_type_does(self):
        for name, kind in (("Fido", "dog"), ("Kit", "cat")):
            self.client.post("/log/pet/add/", {"name": name, "type": kind, "active": "on"})
        self.assertTrue(Pet.objects.get(name="Fido").walks_together)
        self.assertFalse(Pet.objects.get(name="Kit").walks_together)

    def test_changed_in_the_pets_settings(self):
        self.client.post(f"/log/pet/{self.tom.pk}/", {"name": "Tom", "type": "cat", "active": "on",
                                                     "walks_together": "on"})
        self.tom.refresh_from_db()
        self.assertTrue(self.tom.walks_together)
