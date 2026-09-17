"""Regression: encoded Czech casino URLs must survive the WSGI boundary."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from drop_restorer.core.browser_checks import run
from drop_restorer.core.models import RestorationError
from drop_restorer.core.seo_routes import resolve
from drop_restorer.preview.server import PreviewServer


class BrowserRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'pages').mkdir()
        routes = ['/nejlepsi-zahranicn%C3%AD-casino/', '/%C4%8Desky/?id=1',
                  '/%C4%8Desky/?id=2', '/literal%253F/', '/encoded%3Fpath/']
        pages = []
        for number, route in enumerate(routes):
            key = str(number)
            (root / 'pages' / (key + '.html')).write_text('PAGE ' + key, encoding='utf-8')
            pages.append(SimpleNamespace(route=route, key=key, casino=False))
        self.build = SimpleNamespace(root=root, pages=pages, status='site_review', seo_policy={
            'redirects': {'/nejlepsi-zahranicn%C3%AD-casino': routes[0],
                          '/nejlepsi-zahranicn%C3%AD-casino/index.html': routes[0]}})
        self.server = PreviewServer(self.build)
        self.addCleanup(self.server.stop)
        self.session = requests.Session()
        self.session.trust_env = False
        self.addCleanup(self.session.close)

    def get(self, route):
        return self.session.get(self.server.origin + route, timeout=5, allow_redirects=False)

    def test_czech_url_unicode_uppercase_and_lowercase_encoding_return_same_page(self):
        for route in ('/nejlepsi-zahraniční-casino/', '/nejlepsi-zahranicní-casino/',
                      '/nejlepsi-zahranicn%C3%AD-casino/', '/nejlepsi-zahranicn%c3%ad-casino/'):
            # Different spelling remains a real missing URL.
            expected = 404 if 'zahraniční' in route else 200
            response = self.get(route)
            self.assertEqual(response.status_code, expected, route)
            if expected == 200:
                self.assertEqual(response.text, 'PAGE 0')
                self.assertEqual(resolve(self.build, route), (200, self.build.pages[0].route))

    def test_alternatives_redirect_once_and_unknown_urls_still_fail(self):
        for suffix in ('', '/index.html', '/?utm_source=test'):
            response = self.get('/nejlepsi-zahranicní-casino' + suffix)
            self.assertEqual(response.status_code, 301)
            self.assertEqual(response.headers['Location'], self.build.pages[0].route)
            self.assertEqual(self.get(response.headers['Location']).status_code, 200)
        self.assertEqual(self.get('/missing-č/').status_code, 404)

    def test_query_pages_remain_distinct_and_encoded_delimiters_are_decoded_once(self):
        for route, text in [('/česky/?id=1', 'PAGE 1'), ('/%C4%8Desky/?id=2', 'PAGE 2'),
                            ('/literal%253F/', 'PAGE 3'), ('/encoded%3Fpath/', 'PAGE 4')]:
            response = self.get(route)
            self.assertEqual(response.status_code, 200, route)
            self.assertEqual(response.text, text)
        self.assertEqual(self.get('/česky/?id=3').status_code, 404)
        self.assertEqual(self.get('/literal%3F/').status_code, 404)

    def test_browser_failure_exposes_page_and_device_instead_of_generic_message(self):
        def failed_process(*args, **kwargs):
            kwargs['stdout'].write('GPU warning\nERROR: Страница не загрузилась: /test/ (Mobile).\n')
            kwargs['stdout'].flush()
            return SimpleNamespace(poll=lambda: 1, returncode=1, wait=lambda **kw: 1)
        with patch('drop_restorer.core.browser_checks.subprocess.Popen', side_effect=failed_process):
            with self.assertRaisesRegex(RestorationError, '/test/ \\(Mobile\\)'):
                run(self.build, threading.Event())

    def test_browser_crash_without_report_exposes_exit_code(self):
        process = SimpleNamespace(poll=lambda: 42, returncode=42, wait=lambda **kw: 42)
        with patch('drop_restorer.core.browser_checks.subprocess.Popen', return_value=process):
            with self.assertRaisesRegex(RestorationError, 'кодом 42'):
                run(self.build, threading.Event())


if __name__ == '__main__':
    unittest.main()
