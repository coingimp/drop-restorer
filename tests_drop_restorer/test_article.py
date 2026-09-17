import tempfile
import json
import unittest
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup
from lxml import etree

from drop_restorer.core.wordpress import NS
from drop_restorer.core.pipeline import Pipeline
from drop_restorer.core.packager import validate_theme_zip
from drop_restorer.core.models import RestorationError
from drop_restorer.preview.server import PreviewServer
from tests_drop_restorer.fixtures import FixtureArchive, request, completed_fixture
import requests


class ArticleTests(unittest.TestCase):
    def test_new_theme_keeps_editor_empty_and_archived_markup_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            build = completed_fixture(Path(folder))
            xml = etree.parse(str(build.root / 'content.xml'))
            casino = [item for item in xml.findall('.//item') if item.findtext('wp:post_type', namespaces=NS) == 'page'
                      and any(meta.findtext('wp:meta_key', namespaces=NS) == '_dr_casino' and meta.findtext('wp:meta_value', namespaces=NS) == '1'
                              for meta in item.findall('wp:postmeta', NS))]
            self.assertEqual(len(casino), 3)
            self.assertTrue(all(not item.findtext('content:encoded', namespaces=NS) for item in casino))
            for page in build.pages:
                if page.casino:
                    shell = (build.root / 'theme/views' / (page.key + '.html')).read_text(encoding='utf-8')
                    self.assertIn('DR_ARTICLE_HEADING_SLOT', shell)
                    self.assertIn('DR_EDITOR_CONTENT_SLOT', shell)
                    self.assertIn('dr-article', BeautifulSoup(shell, 'lxml').select_one('#dr-editor-content')['class'])
                else:
                    self.assertIsNone(BeautifulSoup(page.html, 'lxml').select_one('.dr-article'))
            self.assertTrue((build.root / 'theme/article.php').is_file())
            css = (build.root / 'theme/assets/dr-article-editor.css').read_text(encoding='utf-8')
            self.assertNotIn(':is(#dr-editor-content, .dr-article)', css)
            self.assertIn('body', css)

    def test_review_preview_has_article_styles_before_theme_generation(self):
        with tempfile.TemporaryDirectory() as folder:
            pipeline = Pipeline(Path(folder), client=FixtureArchive())
            build = pipeline.run(request())
            build = pipeline.finish(build, 'keep')
            self.assertEqual(build.status, 'site_review')
            server = PreviewServer(build)
            origin = server.origin
            try:
                session = requests.Session(); session.trust_env = False
                response = session.get(origin + '/casino-rating/', timeout=10)
                self.assertEqual(response.status_code, 200)
                soup = BeautifulSoup(response.content, 'lxml')
                self.assertIsNotNone(soup.select_one('link[href="/assets/dr-article.css"]'))
                self.assertEqual(session.get(origin + '/assets/dr-article.css', timeout=10).status_code, 200)
            finally:
                server.stop()

    def test_missing_editor_style_blocks_install_package(self):
        with tempfile.TemporaryDirectory() as folder:
            build = completed_fixture(Path(folder))
            theme_slug = json.loads((build.root / 'theme/site.json').read_text(encoding='utf-8'))['theme']['slug']
            archive = build.root / 'broken-theme.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                for path in (build.root / 'theme').rglob('*'):
                    if path.is_file() and path.name != 'dr-article-editor.css':
                        bundle.write(path, theme_slug + '/' + path.relative_to(build.root / 'theme').as_posix())
            with self.assertRaisesRegex(RestorationError, 'dr-article-editor.css'):
                validate_theme_zip(archive)
