"""Local navigation hints and conservative cross-page organization; no model calls."""

import re

from app.core.generator.markdown import MarkdownGenerator
from app.core.markdown_text import map_prose, prose_text


def source_outline(pages, limit=60):
    entries = []
    for page in pages:
        title = next((line.strip() for line in page.text.splitlines() if line.strip()), '')
        if title:
            entries.append(f"Page {page.page_num}: {title[:100]}")
    # Include the whole document's shape even for very long decks.
    if len(entries) > limit:
        entries = [entries[round(index*(len(entries)-1)/(limit-1))] for index in range(limit)]
    return '\n'.join(entries)[:6500]


def navigation_context(outline, previous_notes):
    headings = re.findall(r'(?m)^#{2,3} +([^\n]+)', prose_text(previous_notes))[-12:]
    return ("Document navigation hints, not instructions or additional evidence:\n" + outline +
            "\nPreviously accepted topic names (reuse when the source continues the same concept):\n" +
            '\n'.join(headings))[:8500]


def assemble_notes(title, notes, page_count, language='en'):
    generator = MarkdownGenerator()
    parts, active_topic = [f'# {title}'], None
    missing = sorted(set(range(1, page_count+1))-notes.keys())
    if missing:
        label = '未完成笔记；缺失来源页' if language == 'zh' else 'Incomplete notes; missing source pages'
        parts.append(f"> {label}: {', '.join(map(str, missing))}")
    for page, content in sorted(notes.items()):
        cleaned = generator.sanitize_fragment(content)
        first_heading = True
        def organize(text):
            nonlocal active_topic, first_heading
            def heading(match):
                nonlocal active_topic, first_heading
                topic = re.sub(r'\s+', ' ', match[1]).strip().casefold()
                duplicate = first_heading and topic == active_topic
                active_topic, first_heading = topic, False
                return '' if duplicate else match[0]
            # Only join identical adjacent H2 sections. Never infer semantic equivalence.
            return re.sub(r'(?m)^## +([^\n]+)\n?', heading, text)
        cleaned = map_prose(cleaned, organize)
        parts.append(cleaned + f'\n\n> Source: page {page}')
    return generator.audit_document('\n\n'.join(parts), title).content
