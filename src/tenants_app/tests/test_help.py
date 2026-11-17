"""The Help center inside the dashboard."""

from django.contrib.auth.models import User
from django.test import TestCase

from gateway.help import MODEL_TOPICS, TOPICS


class HelpCenterTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("ops", "ops@example.com", "pw"))

    def test_help_home_lists_every_topic(self):
        html = self.client.get("/admin/help/").content.decode()
        for t in TOPICS:
            self.assertIn(f"/admin/help/{t.slug}/", html, t.slug)

    def test_every_topic_page_renders(self):
        for t in TOPICS:
            r = self.client.get(f"/admin/help/{t.slug}/")
            self.assertEqual(r.status_code, 200, t.slug)
            html = r.content.decode()
            self.assertIn(t.title, html)
            self.assertIn('class="pg-prose"', html)
            self.assertNotIn("{%", html, f"unrendered template tag in {t.slug}")

    def test_key_guides_cover_products_apps_and_keys(self):
        html = self.client.get("/admin/help/add-new-app/").content.decode()
        for text in ("Generate new private key", ".p8", "Products", "Apps", "google-services.json",
                     "publishable_key", "Data safety"):
            self.assertIn(text, html)
        keys = self.client.get("/admin/help/keys/").content.decode()
        for text in ("MASTER_ENCRYPTION_KEY", "Transaction pooler", "Show identity secret", "Rotate keys"):
            self.assertIn(text, keys)

    def test_unknown_topic_is_404(self):
        self.assertEqual(self.client.get("/admin/help/nope/").status_code, 404)

    def test_help_needs_login(self):
        self.client.logout()
        self.assertEqual(self.client.get("/admin/help/").status_code, 302)
        self.assertEqual(self.client.get("/admin/help/products/").status_code, 302)

    def test_every_list_page_has_a_working_help_button(self):
        for model, slug in MODEL_TOPICS.items():
            app, name = model.split(".")
            listing = self.client.get(f"/admin/{app}/{name}/").content.decode()
            self.assertIn("How this page works", listing, model)
            r = self.client.get(f"/admin/{app}/{name}/how-it-works/")
            self.assertEqual((r.status_code, r["Location"]), (302, f"/admin/help/{slug}/"), model)

    def test_sidebar_links_to_help(self):
        html = self.client.get("/admin/").content.decode()
        for path in ("/admin/help/", "/admin/help/getting-started/", "/admin/help/add-new-app/",
                     "/admin/help/keys/", "/admin/help/troubleshooting/"):
            self.assertIn(path, html)
