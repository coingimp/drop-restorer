import unittest

from bs4 import BeautifulSoup

from drop_restorer.core.casino_layout import apply_to_soup, default_layout, normalize_layout, css_for_layout


class CasinoLayoutTests(unittest.TestCase):
    def test_defaults_and_bounds_are_stable(self):
        self.assertEqual(normalize_layout(None), default_layout())
        layout = normalize_layout({'content_width_px': 99999, 'table_width_px': -2,
                                   'row_height_px': 1, 'cell_padding_px': 99,
                                   'cell_widths_px': {'logo': 99999}})
        self.assertEqual(layout['content_width_px'], 2400)
        self.assertEqual(layout['table_width_px'], 0)
        self.assertEqual(layout['row_height_px'], 72)
        self.assertEqual(layout['cell_padding_px'], 32)
        self.assertEqual(layout['cell_widths_px']['logo'], 1200)

    def test_css_is_scoped_to_casino_pages(self):
        html = '<html><head></head><body><main class="dr-casino-content"><div id="dr-editor-content"></div></main></body></html>'
        soup = BeautifulSoup(html, 'html.parser')
        policy = {'casino_layout': {'content_width_px': 980, 'table_width_px': 0, 'row_height_px': 140,
                                    'cell_padding_px': 8, 'cell_widths_px': {'button': 180}}}
        apply_to_soup(soup, policy)
        self.assertIsNone(soup.select_one('#dr-casino-layout'))
        soup.body['class'] = ['dr-casino-page']
        apply_to_soup(soup, policy)
        style = soup.select_one('#dr-casino-layout')
        self.assertIsNotNone(style)
        self.assertIn('max-width: 980px', style.text)
        self.assertIn('row-height', style.text.lower())
        self.assertIn('180px', style.text)

    def test_replaces_old_style(self):
        soup = BeautifulSoup('<html><head><style id="dr-casino-layout">old</style></head><body class="dr-casino-page"></body></html>', 'html.parser')
        apply_to_soup(soup, default_layout())
        self.assertEqual(len(soup.select('#dr-casino-layout')), 1)
        self.assertNotIn('old', soup.select_one('#dr-casino-layout').text)

    def test_legacy_sidebar_uses_flex_shell_without_archived_float_offsets(self):
        soup = BeautifulSoup('''<html><head></head><body class="dr-casino-page dr-legacy-sidebar-navigation">
          <div id="wrapper"><div id="left"><nav class="dr-navigation" data-dr-layout="legacy-sidebar"></nav></div>
          <div id="content" class="dr-casino-content"><div id="dr-editor-content"></div></div><div class="cistic"></div></div>
        </body></html>''', 'html.parser')
        apply_to_soup(soup, {'casino_layout': {'content_width_px': 1120}})
        self.assertIn('dr-legacy-sidebar-shell', soup.select_one('#wrapper').get('class', []))
        self.assertIn('dr-legacy-sidebar-column', soup.select_one('#left').get('class', []))
        self.assertIn('dr-legacy-content-column', soup.select_one('#content').get('class', []))
        css = soup.select_one('#dr-casino-layout').text
        self.assertIn('display: flex !important', css)
        self.assertIn('flex: 1 1 1120px !important', css)
        self.assertIn('position: static !important', css)
        self.assertIn('width: 1120px !important', css)
        self.assertIn('.dr-legacy-sidebar-shell > .cistic', css)
        self.assertIn('display: none !important', css)

    def test_unlimited_legacy_sidebar_width_fills_available_shell(self):
        css = css_for_layout({'content_width_px': 0})
        self.assertIn('width: calc(100% - 32px) !important', css)
        self.assertIn('flex: 1 1 auto !important', css)
        self.assertIn('width: auto !important', css)

    def test_legacy_table_content_shell_fills_donor_width_for_offer_table(self):
        css = css_for_layout(default_layout())
        self.assertIn('.dr-casino-content-shell', css)
        self.assertIn('width: 100% !important', css)
        self.assertIn('.dr-casino-content-shell > tbody > tr > td.dr-casino-content', css)
        self.assertIn('background-size: 100% 100% !important', css)
        self.assertIn('dr-casino-content-shell{width:100%!important', css.replace(' ', ''))


if __name__ == '__main__':
    unittest.main()
