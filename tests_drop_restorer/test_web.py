import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import zipfile

import requests

from drop_restorer.core.models import Build
from drop_restorer.core.pipeline import Pipeline
from drop_restorer.web.server import create_app
from tests_drop_restorer.fixtures import FixtureArchive, completed_fixture, request, simulated_browser_fixture


class WebTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'var/drop-restorer/development-qa/web-fixtures'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.app = create_app(self.workspace)
        self.addCleanup(self.app.extensions['workspace'].close)
        self.state = self.app.extensions['workspace']
        self.client = self.app.test_client()
        self.origin = 'http://127.0.0.1:8780'
        self.headers = {'X-DropRestorer-Token': self.state.token, 'Origin': self.origin}
        self.addCleanup(patch.stopall)
        patch('drop_restorer.web.server.Workspace.visual',side_effect=simulated_browser_fixture).start()

    def get(self, path):
        return self.client.get(path, base_url=self.origin, headers=self.headers)

    def test_favicon_can_be_generated_without_agent_and_is_exported(self):
        build = completed_fixture(self.state.root)
        self.state.select(build)
        response = self.post('/api/action/branding', {'id':build.root.name,'kind':'favicon','topic':'cinema'})
        self.assertEqual(response.status_code,202)
        self.wait()
        report = self.get('/api/state').json['build']['favicon']
        self.assertEqual(report['topic'],'cinema')
        self.assertEqual(report['method'],'local_topic_vector')
        self.assertTrue((build.root/'theme/assets/branding/favicon.ico').is_file())
        site=json.loads((build.root/'theme/site.json').read_text(encoding='utf-8'))
        self.assertTrue(all('/assets/branding/favicon.ico' in page['head'] for page in site['pages'].values()))
        self.assertIsNone(self.state.build.approved_digest)
        bad=self.post('/api/action/branding', {'id':build.root.name,'kind':'favicon','topic':'unrecognized'})
        self.assertEqual(bad.status_code,400)

    def post(self, path, data):
        return self.client.post(path, base_url=self.origin, headers=self.headers, json=data)

    def wait(self, success=True):
        deadline = time.monotonic() + 15
        while self.state.job['busy'] and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(self.state.job['busy'])
        if success:
            self.assertIsNone(self.state.job['error'])

    def ready(self):
        build = completed_fixture(self.state.root)
        self.state.select(build)
        return build

    def test_casino_layout_remains_editable_after_theme_creation(self):
        build = self.ready()
        preview = self.preview(build)
        build.approved_digest = build.digest()
        build.owner_approvals['package'] = {'by': 'owner', 'digest': build.approved_digest}
        build.save()
        stale_archive = build.root / 'previous-theme.zip'
        stale_archive.write_bytes(b'old package')
        layout = {
            'content_width_px': 1360,
            'table_width_px': 1180,
            'row_height_px': 96,
            'cell_padding_px': 8,
            'cell_widths_px': {'logo': 80, 'bonus': 320, 'characteristics': 0, 'rating': 120, 'button': 160},
        }

        response = self.post('/api/action/layout', {'id': build.root.name, 'layout': layout})
        self.assertEqual(response.status_code, 202)
        self.wait()

        self.assertEqual(build.status, 'ready')
        self.assertEqual(build.seo_policy['casino_layout'], layout)
        self.assertIsNone(build.approved_digest)
        self.assertNotIn('package', build.owner_approvals)
        self.assertIsNone(self.state.preview)
        self.assertIsNone(self.state.preview_receipt)
        self.assertIsNone(self.state.archive)
        self.assertNotEqual(preview['digest'], build.digest())
        files = self.post('/api/files', {'id': build.root.name}).json
        self.assertNotIn('previous-theme.zip', [item['name'] for item in files])

        casino = next(page for page in build.pages if page.casino)
        regular = next(page for page in build.pages if not page.casino)
        self.assertIn('id="dr-casino-layout"', casino.html)
        self.assertIn('max-width: 1360px', casino.html)
        self.assertNotIn('id="dr-casino-layout"', regular.html)
        manifest = json.loads((build.root / 'theme' / 'site.json').read_text(encoding='utf-8'))
        self.assertIn('max-width: 1360px', manifest['pages'][casino.key]['head'])
        self.assertNotIn('dr-casino-layout', manifest['pages'][regular.key]['head'])

    def preview(self, build):
        response = self.post('/api/preview', {'id': build.root.name})
        self.assertEqual(response.status_code, 200)
        return response.json

    def test_local_host_origin_and_token_required(self):
        self.assertEqual(self.client.get('/api/state', base_url=self.origin).status_code, 403)
        self.assertEqual(self.client.get('/', base_url='http://evil.example:8780').status_code, 403)
        headers = dict(self.headers, Origin='http://evil.example')
        self.assertEqual(self.client.post('/api/cancel', base_url=self.origin, headers=headers, json={}).status_code, 403)
        response = self.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
        self.assertNotIn('Access-Control-Allow-Origin', response.headers)

    def test_panel_token_survives_service_restart(self):
        token = self.state.token
        token_file = self.state.root / 'web-token.json'
        self.assertTrue(token_file.is_file())
        self.state.close()
        restarted = create_app(self.workspace)
        self.addCleanup(restarted.extensions['workspace'].close)
        self.assertEqual(restarted.extensions['workspace'].token, token)
        script = (Path(__file__).resolve().parents[1] / 'drop_restorer/web/static/app.js').read_text(encoding='utf-8')
        self.assertIn('response.status === 403', script)
        self.assertIn('Связь с локальным инструментом потеряна', script)

    def test_path_traversal_and_unknown_download_are_rejected(self):
        self.assertEqual(self.post('/api/projects/open', {'id':'../../backups'}).status_code, 400)
        self.assertEqual(self.get('/files/missing').status_code, 404)

    def test_restoration_pauses_then_keep_finishes_without_automatic_zip(self):
        real_pipeline = Pipeline
        def fixture_pipeline(output, progress, log, cancel, agent):
            return real_pipeline(output, progress, log, cancel, agent, client=FixtureArchive())
        from dataclasses import asdict
        with patch('drop_restorer.web.server.Pipeline', side_effect=fixture_pipeline):
            self.assertEqual(self.post('/api/restore', asdict(request())).status_code, 202)
            self.wait()
            build = self.state.build
            self.assertEqual(build.status, 'metadata_review')
            self.assertFalse((build.root / 'content.xml').exists())
            original = self.get('/api/state').json['build']['metadata_review']
            self.assertEqual(self.post('/api/action/continue', {'id':build.root.name,'decision':'keep'}).status_code, 202)
            self.wait()
        self.assertEqual(self.state.build.status, 'site_review')
        self.assertFalse((self.state.build.root / 'content.xml').exists())
        self.assertEqual(len(self.state.build.pages), 5)
        self.assertEqual(self.state.build.metadata_review, original)
        self.assertEqual(self.state.build.metadata_decision, 'keep')
        self.assertFalse(list(build.root.glob('*.zip')))
        self.assertIsNone(self.state.build.approved_digest)

    def test_preview_is_separate_origin_and_navigation_loads(self):
        build = self.ready()
        info = self.preview(build)
        self.assertNotEqual(info['origin'], self.origin)
        self.assertEqual(info['digest'], build.digest())
        with requests.Session() as session:
            session.trust_env = False
            for page in build.pages:
                response = session.get(info['origin'] + page.route, timeout=5)
                self.assertEqual(response.status_code, 200)
                self.assertIn('id="dr-primary-menu"', response.text)
                self.assertIn('/__drop_restorer_preview_bridge.js', response.text)
                self.assertNotIn('/__drop_restorer_preview_bridge.js', (build.root / 'pages' / (page.key + '.html')).read_text(encoding='utf-8'))
            self.assertEqual(session.get(info['origin'] + '/casino/casino-rating/', timeout=5).status_code, 200)
            self.assertEqual(session.get(info['origin'] + '/casino-rating/', timeout=5).status_code, 404)
            self.assertIn(self.origin, session.get(info['origin'] + '/__drop_restorer_preview_bridge.js', timeout=5).text)
        self.assertEqual(self.get('/casino-rating/').status_code, 404)

    def test_package_requires_explicit_preview_receipt_and_current_digest(self):
        build = self.ready()
        values = {'id':build.root.name,'confirmed':True}
        self.assertEqual(self.post('/api/package', values).status_code, 400)
        info = self.preview(build)
        values.update(receipt=info['receipt'], digest=info['digest'])
        self.assertEqual(self.post('/api/package', dict(values, confirmed=False)).status_code, 400)
        path = build.root / 'theme' / 'style.css'
        path.write_text(path.read_text(encoding='utf-8') + '\n/* newer revision */', encoding='utf-8')
        self.assertEqual(self.post('/api/package', values).status_code, 202)
        self.wait(False)
        self.assertIn('изменился', self.state.job['error'])
        self.assertFalse(list(build.root.glob('*.zip')))

    def test_approved_fixture_zip_download_contains_report(self):
        build = self.ready()
        info = self.preview(build)
        self.post('/api/package', {'id':build.root.name, 'confirmed':True, **info})
        self.wait()
        archive = self.state.archive
        self.assertTrue(archive['name'].startswith('restoredexample-theme-'), archive['name'])
        files = self.post('/api/files', {'id': build.root.name}).json
        self.assertIn(archive['name'], [item['name'] for item in files])
        self.assertIn(archive['bundle']['name'], [item['name'] for item in files])
        response = self.get(archive['url'])
        self.assertEqual(response.status_code, 200)
        response.close()
        with zipfile.ZipFile(build.root / archive['name']) as zipped:
            self.assertIn('restoredexample-theme/style.css', zipped.namelist())
            self.assertIn('restoredexample-theme/404.php', zipped.namelist())
            self.assertNotIn('content.xml', zipped.namelist())
        with zipfile.ZipFile(build.root / archive['bundle']['name']) as zipped:
            self.assertIn('checks.json', zipped.namelist())
            self.assertIn('seo-checklist.json', zipped.namelist())
            self.assertIn('content.xml', zipped.namelist())
        self.assertEqual(self.state.build.approved_digest, info['digest'])

    def test_audit_failure_blocks_web_packaging(self):
        build = self.ready()
        target = build.root / 'pages' / (build.pages[0].key + '.html')
        target.write_text(target.read_text(encoding='utf-8').replace('id="dr-primary-menu"', 'id="broken-menu"'), encoding='utf-8')
        info = self.preview(build)
        self.post('/api/package', {'id':build.root.name, 'confirmed':True, **info})
        self.wait(False)
        self.assertTrue('Проверки не пройдены' in self.state.job['error'] or 'Чек-лист не завершён' in self.state.job['error'])
        self.assertFalse(list(build.root.glob('*.zip')))

    def test_agent_key_not_persisted_returned_or_called_on_connect(self):
        with patch('drop_restorer.agents.client.requests.post') as network:
            response = self.post('/api/agents', {'action':'connect','endpoint':'https://provider.example/v1','model':'test-model','api_key':'local-secret-do-not-leak'})
            self.assertEqual(response.status_code, 200)
            network.assert_not_called()
        self.assertNotIn('local-secret-do-not-leak', self.get('/api/state').get_data(as_text=True))
        self.assertNotIn('local-secret-do-not-leak', (self.state.root / 'settings.json').read_text(encoding='utf-8'))
        self.state.log('test local-secret-do-not-leak')
        self.assertNotIn('local-secret-do-not-leak', (self.state.root / 'web.log').read_text(encoding='utf-8'))

    def test_openrouter_requires_nonempty_key_before_connecting(self):
        with patch('drop_restorer.agents.client.requests.post') as network:
            response = self.post('/api/agents', {'action': 'connect', 'endpoint': 'https://openrouter.ai/api/v1',
                'model': 'google/gemma-4-26b-a4b-it:free', 'api_key': '  '})
        self.assertEqual(response.status_code, 400)
        self.assertIn('API-ключ', response.json['error'])
        network.assert_not_called()
        self.assertFalse(self.state.agents)

    def test_metadata_trial_uses_only_selected_page_without_changing_site_at_any_review_stage(self):
        from tests_drop_restorer.fixtures import ABOUT_TEXT, HOME_TEXT
        from drop_restorer.core.usage import summary
        self.post('/api/agents', {'action': 'connect', 'endpoint': 'https://openrouter.ai/api/v1',
            'model': 'google/gemma-4-26b-a4b-it:free', 'api_key': 'synthetic-trial-secret'})
        for stage in ('metadata_review', 'site_review', 'ready'):
            with self.subTest(stage=stage):
                build = Pipeline(self.state.root, client=FixtureArchive()).run(request())
                if stage != 'metadata_review':
                    build = Pipeline(self.state.root, client=FixtureArchive()).finish(build, 'keep')
                if stage == 'ready':
                    from tests_drop_restorer.fixtures import export_fixture
                    export_fixture(build)
                self.state.select(build)
                before = (build.root / 'project.json').read_bytes()
                original_html = [p.html for p in build.pages]
                page = next(p for p in build.pages if p.route == '/about/')
                values = {'title': 'About the example organisation and its information',
                          'description': ABOUT_TEXT[:170].rstrip()}
                response = Mock(ok=True, is_redirect=False, headers={})
                response.json.return_value = {'choices': [{'message': {'content': json.dumps(values)}}],
                                              'usage': {'cost': 0, 'prompt_tokens': 220, 'completion_tokens': 50}}
                with patch('drop_restorer.agents.client.requests.post', return_value=response) as network:
                    result = self.post('/api/agents/metadata-sample', {'id': build.root.name, 'key': page.key})
                    self.assertEqual(result.status_code, 202)
                    self.wait()
                network.assert_called_once()
                payload = network.call_args.kwargs['json']
                self.assertIn(ABOUT_TEXT, payload['messages'][1]['content'])
                self.assertNotIn(HOME_TEXT, payload['messages'][1]['content'])
                self.assertEqual(payload['response_format'], {'type': 'json_object'})
                self.assertEqual(payload['reasoning'], {'effort': 'none'})
                self.assertEqual((build.root / 'project.json').read_bytes(), before)
                self.assertEqual([p.html for p in build.pages], original_html)
                self.assertEqual(self.state.build.status, stage)
                sample = self.get('/api/state').json['build']['metadata_sample']
                self.assertEqual(sample['proposed'], values)
                self.assertFalse(sample['applied'] or sample['stale'])
                self.assertNotIn('synthetic-trial-secret', json.dumps(sample))
                self.assertEqual(summary(build.root)['events'][-1]['operation'], 'metadata_sample')
                self.assertEqual(summary(build.root)['cost_usd'], 0)
                other = create_app(self.workspace).extensions['workspace']
                self.addCleanup(other.close)
                self.assertEqual(other.public()['build']['metadata_sample'], sample)
                page.html += '<p>Changed after sample</p>'
                self.assertTrue(self.get('/api/state').json['build']['metadata_sample']['stale'])

    def test_metadata_trial_rejects_casino_unknown_page_and_stale_project(self):
        build = self.ready()
        casino = next(p for p in build.pages if p.casino)
        with patch('drop_restorer.agents.client.requests.post') as network:
            for values in ({'id': 'wrong', 'key': build.pages[0].key},
                           {'id': build.root.name, 'key': casino.key},
                           {'id': build.root.name, 'key': 'unknown'},
                           {'id': build.root.name, 'key': build.pages[0].key}):
                self.assertEqual(self.post('/api/agents/metadata-sample', values).status_code, 400)
        network.assert_not_called()
        self.assertFalse((build.root / 'metadata-sample.json').exists())

    def test_metadata_trial_failure_is_saved_without_fabricated_result_or_cost(self):
        build = self.ready()
        before = (build.root / 'project.json').read_bytes()
        self.post('/api/agents', {'action': 'connect', 'endpoint': 'https://openrouter.ai/api/v1',
            'model': 'google/gemma-4-26b-a4b-it:free', 'api_key': 'synthetic-trial-secret'})
        response = Mock(ok=False, is_redirect=False, status_code=429, headers={'Retry-After': '60'})
        response.json.return_value = {'error': {'message': 'temporarily limited synthetic-trial-secret'}}
        with patch('drop_restorer.agents.client.requests.post', return_value=response) as network:
            self.post('/api/agents/metadata-sample', {'id': build.root.name, 'key': build.pages[0].key})
            self.wait(False)
        network.assert_called_once()
        state = self.get('/api/state').json
        sample = state['build']['metadata_sample']
        self.assertEqual(sample['status'], 'failed')
        self.assertNotIn('proposed', sample)
        self.assertIn('60 с.', sample['error'])
        self.assertNotIn('synthetic-trial-secret', json.dumps(state))
        self.assertIsNone(state['build']['usage']['cost_usd'])
        self.assertEqual(state['build']['report']['usage'], state['build']['usage'])
        self.assertEqual((build.root / 'project.json').read_bytes(), before)

    def test_pending_checkpoint_reopens_after_server_restart(self):
        build = Pipeline(self.state.root, client=FixtureArchive()).run(request())
        self.state.select(build)
        other = create_app(self.workspace).extensions['workspace']
        self.addCleanup(other.close)
        self.assertEqual(other.build.root, build.root)
        self.assertEqual(other.build.status, 'metadata_review')
        self.assertEqual(other.build.metadata_review, build.metadata_review)

    def test_busy_job_rejects_concurrent_mutation_and_can_cancel(self):
        build = self.ready()
        release = threading.Event()
        self.addCleanup(release.set)
        with self.state.lock:
            self.state.start('test', lambda: release.wait(5))
        self.assertEqual(self.post('/api/projects/open', {'id':build.root.name}).status_code, 400)
        self.assertEqual(self.post('/api/cancel', {}).status_code, 200)
        self.assertTrue(self.state.cancel.is_set())
        release.set()
        self.wait()

    def test_html_edit_preserves_owner_metadata_and_invalidates_preview(self):
        build = self.ready()
        info = self.preview(build)
        page = build.pages[0]
        values = self.get('/api/page/' + build.root.name + '/' + page.key).json
        old_title = page.seo_title
        edited = values['html'].replace('Archive example footer', 'Edited footer')
        self.assertEqual(self.post('/api/page', {'id':build.root.name, 'key':page.key, 'digest':values['digest'], 'html':edited}).status_code, 202)
        self.wait()
        self.assertIsNone(self.state.preview)
        self.assertEqual(self.state.build.pages[0].seo_title, old_title)
        self.assertIn('Edited footer', (build.root / 'pages' / (page.key + '.html')).read_text(encoding='utf-8'))
        self.assertTrue(self.state.report['passed'])
        self.assertEqual(self.post('/api/package', {'id':build.root.name, 'confirmed':True, **info}).status_code, 400)
        self.assertEqual(self.get('/api/page/' + build.root.name + '/' + build.pages[-1].key).status_code, 400)

    def test_legacy_project_without_review_can_be_inspected(self):
        build = self.ready()
        build.metadata_review = {}
        build.save()
        response = self.get('/api/state')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json['build']['can_apply'])

    def test_seo_prepare_is_separate_copy_and_theme_gate_is_enforced(self):
        original=self.ready();before=(original.root/'project.json').read_bytes()
        self.assertEqual(self.post('/api/checklist/prepare',{'id':original.root.name}).status_code,202)
        self.wait();build=self.state.build
        self.assertNotEqual(build.root,original.root)
        self.assertEqual((original.root/'project.json').read_bytes(),before)
        self.assertEqual(build.status,'site_review')
        self.assertFalse((build.root/'content.xml').exists())
        self.assertEqual(len(self.get('/api/state').json['build']['checklist']['items']),58)
        self.post('/api/checklist/theme',{'id':build.root.name});self.wait(False)
        self.assertIn('Чек-лист не завершён',self.state.job['error'])
        self.assertFalse((build.root/'content.xml').exists())

    def test_widget_failure_remains_visible_and_blocks_release(self):
        from drop_restorer.core.models import RestorationError
        build=self.ready()
        with patch('drop_restorer.core.affiliates.fetch_table',side_effect=RestorationError('Provider HTTP 520')):
            self.post('/api/checklist/widget',{'id':build.root.name,'table_id':88});self.wait()
        report=self.get('/api/state').json['build']['checklist']
        self.assertEqual(len(report['affiliates']['errors']),3)
        self.assertFalse(report['can_build'])
        self.assertEqual(self.state.build.status,'site_review')
        for row in report['items']:
            if row['key'].startswith('affiliate_'):
                self.assertEqual(row['status'],'fail')

    def test_owner_can_continue_and_package_without_agent_or_row_confirmations(self):
        real_pipeline=Pipeline
        def fixture_pipeline(output,progress,log,cancel,agent):
            self.assertIsNone(agent)
            return real_pipeline(output,progress,log,cancel,agent,client=FixtureArchive())
        from dataclasses import asdict
        with patch('drop_restorer.web.server.Pipeline',side_effect=fixture_pipeline):
            self.post('/api/restore',asdict(request()));self.wait()
            build=self.state.build
            metadata_preview=self.preview(build)
            self.assertEqual(metadata_preview['stage'],'metadata_review')
            self.post('/api/action/continue',{'id':build.root.name,'decision':'keep'});self.wait()
            build=self.state.build
            report=self.state.seo_report
            self.assertFalse(report['can_build'])
            preview=self.preview(build)
            self.assertEqual(preview['stage'],'site_review')
            self.assertEqual(preview['fingerprint'],report['fingerprint'])
            self.post('/api/checklist/theme',{'id':build.root.name,'owner_approved':True,'fingerprint':preview['fingerprint']})
            self.wait()
        self.assertEqual(self.state.build.status,'ready')
        self.assertFalse(list(build.root.glob('*.zip')))
        self.assertEqual(self.state.build.seo_policy.get('decisions'),{})
        info=self.preview(self.state.build)
        self.post('/api/package',{'id':build.root.name,'confirmed':True,'accept_findings':True,**info})
        self.wait()
        with zipfile.ZipFile(build.root/self.state.archive['bundle']['name']) as archive:
            decisions=json.loads(archive.read('owner-approvals.json'))
            seo=json.loads(archive.read('seo-checklist.json'))
        self.assertIn('site_review',decisions)
        self.assertIn('package',decisions)
        self.assertGreater(seo['counts'].get('review',0)+seo['counts'].get('fail',0),0)
        self.assertFalse(seo['checks_passed'])
        self.assertIsNone(self.state.active_agent)
        other=create_app(self.workspace).extensions['workspace']
        self.addCleanup(other.close)
        self.assertEqual(other.build.owner_approvals,self.state.build.owner_approvals)

    def test_owner_continue_rejects_missing_fingerprint_and_non_boolean_choice(self):
        build=self.ready()
        # This case starts without the synthetic owner's earlier fixture approval.
        build.owner_approvals.clear()
        from drop_restorer.core.seo_routes import prepare
        prepare(build);self.state.select(build)
        response=self.post('/api/checklist/theme',{'id':build.root.name,'owner_approved':'true'})
        self.assertEqual(response.status_code,400)
        self.post('/api/checklist/theme',{'id':build.root.name,'owner_approved':True})
        self.wait(False)
        self.assertIn('изменился',self.state.job['error'])
        self.assertFalse(build.owner_approvals)

    def test_installable_package_downloads_survive_restart_and_repackage_requires_approval(self):
        build = self.ready()
        self.assertEqual(self.post('/api/repackage', {'id': build.root.name}).status_code, 400)
        from drop_restorer.core.packager import approve
        approve(build, build.digest())
        before = (build.root/'project.json').read_bytes()
        self.assertEqual(self.post('/api/repackage', {'id': build.root.name}).status_code, 202)
        self.wait()
        self.assertEqual((build.root/'project.json').read_bytes(), before)
        archive = self.state.archive
        self.assertEqual(archive['kind'], 'wordpress_theme')
        other = create_app(self.workspace).extensions['workspace']
        self.addCleanup(other.close)
        self.assertEqual(other.archive['name'], archive['name'])
        self.assertEqual(other.archive['content']['name'], archive['content']['name'])
        self.assertEqual(self.get(archive['content']['url']).data, (build.root/'content.xml').read_bytes())


if __name__ == '__main__':
    unittest.main()
