"""Shared host/automatic build state machine."""
import base64
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import engine
from core import chunking, document, skill_build, page_provenance
from core.errors import CliError, ConflictError
from . import config, jobs, transactions

STAGES = ('parsing', 'chunking', 'indexing', 'finalize')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def invalid(message):
    raise CliError('BUILD_INPUT_INVALID', message, 2)


def _input(path):
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CliError('BUILD_INPUT_INVALID', '无法读取输入文件') from exc


def _safe_output(root, source, output, job):
    target = Path(os.path.abspath(output))
    source = Path(source).resolve()
    # Reject targets that contain the input, project root or private task state.
    for protected in (root, source, job):
        if target == protected or target in protected.parents:
            invalid('输出目录不能覆盖项目、输入或任务目录')
    if (root / '.learnova') == target or (root / '.learnova') in target.parents or source == target:
        invalid('输出目录不能位于任务目录或输入文件内')
    return target


def _normalize(text, source, root, supplied=None):
    changes, assets = [], {}
    for image in skill_build.extract_images(text):
        dest = image['destination']
        url = urlsplit(dest)
        if url.scheme in ('http', 'https') and url.netloc:
            if image['kind'] == 'reference':
                raw = text[image['usage_start']:image['usage_end']]
                alt = text[image['usage_start'] + 2:image['alt_end']]
                changes.append({'start': image['usage_start'], 'end': image['usage_end'], 'original': raw, 'replacement': f'![{alt}]({dest})'})
            continue
        if url.scheme or url.netloc or url.query or url.fragment:
            invalid('不支持的图片链接')
        relative = unquote(url.path)
        if not relative or '\\' in relative or Path(relative).is_absolute():
            invalid('不安全的图片路径')
        supplied_data = None if supplied is None else supplied.get(relative)
        if supplied is not None and (not isinstance(supplied_data, bytes) or any(part in ('', '.', '..') for part in relative.split('/'))):
            invalid('解析器图片缺失或路径不安全')
        target = (source.parent / relative).resolve()
        if not (target.is_relative_to(root) or target.is_relative_to(source.parent.resolve())):
            invalid('图片路径超出允许范围')
        if any(p.lower() in ('.env', '.ssh', '.aws', '.codex', '.agents') or p.lower().startswith('.env.') for p in target.parts):
            invalid('图片路径指向受保护文件')
        suffix = target.suffix.lower()
        if suffix not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'):
            invalid('图片格式不受支持')
        data = supplied_data if supplied is not None else _input(target)
        valid = (data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff') or
                 data.startswith((b'GIF87a', b'GIF89a', b'RIFF')) or
                 (suffix == '.svg' and b'<svg' in data[:1024].lower()))
        if not valid:
            invalid('图片内容无效')
        name = 'images/' + digest(data) + suffix
        asset = assets.setdefault(name, {'sources': [], 'sha256': digest(data), 'data': base64.b64encode(data).decode('ascii')})
        if supplied is None:
            asset['sources'].append({'path': str(source.parent / relative), 'resolved': str(target)})
        if image['kind'] == 'reference':
            alt = text[image['usage_start'] + 2:image['alt_end']]
            start, end, replacement = image['usage_start'], image['usage_end'], f'![{alt}]({name})'
        else:
            start, end, replacement = image['start'], image['end'], name
        changes.append({'start': start, 'end': end, 'original': text[start:end], 'replacement': replacement})
    for change in sorted(changes, key=lambda c: c['start'], reverse=True):
        text = text[:change['start']] + change['replacement'] + text[change['end']:]
    return text, assets, changes


def _matches_output(root, expected):
    if not root.is_dir() or root.is_symlink() or any(p.is_symlink() for p in root.parents):
        return False
    try:
        actual = {}
        for path in root.rglob('*'):
            if path.is_symlink():
                return False
            if path.is_file():
                actual[path.relative_to(root).as_posix()] = path.read_bytes()
        return actual == expected
    except OSError:
        return False


class BuildService:
    """parser.advance(source, config, state, checkpoint) and decision.decide(request, config)."""
    def __init__(self, root, parser=None, decision=None, sleeper=None):
        self.root = Path(root).resolve()
        self.parser = parser
        self.decision = decision
        if sleeper is None:
            import time
            sleeper = time.sleep
        self.sleeper = sleeper

    def create(self, input_path, name=None, output=None, mode='agent', parser=None, force=False):
        if name is not None and (len(name) > 64 or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name)):
            invalid('无效 Skill 名称')
        source = Path(input_path).absolute()
        if not source.is_file() or source.suffix.lower() not in ('.md', '.markdown', '.pdf'):
            invalid('输入必须是存在的 Markdown 或 PDF 文件')
        cfg = config.load(self.root, {'parser': parser} if parser else None)
        fingerprint = digest(_input(source))
        task_id, path = jobs.create_dir(self.root)
        if output:
            output = str(_safe_output(self.root, source, output, path))
        state = {'schema_version': 1, 'task_id': task_id, 'revision': 0, 'document_revision': 1,
                 'input': str(source), 'input_sha256': fingerprint, 'name': name, 'output': output,
                 'force': force, 'mode': mode, 'parser_override': parser, 'config': cfg, 'stage': 'parsing', 'status': 'pending',
                 'request': None, 'text': None, 'assets': {}, 'normalizations': [], 'chunks': [],
                 'protected': [], 'metadata': {}, 'result_path': None, 'parser_state': {}, 'page_map': None,
                 'rules_sha256': self._rules_hash(), 'render_version': skill_build.RENDER_VERSION}
        jobs.write(path, state)
        return self._public(state)

    def _public(self, s):
        result = {k: s[k] for k in ('task_id', 'revision', 'document_revision', 'stage', 'status', 'mode', 'request', 'result_path')}
        result['warning_count'] = sum(bool(c.get('warnings')) for c in s['chunks'])
        done = len(s['metadata'].get('chunks', {}))
        result['summary'] = {'chunks': len(s['chunks']), 'metadata_done': done,
                             'metadata_pending': len(s['chunks']) - done}
        result['next_action'] = ('configure' if s.get('request') and s['request']['kind'] == 'configuration' else
                                 'submit' if s['status'] == 'needs_input' else
                                 'resume' if s['status'] in ('paused', 'failed') else
                                 'use_skill' if s['status'] == 'completed' else 'work')
        if s.get('error'):
            result['error'] = s['error']
        if 'progress' in s:
            result['progress'] = s['progress']
        return result

    def start(self, input_path, name=None, output=None, mode='agent', parser=None, force=False, preset=None):
        from .preflight import inspect
        if name is not None and (len(name) > 64 or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name)):
            invalid('无效 Skill 名称')
        if output and not Path(output).is_absolute():
            output = str(self.root / output)
        target = Path(output) if output else self.root / '.agents' / 'skills' / name if name else None
        if target is not None:
            target = _safe_output(self.root, input_path, target, self.root / '.learnova')
            if any(part.is_symlink() for part in (target, *target.parents)):
                invalid('输出路径不能包含符号链接')
            if target.exists() and not force:
                raise ConflictError('SKILL_CONFLICT', f'目标已存在: {target}；使用 --force 覆盖')
        if preset:
            config.set_values(self.root, config.preset_values(preset))
        report = inspect(self.root, input_path, mode, parser)
        if not report['ready']:
            return {'status': 'needs_input', 'preflight': report, 'next_action': 'configure'}
        state = self.create(input_path, name, output, mode, parser, force)
        task = state['task_id']
        if mode == 'auto' and self.decision is None:
            from .decisions import Decision
            self.decision = Decision(lambda start, end, limit, kind: self.read(task, start, end, limit, kind))
        return self.work(task)

    def tasks(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise CliError('BAD_USAGE', 'limit 需为 1–100', 2)
        base = self.root / '.learnova' / 'builds'
        if not base.exists():
            return {'tasks': [], 'total': 0}
        if not base.is_dir() or base.is_symlink() or base.parent.is_symlink():
            raise CliError('BUILD_INTEGRITY_FAILED', '任务目录不能包含符号链接')
        paths = [p for p in base.iterdir() if re.fullmatch(r'[a-f0-9]{32}', p.name) and not p.is_symlink() and p.is_dir()]
        rows = []
        for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            try:
                with jobs.locked(self.root, path.name):
                    s = jobs.read(path)
                    rows.append({**{k: s[k] for k in ('task_id', 'input', 'stage', 'status', 'result_path')},
                                 'summary': self._public(s)['summary']})
            except CliError as exc:
                rows.append({'task_id': path.name, 'status': 'invalid', 'error': {'code': exc.code}})
        return {'tasks': rows, 'total': len(paths)}

    def _reset(self, s, stage):
        s['document_revision'] += 1
        s['stage'], s['status'], s['request'] = stage, 'pending', None
        s['result_path'] = None
        s.pop('commit_intent', None)
        s.pop('error', None)
        if STAGES.index(stage) <= 0:
            s.update(text=None, assets={}, normalizations=[], parser_state={}, page_map=None)
            s.pop('normalized_page_map', None)
        if STAGES.index(stage) <= 1:
            s.update(chunks=[], protected=[], metadata={})
        elif stage == 'indexing':
            s['metadata'] = {}

    @staticmethod
    def _rules_hash():
        return digest((Path(__file__).with_name('prompts') / 'decision.txt').read_bytes())

    def _check_input(self, s):
        if s['status'] == 'completed':
            return False
        rules = self._rules_hash()
        rules_changed = s.get('rules_sha256') != rules
        if rules_changed:
            s['rules_sha256'] = rules
            if s['text'] is not None:
                self._reset(s, 'chunking')
        changed = digest(_input(Path(s['input']))) != s['input_sha256']
        for asset in s['assets'].values():
            try:
                if digest(base64.b64decode(asset['data'], validate=True)) != asset['sha256']:
                    changed = True
                for origin in asset['sources']:
                    if str(Path(origin['path']).resolve()) != origin['resolved'] or digest(_input(Path(origin['path']))) != asset['sha256']:
                        changed = True
            except (ValueError, KeyError, CliError):
                changed = True
        structure_changed = False
        if s['text'] is not None:
            if digest(s['text'].encode('utf-8')) != s.get('text_sha256'):
                changed = True
            else:
                structure = document.scan(s['text'])
                if s.get('structure') != structure:
                    s['structure'] = structure
                    structure_changed = True
        if changed:
            s['input_sha256'] = digest(_input(Path(s['input'])))
            self._reset(s, 'parsing')
        elif structure_changed:
            self._reset(s, 'chunking')
        render_changed = s.get('render_version') != skill_build.RENDER_VERSION
        if render_changed:
            s['render_version'] = skill_build.RENDER_VERSION
            if s['text'] is not None and 'description' in s['metadata']:
                self._reset(s, 'finalize')
        return changed or structure_changed or rules_changed or render_changed

    def _request(self, s, kind, scope, **extra):
        req = {'schema_version': 1, 'task_id': s['task_id'], 'request_id': uuid.uuid4().hex,
               'document_revision': s['document_revision'], 'stage': s['stage'], 'kind': kind,
               'scope': scope, **extra}
        s['request'], s['status'] = req, 'needs_input'

    def _refresh_configuration(self, s):
        req = s.get('request')
        if not req or req['kind'] != 'configuration':
            return
        scope = req['scope']
        if scope == 'llm' and s['mode'] == 'agent':
            missing = []
        else:
            missing = config.missing(s['config'], s['config']['parser'] if scope == 'parser' else scope)
        if not missing:
            s['request'] = None
            s['status'] = 'pending'
        elif missing != req['questions']:
            self._request(s, 'configuration', scope, questions=missing)

    def _advance(self, s, path, managed=False, batch_size=8):
        if s['status'] in ('paused', 'completed', 'failed'):
            return
        if s['request']:
            return
        s['status'] = 'running'
        if s['stage'] == 'parsing':
            source = Path(s['input'])
            supplied = None
            if source.suffix.lower() == '.pdf':
                missing = config.missing(s['config'], s['config']['parser'])
                if missing:
                    self._request(s, 'configuration', 'parser', questions=missing); return
                if self.parser is None:
                    raise CliError('BUILD_ADAPTER_UNAVAILABLE', 'PDF 解析适配器不可用')
                def checkpoint(state):
                    from .parsers import validate_checkpoint
                    secrets = tuple(os.environ.get(s['config'][key], '') for key in ('remote_key_env', 'llm_key_env'))
                    validate_checkpoint(state, secrets)
                    s['parser_state'] = state
                    jobs.write(path, s)
                parsed = self.parser.advance(source, s['config'], s['parser_state'], checkpoint)
                if parsed['status'] == 'running':
                    s['progress'] = parsed.get('progress')
                    return
                text, supplied = parsed['markdown'], parsed['assets']
                s['page_map'] = parsed.get('page_map')
                s.pop('progress', None)
            else:
                try:
                    text = _input(source).decode('utf-8')
                except UnicodeDecodeError as exc:
                    raise CliError('BUILD_INPUT_INVALID', 'Markdown 必须为 UTF-8') from exc
            s['text'], s['assets'], s['normalizations'] = _normalize(text, source, self.root, supplied)
            if source.suffix.lower() == '.pdf':
                s['normalized_page_map'] = page_provenance.translate(s['page_map'], s['normalizations'], len(text))
            s['text_sha256'] = digest(s['text'].encode('utf-8'))
            s['structure'] = document.scan(s['text'])
            s['stage'] = 'chunking'
        if s['stage'] == 'chunking':
            text, structure = s['text'], s['structure']
            if not s['chunks']:
                scope = {'start': 0, 'end': len(text), 'id': 'document'}
            else:
                scope = next((c for c in s['chunks'] if c['tokens'] > s['config']['max_tokens'] and not c['warnings']), None)
                if scope and scope['depth'] >= s['config']['max_depth']:
                    scope['warnings'].append('oversized:max_depth'); return
            if scope:
                allowed = [b['id'] for b in structure['boundaries'] if scope['start'] < b['offset'] < scope['end'] and not any(u['start'] < b['offset'] < u['end'] for u in structure['protected'] + s['protected'])]
                if managed and s['chunks'] and not allowed:
                    scope['warnings'].append('oversized:no_cut')
                    return
                headings = [dict(h, title=h['title'][:512]) for h in structure['headings'] if scope['start'] <= h['start'] < scope['end']]
                self._request(s, 'split_plan', scope, allowed_boundaries=allowed[:100], boundary_count=len(allowed),
                              headings=headings[:40], heading_count=len(headings),
                              proposal=chunking.propose(structure, scope['start'], scope['end'], s['config']['max_tokens'], s['config']['min_tokens'], s['protected']),
                              samples=document.representative(text, scope['start'], scope['end'], min(500, s['config']['read_limit'])))
                return
            s['chunks'] = chunking.merge_small(text, s['chunks'], s['config']['min_tokens'], s['config']['max_tokens'], structure=structure, protected=s['protected'])
            s['stage'] = 'indexing'
        if s['stage'] == 'indexing':
            details = s['metadata'].setdefault('chunks', {})
            pending = [c for c in s['chunks'] if c['id'] not in details]
            if managed and pending:
                batch = pending[:min(batch_size, 4) if s['mode'] == 'auto' else batch_size]
                contexts, context_size = [], 0
                for c in batch:
                    context = {**c, 'samples': document.representative(s['text'], c['start'], c['end'], min(500, s['config']['read_limit'])),
                               'headings': [dict(h, title=h['title'][:160]) for h in s['structure']['headings'] if c['start'] <= h['start'] < c['end']][:8]}
                    size = len(json.dumps(context, ensure_ascii=False))
                    if contexts and context_size + size > 24000:
                        break
                    contexts.append(context)
                    context_size += size
                self._request(s, 'chunk_metadata_batch', {'ids': [c['id'] for c in contexts]},
                              chunks=contexts, chunk_count=len(s['chunks']), remaining_count=len(pending))
                return
            for chunk in s['chunks']:
                if chunk['id'] not in details:
                    self._request(s, 'chunk_metadata', chunk, allowed_boundaries=[], samples=document.representative(s['text'], chunk['start'], chunk['end'], min(500, s['config']['read_limit']))); return
            if 'description' not in s['metadata']:
                preview = {k: {'title': v['title'][:512], 'summary': v['summary'][:512], 'keywords': v['keywords'][:32]} for k, v in list(details.items())[:10]}
                self._request(s, 'skill_metadata', 'document', allowed_boundaries=[], chunks=preview, chunk_count=len(details),
                              outline=[{'id': key, 'title': value['title'][:120]} for key, value in list(details.items())[:100]], name=s['name']); return
            s['stage'] = 'finalize'
        if s['stage'] == 'finalize':
            if s.get('normalized_page_map') is not None:
                page_provenance.validate(s['normalized_page_map'], len(s['text']))
            files = skill_build.render(s['text'], s['chunks'], s['metadata'], {k: base64.b64decode(v['data']) for k, v in s['assets'].items()}, s['protected'])
            output = Path(s['output']) if s['output'] else self.root / '.agents' / 'skills' / s['metadata']['name']
            output = _safe_output(self.root, s['input'], output, path)
            expected = {name: value if isinstance(value, bytes) else value.encode('utf-8') for name, value in files.items()}
            fingerprint = transactions.fingerprint(expected)
            intent = {'path': str(output), 'sha256': fingerprint}
            with transactions.transaction(output, fingerprint) as (prepare, complete):
                if s.get('commit_intent') == intent and _matches_output(output, expected):
                    s.update(status='completed', result_path=str(output))
                    s.pop('commit_intent', None)
                    return
                if output.exists() and not s['force']:
                    raise ConflictError('SKILL_CONFLICT', f'目标已存在: {output}；使用 --force 覆盖')
                with tempfile.TemporaryDirectory(dir=path) as candidate:
                    for name, value in expected.items():
                        dest = Path(candidate) / name
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(value)
                    s['commit_intent'] = intent
                    jobs.write(path, s)
                    engine.commit_directories(Path(candidate), [output], force=s['force'], before_replace=prepare, after_replace=complete, after_rollback=complete)
                s.update(status='completed', result_path=str(output))
                s.pop('commit_intent', None)

    def status(self, task_id):
        with jobs.locked(self.root, task_id) as path:
            s = jobs.read(path)
            if self._check_input(s):
                jobs.write(path, s)
            return self._public(s)

    def next(self, task_id, *, managed=False, batch_size=8):
        with jobs.locked(self.root, task_id) as path:
            s = jobs.read(path)
            if s['status'] == 'completed':
                return self._public(s)
            try:
                self._check_input(s)
                if s['status'] not in ('paused', 'failed'):
                    self._refresh_configuration(s)
                self._advance(s, path, managed, batch_size)
                jobs.write(path, s)
                return self._public(s)
            except Exception as exc:
                self._save_failure(path, exc)
                raise

    @staticmethod
    def _save_failure(path, error):
        saved = jobs.read(path)
        saved['status'] = 'failed'
        saved['error'] = {'code': error.code if isinstance(error, CliError) else 'INTERNAL_ERROR', 'next': '检查配置或输入后执行 build resume 重试'}
        jobs.write(path, saved)

    def read(self, task_id, start=0, end=None, limit=None, kind='text'):
        with jobs.locked(self.root, task_id) as path:
            s = jobs.read(path)
            if self._check_input(s):
                jobs.write(path, s)
            if s['text'] is None:
                raise CliError('BUILD_INPUT_INVALID', '文档尚未解析', 2)
            if kind != 'text':
                if type(start) is not int or start < 0 or (limit is not None and (type(limit) is not int or limit < 1)):
                    raise CliError('BAD_USAGE', '无效分页范围', 2)
                if kind == 'boundaries':
                    req = s['request']
                    scope = req['scope'] if req and req['kind'] == 'split_plan' else {'start': 0, 'end': len(s['text'])}
                    values = [b for b in s['structure']['boundaries'] if scope['start'] < b['offset'] < scope['end'] and not any(u['start'] < b['offset'] < u['end'] for u in s['structure']['protected'] + s['protected'])]
                elif kind == 'headings':
                    values = [dict(h, title=h['title'][:512]) for h in s['structure']['headings']]
                elif kind == 'chunks':
                    values = []
                    for c in s['chunks']:
                        detail = s['metadata'].get('chunks', {}).get(c['id'], {})
                        values.append({'tokens': c['tokens'], 'warnings': list(c['warnings']), 'id': c['id'], 'start': c['start'], 'end': c['end'], 'title': detail.get('title', ''), 'summary': detail.get('summary', ''), 'keywords': detail.get('keywords', [])})
                else:
                    raise CliError('BAD_USAGE', '未知读取类型', 2)
                size = min(limit or 100, 100)
                stop = min(len(values), start + size)
                return {'kind': kind, 'start': start, 'items': values[start:stop], 'total': len(values), 'next_start': stop if stop < len(values) else None}
            end = len(s['text']) if end is None else end
            limit = s['config']['read_limit'] if limit is None else min(limit, s['config']['read_limit'])
            try:
                return document.read_range(s['text'], start, end, limit)
            except (ValueError, TypeError) as exc:
                raise CliError('BAD_USAGE', '无效读取范围', 2) from exc

    def submit_file(self, task_id, filename):
        try:
            envelope = json.loads(Path(filename).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise CliError('BAD_USAGE', '无法读取 JSON 结果', 2) from exc
        return self.submit(task_id, envelope)

    @staticmethod
    def _validate_chunk_metadata(result):
        if (not isinstance(result, dict) or set(result) != {'title', 'summary', 'keywords'}
                or any(not isinstance(result[k], str) or not result[k].strip() or '\n' in result[k] or '\r' in result[k] or len(result[k]) > 512 for k in ('title', 'summary'))
                or not isinstance(result['keywords'], list) or not result['keywords'] or len(result['keywords']) > 32
                or any(not isinstance(k, str) or not k.strip() or len(k) > 128 or '\n' in k or '\r' in k for k in result['keywords'])):
            raise CliError('BUILD_PLAN_INVALID', '无效分块元数据', 2)

    def submit(self, task_id, envelope):
        with jobs.locked(self.root, task_id) as path:
            s = jobs.read(path)
            if self._check_input(s):
                jobs.write(path, s)
            req = s['request']
            if not isinstance(envelope, dict) or not req or any(envelope.get(k) != req.get(k) for k in ('schema_version', 'task_id', 'request_id', 'document_revision', 'stage', 'kind', 'scope')):
                raise CliError('BUILD_STALE_REQUEST', '请求已过期或不匹配', 2)
            result = envelope.get('result')
            if not isinstance(result, dict):
                raise CliError('BUILD_PLAN_INVALID', '结果必须是对象', 2)
            kind = req['kind']
            if kind == 'split_plan':
                if set(result) - {'splits', 'protected_ranges', 'atomic'} or not isinstance(result.get('splits'), list) or type(result.get('atomic', False)) is not bool:
                    raise CliError('BUILD_PLAN_INVALID', '无效切片方案', 2)
                lookup = {b['id']: b['offset'] for b in s['structure']['boundaries']}
                ranges = result.get('protected_ranges', [])
                if not isinstance(ranges, list):
                    raise CliError('BUILD_PLAN_INVALID', '无效保护范围', 2)
                protected = list(s['protected'])
                for pair in ranges:
                    if not isinstance(pair, list) or len(pair) != 2 or any(not isinstance(x, str) or x not in lookup for x in pair):
                        raise CliError('BUILD_PLAN_INVALID', '无效保护范围', 2)
                    a, b = map(lookup.get, pair)
                    if not req['scope']['start'] <= a < b <= req['scope']['end']:
                        raise CliError('BUILD_PLAN_INVALID', '保护范围超出当前范围', 2)
                    protected.append({'start': a, 'end': b})
                scope = req['scope']
                if result.get('atomic') and result['splits']:
                    raise CliError('BUILD_PLAN_INVALID', '原子范围不能切分', 2)
                children = chunking.split(s['text'], s['structure'], result['splits'], scope['start'], scope['end'], protected, scope.get('id') if scope['id'] != 'document' else None, scope.get('depth', -1) + 1)
                if len(children) == 1 and children[0]['tokens'] > s['config']['max_tokens']:
                    children[0]['warnings'].append('oversized:' + ('atomic' if result.get('atomic') else 'no_cut'))
                s['protected'] = protected
                if scope['id'] == 'document':
                    s['chunks'] = children
                else:
                    index = next(i for i, c in enumerate(s['chunks']) if c['id'] == scope['id'])
                    s['chunks'][index:index + 1] = children
            elif kind == 'chunk_metadata':
                self._validate_chunk_metadata(result)
                s['metadata']['chunks'][req['scope']['id']] = result
            elif kind == 'chunk_metadata_batch':
                values = result.get('chunks')
                if set(result) != {'chunks'} or not isinstance(values, dict) or set(values) != set(req['scope']['ids']):
                    raise CliError('BUILD_PLAN_INVALID', '结果必须完整覆盖当前语义批次', 2)
                for value in values.values():
                    self._validate_chunk_metadata(value)
                s['metadata']['chunks'].update(values)
            elif kind == 'skill_metadata':
                if set(result) != {'name', 'description'} or not skill_build.valid_skill_metadata(result['name'], result['description']) or (s['name'] and s['name'] != result['name']):
                    raise CliError('BUILD_PLAN_INVALID', '无效 Skill 元数据', 2)
                s['metadata'].update(result)
            else:
                raise CliError('BUILD_PLAN_INVALID', '此请求需更新配置后恢复', 2)
            s['request'], s['status'] = None, 'pending'
            jobs.write(path, s)
            return self._public(s)

    def pause(self, task_id):
        with jobs.locked(self.root, task_id) as path:
            s = jobs.read(path)
            if s['status'] == 'completed':
                return self._public(s)
            s['status'] = 'paused'
            jobs.write(path, s)
            return self._public(s)

    def resume(self, task_id, from_stage=None, mode=None, force=False):
        if type(force) is not bool:
            raise CliError('BAD_USAGE', 'force 必须为布尔值', 2)
        with jobs.locked(self.root, task_id) as path:
            s = jobs.read(path)
            if s['status'] == 'completed' and not from_stage:
                if (mode and mode != s['mode']) or (force and not s['force']):
                    if mode:
                        s['mode'] = mode
                    if force:
                        s['force'] = True
                    jobs.write(path, s)
                return self._public(s)
            if from_stage:
                s['status'] = 'pending'
            self._check_input(s)
            if from_stage:
                if from_stage != 'parsing' and s['text'] is None:
                    raise CliError('BUILD_INPUT_INVALID', '必须先完成解析', 2)
                if from_stage in ('indexing', 'finalize') and not s['chunks']:
                    raise CliError('BUILD_INPUT_INVALID', '必须先完成切片', 2)
                if from_stage == 'finalize' and 'description' not in s['metadata']:
                    raise CliError('BUILD_INPUT_INVALID', '必须先完成索引', 2)
                self._reset(s, from_stage)
            if mode:
                s['mode'] = mode
            if force:
                s['force'] = True
            self._refresh_configuration(s)
            s.pop('error', None)
            s['status'] = 'needs_input' if s['request'] else ('completed' if s['result_path'] else 'pending')
            jobs.write(path, s)
            return self._public(s)

    def reconfigure(self, task_id, values):
        with jobs.locked(self.root, task_id) as path:
            s = jobs.read(path)
            updated = dict(s['config'])
            for key, value in values.items():
                updated[key] = config.parse_value(key, value)
            config.validate(updated)
            changed = {k for k in updated if updated[k] != s['config'][k]}
            parser_keys = config.PARSING_KEYS
            chunk_keys = config.CHUNKING_KEYS
            affected = 'parsing' if changed & parser_keys else ('chunking' if changed & chunk_keys else None)
            if affected and s['status'] == 'completed':
                raise CliError('BUILD_INPUT_INVALID', '修改已完成任务的构建配置前，请先显式 resume --from ' + affected, 2)
            s['config'] = updated
            if 'parser' in changed:
                s['parser_override'] = updated['parser']
            if affected:
                current = STAGES.index(s['stage'])
                target = min(current, STAGES.index(affected))
                self._reset(s, STAGES[target])
            elif changed and s['request'] and s['request']['kind'] == 'configuration':
                s['request'] = None
                s['status'] = 'pending'
            jobs.write(path, s)
            return self._public(s)

    def work(self, task_id, batch_size=8, max_steps=1000):
        if type(batch_size) is not int or not 1 <= batch_size <= 16 or type(max_steps) is not int or max_steps < 1:
            raise CliError('BAD_USAGE', 'batch-size 需为 1–16，max-steps 必须为正整数', 2)
        return self.run(task_id, max_steps=max_steps, managed=True, batch_size=batch_size)

    def run(self, task_id, mode=None, until=None, max_steps=100, *, managed=False, batch_size=8):
        if mode:
            self.resume(task_id, mode=mode)
        for step in range(max_steps):
            state = self.next(task_id, managed=managed, batch_size=batch_size)
            if state['status'] in ('paused', 'completed', 'failed') or (until and state['stage'] == until):
                return state
            if step + 1 < max_steps and state['status'] == 'running' and state['stage'] == 'parsing':
                self.sleeper(3)
            if state['status'] == 'needs_input':
                if state['request']['kind'] == 'configuration' or state['mode'] == 'agent':
                    return state
                with jobs.locked(self.root, task_id) as path:
                    saved = jobs.read(path)
                    missing = config.missing(saved['config'], 'llm')
                    if missing:
                        saved['request'] = None
                        self._request(saved, 'configuration', 'llm', questions=missing)
                        jobs.write(path, saved)
                        return self._public(saved)
                try:
                    if self.decision is None:
                        raise CliError('BUILD_ADAPTER_UNAVAILABLE', '自动判断适配器不可用')
                    answer = self.decision.decide(state['request'], saved['config'])
                    self.submit(task_id, {**state['request'], 'result': answer})
                except Exception as exc:
                    with jobs.locked(self.root, task_id) as path:
                        self._save_failure(path, exc)
                    raise
        return self.status(task_id)

    def package(self, task_id):
        with jobs.locked(self.root, task_id) as path:
            s = jobs.read(path)
            if s['status'] != 'completed' or not s['result_path']:
                raise CliError('BUILD_INPUT_INVALID', '任务尚未完成', 2)
            root = Path(s['result_path'])
            if not root.is_dir():
                raise CliError('BUILD_INTEGRITY_FAILED', '成品目录不存在；恢复目录后打包，或显式 resume --from finalize 重新生成')
            try:
                if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
                    raise ValueError('unsafe output')
                files = {}
                excluded = {'.learnova', '.git', '.hg', '.svn', '.ssh', '.aws', '.gnupg',
                            '.learnova-install.json', '.learnova-build.json', 'auth.json',
                            'credentials.json', 'credentials', '.netrc', '.npmrc', '.pypirc'}
                def unreadable(error):
                    raise error
                for directory, directories, names in os.walk(root, followlinks=False, onerror=unreadable):
                    for name in list(directories) + names:
                        file = Path(directory) / name
                        relative = file.relative_to(root).as_posix()
                        mode = file.lstat().st_mode
                        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)) or not skill_build._safe_path(relative):
                            raise ValueError('unsafe file')
                        lower = name.lower()
                        omit = (lower in excluded or lower == '.env' or lower.startswith(('.env.', '.learnova-'))
                                or lower.endswith(('.pem', '.key')) or lower in ('id_rsa', 'id_ed25519', 'id_ecdsa', 'id_dsa'))
                        if omit:
                            if name in directories:
                                directories.remove(name)
                            continue
                        if stat.S_ISREG(mode):
                            files[relative] = file.read_bytes()
                skill_build.validate_package(files)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise CliError('BUILD_INTEGRITY_FAILED', '当前成品目录包含缺失或不安全的文件') from exc
            archive = path / 'skill.zip'
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as out:
                for name, content in files.items():
                    out.writestr(name, content)
            return {'task_id': task_id, 'package': str(archive), 'guidance': '请通过可用渠道自行发布；CLI 尚未集成上传。'}
