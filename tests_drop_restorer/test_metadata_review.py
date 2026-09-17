import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from lxml import etree

from drop_restorer.core.cleaner import parse_html
from drop_restorer.core.models import Build, RestorationError
from drop_restorer.core.packager import approve, package
from drop_restorer.core.pipeline import Pipeline
from drop_restorer.core.review import can_apply, initialize_review, issues, page_label
from drop_restorer.core.wordpress import NS
from tests_drop_restorer.fixtures import FixtureAgent, FixtureArchive, request, export_fixture


class ReviewTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'var/drop-restorer/development-qa/unit-fixtures'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = FixtureArchive()
        self.pending = Pipeline(self.root, client=self.archive).run(request())

    def offline(self, **kwargs):
        client = Mock()
        client.get.side_effect = AssertionError('Resume must not request the archive')
        return Pipeline(self.root, client=client, **kwargs)

    def test_download_pauses_before_agent_casino_export_and_approval(self):
        agent = Mock()
        pending = Pipeline(self.root, client=FixtureArchive(), agent=agent).run(request())
        self.assertEqual(pending.status, 'metadata_review')
        self.assertEqual(len(pending.pages), 2)
        self.assertFalse((pending.root / 'content.xml').exists())
        self.assertTrue((pending.root / 'asset-manifest.json').exists())
        self.assertTrue((pending.root / 'metadata-review.json').exists())
        self.assertTrue(any(issues(pending).values()))
        agent.metadata.assert_not_called()
        agent.svg.assert_not_called()
        for action in (lambda: approve(pending, 'fake'), lambda: package(pending, pending.root / 'no.zip')):
            with self.assertRaises(RestorationError):
                action()

    def test_keep_preserves_short_duplicate_and_missing_metadata_in_html_and_wxr(self):
        for page in self.pending.pages:
            soup = parse_html(page.html)
            soup.title.string = '  Same short title  '
            page.html = str(soup)
        initialize_review(self.pending)
        build = self.offline().finish(self.pending, 'keep')
        self.assertEqual(build.status, 'site_review')
        self.assertFalse((build.root / 'content.xml').exists())
        self.assertEqual(build.metadata_decision, 'keep')
        for page in build.pages:
            soup = parse_html(page.html)
            self.assertEqual(len(soup.select('link[rel~="canonical"]')), 1)
            self.assertEqual(soup.select_one('link[rel~="canonical"]')['href'], build.request.origin + page.route)
            if not page.casino:
                self.assertEqual(soup.title.get_text(), '  Same short title  ')
                self.assertEqual(page.seo_title, '  Same short title  ')
                self.assertIsNone(soup.select_one('meta[name="description"]'))
            else:
                self.assertIsNone(soup.title)
                self.assertFalse(page.seo_title or page.description)
        export_fixture(build)
        tree = etree.parse(str(build.root / 'content.xml'))
        values = tree.xpath('//wp:postmeta[wp:meta_key="_dr_seo_title"]/wp:meta_value/text()', namespaces=NS)
        self.assertEqual(values, ['  Same short title  '] * 2)
        self.assertIsNone(build.approved_digest)

    def test_keep_preserves_description_even_outside_length_limits(self):
        page = self.pending.pages[0]
        soup = parse_html(page.html)
        soup.title.decompose()
        soup.head.append(soup.new_tag('meta', attrs={'name': 'Description', 'content': '  Short original.  '}))
        page.html = str(soup)
        initialize_review(self.pending)
        build = self.offline().finish(self.pending, 'keep')
        self.assertIsNone(parse_html(build.pages[0].html).title)
        self.assertEqual(build.pages[0].description, '  Short original.  ')

    def test_restart_and_continue_use_saved_pages_without_download(self):
        loaded = self.offline().open_saved(self.pending.root)
        self.assertEqual(loaded.metadata_review, self.pending.metadata_review)
        self.assertEqual(loaded.pages[0].html, self.pending.pages[0].html)
        ready = self.offline().finish(loaded, 'keep')
        self.assertEqual(len(ready.pages), 5)

    def test_agent_suggestions_do_not_change_originals_and_keep_discards_suggestions(self):
        originals = deepcopy(self.pending.metadata_review)
        suggested = self.offline(agent=FixtureAgent()).suggest_metadata(self.pending)
        self.assertTrue(can_apply(suggested))
        self.assertEqual(self.pending.metadata_review, originals)
        self.assertEqual(suggested.pages[0].html, self.pending.pages[0].html)
        built = self.offline().finish(suggested, 'keep')
        self.assertEqual(built.pages[0].seo_title, originals[built.pages[0].key]['original']['title'])

    def test_explicit_apply_uses_valid_agent_values(self):
        pending = self.offline(agent=FixtureAgent()).suggest_metadata(self.pending)
        built = self.offline().finish(pending, 'agent')
        for page in built.pages[:2]:
            self.assertEqual(page.seo_title, pending.metadata_review[page.key]['proposed']['title'])
            self.assertEqual(page.description, pending.metadata_review[page.key]['proposed']['description'])

    def test_agent_failure_or_duplicates_keep_review_available(self):
        for agent in (Mock(metadata=Mock(side_effect=RestorationError('Test provider unavailable'))),
                      Mock(metadata=Mock(return_value={'title': 'Repeated valid title for both source pages', 'description': 'Repeated source description. ' * 5}))):
            with self.subTest(agent=agent):
                result = self.offline(agent=agent).suggest_metadata(self.pending)
                self.assertEqual(result.status, 'metadata_review')
                self.assertFalse(can_apply(result))
                with self.assertRaises(RestorationError):
                    self.offline().finish(result, 'agent')
                self.assertEqual(self.offline().finish(result, 'keep').status, 'site_review')

    def test_bulk_generation_stops_after_shared_provider_failure(self):
        from drop_restorer.agents.client import AgentRequestError
        agent = Mock(metadata=Mock(side_effect=AgentRequestError('OpenRouter: HTTP 429. Лимит запросов.', 429)))
        result = self.offline(agent=agent).suggest_metadata(self.pending)
        self.assertEqual(agent.metadata.call_count, 1)
        self.assertIn('429', result.metadata_review[result.pages[0].key]['error'])
        self.assertIn('Не запрашивалось', result.metadata_review[result.pages[1].key]['error'])
        self.assertFalse(can_apply(result))
        self.assertEqual(self.offline().finish(result, 'keep').status, 'site_review')

    def test_failed_finish_retains_resumable_checkpoint_without_duplicate_casino(self):
        with patch('drop_restorer.core.pipeline.branding', side_effect=RestorationError('Test failure')):
            with self.assertRaises(RestorationError):
                self.offline().finish(self.pending, 'keep')
        saved = Build.load(self.pending.root)
        self.assertEqual(saved.status, 'metadata_review')
        self.assertEqual(len(saved.pages), 2)
        ready = self.offline().finish(saved, 'keep')
        self.assertEqual(len(ready.pages), 5)

    def test_legacy_failed_run_recovers_into_new_folder_using_cache(self):
        data = json.loads((self.pending.root / 'project.json').read_text(encoding='utf-8'))
        data.pop('status')
        data.pop('metadata_review')
        for page in data['pages']:
            page['html'] = ''
        path = self.pending.root / 'project.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        original = path.read_bytes()
        (self.pending.root / 'asset-manifest.json').unlink()
        restored = self.offline().open_saved(self.pending.root)
        self.assertNotEqual(restored.root, self.pending.root)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(restored.status, 'metadata_review')
        self.assertIn('/assets/', restored.pages[0].html)
        self.assertTrue((restored.root / 'asset-manifest.json').exists())

    def test_logo_heading_does_not_hide_page_title(self):
        soup = parse_html('<title>Study programmes</title><body><div id="content-holder"><h1 id="logo">School name</h1><div id="main-article"><p>Page content</p></div></div></body>')
        self.assertEqual(page_label(soup, 'example.com'), 'Study programmes')
        soup.select_one('#main-article').append(soup.new_tag('h1'))
        soup.select_one('#main-article h1').string = 'Computer networks'
        self.assertEqual(page_label(soup, 'example.com'), 'Computer networks')


if __name__ == '__main__':
    unittest.main()
