# -*- coding: utf-8 -*-
from django.test import SimpleTestCase

from housetrained.templatetags import housetrained


class TemplateTagsTestCase(SimpleTestCase):
    def test_bool_icon(self):
        self.assertIn("icon-true", housetrained.bool_icon(True))
        self.assertIn("icon-false", housetrained.bool_icon(False))
