"""Shared, offline rules for removal and independent package inspection."""
import re

JUNK = re.compile(r'^(?:ads?|adsense|advertisement|advertising|banner|ad-banner|banner-ad|promo|popup|modal|overlay|cookie-banner|cookie-consent|language-switcher|lang-switch|locale-selector|share-buttons|social-share)(?:[-_].*)?$', re.I)
TRACKER = re.compile(r'google-analytics\.com|googletagmanager\.com|mc\.yandex\.|connect\.facebook\.net|hotjar\.|doubleclick\.net|adservice\.google|facebook\.com/tr', re.I)
WIDGET = re.compile(r'(?:maps\.googleapis\.com/maps/api/js|apis\.google\.com/js/plusone\.js)', re.I)
TRACKER_CODE = re.compile(r'google-analytics|googletagmanager|connect\.facebook\.net|\bgtag\s*\(|\bga\s*\(\s*[\"\'](?:create|send)|\bym\s*\(\s*\d|\bfbq\s*\(|hotjar|__wm\.|wombat\.js|archive_analytics', re.I)
REMOTE_LOADER = re.compile(r'''(?:\.src\s*=\s*|\b(?:fetch|importScripts)\s*\(\s*)["'](?:https?:)?//[^"']+''', re.I)


def script_problem(code='', source=''):
    if TRACKER.search(source) or TRACKER_CODE.search(code):
        return 'Отслеживание или архивный загрузчик'
    if WIDGET.search(source) or WIDGET.search(code):
        return 'Внешний виджет Google Maps / Google+'
    if REMOTE_LOADER.search(code):
        return 'Загрузчик внешнего ресурса'
    return ''


def foreign_language(value, selected):
    return bool(value and value.replace('_', '-').split('-')[0].lower() != selected.split('-')[0].lower())
