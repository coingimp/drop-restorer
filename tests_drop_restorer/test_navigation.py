import tempfile
import unittest
from pathlib import Path

from drop_restorer.core.cleaner import (casino_shell, casino_shell_issues, clean, clean_links,
                                        deactivate_bottom_menu_links, navigation, parse_html)
from drop_restorer.core.layout import repair_layout
from drop_restorer.core.models import Page
from tests_drop_restorer.fixtures import request
from drop_restorer.core.primary_navigation import primary_menu_issues


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.request = request()
        self.pages = [Page('home', '/', 'A very long archived homepage title', ''), Page('about', '/about/', 'About', '')]
        self.pages += [Page(page.slug, page.route, page.title, '', casino=True) for page in self.request.casino_pages]

    def test_primary_menu_wins_over_earlier_sidebar_and_footer_columns(self):
        soup = parse_html('''<body><nav id="sidebar"><ul><li>Sidebar text</li></ul></nav>
          <div id="header-secondary"><nav id="primary-nav"><ul><li><a href="/about/">About us</a>
          <div class="submenu-holder"><ul><li>Nested menu</li></ul></div></li></ul>
          <div class="portal"><ul><li>Portal</li></ul><ul><li>Another portal</li></ul></div></nav></div>
          <footer><nav><div><ul><li>Footer one</li></ul></div><div><ul><li>Footer two</li></ul></div></nav></footer></body>''')
        footer = str(soup.footer)
        navigation(soup, self.pages, self.request)
        self.assertEqual(len(soup.select('.dr-menu')), 1)
        self.assertEqual(len(soup.select('#primary-nav .dr-submenu a')), 3)
        self.assertEqual(str(soup.footer), footer)
        self.assertEqual(soup.select_one('#sidebar').get_text(), 'Sidebar text')
        self.assertIsNone(soup.select_one('.submenu-holder'))
        self.assertEqual([a.get_text() for a in soup.select('.dr-menu>li>a')], ['Home', 'About us'])

    def test_header_menu_wins_over_content_and_footer_without_known_ids(self):
        soup = parse_html('''<body><header><nav class="masthead-links"><ul><li><a href="/about/">Organisation</a></li></ul></nav></header>
          <main><nav><ul><li>Contents</li></ul></nav><article>Article text</article></main>
          <footer><ul class="menu"><li>Footer text</li></ul></footer></body>''')
        navigation(soup, self.pages, self.request)
        self.assertEqual(len(soup.select('header .dr-menu')), 1)
        self.assertEqual(len(soup.select('.dr-mobile-toggle')), 1)
        self.assertIsNone(soup.select_one('main .dr-menu'))
        self.assertIsNone(soup.select_one('footer .dr-menu'))

    def test_navigation_repeat_does_not_duplicate_controls_assets_or_menu(self):
        soup = parse_html('<header><ul id="primary-nav"><li><a href="/about/">About</a></li></ul></header><main>Text</main>')
        for _ in range(2):
            navigation(soup, self.pages, self.request)
        self.assertEqual(len(soup.select('nav.dr-navigation')), 1)
        self.assertEqual(len(soup.select('.dr-mobile-toggle')), 1)
        self.assertEqual(len(soup.select('.dr-casino')), 1)
        self.assertEqual(len(soup.select('link[href="/assets/site-navigation.css"]')), 1)
        self.assertEqual(len(soup.select('script[src="/assets/site-navigation.js"]')), 1)
        self.assertFalse(soup.select('link[href="/assets/drop-restorer.css"], script[src="/assets/drop-restorer.js"]'))

    def test_wix_dropdown_host_is_neutralized_and_keeps_casino_in_main_menu(self):
        soup = parse_html('''<header><wix-dropdown-menu id="comp-menu"
          class="HYblus eK3b7p wixui-dropdown-menu hidden-during-prewarmup"
          data-num-items="5" tabindex="-1"><nav><ul><li><a href="/about/">About</a></li></ul></nav>
          </wix-dropdown-menu></header><main>Content</main>''')
        navigation(soup, self.pages, self.request)
        host = soup.select_one('#comp-menu.dr-menu-host')
        self.assertIsNotNone(host)
        self.assertEqual(host.name, 'div')
        self.assertNotIn('hidden-during-prewarmup', host.get('class', []))
        self.assertNotIn('data-num-items', host.attrs)
        self.assertIs(host.select_one('nav.dr-navigation'), soup.select_one('nav.dr-navigation'))
        self.assertEqual(len(soup.select('header .dr-casino')), 1)
        self.assertEqual(len(soup.select('.dr-navigation')), 1)

    def test_wix_menu_host_css_cancels_archived_grid_offset(self):
        css = (Path(__file__).parents[1] / 'drop_restorer' / 'templates' / 'drop-restorer.css').read_text(encoding='utf-8')
        self.assertIn('min-width:0!important', css)
        self.assertIn('margin:0!important', css)

    def test_primary_menu_is_centered_with_regular_larger_type(self):
        css = (Path(__file__).parents[1] / 'drop_restorer' / 'templates' / 'drop-restorer.css').read_text(encoding='utf-8')
        self.assertIn('justify-content:center', css.replace(' ', ''))
        self.assertIn('font-size:18px!important', css)
        self.assertIn('font-weight:400!important', css)
        self.assertIn('a:hover:not(.dr-navigation a)', css)

    def test_bottom_menu_links_are_unwrapped_but_primary_menu_survives(self):
        soup = parse_html('''<header><nav class="dr-navigation"><a href="/">Primary</a></nav></header>
          <main><p>Article <a href="/about/">body link</a></p></main>
          <footer><a href="/about/"><strong>About</strong></a><span id="menubottom"><a href="/contact/">Contact</a></span></footer>''')
        removed = deactivate_bottom_menu_links(soup)
        self.assertEqual(removed, 2)
        self.assertIsNone(soup.select_one('footer a'))
        self.assertIsNone(soup.select_one('#menubottom a'))
        self.assertEqual(soup.select_one('footer strong').get_text(), 'About')
        self.assertIsNotNone(soup.select_one('header nav.dr-navigation a'))
        self.assertIsNotNone(soup.select_one('main a'))

    def test_removed_links_keep_visual_wrappers_and_images(self):
        soup = parse_html('<main><a class="card" href="https://external.example/" onclick="location.href=this.href"><img src="/local.png"><span>Original caption</span></a></main>')
        clean_links(soup, 'https://example.com/', {})
        card = soup.select_one('span.card.dr-unlinked')
        self.assertIsNotNone(card)
        self.assertNotIn('onclick', card.attrs)
        self.assertNotIn('href', card.attrs)
        self.assertEqual(card.img['src'], '/local.png')
        self.assertEqual(card.get_text(), 'Original caption')

    def test_legacy_topmenu_wins_over_sidebar_menu_list(self):
        soup = parse_html('<div id="header"><div id="logo">Logo</div><div class="topmenu"><a href="/about/">Short label</a></div></div>'
                          '<div id="rightcol"><div class="smenu"><ul class="menu"><li>Sidebar</li></ul></div></div><main>Content</main>')
        sidebar = str(soup.select_one('#rightcol'))
        navigation(soup, self.pages, self.request)
        self.assertEqual(len(soup.select('#header .topmenu #dr-primary-menu > .dr-casino')), 1)
        self.assertEqual(str(soup.select_one('#rightcol')), sidebar)
        self.assertEqual(soup.select_one('.dr-menu > li:nth-of-type(2) > a').get_text(), 'Short label')
        self.assertFalse(primary_menu_issues(soup))

    def test_fixed_width_topmenu_moves_into_left_column(self):
        soup = parse_html('''<body><div id="topmenu"><p><a href="/">Home</a><a href="/about/">About</a></p></div>
          <div id="wrapper"><div id="left"><div id="topimg">Banner</div><div class="box">Sidebar</div></div>
          <div id="content"><div id="main">Article content</div></div></div></body>''')
        navigation(soup, self.pages, self.request)
        menu = soup.select_one('#left #topmenu.dr-navigation[data-dr-layout="legacy-sidebar"]')
        self.assertIsNotNone(menu)
        self.assertIsNone(soup.select_one('body > #topmenu'))
        self.assertIn('dr-legacy-sidebar-navigation', soup.body.get('class', []))
        self.assertEqual(menu.find_next_sibling().get('id'), 'topimg')
        self.assertEqual(len(menu.select(':scope > #dr-primary-menu > .dr-casino')), 1)
        self.assertFalse(primary_menu_issues(soup))

    def test_sidebar_navigation_styles_are_responsive(self):
        css = (Path(__file__).parents[1] / 'drop_restorer' / 'templates' / 'drop-restorer.css').read_text(encoding='utf-8')
        self.assertIn('.dr-navigation[data-dr-layout="legacy-sidebar"]', css)
        self.assertIn('body.dr-legacy-sidebar-navigation :is(#wrapper,.dr-legacy-sidebar-shell)', css)
        self.assertIn('.dr-navigation[data-dr-layout="legacy-sidebar"].dr-open > .dr-menu', css)

    def test_graphical_table_menu_is_replaced_in_original_banner_strip(self):
        soup = parse_html('''<body><table width="870"><tr><td><img src="banner.jpg"></td></tr>
          <tr><td height="23" background="grafik/bck_leiste_navi.gif"><div id="FWTableContainer1">
          <table width="876"><tr><td><img src="grafik/Navigation/home.jpg" width="146" height="18"></td>
          <td><img src="grafik/Navigation/about.jpg" width="146" height="18"></td>
          <td><img src="grafik/Navigation/contact.jpg" width="146" height="18"></td></tr></table>
          </div></td></tr><tr><td><table><tr><td><h1>Article</h1>
          <p>Archived content long enough to identify the main article region.</p></td></tr></table></td></tr></table></body>''')
        clean(soup, 'de-DE', [])
        navigation(soup, self.pages, self.request)
        menu = soup.select_one('td[background*="bck_leiste_navi"] > nav.dr-navigation')
        self.assertIsNotNone(menu)
        self.assertEqual(menu.get('data-dr-placement'), 'legacy-table-menu')
        self.assertEqual(menu.get('data-dr-layout'), 'legacy-table')
        self.assertIsNone(soup.select_one('[id^="FWTableContainer"]'))
        self.assertEqual(len(soup.select('.dr-navigation')), 1)
        self.assertIn('dr-legacy-table-layout', soup.body.get('class', []))
        self.assertFalse(primary_menu_issues(soup))

    def test_graphical_menu_keeps_distinct_archive_labels_when_titles_repeat(self):
        pages = [Page('home', '/', 'Sport-Forum Krefeld Stodolny GmBH', ''),
                 Page('hours', '/oeffnungszeiten.html', 'Sport-Forum Krefeld Stodolny GmBH', ''),
                 Page('contact', '/kontakt.html', 'Sport-Forum Krefeld Stodolny GmBH', ''),
                 Page('legal', '/impressum.html', 'Sport-Forum Krefeld Stodolny GmBH', '')]
        pages += [Page(page.slug, page.route, page.title, '', casino=True)
                  for page in self.request.casino_pages]
        soup = parse_html('''<body><table width="870"><tr><td><img src="banner.jpg"></td></tr>
          <tr><td height="23" background="grafik/bck_leiste_navi.gif"><div id="FWTableContainer1">
          <table><tr><td><a href="index.html">Angebot</a></td>
          <td><a href="oeffnungszeiten.html">Öffnungszeiten</a></td>
          <td><a href="kontakt.html">Kontakt</a></td>
          <td><a href="impressum.html">Impressum</a></td></tr></table>
          </div></td></tr><tr><td><main>Archived content</main></td></tr></table></body>''')
        navigation(soup, pages, self.request)
        labels = [(a.get_text(' ', strip=True), a.get('href'))
                  for a in soup.select('#dr-primary-menu > li > a')]
        self.assertEqual(labels, [('Home', '/'), ('Öffnungszeiten', '/oeffnungszeiten.html'),
                                  ('Kontakt', '/kontakt.html'), ('Impressum', '/impressum.html')])
        self.assertEqual(len(soup.select('#dr-primary-menu > .dr-casino')), 1)

    def test_graphical_table_menu_has_compact_responsive_styles(self):
        css = (Path(__file__).parents[1] / 'drop_restorer' / 'templates' / 'drop-restorer.css').read_text(encoding='utf-8')
        self.assertIn('.dr-navigation[data-dr-layout="legacy-table"]', css)
        self.assertIn('background:#0086c6!important', css)
        self.assertIn('body.dr-casino-page.dr-legacy-table-casino',
                      (Path(__file__).parents[1] / 'drop_restorer' / 'core' / 'casino_layout.py').read_text(encoding='utf-8'))

    def test_graphical_table_casino_keeps_article_cell_background(self):
        source = parse_html('''<body><table width="870"><tr><td><img src="banner.jpg"></td></tr>
          <tr><td height="23" background="grafik/bck_leiste_navi.gif"><div id="FWTableContainer1">
          <table width="876"><tr><td><img src="grafik/Navigation/home.jpg" width="146" height="18"></td>
          <td><img src="grafik/Navigation/about.jpg" width="146" height="18"></td>
          <td><img src="grafik/Navigation/contact.jpg" width="146" height="18"></td></tr></table>
          </div></td></tr><tr><td><table width="870"><tr>
          <td width="135">Left</td><td width="585" background="grafik/bck_main.gif"><p>'''
          + 'Archived article text ' * 20 + '''</p></td><td width="135">Right</td>
          </tr></table></td></tr></table></body>''')
        clean(source, 'de-DE', [])
        result = casino_shell(source, 'Casino rating')
        navigation(result, self.pages, self.request)
        article = result.select_one('.dr-casino-content')
        self.assertEqual(article.get('background'), 'grafik/bck_main.gif')
        self.assertIsNotNone(result.select_one('td[background*="bck_leiste_navi"] .dr-navigation'))

    def test_graphical_table_casino_removes_adjacent_sidebars(self):
        source = parse_html('''<body><table width="870"><tr><td><img src="banner.jpg"></td></tr>
          <tr><td height="23" background="grafik/bck_leiste_navi.gif"><div id="FWTableContainer1">
          <table width="876"><tr><td><img src="grafik/Navigation/home.jpg" width="146" height="18"></td>
          <td><img src="grafik/Navigation/about.jpg" width="146" height="18"></td>
          <td><img src="grafik/Navigation/contact.jpg" width="146" height="18"></td></tr>
          </table></div></td></tr><tr><td><table width="870"><tr>
          <td width="135" bgcolor="#0086C6"><object width="135" height="350"></object></td>
          <td width="585" background="grafik/bck_main.gif"><p>'''
          + 'Archived article text ' * 20 + '''</p></td>
          <td width="135" background="grafik/bck_leiste_news.jpg"><p>News rail</p></td>
          </tr></table></td></tr><tr><td>Footer</td></tr></table></body>''')
        result = casino_shell(source, 'Casino rating')
        navigation(result, self.pages, self.request)
        article = result.select_one('.dr-casino-content')
        self.assertIsNotNone(article)
        row = article.find_parent('tr')
        self.assertEqual(row.find_all('td', recursive=False), [article])
        self.assertIsNotNone(result.select_one('td[background*="bck_leiste_navi"] .dr-navigation'))
        self.assertIsNotNone(result.find(string=lambda value: value and 'Footer' in value))

    def test_legacy_vertical_id_menu_wins_over_generated_fallback(self):
        soup = parse_html('''<body><nav class="dr-navigation" aria-label="Main navigation" data-dr-placement="header-fallback">
          <ul id="dr-primary-menu"><li class="dr-casino">Casino</li></ul></nav><div id="main"><div id="text">
          <div id="top"><strong id="logo">wifi-in</strong><p><strong class="print_hidden">menu</strong></p>
          <ul id="menu"><li><a href="/" accesskey="1">úvod</a></li><li><a href="/about/" accesskey="2">o projektu</a></li></ul>
          </div><p>Article text</p></div></div></body>''')
        navigation(soup, self.pages, self.request)
        self.assertFalse(primary_menu_issues(soup))
        menu = soup.select_one('nav#menu.dr-navigation[data-dr-layout="legacy-vertical"]')
        self.assertIsNotNone(menu)
        self.assertIn('dr-legacy-vertical-navigation', soup.body.get('class', []))
        self.assertEqual(len(soup.select('.dr-navigation')), 1)
        self.assertEqual([a.get_text() for a in menu.select(':scope > #dr-primary-menu > li > a')], ['Home', 'o projektu'])
        self.assertEqual(len(menu.select(':scope > #dr-primary-menu > .dr-casino')), 1)

    def test_narrow_flash_sidebar_is_preserved_as_vertical_html_menu(self):
        pages = [Page('home', '/', 'TOCOEN, s.r.o.', ''),
                 Page('registry', '/rejstrik.htm', 'TOCOEN (Výpis z obchodního rejstříku)', ''),
                 Page('cooperation', '/spoluprace.htm', 'TOCOEN (Spolupráce)', ''),
                 Page('contact', '/kontakt.htm', 'TOCOEN (Kontakt)', '')]
        pages += [Page(page.slug, page.route, page.title, '', casino=True)
                  for page in self.request.casino_pages]
        buttons = ''.join(f'''<object width="168" height="19" id="{name}">
          <param name="movie" value="images/{name}.swf"><embed src="images/{name}.swf" width="168" height="19" name="{name}"></embed></object>'''
                          for name in ('uvod', 'rejstrik', 'spoluprace', 'kontakt'))
        soup = parse_html(f'''<body><table><tr><td width="168" bgcolor="#55576B"><img width="168" height="21">{buttons}
          <div><hr class="menu">Company details</div></td><td><h1>Archived article</h1>
          <p>This is the full archived article content and it remains in the right column.</p></td></tr></table></body>''')
        removed = []
        clean(soup, 'cs-CZ', removed)
        self.assertFalse(soup.select('object,embed'))
        self.assertIsNotNone(soup.select_one('[data-dr-flash-menu-slot]'))
        navigation(soup, pages, self.request)
        menu = soup.select_one('td nav.dr-navigation[data-dr-placement="legacy-flash-menu"][data-dr-layout="legacy-dynamic"]')
        self.assertIsNotNone(menu)
        self.assertEqual([a.get_text() for a in menu.select(':scope > #dr-primary-menu > li > a')],
                         ['Home', 'Výpis z obchodního rejstříku', 'Spolupráce', 'Kontakt'])
        self.assertEqual(len(menu.select(':scope > #dr-primary-menu > .dr-casino')), 1)
        self.assertTrue(soup.select_one('table').get('class'))
        self.assertTrue(all('dr-legacy-width-table' in table.get('class', []) for table in soup.select('table')))
        self.assertFalse(primary_menu_issues(soup))

    def test_clean_removes_image_without_any_source(self):
        soup = parse_html('<body><p>Keep article</p><img width="121" height="73"></body>')
        removed = []
        clean(soup, 'cs-CZ', removed)
        self.assertIsNone(soup.img)
        self.assertIn({'kind': 'asset', 'detail': 'Пустое изображение без источника'}, removed)

    def test_clean_preserves_declared_image_ratio_for_legacy_spacers(self):
        soup = parse_html('<body><img src="/pixel.gif" width="650" height="11"></body>')
        clean(soup, 'cs-CZ', [])
        clean(soup, 'cs-CZ', [])
        self.assertEqual(soup.img.get('style'), 'aspect-ratio:650/11')

    def test_legacy_table_styles_cancel_obsolete_full_height_rows(self):
        css = (Path(__file__).parents[1] / 'drop_restorer' / 'templates' / 'drop-restorer.css').read_text(encoding='utf-8')
        self.assertIn('body.dr-legacy-table-layout :is(table,tbody,tr,td)[height="100%"]', css)
        self.assertIn('.dr-legacy-flash-menu-host > :not(.dr-navigation)', css)

    def test_cleaned_flash_sidebar_replaces_old_horizontal_fallback(self):
        soup = parse_html('''<body><nav class="dr-navigation" data-dr-placement="header-fallback">
          <ul id="dr-primary-menu"><li class="dr-casino">Casino</li></ul></nav><table><tr>
          <td width="168" bgcolor="#55576B"><img width="168" height="21"><div><hr class="menu">Details</div></td>
          <td><h1>Article</h1><p>Long restored article content remains here for the page owner.</p></td></tr></table></body>''')
        navigation(soup, self.pages, self.request)
        menu = soup.select_one('td nav.dr-navigation[data-dr-placement="legacy-flash-menu"]')
        self.assertIsNotNone(menu)
        self.assertEqual(len(soup.select('.dr-navigation')), 1)
        self.assertFalse(primary_menu_issues(soup))

    def test_casino_replaces_right_cell_and_keeps_flash_sidebar_shell(self):
        buttons = ''.join(f'''<object width="168" height="19" id="{name}">
          <param name="movie" value="images/{name}.swf"><embed src="images/{name}.swf" width="168" height="19"></embed></object>'''
                          for name in ('uvod', 'rejstrik', 'spoluprace', 'kontakt'))
        source = parse_html(f'''<body><table id="outer"><tr><td><table id="layout"><tr>
          <td width="168" bgcolor="#55576B"><img width="168" height="21">{buttons}<div><hr class="menu">Company</div></td>
          <td width="1"></td><td bgcolor="#FFFFEE"><h1>Archived heading</h1>
          <p>This right-hand content cell contains the complete original article and should be replaced alone.</p>
          <p>Additional text makes the article region unambiguous without selecting the surrounding table shell.</p></td>
          </tr></table></td></tr></table><footer>Footer</footer></body>''')
        clean(source, 'en-GB', [])
        result = casino_shell(source, 'Casino rating')
        navigation(result, self.pages, self.request)
        menu = result.select_one('td[width="168"] nav[data-dr-placement="legacy-flash-menu"]')
        article = result.select_one('td[bgcolor="#FFFFEE"].dr-casino-content')
        self.assertIsNotNone(menu)
        self.assertIsNotNone(article)
        self.assertEqual(article.select_one('h1').get_text(), 'Casino rating')
        self.assertIsNotNone(article.select_one('#dr-editor-content'))
        self.assertIsNotNone(result.select_one('#outer #layout'))
        self.assertEqual(result.footer.get_text(), 'Footer')
        self.assertFalse(primary_menu_issues(result))

    def test_casino_replaces_home_feed_when_article_cards_have_headers(self):
        source = parse_html('''<body><header id="masthead"><h1>Example site</h1><nav id="site-navigation"><a href="/">Home</a></nav></header>
          <div id="page"><div id="content" role="main"><article><header><h2>Old homepage story</h2></header>
          <p>Homepage teaser content that must not appear on a newly generated casino page.</p></article>
          <article><header><h2>Another old story</h2></header><p>Second homepage teaser remains only on the archive home page.</p></article></div></div>
          <footer id="colophon">Site footer</footer></body>''')
        result = casino_shell(source, 'Casino rating')
        content = result.select_one('#content.dr-casino-content')
        self.assertIsNotNone(content)
        self.assertEqual(content.select('article'), [])
        self.assertEqual(content.select('h1.dr-article-title')[0].get_text(), 'Casino rating')
        self.assertNotIn('Old homepage story', result.get_text())
        self.assertNotIn('Homepage teaser content', result.get_text())
        self.assertEqual(result.select_one('#masthead h1').get_text(), 'Example site')
        self.assertEqual(result.select_one('#colophon').get_text(), 'Site footer')
        self.assertFalse(casino_shell_issues(source, result))

    def test_shell_audit_fails_when_homepage_main_survives_outside_casino_content(self):
        source = parse_html('''<body><header id="masthead">Brand</header><nav id="site-navigation">Menu</nav>
          <div id="content" role="main"><article><h2>Home story</h2><p>Original homepage card content remains here.</p></article></div>
          <footer id="colophon">Footer</footer></body>''')
        broken = parse_html('''<body><header id="masthead">Brand</header><nav id="site-navigation">Menu</nav>
          <div id="content" role="main"><article><h2>Home story</h2><p>Original homepage card content remains here.</p></article></div>
          <div class="dr-casino-content" role="main"><h1 class="dr-article-title">Casino</h1><div id="dr-editor-content"></div></div>
          <footer id="colophon">Footer</footer></body>''')
        self.assertIn('content', {region for region, _ in casino_shell_issues(source, broken)})

    def test_article_ul_named_menu_is_not_promoted(self):
        soup = parse_html('''<header><nav><a href="/about/">About</a></nav></header><main>
          <ul id="menu"><li><a href="/chapter-1/" accesskey="1">Chapter 1</a></li>
          <li><a href="/chapter-2/" accesskey="2">Chapter 2</a></li></ul></main>''')
        navigation(soup, self.pages, self.request)
        self.assertIsNotNone(soup.select_one('header .dr-navigation'))
        self.assertEqual(soup.select_one('main #menu').get_text(' ', strip=True), 'Chapter 1 Chapter 2')

    def test_secondary_containers_never_receive_casino_even_with_primary_id(self):
        for context in ('aside', 'footer', 'div id="rightcol"', 'div id="secondary"', 'div role="complementary"'):
            tag = context.split()[0]
            soup = parse_html(f'<header><div id="logo">Logo</div></header><{context}><nav id="primary-nav">Secondary</nav></{tag}><main>Content</main>')
            navigation(soup, self.pages, self.request)
            menu = soup.select_one('.dr-navigation')
            self.assertEqual(menu.get('data-dr-placement'), 'header-fallback')
            self.assertEqual(menu.find_previous_sibling().name, 'header')
            self.assertEqual(soup.select_one('#primary-nav').get_text(), 'Secondary')
            self.assertFalse(primary_menu_issues(soup))

    def test_misplaced_generated_menu_is_removed_during_repair(self):
        soup = parse_html('<div id="header"><div class="topmenu"><a href="/about/">About</a></div></div>'
                          '<div id="rightcol"><nav class="dr-navigation"><ul id="dr-primary-menu"><li class="dr-casino">Casino</li></ul></nav>'
                          '<ul class="menu"><li>Keep sidebar text</li></ul></div>')
        self.assertTrue(primary_menu_issues(soup))
        for _ in range(2):
            navigation(soup, self.pages, self.request)
        self.assertFalse(primary_menu_issues(soup))
        self.assertEqual(len(soup.select('.dr-casino')), 1)
        self.assertEqual(soup.select_one('#rightcol').get_text(), 'Keep sidebar text')

    def test_separate_generated_menu_does_not_pass_when_real_topmenu_exists(self):
        soup = parse_html('<nav class="dr-navigation" aria-label="Main navigation"><ul id="dr-primary-menu"><li class="dr-casino">Casino</li></ul></nav>'
                          '<div id="header"><div class="topmenu"><a href="/about/">About</a></div></div>')
        self.assertTrue(any('отдельно' in issue for issue in primary_menu_issues(soup)))
        navigation(soup, self.pages, self.request)
        self.assertFalse(primary_menu_issues(soup))
        self.assertIsNotNone(soup.select_one('#header .topmenu .dr-casino'))

    def test_casino_keeps_complete_legacy_shell_and_primary_menu(self):
        source = parse_html('''<body class="hp"><div id="content-holder"><header id="header-main"><img id="logo" src="/assets/logo.png"></header>
          <div id="main-article"><div id="hp-carousel-content">Archived slide text</div></div>
          <header id="header-secondary"><nav id="primary-nav"><ul><li><a href="/about/">About</a></li></ul></nav></header></div>
          <footer>Original footer</footer></body>''')
        soup = casino_shell(source, 'Casino rating')
        navigation(soup, self.pages, self.request)
        with tempfile.TemporaryDirectory() as root:
            repair_layout(soup, Path(root))
        self.assertIsNotNone(soup.select_one('#content-holder #header-main #logo'))
        self.assertEqual(len(soup.select('#primary-nav .dr-submenu a')), 3)
        self.assertEqual(soup.select_one('#dr-editor-content').get_text(), '')
        self.assertEqual(soup.select_one('#main-article h1').get_text(), 'Casino rating')
        self.assertIsNone(soup.select_one('#hp-carousel-content'))
        self.assertEqual(soup.footer.get_text(), 'Original footer')
        self.assertEqual(soup.select_one('#header-main').find_next_sibling()['id'], 'header-secondary')
        self.assertIn('dr-school-layout', soup.body['class'])

    def test_casino_keeps_pre_html5_banner_and_vertical_menu(self):
        source = parse_html('''<body><div id="main"><div id="text"><h1>Old page</h1><div id="top">
          <strong id="logo">wifi-in</strong><p><strong class="print_hidden">menu</strong></p>
          <ul id="menu"><li><a href="/" accesskey="1">úvod</a></li><li><a href="/about/" accesskey="2">o projektu</a></li></ul>
          </div><p>Old body</p></div><div id="vypis">Old listing</div><div id="footer">Footer</div></div></body>''')
        soup = casino_shell(source, 'Casino rating')
        navigation(soup, self.pages, self.request)
        self.assertIsNotNone(soup.select_one('#text #top #logo'))
        self.assertIsNotNone(soup.select_one('#text nav#menu.dr-navigation'))
        self.assertEqual(soup.select_one('#text h1').get_text(), 'Casino rating')
        self.assertIsNotNone(soup.select_one('#text #dr-editor-content'))
        self.assertNotIn('Old body', soup.get_text())
        self.assertNotIn('Old listing', soup.get_text())
        self.assertFalse(casino_shell_issues(source, soup))

    def test_layout_adapter_does_not_alter_other_themes(self):
        soup = parse_html('<header>Different header</header><main><article>Content</article></main><footer>Footer</footer>')
        before = str(soup)
        with tempfile.TemporaryDirectory() as root:
            repair_layout(soup, Path(root))
        self.assertEqual(str(soup), before)

    def test_joomla_div_shell_preserves_banner_menu_footer_and_editor(self):
        source = parse_html('''<body><div id="ja-wrapper"><div id="ja-mainnav"><nav><a href="/about/">About</a></nav></div>
          <div id="ja-header">Original banner</div><div id="ja-mainbody"><div id="ja-content">Old news</div>
          <div id="ja-col2">Sponsor column</div></div><div id="ja-footer">Original footer</div></div></body>''')
        original = str(source)
        soup = casino_shell(source, 'Casino rating')
        navigation(soup, self.pages, self.request)
        with tempfile.TemporaryDirectory() as root:
            repair_layout(soup, Path(root))
        self.assertEqual(str(source), original)
        self.assertEqual(soup.select_one('#ja-header').get_text(), 'Original banner')
        self.assertEqual(soup.select_one('#ja-footer').get_text(), 'Original footer')
        self.assertEqual(soup.select_one('#ja-content h1').get_text(), 'Casino rating')
        self.assertEqual(soup.select_one('#dr-editor-content').get_text(), '')
        self.assertNotIn('Old news', soup.get_text())
        self.assertIsNone(soup.select_one('#ja-col2'))
        self.assertEqual(len(soup.select('#ja-mainnav .dr-submenu a')), 3)
        self.assertFalse(casino_shell_issues(source, soup))

    def test_unknown_content_region_adds_safe_editor_without_destroying_shell(self):
        soup = parse_html('<div id="legacy-brand">Brand</div><div id="unknown-panel">Story</div><div id="footer">Footer</div>')
        original = str(soup)
        result = casino_shell(soup, 'Casino')
        self.assertEqual(str(soup), original)
        self.assertEqual(result.select_one('#unknown-panel').get_text(), 'Story')
        self.assertEqual(result.select_one('#footer').get_text(), 'Footer')
        self.assertEqual(result.select_one('main[data-dr-content-source="generated-fallback"] h1').get_text(), 'Casino')
        self.assertIsNotNone(result.select_one('#dr-editor-content'))

    def test_felixnet_table_article_and_javascript_menu_are_recovered(self):
        source = parse_html('''<body onload="OldMenu.Draw()"><div class="infobarframebox_token" id="ib_token_placeholder">Loading ...</div>
          <table class="town_table_01"><tr><td><table class="town_table_03"><tr>
          <td><table class="town_table_04"><tr><td><div class="menuframebox_token" id="menuframe_7"></div></td></tr></table></td>
          <td><table class="town_table_05"><tr><td class="town_table_05_tr_01_td_01"><h1>Archived heading</h1>
          <p>This is the actual municipal article content and it is long enough to be identified safely.</p></td></tr>
          <tr><td><span class="linklistmenuframebox_token" id="menuframe_7"><a href="/">Archived home</a>
          <a href="/about/">Archived about</a><a href="/unused/">Unused</a></span></td></tr></table></td>
          </tr></table></td></tr></table><div id="footer">Footer</div></body>''')
        original = str(source)
        soup = casino_shell(source, 'Casino rating')
        navigation(soup, self.pages, self.request)
        self.assertEqual(str(source), original)
        menu = soup.select_one('.menuframebox_token.dr-navigation[data-dr-layout="legacy-dynamic"]')
        self.assertIsNotNone(menu)
        self.assertEqual([a.get_text() for a in menu.select(':scope > #dr-primary-menu > li > a')],
                         ['Home', 'Archived about'])
        self.assertEqual(len(menu.select(':scope > #dr-primary-menu > .dr-casino')), 1)
        self.assertIsNone(soup.select_one('.linklistmenuframebox_token'))
        self.assertIsNone(soup.select_one('.infobarframebox_token'))
        self.assertNotIn('onload', soup.body.attrs)
        article = soup.select_one('td.town_table_05_tr_01_td_01.dr-casino-content')
        self.assertEqual(article.select_one('h1').get_text(), 'Casino rating')
        self.assertIsNotNone(article.select_one('#dr-editor-content'))
        self.assertNotIn('Archived heading', article.get_text())
        self.assertIn('dr-legacy-table-casino', soup.body.get('class', []))
        self.assertTrue(soup.select('.dr-legacy-layout-table'))
        self.assertFalse(primary_menu_issues(soup))

    def test_graphical_table_casino_expands_content_shell_after_sidebars_are_removed(self):
        source = parse_html('''<body><table width="870"><tr><td><img width="870" height="100"></td></tr>
          <tr><td><nav class="dr-navigation" data-dr-placement="legacy-table-menu"><a href="/">Home</a>
          <a href="/about/">About</a></nav></td></tr>
          <tr><td><table width="870"><tr>
          <td width="135">Old left rail</td><td id="content" role="main" width="585"><h1>Archived heading</h1>
          <p>This is the original article content and is long enough to identify the centre cell.</p></td>
          <td width="135">Old right rail</td></tr></table></td></tr><tr><td>Footer</td></tr></table></body>''')
        result = casino_shell(source, 'Casino rating')
        article = result.select_one('.dr-casino-content')
        shell = result.select_one('.dr-casino-content-shell')
        self.assertIsNotNone(shell)
        self.assertIs(article.find_parent('table'), shell)
        self.assertNotIn('width', article.attrs)
        row = article.find_parent('tr')
        self.assertEqual(len(row.find_all('td', recursive=False)), 1)

    def test_shell_audit_detects_missing_legacy_landmarks(self):
        source = parse_html('<div id="ja-header">Banner</div><div id="ja-mainnav">Menu</div><div id="ja-footer">Footer</div>')
        broken = parse_html('<main class="dr-casino-content"><h1>Casino</h1><div id="dr-editor-content"></div></main>')
        self.assertEqual({region for region, _ in casino_shell_issues(source, broken)}, {'header', 'navigation', 'footer'})


if __name__ == '__main__':
    unittest.main()
