"""Self-hosted means nothing loads from or reports to the internet: every script, style, font and image the
pages use comes from this server."""
import re

from django.test import override_settings

from pets.tests.test_views import PetTestCase

EXTERNAL = re.compile(
    r"""<(?:script|img|iframe|source|video|audio)[^>]+src=["']?(?:https?:)?//"""
    r"""|<link[^>]+href=["']?(?:https?:)?//"""
    r"""|url\(\s*["']?(?:https?:)?//"""
    r"""|@import\s+["']?(?:url\()?["']?(?:https?:)?//""",
    re.IGNORECASE,
)


class NothingLeavesTheServerTests(PetTestCase):
    PAGES = ["/pets/", "/pets/ruby/", "/pets/setup/", "/pets/setup/members/",
             "/pets/setup/import/", "/pets/notifications/", "/log/potty/", "/log/potty/add/", "/log/walk/add/",
             "/log/fill/add/", "/log/meal/add/", f"/log/pet/0/", "/user/settings/", "/settings/", "/login/"]

    def test_pages_load_nothing_from_elsewhere(self):
        self.owner.is_superuser = True
        self.owner.save()
        for path in self.PAGES:
            if path == "/log/pet/0/":
                path = f"/log/pet/{self.ruby.pk}/"
            if path == "/login/":
                self.client.logout()
            response = self.client.get(path, follow=True)
            self.assertEqual(response.status_code, 200, path)
            found = EXTERNAL.search(response.content.decode())
            self.assertIsNone(found, f"{path} loads something from outside: {found and found.group(0)}")
            if path == "/login/":
                self.client.force_login(self.owner)

    def test_service_worker_is_local(self):
        self.assertIsNone(re.search(r"https?://", self.client.get("/sw.js").content.decode()))
