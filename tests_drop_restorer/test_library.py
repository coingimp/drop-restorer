import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

from drop_restorer.core.library import inventory, load_card, project_directory, remove_project
from drop_restorer.core.models import RestorationError
from drop_restorer.core.packager import approve
from drop_restorer.core.pipeline import Pipeline
from drop_restorer.web.server import create_app
from tests_drop_restorer.fixtures import FixtureArchive, completed_fixture, request


class LibraryTests(unittest.TestCase):
    def setUp(self):
        qa = Path(__file__).resolve().parents[1] / 'var/drop-restorer/development-qa/library-tests'
        qa.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=qa)
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.app = create_app(self.workspace)
        self.state = self.app.extensions['workspace']
        self.addCleanup(self.state.close)
        self.client = self.app.test_client()
        self.origin = 'http://127.0.0.1:8780'
        self.headers = {'X-DropRestorer-Token': self.state.token, 'Origin': self.origin}

    def post(self, route, data):
        return self.client.post(route, json=data, headers=self.headers, base_url=self.origin)

    def get(self, route):
        return self.client.get(route, headers=self.headers, base_url=self.origin)

    def build(self):
        return Pipeline(self.state.root, client=FixtureArchive()).run(request())

    def wait(self):
        deadline = time.monotonic() + 15
        while self.state.job['busy'] and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(self.state.job['busy'])
        self.assertIsNone(self.state.job['error'])

    def confirmation(self, root):
        response = self.post('/api/projects/delete-info', {'id': root.name})
        self.assertEqual(response.status_code, 200)
        return {'id': root.name, 'confirmed': True, 'confirmation': response.json['confirmation']}

    def test_cards_pick_homepage_title_and_target_url_instead_of_wayback(self):
        build = self.build()
        home = build.pages[0]
        home.seo_title = 'Homepage SEO title'
        build.pages[0], build.pages[1] = build.pages[1], build.pages[0]
        build.save()
        with patch.object(self.state.thumbnails, 'ensure', return_value={'status': 'unavailable'}):
            card = self.get('/api/projects').json[0]
        self.assertEqual(card['title'], home.seo_title)
        self.assertEqual(card['url'], 'https://restored.example/')
        self.assertEqual(card['pages'], len(build.pages))
        build.request.final_domain = ''
        build.save()
        self.assertEqual(load_card(build.root)['origin'], 'https://example.com')

    def test_broken_and_interrupted_runs_remain_visible_and_removable(self):
        for identifier, content in (('20260101-010101-broken', '{broken'), ('20260101-010102-interrupted', None)):
            root = self.state.root / identifier
            root.mkdir()
            (root / 'run.log').write_text('Failed before first page')
            if content:
                (root / 'project.json').write_text(content)
        reserved = self.state.root / 'development-qa'
        reserved.mkdir()
        (reserved / 'project.json').write_text('{}')
        cards = self.get('/api/projects').json
        self.assertEqual({row['id'] for row in cards}, {'20260101-010101-broken', '20260101-010102-interrupted'})
        self.assertTrue(all(not row['can_open'] and row['thumbnail']['status'] == 'unavailable' for row in cards))
        for card in cards:
            root = self.state.root / card['id']
            self.assertEqual(self.post('/api/projects/delete', self.confirmation(root)).status_code, 202)
            self.wait()
            self.assertFalse(root.exists())
        self.assertTrue((reserved / 'project.json').is_file())

    def test_delete_needs_confirmation_for_the_exact_build_and_idle_state(self):
        first, other = self.build(), self.build()
        values = self.confirmation(first.root)
        for invalid in ({'id': first.root.name}, dict(values, confirmed='true'), dict(values, id=other.root.name), dict(values, confirmation=[])):
            self.assertEqual(self.post('/api/projects/delete', invalid).status_code, 400)
        self.state.job['busy'] = True
        self.assertEqual(self.post('/api/projects/delete-info', {'id': first.root.name}).status_code, 400)
        self.assertEqual(self.post('/api/projects/delete', values).status_code, 400)
        self.state.job['busy'] = False
        self.assertTrue(first.root.exists() and other.root.exists())

    def test_selected_build_deletes_every_file_and_clears_preview_downloads_and_saved_selection(self):
        build = completed_fixture(self.state.root)
        other = self.build()
        self.state.select(build)
        preview = self.post('/api/preview', {'id': build.root.name})
        self.assertEqual(preview.status_code, 200)
        (build.root / '.extra/nested').mkdir(parents=True)
        (build.root / '.extra/nested/data.bin').write_bytes(b'extra files')
        (build.root / 'theme.zip').write_bytes(b'old archive')
        (build.root / 'card-preview').mkdir()
        (build.root / 'card-preview/homepage.jpg').write_bytes(b'old thumbnail')
        download = self.state.register_file(build.root / 'theme.zip')
        surviving = self.state.register_file(other.root / 'project.json')
        settings = self.state.root / 'settings.json'
        settings.write_text('{"lang":"cs-CZ"}')
        backup = self.workspace / 'backups/snapshot.zip'
        backup.parent.mkdir()
        backup.write_bytes(b'backup must survive')
        values = self.confirmation(build.root)
        self.assertEqual(self.post('/api/projects/delete', values).status_code, 202)
        self.wait()
        self.assertFalse(build.root.exists())
        self.assertTrue(other.root.is_dir())
        self.assertEqual(settings.read_text(), '{"lang":"cs-CZ"}')
        self.assertEqual(backup.read_bytes(), b'backup must survive')
        self.assertIsNone(self.state.build)
        self.assertIsNone(self.state.preview)
        self.assertIsNone(self.state.preview_receipt)
        self.assertIsNone(self.state.archive)
        self.assertEqual(self.get(download['url']).status_code, 404)
        response = self.get(surviving['url'])
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.get('/project-thumbnails/' + build.root.name).status_code, 404)
        restarted = create_app(self.workspace).extensions['workspace']
        self.addCleanup(restarted.close)
        self.assertIsNone(restarted.build)

    def test_deleting_an_unselected_build_preserves_the_current_build(self):
        first, other = self.build(), self.build()
        self.state.select(first)
        self.assertEqual(self.post('/api/projects/delete', self.confirmation(other.root)).status_code, 202)
        self.wait()
        self.assertEqual(self.state.build.root, first.root)
        self.assertEqual(json.loads((self.state.root / 'web-state.json').read_text())['project'], first.root.name)

    def test_paths_outside_the_build_library_cannot_be_deleted(self):
        build = self.build()
        for identifier in ('..', '../backups', '../../backups', str(build.root), 'development-qa', 'settings.json', ''):
            self.assertEqual(self.post('/api/projects/delete-info', {'id': identifier}).status_code, 400)
        self.assertTrue(build.root.is_dir())
        self.assertEqual(self.client.post('/api/projects/delete-info', json={'id': build.root.name}, base_url=self.origin).status_code, 403)

    def test_junctions_and_symlinks_are_rejected_before_any_deletion(self):
        build, other = self.build(), self.build()
        external = self.workspace / 'unrelated'
        external.mkdir()
        sentinel = external / 'keep.txt'
        sentinel.write_text('Keep this')
        link = build.root / 'linked-folder'
        if os.name == 'nt':
            script = "New-Item -ItemType Junction -Path '" + str(link).replace("'", "''") + "' -Target '" + str(external).replace("'", "''") + "' | Out-Null"
            command = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(command.returncode, 0, command.stderr)
        else:
            link.symlink_to(external, target_is_directory=True)
        try:
            self.assertEqual(self.post('/api/projects/delete-info', {'id': build.root.name}).status_code, 400)
            with self.assertRaises(RestorationError):
                remove_project(self.state.root, build.root.name)
            self.assertTrue((build.root / 'project.json').is_file())
            self.assertEqual(sentinel.read_text(), 'Keep this')
            self.assertTrue(other.root.exists())
        finally:
            if os.name == 'nt':
                link.rmdir()  # Remove only the checked junction entry, never its target.
            else:
                link.unlink()

    def test_thumbnail_reuses_desktop_screenshot_without_changing_approval(self):
        build = completed_fixture(self.state.root)
        approve(build, build.digest())
        digest = build.digest()
        folder = build.root / 'preview_screenshots'
        Image.new('RGB', (1920, 1080), '#b3c9a6').save(folder / ('desktop_' + build.pages[0].key + '.png'))
        card = load_card(build.root)
        service = self.state.thumbnails
        stamp = service.fingerprint(build.root, card)
        with patch('drop_restorer.web.thumbnails.subprocess.Popen') as child:
            service.render(build.root, card, stamp)
        child.assert_not_called()
        cover = service.ensure(build.root, card)
        self.assertEqual(cover['status'], 'ready')
        response = self.get(cover['url'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/jpeg')
        response.close()
        with Image.open(build.root / 'card-preview/homepage.jpg') as image:
            self.assertEqual(image.size, (960, 540))
        self.assertEqual(build.digest(), digest)
        self.assertEqual(build.approved_digest, digest)
        build.pages[0].html += '<p>New homepage text</p>'
        build.save()
        self.assertNotEqual(service.fingerprint(build.root, load_card(build.root)), stamp)

    def test_in_flight_thumbnail_finishes_before_folder_removal_and_cannot_recreate_it(self):
        build = self.build()
        started, finish = threading.Event(), threading.Event()
        def rendering(root, card, version):
            started.set()
            finish.wait(5)
            (root / 'card-preview').mkdir(exist_ok=True)
            (root / 'card-preview/late.txt').write_text('Renderer finishing')
        with patch.object(self.state.thumbnails, 'render', side_effect=rendering):
            self.state.thumbnails.ensure(build.root, load_card(build.root))
            self.assertTrue(started.wait(3))
            values = self.confirmation(build.root)
            self.assertEqual(self.post('/api/projects/delete', values).status_code, 202)
            self.assertFalse(self.state.job['cancellable'])
            self.assertEqual(self.post('/api/cancel', {}).status_code, 400)
            finish.set()
            self.wait()
        self.assertFalse(build.root.exists())
        self.assertIn(build.root.name, self.state.thumbnails.blocked)
