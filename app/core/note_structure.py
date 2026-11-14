"""Local navigation hints and conservative cross-page organization; no model calls."""

import re

from app.core.generator.markdown import MarkdownGenerator
from app.core.markdown_text import is_non_content_page, map_prose, prose_text


def _leading_paragraph(body):
    """A plain introductory paragraph only; lists, math, tables and subheadings stay intact."""
    start, end, lines = 0, 0, []
    for line in body.splitlines(keepends=True):
        if not lines and not line.strip():
            start += len(line)
            end = start
            continue
        if (not line.strip() or line.startswith(('    ', '\t')) or line.lstrip().startswith(('\\[', '\\(')) or
                re.match(r'^\s*(?:[#>|$<*+`~]|[-](?:\s|$)|\d+[.)]\s)', line)):
            break
        lines.append(line)
        end += len(line)
    paragraph = ''.join(lines).strip()
    if (len(paragraph) < 40 or paragraph[-1] not in '.!?。！？' or
            any(token in paragraph for token in ('`', '$', '=', ';', '\\[', '\\('))):
        return None
    return start, end, paragraph


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
    introductions = set()
    visible_pages = 0
    missing = sorted(set(range(1, page_count+1))-notes.keys())
    if missing:
        label = '未完成笔记；缺失来源页' if language == 'zh' else 'Incomplete notes; missing source pages'
        parts.append(f"> {label}: {', '.join(map(str, missing))}")
    for page, content in sorted(notes.items()):
        if is_non_content_page(content):
            continue
        cleaned = generator.sanitize_fragment(content)
        if not cleaned:
            continue
        visible_pages += 1
        first_heading = True
        def organize(text):
            nonlocal active_topic, first_heading
            headings = list(re.finditer(r'(?m)^## +([^\n]+)\n?', text))
            if not headings:
                return text
            result = [text[:headings[0].start()]]
            for index, match in enumerate(headings):
                end = headings[index+1].start() if index+1 < len(headings) else len(text)
                body = text[match.end():end]
                topic = re.sub(r'\s+', ' ', match[1]).strip().casefold()
                duplicate = first_heading and topic == active_topic
                if topic != active_topic:
                    introductions.clear()
                paragraph = _leading_paragraph(body)
                if paragraph:
                    start, stop, value = paragraph
                    if duplicate and value in introductions:
                        body = body[:start] + body[stop:]
                    introductions.add(value)
                active_topic, first_heading = topic, False
                result.append(('' if duplicate else match[0]) + body)
            # Only an exact repeated introduction under the continuing topic is removed.
            # Distinct explanations, later revisits and every fenced block are preserved.
            return ''.join(result)
        cleaned = map_prose(cleaned, organize)
        parts.append(cleaned + f'\n\n> Source: page {page}')
    if notes and not visible_pages:
        parts.append('> 已检查的页面没有可纳入的学习内容；如有疑问，请查看来源页。' if language == 'zh' else
                     '> No study content was included from the reviewed pages. Review sources if this is unexpected.')
    return generator.audit_document('\n\n'.join(parts), title).content
