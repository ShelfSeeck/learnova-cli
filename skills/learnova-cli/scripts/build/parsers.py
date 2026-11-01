"""Resumable MinerU V4 HTTP parsing. PDF dependency is loaded at use only."""
import base64
import copy
import io
import json
import os
import posixpath
import re
import stat
import uuid
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import net
from core import page_provenance, skill_build
from core.errors import CliError
from .config import require_http


def _error(code='BUILD_PARSER_RESPONSE_INVALID', message='解析服务返回格式无效'):
    raise CliError(code, message) from None


def _dependency(name):
    try:
        return __import__(name)
    except ImportError as exc:
        raise CliError('BUILD_DEPENDENCY_MISSING',
                       f'缺少 {name}；使用 uv pip install --python <运行解释器> {name} 安装') from exc


def _pdf(source):
    try:
        return _dependency('pypdf').PdfReader(str(source))
    except CliError:
        raise
    except Exception as exc:
        raise CliError('BUILD_INPUT_INVALID', '无法读取 PDF 页数；请确认 PDF 有效且未加密', 2) from exc


def _pdf_bytes(source, part, count):
    try:
        if part['start_page'] == 0 and part['end_page'] == count:
            return source.read_bytes()
        module = _dependency('pypdf')
        reader = _pdf(source)
        writer = module.PdfWriter()
        for index in range(part['start_page'], part['end_page']):
            writer.add_page(reader.pages[index])
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except CliError:
        raise
    except Exception as exc:
        raise CliError('BUILD_INPUT_INVALID', '无法拆分 PDF', 2) from exc


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        _error()
    return value


def _url(value):
    # Signed query parameters may exist in memory only. Userinfo is never valid.
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == '\\' for c in value):
        _error()
    try:
        parts = urlsplit(value)
        if (parts.scheme not in ('http', 'https')
                or not parts.hostname or parts.username or parts.password or parts.fragment):
            _error()
        _ = parts.port
    except (TypeError, ValueError):
        _error()
    return value


def validate_checkpoint(state, secrets=()):
    """Source content may contain URLs; transport metadata must never contain them."""
    if not isinstance(state, dict):
        raise CliError('BUILD_INTEGRITY_FAILED', '解析状态必须是对象')
    def walk(value, path=()):
        # Only these exact production result locations carry original document text.
        content = (len(path) == 4 and path[0] == 'parts' and isinstance(path[1], int)
                   and path[2:] == ('result', 'markdown'))
        normalization = (len(path) == 6 and path[0] == 'parts' and isinstance(path[1], int)
                         and path[2:4] == ('result', 'normalizations') and isinstance(path[4], int)
                         and path[5] in ('original', 'replacement'))
        if isinstance(value, str):
            if any(secret and secret in value for secret in secrets):
                raise CliError('BUILD_INTEGRITY_FAILED', '解析状态不能保存认证信息')
            if not (content or normalization) and re.search(r'https?://', value, re.I):
                raise CliError('BUILD_INTEGRITY_FAILED', '解析传输状态不能保存 URL')
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(key)
                walk(item, path + (key,))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, path + (index,))
    walk(state)


def _redact(value, secret=''):
    """Only private raw page metadata uses this; opaque API replies are never saved."""
    if isinstance(value, str):
        value = value.replace(secret, '[redacted]') if secret else value
        return re.sub(r'https?://[^\s<>"\']+', '[url]', value, flags=re.I)
    if isinstance(value, list):
        return [_redact(v, secret) for v in value]
    if isinstance(value, dict):
        return {_redact(k, secret): _redact(v, secret) for k, v in value.items()}
    return value


def _page_ranges(metadata, markdown, page_offset, page_count):
    candidates = []
    def walk(node, page=None):
        if isinstance(node, list):
            for item in node:
                walk(item, page)
        elif isinstance(node, dict):
            page = node.get('page_idx', page)
            text = node.get('text')
            if not isinstance(text, str) and isinstance(node.get('lines'), list):
                text = ''.join(span.get('content', '') for line in node['lines'] if isinstance(line, dict)
                               for span in line.get('spans', []) if isinstance(span, dict) and isinstance(span.get('content'), str))
            if type(page) is int and 0 <= page < page_count and isinstance(text, str) and text:
                start, end = node.get('start'), node.get('end')
                if not (type(start) is int and type(end) is int and 0 <= start < end <= len(markdown) and markdown[start:end] == text):
                    start = markdown.find(text)
                    end = start + len(text)
                    if start < 0 or markdown.find(text, start + 1) != -1:
                        start = -1
                if start >= 0:
                    candidates.append({'start': start, 'end': end, 'page': page_offset + page + 1})
            # Read known containers only, not arbitrary service fields or coordinates.
            for key in ('pages', 'pdf_info', 'blocks', 'para_blocks', 'content_list', 'children'):
                if key in node:
                    walk(node[key], page)
    for data in metadata:
        walk(data)
    unique = sorted({(s['start'], s['end'], s['page']) for s in candidates})
    ranges = []
    for start, end, page in unique:
        # Overlapping entries that disagree on page are ambiguous; keep neither.
        if any(a < end and b > start and p != page for a, b, p in unique):
            continue
        if ranges and start < ranges[-1]['end']:
            if page == ranges[-1]['page']:
                ranges[-1]['end'] = max(end, ranges[-1]['end'])
            continue
        ranges.append({'start': start, 'end': end, 'page': page})
    return {'status': page_provenance.status(ranges, len(markdown)), 'ranges': ranges}


def read_archive(data, cfg, number, page_offset, page_count, secret=''):
    """Confined in-memory ZIP reading; never extract server paths to the filesystem."""
    def bad():
        _error('BUILD_PARSER_ARCHIVE_INVALID', '解析 ZIP 的路径、类型、大小或内容无效')
    if len(data) > cfg['parser_archive_bytes']:
        bad()
    files, seen, dirs, total = {}, set(), set(), 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 10000:
                bad()
            for info in archive.infolist():
                name = info.filename[:-1] if info.is_dir() else info.filename
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if (not skill_build._safe_path(name) or '\\' in info.orig_filename or '\x00' in info.orig_filename or name.casefold() in seen or info.flag_bits & 1
                        or kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                        or (kind == stat.S_IFDIR and not info.is_dir())
                        or (info.is_dir() and kind == stat.S_IFREG)):
                    bad()
                seen.add(name.casefold())
                if info.is_dir():
                    dirs.add(name)
                    continue
                if info.file_size > cfg['parser_member_bytes']:
                    bad()
                total += info.file_size
                if total > cfg['parser_expanded_bytes']:
                    bad()
                with archive.open(info) as entry:
                    content = entry.read(cfg['parser_member_bytes'] + 1)
                if len(content) != info.file_size or len(content) > cfg['parser_member_bytes']:
                    bad()
                files[name] = content
            file_names = {n.casefold() for n in files}
            for name in list(files) + list(dirs):
                parent = posixpath.dirname(name)
                while parent:
                    if parent.casefold() in file_names:
                        bad()
                    parent = posixpath.dirname(parent)
        markdowns = [n for n in files if n.lower().endswith('.md')]
        canonical = [n for n in markdowns if posixpath.basename(n) in ('full.md', 'markdown.md')]
        choices = canonical or markdowns
        if len(choices) != 1:
            bad()
        selected = choices[0]
        markdown = files[selected].decode('utf-8-sig')
        metadata = [json.loads(content) for name, content in files.items()
                    if posixpath.basename(name) in ('structured_content.json', 'middle_json.json', 'content_list.json')
                    or name.endswith('_content_list.json')]
    except CliError:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError, ValueError, UnicodeError, NotImplementedError):
        bad()
    mapping = _page_ranges(metadata, markdown, page_offset, page_count)
    edits, assets = [], {}
    try:
        images = skill_build.extract_images(markdown)
    except (CliError, ValueError):
        # The shared extractor includes untrusted reference labels in its errors.
        bad()
    for image in images:
        try:
            url = urlsplit(image['destination'])
            if url.scheme or url.netloc or url.query or url.fragment or '\\' in url.path:
                bad()
            raw = unquote(url.path)
            if raw.startswith('/'):
                bad()
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(selected), raw))
            if not skill_build._safe_path(resolved) or resolved not in files:
                bad()
            suffix = Path(resolved).suffix.lower()
            if suffix not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'):
                bad()
            name = f'images/part-{number + 1:04d}/{len(assets) + 1:04d}{suffix}'
            assets[name] = files[resolved]
            if image['kind'] == 'reference':
                start, end = image['usage_start'], image['usage_end']
                alt = markdown[start + 2:image['alt_end']]
                replacement = f'![{alt}]({name})'
            else:
                start, end, replacement = image['start'], image['end'], name
            edits.append({'start': start, 'end': end, 'original': markdown[start:end], 'replacement': replacement})
        except ValueError:
            bad()
    original_length = len(markdown)
    for edit in sorted(edits, key=lambda e: e['start'], reverse=True):
        markdown = markdown[:edit['start']] + edit['replacement'] + markdown[edit['end']:]
    return {'markdown': markdown, 'assets': {k: base64.b64encode(v).decode('ascii') for k, v in assets.items()},
            'page_map': page_provenance.translate(mapping, edits, original_length),
            'raw_page_metadata': _redact(metadata, secret), 'normalizations': edits}


class Parser:
    def advance(self, source, config, state, checkpoint):
        cfg = config
        require_http(cfg)
        state = copy.deepcopy(state)
        if not state:
            try:
                count = len(_pdf(source).pages)
            except CliError:
                raise
            except Exception as exc:
                raise CliError('BUILD_INPUT_INVALID', '无法读取 PDF 页面；请确认 PDF 有效且未加密', 2) from exc
            if count < 1:
                _error('BUILD_INPUT_INVALID', 'PDF 没有页面')
            size = cfg['parser_pages_per_part']
            state = {'version': 1, 'page_count': count, 'warnings': [],
                     'parts': [{'start_page': i, 'end_page': min(i + size, count), 'status': 'pending',
                                'data_id': uuid.uuid4().hex, 'attempts': 0} for i in range(0, count, size)]}
            checkpoint(state)
        for number, part in enumerate(state['parts']):
            if part['status'] == 'done':
                continue
            self._http(source, cfg, state, part, number, checkpoint)
            return {'status': 'running', 'progress': {'completed_parts': sum(p['status'] == 'done' for p in state['parts']),
                    'total_parts': len(state['parts']), 'part': number + 1, 'state': part['status'],
                    'service_state': part.get('service_state'), 'total_pages': state['page_count'],
                    'completed_pages': sum(p['end_page'] - p['start_page'] for p in state['parts'] if p['status'] == 'done')
                                       + (part.get('extracted_pages', 0) if part['status'] != 'done' else 0),
                    'warnings': list(state['warnings'])}}
        texts, assets, ranges, offset = [], {}, [], 0
        for part in state['parts']:
            result = part['result']
            if texts:
                texts.append('\n\n')
                offset += 2
            texts.append(result['markdown'])
            assets.update({k: base64.b64decode(v) for k, v in result['assets'].items()})
            ranges.extend(dict(r, start=r['start'] + offset, end=r['end'] + offset) for r in result['page_map']['ranges'])
            offset += len(result['markdown'])
        text = ''.join(texts)
        return {'status': 'completed', 'markdown': text, 'assets': assets,
                'page_map': {'status': page_provenance.status(ranges, len(text)), 'ranges': ranges}}

    def _http(self, source, cfg, state, part, number, checkpoint):
        endpoint = cfg['remote_endpoint'].rstrip('/')
        token = os.environ.get(cfg['remote_key_env']) if cfg['remote_key_env'] else None
        if cfg['remote_key_env'] and not token:
            _error('BUILD_CONFIG_INVALID', '缺少已配置的解析密钥环境变量')
        def api(method, path, body=None):
            result = net.request(method, endpoint + path, body=body, token=token,
                                 timeout=cfg['parser_request_timeout'])
            if not isinstance(result, dict) or type(result.get('code')) is not int or result['code'] != 0 or not isinstance(result.get('data'), dict):
                _error('BUILD_PARSER_FAILED', '解析服务拒绝请求或回复无效')
            return result['data']
        if part['status'] == 'pending':
            content = _pdf_bytes(source, part, state['page_count'])
            if len(content) > cfg['parser_max_part_bytes']:
                _error('BUILD_INPUT_INVALID', 'PDF 分片超过上传上限；请调小 parser_pages_per_part')
            data = api('POST', '/api/v4/file-urls/batch', {
                'enable_formula': cfg['parser_formula'], 'enable_table': cfg['parser_table'],
                'language': cfg['parser_language'], 'model_version': cfg['remote_model_version'],
                'files': [{'name': f'part-{number + 1:04d}.pdf', 'data_id': part['data_id'], 'is_ocr': cfg['parser_ocr']}]})
            batch = _id(data.get('batch_id'))
            if token and token in batch:
                _error()
            part.update(batch_id=batch, status='submitted', attempts=part['attempts'] + 1)
            checkpoint(state)  # The server ID survives even malformed upload URL replies.
            urls = data.get('file_urls')
            if not isinstance(urls, list) or len(urls) != 1:
                _error()
            upload = _url(urls[0])
            try:
                net.request_bytes('PUT', upload, body=content, timeout=cfg['parser_upload_timeout'], max_bytes=1024 * 1024)
            except CliError as exc:
                if exc.code in ('HTTP_ERROR', 'UNAUTHORIZED'):
                    raise CliError('BUILD_PARSER_UPLOAD_FAILED', '预签名文件上传被拒绝；检查上传头或链接时效，恢复任务重取上传链接。此阶段不使用 API Token') from None
                raise
            part['status'] = 'uploaded'
            checkpoint(state)
            return
        checkpoint(state)  # Persist identifier before polling or re-acquiring result URLs.
        data = api('GET', '/api/v4/extract-results/batch/' + _id(part['batch_id']))
        results = data.get('extract_result')
        if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
            _error()
        result = results[0]
        if result.get('data_id') not in (None, part['data_id']):
            _error()
        status = result.get('state')
        if status in ('pending', 'running', 'converting', 'waiting-file', 'done', 'failed'):
            part['service_state'] = status
            progress = result.get('extract_progress')
            extracted = progress.get('extracted_pages') if isinstance(progress, dict) else None
            if type(extracted) is int and 0 <= extracted <= part['end_page'] - part['start_page']:
                part['extracted_pages'] = extracted
        if status == 'done':
            url = _url(result.get('full_zip_url'))
            part['status'] = 'downloading'
            checkpoint(state)
            archive = net.request_bytes('GET', url, timeout=cfg['parser_download_timeout'], max_bytes=cfg['parser_archive_bytes'])
            parsed = read_archive(archive, cfg, number, part['start_page'], part['end_page'] - part['start_page'], token)
            if token and token in json.dumps(parsed, ensure_ascii=False):
                _error('BUILD_PARSER_RESPONSE_INVALID', '解析结果意外包含认证信息')
            part['result'] = parsed
            part['status'] = 'done'
            checkpoint(state)
        elif status == 'waiting-file' and part['status'] == 'submitted':
            part['status'] = 'pending'
            if 'upload_resubmitted' not in state['warnings']:
                state['warnings'].append('upload_resubmitted')
            checkpoint(state)
        elif status in ('pending', 'running', 'converting', 'waiting-file'):
            return
        elif status == 'failed':
            _error('BUILD_PARSER_FAILED', '解析服务任务失败；检查输入后重置解析阶段')
        else:
            _error()
