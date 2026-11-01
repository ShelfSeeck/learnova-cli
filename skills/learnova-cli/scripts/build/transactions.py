"""Private, per-output build commit journal and crash recovery."""
import contextlib
import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path

import engine
import store
from core.errors import CliError
from core.package_paths import has_unsafe_symlink_ancestor


def _error():
    raise CliError('BUILD_INTEGRITY_FAILED', '目录事务记录无效；保留暂存目录供检查')


def fingerprint(files):
    return hashlib.sha256(b''.join(name.encode('utf-8') + b'\0' + hashlib.sha256(value).hexdigest().encode('ascii') for name, value in sorted(files.items()))).hexdigest()



def _read_private_json(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                _error()
            data = stream.read(16385)
        if len(data) > 16384:
            _error()
        result = json.loads(data)
        if not isinstance(result, dict):
            _error()
        return result
    except (OSError, ValueError, UnicodeError) as exc:
        raise CliError('BUILD_INTEGRITY_FAILED', '无法读取目录事务记录') from exc

def _tree_hash(root):
    if root.is_symlink() or not root.is_dir():
        _error()
    files = {}
    try:
        for path in root.rglob('*'):
            if path.is_symlink():
                _error()
            if path.is_file():
                files[path.relative_to(root).as_posix()] = path.read_bytes()
    except OSError as exc:
        raise CliError('BUILD_INTEGRITY_FAILED', '无法检查目录事务') from exc
    return fingerprint(files)


def _original_hash(root):
    """Hash original tree entries without following links, including empty directories."""
    if root.is_symlink() or not root.is_dir():
        _error()
    entries = {}
    try:
        entries['.'] = str(stat.S_IMODE(root.lstat().st_mode)).encode('ascii')
        for path in root.rglob('*'):
            mode = path.lstat().st_mode
            name = path.relative_to(root).as_posix()
            if stat.S_ISLNK(mode):
                entries[name] = b'link\0' + os.fsencode(os.readlink(path))
            elif stat.S_ISDIR(mode):
                entries[name] = b'directory\0' + str(stat.S_IMODE(mode)).encode('ascii')
            elif stat.S_ISREG(mode):
                entries[name] = b'file\0' + str(stat.S_IMODE(mode)).encode('ascii') + b'\0' + path.read_bytes()
            else:
                _error()
    except OSError as exc:
        raise CliError('BUILD_INTEGRITY_FAILED', '无法检查旧目录事务') from exc
    return fingerprint(entries)


def _safe_final(final):
    if has_unsafe_symlink_ancestor(final):
        _error()


def _recover(final, journal):
    if journal.is_symlink():
        _error()
    if not journal.exists():
        return
    try:
        data = _read_private_json(journal)
        work_name, token, expected = data['work'], data['token'], data['sha256']
        restored = data.get('recovery_sha256')
        if (data['schema_version'] != 1 or data['target'] != final.name
                or not isinstance(work_name, str) or not re.fullmatch(r'\.learnova-staging-[A-Za-z0-9_-]+', work_name)
                or not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token)
                or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected)
                or (restored is not None and (not isinstance(restored, str) or not re.fullmatch(r'[0-9a-f]{64}', restored)))):
            _error()
        work = final.parent / work_name
        if work.is_symlink() or not work.is_dir():
            _error()
        owner = work / '.learnova-owner.json'
        if owner.is_symlink() or _read_private_json(owner) != {'target': final.name, 'token': token}:
            _error()
        backup, candidate = work / 'backup', work / 'candidate'
        if backup.is_symlink() or candidate.is_symlink():
            _error()
        _safe_final(final)
        if backup.exists():
            if not backup.is_dir():
                _error()
            backup_hash = _original_hash(backup)
            if restored is not None and restored != backup_hash:
                _error()
            if final.exists() and (candidate.exists() or _tree_hash(final) != expected):
                _error()
            if restored is None:
                data['recovery_sha256'] = backup_hash
                store._write_json(journal, data)
            if final.exists():
                engine._remove_generated(final)
            os.replace(backup, final)
            if _original_hash(final) != backup_hash:
                _error()
        elif restored is not None:
            if not final.exists() or _original_hash(final) != restored:
                _error()
        elif final.exists() and not candidate.exists() and _tree_hash(final) != expected:
            _error()
        journal.unlink()
        engine._remove_generated(work)
    except (KeyError, TypeError, ValueError, OSError, CliError) as exc:
        raise CliError('BUILD_INTEGRITY_FAILED', '无法恢复目录事务；保留暂存目录供检查') from exc


@contextlib.contextmanager
def transaction(final, sha256):
    final = Path(os.path.abspath(final))
    _safe_final(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    _safe_final(final)
    key = hashlib.sha256(os.path.normcase(str(final)).encode('utf-8')).hexdigest()
    lock_path = final.parent / ('.learnova-txn-' + key + '.lock')
    journal = final.parent / ('.learnova-txn-' + key + '.json')
    if lock_path.is_symlink() or journal.is_symlink():
        _error()
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise CliError('BUILD_INTEGRITY_FAILED', '无法锁定目录事务') from exc
    with os.fdopen(fd, 'a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            if not lock.read(1):
                lock.write(b'0'); lock.flush()
            lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            def check_lock():
                try:
                    held = os.fstat(lock.fileno())
                    current = os.stat(lock_path, follow_symlinks=False)
                    if not stat.S_ISREG(held.st_mode) or held.st_nlink != 1 or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
                        _error()
                except OSError as exc:
                    raise CliError('BUILD_INTEGRITY_FAILED', '目录事务锁已改变') from exc
            check_lock()
            _safe_final(final)
            _recover(final, journal)
            journal_created = False
            def prepare(target, work):
                nonlocal journal_created
                check_lock()
                _safe_final(final)
                if target != final or work.parent != final.parent or work.is_symlink() or not work.name.startswith(engine.STAGING_PREFIX):
                    _error()
                token = uuid.uuid4().hex
                store._write_json(work / '.learnova-owner.json', {'target': final.name, 'token': token})
                store._write_json(journal, {'schema_version': 1, 'target': final.name, 'work': work.name, 'token': token, 'sha256': sha256})
                journal_created = True
            def complete():
                check_lock()
                _safe_final(final)
                if not journal_created:
                    return
                if journal.is_symlink() or not journal.is_file():
                    _error()
                journal.unlink()
            try:
                yield prepare, complete
            finally:
                # The engine removes work after successful commits and successful rollbacks.
                if journal.exists() and not journal.is_symlink():
                    data = _read_private_json(journal)
                    work = final.parent / data.get('work', '')
                    if work.parent == final.parent and not work.exists():
                        journal.unlink()
        finally:
            if os.name == 'nt':
                lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)
