"""MinerU V4 wire and installed-package tests with synthetic PDFs and localhost."""
import base64
import copy
from contextlib import contextmanager
import http.client
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types
import unittest
from unittest.mock import patch
import warnings
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/learnova-cli/scripts'
sys.path.insert(0, str(SCRIPTS))
from build import config, jobs, parsers
from build.pipeline import BuildService
from core import chunking, document, page_provenance, skill_build
from core.errors import CliError
import net

PNG = b'\x89PNG\r\n\x1a\n'
SECRET = 'synthetic-key-never-saved'
SIGNED = 'synthetic-signed-token'


def archive(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, value in entries:
            # ZipInfo normalizes backslashes on Windows during construction.
            # Preserve the intentionally malicious raw member name in the ZIP
            # so the archive parser is tested against the same bytes on all OSes.
            if isinstance(name, str) and '\\' in name:
                info = zipfile.ZipInfo(name)
                info.filename = name
                z.writestr(info, value)
            else:
                z.writestr(name, value)
    return out.getvalue()


def sample_zip(index=0, modern=False):
    text = f'# Part {index + 1}\n\n![figure](images/shared.png)\n\nUnique content {index + 1}.\n'
    data = [{'page_idx': 0, 'text': text}]
    if modern:
        data = {'pages': [{'page_idx': 0, 'blocks': [{'text': text}]}]}
    return archive([('document/' + ('markdown.md' if modern else 'full.md'), text),
                    ('document/images/shared.png', PNG + bytes([index])),
                    ('document/' + ('structured_content.json' if modern else 'part_content_list.json'), json.dumps(data))])


class Fixture:
    def __init__(self):
        self.calls, self.batches = [], {}
        self.fail_post = None
        self.fail_put = False
        self.fail_download = False
        self.archive_override = None
        self.poll_state = None
        self.delay = 0
        self.model_responses = []
        self.model_status = 200
        fixture = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def send(self, data, status=200, content_type='application/json'):
                if isinstance(data, (dict, list)):
                    data = json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Content-Type', content_type)
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def record(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                fixture.calls.append((self.command, self.path, self.headers.get('Authorization'), body))
                return body
            def do_POST(self):
                body = json.loads(self.record())
                if self.path == '/chat/completions':
                    if fixture.model_status != 200:
                        self.send(SECRET.encode(), fixture.model_status)
                        return
                    if fixture.model_responses:
                        self.send(fixture.model_responses.pop(0))
                        return
                    request = json.loads(body['messages'][1]['content'])
                    kind = request['kind']
                    if kind == 'chunk_metadata_batch':
                        result = {'chunks': {key: {'title': 'Topic', 'summary': 'Reference', 'keywords': ['topic']} for key in request['scope']['ids']}}
                    else:
                        result = {'splits': []} if kind == 'split_plan' else ({'title': 'Topic', 'summary': 'Reference', 'keywords': ['topic']} if kind == 'chunk_metadata' else {'name': 'sample', 'description': 'Reference'})
                    self.send({'choices': [{'message': {'content': json.dumps({'result': result})}}]})
                    return
                if fixture.fail_post is not None:
                    self.send(fixture.fail_post)
                    return
                number = len(fixture.batches)
                key = 'batch-' + str(number)
                fixture.batches[key] = {'body': body, 'uploaded': False, 'index': number}
                self.send({'code': 0, 'data': {'batch_id': key, 'file_urls': [fixture.url + '/upload/' + key + '?signature=' + SIGNED]}})
            def do_PUT(self):
                self.record()
                if self.headers.get('Content-Type', ''):
                    self.send(b'SignatureDoesNotMatch', 403)
                    return
                if fixture.fail_put:
                    self.send(b'error ' + SECRET.encode(), 503)
                    return
                key = self.path.split('/')[-1].split('?')[0]
                fixture.batches[key]['uploaded'] = True
                self.send(b'')
            def do_GET(self):
                self.record()
                if fixture.delay:
                    time.sleep(fixture.delay)
                if self.path.startswith('/download/'):
                    if fixture.fail_download:
                        self.send(b'error ' + SECRET.encode(), 503)
                        return
                    key = self.path.split('/')[-1].split('?')[0]
                    index = fixture.batches[key]['index']
                    self.send(fixture.archive_override if fixture.archive_override is not None else sample_zip(index, modern=index % 2 == 1), content_type='application/zip')
                    return
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', fixture.redirect_target)
                    self.end_headers()
                    return
                if self.path.startswith('/api/v4/extract-results/batch/'):
                    key = self.path.split('/')[-1]
                    batch = fixture.batches[key]
                    state = fixture.poll_state or ('done' if batch['uploaded'] else 'waiting-file')
                    self.send({'code': 0, 'data': {'extract_result': [{'data_id': batch['body']['files'][0]['data_id'], 'state': state,
                        'err_msg': SECRET, 'full_zip_url': fixture.url + '/download/' + key + '?signature=' + SIGNED}]}})
                    return
                if self.path.startswith('/malformed'):
                    self.send(('not JSON ' + SECRET).encode())
                    return
                self.send(b'hello', content_type='application/octet-stream')
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class ModelTests(unittest.TestCase):
    def setUp(self):
        from build.decisions import Decision
        self.fixture = Fixture()
        self.reads = []
        self.decision = Decision(lambda start, end, limit, kind: self.reads.append((start, end, limit, kind)) or {'items': [{'id': 'b999'}]})
        self.cfg = dict(config.DEFAULTS, llm_base_url=self.fixture.url, llm_model='synthetic', llm_key_env='TEST_MODEL_KEY')
        self.env = patch.dict(os.environ, {'TEST_MODEL_KEY': SECRET})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.fixture.close()

    def response(self, content):
        return {'choices': [{'message': {'content': content}}]}

    def test_managed_auto_start_finishes_without_host_submissions(self):
        from build.pipeline import BuildService
        import store
        with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'LEARNOVA_HOME', Path(tmp) / 'home'):
            root = Path(tmp)
            source = root / 'source.md'
            source.write_text('# Topic\n\nSynthetic reference content.\n')
            config.set_values(root, {key: self.cfg[key] for key in ('llm_base_url', 'llm_model', 'llm_key_env')})
            state = BuildService(root).start(source, mode='auto')
            self.assertEqual(state['status'], 'completed')
            requests = [json.loads(json.loads(call[3])['messages'][1]['content']) for call in self.fixture.calls]
            self.assertEqual([req['kind'] for req in requests], ['split_plan', 'chunk_metadata_batch', 'skill_metadata'])
            reference = next((Path(state['result_path']) / 'references').glob('*.md'))
            self.assertEqual(reference.read_text(), source.read_text())
            from build.decisions import Decision
            service = BuildService(root)
            task = service.create(source, mode='auto')['task_id']
            service.decision = Decision(lambda start, end, limit, kind: service.read(task, start, end, limit, kind))
            limited = service.run(task, max_steps=1)
            self.assertEqual(limited['status'], 'pending')
            self.assertIsNone(limited['request'])
            self.assertEqual(limited['next_action'], 'work')

    def test_http_paging_auth_and_late_boundary(self):
        self.fixture.model_responses = [self.response(json.dumps({'read': {'kind': 'boundaries', 'start': 100, 'limit': 100}})), self.response(json.dumps({'result': {'splits': ['b999']}}))]
        self.assertEqual(self.decision.decide({'kind': 'split_plan'}, self.cfg), {'splits': ['b999']})
        self.assertEqual(self.reads, [(100, None, 100, 'boundaries')])
        calls = self.fixture.calls
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][2], 'Bearer ' + SECRET)
        payload = json.loads(calls[1][3])
        self.assertEqual(payload['model'], 'synthetic')
        self.assertIn('b999', payload['messages'][-1]['content'])

    def test_invalid_and_secret_echo_are_redacted(self):
        for response in (self.response('{'), self.response(json.dumps({'result': {'title': SECRET}})), self.response('{\"result\":{\"title\":\"\\u0073ynthetic-key-never-saved\"}}'), {'wrong': SECRET}):
            self.fixture.model_responses = [response]
            with self.assertRaises(CliError) as caught:
                self.decision.decide({'kind': 'split_plan'}, self.cfg)
            self.assertEqual(caught.exception.code, 'BUILD_MODEL_RESPONSE_INVALID')
            self.assertNotIn(SECRET, str(caught.exception))

    def test_http_error_is_typed_and_redacted(self):
        self.fixture.model_status = 401
        with self.assertRaises(CliError) as caught:
            self.decision.decide({'kind': 'split_plan'}, self.cfg)
        self.assertEqual(caught.exception.code, 'BUILD_MODEL_REQUEST_FAILED')
        self.assertNotIn(SECRET, str(caught.exception))

    def test_bounded_reads(self):
        self.fixture.model_responses = [self.response(json.dumps({'read': {'kind': 'boundaries', 'start': 100, 'limit': 100}})) for _ in range(8)]
        with self.assertRaises(CliError) as caught:
            self.decision.decide({'kind': 'split_plan'}, self.cfg)
        self.assertEqual(caught.exception.code, 'BUILD_MODEL_RESPONSE_INVALID')
        self.assertEqual(len(self.reads), 8)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(config.DEFAULTS)

    def test_review_malformed_image_errors_are_safe(self):
        sensitive = [SECRET, 'https://private-user:private-password@example.invalid/file?token=private-query']
        for label in sensitive:
            with self.subTest(label=label), self.assertRaises(CliError) as raised:
                parsers.read_archive(archive([('full.md', '![' + label + ']')]), self.cfg, 0, 0, 1, SECRET)
            self.assertEqual(raised.exception.code, 'BUILD_PARSER_ARCHIVE_INVALID')
            visible = ''.join(traceback.format_exception(raised.exception))
            for value in (SECRET, 'private-user', 'private-password', 'private-query'):
                self.assertNotIn(value, str(raised.exception))
                self.assertNotIn(value, visible)

    def test_legacy_current_images_and_page_offsets(self):
        for modern in (False, True):
            result = parsers.read_archive(sample_zip(1, modern), self.cfg, 1, 200, 200)
            self.assertIn('images/part-0002/0001.png', result['markdown'])
            self.assertEqual(base64.b64decode(result['assets']['images/part-0002/0001.png']), PNG + b'\1')
            self.assertEqual(result['page_map'], {'status': 'complete', 'ranges': [{'start': 0, 'end': len(result['markdown']), 'page': 201}]})

    def test_single_markdown_relative_reference_image(self):
        data = archive([('root/source.md', '![x][id]\n\n[id]: ../assets/a.png\n'), ('assets/a.png', PNG)])
        result = parsers.read_archive(data, self.cfg, 0, 0, 1)
        self.assertIn('![x](images/part-0001/0001.png)', result['markdown'])
        self.assertEqual(len(result['assets']), 1)
        self.assertEqual(result['page_map']['status'], 'unavailable')

    def test_bad_zip_paths_types_duplicates_and_limits(self):
        items = [('../escape', b'x'), ('/absolute', b'x'), ('C:/drive', b'x'), ('a\\b', b'x')]
        for mode in (stat.S_IFLNK, stat.S_IFCHR, stat.S_IFIFO, stat.S_IFSOCK):
            info = zipfile.ZipInfo('bad')
            info.external_attr = (mode | 0o777) << 16
            items.append((info, b'target'))
        for entry in items:
            with self.subTest(entry=entry[0]), self.assertRaises(CliError) as raised:
                parsers.read_archive(archive([('full.md', 'text'), entry]), self.cfg, 0, 0, 1)
            self.assertEqual(raised.exception.code, 'BUILD_PARSER_ARCHIVE_INVALID')
        bad = [[('full.md', 'one'), ('full.md', 'two')], [('full.md', 'one'), ('FULL.md', 'two')],
               [('a', 'file'), ('a/full.md', 'text')], [('full.md', 'one'), ('markdown.md', 'two')],
               [('first.md', 'one'), ('second.md', 'two')], [('full.md', '![x](../../escape.png)')],
               [('full.md', '![x](https://user:pw@example.invalid/a.png?token=x)')],
               [('full.md', '![x](missing.png)')], [('full.md', b'\xff')]]
        for entries in bad:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                data = archive(entries)
            with self.subTest(entries=entries), self.assertRaises(CliError):
                parsers.read_archive(data, self.cfg, 0, 0, 1)
        bomb = archive([('full.md', 'x' * 10000)])
        for key in ('parser_archive_bytes', 'parser_expanded_bytes', 'parser_member_bytes'):
            with self.subTest(key=key), self.assertRaises(CliError):
                parsers.read_archive(bomb, dict(self.cfg, **{key: 10}), 0, 0, 1)

    def test_metadata_partial_ambiguous_and_scrubbed(self):
        text = '# Heading\n\nsame same\n\nUnique.\n'
        metadata = [[{'page_idx': 0, 'text': 'same'}], [{'page_idx': 1, 'text': 'Unique.', 'private': SECRET + ' https://user:password@example.invalid/a?secret=x'}]]
        result = parsers.read_archive(archive([('full.md', text), ('x_content_list.json', json.dumps(metadata))]), self.cfg, 0, 10, 2, SECRET)
        self.assertEqual(result['page_map']['status'], 'partial')
        self.assertEqual(result['page_map']['ranges'], [{'start': text.index('Unique.'), 'end': text.index('Unique.') + 7, 'page': 12}])
        self.assertNotIn(SECRET, json.dumps(result['raw_page_metadata']))
        self.assertNotIn('password', json.dumps(result['raw_page_metadata']))
        self.assertNotIn('secret=x', json.dumps(result['raw_page_metadata']))
        middle = {'pdf_info': [{'page_idx': 0, 'para_blocks': [{'lines': [{'spans': [{'content': 'Unique.'}]}]}]}]}
        mapped = parsers.read_archive(archive([('markdown.md', text), ('middle_json.json', json.dumps(middle))]), self.cfg, 0, 0, 2)
        self.assertEqual(mapped['page_map']['ranges'][0]['page'], 1)

    def test_numeric_mapping_edits_and_validation(self):
        mapping = {'ranges': [{'start': 0, 'end': 20, 'page': 1}, {'start': 20, 'end': 30, 'page': 2}]}
        translated = page_provenance.translate(mapping, [{'start': 5, 'end': 8, 'replacement': 'longer'}], 30)
        self.assertEqual(translated, {'status': 'complete', 'ranges': [{'start': 0, 'end': 23, 'page': 1}, {'start': 23, 'end': 33, 'page': 2}]})
        self.assertEqual(page_provenance.translate({'ranges': [{'start': 6, 'end': 20, 'page': 1}]}, [{'start': 5, 'end': 8, 'replacement': 'longer'}], 30)['ranges'], [])
        for ranges in ([{'start': 0, 'end': 20, 'page': True}], [{'start': 0, 'end': 99, 'page': 1}], [{'start': 0, 'end': 2, 'page': 1, 'secret': 'bad'}]):
            self.assertFalse(page_provenance.valid_ranges(ranges, 30))

    def test_private_page_mapping_and_minimal_public_origin(self):
        text = 'first\n\nsecond\n'
        chunks = [chunking.make_chunk(text, 0, 7), chunking.make_chunk(text, 7, len(text))]
        metadata = {'name': 'sample', 'description': 'Reference', 'chunks': {c['id']: {'title': 'Title', 'summary': 'Summary', 'keywords': ['topic']} for c in chunks}}
        mapping = {'status': 'complete', 'ranges': [{'start': 0, 'end': len(text), 'page': 7}]}
        page_provenance.validate(mapping, len(text))
        files = skill_build.render(text, chunks, metadata)
        self.assertEqual(json.loads(files['.learnova-build.json']),
                         {'schema_version': 2, 'source': 'local-build', 'version': None})
        mapping['ranges'][0]['path'] = '/private'
        with self.assertRaises(CliError):
            page_provenance.validate(mapping, len(text))


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.pdf'
        self.source.write_bytes(b'%PDF synthetic')
        self.cfg = dict(config.DEFAULTS, remote_endpoint=self.fixture.url, remote_key_env='TEST_PARSER_KEY', parser_pages_per_part=1)
        self.saved = {}
        self.history = []
        self.env = patch.dict(os.environ, {'TEST_PARSER_KEY': SECRET})
        self.env.start()
        self.reader = patch.object(parsers, '_pdf', return_value=types.SimpleNamespace(pages=[0, 1]))
        self.reader.start()
        self.bytes = patch.object(parsers, '_pdf_bytes', side_effect=lambda source, part, count: b'%PDF part-' + str(part['start_page']).encode())
        self.bytes.start()

    def tearDown(self):
        self.bytes.stop()
        self.reader.stop()
        self.env.stop()
        self.fixture.close()
        self.tmp.cleanup()

    def checkpoint(self, state):
        self.saved = copy.deepcopy(state)
        self.history.append(copy.deepcopy(state))
        encoded = json.dumps(state)
        self.assertNotIn(SECRET, encoded)
        self.assertNotIn(SIGNED, encoded)
        self.assertNotIn('signature=', encoded)

    def advance(self):
        return parsers.Parser().advance(self.source, self.cfg, self.saved, self.checkpoint)

    def test_wire_split_checkpoint_reuse_and_auth(self):
        result = self.advance()
        self.assertEqual(result['progress']['state'], 'uploaded')
        self.assertTrue(any(s['parts'][0].get('batch_id') == 'batch-0' and s['parts'][0]['status'] == 'submitted' for s in self.history))
        self.advance()
        self.assertEqual(self.saved['parts'][0]['status'], 'done')
        self.advance()
        self.advance()
        result = self.advance()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(result['assets']), 2)
        self.assertEqual([r['page'] for r in result['page_map']['ranges']], [1, 2])
        self.assertIn('Unique content 1.', result['markdown'])
        self.assertIn('Unique content 2.', result['markdown'])
        calls = self.fixture.calls
        self.assertEqual([c[0] for c in calls], ['POST', 'PUT', 'GET', 'GET', 'POST', 'PUT', 'GET', 'GET'])
        payload = json.loads(calls[0][3])
        self.assertEqual(set(payload), {'enable_formula', 'enable_table', 'language', 'model_version', 'files'})
        self.assertEqual(set(payload['files'][0]), {'name', 'data_id', 'is_ocr'})
        self.assertEqual(payload['files'][0]['name'], 'part-0001.pdf')
        for method, path, auth, body in calls:
            self.assertEqual(auth, 'Bearer ' + SECRET if path.startswith('/api/') else None)
            if method == 'PUT':
                self.assertTrue(body.startswith(b'%PDF part-'))
        before = len(calls)
        self.advance()
        self.assertEqual(len(calls), before)

    def test_interrupted_before_upload_resubmits_only_unfinished_part(self):
        self.advance(); self.advance()
        original = self.checkpoint
        def die(state):
            original(state)
            if state['parts'][1]['status'] == 'submitted':
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            parsers.Parser().advance(self.source, self.cfg, self.saved, die)
        self.assertEqual(len(self.fixture.batches), 2)
        result = self.advance()
        self.assertIn('upload_resubmitted', result['progress']['warnings'])
        self.assertEqual(self.saved['parts'][1]['status'], 'pending')
        self.advance(); self.advance()
        self.assertEqual(self.advance()['status'], 'completed')
        self.assertEqual(len(self.fixture.batches), 3)
        self.assertEqual(sum('/batch/batch-0' in c[1] for c in self.fixture.calls), 1)
        self.assertEqual(self.saved['parts'][0]['attempts'], 1)

    def test_interrupt_after_upload_before_checkpoint_does_not_resubmit(self):
        original = self.checkpoint
        def die(state):
            if state['parts'][0]['status'] == 'uploaded':
                raise KeyboardInterrupt()
            original(state)
        with self.assertRaises(KeyboardInterrupt):
            parsers.Parser().advance(self.source, self.cfg, {}, die)
        self.assertEqual(self.saved['parts'][0]['status'], 'submitted')
        self.advance()
        self.assertEqual(self.saved['parts'][0]['status'], 'done')
        self.assertEqual(len(self.fixture.batches), 1)

    def test_failed_upload_poll_download_resume_ids_and_redaction(self):
        self.fixture.fail_put = True
        with self.assertRaises(CliError) as raised:
            self.advance()
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertNotIn(SIGNED, str(raised.exception))
        self.assertEqual(self.saved['parts'][0]['status'], 'submitted')
        self.fixture.fail_put = False
        self.advance(); self.advance()
        self.fixture.poll_state = 'running'
        before = len(self.fixture.calls)
        self.assertEqual(self.advance()['status'], 'running')
        self.assertEqual(len(self.fixture.calls) - before, 1)
        self.fixture.poll_state = None
        self.fixture.fail_download = True
        with self.assertRaises(CliError):
            self.advance()
        self.assertEqual(self.saved['parts'][0]['status'], 'downloading')
        batch_count = len(self.fixture.batches)
        self.fixture.fail_download = False
        self.advance()
        self.assertEqual(len(self.fixture.batches), batch_count)
        self.assertEqual(self.saved['parts'][0]['status'], 'done')

    def test_api_errors_bad_replies_and_limits(self):
        for body in ({'code': 1, 'msg': SECRET}, [], {'code': 0, 'data': {'batch_id': 'bad?token=' + SECRET}}, {'code': 0, 'data': {'batch_id': 'safe', 'file_urls': []}}):
            self.saved = {}
            self.fixture.fail_post = body
            with self.subTest(body=body), self.assertRaises(CliError) as raised:
                self.advance()
            self.assertNotIn(SECRET, str(raised.exception))
        self.fixture.fail_post = None
        self.saved = {}
        self.cfg['parser_max_part_bytes'] = 1
        with self.assertRaises(CliError) as raised:
            self.advance()
        self.assertEqual(raised.exception.code, 'BUILD_INPUT_INVALID')

    def test_native_v1_reply_is_rejected_without_protocol_fallback(self):
        self.fixture.fail_post = {'status': 'success', 'task_id': 'native-v1-task'}
        with self.assertRaises(CliError) as raised:
            self.advance()
        self.assertEqual(raised.exception.code, 'BUILD_PARSER_FAILED')
        self.assertEqual([(call[0], call[1]) for call in self.fixture.calls],
                         [('POST', '/api/v4/file-urls/batch')])
        self.assertEqual(self.fixture.batches, {})

    def test_interrupt_before_download_and_failed_poll_never_resubmits(self):
        self.advance()
        original = self.checkpoint
        def die(state):
            original(state)
            if state['parts'][0]['status'] == 'downloading':
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            parsers.Parser().advance(self.source, self.cfg, self.saved, die)
        self.assertFalse(any('/download/' in c[1] for c in self.fixture.calls))
        self.assertEqual(self.saved['parts'][0]['batch_id'], 'batch-0')
        self.fixture.poll_state = 'failed'
        with self.assertRaises(CliError) as raised:
            self.advance()
        self.assertEqual(raised.exception.code, 'BUILD_PARSER_FAILED')
        self.assertNotIn(SECRET, str(raised.exception))
        self.fixture.poll_state = 'unknown'
        with self.assertRaises(CliError) as raised:
            self.advance()
        self.assertEqual(raised.exception.code, 'BUILD_PARSER_RESPONSE_INVALID')
        self.fixture.poll_state = None
        self.advance()
        self.assertEqual(self.saved['parts'][0]['status'], 'done')
        self.assertEqual(len(self.fixture.batches), 1)

    def test_network_timeouts_sizes_json_and_auth_redirect(self):
        self.fixture.delay = .2
        with self.assertRaises(CliError) as raised:
            net.request_bytes('GET', self.fixture.url + '/data?signature=' + SIGNED, timeout=.03)
        self.assertEqual(raised.exception.code, 'HTTP_TIMEOUT')
        self.assertNotIn(SIGNED, str(raised.exception))
        self.fixture.delay = 0
        with self.assertRaises(CliError) as raised:
            net.request_bytes('GET', self.fixture.url + '/data', max_bytes=1)
        self.assertEqual(raised.exception.code, 'HTTP_RESPONSE_TOO_LARGE')
        with self.assertRaises(CliError) as raised:
            net.request('GET', self.fixture.url + '/malformed?signature=' + SIGNED)
        self.assertNotIn(SIGNED, str(raised.exception))
        other = Fixture()
        try:
            self.fixture.redirect_target = other.url + '/data?signature=' + SIGNED
            with self.assertRaises(CliError):
                net.request_bytes('GET', self.fixture.url + '/redirect', token=SECRET)
            self.assertEqual(other.calls, [])
        finally:
            other.close()

    def test_failed_state_is_generic_and_page_provenance_end_to_end(self):
        import store
        with patch.object(store, 'LEARNOVA_HOME', self.root / 'user'):
            store._write_json(self.root / '.learnova/build-config.json', self.cfg)
            service = BuildService(self.root, parser=parsers.Parser())
            task = service.create(self.source)['task_id']
            self.fixture.fail_put = True
            with self.assertRaises(CliError):
                service.next(task)
            state = service.status(task)
            self.assertEqual(state['status'], 'failed')
            job_path = self.root / '.learnova/builds' / task
            self.assertNotIn(SECRET, (job_path / 'job.json').read_text())
            self.assertNotIn(SIGNED, (job_path / 'job.json').read_text())
            self.fixture.fail_put = False
            service.resume(task)
            for _ in range(30):
                state = service.next(task)
                if state['status'] == 'completed':
                    break
                if state['status'] == 'needs_input':
                    request = state['request']
                    result = {'splits': []} if request['kind'] == 'split_plan' else ({'title': 'Topic', 'summary': 'Reference', 'keywords': ['topic']} if request['kind'] == 'chunk_metadata' else {'name': 'sample', 'description': 'Reference'})
                    service.submit(task, dict(request, result=result))
            self.assertEqual(state['status'], 'completed')
            output = Path(state['result_path'])
            provenance = json.loads((output / '.learnova-build.json').read_text())
            job = jobs.read(job_path)
            mapping = job['normalized_page_map']
            self.assertEqual(mapping['status'], 'partial')
            self.assertEqual([r['page'] for r in mapping['ranges']], [1, 2])
            reconstructed = ''.join((output / ('references/' + c['id'] + '.md')).read_text() for c in job['chunks'])
            self.assertEqual(reconstructed, job['text'])
            for chunk in job['chunks']:
                ref = output / ('references/' + chunk['id'] + '.md')
                for image in skill_build.extract_images(ref.read_text()):
                    self.assertTrue(ref.parent.joinpath(image['destination']).is_file())
            self.assertEqual(mapping['ranges'][0]['end'], job['text'].index('\n\n# Part 3'))
            self.assertEqual(provenance, {'schema_version': 2, 'source': 'local-build', 'version': None})

FAKE_PDF = '''
class Page:
    def extract_text(self): return ''
class PdfReader:
    def __init__(self, source):
        from pathlib import Path
        self.pages = [Page() for _ in range(int(Path(source).read_bytes().split(b'=')[-1]))]
class PdfWriter:
    def __init__(self): self.pages = []
    def add_page(self, page): self.pages.append(page)
    def write(self, target): target.write(b'%PDF pages=' + str(len(self.pages)).encode())
'''


class InstalledTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.optional = self.root / 'optional'
        self.optional.mkdir()
        (self.optional / 'pypdf.py').write_text(FAKE_PDF)
        (self.optional / 'gradio_client.py').write_text("raise AssertionError('retired SDK must not be imported')\n")
        self.package = self.root / 'installed/learnova-cli'
        shutil.copytree(SCRIPTS.parent, self.package, ignore=shutil.ignore_patterns('__pycache__'))
        self.project = self.root / 'project'
        self.project.mkdir()
        self.source = self.project / 'source.pdf'
        self.source.write_bytes(b'%PDF pages=1')
        self.env = {**os.environ, 'HOME': str(self.root / 'home'), 'LEARNOVA_HOME': str(self.root / 'home/.learnova'),
                    'PYTHONPATH': str(self.optional), 'TEST_PARSER_KEY': SECRET, 'PYTHONDONTWRITEBYTECODE': '1'}
        self.cli = self.package / 'scripts/learnova.py'

    def tearDown(self):
        self.tmp.cleanup()

    def command(self, *args, error=False):
        result = subprocess.run([sys.executable, '-B', str(self.cli), '--json', 'build', *args, '--project-root', str(self.project)],
                                cwd=self.root, env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode == 0, not error, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        return json.loads(result.stdout)

    def setup_config(self, **values):
        (self.project / '.learnova').mkdir(exist_ok=True)
        (self.project / '.learnova/build-config.json').write_text(json.dumps(values))

    def finish(self, task):
        for _ in range(20):
            state = self.command('next', task)
            if state['status'] == 'completed':
                return state
            if state['status'] == 'needs_input':
                request = state['request']
                kind = request['kind']
                result = {'splits': []} if kind == 'split_plan' else ({'title': 'Topic', 'summary': 'Reference', 'keywords': ['topic']} if kind == 'chunk_metadata' else {'name': 'sample', 'description': 'Reference'})
                self.command('submit', task, '--data', json.dumps(dict(request, result=result)))
        self.fail('did not complete')

    def test_installed_guide_from_separate_caller_and_project(self):
        source = self.project / 'source.md'
        source.write_text('# Topic\n\nSynthetic content.\n')
        task = self.command('create', str(source), '--mode', 'auto')['task_id']
        state = self.command('run', task)
        self.assertEqual(state['request']['kind'], 'configuration')
        for key, value in (('llm_base_url', 'http://localhost:1234'), ('llm_model', 'synthetic'), ('llm_key_env', 'TEST_PARSER_KEY')):
            self.command('config', 'set', key, value)
        self.assertEqual(self.command('next', task)['request']['kind'], 'configuration')
        self.command('reconfigure', task, '--set', 'llm_base_url=http://localhost:1234', '--set', 'llm_model=synthetic', '--set', 'llm_key_env=TEST_PARSER_KEY')
        state = self.command('resume', task, '--mode', 'agent')
        state = self.command('next', task)
        self.assertEqual(state['request']['kind'], 'split_plan')
        self.assertEqual(self.command('read', task, '--kind', 'text', '--start', '0', '--end', '7', '--limit', '7')['text'], '# Topic')
        self.assertEqual(self.command('read', task, '--kind', 'boundaries', '--start', '0', '--limit', '100')['kind'], 'boundaries')
        self.command('pause', task)
        self.command('resume', task)
        result = self.finish(task)
        self.assertEqual(self.command('status', task)['status'], 'completed')
        self.assertTrue(Path(self.command('package', task)['package']).is_file())
        prompt = self.package / 'scripts/build/prompts/decision.txt'
        prompt.write_text(prompt.read_text() + '\nNew synthetic rule.\n')
        self.assertEqual(self.command('status', task)['status'], 'completed')
        self.assertEqual(self.command('next', task)['status'], 'completed')
        self.command('resume', task, '--from', 'chunking')
        self.assertEqual(self.command('status', task)['stage'], 'chunking')
        for _ in range(10):
            state = self.command('next', task, error=False) if _ < 3 else self.command('next', task, error=True)
            if 'error' in state:
                self.assertEqual(state['error']['code'], 'SKILL_CONFLICT')
                break
            req = state['request']
            answer = {'splits': []} if req['kind'] == 'split_plan' else ({'title': 'Topic', 'summary': 'Reference', 'keywords': ['topic']} if req['kind'] == 'chunk_metadata' else {'name': 'sample', 'description': 'Reference'})
            self.command('submit', task, '--data', json.dumps(dict(req, result=answer)))
        else:
            self.fail('expected output conflict')
        self.command('resume', task, '--force')
        self.assertEqual(self.command('next', task)['status'], 'completed')
        self.assertEqual(Path(result['result_path']).resolve(), (self.project / '.agents/skills/sample').resolve())

    def test_installed_final_warning_validation_and_recovery(self):
        source = self.project / 'source.md'
        text = '```\n' + 'x' * 400 + '\n```\n'
        source.write_text(text, newline='\n')
        self.setup_config(min_tokens=0, max_tokens=5)
        task = self.command('create', str(source))['task_id']
        result = self.finish(task)
        self.assertEqual(result['warning_count'], 1)
        self.assertEqual(self.command('status', task)['warning_count'], 1)
        chunk = self.command('read', task, '--kind', 'chunks', '--limit', '1')['items'][0]
        self.assertEqual(chunk['warnings'], ['oversized:no_cut'])
        self.assertEqual(chunk['tokens'], (len(text) + 3) // 4)
        output = Path(result['result_path'])
        index = output / 'SKILL.md'
        original = index.read_text()
        index.write_text(original[original.index('## References'):])
        self.assertEqual(self.command('package', task, error=True)['error']['code'], 'BUILD_INTEGRITY_FAILED')
        for verb in ('status', 'next', 'resume'):
            self.assertEqual(self.command(verb, task)['status'], 'completed')
        self.command('resume', task, '--from', 'finalize')
        self.assertEqual(self.command('next', task, error=True)['error']['code'], 'SKILL_CONFLICT')
        self.command('resume', task, '--force')
        self.assertEqual(self.command('next', task)['status'], 'completed')
        self.assertTrue(Path(self.command('package', task)['package']).is_file())
        shutil.rmtree(output)
        self.assertEqual(self.command('status', task)['status'], 'completed')
        self.assertEqual(self.command('package', task, error=True)['error']['code'], 'BUILD_INTEGRITY_FAILED')
        self.assertFalse(output.exists())
        self.command('resume', task, '--from', 'finalize')
        self.assertEqual(self.command('next', task)['status'], 'completed')
        self.assertEqual(self.command('read', task)['text'], text)

    def test_installed_remote_pdf_to_host_skill(self):
        fixture = Fixture()
        try:
            self.setup_config(remote_endpoint=fixture.url, remote_key_env='TEST_PARSER_KEY')
            task = self.command('create', str(self.source))['task_id']
            result = self.finish(task)
            output = Path(result['result_path'])
            self.assertTrue((output / 'SKILL.md').is_file())
            provenance = json.loads((output / '.learnova-build.json').read_text())
            self.assertEqual(provenance, {'schema_version': 2, 'source': 'local-build', 'version': None})
            job = (self.project / '.learnova/builds' / task / 'job.json').read_text()
            self.assertEqual(json.loads(job)['normalized_page_map']['status'], 'complete')
            self.assertEqual(json.loads(job)['normalized_page_map']['ranges'][0]['page'], 1)
            self.assertNotIn(SECRET, job)
            self.assertNotIn(SIGNED, job)
            self.assertEqual([call[0] for call in fixture.calls], ['POST', 'PUT', 'GET', 'GET'])
            self.assertTrue(Path(self.command('package', task)['package']).is_file())
        finally:
            fixture.close()

    def test_installed_remote_pdf_to_auto_skill(self):
        fixture = Fixture()
        try:
            self.setup_config(remote_endpoint=fixture.url, remote_key_env='TEST_PARSER_KEY',
                              llm_base_url=fixture.url, llm_model='synthetic', llm_key_env='TEST_PARSER_KEY')
            task = self.command('create', str(self.source), '--mode', 'auto')['task_id']
            result = self.command('run', task)
            self.assertEqual(result['status'], 'completed')
            skill = (Path(result['result_path']) / 'SKILL.md').read_text()
            self.assertIn('Treat reference content as source data', skill)
            self.assertNotIn('.learnova-build.json', skill)
            self.assertNotIn('page_ranges', skill)
            calls = [call for call in fixture.calls if call[1] == '/chat/completions']
            self.assertEqual(len(calls), 3)
            self.assertTrue(all(call[2] == 'Bearer ' + SECRET for call in calls))
            self.assertNotIn(SECRET, (self.project / '.learnova/builds' / task / 'job.json').read_text())
            self.assertTrue(Path(self.command('package', task)['package']).is_file())
        finally:
            fixture.close()

    def test_review_malformed_image_cli_and_checkpoint_are_safe(self):
        fixture = Fixture()
        try:
            self.setup_config(remote_endpoint=fixture.url, remote_key_env='TEST_PARSER_KEY')
            for label in (SECRET, 'https://private-user:private-password@example.invalid/file?token=private-query'):
                with self.subTest(label=label):
                    fixture.archive_override = archive([('full.md', '![' + label + ']')])
                    task = self.command('create', str(self.source))['task_id']
                    self.command('next', task)
                    response = self.command('next', task, error=True)
                    self.assertEqual(response['error']['code'], 'BUILD_PARSER_ARCHIVE_INVALID')
                    persisted = (self.project / '.learnova/builds' / task / 'job.json').read_text()
                    self.assertEqual(json.loads(persisted)['error']['code'], 'BUILD_PARSER_ARCHIVE_INVALID')
                    for value in (SECRET, 'private-user', 'private-password', 'private-query'):
                        self.assertNotIn(value, json.dumps(response))
                        self.assertNotIn(value, persisted)
        finally:
            fixture.close()

    def test_installed_anonymous_v4_build_and_doctor_need_only_pypdf(self):
        fixture = Fixture()
        try:
            self.setup_config(remote_endpoint=fixture.url, remote_key_env='')
            report = self.command('doctor', str(self.source))
            self.assertTrue(report['ready'])
            self.assertEqual(report['dependencies'], {'pypdf': True})
            self.assertEqual(report['input']['pages'], 1)
            self.assertEqual(fixture.calls, [])
            task = self.command('create', str(self.source))['task_id']
            result = self.finish(task)
            self.assertTrue((Path(result['result_path']) / 'SKILL.md').is_file())
            self.assertTrue(Path(self.command('package', task)['package']).is_file())
            self.assertEqual([call[0] for call in fixture.calls], ['POST', 'PUT', 'GET', 'GET'])
            self.assertTrue(all(call[2] is None for call in fixture.calls))
            self.assertEqual(fixture.calls[0][1], '/api/v4/file-urls/batch')
            self.assertEqual(fixture.calls[2][1], '/api/v4/extract-results/batch/batch-0')
            job = json.loads((self.project / '.learnova/builds' / task / 'job.json').read_text())
            self.assertNotIn('event_id', job['parser_state']['parts'][0])
            self.assertEqual(job['parser_state']['parts'][0]['attempts'], 1)
            self.assertEqual(job['normalized_page_map']['status'], 'complete')
        finally:
            fixture.close()

    def test_installed_v4_query_urls_preserve_source(self):
        link = '[query](https://example.invalid/?q=synthetic&x=%2B)'
        text = '# Source\n\nContent. ' + link + '\n'
        fixture = Fixture()
        try:
            fixture.archive_override = archive([('markdown.md', text)])
            self.setup_config(remote_endpoint=fixture.url, remote_key_env='TEST_PARSER_KEY')
            task = self.command('create', str(self.source))['task_id']
            result = self.finish(task)
            output = Path(result['result_path'])
            job = json.loads((self.project / '.learnova/builds' / task / 'job.json').read_text())
            restored = ''.join((output / ('references/' + c['id'] + '.md')).read_text() for c in job['chunks'])
            self.assertEqual(restored, text)
            self.assertEqual(job['parser_state']['parts'][0]['result']['markdown'], text)
            self.assertEqual(self.command('read', task)['text'], text)
            self.assertEqual(self.command('status', task)['status'], 'completed')
        finally:
            fixture.close()

    def test_installed_v4_download_failure_resumes_without_reupload(self):
        fixture = Fixture()
        try:
            self.setup_config(remote_endpoint=fixture.url, remote_key_env='TEST_PARSER_KEY')
            task = self.command('create', str(self.source))['task_id']
            self.command('next', task)
            fixture.fail_download = True
            error = self.command('next', task, error=True)['error']
            self.assertEqual(error['code'], 'HTTP_ERROR')
            path = self.project / '.learnova/builds' / task
            failed = jobs.read(path)
            part = failed['parser_state']['parts'][0]
            self.assertEqual(failed['status'], 'failed')
            self.assertEqual(part['status'], 'downloading')
            self.assertEqual(part['batch_id'], 'batch-0')
            self.assertNotIn(SECRET, (path / 'job.json').read_text())
            self.assertNotIn(SIGNED, (path / 'job.json').read_text())
            fixture.fail_download = False
            self.command('resume', task)
            self.assertEqual(jobs.read(path)['validity']['parsing'], failed['validity']['parsing'])
            self.assertEqual(self.finish(task)['status'], 'completed')
            self.assertEqual(len(fixture.batches), 1)
            self.assertEqual(sum(call[0] == 'PUT' for call in fixture.calls), 1)
            resumed = jobs.read(path)
            self.assertEqual(resumed['parser_state']['parts'][0]['data_id'], part['data_id'])
            self.assertEqual(resumed['parser_state']['parts'][0]['attempts'], 1)
        finally:
            fixture.close()

    def legacy_local_task(self, task, endpoint):
        path = self.project / '.learnova/builds' / task
        state = jobs.read(path)
        state['config'].update(parser='local', local_endpoint=endpoint)
        state['parser_override'] = 'local'
        part = {'start_page': 0, 'end_page': 1, 'status': 'submitted', 'data_id': 'legacy',
                'attempts': 1, 'event_id': 'local-event-1'}
        if state['status'] == 'completed':
            part.update(status='done', result=state['parser_state']['parts'][0]['result'])
        state['parser_state'] = {'version': 1, 'page_count': 1, 'warnings': [], 'parts': [part]}
        jobs.write(path, state)
        return path

    def test_installed_config_keeps_retired_fields_in_output(self):
        retired = {'local_endpoint': 'http://localhost:1234', 'local_api_name': '/legacy',
                   'local_language': 'legacy', 'local_backend': 'legacy', 'local_result_timeout': 90}
        self.setup_config(**retired)
        output = self.command('config', 'get')
        self.assertEqual({key: output[key] for key in retired}, retired)
        error = self.command('config', 'set', 'local_endpoint', 'http://localhost:4321', error=True)['error']
        self.assertEqual(error['code'], 'BUILD_CONFIG_INVALID')
        self.assertEqual(self.command('config', 'get'), output)

    def test_legacy_local_task_requires_explicit_v4_reconfigure(self):
        fixture = Fixture()
        try:
            self.setup_config(remote_endpoint=fixture.url, remote_key_env='')
            task = self.command('create', str(self.source))['task_id']
            path = self.legacy_local_task(task, fixture.url)
            error = self.command('next', task, error=True)['error']
            self.assertEqual(error['code'], 'BUILD_CONFIG_INVALID')
            self.assertIn('reconfigure', error['message'])
            self.assertIn('remote_endpoint', error['message'])
            self.assertEqual(fixture.calls, [])
            self.assertEqual(jobs.read(path)['parser_state']['parts'][0]['event_id'], 'local-event-1')
            self.command('reconfigure', task, '--set', 'parser=remote', '--set', 'remote_endpoint=' + fixture.url)
            self.assertEqual(jobs.read(path)['parser_state'], {})
            self.assertEqual(self.finish(task)['status'], 'completed')
            self.assertEqual([call[0] for call in fixture.calls], ['POST', 'PUT', 'GET', 'GET'])
        finally:
            fixture.close()

    def test_legacy_post_parsing_tasks_reuse_parsed_results(self):
        fixture = Fixture()
        try:
            self.setup_config(remote_endpoint=fixture.url, remote_key_env='')
            task = self.command('create', str(self.source))['task_id']
            result = self.finish(task)
            output = Path(result['result_path'])
            path = self.project / '.learnova/builds' / task
            baseline = jobs.read(path)
            baseline['result_path'] = None
            calls = list(fixture.calls)
            for stage in ('chunking', 'indexing', 'finalize'):
                with self.subTest(stage=stage):
                    shutil.rmtree(output)
                    legacy = copy.deepcopy(baseline)
                    legacy.update(stage=stage, status='paused', request=None, parser_override='local')
                    legacy['config'].update(parser='local', local_endpoint=fixture.url)
                    for part in legacy['parser_state']['parts']:
                        part.pop('batch_id', None)
                        part['event_id'] = 'legacy-event'
                    jobs.write(path, legacy)
                    self.assertEqual(self.command('resume', task)['status'], 'pending')
                    self.assertEqual(self.command('work', task)['status'], 'completed')
                    self.assertTrue((output / 'SKILL.md').is_file())
                    self.assertEqual(jobs.read(path)['text'], baseline['text'])
                    self.assertEqual(jobs.read(path)['config']['parser'], 'local')
                    self.assertEqual(fixture.calls, calls)
            shutil.rmtree(output)
            active = copy.deepcopy(baseline)
            active.update(stage='indexing', status='pending', request=None)
            active['metadata'].pop('description', None)
            jobs.write(path, active)
            request = self.command('next', task)['request']
            self.assertEqual(request['kind'], 'skill_metadata')
            legacy = jobs.read(path)
            legacy['config']['parser'] = 'local'
            legacy['parser_override'] = 'local'
            jobs.write(path, legacy)
            envelope = dict(request, result={'name': 'sample', 'description': 'Reference'})
            self.assertEqual(self.command('submit', task, '--data', json.dumps(envelope))['status'], 'pending')
            self.assertEqual(self.command('work', task)['status'], 'completed')
            self.assertTrue((output / 'SKILL.md').is_file())
            self.assertEqual(fixture.calls, calls)
        finally:
            fixture.close()

    def test_completed_legacy_local_history_remains_readable(self):
        fixture = Fixture()
        try:
            self.setup_config(remote_endpoint=fixture.url, remote_key_env='')
            task = self.command('create', str(self.source))['task_id']
            completed = self.finish(task)
            text = self.command('read', task)['text']
            path = self.legacy_local_task(task, fixture.url)
            original = (path / 'job.json').read_bytes()
            self.source.unlink()
            before = len(fixture.calls)
            for verb in ('status', 'next', 'resume'):
                state = self.command(verb, task)
                self.assertEqual(state['status'], 'completed')
                self.assertEqual(state['result_path'], completed['result_path'])
            self.assertEqual(self.command('read', task)['text'], text)
            self.assertTrue(Path(self.command('package', task)['package']).is_file())
            self.assertEqual(len(fixture.calls), before)
            self.assertEqual((path / 'job.json').read_bytes(), original)
        finally:
            fixture.close()

    def test_missing_optional_dependency_does_not_break_old_commands(self):
        # A fake missing package exercises import failure even on hosts with pypdf installed.
        (self.optional / 'pypdf.py').write_text("raise ImportError('synthetic missing')\n")
        self.setup_config(remote_endpoint='http://127.0.0.1:9999', remote_key_env='TEST_PARSER_KEY')
        task = self.command('create', str(self.source))['task_id']
        error = self.command('next', task, error=True)['error']
        self.assertEqual(error['code'], 'BUILD_DEPENDENCY_MISSING')
        self.assertIn('uv pip install', error['message'])
        # Invalid build config and missing optional imports do not affect the existing registry.
        (self.project / '.learnova/build-config.json').write_text('{invalid')
        result = subprocess.run([sys.executable, '-B', str(self.cli), '--json', 'list', '--project'],
                                cwd=self.project, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsInstance(json.loads(result.stdout), (dict, list))


class ConfigTests(unittest.TestCase):
    def test_review_numeric_response_url_is_typed(self):
        for value in (123, None, [], {}):
            with self.subTest(value=value), self.assertRaises(CliError) as raised:
                parsers._url(value)
            self.assertEqual(raised.exception.code, 'BUILD_PARSER_RESPONSE_INVALID')

    def test_review_response_header_disconnect_is_typed_and_safe(self):
        for error in (http.client.RemoteDisconnected(SECRET), ConnectionResetError(SECRET)):
            with self.subTest(error=type(error).__name__), patch.object(net._OPENER, 'open', side_effect=error), self.assertRaises(CliError) as raised:
                net.request_bytes('GET', 'https://example.invalid/file?token=private-query')
            self.assertEqual(raised.exception.code, 'HTTP_ERROR')
            visible = ''.join(traceback.format_exception(raised.exception))
            self.assertNotIn(SECRET, visible)
            self.assertNotIn('private-query', str(raised.exception))

    def test_dependency_groups_and_reconfigure_seals(self):
        import store
        self.assertEqual(config.PARSING_KEYS | config.CHUNKING_KEYS | {'read_limit'}, set(config.DEFAULTS))
        with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'LEARNOVA_HOME', Path(tmp) / 'home'):
            root = Path(tmp)
            source = root / 'source.md'
            source.write_text('# Source\n\nText.\n')
            service = BuildService(root)
            task = service.create(source)['task_id']
            service.next(task)
            path = root / '.learnova/builds' / task
            baseline = jobs.read(path)
            for key in config.PARSING_KEYS:
                changed = copy.deepcopy(baseline)
                value = changed['config'][key]
                changed['config'][key] = not value if isinstance(value, bool) else (value + 1 if isinstance(value, int) else value + 'x')
                self.assertNotEqual(jobs._seals(baseline)['parsing'], jobs._seals(changed)['parsing'], key)
            result = service.reconfigure(task, {'parser_pages_per_part': '100'})
            self.assertEqual(result['stage'], 'parsing')
            self.assertIsNone(jobs.read(path)['text'])
            service.next(task)
            result = service.reconfigure(task, {'parser_formula': 'false'})
            self.assertEqual(result['stage'], 'parsing')
            self.assertIs(config.parse_value('parser_formula', 'false'), False)
            with self.assertRaises(CliError):
                config.parse_value('parser_formula', 'no')
            for key, value in [('parser_language', '?bad'), ('parser_formula', 1), ('parser_member_bytes', 0)]:
                with self.subTest(key=key), self.assertRaises(CliError):
                    config.validate(dict(config.DEFAULTS, **{key: value}))

    def test_pdf_split_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.pdf'
            source.write_bytes(b'%PDF pages=3')
            module = types.ModuleType('pypdf')
            exec(FAKE_PDF, module.__dict__)
            with patch.dict(sys.modules, {'pypdf': module}):
                data = parsers._pdf_bytes(source, {'start_page': 1, 'end_page': 3}, 3)
                self.assertEqual(data, b'%PDF pages=2')
                self.assertEqual(parsers._pdf_bytes(source, {'start_page': 0, 'end_page': 3}, 3), source.read_bytes())

    def test_v4_parsing_snapshot_defaults_remain_compatible(self):
        legacy = {'parser': 'remote', 'remote_endpoint': '', 'remote_key_env': '', 'local_endpoint': '',
                  'parser_pages_per_part': 200, 'parser_max_part_bytes': 209715200,
                  'parser_language': 'ch', 'parser_formula': True, 'parser_table': True, 'parser_ocr': False,
                  'remote_model_version': 'vlm', 'parser_request_timeout': 30, 'parser_upload_timeout': 120,
                  'parser_download_timeout': 120, 'parser_archive_bytes': 104857600,
                  'parser_expanded_bytes': 268435456, 'parser_member_bytes': 67108864,
                  'local_api_name': '/convert_to_markdown_stream',
                  'local_language': 'ch (Chinese, English, Chinese Traditional)',
                  'local_backend': 'pipeline', 'local_result_timeout': 300}
        self.assertEqual(config.PARSING_KEYS, set(legacy))
        self.assertEqual({key: config.DEFAULTS[key] for key in config.PARSING_KEYS}, legacy)

    def test_optional_auth_and_configured_missing_key(self):
        cfg = dict(config.DEFAULTS, remote_endpoint='https://example.invalid')
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(config.missing(cfg, 'remote'), [])
            cfg['remote_key_env'] = 'SYNTHETIC_MISSING_KEY'
            self.assertEqual(config.missing(cfg, 'remote'), ['remote_key_env'])
            os.environ['SYNTHETIC_MISSING_KEY'] = SECRET
            self.assertEqual(config.missing(cfg, 'remote'), [])

    def test_retired_local_settings_are_read_only(self):
        import store
        retired = {'local_endpoint': 'http://localhost:1234', 'local_api_name': '/legacy',
                   'local_language': 'legacy', 'local_backend': 'legacy', 'local_result_timeout': 90}
        with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'LEARNOVA_HOME', Path(tmp) / 'home'):
            root = Path(tmp)
            store._write_json(root / '.learnova/build-config.json', retired)
            loaded = config.load(root)
            self.assertEqual({key: loaded[key] for key in retired}, retired)
            original = (root / '.learnova/build-config.json').read_bytes()
            for key, value in retired.items():
                with self.subTest(key=key), self.assertRaises(CliError) as raised:
                    config.set_value(root, key, value)
                self.assertEqual(raised.exception.code, 'BUILD_CONFIG_INVALID')
                self.assertEqual((root / '.learnova/build-config.json').read_bytes(), original)

    def test_local_parser_is_rejected_before_pdf_or_network_access(self):
        cfg = dict(config.DEFAULTS, parser='local', local_endpoint='http://localhost:1234')
        actions = [lambda: config.validate(cfg), lambda: config.missing(cfg, 'local'),
                   lambda: parsers.Parser().advance(Path('missing.pdf'), cfg, {}, lambda state: None)]
        with patch.object(parsers, '_pdf', side_effect=AssertionError('must not read PDF')), \
                patch.object(net, 'request', side_effect=AssertionError('must not contact service')):
            for action in actions:
                with self.subTest(action=actions.index(action)), self.assertRaises(CliError) as raised:
                    action()
                self.assertEqual(raised.exception.code, 'BUILD_CONFIG_INVALID')
                self.assertIn('remote_endpoint', str(raised.exception))

    def test_pdf_doctor_reports_only_the_missing_pypdf_dependency(self):
        import store
        from build import preflight
        with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'LEARNOVA_HOME', Path(tmp) / 'home'):
            root = Path(tmp)
            source = root / 'source.pdf'
            source.write_bytes(b'%PDF synthetic')
            config.set_value(root, 'remote_endpoint', 'https://example.invalid')
            with patch.object(preflight.importlib.util, 'find_spec', return_value=None) as find_spec:
                report = preflight.inspect(root, source)
            find_spec.assert_called_once_with('pypdf')
            self.assertEqual(report['dependencies'], {'pypdf': False})
            self.assertEqual(report['issues'][0]['packages'], ['pypdf'])
            self.assertFalse(report['ready'])


if __name__ == '__main__':
    unittest.main()
