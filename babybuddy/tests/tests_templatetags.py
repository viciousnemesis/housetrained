# -*- coding: utf-8 -*-
from django.test import SimpleTestCase

from babybuddy.templatetags import babybuddy


class TemplateTagsTestCase(SimpleTestCase):
    def test_bool_icon(self):
        self.assertIn("icon-true", babybuddy.bool_icon(True))
        self.assertIn("icon-false", babybuddy.bool_icon(False))
