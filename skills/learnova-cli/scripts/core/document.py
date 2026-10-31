"""Deterministic Markdown structure, expressed in Unicode character offsets."""
import re

_FENCE = re.compile(r'^( {0,3})(`{3,}|~{3,})(.*)$')
_HEADING = re.compile(r'^ {0,3}(#{1,6})(?:[ \t]+|$)(.*)$')
_TABLE_DIVIDER = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$')


def line_number(text, offset):
    return text.count('\n', 0, offset) + 1


def scan(text):
    """Return headings, legal boundaries and protected ranges for exact source text."""
    lines = text.splitlines(keepends=True)
    starts, pos = [], 0
    for line in lines:
        starts.append(pos)
        pos += len(line)
    headings, protected, candidates = [], [], {0: 'start', len(text): 'end'}
    i = 0
    while i < len(lines):
        raw = lines[i].rstrip('\r\n')
        start = starts[i]
        fence = _FENCE.match(raw)
        kind = None
        end_i = i + 1
        if fence:
            kind = 'fence'
            marker = fence.group(2)
            close = re.compile(r'^ {0,3}' + re.escape(marker[0]) + '{' + str(len(marker)) + r',}[ \t]*$')
            while end_i < len(lines):
                if close.match(lines[end_i].rstrip('\r\n')):
                    end_i += 1
                    break
                end_i += 1
        elif re.match(r'^ {0,3}(?:\$\$|\\\[)', raw):
            kind = 'math'
            delimiter = '$$' if raw.lstrip().startswith('$$') else r'\]'
            opening = '$$' if delimiter == '$$' else r'\['
            if len(raw.strip()) > len(opening) and raw.strip().endswith(delimiter):
                pass
            else:
                while end_i < len(lines):
                    if lines[end_i].strip().endswith(delimiter):
                        end_i += 1
                        break
                    end_i += 1
        elif re.match(r'^ {0,3}<table(?:\s|>)', raw, re.I):
            kind = 'html_table'
            depth = 0
            end_i = i
            while end_i < len(lines):
                for tag in re.finditer(r'<(/?)table\b[^>]*>', lines[end_i], re.I):
                    depth += -1 if tag.group(1) else 1
                end_i += 1
                if depth <= 0:
                    break
        elif raw.startswith(('    ', '\t')):
            kind = 'indented_code'
            while end_i < len(lines) and (lines[end_i].startswith(('    ', '\t')) or not lines[end_i].strip()):
                end_i += 1
        elif i + 1 < len(lines) and '|' in raw and _TABLE_DIVIDER.match(lines[i + 1].rstrip('\r\n')):
            kind = 'table'
            end_i = i + 2
            while end_i < len(lines) and '|' in lines[end_i] and lines[end_i].strip():
                end_i += 1
        if kind:
            end = starts[end_i] if end_i < len(lines) else len(text)
            protected.append({'start': start, 'end': end, 'kind': kind})
            candidates.setdefault(start, 'block')
            candidates.setdefault(end, 'block')
            i = end_i
            continue
        heading = _HEADING.match(raw)
        if heading:
            headings.append({'start': start, 'line': i + 1, 'level': len(heading.group(1)), 'title': re.sub(r'[ \t]+#+[ \t]*$', '', heading.group(2)).strip()})
            candidates.setdefault(start, 'heading')
        elif raw.strip() and i and not lines[i - 1].strip():
            candidates.setdefault(start, 'paragraph')
        i += 1
    boundaries = [{'id': f'b{offset}', 'offset': offset, 'line': line_number(text, offset), 'kind': kind} for offset, kind in sorted(candidates.items())]
    return {'length': len(text), 'line_count': line_number(text, len(text)), 'headings': headings, 'boundaries': boundaries, 'protected': protected}


def read_range(text, start, end, limit=4000):
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= len(text) or not isinstance(limit, int) or limit < 1:
        raise ValueError('invalid range or limit')
    stop = min(end, start + limit)
    return {'start': start, 'end': stop, 'text': text[start:stop], 'next_start': stop if stop < end else None}


def representative(text, start, end, size=500):
    if not 0 <= start <= end <= len(text) or size < 1:
        raise ValueError('invalid range or size')
    length = end - start
    offsets = sorted(set((start, start + max(0, (length - size) // 2), max(start, end - size))))
    return [read_range(text, offset, end, size) for offset in offsets]
