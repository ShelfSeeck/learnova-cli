"""Verified Unicode spans to original PDF pages; pure data transforms only."""
from .errors import CliError


def valid_ranges(ranges, length):
    if not isinstance(ranges, list):
        return False
    previous = 0
    for item in ranges:
        if (not isinstance(item, dict) or set(item) != {'start', 'end', 'page'}
                or any(type(item[k]) is not int for k in item)
                or not 0 <= item['start'] < item['end'] <= length
                or item['page'] < 1 or item['start'] < previous):
            return False
        previous = item['end']
    return True


def status(ranges, length):
    if not ranges:
        return 'unavailable'
    return 'complete' if sum(r['end'] - r['start'] for r in ranges) == length else 'partial'


def translate(page_map, edits, original_length):
    """Drop ambiguous edited endpoints, shift exact boundaries, never guess pages."""
    ranges = page_map.get('ranges') if isinstance(page_map, dict) else None
    if not valid_ranges(ranges, original_length):
        return {'status': 'unavailable', 'ranges': []}
    edits = sorted(edits, key=lambda c: c['start'])
    def position(offset):
        delta = 0
        for edit in edits:
            if edit['start'] < offset < edit['end']:
                return None
            if edit['end'] <= offset:
                delta += len(edit['replacement']) - (edit['end'] - edit['start'])
        return offset + delta
    result = []
    for span in ranges:
        start, end = position(span['start']), position(span['end'])
        if start is not None and end is not None and start < end:
            result.append({'start': start, 'end': end, 'page': span['page']})
    length = original_length + sum(len(e['replacement']) - (e['end'] - e['start']) for e in edits)
    return {'status': status(result, length), 'ranges': result}


def validate(page_map, length):
    if (not isinstance(page_map, dict) or set(page_map) != {'status', 'ranges'}
            or not valid_ranges(page_map['ranges'], length)
            or page_map['status'] != status(page_map['ranges'], length)):
        raise CliError('BUILD_INTEGRITY_FAILED', '无效页码映射')
