from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import langcodes


class RestorationError(ValueError):
    """Actionable error safe to display in the desktop UI."""


def host_name(host: str) -> str:
    try:
        host = host.rstrip('.').encode('idna').decode('ascii').lower()
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise RestorationError('Укажите доменное имя сайта, не IP-адрес.')
    if '.' not in host or len(host) > 253 or not all(
        re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
        for part in host.split('.')
    ):
        raise RestorationError('Некорректное доменное имя.')
    return host


def site_origin(value: str) -> str:
    parsed = urlsplit(value if '://' in value else 'https://' + value)
    if (parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
            or parsed.port or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise RestorationError('Финальный домен: укажите домен без пути и порта.')
    return parsed.scheme + '://' + host_name(parsed.hostname or '')


def route_for(url: str) -> str:
    parsed = urlsplit(url)
    path = unquote(parsed.path or '/')
    if '\\' in path or any(x in ('.', '..') for x in path.split('/')) or any(ord(x) < 32 for x in path):
        raise RestorationError('Недопустимый путь страницы.')
    return quote(path, safe="/!$&'()*+,;=:@-._~") + ('?' + parsed.query if parsed.query else '')


def latin_url_path(route: str) -> str:
    """Remove Latin accents in final paths, never in archive fetch URLs or queries."""
    path, separator, query = route.partition('?')
    segments = []
    for segment in path.split('/'):
        result = []
        latin = False
        decoded = unquote(segment).translate(str.maketrans({'ł':'l', 'Ł':'L', 'ø':'o', 'Ø':'O', 'đ':'d', 'Đ':'D'}))
        for char in unicodedata.normalize('NFD', decoded):
            if unicodedata.combining(char):
                if not latin:
                    result.append(char)
            else:
                latin = 'LATIN' in unicodedata.name(char, '')
                result.append(char)
        segments.append(quote(unicodedata.normalize('NFC', ''.join(result)), safe="!$&'()*+,;=:@-._~"))
    return '/'.join(segments) + separator + query


@dataclass(frozen=True)
class Snapshot:
    timestamp: str
    original: str

    @classmethod
    def parse(cls, value: str) -> 'Snapshot':
        parsed = urlsplit(value.strip())
        if (parsed.scheme not in ('http', 'https') or parsed.hostname != 'web.archive.org'
                or parsed.username or parsed.password or parsed.port):
            raise RestorationError('Каждая ссылка должна вести на web.archive.org и содержать дату снимка.')
        match = re.fullmatch(r'/web/(\d{8}|\d{14})(?:[a-z]{1,3}_)?/(https?://.+)', parsed.path)
        if not match:
            raise RestorationError('Нужна ссылка на конкретный снимок Wayback с датой из 8 или 14 цифр.')
        stamp, original = match.groups()
        try:
            datetime.strptime(stamp, '%Y%m%d' if len(stamp) == 8 else '%Y%m%d%H%M%S')
        except ValueError as error:
            raise RestorationError('В ссылке указана некорректная дата снимка.') from error
        original += ('?' + parsed.query) if parsed.query else ''
        source = urlsplit(original)
        if source.username or source.password or source.port:
            raise RestorationError('Архивный адрес не должен содержать пароль или порт.')
        host_name(source.hostname or '')
        route_for(original)
        return cls(stamp, urlunsplit((source.scheme, source.netloc, source.path or '/', source.query, '')))

    @property
    def archive_url(self) -> str:
        return f'https://web.archive.org/web/{self.timestamp}id_/{self.original}'


@dataclass(frozen=True)
class CasinoPage:
    title: str
    slug: str

    @property
    def route(self) -> str:
        return latin_url_path('/' + self.slug.strip('/') + '/')


DEFAULT_CASINO = (
    CasinoPage('Рейтинг казино', 'casino-rating'),
    CasinoPage('Бонусы казино', 'casino-bonuses'),
    CasinoPage('Отзывы игроков', 'casino-reviews'),
)


@dataclass
class RestoreRequest:
    main_page: str
    menu_pages: list[str]
    lang: str = 'cs-CZ'
    final_domain: str = ''
    casino_pages: list[CasinoPage] = field(default_factory=lambda: list(DEFAULT_CASINO))
    casino_label: str = 'Казино'

    def validate(self) -> list[Snapshot]:
        if not self.menu_pages:
            raise RestorationError('Добавьте хотя бы одну архивную страницу меню.')
        snapshots = [Snapshot.parse(x) for x in [self.main_page, *self.menu_pages]]
        donor = (urlsplit(snapshots[0].original).hostname or '').removeprefix('www.')
        if any((urlsplit(s.original).hostname or '').removeprefix('www.') != donor for s in snapshots):
            raise RestorationError('Все страницы должны относиться к одному архивному сайту.')
        if not self.lang or not langcodes.tag_is_valid(self.lang) or '_' in self.lang:
            raise RestorationError('Укажите корректный языковой тег BCP 47, например cs-CZ.')
        self.lang = langcodes.standardize_tag(self.lang)
        if self.final_domain:
            site_origin(self.final_domain.strip())
        if len(self.casino_pages) != 3 or not self.casino_label.strip():
            raise RestorationError('Задайте название раздела казино и три страницы.')
        routes = ['/'] + [route_for(s.original) for s in snapshots[1:]]
        originals = [s.original for s in snapshots]
        if len(set(originals)) != len(originals) or len(set(routes)) != len(routes):
            raise RestorationError('В списке есть повторяющиеся страницы или главная страница.')
        for page in self.casino_pages:
            slug = unicodedata.normalize('NFC', unquote(page.slug.strip('/')))
            if not page.title.strip() or not re.fullmatch(r'[\w-]+', slug, re.UNICODE):
                raise RestorationError('У казино-страниц нужны названия и слаги из букв, цифр и дефисов.')
            routes.append(page.route)
        routes = [latin_url_path(route) for route in routes]
        if len({r.split('?')[0].rstrip('/') or '/' for r in routes if '?' not in r}) != len([r for r in routes if '?' not in r]):
            raise RestorationError('Слаги казино и пути восстановленных страниц не должны совпадать.')
        return snapshots

    @property
    def origin(self) -> str:
        if self.final_domain.strip():
            return site_origin(self.final_domain.strip())
        parsed = urlsplit(Snapshot.parse(self.main_page).original)
        return f'{parsed.scheme}://{parsed.netloc}'


@dataclass
class Page:
    key: str
    route: str
    title: str
    html: str
    source: str = ''
    casino: bool = False
    seo_title: str = ''
    description: str = ''


@dataclass
class Build:
    root: Path
    request: RestoreRequest
    pages: list[Page] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    approved_digest: str | None = None
    status: str = 'ready'
    metadata_review: dict = field(default_factory=dict)
    metadata_decision: str = ''
    cleanup: list[dict] = field(default_factory=list)
    seo_policy: dict = field(default_factory=dict)
    owner_approvals: dict = field(default_factory=dict)

    def save(self) -> None:
        data = {'request': asdict(self.request), 'pages': [asdict(p) for p in self.pages],
                'warnings': self.warnings, 'approved_digest': self.approved_digest,
                'status': self.status, 'metadata_review': self.metadata_review,
                'metadata_decision': self.metadata_decision, 'cleanup': self.cleanup, 'seo_policy': self.seo_policy,
                'owner_approvals': self.owner_approvals}
        temporary = self.root / 'project.json.tmp'
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.root / 'project.json')

    @classmethod
    def load(cls, root: Path) -> 'Build':
        data = json.loads((root / 'project.json').read_text(encoding='utf-8'))
        request = data['request']
        request['casino_pages'] = [CasinoPage(**page) for page in request['casino_pages']]
        request = RestoreRequest(**request)
        request.validate()
        return cls(root, request, [Page(**page) for page in data['pages']], data.get('warnings', []),
                   data.get('approved_digest'), data.get('status', 'ready' if (root / 'content.xml').exists() else 'incomplete'),
                   data.get('metadata_review', {}), data.get('metadata_decision', ''), data.get('cleanup', []), data.get('seo_policy', {}),
                   data.get('owner_approvals', {}))

    def digest(self) -> str:
        digest = hashlib.sha256()
        if self.seo_policy:
            digest.update(json.dumps(self.seo_policy, ensure_ascii=False, sort_keys=True).encode())
        for folder in ('theme', 'pages'):
            for path in sorted((self.root / folder).rglob('*')):
                if path.is_file():
                    digest.update(path.relative_to(self.root).as_posix().encode())
                    digest.update(path.read_bytes())
        digest.update((self.root / 'content.xml').read_bytes())
        digest.update((self.root / 'README.md').read_bytes())
        return digest.hexdigest()
