import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup
from lxml import etree
import requests
from unittest.mock import Mock, patch

from drop_restorer.core.models import RestorationError, Snapshot, CasinoPage, RestoreRequest, route_for
from drop_restorer.core.pipeline import Pipeline
from drop_restorer.core.packager import approve, package
from drop_restorer.core.theme_identity import theme_identity
from drop_restorer.core.wordpress import NS, write_site
from drop_restorer.core.downloader import ArchiveClient
from drop_restorer.core.assets import original_url
from drop_restorer.agents.client import AgentClient, AgentConfig
from drop_restorer.preview.server import PreviewServer
from tests_drop_restorer.fixtures import FixtureArchive, STAMP, request, completed_fixture


class InputTests(unittest.TestCase):
    def test_theme_identity_uses_compact_domain_and_stable_color(self):
        example = theme_identity('https://www.example.com')
        wifi = theme_identity('https://wifi-in.cz')
        self.assertEqual(example.name, 'examplecom theme')
        self.assertEqual(example.slug, 'examplecom-theme')
        self.assertEqual(wifi.name, 'wifiincz theme')
        self.assertEqual(wifi.slug, 'wifiincz-theme')
        self.assertEqual(wifi.accent_hue, theme_identity('http://wifi-in.cz').accent_hue)
        self.assertNotEqual(example.accent_hue, wifi.accent_hue)

    def test_donor_web_directory_is_not_a_wayback_wrapper(self):
        for path in ('/web/_css/style.css', '/web/_js/jquery.1.9.1.js', '/web/_images/spsao-bruntal.png', '/web/123/style.css'):
            self.assertEqual(original_url(path, 'http://sps-br.cz/skola/soucasnost'), 'http://sps-br.cz' + path)
        for wrapper in ('/web/20160604215401id_/http://sps-br.cz/web/_css/style.css',
                        'https://web.archive.org/web/20160604215401cs_/http://sps-br.cz/web/_css/style.css',
                        '//web.archive.org/web/20160604215401/http://sps-br.cz/web/_css/style.css'):
            self.assertEqual(original_url(wrapper, 'http://sps-br.cz/'), 'http://sps-br.cz/web/_css/style.css')

    def response(self, body=b'complete'):
        response = Mock()
        response.status_code, response.is_redirect = 200, False
        response.headers = {'Content-Type': 'text/plain'}
        response.iter_content.return_value = iter([body])
        return response

    def test_transient_timeout_retries_same_snapshot(self):
        events, activity = [], []
        client = ArchiveClient(on_event=events.append, on_activity=activity.append)
        client.cancel = Mock()
        client.cancel.is_set.return_value = False
        client.cancel.wait.return_value = False
        client.session.get = Mock(side_effect=[requests.Timeout('temporary'), self.response()])
        self.assertEqual(client.get(STAMP + '/')[0], b'complete')
        self.assertEqual(client.session.get.call_count, 2)
        self.assertEqual(client.session.get.call_args_list[0].args, client.session.get.call_args_list[1].args)
        self.assertIn('Timeout', events[0])
        self.assertEqual([row['status'] for row in activity], ['requesting', 'retrying', 'requesting', 'received'])
        self.assertEqual(activity[1]['attempt'], 2)
        self.assertEqual(activity[-1]['request_bytes'], len(b'complete'))
        client.close()

    def test_partial_response_is_discarded_before_retry(self):
        first = self.response()
        def interrupted_body(*args):
            yield b'partial'
            raise requests.exceptions.ChunkedEncodingError('incomplete')
        first.iter_content.side_effect = interrupted_body
        client = ArchiveClient()
        client.cancel = Mock()
        client.cancel.is_set.return_value = False
        client.cancel.wait.return_value = False
        client.session.get = Mock(side_effect=[first, self.response()])
        self.assertEqual(client.get(STAMP + '/')[0], b'complete')
        client.close()

    def test_final_network_error_includes_type_without_query_secrets(self):
        activity = []
        client = ArchiveClient(on_activity=activity.append)
        client.cancel = Mock()
        client.cancel.is_set.return_value = False
        client.cancel.wait.return_value = False
        client.session.get = Mock(side_effect=requests.ConnectionError('temporary'))
        with self.assertRaises(RestorationError) as raised:
            client.get(STAMP + '/?token=synthetic-sensitive-value')
        self.assertEqual(client.session.get.call_count, 3)
        self.assertIn('ConnectionError', str(raised.exception))
        self.assertNotIn('synthetic-sensitive-value', str(raised.exception))
        self.assertEqual(activity[-1]['status'], 'failed')
        self.assertEqual(activity[-1]['attempt'], 3)
        self.assertIn('?[параметры скрыты]', activity[-1]['url'])
        self.assertNotIn('synthetic-sensitive-value', json.dumps(activity, ensure_ascii=False))
        client.close()

    def test_archive_modifier_and_query(self):
        snapshot = Snapshot.parse('http://web.archive.org/web/20200101/https://example.com/index.php?id=7')
        self.assertEqual(snapshot.archive_url, STAMP.replace('20200101000000', '20200101') + '/index.php?id=7')
        self.assertEqual(route_for(snapshot.original), '/index.php?id=7')

    def test_invalid_snapshots_and_domain(self):
        invalid = ['https://example.com/', 'https://web.archive.org.evil.example/web/20200101/https://example.com/',
                   'https://web.archive.org/web/20201301/https://example.com/',
                   'https://web.archive.org/web/20200101/https://example.com/%2e%2e/private',
                   'https://web.archive.org/web/20200101/https://127.0.0.1/']
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(RestorationError):
                Snapshot.parse(value)

    def test_request_validation(self):
        for field, value in [('lang', 'cs_CZ'), ('menu_pages', []), ('final_domain', 'example.com/path')]:
            item = request()
            setattr(item, field, value)
            with self.subTest(field=field), self.assertRaises((ValueError, RestorationError)):
                item.validate()

    def test_slug_collision(self):
        item = request()
        item.menu_pages = [item.menu_pages[0].replace('/about/', '/casino/about/')]
        item.casino_pages[0] = CasinoPage('Collision', 'about')
        with self.assertRaises(RestorationError):
            item.validate()

    def test_agent_transport_validation(self):
        for endpoint in ('http://provider.example/v1', 'https://key@provider.example/v1', 'https://provider.example/v1?key=secret'):
            with self.subTest(endpoint=endpoint), self.assertRaises(RestorationError):
                AgentClient(AgentConfig(endpoint, 'test-model'))
        AgentClient(AgentConfig('http://127.0.0.1:1234/v1', 'local-model'))

    def test_archive_redirect_cannot_fetch_live_site(self):
        client = ArchiveClient()
        response = Mock()
        response.status_code, response.is_redirect = 302, True
        response.headers = {'Location': 'https://example.com/live'}
        client.session.get = Mock(return_value=response)
        with self.assertRaises(RestorationError):
            client.get(STAMP + '/')
        self.assertEqual(client.session.get.call_count, 1)
        client.close()

    def test_cancel_stops_before_network(self):
        client = ArchiveClient()
        client.cancel.set()
        client.session.get = Mock()
        with self.assertRaises(RestorationError):
            client.get(STAMP + '/')
        client.session.get.assert_not_called()
        client.close()

    def test_agent_openai_contract_and_json(self):
        response = Mock()
        response.ok, response.is_redirect = True, False
        response.json.return_value = {'choices': [{'message': {'content': '```json\n{"title":"Page title","description":"Page description"}\n```'}}]}
        agent = AgentClient(AgentConfig('https://provider.example/v1', 'test-model', 'synthetic-test-key'))
        with patch('drop_restorer.agents.client.requests.post', return_value=response) as post:
            self.assertEqual(agent.metadata('Example', 'Archived text', 'en-GB')['title'], 'Page title')
            self.assertEqual(post.call_args.args[0], 'https://provider.example/v1/chat/completions')
            self.assertFalse(post.call_args.kwargs['allow_redirects'])
            self.assertEqual(post.call_args.kwargs['json']['model'], 'test-model')

    def test_agent_empty_response_is_actionable(self):
        response = Mock()
        response.ok, response.is_redirect = True, False
        response.json.return_value = {'choices': [{'message': {'content': None}}]}
        agent = AgentClient(AgentConfig('https://provider.example/v1', 'test-model'))
        with patch('drop_restorer.agents.client.requests.post', return_value=response), self.assertRaises(RestorationError):
            agent.complete('Test')


class PipelineTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'var' / 'drop-restorer' / 'development-qa' / 'unit-fixtures'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='drop-restorer-test-', dir=base)
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(base.resolve()))
        self.addCleanup(self.temp.cleanup)
        self.client = FixtureArchive()
        self.build = completed_fixture(Path(self.temp.name), client=self.client)

    def test_only_selected_pages_and_assets_requested(self):
        self.assertEqual(len(self.build.pages), 5)
        self.assertTrue(all(url.startswith(STAMP) for url in self.client.seen))
        self.assertFalse(any('/hidden/' in url or 'external.example' in url for url in self.client.seen))
        self.assertEqual(sum('/images/logo.png' in x for x in self.client.seen), 1)

    def test_canonical_and_metadata_ownership(self):
        titles, descriptions = set(), set()
        for page in self.build.pages:
            soup = BeautifulSoup(page.html, 'lxml')
            self.assertEqual(soup.html['lang'], 'en-GB')
            self.assertEqual(len(soup.select('link[rel~="canonical"]')), 1)
            self.assertEqual(soup.select_one('link[rel~="canonical"]')['href'], 'https://restored.example' + page.route)
            if page.casino:
                self.assertIsNone(soup.title)
                self.assertIsNone(soup.select_one('meta[name="description"]'))
                self.assertEqual(soup.select_one('#dr-editor-content').get_text(), '')
            else:
                titles.add(page.seo_title)
                descriptions.add(page.description)
                self.assertTrue(30 <= len(page.seo_title) <= 60)
                self.assertTrue(100 <= len(page.description) <= 180)
        self.assertEqual(len(titles), 2)
        self.assertEqual(len(descriptions), 2)

    def test_cleanup_preserves_source_visuals_and_local_navigation(self):
        soup = BeautifulSoup(self.build.pages[0].html, 'lxml')
        self.assertIsNone(soup.select_one('.language-switcher'))
        self.assertIsNone(soup.select_one('.ads'))
        self.assertIsNone(soup.select_one('.banner'))
        self.assertIn('External label', soup.get_text())
        self.assertIsNone(soup.find('a', href='https://external.example/'))
        self.assertIsNotNone(soup.find('img', alt='Social icon'))
        self.assertEqual(len(soup.select('.dr-submenu a')), 3)
        self.assertEqual(len(soup.select('nav .dr-menu > li')), 3)
        self.assertNotIn('Hidden', soup.select_one('nav').get_text())
        self.assertNotIn('gtag(', str(soup))
        self.assertEqual(soup.form['action'], '#')
        self.assertIn('/assets/media/', soup.select_one('[style]')['style'])

    def test_css_and_assets_are_portable(self):
        records = json.loads((self.build.root / 'asset-manifest.json').read_text())
        self.assertGreaterEqual(len(records), 4)
        css = next((self.build.root / 'theme' / 'assets' / 'css').glob('*.css')).read_text()
        self.assertIn('../media/', css)
        self.assertNotIn('web.archive.org', css)

    def test_generated_theme_has_domain_identity_and_complete_404(self):
        theme = self.build.root / 'theme'
        style = (theme / 'style.css').read_text(encoding='utf-8')
        manifest = json.loads((theme / 'site.json').read_text(encoding='utf-8'))
        self.assertIn('Theme Name: restoredexample theme', style)
        self.assertIn('Text Domain: restoredexample-theme', style)
        self.assertEqual(manifest['theme']['slug'], 'restoredexample-theme')
        self.assertTrue(0 <= manifest['theme']['accent_hue'] <= 359)
        self.assertTrue((theme / '404.php').is_file())
        self.assertTrue((theme / 'assets/site-404.css').is_file())
        self.assertTrue((theme / 'assets/site-navigation.css').is_file())
        self.assertTrue((theme / 'assets/site-navigation.js').is_file())
        self.assertFalse((theme / 'assets/drop-restorer.css').exists())
        self.assertFalse((theme / 'assets/drop-restorer.js').exists())
        branded = []
        for path in theme.rglob('*'):
            if not path.is_file():
                continue
            if 'drop-restorer' in path.relative_to(theme).as_posix().lower():
                branded.append(str(path))
            elif path.suffix.lower() in {'.css', '.js', '.json', '.php', '.txt', '.xml', '.md'}:
                if 'droprestorer' in path.read_text(encoding='utf-8', errors='replace').lower().replace('-', '').replace('_', '').replace(' ', ''):
                    branded.append(str(path))
        self.assertFalse(branded)

    def test_wxr_has_empty_editable_casino_pages(self):
        tree = etree.parse(str(self.build.root / 'content.xml'))
        items = [item for item in tree.findall('./channel/item') if item.findtext('wp:post_type', namespaces=NS) == 'page']
        self.assertEqual(len(items), 5)
        for item, page in zip(items, self.build.pages):
            meta = {node.findtext('wp:meta_key', namespaces=NS): node.findtext('wp:meta_value', namespaces=NS)
                    for node in item.findall('wp:postmeta', NS)}
            post_name = item.findtext('wp:post_name', namespaces=NS)
            if page.casino:
                self.assertFalse(item.findtext('content:encoded', namespaces=NS))
                self.assertEqual(meta['_wp_page_template'], 'casino-page.php')
                self.assertNotIn('_dr_seo_title', meta)
                self.assertNotIn('_dr_description', meta)
                self.assertEqual(post_name, page.route.rstrip('/').rsplit('/', 1)[-1])
                self.assertNotIn('/', post_name)
            else:
                self.assertIn('<main>', item.findtext('content:encoded', namespaces=NS))
            self.assertEqual(item.findtext('wp:status', namespaces=NS), 'publish')
            self.assertEqual(meta['_dr_route'], page.route)
        menus = [item for item in tree.findall('./channel/item') if item.findtext('wp:post_type', namespaces=NS) == 'nav_menu_item']
        self.assertEqual(len(menus), 6)

    def test_theme_routes_survive_first_request_before_rewrite_flush(self):
        functions = (self.build.root / 'theme' / 'functions.php').read_text(encoding='utf-8')
        self.assertIn("add_action('parse_request'", functions)
        self.assertIn('function dr_route_key_for_request', functions)
        self.assertIn('function dr_route_request_is_canonical', functions)
        self.assertIn("}, -10);", functions)
        self.assertIn("add_action('init', 'dr_setup_routing', 100);", functions)
        self.assertLess(functions.index("}, -10);"), functions.index("function dr_canonical"))
        seo = (self.build.root / 'theme' / 'seo.php').read_text(encoding='utf-8')
        self.assertIn("add_action('template_redirect', function()", seo)
        self.assertIn("}, -30);", seo)

    def test_packaging_requires_current_explicit_approval(self):
        target = self.build.root / 'synthetic-development-test.zip'
        with self.assertRaises(RestorationError):
            package(self.build, target)
        digest = self.build.digest()
        approve(self.build, digest)
        asset = self.build.root / 'theme' / 'style.css'
        original = asset.read_bytes()
        asset.write_bytes(original + b'/* changed */')
        with self.assertRaises(RestorationError):
            package(self.build, target)
        with self.assertRaises(RestorationError):
            approve(self.build, digest)
        approve(self.build, self.build.digest())
        package(self.build, target)
        with zipfile.ZipFile(target) as archive:
            self.assertIn('restoredexample-theme/style.css', archive.namelist())
            self.assertIn('restoredexample-theme/404.php', archive.namelist())
            self.assertIn('restoredexample-theme/functions.php', archive.namelist())
            self.assertNotIn('content.xml', archive.namelist())
            self.assertFalse(any('source/' in name or 'settings' in name for name in archive.namelist()))
            self.assertIsNone(archive.testzip())
        from drop_restorer.core.packager import bundle_path, content_path
        with zipfile.ZipFile(bundle_path(target)) as archive:
            self.assertIn('content.xml', archive.namelist())
            self.assertIn('restoredexample-theme.zip', archive.namelist())
            self.assertTrue(json.loads(archive.read('install-checks.json'))['passed'])
        self.assertEqual(content_path(target).read_bytes(), (self.build.root/'content.xml').read_bytes())
        with self.assertRaises(RestorationError):
            package(self.build, target)

    def test_missing_stylesheet_is_never_packaged_even_when_owner_accepts_findings(self):
        (self.build.root / 'theme/style.css').unlink()
        approve(self.build, self.build.digest(), accept_findings=True)
        target = self.build.root / 'missing-style.zip'
        with self.assertRaisesRegex(RestorationError, 'style.css'):
            package(self.build, target)
        self.assertFalse(target.exists())
        self.assertFalse((self.build.root/'missing-style-bundle.zip').exists())

    def test_existing_companion_is_preserved_and_no_partial_theme_is_created(self):
        from drop_restorer.core.packager import bundle_path
        target = self.build.root/'collision.zip'
        companion = bundle_path(target)
        companion.write_bytes(b'Existing owner file')
        approve(self.build, self.build.digest(), accept_findings=True)
        with self.assertRaisesRegex(RestorationError, 'существует'):
            package(self.build, target)
        self.assertEqual(companion.read_bytes(), b'Existing owner file')
        self.assertFalse(target.exists())

    def test_bundle_and_invalid_style_header_are_rejected_as_themes(self):
        from drop_restorer.core.packager import validate_theme_zip
        target = self.build.root/'not-a-theme.zip'
        with zipfile.ZipFile(target, 'w') as archive:
            archive.writestr('theme/style.css', '/* Theme Name: Example */')
            archive.writestr('content.xml', '<rss/>')
        with self.assertRaises(RestorationError):
            validate_theme_zip(target)
        (self.build.root/'theme/style.css').write_text('body {color: black;}')
        approve(self.build, self.build.digest(), accept_findings=True)
        with self.assertRaisesRegex(RestorationError, 'Theme Name'):
            package(self.build, self.build.root/'invalid-header.zip')

    def test_preview_routes_and_assets_only(self):
        server = PreviewServer(self.build)
        self.addCleanup(server.stop)
        for page in self.build.pages:
            response = requests.get(server.origin + page.route, timeout=5)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(requests.get(server.origin + '/hidden/', timeout=5).status_code, 404)
        missing = requests.get(server.origin + '/hidden/', timeout=5)
        self.assertIn('site-not-found__card', missing.text)
        self.assertIn('restored.example', missing.text)
        self.assertEqual(requests.get(server.origin + '/', headers={'Host': 'attacker.example'}, timeout=5).status_code, 403)

    def test_source_or_screenshot_changes_do_not_approve_site(self):
        write_site(self.build)
        self.assertIsNone(self.build.approved_digest)

    def test_query_page_takes_precedence_over_homepage(self):
        self.build.pages[1].route = '/?section=about'
        server = PreviewServer(self.build)
        self.addCleanup(server.stop)
        response = requests.get(server.origin + '/?section=about', timeout=5)
        soup = BeautifulSoup(response.content, 'lxml')
        self.assertEqual(soup.h1.get_text(), 'About the example organisation')


if __name__ == '__main__':
    unittest.main()
