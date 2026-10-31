"""Final cross-stage regressions, using synthetic sources and no live services."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/learnova-cli/scripts'))
from build import jobs, parsers
from build.pipeline import BuildService
from core import chunking, document, skill_build
from core.errors import CliError
import store


class Decisions:
    def __init__(self, reason='atomic'):
        self.reason = reason

    def decide(self, request, config):
        if request['kind'] == 'split_plan':
            if self.reason == 'max_depth':
                boundaries = request['allowed_boundaries']
                middle = len(boundaries) // 2
                return {'splits': boundaries[middle:middle + 1]}
            return {'splits': [], 'atomic': self.reason == 'atomic'}
        if request['kind'] == 'chunk_metadata':
            return {'title': 'Topic', 'summary': 'Reference', 'keywords': ['topic']}
        return {'name': 'sample', 'description': 'Synthetic reference'}


class FinalBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = patch.object(store, 'LEARNOVA_HOME', self.root / 'home')
        self.home.start()
        self.addCleanup(self.home.stop)
        self.env = patch.dict(os.environ, {'SYNTHETIC_FINAL_KEY': 'synthetic-final-secret'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def create(self, text='```\n' + 'x' * 400 + '\n```\n', reason='atomic', pdf=False, force=False):
        source = self.root / ('source.pdf' if pdf else 'source.md')
        source.write_bytes(b'%PDF synthetic' if pdf else text.encode())
        service = BuildService(self.root, parser=parsers.Parser(), decision=Decisions(reason))
        task = service.create(source, mode='auto', force=force)['task_id']
        service.reconfigure(task, {'min_tokens': 0, 'max_tokens': 5, 'max_depth': 1,
                                  'remote_endpoint': 'http://localhost:1234', 'remote_key_env': 'SYNTHETIC_FINAL_KEY',
                                  'llm_base_url': 'http://localhost:1234', 'llm_model': 'synthetic', 'llm_key_env': 'SYNTHETIC_FINAL_KEY'})
        return service, task

    def saved(self, task):
        return jobs.read(jobs.task_dir(self.root, task))

    def test_production_pdf_query_content_and_transport_separation(self):
        text = '[search](https://example.invalid/search?q=synthetic&x=%2B#part)\n'
        text += '![https://example.invalid/?caption=yes][figure]\n\n[figure]: image.png\n'
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as archive:
            archive.writestr('full.md', text)
            archive.writestr('image.png', b'\x89PNG\r\n\x1a\nsynthetic')
        service, task = self.create(pdf=True)
        replies = [{'code': 0, 'data': {'batch_id': 'batch-safe', 'file_urls': ['http://localhost/upload?sig=synthetic-upload']}},
                   {'code': 0, 'data': {'extract_result': [{'state': 'done', 'full_zip_url': 'http://localhost/result?sig=synthetic-download'}]}}]
        with patch.object(parsers, '_pdf', return_value=types.SimpleNamespace(pages=[object()])), patch.object(parsers.net, 'request', side_effect=replies), patch.object(parsers.net, 'request_bytes', side_effect=[b'', content.getvalue()]):
            self.assertEqual(service.next(task)['status'], 'running')
            self.assertEqual(service.next(task)['status'], 'running')
            result = service.run(task)
        self.assertEqual(result['status'], 'completed')
        saved = self.saved(task)
        self.assertIn('[search](https://example.invalid/search?q=synthetic&x=%2B#part)', saved['text'])
        self.assertIn('https://example.invalid/?caption=yes', saved['parser_state']['parts'][0]['result']['normalizations'][0]['original'])
        output = Path(result['result_path'])
        provenance = json.loads((output / '.learnova-build.json').read_text())
        self.assertEqual(''.join((output / ('references/' + c['id'] + '.md')).read_text() for c in saved['chunks']), saved['text'])
        persisted = json.dumps(saved) + ''.join(p.read_bytes().decode('utf-8', 'replace') for p in output.rglob('*') if p.is_file())
        for forbidden in ('synthetic-upload', 'synthetic-download', 'synthetic-final-secret'):
            self.assertNotIn(forbidden, persisted)
        # Completed-part replay preserves source URLs without reacquiring transport URLs.
        local = copy.deepcopy(saved['parser_state'])
        parsers.validate_checkpoint(local)
        self.assertEqual(parsers.Parser().advance(Path(saved['input']), saved['config'], local, parsers.validate_checkpoint)['markdown'], local['parts'][0]['result']['markdown'])

    def test_transport_urls_and_actual_secret_are_rejected_safely(self):
        for value in (None, 1, 'https://[broken/x', 'https://localhost:bad/x', 'https://user:pass@localhost/x',
                      'https://local\nhost/x', 'https://localhost/\tx', '\x00https://localhost/x',
                      'https://localhost/a b', 'https://localhost/\\x', 'https://localhost/\x7f'):
            with self.subTest(value=value), self.assertRaises(CliError) as caught:
                parsers._url(value)
            self.assertEqual(caught.exception.code, 'BUILD_PARSER_RESPONSE_INVALID')
            self.assertNotIn(str(value), str(caught.exception))
        for state in ({'upload_url': 'https://localhost/x?sig=private'},
                      {'parts': [{'batch_id': 'https://localhost/x?sig=private'}]},
                      {'parts': [{'result': {'transport': 'https://localhost/x?sig=private'}}]}):
            with self.assertRaises(CliError) as caught:
                parsers.validate_checkpoint(state)
            self.assertEqual(caught.exception.code, 'BUILD_INTEGRITY_FAILED')
            self.assertNotIn('private', str(caught.exception))
        for in_id in (True, False):
            with self.subTest(in_id=in_id):
                service, task = self.create(pdf=True)
                content = io.BytesIO()
                with zipfile.ZipFile(content, 'w') as archive:
                    archive.writestr('full.md', 'synthetic-final-secret')
                replies = [{'code': 0, 'data': {'batch_id': 'synthetic-final-secret' if in_id else 'safe-batch', 'file_urls': ['https://localhost/upload?sig=x']}},
                           {'code': 0, 'data': {'extract_result': [{'state': 'done', 'full_zip_url': 'https://localhost/result?sig=x'}]}}]
                with patch.object(parsers, '_pdf', return_value=types.SimpleNamespace(pages=[object()])), patch.object(parsers.net, 'request', side_effect=replies), patch.object(parsers.net, 'request_bytes', side_effect=[b'', content.getvalue()]):
                    with self.assertRaises(CliError) as caught:
                        service.next(task)
                        service.next(task)
                self.assertEqual(caught.exception.code, 'BUILD_PARSER_RESPONSE_INVALID')
                self.assertNotIn('synthetic-final-secret', json.dumps(self.saved(task)))

    def test_checkpoint_rejects_known_credentials_in_content_and_control(self):
        credential = 'synthetic-final-secret'
        for state in ({'batch_id': credential}, {'parts': [{'event_id': credential}]},
                      {'parts': [{'result': {'markdown': credential}}]},
                      {'parts': [{'result': {'normalizations': [{'original': credential}]}}]}):
            with self.subTest(state=state):
                service, task = self.create(pdf=True)
                class Echo:
                    def advance(self, source, config, previous, checkpoint):
                        checkpoint(state)
                        raise AssertionError('credential checkpoint was accepted')
                service.parser = Echo()
                with self.assertRaises(CliError) as caught:
                    service.next(task)
                self.assertEqual(caught.exception.code, 'BUILD_INTEGRITY_FAILED')
                self.assertNotIn(credential, str(caught.exception))
                self.assertNotIn(credential, json.dumps(self.saved(task)))

    def test_completed_history_and_explicit_same_task_rebuild(self):
        for damage in ('removed', 'missing-reference', 'changed-index'):
            with self.subTest(damage=damage):
                service, task = self.create(force=False)
                result = service.run(task)
                output = Path(result['result_path'])
                original = self.saved(task)
                if damage == 'removed':
                    shutil.rmtree(output)
                elif damage == 'missing-reference':
                    next((output / 'references').glob('*.md')).unlink()
                else:
                    (output / 'SKILL.md').write_text('changed')
                for method in (service.status, service.next, service.resume):
                    self.assertEqual(method(task)['status'], 'completed')
                self.assertEqual(self.saved(task), original)
                self.assertEqual(output.exists(), damage != 'removed')
                service.resume(task, from_stage='finalize')
                for key in ('text', 'parser_state', 'chunks', 'metadata'):
                    self.assertEqual(self.saved(task)[key], original[key])
                if damage != 'removed':
                    with self.assertRaises(CliError) as caught:
                        service.next(task)
                    self.assertEqual(caught.exception.code, 'SKILL_CONFLICT')
                    service.resume(task, force=True)
                self.assertEqual(service.next(task)['status'], 'completed')
                self.assertEqual(service.status(task)['status'], 'completed')
                shutil.rmtree(output)

    def test_completed_history_ignores_external_inputs_assets_and_rules(self):
        image = self.root / 'figure.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic')
        service, task = self.create(text='![figure](figure.png)\n')
        result = service.run(task)
        original = self.saved(task)
        image.unlink()
        (self.root / 'source.md').rename(self.root / 'moved.md')
        with patch.object(service, '_rules_hash', side_effect=AssertionError('completed history must not read rules')), \
             patch('build.pipeline._input', side_effect=AssertionError('completed history must not read source')):
            for method in (service.status, service.next, service.resume):
                self.assertEqual(method(task), result)
            self.assertTrue(Path(service.package(task)['package']).is_file())
        self.assertEqual(self.saved(task), original)
        with self.assertRaises(CliError) as caught:
            service.resume(task, from_stage='finalize')
        self.assertEqual(caught.exception.code, 'BUILD_INPUT_INVALID')
        self.assertEqual(service.status(task)['status'], 'completed')

    def test_completed_controls_do_not_rebuild_and_reconfiguration_is_explicit(self):
        service, task = self.create()
        service.run(task)
        original = self.saved(task)
        with self.assertRaises(CliError) as caught:
            service.reconfigure(task, {'max_tokens': 100})
        self.assertIn('resume --from chunking', str(caught.exception))
        self.assertEqual(self.saved(task), original)
        with patch.object(service, '_rules_hash', side_effect=AssertionError('no implicit rebuild')):
            self.assertEqual(service.resume(task, force=True, mode='agent')['status'], 'completed')
        self.assertTrue(self.saved(task)['force'])
        self.assertEqual(self.saved(task)['mode'], 'agent')
        service.resume(task, from_stage='finalize')
        self.assertEqual(service.next(task)['status'], 'completed')
        service.resume(task, from_stage='chunking')
        service.reconfigure(task, {'max_tokens': 100})
        self.assertEqual(service.status(task)['status'], 'pending')

    def test_package_current_edits_additions_and_exclusions_without_marker(self):
        service, task = self.create(text='Original reference.\n')
        output = Path(service.run(task)['result_path'])
        original = self.saved(task)
        ref = next((output / 'references').glob('*.md'))
        ref.write_text('Edited reference.\n')
        (output / 'SKILL.md').write_text('---\nname: edited-skill\ndescription: Current content\n---\n[Guide](README.md)\n')
        (output / 'README.md').write_text('[Resource](resources/new.txt)\n![Figure](resources/figure.png)\n[Contact](mailto:example@example.invalid)\n')
        (output / 'resources').mkdir()
        (output / 'resources/new.txt').write_text('Current resource')
        (output / 'resources/figure.png').write_bytes(b'image')
        omitted = ['.learnova-install.json', '.env', '.env.local', '.learnova-tmp', '.npmrc',
                   'resources/.env', 'credentials.json', 'private.key', '.git/config', '.learnova/builds/job.json', '.ssh/id_rsa']
        for name in omitted:
            path = output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic credential or local state')
        (output / '.learnova-build.json').unlink()
        (self.root / 'source.md').unlink()
        with zipfile.ZipFile(service.package(task)['package']) as archive:
            self.assertEqual(set(archive.namelist()), {'SKILL.md', 'README.md', ref.relative_to(output).as_posix(),
                                                       'resources/new.txt', 'resources/figure.png'})
            self.assertEqual(archive.read(ref.relative_to(output).as_posix()), b'Edited reference.\n')
            self.assertEqual(archive.read('resources/new.txt'), b'Current resource')
        self.assertEqual(self.saved(task), original)
        self.assertEqual(service.status(task)['status'], 'completed')

    def test_package_missing_output_does_not_change_or_recreate_history(self):
        service, task = self.create()
        output = Path(service.run(task)['result_path'])
        original = self.saved(task)
        shutil.rmtree(output)
        with self.assertRaises(CliError) as caught:
            service.package(task)
        self.assertIn('--from finalize', str(caught.exception))
        self.assertEqual(self.saved(task), original)
        self.assertEqual(service.status(task)['status'], 'completed')
        self.assertFalse(output.exists())

    def test_package_rejects_broken_active_links_and_unsafe_files(self):
        service, task = self.create(text='Reference.\n')
        output = Path(service.run(task)['result_path'])
        index = output / 'SKILL.md'
        original = index.read_text()
        for body in ('[missing](missing.md)', '[missing][ref]\n\n[ref]: missing.md',
                     '![missing](images/missing.png)', '[unsafe](../../outside.md)',
                     '![unsafe](javascript:alert(1))', '![unsafe](javascript:#figure)', '![unsafe](#figure)', '[credential](.env)', '<a href="missing.md">missing</a>'):
            with self.subTest(body=body):
                index.write_text(original + '\n' + body)
                (output / '.env').write_text('synthetic secret')
                with self.assertRaises(CliError) as caught:
                    service.package(task)
                self.assertEqual(caught.exception.code, 'BUILD_INTEGRITY_FAILED')
                self.assertEqual(service.status(task)['status'], 'completed')
        index.write_text(original)
        unsafe = output / 'unsafe'
        unsafe.symlink_to(self.root / 'source.md')
        with self.assertRaises(CliError):
            service.package(task)
        unsafe.unlink()
        unsafe.symlink_to(output / 'references', target_is_directory=True)
        with self.assertRaises(CliError):
            service.package(task)
        unsafe.unlink()
        if hasattr(os, 'mkfifo'):
            os.mkfifo(unsafe)
            with self.assertRaises(CliError):
                service.package(task)
            unsafe.unlink()
        (output / 'a:b.txt').write_text('unsafe name')
        with self.assertRaises(CliError):
            service.package(task)

    def test_package_rejects_actual_invalid_frontmatter(self):
        service, task = self.create()
        output = Path(service.run(task)['result_path'])
        index = output / 'SKILL.md'
        original = index.read_text()
        for invalid in (original[original.index('## References'):],
                        original.replace('name: "sample"', 'name: "../invalid"'),
                        original.replace('description: "Synthetic reference"', 'description: ""')):
            index.write_text(invalid)
            with self.assertRaises(CliError) as caught:
                service.package(task)
            self.assertEqual(caught.exception.code, 'BUILD_INTEGRITY_FAILED')
            self.assertFalse((jobs.task_dir(self.root, task) / 'skill.zip').exists())
        index.write_text(original)
        self.assertTrue(Path(service.package(task)['package']).is_file())

    def test_auto_oversize_reasons_visible_after_reload_and_in_artifact(self):
        for reason in ('atomic', 'no_cut', 'max_depth'):
            with self.subTest(reason=reason):
                text = ('x' * 80 + '\n\n' + 'y' * 80 + '\n\n' + 'z' * 80 + '\n\n' + 'w' * 80) if reason == 'max_depth' else '```\n' + 'x' * 400 + '\n```\n'
                service, task = self.create(text, reason)
                result = service.run(task)
                self.assertEqual(result['status'], 'completed')
                restored = BuildService(self.root)
                chunks = []
                start = 0
                while True:
                    page = restored.read(task, start=start, limit=1, kind='chunks')
                    chunks.extend(page['items'])
                    if page['next_start'] is None:
                        break
                    start = page['next_start']
                self.assertEqual(result['warning_count'], len(chunks))
                self.assertEqual(restored.status(task)['warning_count'], len(chunks))
                output = Path(result['result_path'])
                provenance = json.loads((output / '.learnova-build.json').read_text())
                self.assertEqual(provenance, {'schema_version': 2, 'source': 'local-build', 'version': None})
                for chunk in chunks:
                    self.assertEqual(chunk['warnings'], ['oversized:' + reason])
                    self.assertEqual(chunk['tokens'], (chunk['end'] - chunk['start'] + 3) // 4)
                    self.assertIn('oversized:' + reason, (output / 'SKILL.md').read_text())
                self.assertEqual(''.join((output / ('references/' + c['id'] + '.md')).read_text() for c in chunks), text)
                shutil.rmtree(output)

    def test_completed_render_change_waits_for_explicit_rebuild(self):
        service, task = self.create()
        service.run(task)
        original = self.saved(task)
        with patch.object(skill_build, 'RENDER_VERSION', skill_build.RENDER_VERSION + 1):
            status = service.status(task)
            self.assertEqual((status['stage'], status['status']), ('finalize', 'completed'))
            self.assertEqual(service.next(task), status)
            service.resume(task, from_stage='finalize')
            current = self.saved(task)
            for key in ('text', 'chunks', 'metadata', 'parser_state'):
                self.assertEqual(current[key], original[key])
            with self.assertRaises(CliError) as caught:
                service.next(task)
            self.assertEqual(caught.exception.code, 'SKILL_CONFLICT')
            service.resume(task, force=True)
            self.assertEqual(service.next(task)['status'], 'completed')


class FinalPureTests(unittest.TestCase):
    def candidate(self, description='Synthetic'):
        text = 'source'
        chunks = chunking.split(text, document.scan(text), [])
        meta = {'name': 'sample', 'description': description, 'chunks': {chunks[0]['id']: {'title': 'Topic', 'summary': 'Reference', 'keywords': ['topic']}}}
        return text, chunks, meta, skill_build.render(text, chunks, meta)

    def test_actual_skill_metadata_validation_and_decoded_length(self):
        for description in ('Unicode\u2028separator\u0085next', 'Unicode 中文 "quotes" \\ and\ttabs', '"\\\t中' * 256):
            text, chunks, meta, files = self.candidate(description)
            self.assertTrue(skill_build.validate_package(files))
        text, _, _, original = self.candidate()
        body = original['SKILL.md'][original['SKILL.md'].index('## References'):]
        for header in ('', '---\nname: sample\n', '---\nname: sample\ndescription: # missing\n---\n',
                       '---\nname: sample\ndescription: 1e6\n---\n',
                       '---broken\nname: sample\ndescription: text\n---\n',
                       '---\nname: sample\ndescription: "unterminated\n---\n',
                       '---\nname: sample\nname: duplicate\ndescription: text\n---\n'):
            with self.subTest(header=header), self.assertRaises(CliError) as caught:
                skill_build.validate_package(dict(original, **{'SKILL.md': header + body}))
            self.assertEqual(caught.exception.code, 'BUILD_INTEGRITY_FAILED')
        for field, scalar in (('description', '[]'), ('description', '{}'), ('description', 'null'),
                              ('description', 'true'), ('description', '123'), ('name', '123')):
            fields = {'name': '"sample"', 'description': '"Synthetic"', field: scalar}
            index = '---\n' + '\n'.join(k + ': ' + v for k, v in fields.items()) + '\n---\n' + body
            with self.subTest(field=field, scalar=scalar), self.assertRaises(CliError):
                skill_build.validate_package(dict(original, **{'SKILL.md': index}))
        numeric = original['SKILL.md'].replace('name: "sample"', 'name: "123"').replace('description: "Synthetic"', 'description: "123"')
        self.assertTrue(skill_build.validate_package(dict(original, **{'SKILL.md': numeric})))
        for name, description in (('../private-value', 'valid'), ('a' * 65, 'valid'), ('Bad_Name', 'valid'),
                                  ('sample', ''), ('sample', '  '), ('sample', 'x' * 1025), ('sample', 'bad\nvalue'), ('sample', 'bad\rvalue')):
            index = '---\nname: ' + json.dumps(name) + '\ndescription: ' + json.dumps(description) + '\n---\n' + body
            with self.assertRaises(CliError) as caught:
                skill_build.validate_package(dict(original, **{'SKILL.md': index}))
            self.assertEqual(caught.exception.code, 'BUILD_INTEGRITY_FAILED')
            self.assertNotIn('private-value', str(caught.exception))

    def test_complex_link_labels_validate_current_targets(self):
        _, _, _, original = self.candidate()
        for body in ('[Guide [v1]](missing.md)', r'[Guide \] v1](missing.md)',
                     '[Guide [v1]](../../outside.md)',
                     '[Guide [v1]][destination]\n\n[destination]: missing.md',
                     r'[Guide \] v1][destination]' + '\n\n[destination]: ../../outside.md',
                     r'[Guide][destination\]id]' + '\n\n' + r'[destination\]id]: missing.md',
                     r'[Guide \] v1][]' + '\n\n' + r'[Guide \] v1]: missing.md',
                     r'[Guide \] v1]' + '\n\n' + r'[Guide \] v1]: missing.md'):
            with self.subTest(body=body):
                with self.assertRaises(CliError) as caught:
                    skill_build.validate_package(dict(original, **{'SKILL.md': original['SKILL.md'] + '\n' + body}))
                self.assertEqual(caught.exception.code, 'BUILD_INTEGRITY_FAILED')
        for body in ('[Guide [v1]](present.md)', r'[Guide \] v1](present.md)',
                     '[Guide [v1]][destination]\n\n[destination]: present.md',
                     r'[Guide \] v1][destination]' + '\n\n[destination]: present.md',
                     r'[Guide][destination\]id]' + '\n\n' + r'[destination\]id]: present.md',
                     r'[Guide \] v1][]' + '\n\n' + r'[Guide \] v1]: present.md',
                     r'[Guide \] v1]' + '\n\n' + r'[Guide \] v1]: present.md'):
            with self.subTest(body=body):
                files = dict(original, **{'SKILL.md': original['SKILL.md'] + '\n' + body, 'present.md': 'Current content'})
                self.assertTrue(skill_build.validate_package(files))
        for body in ('`[Guide [v1]](missing.md)`', '```md\n[Guide [v1]](missing.md)\n```',
                     '    [Guide [v1]](missing.md)', r'\[Guide \] v1](missing.md)'):
            with self.subTest(masked=body):
                self.assertTrue(skill_build.validate_package(dict(original, **{'SKILL.md': original['SKILL.md'] + '\n' + body})))

    def test_inner_active_links_disable_containing_links(self):
        _, _, _, original = self.candidate()
        for destination in ('missing.md', '../../outside.md'):
            for inner, definition in (('[inner]({target})', ''),
                                      ('[inner][ref]', '\n\n[ref]: {target}'),
                                      ('[inner][]', '\n\n[inner]: {target}'),
                                      ('[inner]', '\n\n[inner]: {target}')):
                body = ('[outer ' + inner + '](present.md)' + definition).format(target=destination)
                with self.subTest(body=body):
                    files = dict(original, **{'SKILL.md': original['SKILL.md'] + '\n' + body, 'present.md': 'Present'})
                    with self.assertRaises(CliError) as caught:
                        skill_build.validate_package(files)
                    self.assertEqual(caught.exception.code, 'BUILD_INTEGRITY_FAILED')
                body = ('[outer ' + inner.format(target='present.md') + '](' + destination + ')' + definition.format(target='present.md'))
                with self.subTest(inactive_outer=body):
                    files = dict(original, **{'SKILL.md': original['SKILL.md'] + '\n' + body, 'present.md': 'Present'})
                    self.assertTrue(skill_build.validate_package(files))
        body = '[Guide [v1]](present.md)\n\n[v1]: missing.md'
        with self.assertRaises(CliError):
            skill_build.validate_package(dict(original, **{'SKILL.md': original['SKILL.md'] + '\n' + body, 'present.md': 'Present'}))
        for target in ('missing.md', '../../outside.md'):
            with self.subTest(image_label_target=target), self.assertRaises(CliError):
                skill_build.validate_package(dict(original, **{'SKILL.md': original['SKILL.md'] + '\n[![figure](figure.png)](' + target + ')', 'figure.png': b'image'}))
        for body in ('[![figure](figure.png)](present.md)',
                     '[![figure][image]][guide]\n\n[image]: figure.png\n[guide]: present.md',
                     '[outer [literal][undefined]](present.md)'):
            with self.subTest(valid=body):
                self.assertTrue(skill_build.validate_package(dict(original, **{'SKILL.md': original['SKILL.md'] + '\n' + body,
                                                                              'figure.png': b'image', 'present.md': 'Present'})))

    def test_image_alt_links_wait_for_image_boundary_resolution(self):
        _, _, _, original = self.candidate()
        for target in ('missing.md', '../../outside.md'):
            body = '[![figure [caption](present.md)](figure.png)](' + target + ')'
            with self.subTest(outer=target):
                self.assertEqual(list(skill_build._reference_links(body)), [target])
                files = dict(original, **{'SKILL.md': original['SKILL.md'] + '\n' + body,
                                          'present.md': 'Present', 'figure.png': b'image'})
                with self.assertRaises(CliError) as caught:
                    skill_build.validate_package(files)
                self.assertEqual(caught.exception.code, 'BUILD_INTEGRITY_FAILED')
        for caption, definition in (('[caption](missing.md)', ''),
                                    ('[caption][ref]', '\n\n[ref]: missing.md'),
                                    ('[caption][]', '\n\n[caption]: missing.md'),
                                    ('[caption]', '\n\n[caption]: missing.md')):
            for image_suffix, image_definition in (('(figure.png)', ''), ('[image]', '\n\n[image]: figure.png')):
                body = '[![figure ' + caption + ']' + image_suffix + '](present.md)' + definition + image_definition
                with self.subTest(successful_image=body):
                    self.assertEqual(list(skill_build._reference_links(body)), ['present.md'])
            for image_suffix in ('', '[undefined]'):
                body = '[![figure ' + caption + ']' + image_suffix + '](present.md)' + definition
                with self.subTest(failed_image=body):
                    self.assertEqual(list(skill_build._reference_links(body)), ['missing.md'])
        for body, expected in (
                ('[![figure [caption](missing.md)](figure.png)](present.md)', ['present.md']),
                ('[![figure [caption](missing.md)]](present.md)', ['missing.md']),
                ('[![figure [caption](missing.md)][undefined]](present.md)', ['missing.md']),
                ('![unclosed [caption](missing.md)', ['missing.md']),
                ('![unclosed [caption](missing.md)\n[other](present.md)', ['missing.md', 'present.md']),
                ('[![outer ![failed [caption](missing.md)]](figure.png)](present.md)', ['present.md']),
                ('[![outer ![failed [caption](missing.md)]]](present.md)', ['missing.md']),
                ('[![outer ![nested [caption](missing.md)](figure.png)]](present.md)', ['present.md']),
                ('[outer [![figure [caption](missing.md)](figure.png)](present.md)](missing.md)', ['present.md'])):
            with self.subTest(boundary=body):
                self.assertEqual(list(skill_build._reference_links(body)), expected)

    def test_generation_warning_whitelist_and_static_guidance(self):
        text, chunks, meta, files = self.candidate()
        for warnings in (['oversized:unknown-private-value'], ['https://private.invalid/?secret=x'], 'oversized:atomic', [None]):
            chunks[0]['warnings'] = warnings
            with self.assertRaises(CliError) as caught:
                skill_build.render(text, chunks, meta)
            self.assertNotIn('private', str(caught.exception))
        chunks[0]['warnings'] = ['oversized:atomic']
        files = skill_build.render(text, chunks, meta)
        self.assertIn('oversized:atomic', files['SKILL.md'])
        self.assertEqual(json.loads(files['.learnova-build.json']),
                         {'schema_version': 2, 'source': 'local-build', 'version': None})


if __name__ == '__main__':
    unittest.main()
