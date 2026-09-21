"""Published package pages are indexable independently of editorial review."""
import re
from bs4 import Comment


# These names are page-level robot directives.  We remove the complete meta
# element instead of trying to merge its values: an archived ``index,follow``
# tag can still be contradicted by a second tag injected by the donor site.
# A clean head with no robot meta tag is the least ambiguous output.
ROBOT_META_NAME = re.compile(r'^(?:robots|x-robots-tag|[a-z0-9_-]*bot[a-z0-9_-]*)$', re.I)
INDEXATION_COMMENT = re.compile(r'(?:/?noindex|google(?:off|on)\s*:\s*index)', re.I)


def robot_tags(soup):
    for node in list(soup.find_all('meta')):
        name = str(node.get('name', '')).strip()
        prop = str(node.get('property', '')).strip()
        header = str(node.get('http-equiv', '')).strip()
        if (ROBOT_META_NAME.fullmatch(name) or ROBOT_META_NAME.fullmatch(prop)
                or ROBOT_META_NAME.fullmatch(header)):
            yield node


def open_indexation(soup):
    """Remove every archived page-level robot restriction.

    Link-level ``rel="nofollow"`` is deliberately left intact because it is
    an affiliate/link policy, not a page-level indexing directive.  The
    ``noindex`` element and the old crawler comment markers are unwrapped or
    removed so the enclosed article text remains available to the owner.
    """
    for node in list(robot_tags(soup)):
        node.decompose()
    for node in list(soup.find_all('noindex')):
        node.unwrap()
    for node in list(soup.find_all(string=lambda value: isinstance(value, Comment))):
        if INDEXATION_COMMENT.fullmatch(str(node).strip()):
            node.extract()
    return soup


def indexation_blockers(soup):
    """Return saved HTML markers that can close a page to crawlers."""
    blockers = list(robot_tags(soup))
    blockers.extend(soup.find_all('noindex'))
    blockers.extend(node for node in soup.find_all(string=lambda value: isinstance(value, Comment))
                    if INDEXATION_COMMENT.fullmatch(str(node).strip()))
    return blockers
