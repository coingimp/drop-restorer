import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from drop_restorer.agents.client import AgentClient, AgentConfig
from drop_restorer.core.audit import audit
from drop_restorer.core.cleaner import clean, parse_html
from drop_restorer.core.models import Build, RestorationError
from drop_restorer.core.packager import approve, package
from drop_restorer.core.pipeline import Pipeline
from drop_restorer.core import usage
from tests_drop_restorer.fixtures import FixtureArchive, request, completed_fixture, export_fixture


class ChecksTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'var/drop-restorer/development-qa/unit-fixtures'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(base.resolve()))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_local_tracker_bytes_and_inline_facebook_removed_before_export(self):
        client = FixtureArchive()
        body, mime = client.resources['/']
        body = body.replace(b'</head>', b'''<script src="/js/helper.js"></script><script>var js={};js.src="//connect.facebook.net/cs_CZ/all.js";</script></head>''')
        client.resources['/'] = (body, mime)
        client.resources['/js/helper.js'] = (b"ga('send', 'pageview');", 'text/javascript')
        pending = Pipeline(self.root, client=client).run(request())
        build = Pipeline(self.root, client=client).finish(pending, 'keep')
        export_fixture(build)
        report = audit(build)
        self.assertTrue(report['passed'], report['findings'])
        self.assertGreaterEqual(len([row for row in report['removed'] if row['kind'] == 'script']), 2)
        for path in (build.root / 'theme').rglob('*.js'):
            self.assertNotIn("ga('send'", path.read_text())
        self.assertNotIn('connect.facebook.net', (build.root / 'content.xml').read_text(encoding='utf-8'))
        self.assertEqual(report['usage']['cost_usd'], 0)

    def test_old_cached_tracker_pruned_and_original_build_preserved(self):
        old = completed_fixture(self.root)
        local = '/assets/js/synthetic-cached-tracker.js'
        path = old.root / 'theme' / local.lstrip('/')
        path.write_text("ga('create', 'test');", encoding='utf-8')
        manifest = old.root / 'asset-manifest.json'
        records = json.loads(manifest.read_text())
        records.append({'source': 'https://example.com/js/tracker.js', 'snapshot': '20200101000000', 'local': local})
        manifest.write_text(json.dumps(records))
        client = FixtureArchive()
        client.get = Mock(side_effect=AssertionError('Unexpected network request'))
        pending = Pipeline(self.root, client=client).open_saved(old.root)
        new = Pipeline(self.root, client=client).finish(pending, 'keep')
        export_fixture(new)
        self.assertTrue(path.is_file())
        self.assertFalse((new.root / 'theme' / local.lstrip('/')).exists())
        self.assertTrue(audit(new)['passed'])
        client.get.assert_not_called()

    def test_foreign_blocks_removed_selected_language_preserved(self):
        soup = parse_html('<html lang="de"><body><p lang="cs">Ahoj</p><p lang="en">English version</p><div class="language-switcher">DE</div></body></html>')
        clean(soup, 'cs-CZ')
        self.assertIn('Ahoj', soup.get_text())
        self.assertNotIn('English', soup.get_text())
        self.assertEqual(soup.html['lang'], 'cs-CZ')

    def test_audit_reads_disk_and_blocks_packaging_even_with_current_digest(self):
        build = completed_fixture(self.root)
        path = build.root / 'pages' / (build.pages[0].key + '.html')
        path.write_text(path.read_text(encoding='utf-8').replace('</body>', '<a href="https://outside.example/">Outside</a><span lang="de">DE</span><div class="ads">Ad</div></body>'), encoding='utf-8')
        report = audit(build)
        self.assertFalse(report['passed'])
        self.assertTrue({'links', 'languages', 'junk', 'export'} <= {row['category'] for row in report['findings']})
        with self.assertRaises(RestorationError):
            approve(build, build.digest())
        build.approved_digest = build.digest()
        with self.assertRaises(RestorationError):
            package(build, self.root / 'must-not-exist.zip')
        self.assertFalse((self.root / 'must-not-exist.zip').exists())

    def test_export_only_tampering_and_unreferenced_tracker_detected(self):
        build = completed_fixture(self.root)
        path = build.root / 'content.xml'
        path.write_text(path.read_text(encoding='utf-8').replace('<main>', '<main><a href="https://extra.example/">Extra</a>', 1), encoding='utf-8')
        (build.root / 'theme/assets/forgotten.js').write_text("ga('send', 'pageview')")
        report = audit(build)
        self.assertFalse(report['passed'])
        self.assertTrue({'scripts', 'export'} <= {row['category'] for row in report['findings']})

    def test_keep_preserves_owner_choice_but_missing_metadata_blocks_new_seo_gate(self):
        client = FixtureArchive()
        pending = Pipeline(self.root, client=client).run(request())
        build = Pipeline(self.root, client=client).finish(pending, 'keep')
        export_fixture(build)
        report = audit(build)
        self.assertTrue(report['passed'], report['findings'])
        self.assertGreater(report['warnings'], 0)
        digest = build.digest()
        audit(build)
        self.assertEqual(digest, build.digest())
        with self.assertRaises(RestorationError):
            approve(build, digest)
        self.assertFalse((self.root / 'synthetic-checks.zip').exists())

    def test_library_documentation_url_is_not_live_tracking(self):
        build = completed_fixture(self.root)
        (build.root / 'theme/assets/license.js').write_text('/* See https://jquery.com/ for docs */\nvar projectUrl="https://example.com";')
        self.assertTrue(audit(build)['passed'])

    def test_windows_line_endings_match_wordpress_export(self):
        client = FixtureArchive()
        raw, mime = client.resources['/']
        client.resources['/'] = (raw.replace(b'</p>', b'\r\nSecond line\r\n</p>'), mime)
        pending = Pipeline(self.root, client=client).run(request())
        build = Pipeline(self.root, client=client).finish(pending, 'keep')
        export_fixture(build)
        self.assertTrue(audit(build)['passed'])


class UsageTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'var/drop-restorer/development-qa/unit-fixtures'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(base.resolve()))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agent = AgentClient(AgentConfig('https://openrouter.ai/api/v1', 'test-model', 'synthetic-secret'))
        self.agent.usage_path = usage.initialize(self.root)

    def response(self, content='OK', **usage_data):
        response = Mock(ok=True, is_redirect=False)
        response.json.return_value = {'choices': [{'message': {'content': content}}], 'usage': usage_data}
        return response

    def test_provider_cost_persists_across_reload_without_secrets(self):
        with patch('drop_restorer.agents.client.requests.post', return_value=self.response(cost=0.002, prompt_tokens=10, completion_tokens=4)):
            self.agent.test()
        result = usage.summary(self.root)
        self.assertEqual(result['calls'], 1)
        self.assertEqual(result['cost_usd'], 0.002)
        self.assertEqual(result['prompt_tokens'], 10)
        self.assertNotIn('synthetic-secret', self.agent.usage_path.read_text())
        self.assertNotIn('Reply with', self.agent.usage_path.read_text())

    def test_timeout_and_missing_usage_do_not_become_zero(self):
        with patch('drop_restorer.agents.client.requests.post', side_effect=requests.Timeout), self.assertRaises(RestorationError):
            self.agent.test()
        with patch('drop_restorer.agents.client.requests.post', return_value=self.response()):
            self.agent.test()
        report = usage.summary(self.root)
        self.assertEqual(report['calls'], 2)
        self.assertIsNone(report['cost_usd'])
        self.assertEqual(report['unknown_cost_calls'], 2)

    def test_billed_but_unusable_metadata_still_counted(self):
        with patch('drop_restorer.agents.client.requests.post', return_value=self.response(content='not JSON', cost=0.01)), self.assertRaises(RestorationError):
            self.agent.metadata('Title', 'Content', 'en')
        self.assertEqual(usage.summary(self.root)['cost_usd'], 0.01)

    def test_connection_tests_separate_from_run_and_context_restored(self):
        run = self.root / 'run'
        run.mkdir()
        usage.initialize(run)
        previous = self.agent.usage_path
        with patch('drop_restorer.agents.client.requests.post', return_value=self.response(cost=0.01)):
            self.agent.test()
            with usage.metered(self.agent, run):
                self.agent.complete('metadata', operation='metadata')
        self.assertEqual(self.agent.usage_path, previous)
        self.assertEqual(usage.summary(run)['calls'], 1)
        self.assertEqual(usage.summary(self.root)['calls'], 1)

    def test_legacy_and_inflight_requests_have_unknown_total(self):
        legacy = self.root / 'legacy'
        legacy.mkdir()
        self.assertIsNone(usage.summary(legacy)['cost_usd'])
        usage.initialize(legacy, complete_history=False)
        self.assertIsNone(usage.summary(legacy)['cost_usd'])
        usage.start(self.agent.usage_path, 'openrouter.ai', 'test', 'metadata')
        self.assertIsNone(usage.summary(self.root)['cost_usd'])

    def test_rate_limit_and_auth_errors_are_actionable_without_provider_body(self):
        from drop_restorer.agents.client import AgentRequestError
        for code, marker in ((401, 'Ключ не принят'), (429, 'лимит')):
            with self.subTest(code=code):
                response = Mock(ok=False, is_redirect=False, status_code=code,
                                headers={'Retry-After': '30'})
                response.json.return_value = {'error': {'message': 'free-models-per-day synthetic-secret'}}
                with patch('drop_restorer.agents.client.requests.post', return_value=response) as network:
                    with self.assertRaises(AgentRequestError) as caught:
                        self.agent.test()
                self.assertEqual(network.call_count, 1)
                self.assertEqual(caught.exception.status_code, code)
                self.assertIn(marker, str(caught.exception))
                self.assertNotIn('synthetic-secret', str(caught.exception))
                if code == 429:
                    self.assertIn('суточный', str(caught.exception))
                    self.assertIn('30 с.', str(caught.exception))
        self.assertEqual(usage.summary(self.root)['calls'], 2)
        self.assertIsNone(usage.summary(self.root)['cost_usd'])

    def test_success_status_with_embedded_error_is_not_a_successful_generation(self):
        response = self.response()
        response.json.return_value = {'error': {'code': 429, 'message': 'limited'}}
        with patch('drop_restorer.agents.client.requests.post', return_value=response):
            with self.assertRaisesRegex(RestorationError, '429'):
                self.agent.metadata('Title', 'Text', 'en')
        self.assertEqual(usage.summary(self.root)['events'][0]['status'], 'failed')

    def test_metadata_requires_two_text_fields_and_keeps_billed_usage(self):
        for data in ({'title': ['bad'], 'description': 'text'}, {'title': 'only title'}, []):
            with self.subTest(data=data):
                with patch('drop_restorer.agents.client.requests.post', return_value=self.response(content=json.dumps(data), cost=0.01)):
                    with self.assertRaisesRegex(RestorationError, 'title и description'):
                        self.agent.metadata('Title', 'Text', 'en')
        self.assertEqual(usage.summary(self.root)['cost_usd'], 0.03)
