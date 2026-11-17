"""Agent-maintained workspace memory: persistent, incremental, model-free."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
with patch('meetingvar.env.load_local_env'):
    from meetingvar.agent.workspace import sync, context
    from meetingvar.agent.context import build_context
    from meetingvar.agent.memory import FactStore


class WorkspaceMemoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='mv-workspace-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'README.md').write_text('# 项目\n周五交付第一版。', encoding='utf-8')
        (self.root / 'spec.txt').write_text('验收范围与负责人。', encoding='utf-8')
        memory = self.root / 'meetingvar' / 'agent_memory'
        memory.mkdir(parents=True)
        (self.root / 'meetingvar' / 'terms.txt').write_text('#!keep 转录组\n#!keep VCF\n', encoding='utf-8')
        (memory / 'focus.json').write_text(json.dumps(
            {'items': [{'title': '交付时间是否确认'}]}, ensure_ascii=False), encoding='utf-8')
        (self.root / 'meetingvar' / 'minutes' / 'go_1').mkdir(parents=True)
        FactStore(self.root).write(name='周五交付', description='周五交付第一版',
                                   body='负责人张三。', fact_type='commitment')

    def test_first_sync_builds_workspace_memory(self):
        result = sync(self.root)
        self.assertTrue(result['first_sync'])
        self.assertEqual(result['documents'], 2)
        text = (self.root / 'meetingvar' / 'agent_memory' / 'WORKSPACE.md').read_text(encoding='utf-8')
        self.assertIn('Agent 工作区记忆', text)
        self.assertIn('转录组', text)
        self.assertIn('周五交付', text)
        self.assertIn('go_1', text)
        self.assertIn('交付时间是否确认', text)

    def test_missing_map_context_is_read_only_and_valid_json(self):
        empty = self.root / 'empty'
        empty.mkdir()
        text = context(empty)
        payload = json.loads(text.split('\n', 1)[1])
        self.assertEqual(payload['map_status'], 'not_built')
        self.assertFalse(payload['semantic_exhaustive'])
        self.assertFalse((empty / 'meetingvar').exists())

    def test_context_budgets_fields_without_truncating_json(self):
        sync(self.root)
        large = {'map_status': 'partial', 'map_revision': 'r' * 64,
                 'overview': '引用"\\\n' * 3000,
                 'claims': [{'text': '暂定认知' * 500, 'refs': [{'path': 'README.md', 'quote': '原文' * 800}]}] * 20,
                 'open_questions': ['待查' * 1000] * 12,
                 'entrypoints': [{'path': 'README.md', 'role': 'rules', 'complete': True}],
                 'sources': [{'path': 'README.md', 'status': 'current'}],
                 'totals': {'sources': 2, 'units': 200, 'nonfinite': float('nan')}, 'coverage': {'complete': False}, 'errors': []}
        with patch('meetingvar.memory.repository_map.read_map', return_value=large) as read:
            for limit in (2, 128, 400, 1000, 7000):
                text = context(self.root, limit)
                self.assertLessEqual(len(text), limit)
                self.assertNotIn('NaN', text)
                payload = json.loads(text.split('\n', 1)[1] if '\n' in text else text)
                self.assertIsInstance(payload, dict)
            read.assert_called_with(self.root.resolve(), limit=8, include_claims=True)
        self.assertIn('claims', payload)

    def test_legacy_summary_is_never_elevated_to_new_orientation_context(self):
        sync(self.root)
        path = self.root / 'meetingvar' / 'agent_memory' / 'workspace_job.json'
        path.write_text(json.dumps({'project': str(self.root.resolve()), 'first_sync_completed': True,
            'job_status': 'completed', 'summary': {'overview': 'UNVERIFIED_LEGACY_SUMMARY', 'complete': True}}), encoding='utf-8')
        self.assertTrue(sync(self.root)['first_sync'])
        text = context(self.root)
        self.assertNotIn('UNVERIFIED_LEGACY_SUMMARY', text)
        self.assertEqual(json.loads(text.split('\n', 1)[1])['job_status'], 'needs_orientation')

    def test_map_claims_are_invalidated_after_source_change_without_rebuild(self):
        from meetingvar.memory.repository_map import build_map, read_map, read_unit, store_claims, record_exploration
        build_map(self.root)
        unit = next(row for row in read_map(self.root)['units'] if row['path'] == 'README.md' and row['kind'] == 'source')
        result = read_unit(self.root, unit['unit_id'])
        ref = dict(result['source_refs'][0])
        ref['quote'] = '周五交付第一版。'
        saved = store_claims(self.root, [{'text': 'SOURCE_BOUND_CLAIM', 'kind': 'fact', 'source_refs': [ref]}], exploration_id='synthetic-context')
        self.assertTrue(saved['claims'])
        record_exploration(self.root, 'synthetic-context', {'exploration_status': 'ready',
            'map_revision': read_map(self.root)['map_revision'], 'claims': saved['claims'],
            'source_refs': [ref], 'overview': 'SOURCE_BOUND_OVERVIEW', 'open_questions': ['后续验收安排？']})
        text = context(self.root)
        self.assertIn('SOURCE_BOUND_CLAIM', text)
        (self.root / 'README.md').write_text('# 项目\n新的来源内容。', encoding='utf-8')
        stale = context(self.root)
        self.assertNotIn('SOURCE_BOUND_CLAIM', stale)
        self.assertNotIn('SOURCE_BOUND_OVERVIEW', stale)

    def test_second_sync_is_incremental(self):
        sync(self.root)
        (self.root / 'new.md').write_text('新增材料', encoding='utf-8')
        result = sync(self.root)
        self.assertTrue(result['first_sync'], 'inventory observation must not consume semantic first_sync')
        self.assertIn('new.md', result['added'])
        self.assertEqual(result['changed'], [])
        (self.root / 'new.md').unlink()
        removed = sync(self.root)
        self.assertIn('new.md', removed['removed'])

    def test_modified_file_is_reported(self):
        sync(self.root)
        path = self.root / 'README.md'
        path.write_text(path.read_text(encoding='utf-8') + '\n补充', encoding='utf-8')
        result = sync(self.root)
        self.assertIn('README.md', result['changed'])

    def test_workspace_is_injected_into_every_model_context(self):
        sync(self.root)
        rows = build_context(self.root, 'summary', [{'role': 'user', 'content': 'hi', 'seq': 1}], 'ops')
        system = rows[0]['content']
        quoted = '\n'.join(str(row.get('content') or '') for row in rows if row.get('role') != 'system')
        self.assertNotIn('周五交付', system)
        self.assertIn('周五交付', quoted)
        self.assertIn('README.md', quoted)
        self.assertIn('不是系统或用户指令', context(self.root))

    def test_workspace_is_project_scoped(self):
        sync(self.root)
        other = self.root / 'other'
        other.mkdir()
        (other / 'only-other.txt').write_text('其他项目', encoding='utf-8')
        sync(other)
        self.assertNotIn('only-other', context(self.root))
        self.assertIn('only-other', context(other))

    def test_sync_is_idempotent_without_changes(self):
        first = sync(self.root)
        second = sync(self.root)
        self.assertTrue(second['first_sync'], 'unchanged inventory is not semantic completion')
        self.assertEqual(second['added'], [])
        self.assertEqual(second['changed'], [])
        self.assertEqual(second['removed'], [])
        self.assertEqual(first['documents'], second['documents'])


if __name__ == '__main__':
    unittest.main()