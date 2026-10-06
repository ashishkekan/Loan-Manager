from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from loans.models import AppearancePreference, SiteAppearance
from loans.themes import PALETTES


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AppearanceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('theme-user', password='test-pass')
        self.other = get_user_model().objects.create_user('other-theme-user')
        self.admin = get_user_model().objects.create_user('theme-admin', is_staff=True)
        self.client.force_login(self.user)
        self.url = reverse('save_theme')

    def test_all_palettes_persist_across_pages_and_remain_personal(self):
        for palette in PALETTES:
            with self.subTest(palette=palette):
                response = self.client.post(self.url, {'palette': palette})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['palette'], palette)
                for route in ['dashboard', 'loan_list', 'settings_dashboard']:
                    self.assertContains(self.client.get(reverse(route)), f'data-palette="{palette}"')
        self.assertFalse(AppearancePreference.objects.filter(user=self.other).exists())
        self.client.force_login(self.other)
        self.assertContains(self.client.get(reverse('dashboard')), 'data-palette="green"')

    def test_defaults_inheritance_override_reset_and_policy(self):
        self.client.post(self.url, {'palette': 'pink'})
        self.client.force_login(self.admin)
        response = self.client.post(self.url, {'scope': 'workspace', 'palette': 'purple'})
        self.assertEqual(response.status_code, 200)
        self.client.force_login(self.other)
        self.assertContains(self.client.get(reverse('dashboard')), 'data-palette="purple"')
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('dashboard')), 'data-palette="pink"')
        self.client.post(self.url, {'palette': 'inherit'})
        self.assertContains(self.client.get(reverse('dashboard')), 'data-palette="purple"')
        self.client.post(self.url, {'palette': 'red'})
        self.client.force_login(self.admin)
        self.client.post(self.url, {'scope':'workspace', 'palette':'blue', 'allow_personal':'false'})
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('dashboard')), 'data-palette="blue"')
        self.assertEqual(self.client.post(self.url, {'palette':'pink'}).status_code, 403)
        for route in ['update_settings_theme', 'update_appearance_preferences']:
            self.assertEqual(self.client.post(reverse(route), {'theme':'dark'}).status_code, 403)
        self.client.logout()
        self.assertContains(self.client.get('/'), 'data-palette="blue"')

    def test_authorization_validation_and_csrf(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.post(self.url, {'scope':'workspace','palette':'red'}).status_code, 403)
        for payload in [{'palette':'invalid'}, {'palette':'pink','font':'invalid'}, {'palette':'blue','mode':'invalid'}, {'palette':'green','scope':'invalid'}]:
            self.assertEqual(self.client.post(self.url, payload).status_code, 400)
        self.assertFalse(SiteAppearance.objects.exists())
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post(self.url, {'palette':'red'}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(self.url, {'palette':'red'}).status_code, 302)

    def test_font_mode_and_legacy_settings_are_effective(self):
        self.client.post(self.url, {'palette':'black','font':'classic','mode':'dark'})
        page = self.client.get(reverse('dashboard'))
        self.assertContains(page, 'data-font="classic"')
        self.assertContains(page, 'data-theme="dark"')
        self.client.post(self.url, {'palette':'inherit'})
        self.client.post(reverse('update_settings_theme'), {'theme':'dark'})
        self.assertContains(self.client.get(reverse('dashboard')), 'data-theme="dark"')
