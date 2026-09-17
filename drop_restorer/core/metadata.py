import re

from .models import Page, RestorationError


def normalize(value) -> str:
    return re.sub(r'\s+', ' ', value).strip() if isinstance(value, str) else ''


def shorten(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    result = value[:maximum + 1].rsplit(' ', 1)[0]
    return result if len(result) >= maximum // 2 else value[:maximum]


def metadata(soup, page: Page, lang: str, used_titles: set, used_descriptions: set, warn, agent=None):
    title = normalize(soup.title.get_text(' ', strip=True) if soup.title else '')
    node = soup.find('meta', attrs={'name': 'description'})
    description = normalize(node.get('content', '') if node else '')
    content = normalize((soup.find('main') or soup.body).get_text(' ', strip=True))
    valid_title = 30 <= len(title) <= 60 and title not in used_titles
    valid_description = 100 <= len(description) <= 180 and description not in used_descriptions
    generated = {}
    if agent and not (valid_title and valid_description):
        try:
            generated = agent.metadata(page.title, content, lang)
        except RestorationError as error:
            warn(f'{page.title}: {error} Используются данные страницы.')
    if not valid_title:
        candidates = [normalize(generated.get('title')), shorten(f'{page.title} — {content}', 60),
                      shorten(f'{page.title} — {page.route} — {content}', 60)]
        title = next((x for x in candidates if 30 <= len(x) <= 60 and x not in used_titles), '')
    if not valid_description:
        candidates = [normalize(generated.get('description')), shorten(content, 180),
                      shorten(f'{page.title}. {content}', 180)]
        description = next((x for x in candidates if 100 <= len(x) <= 180 and x not in used_descriptions), '')
    if not title or not description:
        raise RestorationError(f'«{page.title}»: недостаточно исходного текста для уникальных метаданных нужной длины. Подключите агента или выберите более полный снимок.')
    page.seo_title, page.description = title, description
    used_titles.add(title)
    used_descriptions.add(description)
