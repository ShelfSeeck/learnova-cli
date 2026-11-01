"""End-to-end host build protocol with synthetic local data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[1] / 'skills/learnova-cli/scripts/learnova.py'


class RuleVersionTests(unittest.TestCase):
    def test_completed_rule_change_conflict_and_explicit_force_preserves_parse(self):
        from unittest.mock import patch
        sys.path.insert(0, str(CLI.parent))
        import store
        from build.pipeline import BuildService
        from core.errors import CliError
        class Parser:
            calls = 0
            def advance(self, source, config, state, checkpoint):
                self.calls += 1
                return {'status': 'completed', 'markdown': '# Synthetic\n\nContent.\n', 'assets': {}, 'page_map': None}
        def finish(service, task):
            for _ in range(20):
                state = service.next(task)
                if state['status'] == 'completed':
                    return state
                if state['status'] == 'needs_input':
                    req = state['request']
                    result = {'splits': []} if req['kind'] == 'split_plan' else ({'title': 'Topic', 'summary': 'Summary', 'keywords': ['topic']} if req['kind'] == 'chunk_metadata' else {'name': 'sample', 'description': 'Reference'})
                    service.submit(task, {**req, 'result': result})
            self.fail('task did not complete')
        with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'LEARNOVA_HOME', Path(tmp) / 'home'), patch.dict(os.environ, {'SYNTHETIC_PARSER_KEY': 'synthetic'}):
            root = Path(tmp)
            source = root / 'input.pdf'
            source.write_bytes(b'%PDF synthetic')
            parser = Parser()
            service = BuildService(root, parser=parser)
            task = service.create(source)['task_id']
            service.reconfigure(task, {'remote_endpoint': 'http://localhost:1234', 'remote_key_env': 'SYNTHETIC_PARSER_KEY'})
            original = finish(service, task)
            self.assertEqual(parser.calls, 1)
            with patch.object(BuildService, '_rules_hash', return_value='new-rules'):
                self.assertEqual(service.status(task)['status'], 'completed')
                self.assertEqual(service.next(task)['status'], 'completed')
                service.resume(task, from_stage='chunking')
                self.assertEqual(service.status(task)['stage'], 'chunking')
                with self.assertRaises(CliError) as caught:
                    finish(service, task)
                self.assertEqual(caught.exception.code, 'SKILL_CONFLICT')
                failed = service.status(task)
                self.assertEqual(failed['stage'], 'indexing')
                self.assertTrue(Path(original['result_path']).is_dir())
                service.resume(task, force=True)
                self.assertEqual(service.status(task)['stage'], 'indexing')
                self.assertEqual(finish(service, task)['status'], 'completed')
                service.resume(task)
                from build import jobs
                self.assertTrue(jobs.read(jobs.task_dir(root, task))['force'])
            self.assertEqual(parser.calls, 1)

    def test_rule_change_preserves_parsing_and_invalidates_pending_request(self):
        from unittest.mock import patch
        sys.path.insert(0, str(CLI.parent))
        import store
        from build.pipeline import BuildService
        from core.errors import CliError
        with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'LEARNOVA_HOME', Path(tmp) / 'home'):
            root = Path(tmp)
            source = root / 'input.md'
            source.write_text('# Topic\n\nContent.\n')
            service = BuildService(root)
            task = service.create(source)['task_id']
            old = service.next(task)['request']
            with patch.object(BuildService, '_rules_hash', return_value='changed-rule'):
                state = service.status(task)
                self.assertEqual(state['stage'], 'chunking')
                self.assertIsNone(state['request'])
                self.assertEqual(service.read(task)['text'], source.read_text())
                with self.assertRaises(CliError) as caught:
                    service.submit(task, {**old, 'result': {'splits': []}})
                self.assertEqual(caught.exception.code, 'BUILD_STALE_REQUEST')
                self.assertNotEqual(service.next(task)['request']['request_id'], old['request_id'])


class AutoFailureTests(unittest.TestCase):
    def test_invalid_plan_preserves_request_and_recovery(self):
        from unittest.mock import patch
        sys.path.insert(0, str(CLI.parent))
        import store
        from build.pipeline import BuildService
        from core.errors import CliError
        class Invalid:
            def decide(self, request, config):
                return {'splits': ['invalid-boundary']}
        with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'LEARNOVA_HOME', Path(tmp) / 'home'), patch.dict(os.environ, {'SYNTHETIC_MODEL_KEY': 'synthetic-key'}):
            root = Path(tmp)
            source = root / 'source.md'
            source.write_text('# A\n\nText.\n')
            service = BuildService(root, decision=Invalid())
            task = service.create(source, mode='auto')['task_id']
            service.reconfigure(task, {'llm_base_url': 'http://localhost:1234', 'llm_model': 'test', 'llm_key_env': 'SYNTHETIC_MODEL_KEY'})
            with self.assertRaises(CliError) as caught:
                service.run(task)
            self.assertEqual(caught.exception.code, 'BUILD_PLAN_INVALID')
            failed = service.status(task)
            self.assertEqual(failed['status'], 'failed')
            self.assertEqual(failed['request']['kind'], 'split_plan')
            resumed = service.resume(task, mode='agent')
            self.assertEqual(resumed['request'], failed['request'])
            self.assertEqual(service.submit(task, {**resumed['request'], 'result': {'splits': []}})['status'], 'pending')

    def test_parser_polling_waits_only_in_run(self):
        from unittest.mock import patch
        sys.path.insert(0, str(CLI.parent))
        import store
        from build.pipeline import BuildService
        class Parser:
            def advance(self, source, config, state, checkpoint):
                return {'status': 'running', 'progress': {'state': 'running'}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'LEARNOVA_HOME', Path(tmp) / 'home'), patch.dict(os.environ, {'SYNTHETIC_PARSER_KEY': 'synthetic'}):
            root = Path(tmp)
            source = root / 'source.pdf'
            source.write_bytes(b'%PDF synthetic')
            sleeps = []
            service = BuildService(root, parser=Parser(), sleeper=sleeps.append)
            task = service.create(source)['task_id']
            service.reconfigure(task, {'remote_endpoint': 'http://localhost:1234', 'remote_key_env': 'SYNTHETIC_PARSER_KEY'})
            service.next(task)
            self.assertEqual(sleeps, [])
            service.run(task, max_steps=2)
            self.assertEqual(sleeps, [3])


class HostBuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, 'HOME': str(self.root), 'LEARNOVA_HOME': str(self.root / 'home')}
        self.source = self.root / 'source.md'
        self.source.write_text('# Alpha\n\nFirst.\n\n# Beta\n\nSecond.\n', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args, error=False):
        p = subprocess.run([sys.executable, str(CLI), '--json', 'build', *args, '--project-root', str(self.root)], env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode == 0, not error, p.stdout + p.stderr)
        return json.loads(p.stdout)

    def answer(self, state, result):
        req = state['request']
        return self.cli('submit', state['task_id'], '--data', json.dumps({**req, 'result': result}))

    def finish(self, state):
        while state['status'] != 'completed':
            state = self.cli('next', state['task_id'])
            if state['status'] != 'needs_input':
                continue
            kind = state['request']['kind']
            if kind == 'split_plan':
                result = {'splits': []}
            elif kind == 'chunk_metadata':
                result = {'title': 'Section', 'summary': 'Contents', 'keywords': ['topic']}
            else:
                result = {'name': 'demo', 'description': 'Reference material'}
            state = self.answer(state, result)
        return state

    def test_host_lifecycle_and_reconstruction(self):
        state = self.cli('create', str(self.source))
        task = state['task_id']
        state = self.cli('next', task)
        old = state['request']
        self.assertEqual(old['kind'], 'split_plan')
        self.assertEqual(old, self.cli('next', task)['request'])
        page = self.cli('read', task, '--limit', '5')
        self.assertEqual(page['text'], '# Alp')
        self.cli('pause', task)
        self.assertEqual(self.cli('next', task)['status'], 'paused')
        self.cli('resume', task, '--from', 'chunking')
        self.assertEqual(self.cli('submit', task, '--data', json.dumps({**old, 'result': {'splits': []}}), error=True)['error']['code'], 'BUILD_STALE_REQUEST')
        state = self.finish(self.cli('status', task))
        output = Path(state['result_path'])
        chunks = self.cli('read', task, '--kind', 'chunks')['items']
        self.assertEqual(chunks[0]['summary'], 'Contents')
        self.assertEqual(chunks[0]['keywords'], ['topic'])
        provenance = json.loads((output / '.learnova-build.json').read_text())
        reconstructed = ''.join((output / ('references/' + c['id'] + '.md')).read_text() for c in chunks)
        self.assertEqual(reconstructed, self.source.read_text())
        self.assertTrue(Path(self.cli('package', task)['package']).is_file())

    def test_snapshot_and_input_change(self):
        self.cli('config', 'set', 'max_tokens', '300')
        state = self.cli('create', str(self.source))
        task = state['task_id']
        request = self.cli('next', task)['request']
        self.cli('config', 'set', 'max_tokens', '500')
        job = json.loads((self.root / '.learnova/builds' / task / 'job.json').read_text())
        self.assertEqual(job['config']['max_tokens'], 300)
        self.source.write_text('changed', encoding='utf-8')
        err = self.cli('submit', task, '--data', json.dumps({**request, 'result': {'splits': []}}), error=True)
        self.assertEqual(err['error']['code'], 'BUILD_STALE_REQUEST')
        self.assertEqual(self.cli('status', task)['stage'], 'parsing')

    def test_reference_image_and_conflict(self):
        image = self.root / 'img.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'fake')
        self.source.write_text('# Image\n\n![alt][picture]\n\n[picture]: img.png\n', encoding='utf-8')
        done = self.finish(self.cli('create', str(self.source)))
        ref = next((Path(done['result_path']) / 'references').glob('*.md')).read_text()
        self.assertIn('![alt](images/', ref)
        self.assertEqual(len(list((Path(done['result_path']) / 'references/images').iterdir())), 1)
        state = self.cli('create', str(self.source))
        with self.assertRaises(AssertionError):
            self.finish(state)
        self.assertTrue((Path(done['result_path']) / 'SKILL.md').exists())


class ManagedWorkflowTests(unittest.TestCase):
    setUp = HostBuildTest.setUp
    tearDown = HostBuildTest.tearDown
    cli = HostBuildTest.cli

    def test_managed_start_batches_semantics_and_continues(self):
        self.cli('config', 'set', '--set', 'min_tokens=1', '--set', 'max_tokens=6')
        state = self.cli('start', str(self.source), '--name', 'demo')
        task = state['task_id']
        req = state['request']
        listed = self.cli('tasks')
        self.assertEqual(listed['tasks'][0]['task_id'], task)
        self.assertNotIn('text', listed['tasks'][0])
        self.assertEqual(req['kind'], 'split_plan')
        self.assertTrue(req['proposal']['splits'])
        state = self.cli('submit', task, '--data', json.dumps({**req, 'result': req['proposal']}), '--continue', '--batch-size', '2')
        req = state['request']
        self.assertEqual(req['kind'], 'chunk_metadata_batch')
        ids = req['scope']['ids']
        self.assertEqual(len(ids), 2)
        self.assertTrue(all(c['samples'] and c['headings'] for c in req['chunks']))
        values = {key: {'title': 'Topic', 'summary': 'Contents', 'keywords': ['topic']} for key in ids}
        incomplete = {ids[0]: values[ids[0]]}
        failed = self.cli('submit', task, '--data', json.dumps({**req, 'result': {'chunks': incomplete}}), '--continue', error=True)
        self.assertEqual(failed['error']['code'], 'BUILD_PLAN_INVALID')
        self.assertEqual(self.cli('status', task)['summary']['metadata_done'], 0)
        self.assertEqual(self.cli('status', task)['request'], req)
        state = self.cli('submit', task, '--data', json.dumps({**req, 'result': {'chunks': values}}), '--continue')
        self.assertEqual(state['request']['kind'], 'skill_metadata')
        self.assertEqual(state['summary']['metadata_pending'], 0)
        req = state['request']
        state = self.cli('submit', task, '--data', json.dumps({**req, 'result': {'name': 'demo', 'description': 'Reference'}}), '--continue')
        self.assertEqual(state['status'], 'completed')
        output = Path(state['result_path'])
        chunks = self.cli('read', task, '--kind', 'chunks')['items']
        self.assertEqual(''.join((output / 'references' / (c['id'] + '.md')).read_text() for c in chunks), self.source.read_text())

    def test_managed_preflight_and_atomic_preset(self):
        report = self.cli('doctor', str(self.source), '--mode', 'auto')
        self.assertFalse(report['ready'])
        state = self.cli('start', str(self.source), '--mode', 'auto')
        self.assertEqual(state['status'], 'needs_input')
        self.assertNotIn('task_id', state)
        self.assertFalse((self.root / '.learnova/builds').exists())
        preset = self.cli('config', 'preset', 'mineru')
        self.assertEqual(preset['values']['remote_key_env'], 'MINERU_API_KEY')
        path = self.root / '.learnova/build-config.json'
        before = path.read_bytes()
        self.cli('config', 'set', '--set', 'min_tokens=3000', '--set', 'max_tokens=10', error=True)
        self.assertEqual(path.read_bytes(), before)

    def test_managed_html_table_preserves_literal_source(self):
        self.source.write_text('<table><tr><td>[Axis](symbol)' + ' value' * 2000 + '</td></tr></table>\n')
        self.cli('config', 'set', '--set', 'min_tokens=1', '--set', 'max_tokens=10')
        state = self.cli('start', str(self.source))
        task = state['task_id']
        req = state['request']
        state = self.cli('submit', task, '--data', json.dumps({**req, 'result': req['proposal']}), '--continue')
        self.assertEqual(state['request']['kind'], 'chunk_metadata_batch')
        self.assertEqual(state['warning_count'], 1)
        req = state['request']
        values = {key: {'title': 'Data table', 'summary': 'Synthetic values', 'keywords': ['data']} for key in req['scope']['ids']}
        state = self.cli('submit', task, '--data', json.dumps({**req, 'result': {'chunks': values}}), '--continue')
        req = state['request']
        state = self.cli('submit', task, '--data', json.dumps({**req, 'result': {'name': 'data-table', 'description': 'Reference'}}), '--continue')
        self.assertEqual(state['status'], 'completed')
        files = list((Path(state['result_path']) / 'references').glob('*.md'))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_text(), self.source.read_text())


if __name__ == '__main__':
    unittest.main()

class AdapterTest(unittest.TestCase):
    def test_parser_checkpoint_and_resume(self):
        from unittest.mock import patch
        scripts = str(CLI.parent)
        sys.path.insert(0, scripts)
        try:
            import store
            from build.pipeline import BuildService
            with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'LEARNOVA_HOME', Path(tmp) / 'home'):
                root = Path(tmp)
                source = root / 'input.pdf'
                source.write_bytes(b'%PDF synthetic')
                class Parser:
                    def advance(self, source, cfg, state, checkpoint):
                        if not state:
                            checkpoint({'batch_id': 'synthetic'})
                            return {'status': 'running', 'progress': {'part': 1}}
                        self_state = state['batch_id']
                        if self_state != 'synthetic':
                            raise AssertionError(self_state)
                        return {'status': 'completed', 'markdown': '# Parsed\n', 'assets': {}, 'page_map': [{'page': 1}]}
                service = BuildService(root, parser=Parser())
                state = service.create(source)
                task = state['task_id']
                config_path = root / '.learnova/build-config.json'
                config_path.write_text(json.dumps({'remote_endpoint': 'http://localhost:1234', 'remote_key_env': ''}))
                service.reconfigure(task, {'remote_endpoint': 'http://localhost:1234', 'remote_key_env': ''})
                self.assertEqual(service.next(task)['progress'], {'part': 1})
                self.assertEqual(service.next(task)['request']['kind'], 'split_plan')
                saved = json.loads((root / '.learnova/builds' / task / 'job.json').read_text())
                self.assertEqual(saved['parser_state']['batch_id'], 'synthetic')
                self.assertEqual(saved['page_map'], [{'page': 1}])
        finally:
            sys.path.remove(scripts)
    def test_parser_failure_preserves_immediate_checkpoint(self):
        from unittest.mock import patch
        scripts = str(CLI.parent)
        sys.path.insert(0, scripts)
        try:
            import store
            from build.pipeline import BuildService
            class Parser:
                def advance(self, source, cfg, state, checkpoint):
                    if not state:
                        checkpoint({'batch_id': 'saved'})
                        raise RuntimeError('synthetic private details')
                    return {'status': 'completed', 'markdown': '# Parsed\n', 'assets': {}, 'page_map': []}
            with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'LEARNOVA_HOME', Path(tmp) / 'home'):
                root = Path(tmp)
                source = root / 'input.pdf'
                source.write_bytes(b'%PDF synthetic')
                service = BuildService(root, parser=Parser())
                task = service.create(source)['task_id']
                service.reconfigure(task, {'remote_endpoint': 'http://localhost:1234', 'remote_key_env': ''})
                with self.assertRaises(RuntimeError):
                    service.next(task)
                saved = json.loads((root / '.learnova/builds' / task / 'job.json').read_text())
                self.assertEqual(saved['parser_state']['batch_id'], 'saved')
                status = service.status(task)
                self.assertEqual(status['status'], 'failed')
                self.assertNotIn('private details', json.dumps(status))
                service.resume(task)
                self.assertEqual(service.next(task)['request']['kind'], 'split_plan')
        finally:
            sys.path.remove(scripts)


class BuildIntegrityTest(HostBuildTest):
    def test_unknown_and_duplicate_submissions_do_not_change_state(self):
        state = self.cli('create', str(self.source))
        task = state['task_id']
        state = self.cli('next', task)
        request = state['request']
        forged = {**request, 'request_id': 'unknown', 'result': {'splits': []}}
        self.assertEqual(self.cli('submit', task, '--data', json.dumps(forged), error=True)['error']['code'], 'BUILD_STALE_REQUEST')
        self.assertEqual(self.cli('status', task)['request'], request)
        self.answer(state, {'splits': []})
        self.assertEqual(self.cli('submit', task, '--data', json.dumps({**request, 'result': {'splits': []}}), error=True)['error']['code'], 'BUILD_STALE_REQUEST')
        self.assertIsNone(self.cli('status', task)['request'])

    def test_private_job_symlinks_are_rejected(self):
        task = self.cli('create', str(self.source))['task_id']
        path = self.root / '.learnova/builds' / task
        target = self.root / 'outside'
        target.write_text('untouched')
        (path / '.lock').symlink_to(target)
        self.assertEqual(self.cli('status', task, error=True)['error']['code'], 'BUILD_INTEGRITY_FAILED')
        self.assertEqual(target.read_text(), 'untouched')

    def test_corrupt_checkpoints_fail_typed(self):
        for key in ('structure', 'chunks', 'metadata', 'config', 'request'):
            state = self.cli('create', str(self.source))
            task = state['task_id']
            self.cli('next', task)
            path = self.root / '.learnova/builds' / task / 'job.json'
            data = json.loads(path.read_text())
            data.pop(key)
            path.write_text(json.dumps(data))
            result = self.cli('status', task, error=True)
            self.assertEqual(result['error']['code'], 'BUILD_INTEGRITY_FAILED')

    def test_reconfigure_dependency_and_bounded_paging(self):
        self.source.write_text(''.join(f'# Heading {i}\n\ntext {i}\n\n' for i in range(250)))
        state = self.cli('create', str(self.source))
        task = state['task_id']
        state = self.cli('next', task)
        req = state['request']
        self.assertGreater(req['boundary_count'], 100)
        self.assertEqual(len(req['allowed_boundaries']), 100)
        page = self.cli('read', task, '--kind', 'boundaries', '--start', '100', '--limit', '500')
        self.assertEqual(len(page['items']), 100)
        self.assertIsNotNone(page['next_start'])
        accepted = page['items'][0]['id']
        state = self.answer(state, {'splits': [accepted]})
        before = state['document_revision']
        state = self.cli('reconfigure', task, '--set', 'read_limit=100')
        self.assertEqual(state['document_revision'], before)
        state = self.cli('reconfigure', task, '--set', 'max_tokens=2100')
        self.assertGreater(state['document_revision'], before)
        self.assertEqual(state['stage'], 'chunking')
        path = self.root / '.learnova/builds' / task / 'job.json'
        self.assertIsNotNone(json.loads(path.read_text())['text'])
        state = self.cli('reconfigure', task, '--set', 'remote_endpoint=http://localhost:1234')
        self.assertEqual(state['stage'], 'parsing')
        self.assertIsNone(json.loads(path.read_text())['text'])

    def test_failed_conflict_resume_and_management(self):
        first = self.finish(self.cli('create', str(self.source)))
        output = Path(first['result_path'])
        original = (output / 'SKILL.md').read_bytes()
        state = self.cli('create', str(self.source))
        task = state['task_id']
        while True:
            result = self.cli('next', task)
            state = result
            if state['status'] == 'needs_input':
                kind = state['request']['kind']
                answer = {'splits': []} if kind == 'split_plan' else ({'title': 'Section', 'summary': 'Contents', 'keywords': ['topic']} if kind == 'chunk_metadata' else {'name': 'demo', 'description': 'Reference material'})
                state = self.answer(state, answer)
                if kind == 'skill_metadata':
                    result = self.cli('next', task, error=True)
                    self.assertEqual(result['error']['code'], 'SKILL_CONFLICT')
                    break
        self.assertEqual((output / 'SKILL.md').read_bytes(), original)
        self.assertEqual(self.cli('status', task)['status'], 'failed')
        self.assertEqual(self.cli('resume', task)['status'], 'pending')
        listing = subprocess.run([sys.executable, str(CLI), '--json', 'list', '--project'], cwd=self.root, env=self.env, capture_output=True, text=True, check=True)
        self.assertTrue(any(item['name'] == 'demo' and item['source'] == 'local-build' for item in json.loads(listing.stdout)['installed']))
        subprocess.run([sys.executable, str(CLI), '--json', 'uninstall', 'demo', '--project'], cwd=self.root, env=self.env, capture_output=True, text=True, check=True)
        self.assertFalse(output.exists())
        self.assertTrue(self.source.exists())
        self.assertTrue((self.root / '.learnova/builds' / task / 'job.json').exists())

    def test_cli_forced_replacement_rolls_back_on_failure(self):
        first = self.finish(self.cli('create', str(self.source)))
        output = Path(first['result_path'])
        original = (output / 'SKILL.md').read_bytes()
        state = self.cli('create', str(self.source), '--force')
        task = state['task_id']
        while True:
            state = self.cli('next', task)
            kind = state['request']['kind']
            answer = {'splits': []} if kind == 'split_plan' else ({'title': 'Section', 'summary': 'New description', 'keywords': ['topic']} if kind == 'chunk_metadata' else {'name': 'demo', 'description': 'New reference'})
            state = self.answer(state, answer)
            if kind == 'skill_metadata':
                break
        hook = self.root / 'hook'
        hook.mkdir()
        (hook / 'sitecustomize.py').write_text("import os\nfrom pathlib import Path\nreal = os.replace\ndef fail(source, target):\n    if Path(source).name == 'candidate' and Path(target).name == 'demo':\n        raise OSError('synthetic failure')\n    return real(source, target)\nos.replace = fail\n")
        env = {**self.env, 'PYTHONPATH': str(hook)}
        result = subprocess.run([sys.executable, str(CLI), '--json', 'build', 'next', task, '--project-root', str(self.root)], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((output / 'SKILL.md').read_bytes(), original)
        self.assertEqual(self.cli('status', task)['status'], 'failed')
        self.cli('resume', task)
        self.assertEqual(self.cli('next', task)['status'], 'completed')
        self.assertNotEqual((output / 'SKILL.md').read_bytes(), original)

    def test_crash_after_commit_reuses_matching_candidate(self):
        state = self.cli('create', str(self.source))
        task = state['task_id']
        while True:
            state = self.cli('next', task)
            kind = state['request']['kind']
            answer = {'splits': []} if kind == 'split_plan' else ({'title': 'Section', 'summary': 'Contents', 'keywords': ['topic']} if kind == 'chunk_metadata' else {'name': 'demo', 'description': 'Reference material'})
            state = self.answer(state, answer)
            if kind == 'skill_metadata':
                break
        hook = self.root / 'hook'
        hook.mkdir()
        (hook / 'sitecustomize.py').write_text("import os\nfrom pathlib import Path\nreal = os.replace\ndef crash(source, target):\n    result = real(source, target)\n    if Path(source).name == 'candidate' and Path(target).name == 'demo':\n        os._exit(73)\n    return result\nos.replace = crash\n")
        env = {**self.env, 'PYTHONPATH': str(hook)}
        result = subprocess.run([sys.executable, str(CLI), '--json', 'build', 'next', task, '--project-root', str(self.root)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 73)
        self.assertTrue((self.root / '.agents/skills/demo/SKILL.md').exists())
        self.assertEqual(self.cli('next', task)['status'], 'completed')

    def test_user_config_is_validated_without_project_override(self):
        self.cli('config', 'set', 'max_tokens', '1000')
        result = self.cli('config', 'set', 'min_tokens', '1500', '--user')
        self.assertEqual(result['value'], 1500)
        user = json.loads((self.root / 'home/build-config.json').read_text())
        self.assertEqual(user['min_tokens'], 1500)

    def test_mutated_checkpoint_rejected(self):
        task = self.cli('create', str(self.source))['task_id']
        self.cli('next', task)
        path = self.root / '.learnova/builds' / task / 'job.json'
        state = json.loads(path.read_text())
        state['structure']['headings'][0]['title'] = 'altered'
        path.write_text(json.dumps(state))
        self.assertEqual(self.cli('next', task, error=True)['error']['code'], 'BUILD_INTEGRITY_FAILED')

    def test_lock_releases_after_process_crash(self):
        if os.name == 'nt':
            self.skipTest('POSIX crash test')
        state = self.cli('create', str(self.source))
        task = state['task_id']
        lock = self.root / '.learnova/builds' / task / '.lock'
        code = 'import fcntl,sys,time; f=open(sys.argv[1],"a+"); fcntl.flock(f,fcntl.LOCK_EX); print("locked",flush=True); time.sleep(60)'
        holder = subprocess.Popen([sys.executable, '-c', code, str(lock)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), 'locked')
            waiting = subprocess.Popen([sys.executable, str(CLI), '--json', 'build', 'status', task, '--project-root', str(self.root)], env=self.env, stdout=subprocess.PIPE, text=True)
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    waiting.wait(timeout=0.3)
                holder.kill()
                holder.wait(timeout=5)
                self.assertEqual(waiting.wait(timeout=5), 0)
            finally:
                if waiting.poll() is None:
                    waiting.kill()
                waiting.communicate()
        finally:
            if holder.poll() is None:
                holder.kill()
            holder.communicate()

class ProviderAndRefinementTest(unittest.TestCase):
    def test_recursive_protection_and_auto_host_parity(self):
        from unittest.mock import patch
        scripts = str(CLI.parent)
        sys.path.insert(0, scripts)
        try:
            import store
            from build.pipeline import BuildService
            class Decision:
                def decide(self, request, cfg):
                    kind = request['kind']
                    if kind == 'split_plan':
                        return {'splits': []}
                    if kind == 'chunk_metadata':
                        return {'title': 'Section', 'summary': 'Contents', 'keywords': ['topic']}
                    return {'name': 'demo', 'description': 'Reference material'}
            with tempfile.TemporaryDirectory() as tmp, patch.object(store, 'LEARNOVA_HOME', Path(tmp) / 'home'), patch.dict(os.environ, {'SYNTHETIC_KEY': 'synthetic'}):
                paths = []
                for mode in ('agent', 'auto'):
                    root = Path(tmp) / mode
                    root.mkdir()
                    source = root / 'input.md'
                    source.write_text('# A\n\nExample.\n')
                    svc = BuildService(root, decision=Decision())
                    state = svc.create(source, mode=mode)
                    task = state['task_id']
                    if mode == 'auto':
                        svc.reconfigure(task, {'llm_base_url': 'http://localhost:1234', 'llm_model': 'synthetic', 'llm_key_env': 'SYNTHETIC_KEY'})
                        state = svc.run(task)
                    else:
                        while state['status'] != 'completed':
                            state = svc.next(task)
                            if state['status'] == 'needs_input':
                                state = svc.submit(task, {**state['request'], 'result': Decision().decide(state['request'], {})})
                    self.assertEqual(state['status'], 'completed')
                    paths.append(Path(state['result_path']))
                self.assertEqual((paths[0] / 'SKILL.md').read_bytes(), (paths[1] / 'SKILL.md').read_bytes())
                self.assertEqual((paths[0] / '.learnova-build.json').read_bytes(), (paths[1] / '.learnova-build.json').read_bytes())

                root = Path(tmp) / 'recursive'
                root.mkdir()
                source = root / 'input.md'
                source.write_text('# One\n\n' + 'a' * 80 + '\n\n# Two\n\n' + 'b' * 80 + '\n\n# Three\n\n' + 'c' * 80 + '\n')
                svc = BuildService(root)
                task = svc.create(source)['task_id']
                svc.reconfigure(task, {'min_tokens': '0', 'max_tokens': '5'})
                first = svc.next(task)
                boundaries = svc.read(task, kind='boundaries')['items']
                second = next(b['id'] for b in boundaries if b['offset'] == source.read_text().index('# Two'))
                second_offset = int(second[1:])
                svc.submit(task, {**first['request'], 'result': {'splits': [second], 'protected_ranges': [['b0', second]]}})
                fine = svc.next(task)
                self.assertEqual(fine['request']['scope']['start'], 0)
                self.assertEqual(fine['request']['scope']['end'], second_offset)
                self.assertEqual(fine['request']['allowed_boundaries'], [])
                svc.submit(task, {**fine['request'], 'result': {'splits': [], 'atomic': True}})
                fine = svc.next(task)
                self.assertEqual(fine['request']['scope']['start'], second_offset)
                split = fine['request']['allowed_boundaries'][-1]
                svc.submit(task, {**fine['request'], 'result': {'splits': [split]}})
                saved = json.loads((root / '.learnova/builds' / task / 'job.json').read_text())
                self.assertTrue(any(c['depth'] == 1 for c in saved['chunks']))
                self.assertTrue(any('oversized:atomic' in c['warnings'] for c in saved['chunks']))
        finally:
            sys.path.remove(scripts)

class ReviewFixTest(HostBuildTest):
    def test_reject_credential_urls_and_bad_ipv6_without_echo(self):
        for value in ('https://example.invalid/parser?api_key=synthetic_secret', 'https://example.invalid/#synthetic_secret', 'https://user:synthetic_secret@example.invalid', 'https://['):
            result = self.cli('config', 'set', 'remote_endpoint', value, error=True)
            self.assertEqual(result['error']['code'], 'BUILD_CONFIG_INVALID')
            self.assertNotIn('synthetic_secret', json.dumps(result))
            self.assertFalse((self.root / '.learnova/build-config.json').exists())

    def test_saved_credential_url_rejected_before_task_creation(self):
        config_dir = self.root / '.learnova'
        config_dir.mkdir()
        (config_dir / 'build-config.json').write_text(json.dumps({'remote_endpoint': 'https://example.invalid/?api_key=synthetic_secret'}))
        result = self.cli('create', str(self.source), error=True)
        self.assertEqual(result['error']['code'], 'BUILD_CONFIG_INVALID')
        self.assertNotIn('synthetic_secret', json.dumps(result))
        self.assertFalse((config_dir / 'builds').exists())

    def test_crash_after_backup_restores_before_retry(self):
        first = self.finish(self.cli('create', str(self.source)))
        output = Path(first['result_path'])
        original = (output / 'SKILL.md').read_bytes()
        state = self.cli('create', str(self.source), '--force')
        task = state['task_id']
        while True:
            state = self.cli('next', task)
            kind = state['request']['kind']
            answer = {'splits': []} if kind == 'split_plan' else ({'title': 'Section', 'summary': 'Updated', 'keywords': ['topic']} if kind == 'chunk_metadata' else {'name': 'demo', 'description': 'Updated reference'})
            state = self.answer(state, answer)
            if kind == 'skill_metadata':
                break
        hook = self.root / 'hook'
        hook.mkdir()
        file = hook / 'sitecustomize.py'
        file.write_text("import os\nfrom pathlib import Path\nreal = os.replace\ndef crash(source, target):\n    result = real(source, target)\n    if Path(source).name == 'demo' and Path(target).name == 'backup':\n        os._exit(73)\n    return result\nos.replace = crash\n")
        env = {**self.env, 'PYTHONPATH': str(hook)}
        args = [sys.executable, str(CLI), '--json', 'build', 'next', task, '--project-root', str(self.root)]
        result = subprocess.run(args, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 73)
        self.assertFalse(output.exists())
        file.write_text("import os\nfrom pathlib import Path\nreal = os.replace\ndef fail(source, target):\n    if Path(source).name == 'candidate' and Path(target).name == 'demo':\n        raise OSError('synthetic failure')\n    return real(source, target)\nos.replace = fail\n")
        result = subprocess.run(args, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((output / 'SKILL.md').read_bytes(), original)
        self.assertEqual(self.cli('status', task)['status'], 'failed')
        self.cli('resume', task)
        self.assertEqual(self.cli('next', task)['status'], 'completed')
        self.assertNotEqual((output / 'SKILL.md').read_bytes(), original)

    def test_forced_commit_crash_restores_backup_before_new_attempt(self):
        first = self.finish(self.cli('create', str(self.source)))
        output = Path(first['result_path'])
        original = (output / 'SKILL.md').read_bytes()
        state = self.cli('create', str(self.source), '--force')
        task = state['task_id']
        while True:
            state = self.cli('next', task)
            kind = state['request']['kind']
            result = {'splits': []} if kind == 'split_plan' else ({'title': 'Section', 'summary': 'Updated', 'keywords': ['topic']} if kind == 'chunk_metadata' else {'name': 'demo', 'description': 'Updated reference'})
            state = self.answer(state, result)
            if kind == 'skill_metadata':
                break
        hook = self.root / 'hook'
        hook.mkdir()
        file = hook / 'sitecustomize.py'
        file.write_text("import os\nfrom pathlib import Path\nreal = os.replace\ndef crash(source, target):\n    result = real(source, target)\n    if Path(source).name == 'candidate' and Path(target).name == 'demo':\n        os._exit(73)\n    return result\nos.replace = crash\n")
        env = {**self.env, 'PYTHONPATH': str(hook), 'PYTHONDONTWRITEBYTECODE': '1'}
        args = [sys.executable, str(CLI), '--json', 'build', 'next', task, '--project-root', str(self.root)]
        self.assertEqual(subprocess.run(args, env=env, capture_output=True).returncode, 73)
        self.assertNotEqual((output / 'SKILL.md').read_bytes(), original)
        file.write_text("import os\nfrom pathlib import Path\nreal = os.replace\ndef fail(source, target):\n    if Path(source).name == 'candidate' and Path(target).name == 'demo':\n        raise OSError('synthetic failure')\n    return real(source, target)\nos.replace = fail\n")
        self.assertNotEqual(subprocess.run(args, env=env, capture_output=True).returncode, 0)
        self.assertEqual((output / 'SKILL.md').read_bytes(), original)

    def test_recovery_interrupted_after_restore_is_idempotent(self):
        first = self.finish(self.cli('create', str(self.source)))
        output = Path(first['result_path'])
        original = (output / 'SKILL.md').read_bytes()
        state = self.cli('create', str(self.source), '--force')
        task = state['task_id']
        while True:
            state = self.cli('next', task)
            kind = state['request']['kind']
            result = {'splits': []} if kind == 'split_plan' else ({'title': 'Section', 'summary': 'Updated', 'keywords': ['topic']} if kind == 'chunk_metadata' else {'name': 'demo', 'description': 'Updated reference'})
            state = self.answer(state, result)
            if kind == 'skill_metadata':
                break
        hook = self.root / 'hook'
        hook.mkdir()
        script = hook / 'sitecustomize.py'
        env = {**self.env, 'PYTHONPATH': str(hook), 'PYTHONDONTWRITEBYTECODE': '1'}
        args = [sys.executable, str(CLI), '--json', 'build', 'next', task, '--project-root', str(self.root)]
        def crash_after(source_name, code):
            script.write_text("import os\nfrom pathlib import Path\nreal = os.replace\ndef crash(source, target):\n    result = real(source, target)\n    if Path(source).name == %r and Path(target).name == 'demo':\n        os._exit(%d)\n    return result\nos.replace = crash\n" % (source_name, code))
            return subprocess.run(args, env=env, capture_output=True, text=True)
        self.assertEqual(crash_after('candidate', 73).returncode, 73)
        self.assertNotEqual((output / 'SKILL.md').read_bytes(), original)
        self.assertEqual(crash_after('backup', 74).returncode, 74)
        self.assertEqual((output / 'SKILL.md').read_bytes(), original)
        self.assertEqual(self.cli('next', task)['status'], 'completed')
        self.assertNotEqual((output / 'SKILL.md').read_bytes(), original)
        self.assertFalse(list(output.parent.glob('.learnova-txn-*.json')))

    def test_auto_configuration_clears_when_switching_to_agent(self):
        state = self.cli('create', str(self.source), '--mode', 'auto')
        task = state['task_id']
        state = self.cli('run', task)
        self.assertEqual(state['request']['scope'], 'llm')
        state = self.cli('resume', task, '--mode', 'agent')
        self.assertIsNone(state['request'])
        state = self.cli('next', task)
        self.assertEqual(state['request']['kind'], 'split_plan')

    def test_agent_switch_preserves_accepted_chunk_plan(self):
        state = self.cli('create', str(self.source))
        task = state['task_id']
        state = self.cli('next', task)
        self.answer(state, {'splits': []})
        state = self.cli('run', task, '--mode', 'auto')
        self.assertEqual(state['request']['scope'], 'llm')
        saved = json.loads((self.root / '.learnova/builds' / task / 'job.json').read_text())
        self.assertTrue(saved['chunks'])
        state = self.cli('resume', task, '--mode', 'agent')
        self.assertIsNone(state['request'])
        state = self.cli('next', task)
        self.assertEqual(state['request']['kind'], 'chunk_metadata')
        after = json.loads((self.root / '.learnova/builds' / task / 'job.json').read_text())
        self.assertEqual(saved['chunks'], after['chunks'])

    def test_environment_supply_unblocks_parser_and_identical_reconfigure(self):
        from unittest.mock import patch
        scripts = str(CLI.parent)
        sys.path.insert(0, scripts)
        try:
            import store
            from build.pipeline import BuildService
            class Parser:
                def advance(self, source, cfg, state, checkpoint):
                    return {'status': 'completed', 'markdown': '# Synthetic\n', 'assets': {}, 'page_map': []}
            source = self.root / 'source.pdf'
            source.write_bytes(b'%PDF synthetic')
            with patch.object(store, 'LEARNOVA_HOME', self.root / 'home'), patch.dict(os.environ, {}, clear=True):
                service = BuildService(self.root, parser=Parser())
                task = service.create(source)['task_id']
                service.reconfigure(task, {'remote_endpoint': 'https://example.invalid', 'remote_key_env': 'SYNTHETIC_KEY'})
                self.assertEqual(service.next(task)['request']['kind'], 'configuration')
                service.reconfigure(task, {'remote_key_env': 'SYNTHETIC_KEY'})
                os.environ['SYNTHETIC_KEY'] = 'synthetic'
                self.assertIsNone(service.resume(task)['request'])
                self.assertEqual(service.next(task)['request']['kind'], 'split_plan')
        finally:
            sys.path.remove(scripts)

class TransactionSafetyTest(unittest.TestCase):
    def test_forged_journal_cannot_touch_unowned_paths(self):
        import hashlib
        scripts = str(CLI.parent)
        sys.path.insert(0, scripts)
        try:
            from build.transactions import transaction
            from core.errors import CliError
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target = root / 'demo'
                victim = root / 'victim'
                victim.mkdir()
                (victim / 'keep').write_text('safe')
                key = hashlib.sha256(os.path.normcase(str(target)).encode()).hexdigest()
                journal = root / ('.learnova-txn-' + key + '.json')
                journal.write_text(json.dumps({'schema_version': 1, 'target': 'demo', 'work': '../victim', 'token': 'a' * 32, 'sha256': 'b' * 64}))
                with self.assertRaises(CliError) as exc:
                    with transaction(target, 'c' * 64):
                        self.fail('must reject')
                self.assertEqual(exc.exception.code, 'BUILD_INTEGRITY_FAILED')
                self.assertEqual((victim / 'keep').read_text(), 'safe')
                journal.unlink()
                journal.symlink_to(victim / 'keep')
                with self.assertRaises(CliError):
                    with transaction(target, 'c' * 64):
                        self.fail('must reject')
                self.assertEqual((victim / 'keep').read_text(), 'safe')
        finally:
            sys.path.remove(scripts)

    def test_restored_output_must_match_recorded_original_digest(self):
        import hashlib
        scripts = str(CLI.parent)
        sys.path.insert(0, scripts)
        try:
            from build.transactions import transaction, fingerprint, _original_hash
            from core.errors import CliError
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target = root / 'demo'
                target.mkdir()
                (target / 'SKILL.md').write_text('old')
                original_hash = _original_hash(target)
                (target / 'SKILL.md').write_text('modified outside recovery')
                work = root / '.learnova-staging-synthetic'
                work.mkdir()
                token = 'a' * 32
                (work / '.learnova-owner.json').write_text(json.dumps({'target': 'demo', 'token': token}))
                key = hashlib.sha256(os.path.normcase(str(target)).encode()).hexdigest()
                journal = root / ('.learnova-txn-' + key + '.json')
                journal.write_text(json.dumps({'schema_version': 1, 'target': 'demo', 'work': work.name, 'token': token, 'sha256': fingerprint({'SKILL.md': b'new'}), 'recovery_sha256': original_hash}))
                with self.assertRaises(CliError) as exc:
                    with transaction(target, 'b' * 64):
                        self.fail('must reject changed output')
                self.assertEqual(exc.exception.code, 'BUILD_INTEGRITY_FAILED')
                self.assertEqual((target / 'SKILL.md').read_text(), 'modified outside recovery')
                self.assertTrue(journal.exists())
                self.assertTrue(work.exists())
        finally:
            sys.path.remove(scripts)

    def test_original_tree_digest_tracks_directories_and_links_without_following(self):
        scripts = str(CLI.parent)
        sys.path.insert(0, scripts)
        try:
            from build.transactions import _original_hash
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                baseline = _original_hash(root)
                (root / 'empty').mkdir()
                self.assertNotEqual(_original_hash(root), baseline)
                (root / 'link').symlink_to('missing-target')
                with_link = _original_hash(root)
                (root / 'link').unlink()
                (root / 'link').symlink_to('other-target')
                self.assertNotEqual(_original_hash(root), with_link)
        finally:
            sys.path.remove(scripts)

    def test_output_transaction_lock_serializes_processes_and_survives_crash(self):
        if os.name == 'nt':
            self.skipTest('POSIX process termination test')
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'demo'
            env = {**os.environ, 'PYTHONPATH': str(CLI.parent)}
            code = "import sys,time; from build.transactions import transaction;\nwith transaction(sys.argv[1], 'a'*64):\n print('locked', flush=True); time.sleep(60)"
            holder = subprocess.Popen([sys.executable, '-c', code, str(target)], env=env, stdout=subprocess.PIPE, text=True)
            waiting = None
            try:
                self.assertEqual(holder.stdout.readline().strip(), 'locked')
                waiting = subprocess.Popen([sys.executable, '-c', code, str(target)], env=env, stdout=subprocess.PIPE, text=True)
                with self.assertRaises(subprocess.TimeoutExpired):
                    waiting.wait(timeout=0.3)
                holder.kill()
                holder.wait(timeout=5)
                self.assertEqual(waiting.stdout.readline().strip(), 'locked')
            finally:
                if waiting is not None:
                    waiting.kill()
                    waiting.communicate(timeout=5)
                if holder.poll() is None:
                    holder.kill()
                holder.communicate(timeout=5)
