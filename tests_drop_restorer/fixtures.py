from io import BytesIO
from urllib.parse import urlsplit

from PIL import Image

from drop_restorer.core.models import CasinoPage, RestorationError, RestoreRequest
from drop_restorer.core.metadata import shorten


STAMP = 'https://web.archive.org/web/20200101000000id_/https://example.com'


def source(title, text, second=False):
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title>
    <link rel="canonical" href="https://web.archive.org/wrong"><link rel="stylesheet" href="/css/site.css">
    <script src="/js/menu.js"></script><script>gtag('config','TEST');</script></head>
    <body class="donor-layout"><header><img class="logo" src="/images/logo.png" alt="Test archive logo">
    <nav><ul class="menu"><li><a href="/">Home</a></li><li><a href="/about/">About</a><ul><li><a href="/hidden/">Hidden</a></li></ul></li></ul></nav></header>
    <main><h1>{title}</h1><p>{text}</p><p><a href="https://external.example/">External label</a> <a href="/about/">About</a></p>
    <a href="https://social.example/"><img src="/images/logo.png" alt="Social icon"></a>
    <div class="language-switcher"><a href="/de/">Deutsch</a></div><div class="ads">Advertisement</div>
    <div class="banner">Editorial image remains</div><div style="background-image:url('/images/logo.png')">CSS image</div>
    <form action="https://example.com/submit"><input name="email"><button>Send</button></form></main>
    <footer>Archive example footer</footer></body></html>'''


HOME_TEXT = 'This local demonstration preserves the original site layout and its selected pages. Visitors can inspect the navigation, images, readable text and the mobile presentation before approving the restored project.'
ABOUT_TEXT = 'The about page describes a small example organisation and the information it shares with visitors. This distinct archived paragraph supplies enough original material for page-specific metadata without inventing additional facts.'


class FixtureArchive:
    def __init__(self):
        image = Image.new('RGB', (96, 44), '#176da0')
        memory = BytesIO()
        image.save(memory, format='PNG')
        self.resources = {
            '/': (source('A local archive demonstration', HOME_TEXT).encode(), 'text/html'),
            '/about/': (source('About the example organisation', ABOUT_TEXT, True).encode(), 'text/html'),
            '/css/site.css': (b'body{font-family:Arial;background:#faf7f0;color:#172333;margin:0}header,main,footer{padding:24px}header{background:#e4eef6}nav ul{display:flex;gap:18px;list-style:none}nav a{color:#172333}main{max-width:960px;margin:auto}.hero{background:url(../images/logo.png)}', 'text/css'),
            '/images/logo.png': (memory.getvalue(), 'image/png'),
            '/favicon.ico': (memory.getvalue(), 'image/png'),
            '/js/menu.js': (b'document.documentElement.dataset.archivedScript="loaded";', 'text/javascript'),
        }
        self.seen = []

    def get(self, url, limit=0):
        self.seen.append(url)
        original = url.split('id_/', 1)[1]
        path = urlsplit(original).path
        if path not in self.resources:
            raise RestorationError('Нет такого тестового ресурса.')
        return self.resources[path]

    def check_cancel(self):
        pass

    def close(self):
        pass


def request():
    return RestoreRequest(STAMP + '/', [STAMP + '/about/'], 'en-GB', 'restored.example',
        [CasinoPage('Casino rating', 'casino-rating'), CasinoPage('Casino bonuses', 'casino-bonuses'), CasinoPage('Player reviews', 'casino-reviews')], 'Casino')


class FixtureAgent:
    def metadata(self, title, content, lang):
        return {'title': shorten(title + ' — archived information', 60), 'description': shorten(content, 180)}


def completed_fixture(output_root, client=None):
    from drop_restorer.core.pipeline import Pipeline
    client = client or FixtureArchive()
    pending = Pipeline(output_root, client=client).run(request())
    pending = Pipeline(output_root, client=client, agent=FixtureAgent()).suggest_metadata(pending)
    build = Pipeline(output_root, client=client).finish(pending, 'agent')
    attest_fixture(build)
    return Pipeline(output_root, client=client).build_theme(build)


def attest_fixture(build):
    """Simulate the owner ONLY for known synthetic test content, never real sites."""
    from drop_restorer.core.checklist import inspect_site, approve_site
    assert build.request.origin == 'https://restored.example'
    simulated_browser_fixture(build)
    report = inspect_site(build)
    # Empty owner casino fields remain real findings, explicitly accepted only
    # in this synthetic fixture to exercise downstream export/install behavior.
    build.seo_policy['decisions'] = {row['id']:{'fingerprint':report['fingerprint'],'decision':'confirm',
        'note':'Synthetic test fixture review', 'by':'test-fixture'} for row in report['items'] if row['manual']}
    build.save()
    previous_status = build.status
    build.status = 'site_review'
    approve_site(build, report['fingerprint'])
    build.status = previous_status
    build.save()


def simulated_browser_fixture(build):
    """Unit-test evidence only; real browser coverage is a separate smoke test."""
    from drop_restorer.core.seo_contract import fingerprint, save_json
    save_json(build.root/'visual-checks.json', {'fingerprint':fingerprint(build),'complete':True,'fixture_only':True,
        'pages':[{'route':page.route,'device':device,'overflow':0,'images_outside':[], 'broken_images':[],
                  'unreadable':[], 'menu_failed':False} for page in build.pages for device in ('Desktop','Tablet','Mobile')]})
    return build


def export_fixture(build):
    """Exercise low-level WXR serialization even for deliberately invalid SEO."""
    from drop_restorer.core.wordpress import write_site
    assert build.request.origin == 'https://restored.example'
    build.status = 'ready'
    write_site(build)
    return build
