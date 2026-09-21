import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from drop_restorer.core.cleaner import parse_html
from drop_restorer.core.indexation import indexation_blockers, open_indexation, robot_tags
from drop_restorer.core.seo_routes import indexable, pending_casino, sitemap, robots, write_staging
from drop_restorer.core.wordpress import write_site
from drop_restorer.core.checklist import inspect_site
from drop_restorer.core.pipeline import Pipeline
from tests_drop_restorer.fixtures import FixtureArchive, FixtureAgent, request


class IndexationTests(unittest.TestCase):
    def test_archived_bot_restrictions_and_wrappers_removed_content_kept(self):
        soup = parse_html('''<html><head><meta name="ROBOTS" content="none">
            <meta name="Googlebot" content="noindex"><meta name="YandexBot" content="nofollow">
            <meta property="robots" content="noindex"><meta name="googlebot-news" content="none">
            <meta http-equiv="X-Robots-Tag" content="noindex"><title>Original title</title>
            <meta name="description" content="Original description"></head><body>
            <!--noindex--><!--googleoff: index--><noindex><p>Keep original text</p></noindex><!--/noindex--><!--googleon: index-->
            <a href="/go/brand" rel="nofollow sponsored">Offer</a></body></html>''')
        open_indexation(soup)
        self.assertFalse(list(robot_tags(soup)))
        self.assertNotIn('noindex', str(soup))
        self.assertEqual(soup.p.get_text(), 'Keep original text')
        self.assertEqual(soup.title.get_text(), 'Original title')
        self.assertEqual(soup.a['rel'], ['nofollow', 'sponsored'])
        first = str(soup)
        self.assertEqual(first, str(open_indexation(soup)))
        self.assertFalse(indexation_blockers(soup))

    def test_generated_theme_has_runtime_indexation_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Pipeline(Path(directory), client=FixtureArchive(), agent=FixtureAgent())
            build = pipeline.finish(pipeline.run(request()), 'keep')
            write_site(build)
            seo = (build.root / 'theme' / 'seo.php').read_text(encoding='utf-8')
            self.assertIn("return array('index' => true, 'follow' => true);", seo)
            self.assertIn('dr_strip_public_robot_markup', seo)
            self.assertIn("header_remove('X-Robots-Tag')", seo)

    def test_legacy_policy_cannot_exclude_any_page(self):
        pages = [SimpleNamespace(route='/', casino=False), SimpleNamespace(route='/casino/', casino=True)]
        for old_policy in ('noindex', 'block', 'index'):
            build = SimpleNamespace(pages=pages, seo_policy={'casino_pending':old_policy},
                                    request=SimpleNamespace(origin='https://restored.example'))
            self.assertTrue(all(indexable(build, p) and not pending_casino(build, p) for p in pages))
            self.assertIn('https://restored.example/casino/', sitemap(build))
            self.assertNotIn('Disallow:', robots(build))

    def test_export_and_staging_repair_old_html_and_keep_honest_content_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline = Pipeline(Path(directory), client=FixtureArchive(), agent=FixtureAgent())
            pending = pipeline.suggest_metadata(pipeline.run(request()))
            build = pipeline.finish(pending, 'agent')
            for writer in (write_staging, write_site):
                build.seo_policy['casino_pending'] = 'noindex'
                for page in build.pages:
                    page.html = page.html.replace('</head>', '<meta name="googlebot" content="noindex"></head>')
                writer(build)
                self.assertEqual(build.seo_policy['casino_pending'], 'index')
                for page in build.pages:
                    self.assertFalse(list(robot_tags(parse_html(page.html))))
                    self.assertNotIn('noindex', (build.root/'pages'/(page.key+'.html')).read_text(encoding='utf-8'))
            site = json.loads((build.root/'theme/site.json').read_text(encoding='utf-8'))
            self.assertTrue(all('noindex' not in p['head'] for p in site['pages'].values()))
            report = inspect_site(build)
            self.assertTrue(all(p['indexable'] for p in report['pages']))
            self.assertEqual(next(r for r in report['items'] if r['key']=='noindex')['status'], 'pass')
            self.assertEqual(next(r for r in report['items'] if r['key']=='sitemap_urls')['status'], 'pass')
            self.assertEqual(next(r for r in report['items'] if r['key']=='content_present')['status'], 'fail')
            self.assertFalse(report['checks_passed'])
            self.assertFalse(report['owner_approval'])
            # If a later write bypasses the sanitizer, bot-specific bans remain audit failures.
            page = build.pages[0]
            path = build.root/'pages'/(page.key+'.html')
            path.write_text(page.html.replace('</head>', '<meta name="googlebot" content="none"></head>'), encoding='utf-8')
            report = inspect_site(build)
            self.assertEqual(next(r for r in report['items'] if r['key']=='noindex')['status'], 'fail')
            path.write_text(page.html.replace('</head>', '<!--googleoff: index--></head>'), encoding='utf-8')
            report = inspect_site(build)
            self.assertEqual(next(r for r in report['items'] if r['key']=='noindex')['status'], 'fail')


if __name__ == '__main__':
    unittest.main()
