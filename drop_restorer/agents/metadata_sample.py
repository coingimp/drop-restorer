"""One-page metadata trial, independent of the site's approval and build stage."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json

from ..core.cleaner import parse_html
from ..core.metadata import normalize
from ..core.models import RestorationError
from ..core.review import content_node, issues, source_metadata
from ..core.usage import metered, save


def source_digest(page, lang):
    return hashlib.sha256(json.dumps([page.html, page.title, lang], ensure_ascii=False).encode('utf-8')).hexdigest()


def read_sample(build):
    try:
        result = json.loads((build.root / 'metadata-sample.json').read_text(encoding='utf-8'))
        page = next(p for p in build.pages if p.key == result['key'] and not p.casino)
        result['stale'] = result['source_digest'] != source_digest(page, build.request.lang)
        return result
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None


def generate_sample(build, page, agent, cancel):
    if page.casino or not any(p.key == page.key and not p.casino for p in build.pages):
        raise RestorationError('Для пробы выберите восстановленную страницу. Метаданные казино задаёт владелец.')
    soup = parse_html(page.html)
    result = {
        'key': page.key, 'route': page.route, 'page_title': page.title, 'lang': build.request.lang,
        'model': agent.config.model, 'at': datetime.now(timezone.utc).isoformat(),
        'source_digest': source_digest(page, build.request.lang), 'original': source_metadata(soup),
        'applied': False, 'status': 'pending',
    }
    path = build.root / 'metadata-sample.json'
    save(path, result)
    try:
        if cancel.is_set():
            raise RestorationError('Пробная генерация отменена.')
        with metered(agent, build.root):
            proposed = agent.metadata(page.title, normalize(content_node(soup).get_text(' ', strip=True)),
                                      build.request.lang, operation='metadata_sample')
        if cancel.is_set():
            raise RestorationError('Пробная генерация отменена. Вызов агента учтён в расходах.')
        result['proposed'] = {field: normalize(proposed[field]) for field in ('title', 'description')}
        candidates = {p.key: {'proposed': source_metadata(parse_html(p.html))} for p in build.pages if not p.casino}
        candidates[page.key]['proposed'] = result['proposed']
        result['issues'] = issues(replace(build, metadata_review=candidates), 'proposed')[page.key]
        result['status'] = 'received'
    except RestorationError as error:
        result.update(status='failed', error=str(error))
        raise
    finally:
        save(path, result)
    return result
