import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/learnova-cli/scripts'))
import agents
import engine
from core import chunking, skill_build
from core.errors import CliError
from core.skill import write_install_metadata
from cli.commands import upgrade


class ManagementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.candidate = self.root / 'candidate'
        self.candidate.mkdir()
        (self.candidate / 'SKILL.md').write_text('---\nname: demo\n---\n')

    def test_versionless_commit_conflict_and_symlink(self):
        final = self.root / 'skills/demo'
        engine.commit_directories(self.candidate, [final])
        self.assertTrue((final / 'SKILL.md').exists())
        with self.assertRaises(CliError):
            engine.commit_directories(self.candidate, [final])
        link = self.root / 'link'
        link.symlink_to(final.parent, target_is_directory=True)
        with self.assertRaises(CliError):
            engine.commit_directories(self.candidate, [link / 'demo'], True)
        with self.assertRaises(CliError):
            engine.commit_directories(self.candidate, [self.candidate / 'bad'])

    def test_stage_failure_cleans_and_commit_failure_rolls_back(self):
        a, b = self.root / 'a/demo', self.root / 'b/demo'
        for path in (a, b):
            path.mkdir(parents=True)
            (path / 'old').write_text('old')
        original_copy = engine.shutil.copytree
        def fail_copy(src, dst):
            if Path(dst).parent.parent == b.parent:
                raise OSError('stage')
            return original_copy(src, dst)
        with patch.object(engine.shutil, 'copytree', side_effect=fail_copy), self.assertRaises(OSError):
            engine.commit_directories(self.candidate, [a, b], True)
        self.assertEqual((a / 'old').read_text(), 'old')
        self.assertFalse(list(a.parent.glob('.learnova-staging-*')))
        original_replace = engine.os.replace
        def fail_replace(src, dst):
            if Path(dst) == b and Path(src).name == 'candidate':
                raise OSError('commit')
            return original_replace(src, dst)
        with patch.object(engine.os, 'replace', side_effect=fail_replace), self.assertRaises(OSError):
            engine.commit_directories(self.candidate, [a, b], True)
        self.assertEqual((a / 'old').read_text(), 'old')
        self.assertEqual((b / 'old').read_text(), 'old')
        self.assertFalse(list(b.parent.glob('.learnova-staging-*')))

    def test_rollback_failure_does_not_block_other_targets(self):
        a, b, c = (self.root / name / 'demo' for name in ('a', 'b', 'c'))
        for final in (a, b, c):
            final.mkdir(parents=True)
            (final / 'old').write_text('old')
        original_replace = engine.os.replace

        def fail_replace(src, dst):
            if Path(dst) == c and Path(src).name == 'candidate':
                raise OSError('commit failure')
            if Path(dst) == c and Path(src).name == 'backup':
                raise OSError('restore failure')
            return original_replace(src, dst)

        with patch.object(engine.os, 'replace', side_effect=fail_replace):
            with self.assertRaises(CliError) as caught:
                engine.commit_directories(self.candidate, [a, b, c], True)
        self.assertEqual(caught.exception.code, 'DIRECTORY_ROLLBACK_FAILED')
        self.assertIn('commit failure', caught.exception.message)
        self.assertIn('restore failure', caught.exception.message)
        self.assertEqual(str(caught.exception.__cause__), 'commit failure')
        for final in (a, b):
            self.assertEqual((final / 'old').read_text(), 'old')
            self.assertFalse(list(final.parent.glob('.learnova-staging-*')))
        backups = list(c.parent.glob('.learnova-staging-*/backup/old'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), 'old')
        self.assertIn(str(backups[0].parent), caught.exception.message)

    def build(self, root):
        text = 'Example text\n'
        chunks = [chunking.make_chunk(text, 0, len(text))]
        meta = {'name': 'demo', 'description': 'Guide', 'chunks': {chunks[0]['id']: {'title': 'Part', 'summary': 'Summary', 'keywords': ['x']}}}
        for name, value in skill_build.render(text, chunks, meta).items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value if isinstance(value, bytes) else value.encode())

    def test_scan_provenance_and_canonical_dedup(self):
        root = self.root / '.agents/skills/demo'
        self.build(root)
        targets = agents.management_targets(['claude'], True, self.root, self.root)
        self.assertEqual(engine.scan_installed(targets)[0].source, 'local-build')
        self.assertEqual(engine.scan_installed(targets)[0].agent, 'project')
        targets = agents.management_targets(['codex', 'opencode'], True, self.root, self.root)
        self.assertEqual(len(targets), 1)
        write_install_metadata(root, 'demo', '1.0')
        self.assertEqual(engine.scan_installed(targets)[0].source, 'community')
        write_install_metadata(root, 'other', '1.0')
        self.assertEqual(engine.scan_installed(targets)[0].source, 'local-build')
        (root / '.learnova-build.json').write_text('{}')
        self.assertEqual(engine.scan_installed(targets)[0].source, 'unknown')
        self.assertEqual(len(agents.management_targets([], True, self.root, self.root)), 1)

    def test_origin_and_installed_version_survive_edits_without_content_scan(self):
        root = self.root / '.agents/skills/demo'
        self.build(root)
        targets = agents.management_targets(['codex'], True, self.root, self.root)
        (root / 'SKILL.md').write_text('---\nname: edited\ndescription: Edited locally\nversion: 99.0\n---\n')
        (root / 'extra.bin').write_bytes(b'\xff\x00')
        for ref in (root / 'references').glob('*.md'):
            ref.unlink()
        original_read = Path.read_text
        def only_records(path, *args, **kwargs):
            self.assertIn(path.name, ('SKILL.md', '.learnova-build.json', '.learnova-install.json'))
            return original_read(path, *args, **kwargs)
        with patch.object(Path, 'rglob', side_effect=AssertionError('no recursive scan')), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('no content hashing')), \
             patch.object(Path, 'read_text', only_records):
            local = engine.scan_installed(targets)[0]
            self.assertEqual((local.source, local.version), ('local-build', ''))
            legacy = {'schema_version': 1, 'source': 'local-build', 'references': [{'path': 'missing'}],
                      'source_sha256': 'historical', 'document_sha256': 'historical', 'images': []}
            (root / '.learnova-build.json').write_text(json.dumps(legacy))
            self.assertEqual(engine.scan_installed(targets)[0].source, 'local-build')
            self.assertEqual(json.loads(original_read(root / '.learnova-build.json')), legacy)
            write_install_metadata(root, 'demo', '1.0')
            installed = engine.scan_installed(targets)[0]
            self.assertEqual((installed.source, installed.version), ('community', '1.0'))

    def test_mixed_upgrade_and_unknown_skip(self):
        local = self.root / '.agents/skills/demo'
        self.build(local)
        community = self.root / '.claude/skills/demo'
        community.mkdir(parents=True)
        (community / 'SKILL.md').write_text('---\nname: demo\n---\n')
        write_install_metadata(community, 'demo', '1.0')
        ctx = SimpleNamespace(cwd=self.root, selected_agents=lambda: ['claude'])
        args = SimpleNamespace(skill='demo', all=False, project=True)
        source = SimpleNamespace(name='test', search=lambda *a: {'skills': [{'dir': 'demo', 'name': 'demo', 'latest_version': '2.0'}]})
        with patch.object(upgrade.resolver, 'resolve', return_value=source), patch.object(engine, 'fetch_and_install') as install:
            result = upgrade.handler(args, ctx)
        self.assertEqual(install.call_args.args[3][0].path, community.parent)
        self.assertEqual(result['skipped'][0]['reason'], '来源为 local-build')
        (community / '.learnova-install.json').unlink()
        with patch.object(upgrade.resolver, 'resolve') as resolve:
            result = upgrade.handler(args, ctx)
        resolve.assert_not_called()
        self.assertEqual({x['reason'] for x in result['skipped']}, {'来源为 unknown', '来源为 local-build'})


if __name__ == '__main__':
    unittest.main()
