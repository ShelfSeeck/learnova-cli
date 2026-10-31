import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'skills/learnova-cli/scripts'))
from core import document, chunking, skill_build
from core.errors import CliError


class BuildCoreTest(unittest.TestCase):
    def test_structure_and_ranges(self):
        text = '# 标题\r\n\r\n```python\r\n# not heading\r\n```\r\n\r\n| a | b |\r\n| --- | --- |\r\n| x | y |\r\n\r\n$$\r\nx\r\n$$\r\n\r\n    # code\r\n\r\n尾声\r\n'
        structure = document.scan(text)
        self.assertEqual([h['title'] for h in structure['headings']], ['标题'])
        self.assertEqual([u['kind'] for u in structure['protected']], ['fence', 'table', 'math', 'indented_code'])
        for u in structure['protected']:
            self.assertTrue(text[u['start']:u['end']])
        cuts = [b['id'] for b in structure['boundaries'][1:-1]]
        chunks = chunking.split(text, structure, cuts)
        self.assertEqual(''.join(text[c['start']:c['end']] for c in chunks), text)
        self.assertEqual(chunks[-1]['start_line'], document.line_number(text, chunks[-1]['start']))
        self.assertIsNotNone(document.read_range(text, 0, len(text), 2)['next_start'])
        self.assertEqual(len(document.representative(text, 0, len(text), 2)), 3)

    def test_empty_headerless_and_invalid_cuts(self):
        for text in ('', 'one\n\ntwo'):
            structure = document.scan(text)
            self.assertTrue(chunking.validate_coverage(text, chunking.split(text, structure, [])))
        text = 'one\n\ntwo\n\nthree'
        structure = document.scan(text)
        ids = [b['id'] for b in structure['boundaries'][1:-1]]
        for cuts in ([ids[0], ids[0]], ids[::-1], ['bad']):
            with self.assertRaises(CliError) as cm:
                chunking.split(text, structure, cuts)
            self.assertEqual((cm.exception.code, cm.exception.exit_code), ('BUILD_PLAN_INVALID', 2))
        with self.assertRaises(CliError):
            chunking.split(text, structure, [ids[0]], protected=[{'start': 0, 'end': len(text)}])
        with self.assertRaises(CliError):
            chunking.validate_coverage(text, [chunking.make_chunk(text, 1, len(text))])

    def test_refinement_and_merging(self):
        text = 'one\n\ntwo\n\nthree'
        structure = document.scan(text)
        root = chunking.make_chunk(text, 0, len(text))
        ids = [b['id'] for b in structure['boundaries'][1:-1]]
        self.assertEqual(chunking.refine(text, structure, root, [], 1, 2)[0]['warnings'], ['oversized:no_cut'])
        self.assertEqual(chunking.refine(text, structure, root, ids, 1, 0)[0]['warnings'], ['oversized:max_depth'])
        children = chunking.refine(text, structure, root, ids, 1, 2)
        self.assertTrue(all(c['end'] - c['start'] < len(text) and c['parent_id'] == root['id'] for c in children))
        merged = chunking.merge_small(text, children, 9, 100, structure=structure, protected=(), barriers=[children[1]['start']])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]['end'], children[1]['start'])
        atom = '```\n' + 'x' * 40 + '\n```\n'
        self.assertEqual(chunking.refine(atom, document.scan(atom), chunking.make_chunk(atom, 0, len(atom)), [], 1, 2)[0]['warnings'], ['oversized:atomic'])

    def test_code_images_are_not_active(self):
        text = '```md\n![example](missing.png)\n```\n\n`![example](missing.png)`\n'
        chunks = chunking.split(text, document.scan(text), [])
        metadata = {'name': 'guide', 'description': 'Guide', 'chunks': {chunks[0]['id']: {'title': 'Code', 'summary': 'Examples', 'keywords': ['code']}}}
        self.assertTrue(skill_build.validate_generation(text, chunks, skill_build.render(text, chunks, metadata)))

    def test_image_forms_and_destination_offsets(self):
        text = ('![one](images/a.png) ![Two][PHOTO] ![photo][] ![photo]\n'
                '<img alt="x" src="images/b.png">\n\n[photo]: <images/b.png> "title"\n')
        images = skill_build.extract_images(text)
        self.assertEqual([item['kind'] for item in images].count('reference'), 3)
        self.assertEqual({item['destination'] for item in images}, {'images/a.png', 'images/b.png'})
        for item in images:
            self.assertEqual(text[item['start']:item['end']], item['destination'])
            self.assertTrue(text[item['usage_start']:item['usage_end']].startswith(('![', '<img')))
        chunks = chunking.split(text, document.scan(text), [])
        meta = {'name': 'guide', 'description': 'Guide', 'chunks': {chunks[0]['id']: {'title': 'Images', 'summary': 'See images', 'keywords': ['images']}}}
        files = skill_build.render(text, chunks, meta, {'images/a.png': b'a', 'images/b.png': b'b'})
        self.assertEqual(files['references/' + chunks[0]['id'] + '.md'], text)

    def test_image_reference_failures_and_unsafe_destinations(self):
        for text in ('![x][missing]', '![x][]', '![missing]', '<img src="missing.png">',
                     '<img alt="no src">', '![x](../secret.png)', '<img src="javascript:alert(1)">'):
            with self.subTest(text=text):
                chunks = chunking.split(text, document.scan(text), [])
                meta = {'name': 'guide', 'description': 'Guide', 'chunks': {chunks[0]['id']: {'title': 'X', 'summary': 'X', 'keywords': ['x']}}}
                with self.assertRaises(CliError) as cm:
                    skill_build.render(text, chunks, meta)
                self.assertEqual(cm.exception.code, 'BUILD_INTEGRITY_FAILED')

    def test_code_mask_and_reference_definitions(self):
        text = ('~~~md\n![x][missing]\n<img src="missing.png">\n~~~\n\n'
                '    ![x](missing.png)\n\n`` ![x][missing] ``\n\n'
                '![x][ Ref  Name ]\n\n[ref name]: images/a.png\n')
        images = skill_build.extract_images(text)
        self.assertEqual([(i['kind'], i['destination']) for i in images], [('reference', 'images/a.png')])

    def test_merge_respects_heading_and_protected_barriers(self):
        text = 'Intro\n\n# Chapter\n\nBefore\n\n```\ncode\n```\n\nAfter'
        structure = document.scan(text)
        cuts = [b['id'] for b in structure['boundaries'][1:-1]]
        chunks = chunking.split(text, structure, cuts)
        protected = [{'start': text.index('Before'), 'end': text.index('```')}]
        merged = chunking.merge_small(text, chunks, 100, 100, structure=structure, protected=protected)
        boundaries = {c['start'] for c in merged} | {c['end'] for c in merged}
        self.assertIn(text.index('# Chapter'), boundaries)
        self.assertIn(text.index('Before'), boundaries)
        self.assertIn(text.index('```'), boundaries)
        self.assertIn(structure['protected'][0]['end'], boundaries)
        with self.assertRaises(TypeError):
            chunking.merge_small(text, chunks, 100, 100)

    def test_invalid_decision_shapes_and_minimal_origin(self):
        text = 'one\n\ntwo'
        structure = document.scan(text)
        for cuts in (None, True, 'b0', [None], [[]]):
            with self.subTest(cuts=cuts), self.assertRaises(CliError) as cm:
                chunking.split(text, structure, cuts)
            self.assertEqual(cm.exception.code, 'BUILD_PLAN_INVALID')
        for protected in (None, True, [None], [{'start': True, 'end': 3}], [{'start': [], 'end': 3}]):
            with self.subTest(protected=protected), self.assertRaises(CliError) as cm:
                chunking.split(text, structure, [], protected=protected)
            self.assertEqual(cm.exception.code, 'BUILD_PLAN_INVALID')
        chunks = chunking.split(text, structure, [])
        for meta in (None, True, [], {'name': 'x', 'description': 'x', 'chunks': {chunks[0]['id']: None}}):
            with self.subTest(meta=meta), self.assertRaises(CliError) as cm:
                skill_build.render(text, chunks, meta)
            self.assertEqual(cm.exception.code, 'BUILD_PLAN_INVALID')
        meta = {'name': 'x', 'description': 'x', 'chunks': {chunks[0]['id']: {'title': 'x', 'summary': 'x', 'keywords': ['x']}}}
        files = skill_build.render(text, chunks, meta)
        provenance = json.loads(files['.learnova-build.json'])
        self.assertEqual(provenance, {'schema_version': 2, 'source': 'local-build', 'version': None})
        self.assertNotIn('.learnova-build.json', files['SKILL.md'])

    def test_active_images_escapes_code_and_parentheses(self):
        self.assertEqual(len(skill_build.extract_images(r'\\![x](missing.png)')), 1)
        self.assertEqual(skill_build.extract_images(r'\![x](missing.png)'), [])
        self.assertEqual(len(skill_build.extract_images('`unclosed\n\n![x](missing.png)\n\n`')), 1)
        self.assertEqual(len(skill_build.extract_images('`unclosed\r\n\r\n![x](missing.png)\r\n\r\n`')), 1)
        self.assertEqual(skill_build.extract_images('`` ` ![x](missing.png) ` ``'), [])
        text = '![x](images/a(b).png)'
        self.assertEqual(skill_build.extract_images(text)[0]['destination'], 'images/a(b).png')
        chunks = chunking.split(text, document.scan(text), [])
        meta = {'name': 'x', 'description': 'x', 'chunks': {chunks[0]['id']: {'title': 'x', 'summary': 'x', 'keywords': ['x']}}}
        skill_build.render(text, chunks, meta, {'images/a(b).png': b'image'})
        for missing in (r'\\![x](missing.png)', '`unclosed\n\n![x](missing.png)\n\n`'):
            chunks = chunking.split(missing, document.scan(missing), [])
            meta['chunks'] = {chunks[0]['id']: {'title': 'x', 'summary': 'x', 'keywords': ['x']}}
            with self.assertRaises(CliError):
                skill_build.render(missing, chunks, meta)

    def test_scanner_protection_is_mandatory_at_scope_and_artifact(self):
        text = '```\ncode\n```\n'
        structure = document.scan(text)
        with self.assertRaises(CliError):
            chunking.split(text, structure, [], start=4, end=8)
        chunks = [chunking.make_chunk(text, 0, 4), chunking.make_chunk(text, 4, len(text))]
        meta = {'name': 'x', 'description': 'x', 'chunks': {c['id']: {'title': 'x', 'summary': 'x', 'keywords': ['x']} for c in chunks}}
        with self.assertRaises(CliError) as cm:
            skill_build.render(text, chunks, meta)
        self.assertEqual(cm.exception.code, 'BUILD_INTEGRITY_FAILED')

    def test_bracket_math_and_heading_suffix(self):
        text = 'before\n\n\\[\n# formula\n\ncontinued\n\\]\n\n# C#\n'
        structure = document.scan(text)
        self.assertEqual([h['title'] for h in structure['headings']], ['C#'])
        unit = structure['protected'][0]
        self.assertEqual(unit['kind'], 'math')
        self.assertTrue(all(not unit['start'] < b['offset'] < unit['end'] for b in structure['boundaries']))
        unclosed = document.scan('\\[\n# hidden\n\nlast')
        self.assertEqual(unclosed['headings'], [])
        self.assertEqual(unclosed['protected'][0]['end'], len('\\[\n# hidden\n\nlast'))

    def test_portable_image_paths_and_private_generation_integrity(self):
        for path in ('images/a:b.png', 'images/CON.png', 'images/name.', 'images/trailing /x.png', 'images/a?.png'):
            text = '![x](' + path + ')'
            chunks = chunking.split(text, document.scan(text), [])
            meta = {'name': 'x', 'description': 'x', 'chunks': {chunks[0]['id']: {'title': 'x', 'summary': 'x', 'keywords': ['x']}}}
            with self.subTest(path=path), self.assertRaises(CliError):
                skill_build.render(text, chunks, meta, {path: b'x'})
        text = 'content'
        chunks = chunking.split(text, document.scan(text), [])
        meta = {'name': 'x', 'description': 'x', 'chunks': {chunks[0]['id']: {'title': 'x', 'summary': 'x', 'keywords': ['x']}}}
        original = skill_build.render(text, chunks, meta)
        path = 'references/' + chunks[0]['id'] + '.md'
        edited = dict(original, **{path: 'edited current content'})
        self.assertTrue(skill_build.validate_package(edited))
        with self.assertRaises(CliError):
            skill_build.validate_generation(text, chunks, edited)
        broken = [dict(chunks[0], start_line=99)]
        with self.assertRaises(CliError):
            skill_build.validate_generation(text, broken, original)

    def test_artifacts_and_bad_inputs(self):
        text = '# 中文\n\n![x](images/x.png)\n\nEnd\n'
        structure = document.scan(text)
        chunks = chunking.split(text, structure, [structure['boundaries'][-2]['id']])
        metadata = {'name': 'guide', 'description': 'Quotes: "safe"', 'chunks': {c['id']: {'title': 'Part', 'summary': 'Read this', 'keywords': ['one']} for c in chunks}}
        files = skill_build.render(text, chunks, metadata, {'images/x.png': b'png'})
        provenance = json.loads(files['.learnova-build.json'])
        self.assertEqual(''.join(files['references/' + c['id'] + '.md'] for c in chunks), text)
        self.assertIn('description: "Quotes: \\"safe\\""', files['SKILL.md'])
        self.assertTrue(skill_build.validate_generation(text, chunks, files))
        for change in ({'name': '../bad'}, {'description': 'bad\n---'}):
            with self.assertRaises(CliError):
                skill_build.render(text, chunks, dict(metadata, **change), {'images/x.png': b'png'})
        with self.assertRaises(CliError):
            skill_build.render(text, chunks, metadata, {})
        with self.assertRaises(CliError):
            skill_build.render(text, chunks, metadata, {'../escape': b'x'})


if __name__ == '__main__':
    unittest.main()
