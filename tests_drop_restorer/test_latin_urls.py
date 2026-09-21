import tempfile
import unittest
from pathlib import Path

from drop_restorer.core.models import CasinoPage, RestorationError, latin_url_path, route_for
from drop_restorer.core.pipeline import Pipeline
from drop_restorer.core.cleaner import parse_html
from drop_restorer.core.seo_routes import prepare, resolve, sitemap
from tests_drop_restorer.fixtures import FixtureArchive, request


class LatinUrlTests(unittest.TestCase):
    def test_latin_accents_are_removed_from_final_paths_only(self):
        for source in ('/nejlepsi-zahranicní-casino/', '/nejlepsi-zahranicn%C3%AD-casino/',
                       '/nejlepsi-zahranicni\u0301-casino/'):
            self.assertEqual(latin_url_path(source), '/nejlepsi-zahranicni-casino/')
        self.assertEqual(latin_url_path('/č-ř-š-ž-ý-ě-ů-ú-á-é-ó-ň-ť-ď-ł/'), '/c-r-s-z-y-e-u-u-a-e-o-n-t-d-l/')
        self.assertEqual(latin_url_path('/česky/?value=%C3%AD'), '/cesky/?value=%C3%AD')
        self.assertEqual(latin_url_path('/literal%253F/a%2Fb/'), '/literal%253F/a%2Fb/')
        self.assertEqual(route_for('https://example.com/článek/'), '/%C4%8Dl%C3%A1nek/')
        self.assertEqual(latin_url_path('/й/'), '/%D0%B9/')

    def test_colliding_casino_slugs_are_rejected_before_download(self):
        values = request()
        values.casino_pages = [CasinoPage('A', 'casíno'), CasinoPage('B', 'casino'), CasinoPage('C', 'third')]
        with self.assertRaises(RestorationError):
            values.validate()

    def test_casino_routes_have_a_stable_section_prefix(self):
        values = request()
        self.assertEqual([page.route for page in values.casino_pages], [
            '/casino/casino-rating/', '/casino/casino-bonuses/', '/casino/casino-reviews/'
        ])
        values.casino_pages[0] = CasinoPage('Nejlepší casino', 'nejlepsi-zahranicn%C3%AD-casino')
        self.assertEqual(values.casino_pages[0].route, '/casino/nejlepsi-zahranicni-casino/')

    def test_pipeline_creates_latin_routes_without_changing_titles(self):
        with tempfile.TemporaryDirectory() as folder:
            pipeline = Pipeline(Path(folder), client=FixtureArchive())
            values = request()
            values.casino_pages[0] = CasinoPage('Nejlepší zahraniční casino', 'nejlepsi-zahranicn%C3%AD-casino')
            pending = pipeline.run(values)
            build = pipeline.finish(pending, 'keep')
            casino = next(page for page in build.pages if page.casino)
            self.assertEqual(casino.route, '/casino/nejlepsi-zahranicni-casino/')
            self.assertEqual(casino.title, 'Nejlepší zahraniční casino')
            self.assertIn(casino.route, [a.get('href') for a in parse_html(build.pages[0].html).select('a')])
            self.assertTrue(parse_html(casino.html).select_one('link[rel=canonical]')['href'].endswith(casino.route))

            # Owner URL edits and restored paths use the same final rule;
            # legacy accented URLs keep a one-step redirect to the new route.
            old = build.pages[1].route
            build.seo_policy['url_overrides'] = {build.pages[1].key: '/článek/'}
            prepare(build)
            page = build.pages[1]
            self.assertEqual(page.route, '/clanek/')
            self.assertEqual(resolve(build, old), (301, '/clanek/'))
            self.assertIn(build.request.origin + '/clanek/', sitemap(build))
            self.assertTrue(parse_html(page.html).select_one('link[rel=canonical]')['href'].endswith('/clanek/'))
            self.assertIn('/clanek/', [a.get('href') for a in parse_html(build.pages[0].html).select('a')])
            build.seo_policy['url_overrides'] = {}
            build.seo_policy['original_routes'][page.key] = '/%C4%8Dl%C3%A1nek/'
            page.route = '/%C4%8Dl%C3%A1nek/'
            build.pages[0].html = build.pages[0].html.replace('/clanek/', '/článek/#detail')
            prepare(build)
            self.assertEqual(page.route, '/clanek/')
            for legacy in ('/článek/', '/%C4%8Dl%C3%A1nek/'):
                self.assertEqual(resolve(build, legacy), (301, '/clanek/'))
            self.assertIn('/clanek/#detail', [a.get('href') for a in parse_html(build.pages[0].html).select('a')])
            build.seo_policy['url_overrides'][casino.key] = '/clánek/'
            with self.assertRaises(RestorationError):
                prepare(build)


if __name__ == '__main__':
    unittest.main()
