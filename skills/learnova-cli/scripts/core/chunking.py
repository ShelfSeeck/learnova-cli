"""Validate split decisions and retain exact, continuous source ranges."""
from .document import line_number, scan
from .errors import CliError


def _invalid(message):
    raise CliError('BUILD_PLAN_INVALID', message, 2)


def _integrity(message):
    raise CliError('BUILD_INTEGRITY_FAILED', message, 1)


def make_chunk(text, start, end, parent_id=None, depth=0):
    return {'id': f'c{start}-{end}', 'start': start, 'end': end,
            'start_line': line_number(text, start), 'end_line': line_number(text, max(start, end - 1)),
            'parent_id': parent_id, 'depth': depth, 'tokens': (end - start + 3) // 4, 'warnings': []}


def split(text, structure, cuts, start=0, end=None, protected=(), parent_id=None, depth=0):
    """Split scope at boundary IDs; supplied protected ranges add to scanner ranges."""
    end = len(text) if end is None else end
    if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(text):
        _invalid('invalid scope')
    lookup = {b['id']: b['offset'] for b in structure['boundaries']}
    if not isinstance(cuts, (list, tuple)) or any(not isinstance(c, str) for c in cuts):
        _invalid('cuts must be a list of boundary IDs')
    if any(c not in lookup for c in cuts) or len(set(cuts)) != len(cuts):
        _invalid('unknown or duplicate boundary')
    offsets = [lookup[c] for c in cuts]
    if offsets != sorted(offsets) or any(not start < x < end for x in offsets):
        _invalid('unordered or out-of-scope boundary')
    if not isinstance(protected, (list, tuple)):
        _invalid('protected ranges must be a list')
    for unit in list(structure['protected']) + list(protected):
        if not isinstance(unit, dict):
            _invalid('invalid protected range')
        a, b = unit.get('start'), unit.get('end')
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text):
            _invalid('invalid protected range')
        if any(a < x < b for x in [start, end] + offsets):
            _invalid('boundary inside protected range')
    points = [start] + offsets + [end]
    return [make_chunk(text, a, b, parent_id, depth) for a, b in zip(points, points[1:])]


def refine(text, structure, chunk, cuts, max_tokens, max_depth, protected=()):
    """Refine once; preserve oversized parent with a reason when progress stops."""
    if chunk['tokens'] <= max_tokens:
        return [chunk]
    reason = None
    if chunk['depth'] >= max_depth:
        reason = 'max_depth'
    elif not cuts:
        units = list(structure['protected']) + list(protected)
        reason = 'atomic' if any(u['start'] <= chunk['start'] and u['end'] >= chunk['end'] for u in units) else 'no_cut'
    if reason:
        result = dict(chunk)
        result['warnings'] = list(chunk['warnings']) + ['oversized:' + reason]
        return [result]
    return split(text, structure, cuts, chunk['start'], chunk['end'], protected, chunk['id'], chunk['depth'] + 1)


def merge_small(text, chunks, min_tokens, max_tokens, *, structure, protected, barriers=()):
    """Merge only adjacent siblings, never across a barrier offset."""
    barriers = set(barriers)
    barriers.update(h['start'] for h in structure['headings'])
    protected = list(protected) + structure['protected']
    for unit in protected:
        barriers.update((unit['start'], unit['end']))
    out = []
    for chunk in chunks:
        if out:
            prev = out[-1]
            if (prev['end'] == chunk['start'] and prev['parent_id'] == chunk['parent_id'] and prev['depth'] == chunk['depth']
                    and chunk['start'] not in barriers and (prev['tokens'] < min_tokens or chunk['tokens'] < min_tokens)
                    and (chunk['end'] - prev['start'] + 3) // 4 <= max_tokens):
                merged = make_chunk(text, prev['start'], chunk['end'], prev['parent_id'], prev['depth'])
                merged['warnings'] = prev['warnings'] + chunk['warnings']
                out[-1] = merged
                continue
        out.append(chunk)
    return out


def validate_coverage(text, chunks, protected=()):
    cursor = 0
    protected = list(scan(text)['protected']) + list(protected)
    for c in sorted(chunks, key=lambda item: item['start']):
        if c['start'] != cursor or not c['start'] <= c['end'] <= len(text) or (c['start'] == c['end'] and text):
            _integrity('source coverage has gap or overlap')
        if any(u['start'] < c['end'] < u['end'] for u in protected):
            _integrity('source coverage splits protected range')
        cursor = c['end']
    if cursor != len(text) or not chunks:
        _integrity('source coverage incomplete')
    return True
