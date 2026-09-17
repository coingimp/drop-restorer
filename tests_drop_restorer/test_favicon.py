import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
from drop_restorer.core.branding import branding
from drop_restorer.core.cleaner import parse_html
from drop_restorer.core.favicon import profile
from drop_restorer.core.models import Page, RestorationError


class FaviconTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'theme/assets').mkdir(parents=True)
        Image.new('RGBA',(64,32),'#ff6600').save(self.root/'theme/assets/logo.png')
        self.soups = [parse_html('<title>Film a divadlo</title><header><div id="logo"><img src="/assets/logo.png"></div></header><main><h1>Film</h1></main>'),
                      parse_html('<h1>Casino gambling poker roulette casino</h1>')]
        self.build = SimpleNamespace(root=self.root,pages=[Page('home','/','Film a divadlo',''),Page('casino','/casino/','Casino gambling poker','',casino=True)], warnings=[])
        self.downloader = Mock(); self.downloader.fetch.return_value = ''

    def test_missing_favicon_is_generated_without_agent_in_all_required_sizes(self):
        branding(self.soups, self.build, self.downloader, None)
        report = json.loads((self.root/'favicon-report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['topic'], 'cinema')
        self.assertEqual(report['method'], 'local_topic_vector')
        for size in (16,32,64,128,180,192,512):
            with Image.open(self.root/f'theme/assets/branding/favicon-{size}.png') as image:
                self.assertEqual(image.size,(size,size)); self.assertTrue(image.getbbox())
        with Image.open(self.root/'theme/assets/branding/favicon.ico') as image:
            self.assertEqual(image.ico.sizes(), {(n,n) for n in (16,32,48,64,128,256)})
        for soup in self.soups:
            self.assertEqual(len(soup.select('link[rel=icon]')),2)
            self.assertEqual(soup.select_one('link[rel=apple-touch-icon]')['sizes'],'180x180')
        self.assertFalse(any('Подключите агента' in message for message in self.build.warnings))

    def test_broken_declared_icon_is_replaced_and_valid_icon_is_preserved(self):
        self.soups[0].head.append(self.soups[0].new_tag('link',rel='icon',href='/assets/broken.ico'))
        (self.root/'theme/assets/broken.ico').write_text('not an image')
        branding(self.soups,self.build,self.downloader,None)
        before = (self.root/'theme/assets/branding/favicon.ico').read_bytes()
        agent = Mock()
        branding(self.soups,self.build,self.downloader,None,agent)
        agent.svg.assert_not_called()
        self.assertEqual(before,(self.root/'theme/assets/branding/favicon.ico').read_bytes())
        self.assertEqual(len(self.soups[0].select('link[rel=icon]')),2)

    def test_agent_failure_falls_back_to_topic_and_owner_can_choose_topic(self):
        agent = Mock(); agent.svg.side_effect = RestorationError('Provider unavailable')
        branding(self.soups,self.build,self.downloader,None,agent,'favicon','cycling')
        report = json.loads((self.root/'favicon-report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['topic'],'cycling')
        self.assertEqual(report['method'],'local_after_agent_failure')
        self.assertEqual(report['selected_by'],'owner')
        self.assertIn('cycling',agent.svg.call_args.args[0])

    def test_unknown_topic_is_honestly_marked_for_review(self):
        self.build.pages[0].title='Example'
        self.soups[0]=parse_html('<title>Example</title><h1>Example</h1>')
        result=profile(self.soups,self.build)
        self.assertEqual(result['topic'],'general')
        self.assertTrue(result['needs_topic_review'])


if __name__ == '__main__':
    unittest.main()
