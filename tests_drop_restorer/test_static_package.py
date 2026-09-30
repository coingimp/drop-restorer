from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from drop_restorer.core.models import Build, RestorationError
from drop_restorer.core.packager import _inline_static_page_css, _static_file_for_route, approve, package
from drop_restorer.core.pipeline import Pipeline
from tests_drop_restorer.fixtures import FixtureAgent, FixtureArchive, attest_fixture, request


class StaticPackageTests(unittest.TestCase):
    def test_inline_css_preserves_media_imports_and_embeds_local_resources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            assets = root / 'assets'
            (assets / 'css').mkdir(parents=True)
            (assets / 'img').mkdir()
            (assets / 'css' / 'base.css').write_text('.base { color: #123456; }', encoding='utf-8')
            (assets / 'css' / 'main.css').write_text(
                '@import "base.css"; @media (max-width: 700px) { .box { color: red; } } '
                '.logo { background-image: url("../img/mark.svg"); }', encoding='utf-8')
            (assets / 'img' / 'mark.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding='utf-8')

            result = _inline_static_page_css(
                '<!doctype html><html><head><link rel="stylesheet" href="/assets/css/main.css" media="screen"></head>'
                '<body><main>hello</main></body></html>', assets)

            self.assertNotIn('rel="stylesheet"', result)
            self.assertIn('data-drop-restorer-inline-css="/assets/css/main.css"', result)
            self.assertIn('.base { color: #123456; }', result)
            self.assertIn('@media (max-width: 700px)', result)
            self.assertIn('data:image/svg+xml;base64,', result)
            self.assertIn('media="screen"', result)

    def test_routes_map_to_static_index_files_and_reject_query_routes(self):
        self.assertEqual(_static_file_for_route('/'), 'index.html')
        self.assertEqual(_static_file_for_route('/about/'), 'about/index.html')
        self.assertEqual(_static_file_for_route('/casino/review'), 'casino/review/index.html')
        self.assertEqual(_static_file_for_route('/contact.html'), 'contact.html')
        for route in ('/about/?page=2', '/../outside/', '/robots.txt', '/assets/'):
            with self.subTest(route=route), self.assertRaises(RestorationError):
                _static_file_for_route(route)

    def test_static_redirects_reject_queries_and_missing_targets(self):
        from types import SimpleNamespace
        from drop_restorer.core.packager import _static_redirects
        build = SimpleNamespace(pages=[SimpleNamespace(route='/about/')], seo_policy={
            'url_style': 'slash', 'redirects': {'/old/': '/about/?source=archive'}
        })
        with self.assertRaisesRegex(RestorationError, 'параметрами'):
            _static_redirects(build)
        build.seo_policy['redirects'] = {'/old/': '/missing/'}
        with self.assertRaisesRegex(RestorationError, 'отсутствующую страницу'):
            _static_redirects(build)

    def test_static_package_contains_pages_and_assets_without_wordpress_files(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            client = FixtureArchive()
            pipeline = Pipeline(output, client=client, agent=FixtureAgent())
            pending = pipeline.suggest_metadata(pipeline.run(replace(request(), output_format='static_html')))
            build = pipeline.finish(pending, 'agent')
            attest_fixture(build)
            build = Pipeline(output, client=client, agent=FixtureAgent()).build_theme(build)
            approve(build, build.digest(), accept_findings=True)
            archive_path = package(build, build.root / 'restored-example-html.zip')

            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
                self.assertIn('index.html', names)
                self.assertIn('about/index.html', names)
                self.assertIn('casino/casino-rating/index.html', names)
                self.assertIn('assets/site-navigation.css', names)
                self.assertIn('404.html', names)
                self.assertIn('robots.txt', names)
                self.assertIn('sitemap.xml', names)
                self.assertIn('.htaccess', names)
                self.assertIn('_redirects', names)
                self.assertIn('README.md', names)
                self.assertFalse(any(name.endswith('.php') for name in names))
                self.assertNotIn('content.xml', names)
                self.assertNotIn('restoredexample-theme/style.css', names)
                self.assertTrue(archive.testzip() is None)

            self.assertEqual(build.request.output_format, 'static_html')
            self.assertEqual(build.approved_digest, build.digest())
            self.assertEqual((build.root / 'static-install-checks.json').is_file(), True)
            restored = Build.load(build.root)
            self.assertEqual(restored.request.output_format, 'static_html')
            self.assertEqual(restored.digest(), build.digest())

    def test_static_package_can_embed_all_stylesheets_in_html_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            client = FixtureArchive()
            pipeline = Pipeline(output, client=client, agent=FixtureAgent())
            pending = pipeline.suggest_metadata(pipeline.run(replace(
                request(), output_format='static_html', static_css_mode='inline')))
            build = pipeline.finish(pending, 'agent')
            attest_fixture(build)
            build = Pipeline(output, client=client, agent=FixtureAgent()).build_theme(build)
            approve(build, build.digest(), accept_findings=True)
            archive_path = package(build, build.root / 'restored-inline-html.zip')

            with zipfile.ZipFile(archive_path) as archive:
                for page_name in ('index.html', 'about/index.html', 'casino/casino-rating/index.html'):
                    with self.subTest(page=page_name):
                        source = archive.read(page_name).decode('utf-8')
                        self.assertNotIn('rel="stylesheet"', source)
                        self.assertIn('data-drop-restorer-inline-css=', source)
                        self.assertIn('<style', source)
                self.assertTrue(archive.testzip() is None)

            self.assertEqual(build.request.static_css_mode, 'inline')
            self.assertEqual(build.approved_digest, build.digest())
            receipt = json.loads((build.root / 'approval.json').read_text(encoding='utf-8'))
            checks = json.loads((build.root / 'static-install-checks.json').read_text(encoding='utf-8'))
            self.assertEqual(receipt['css_mode'], 'inline')
            self.assertIn('inline_css', checks['checks'])
            restored = Build.load(build.root)
            self.assertEqual(restored.request.static_css_mode, 'inline')
            self.assertEqual(restored.digest(), build.digest())


if __name__ == '__main__':
    unittest.main()
