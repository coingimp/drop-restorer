"""Published package pages are indexable independently of editorial review."""
import re
from bs4 import Comment


def robot_tags(soup):
    for node in list(soup.find_all('meta')):
        name = str(node.get('name', '')).casefold()
        header = str(node.get('http-equiv', '')).casefold()
        if (name in ('robots', 'googlebot', 'googlebot-news', 'bingbot', 'yandex', 'yandexbot', 'slurp')
                or name.endswith('bot') or header == 'x-robots-tag'):
            yield node


def open_indexation(soup):
    """Remove archived robot restrictions without removing the enclosed content."""
    for node in list(robot_tags(soup)):
        node.decompose()
    for node in list(soup.find_all('noindex')):
        node.unwrap()
    for node in list(soup.find_all(string=lambda value: isinstance(value, Comment))):
        if re.fullmatch(r'\s*/?noindex\s*', str(node), re.I):
            node.extract()
    return soup
