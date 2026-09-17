"""Representative archived CMS HTML; no CMS engine or external network is used."""
from collections import Counter
from io import BytesIO
import ssl
import threading
from urllib.parse import urlsplit

import requests
from requests.adapters import BaseAdapter

from tests_drop_restorer.fixtures import FixtureArchive, HOME_TEXT, ABOUT_TEXT, request


CMS = {
    'wordpress': ('<header id="masthead">{nav}</header><div id="content"><main id="main">{article}</main></div><footer>{footer}</footer>', '/about/'),
    'joomla15': ('<div id="ja-wrapper"><div id="ja-header">{brand}</div><div id="ja-mainnav">{nav}</div><div id="ja-container"><div id="ja-content">{article}</div></div><div id="ja-footer">{footer}</div></div>', '/index.php?option=com_content&view=article&id=2'),
    'joomla3': ('<header class="header">{nav}</header><div class="body"><div class="container"><main id="content" role="main">{article}</main></div></div><footer>{footer}</footer>', '/about.html'),
    'drupal7': ('<div id="page"><div id="header">{nav}</div><div id="main-wrapper"><div id="main"><div id="content"><div class="region region-content">{article}</div></div></div></div><div id="footer">{footer}</div></div>', '/node/2'),
    'bitrix': ('<div id="header">{nav}</div><div class="page"><div id="content"><div class="bx-content">{article}</div></div></div><div id="footer">{footer}</div>', '/about/index.php'),
    'modx': ('<header>{nav}</header><div class="layout"><article id="content">{article}</article></div><footer>{footer}</footer>', '/o-projekte.html'),
    'dle': ('<div id="header">{nav}</div><div class="wrapper"><div id="dle-content">{article}</div></div><div id="footer">{footer}</div>', '/index.php?do=static&page=about'),
    'typo3': ('<div id="page"><header>{nav}</header><div id="page-content"><div id="content"><div class="csc-default">{article}</div></div></div><footer>{footer}</footer></div>', '/index.php?id=2'),
}


def cms_archive(cms, assets=0):
    archive = FixtureArchive()
    wrapper, about = CMS[cms]
    fixture_request = request()
    fixture_request.menu_pages = [fixture_request.menu_pages[0].rsplit('/about/', 1)[0] + about]
    resources = {k: v for k, v in archive.resources.items() if k not in ('/', '/about/')}
    logo = '<img src="/images/logo.png" alt="Fixture logo" width="96" height="44">'
    for index, route in enumerate(('/', about)):
        title = f'{cms} ' + ('Archive home' if index == 0 else 'About this archive')
        text = HOME_TEXT if index == 0 else ABOUT_TEXT
        nav = f'{logo}<ul class="mainmenu"><li><a href="/">Home</a></li><li><a href="{about}">About</a></li></ul>'
        extra = ''.join(f'<img src="/images/load-{n}.png" alt="Load fixture {n}" width="96" height="44">' for n in range(assets)) if index == 0 else ''
        article = f'<h1>{title}</h1><p>{text}</p><h2>Archive details</h2><p>{text}</p><ul><li>First point</li><li>Second point</li></ul><table><tbody><tr><th>Item</th><th>Value</th></tr><tr><td>Fixture</td><td>{cms}</td></tr></tbody></table>{extra}'
        body = wrapper.format(nav=nav, article=article, footer='Original fixture footer', brand=logo)
        html = f'<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><meta name="generator" content="{cms}"><title>{title}</title><meta name="description" content="{text[:170]}"><link rel="stylesheet" href="/css/site.css"><link rel="icon" href="/favicon.ico"></head><body>{body}</body></html>'
        resources[route] = (html.encode(), 'text/html')
    resources['/css/site.css'] = (b'body{margin:0;color:#172333;background:#faf7f0;font:17px/1.6 Arial}header,#header,#ja-header,#ja-mainnav,footer,#footer,#ja-footer{padding:16px;background:#e4eef6}main,article,#content,#ja-content,#dle-content{max-width:960px;padding:20px;margin:auto}img{max-width:100%;height:auto}table{border-collapse:collapse;max-width:100%}td,th{padding:10px;border:1px solid #bbb}.mainmenu{display:flex;gap:20px;list-style:none;padding:0;margin:0}#page,#ja-wrapper,.wrapper,.layout{max-width:100%}', 'text/css')
    for n in range(assets):
        resources[f'/images/load-{n}.png'] = resources['/images/logo.png']
    return fixture_request, resources, about


class FastCancel(threading.Event):
    """Virtualise only retry delays in deterministic fault tests."""
    def __init__(self):
        super().__init__()
        self.delays = []

    def wait(self, timeout=None):
        self.delays.append(timeout)
        return self.is_set()


class BrokenStream(BytesIO):
    def stream(self, chunk_size, decode_content=True):
        yield b'<html>PARTIAL-MUST-NOT-BE-SAVED'
        raise requests.exceptions.ChunkedEncodingError('Synthetic stream interrupted')


class FaultAdapter(BaseAdapter):
    """Inject faults below ArchiveClient at the Requests transport boundary."""
    def __init__(self, resources, target, fault='baseline', cancel=None):
        self.resources, self.target, self.fault = resources, target, fault
        self.calls = Counter()
        self.urls = []
        self.cancel = cancel

    def send(self, request, **kwargs):
        assert urlsplit(request.url).hostname == 'web.archive.org', 'External network forbidden in fixture'
        original = request.url.split('id_/', 1)[1]
        parsed = urlsplit(original)
        route = parsed.path + ('?' + parsed.query if parsed.query else '')
        self.calls[route] += 1
        self.urls.append(request.url)
        number = self.calls[route]
        active = route == self.target
        fault = self.fault if active else 'baseline'
        if fault == 'tls_permanent' or fault == 'tls_once' and number == 1:
            raise requests.exceptions.SSLError(ssl.SSLEOFError(8, 'UNEXPECTED_EOF_WHILE_READING'))
        if fault == 'certificate':
            raise requests.exceptions.SSLError(ssl.SSLCertVerificationError(1, 'CERTIFICATE_VERIFY_FAILED'))
        if fault == 'timeout_permanent' or fault == 'timeout_once' and number == 1:
            raise requests.ReadTimeout('Synthetic timeout')
        if fault == 'connection_once' and number == 1:
            raise requests.ConnectionError('Synthetic connection reset')
        if fault == 'cancel':
            self.cancel.set()
            raise requests.ReadTimeout('Cancelled fixture request')
        response = requests.Response()
        response.request, response.url = request, request.url
        response.status_code = 200 if route in self.resources else 404
        body, mime = self.resources.get(route, (b'Not found', 'text/plain'))
        response.headers['Content-Type'] = mime
        if fault in ('http429', 'http503', 'http520') and number == 1:
            response.status_code = int(fault[4:])
            response.headers['Retry-After'] = '7'
        if fault in ('page404', 'missing_image'):
            response.status_code = 404
        if fault == 'redirect_outside':
            response.status_code = 302
            response.headers['Location'] = 'https://outside.invalid/forbidden'
        if fault == 'redirect_loop':
            response.status_code = 302
            response.headers['Location'] = request.url
        if fault == 'empty_html':
            body = b'<html><head></head><body></body></html>'
        if fault in ('html_css', 'service_html'):
            body = b'<html><head><title>Wayback Machine</title></head><body>Service unavailable</body></html>'
            response.headers['Content-Type'] = 'text/html'
        if fault == 'corrupt_image':
            body = b'THIS IS NOT AN IMAGE'
        response.raw = BrokenStream() if fault == 'partial_once' and number == 1 else BytesIO(body)
        response._content_consumed = False
        return response

    def close(self):
        pass
