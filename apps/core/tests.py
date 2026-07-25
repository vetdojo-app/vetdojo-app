from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse


class HomePageTests(TestCase):
    @patch("apps.qa.models.RagChunk.objects")
    def test_homepage_displays_live_searchable_transcript_count(self, objects):
        objects.values.return_value.distinct.return_value.count.return_value = 527
        response = self.client.get(reverse("core:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "527 searchable transcripts")
        self.assertContains(response, "images/vetdojo-logo.jpeg")
        self.assertContains(response, "css/brand-theme.css")
