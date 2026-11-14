"""Small fence-aware Markdown helpers. Never interpret source code as prose."""

import re


OPEN_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)")
NON_CONTENT_PAGE = "<!-- noteagent:non-content -->"


def is_non_content_page(content):
    """Only the entire reserved receipt is omitted; never remove mixed content or code."""
    return isinstance(content, str) and content.strip() == NON_CONTENT_PAGE


def markdown_blocks(content):
    """Yield (is_fenced, text), including unfinished fenced blocks verbatim."""
    lines, current, fence = content.splitlines(keepends=True), [], None
    for line in lines:
        match = OPEN_FENCE.match(line)
        if fence is None and match:
            if current:
                yield False, ''.join(current)
            current, fence = [line], match[1]
        elif fence is not None:
            current.append(line)
            if (match and match[1][0] == fence[0] and len(match[1]) >= len(fence)
                    and not match[2].strip()):
                yield True, ''.join(current)
                current, fence = [], None
        else:
            current.append(line)
    if current:
        yield fence is not None, ''.join(current)


def map_prose(content, transform):
    return ''.join(block if fenced else transform(block) for fenced, block in markdown_blocks(content))


def prose_text(content):
    return ''.join(block for fenced, block in markdown_blocks(content) if not fenced)
