from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ThemeIdentity:
    domain: str
    base: str
    name: str
    slug: str
    text_domain: str
    accent_hue: int

    def manifest(self) -> dict:
        return {
            'domain': self.domain,
            'name': self.name,
            'slug': self.slug,
            'text_domain': self.text_domain,
            'accent_hue': self.accent_hue,
        }


def theme_identity(origin: str) -> ThemeIdentity:
    """Return stable, WordPress-safe theme details derived only from the site domain."""
    host = (urlsplit(origin).hostname or 'website').strip('.').lower()
    if host.startswith('www.'):
        host = host[4:]
    try:
        ascii_host = host.encode('idna').decode('ascii')
    except UnicodeError:
        ascii_host = host
    base = re.sub(r'[^a-z0-9]+', '', ascii_host.lower())[:48] or 'website'
    slug = f'{base}-theme'
    hue = int.from_bytes(hashlib.sha256(ascii_host.encode('utf-8')).digest()[:4], 'big') % 360
    return ThemeIdentity(
        domain=host,
        base=base,
        name=f'{base} theme',
        slug=slug,
        text_domain=slug,
        accent_hue=hue,
    )


NOT_FOUND_COPY = {
    'cs': ('Tato stránka neexistuje', 'Odkaz je pravděpodobně zastaralý nebo byla stránka přesunuta. Vraťte se na hlavní stránku a pokračujte odtud.', 'Zpět na hlavní stránku'),
    'de': ('Diese Seite wurde nicht gefunden', 'Der Link ist möglicherweise veraltet oder die Seite wurde verschoben. Kehren Sie zur Startseite zurück und setzen Sie Ihren Besuch dort fort.', 'Zur Startseite'),
    'es': ('Esta página no existe', 'Es posible que el enlace esté desactualizado o que la página se haya movido. Vuelve a la página principal para continuar.', 'Volver al inicio'),
    'fr': ('Cette page est introuvable', 'Le lien est peut-être obsolète ou la page a été déplacée. Revenez à la page d’accueil pour poursuivre votre visite.', 'Retour à l’accueil'),
    'it': ('Questa pagina non esiste', 'Il collegamento potrebbe essere obsoleto oppure la pagina è stata spostata. Torna alla pagina iniziale per continuare.', 'Torna alla home'),
    'pl': ('Ta strona nie istnieje', 'Link może być nieaktualny albo strona została przeniesiona. Wróć na stronę główną i kontynuuj przeglądanie.', 'Wróć na stronę główną'),
    'ru': ('Такой страницы нет', 'Возможно, ссылка устарела или страница была перемещена. Вернитесь на главную и продолжите просмотр сайта.', 'Вернуться на главную'),
    'en': ('This page could not be found', 'The link may be out of date or the page may have moved. Return to the home page and continue from there.', 'Return to the home page'),
}


def not_found_document(origin: str, lang: str, css: str) -> str:
    """Render the same domain-colored 404 in the local review server."""
    identity = theme_identity(origin)
    language = (lang or 'en').lower()[:2]
    title, message, home = NOT_FOUND_COPY.get(language, NOT_FOUND_COPY['en'])
    escaped = [html.escape(value) for value in (identity.domain, title, message, home, lang or 'en')]
    domain, title, message, home, html_lang = escaped
    return (f'<!doctype html><html lang="{html_lang}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>404</title><style>{css}</style></head><body>'
            f'<main class="site-not-found" style="--site-404-hue:{identity.accent_hue}"><section class="site-not-found__card" aria-labelledby="site-not-found-title">'
            f'<div class="site-not-found__glow" aria-hidden="true"></div><p class="site-not-found__domain">{domain}</p>'
            f'<p class="site-not-found__code" aria-hidden="true">404</p><h1 id="site-not-found-title">{title}</h1>'
            f'<p class="site-not-found__text">{message}</p><a class="site-not-found__home" href="/">'
            f'<span aria-hidden="true">←</span> {home}</a></section></main></body></html>')
