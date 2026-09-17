from __future__ import annotations

import logging
import json
import hashlib
import threading
from importlib.resources import files
from urllib.parse import quote

from ..core.seo_routes import route_key

from flask import Flask, abort, request, send_from_directory, redirect, Response
from werkzeug.serving import make_server, WSGIRequestHandler


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


class PreviewServer:
    def __init__(self, build, parent_origin=None, audit_mode=False):
        self.build = build
        app = Flask('drop-restorer-preview', static_folder=None)
        app.logger.disabled = True

        def not_found_response():
            from ..core.theme_identity import not_found_document
            restore_request = getattr(build, 'request', None)
            origin = getattr(restore_request, 'origin', 'https://website.example')
            lang = getattr(restore_request, 'lang', 'en')
            css = files('drop_restorer').joinpath('templates/site-404.css').read_text(encoding='utf-8')
            return Response(not_found_document(origin, lang, css), status=404, mimetype='text/html')

        @app.before_request
        def local_host():
            if request.host != f'127.0.0.1:{self.port}':
                abort(403)

        @app.after_request
        def headers(response):
            response.headers['Cache-Control'] = 'no-store'
            if not audit_mode:
                response.headers['X-Robots-Tag'] = 'noindex, nofollow'
            response.headers['Content-Security-Policy'] = "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-src 'none'; form-action 'none'; base-uri 'none'"
            return response

        @app.get('/assets/<path:path>')
        def assets(path):
            return send_from_directory(build.root / 'theme' / 'assets', path)

        @app.get('/__drop_restorer_preview_bridge.js')
        def bridge():
            if not parent_origin:
                abort(404)
            script = "window.addEventListener('load',()=>parent.postMessage({type:'drop-restorer-page',route:location.pathname+location.search}," + json.dumps(parent_origin) + "));"
            return script, 200, {'Content-Type': 'application/javascript; charset=utf-8'}

        @app.get('/', defaults={'path': ''})
        @app.get('/<path:path>')
        def page(path):
            # WSGI request.path is already decoded. Retain the original URI so
            # encoded diacritics and literal percent signs are decoded once.
            key = request.environ.get('RAW_URI') or request.environ.get('REQUEST_URI') or (
                quote(request.path, safe="/:@!$&'()*+,;=-._~") +
                ('?' + request.query_string.decode('ascii', errors='replace') if request.query_string else ''))

            def metadata_preview_markup(item):
                """Render the saved page with a deterministic menu before finish().

                Metadata review intentionally precedes the normal preparation pass,
                but the owner still needs to see the complete shell. Work on a copy
                so opening the preview never changes the saved source page.
                """
                from ..core.cleaner import navigation, parse_html
                from ..core.models import Page
                from ..core.wordpress import write_theme_assets
                soup = parse_html(item.html)
                pages = list(build.pages)
                existing = {page.route for page in pages}
                for casino in build.request.casino_pages:
                    if casino.route in existing:
                        continue
                    casino_key = hashlib.sha256(casino.route.encode()).hexdigest()[:16]
                    pages.append(Page(casino_key, casino.route, casino.title, '', casino=True))
                navigation(soup, pages, build.request)
                write_theme_assets(build.root / 'theme')
                from ..core.casino_layout import apply_to_soup
                apply_to_soup(soup, build.seo_policy)
                return str(soup)
            if build.seo_policy and build.status != 'metadata_review':
                from ..core.seo_routes import resolve, robots, sitemap
                if request.path == '/robots.txt':
                    return Response(robots(build), mimetype='text/plain')
                if request.path == '/sitemap.xml':
                    return Response(sitemap(build), mimetype='application/xml')
                if request.path.startswith('/go/'):
                    from ..core.affiliates import validate_target, resolve_offer
                    try:
                        manifest = json.loads((build.root / 'affiliate-manifest.json').read_text(encoding='utf-8'))
                    except (ValueError, OSError):
                        manifest = {}
                    alias = request.path[len('/go/'):].strip('/')
                    offer = resolve_offer(manifest.get('offers', []), alias, self.origin, request.referrer or '')
                    if not offer:
                        return Response('Unknown or ambiguous offer', status=404)
                    target = offer['target']
                    validate_target(target)
                    if request.path != '/go/' + alias or request.query_string:
                        return Response('', status=301, headers={'Location':self.origin + '/go/' + alias})
                    return Response('', status=302, headers={'Location':target, 'Cache-Control':'no-store'})
                status, location = resolve(build, key)
                if status == 301:
                    return redirect(location, code=301)
                if status == 404:
                    return not_found_response()
            for item in sorted(build.pages, key=lambda item: '?' not in item.route):
                if route_key(item.route) == route_key(key) or ('?' not in item.route and route_key(item.route)[0] == route_key(key)[0]):
                    widget_html = None
                    if item.casino and build.seo_policy.get('widget_id'):
                        try:
                            manifest = json.loads((build.root / 'affiliate-manifest.json').read_text(encoding='utf-8'))
                            table = next((t for t in manifest.get('tables',[]) if t['route']==item.route),None)
                            if table:
                                from ..core.cleaner import parse_html
                                from bs4 import BeautifulSoup
                                html = (build.root / 'pages' / (item.key + '.html')).read_text(encoding='utf-8')
                                soup = parse_html(html);slot=soup.select_one('#dr-editor-content')
                                if slot is not None:
                                    fragment=BeautifulSoup('<div class="dr-casino-table">'+table['html']+'</div>','html.parser')
                                    slot.append(fragment)
                                for style in table.get('styles',[]):
                                    if style:
                                        soup.head.append(soup.new_tag('link',rel='stylesheet',href=style))
                                widget_html=str(soup)
                        except (OSError,ValueError):
                            pass
                    if parent_origin:
                        html = widget_html or (metadata_preview_markup(item) if build.status == 'metadata_review' else (build.root / 'pages' / (item.key + '.html')).read_text(encoding='utf-8'))
                        # The bridge exists only in the browser response, never in the export.
                        return html.replace('</head>', '<script defer src="/__drop_restorer_preview_bridge.js"></script></head>', 1)
                    if build.status == 'metadata_review':
                        return metadata_preview_markup(item)
                    if widget_html:
                        return widget_html
                    return send_from_directory(build.root / 'pages', item.key + '.html')
            return not_found_response()

        self.app = app
        self.http = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
        self.port = self.http.server_port
        self.origin = f'http://127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True, name='drop-restorer-preview')
        self.thread.start()

    def stop(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)
