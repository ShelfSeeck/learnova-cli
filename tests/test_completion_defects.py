"""Regression coverage for source isolation and interrupted operations."""
import os
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import zipfile
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/learnova-cli/scripts'))
import agents
import engine
import store
import auth
import bootstrap
import publishing
from build.pipeline import BuildService
from build.decisions import Decision
from cli.commands import upgrade
from core.errors import CliError
from core.skill import read_install_metadata, write_install_metadata
from sources.mirror import MirrorSource
from sources.backend import BackendSource, _safe_path


def catalog(name='demo', version='1.0.0'):
    return {'schema_version': 1, 'generated_at': 1, 'skills': [{
        'dir': name, 'name': name, 'description': 'Synthetic fixture', 'display_name': name,
        'tags': [], 'likes': 0, 'downloads': 0, 'latest_version': version,
        'versions': [{'version': version, 'commit': 'abcdef12', 'created_at': 1}],
    }]}


class CompletionDefectsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for attr, path in [('CONFIG_PATH', 'config.json'), ('INDEX_CACHE_PATH', 'cache.json'),
                           ('LEARNOVA_HOME', 'home')]:
            patcher = patch.object(store, attr, self.root / path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_mirror_switch_refreshes_and_cannot_use_other_source_offline(self):
        old, new = 'https://example.invalid/mirror-a', 'https://example.invalid/mirror-b'
        store.set_config_value('mirror_repo_url', old)
        store.save_index_cache(catalog('only-in-a'))
        store.set_config_value('mirror_repo_url', new)
        ctx = SimpleNamespace(config=store.load_config(), offline=True)
        with patch('net.request') as request, self.assertRaises(CliError) as caught:
            MirrorSource(ctx).search('', [], 'popular')
        self.assertEqual(caught.exception.code, 'OFFLINE_CACHE_MISS')
        request.assert_not_called()
        ctx.offline = False
        with patch('net.request', return_value=catalog('only-in-b')) as request:
            result = MirrorSource(ctx).search('', [], 'popular')
        self.assertEqual([s['dir'] for s in result['skills']], ['only-in-b'])
        self.assertEqual(request.call_args.args[1], new + '/-/raw/main/index.json')
        ctx.offline = True
        with patch('net.request') as request:
            self.assertEqual(MirrorSource(ctx).search('', [], 'popular')['total'], 1)
        request.assert_not_called()

    def test_legacy_cache_only_matches_default_source(self):
        store._write_json(store.INDEX_CACHE_PATH, {'fetched_at': 1, 'index': catalog()})
        self.assertIsNotNone(store.load_index_cache(store.DEFAULT_MIRROR_REPO_URL)[0])
        self.assertIsNone(store.load_index_cache('https://example.invalid/other')[0])

    def test_upgrade_failure_rolls_back_all_agent_copies(self):
        targets = [agents.AgentTarget(self.root / agent / 'skills', (agent,))
                   for agent in ('claude', 'codex')]
        for target in targets:
            final = target.path / 'demo'
            final.mkdir(parents=True)
            (final / 'SKILL.md').write_text('---\nname: demo\n---\nOld contents\n')
            write_install_metadata(final, 'demo', '1.0.0')
        def fetch(name, version, dest):
            package = dest / name
            package.mkdir()
            (package / 'SKILL.md').write_text('---\nname: demo\n---\nNew contents\n')
            write_install_metadata(package, name, version)
            return package
        source = SimpleNamespace(name='fixture', search=lambda *a: catalog(version='2.0.0'), fetch=fetch)
        original = engine.os.replace
        def fail_second(source, destination):
            if Path(destination) == targets[1].path / 'demo' and Path(source).name == 'candidate':
                raise OSError('synthetic commit failure')
            return original(source, destination)
        ctx = SimpleNamespace(selected_agents=lambda: None, cwd=self.root)
        args = SimpleNamespace(skill='demo', all=False, project=False)
        with patch.object(agents, 'management_targets', return_value=targets), \
             patch.object(upgrade.resolver, 'resolve', return_value=source), \
             patch.object(engine.os, 'replace', side_effect=fail_second), self.assertRaises(OSError):
            upgrade.handler(args, ctx)
        for target in targets:
            final = target.path / 'demo'
            self.assertEqual(read_install_metadata(final)['version'], '1.0.0')
            self.assertIn('Old contents', (final / 'SKILL.md').read_text())
            self.assertFalse(list(target.path.glob('.learnova-staging-*')))

    def test_auto_run_limit_returns_persisted_current_state(self):
        source = self.root / 'input.md'
        source.write_text('# Topic\n\nSynthetic content.\n')
        service = BuildService(self.root, decision=SimpleNamespace(decide=lambda *a: {'splits': []}))
        with patch.dict(os.environ, {'SYNTHETIC_MODEL_KEY': 'fixture-value'}):
            task = service.create(source, mode='auto')['task_id']
            service.reconfigure(task, {'llm_base_url': 'https://example.invalid/v1',
                                      'llm_model': 'fixture', 'llm_key_env': 'SYNTHETIC_MODEL_KEY'})
            returned = service.run(task, max_steps=1)
        self.assertEqual(returned, service.status(task))
        self.assertEqual(returned['status'], 'pending')
        self.assertIsNone(returned['request'])

    def test_interactive_prompts_do_not_contaminate_json_stdout(self):
        ctx = SimpleNamespace(config=dict(store.DEFAULTS, backend_url='https://example.invalid'),
                              auth={}, cwd=self.root)
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(auth, 'terminal_available', return_value=True), \
             patch.object(auth.sys, 'stdin', io.StringIO('fixture@example.invalid\n')), \
             patch.object(auth.getpass, 'getpass', return_value='synthetic-fixture'), \
             patch.object(auth, 'login', return_value={'username': 'fixture'}), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertTrue(auth.login_interactive(ctx)['logged_in'])
        self.assertEqual(output.getvalue(), '')
        self.assertIn('邮箱', errors.getvalue())
        output, errors = io.StringIO(), io.StringIO()
        target = agents.AgentTarget(self.root / '.agents/skills', ('codex',))
        with patch.object(auth, 'terminal_available', return_value=True), \
             patch.object(auth.sys, 'stdin', io.StringIO('y\n')), \
             patch.object(agents, 'detect', return_value=['codex']), \
             patch.object(agents, 'resolve_targets', return_value=[target]), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(bootstrap.initialize(ctx)['status'], 'completed')
        self.assertEqual(output.getvalue(), '')
        self.assertIn('确认保存', errors.getvalue())

    def test_reasoning_model_truncation_has_one_bounded_retry(self):
        truncated = {'choices': [{'message': {'content': ''}, 'finish_reason': 'length'}]}
        complete = {'choices': [{'message': {'content': json.dumps({'result': {'splits': []}})},
                                  'finish_reason': 'stop'}]}
        decision = Decision(lambda *args: {})
        cfg = {'llm_key_env': 'SYNTHETIC_MODEL_KEY', 'llm_base_url': 'https://example.invalid',
               'llm_model': 'fixture'}
        with patch.dict(os.environ, {'SYNTHETIC_MODEL_KEY': 'fixture-value'}), \
             patch('net.request', side_effect=[truncated, complete]) as request:
            self.assertEqual(decision.decide({'kind': 'split_plan'}, cfg), {'splits': []})
        self.assertEqual(request.call_count, 2)
        self.assertLess(request.call_args_list[0].args[2]['max_tokens'],
                        request.call_args_list[1].args[2]['max_tokens'])
        self.assertLessEqual(request.call_args_list[1].args[2]['max_tokens'], 8192)
        with patch.dict(os.environ, {'SYNTHETIC_MODEL_KEY': 'fixture-value'}), \
             patch('net.request', return_value=truncated) as request, self.assertRaises(CliError) as caught:
            decision.decide({'kind': 'split_plan'}, cfg)
        self.assertEqual(caught.exception.code, 'BUILD_MODEL_RESPONSE_INVALID')
        self.assertEqual(request.call_count, 2)

    def test_backend_file_paths_fail_as_package_errors_before_io(self):
        for path in ['references/\ud800.md', *('references/a' + char + '.md' for char in '?*|<>"')]:
            with self.subTest(path=repr(path)), self.assertRaises(CliError) as caught:
                _safe_path(path)
            self.assertEqual(caught.exception.code, 'SKILL_PACKAGE_INVALID')
        source = BackendSource(SimpleNamespace())
        listing = {'path': '.', 'submission_id': None, 'entries': [
            {'name': '.learnova-install.json', 'path': '.learnova-install.json', 'is_dir': True},
        ]}
        with patch.object(source, '_get', return_value=listing), self.assertRaises(CliError) as caught:
            source._files('/community/skills/fixture/versions/fixture')
        self.assertEqual(caught.exception.code, 'SKILL_PACKAGE_INVALID')

    def test_batch_decision_keeps_the_managed_workflow_output_budget(self):
        expected = {'chunks': {'part': {'title': 'Topic', 'summary': 'Summary', 'keywords': ['sample']}}}
        def model(method, url, body, **kwargs):
            if body['max_tokens'] < 8192:
                return {'choices': [{'finish_reason': 'length', 'message': {'content': ''}}]}
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'result': expected})}}]}
        config = {'llm_key_env': 'SYNTHETIC_MODEL_KEY', 'llm_base_url': 'https://example.invalid', 'llm_model': 'fixture'}
        with patch.dict(os.environ, {'SYNTHETIC_MODEL_KEY': 'fixture-value'}), patch('net.request', side_effect=model) as request:
            self.assertEqual(Decision(lambda *a: {}).decide({'kind': 'chunk_metadata_batch'}, config), expected)
        self.assertEqual(request.call_count, 1)

    def test_publish_preserves_valid_yaml_comments_and_excludes_hidden_credentials(self):
        folder = self.root / 'skill'
        folder.mkdir()
        text = ('---\nname: sample # valid comment\ndescription: "Quoted # description" # comment\n'
                'metadata:\n  category: example\n---\nPublic reference.\n')
        (folder / 'SKILL.md').write_text(text, newline='\n')
        for path in ('.docker/config.json', '.kube/config', '.azure/accessTokens.json',
                     'tokens.json', 'secrets.yaml', 'service_account.json', 'certificate.p12'):
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('synthetic-private-state')
        content, meta = publishing.package(folder)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertEqual(archive.namelist(), ['SKILL.md'])
            self.assertEqual(archive.read('SKILL.md'), text.encode())
        self.assertEqual(meta['name'], 'sample')
        self.assertEqual(meta['description'], 'Quoted # description')

    def test_build_package_also_excludes_hidden_credentials(self):
        source = self.root / 'input.md'
        source.write_text('# Public\n\nSynthetic reference.\n')
        service = BuildService(self.root)
        task = service.create(source)['task_id']
        for _ in range(12):
            state = service.next(task)
            if state['status'] == 'completed':
                break
            req = state['request']
            answer = ({'splits': []} if req['kind'] == 'split_plan' else
                      {'title': 'Topic', 'summary': 'Summary', 'keywords': ['topic']}
                      if req['kind'] == 'chunk_metadata' else {'name': 'sample', 'description': 'Public reference'})
            service.submit(task, {**req, 'result': answer})
        self.assertEqual(state['status'], 'completed')
        output = Path(state['result_path'])
        for relative in ('.docker/config.json', '.kube/config', '.azure/accessTokens.json',
                         'tokens.json', 'secrets.yaml', 'service_account.json', 'certificate.p12'):
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('synthetic-private-state')
        with zipfile.ZipFile(service.package(task)['package']) as archive:
            self.assertFalse(any(name.startswith('.') for name in archive.namelist()))
            self.assertNotIn(b'synthetic-private-state', b''.join(archive.read(name) for name in archive.namelist()))

    def test_published_nested_yaml_round_trips_through_mirror_install(self):
        for number, version_field in enumerate(('', 'version: "1.0.0" # published release\n')):
            with self.subTest(top_level_version=bool(version_field)):
                folder = self.root / ('published-' + str(number))
                folder.mkdir()
                original = ('---\nname: sample\ndescription: Public reference\n' + version_field
                            + 'metadata:\n  version: "internal-tag"\n  name: nested-name\n---\nContent.\n')
                (folder / 'SKILL.md').write_text(original, newline='\n')
                archive, _ = publishing.package(folder)
                data = io.BytesIO()
                with zipfile.ZipFile(io.BytesIO(archive)) as zipped, tarfile.open(fileobj=data, mode='w:gz') as tar:
                    for name in zipped.namelist():
                        content = zipped.read(name)
                        member = tarfile.TarInfo('repository/sample/' + name)
                        member.size = len(content)
                        tar.addfile(member, io.BytesIO(content))
                source = MirrorSource(SimpleNamespace(config={'mirror_repo_url': 'https://example.invalid/mirror'}, offline=False))
                with patch.object(store, 'load_index_cache', return_value=(catalog('sample'), False)), \
                     patch('net.download', side_effect=lambda url, dest: dest.write_bytes(data.getvalue())):
                    fetched = source.fetch('sample', '1.0.0', self.root / ('download-' + str(number)))
                target = agents.AgentTarget(self.root / ('installed-' + str(number)), ('codex',))
                result = engine.install(fetched, [target], 'sample', False)
                self.assertEqual((result[0].name, result[0].version), ('sample', '1.0.0'))
                self.assertEqual((target.path / 'sample/SKILL.md').read_text(), original)


if __name__ == '__main__':
    unittest.main()
