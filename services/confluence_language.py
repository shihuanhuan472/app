"""Select explicitly marked Chinese content without translating technical text."""
import re
from bs4 import BeautifulSoup


def chinese_blocks(soup):
    for node in list(soup.find_all(True)):
        if not node.attrs:
            continue
        language = str(node.get('lang') or node.get('data-language') or node.get('data-lang') or '').lower()
        name = str(node.get('ac:name') or '').lower() if node.name == 'ac:structured-macro' else ''
        if language == 'en' or language.startswith('en-') or name == 'english' or name.startswith('english-'):
            node.decompose()
    return soup


def chinese_title(value):
    value = str(value or '')
    # Only split explicit bilingual labels; preserve English technical terms.
    parts = re.split(r'\s*(?:/|\|)\s*', value)
    chinese = [part for part in parts if re.search(r'[\u4e00-\u9fff]', part)]
    return chinese[0].strip() if len(parts) > 1 and len(chinese) == 1 else value.strip()


def rendered_title(html, fallback):
    if isinstance(html, bytes):
        html = html.decode('utf-8-sig', errors='replace')
    soup = chinese_blocks(BeautifulSoup(html, 'html.parser'))
    candidates = []
    heading = soup.select_one('#title-text')
    if heading:
        candidates.append(heading.get_text(' ', strip=True))
    for name in ('ajs-page-title', 'ajs-latest-published-page-title'):
        meta = soup.find('meta', attrs={'name': name})
        if meta:
            candidates.append(meta.get('content', ''))
    for candidate in candidates:
        title = chinese_title(candidate)
        if re.search(r'[\u4e00-\u9fff]', title):
            return title
    return chinese_title(fallback)
