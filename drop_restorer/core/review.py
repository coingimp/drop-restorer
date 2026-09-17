"""Saved metadata review, independent of the network and the desktop session."""
from collections import Counter
import json

from .cleaner import parse_html
from .metadata import normalize
from .models import RestorationError


def content_node(soup):
    for selector in ('main', '[role="main"]', '#main-article', '#main-content', '#content', '#content-holder'):
        node = soup.select_one(selector)
        if node is not None:
            return node
    return soup.body


def page_label(soup, fallback):
    main = content_node(soup)
    heading = main.find('h1') if main is not soup.body else None
    if heading and (heading.get('id') == 'logo' or 'logo' in heading.get('class', [])):
        heading = None
    # A logo H1 is a site name, not a distinct page heading.
    node = heading or soup.title or soup.find('h1')
    return normalize(node.get_text(' ', strip=True) if node else fallback)[:140]


def source_metadata(soup):
    node = soup.find('meta', attrs={'name': lambda value: value and value.lower() == 'description'})
    return {'title': soup.title.get_text() if soup.title else '',
            'description': node.get('content', '') if node else ''}


def initialize_review(build):
    build.metadata_review = {page.key: {'original': source_metadata(parse_html(page.html))}
                             for page in build.pages if not page.casino}
    build.status = 'metadata_review'
    save_review(build)


def issues(build, variant='original'):
    values = {page.key: build.metadata_review[page.key].get(variant, {})
              for page in build.pages if not page.casino}
    titles = Counter(normalize(value.get('title', '')).casefold() for value in values.values())
    descriptions = Counter(normalize(value.get('description', '')).casefold() for value in values.values())
    result = {}
    for key, value in values.items():
        notes = []
        for field, label, minimum, maximum, counts in [
            ('title', 'Title', 30, 60, titles), ('description', 'Description', 100, 180, descriptions)
        ]:
            text = normalize(value.get(field, ''))
            if not text:
                notes.append(f'{label}: отсутствует')
            elif not minimum <= len(text) <= maximum:
                notes.append(f'{label}: {len(text)} символов, ориентир {minimum}–{maximum}')
            if text and counts[text.casefold()] > 1:
                notes.append(f'{label}: повторяется')
        if variant == 'proposed' and build.metadata_review[key].get('error'):
            notes.append(build.metadata_review[key]['error'])
        result[key] = notes
    return result


def require_review(build):
    if build.status != 'metadata_review' or not build.metadata_review:
        raise RestorationError('Сначала откройте скачанные страницы для проверки метаданных.')


def can_apply(build):
    return bool(build.metadata_review) and all('proposed' in row for row in build.metadata_review.values()) and not any(issues(build, 'proposed').values())


def save_review(build):
    report = {'status': build.status, 'decision': build.metadata_decision, 'pages': []}
    original_issues = issues(build)
    proposed_issues = issues(build, 'proposed')
    for page in build.pages:
        if page.casino:
            continue
        report['pages'].append({'page': page.title, 'route': page.route, 'source': page.source,
            'canonical': build.request.origin + page.route, **build.metadata_review[page.key],
            'original_issues': original_issues[page.key], 'proposed_issues': proposed_issues[page.key]})
    (build.root / 'metadata-review.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    build.save()
