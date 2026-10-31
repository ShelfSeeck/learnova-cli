"""Atomic job state and interprocess locks."""
import contextlib
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
import store
from core.errors import CliError


def task_dir(root, task_id):
    if not re.fullmatch(r'[a-f0-9]{32}', task_id or ''):
        raise CliError('BUILD_TASK_NOT_FOUND', '无效任务编号', 2)
    base = Path(root).resolve()
    path = base / '.learnova' / 'builds' / task_id
    if any(part.is_symlink() for part in (base / '.learnova', base / '.learnova' / 'builds', path)) or not path.resolve().is_relative_to(base):
        raise CliError('BUILD_INTEGRITY_FAILED', '任务目录不能包含符号链接')
    return path


def create_dir(root):
    base = Path(root).resolve() / '.learnova'
    if base.is_symlink() or (base / 'builds').is_symlink():
        raise CliError('BUILD_INTEGRITY_FAILED', '任务目录不能包含符号链接')
    base.mkdir(parents=True, exist_ok=True)
    ignore = base / '.gitignore'
    if not ignore.exists():
        try:
            with ignore.open('x', encoding='utf-8') as f:
                f.write('builds/\n')
        except FileExistsError:
            pass
    task_id = uuid.uuid4().hex
    path = task_dir(root, task_id)
    path.mkdir(parents=True)
    return task_id, path


@contextlib.contextmanager
def locked(root, task_id):
    path = task_dir(root, task_id)
    if not path.is_dir():
        raise CliError('BUILD_TASK_NOT_FOUND', '任务不存在', 1)
    if (path / '.lock').is_symlink() or (path / 'job.json').is_symlink():
        raise CliError('BUILD_INTEGRITY_FAILED', '任务文件不能是符号链接')
    with (path / '.lock').open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            if not lock.read(1):
                lock.write(b'0'); lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield path
        finally:
            if os.name == 'nt':
                lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def _seals(data):
    from .config import PARSING_KEYS, CHUNKING_KEYS
    # Each seal includes its prerequisites, so a mismatch cannot be skipped.
    cfg = data['config']
    parse_config = {k: cfg[k] for k in PARSING_KEYS}
    chunk_config = {k: cfg[k] for k in CHUNKING_KEYS}
    parsing = _hash([data['input_sha256'], parse_config, data['text'], data.get('text_sha256'), data.get('structure'), data['assets'], data['normalizations'], data['parser_state'], data['page_map'], data.get('normalized_page_map')])
    chunk_parts = [parsing, chunk_config, data['chunks'], data['protected']]
    if 'rules_sha256' in data:
        chunk_parts.append(data['rules_sha256'])
    chunks = _hash(chunk_parts)
    semantic_parts = [chunks, data['metadata']]
    if 'render_version' in data:
        semantic_parts.append(data['render_version'])
    semantics = _hash(semantic_parts)
    return {'parsing': parsing, 'chunking': chunks, 'indexing': semantics,
            'config': _hash(cfg), 'request': _hash(data['request']),
            'control': _hash([data[k] for k in ('task_id', 'revision', 'document_revision', 'input', 'name', 'output', 'force', 'mode', 'parser_override', 'stage', 'status', 'result_path')] + [data.get('error'), data.get('commit_intent')])}


def read(path):
    try:
        data = store._read_json(path / 'job.json')
        if (not data or data.get('schema_version') != 1 or data['stage'] not in ('parsing', 'chunking', 'indexing', 'finalize')
                or data['status'] not in ('pending', 'running', 'needs_input', 'paused', 'failed', 'completed')
                or data.get('validity') != _seals(data)):
            raise ValueError('invalid state')
        return data
    except (CliError, KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise CliError('BUILD_INTEGRITY_FAILED', '任务断点缺失或损坏；请保留文件并重新创建任务') from exc


def write(path, data):
    data['revision'] += 1
    data['validity'] = _seals(data)
    store._write_json(path / 'job.json', data)
