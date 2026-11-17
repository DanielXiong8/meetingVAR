"""Chinese context budgeting and compact operation receipts."""
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
with patch('meetingvar.env.load_local_env'):
    from meetingvar.llm import _messages_bytes
    from meetingvar.agent.operations import OperationStore
    from meetingvar.agent.loop import AgentLoop


class ContextBudgetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='mv-context-budget-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_chinese_text_is_counted_as_tokens_not_utf8_bytes(self):
        messages = [{'role': 'user', 'content': '中' * 8000}]
        estimate = _messages_bytes(messages)
        self.assertLess(estimate, 9000)
        self.assertGreater(estimate, 7000)

    def test_tool_read_receipt_is_compact_in_context(self):
        store = OperationStore(self.root)
        operation = store.create('tool.search_project_knowledge',
                                 {'query': '二分查找', 'limit': 20}, thread_id='t')
        store.claim(operation['id'])
        store.finish(operation['id'], {
            'ok': True,
            'value': {'chunks': [{'text': '很长的正文' * 2000}]},
        })
        context = store.recent_context('t')
        self.assertIn('tool.search_project_knowledge', context)
        self.assertNotIn('很长的正文' * 10, context)
        self.assertLess(len(context), 4000)

    def test_rag_chunks_can_form_a_fallback_answer(self):
        loop = AgentLoop(project_root=self.root, data_dir=self.root / 'data',
                         question='PDF里面有什么', llm=lambda *a, **k: {})
        loop.observations = [{
            'tool': 'search_project_knowledge', 'arguments': {'query': '目录'},
            'value': {'chunks': [
                {'text': '目录\n算法概论\n向量\n列表\n栈与队列'},
                {'text': '排序：插入、希尔、快速、归并'},
            ]},
            'step': 1,
        }]
        final = loop._best_effort_final()
        self.assertEqual(final['answer_status'], 'partial')
        self.assertIn('排序', loop._evidence_set().render())
        self.assertNotIn('没有找到足够依据', final['answer'])

    def test_fit_messages_uses_token_budget_not_bytes(self):
        class Budget:
            input_limit = 16000

        loop = AgentLoop(project_root=self.root, data_dir=self.root / 'data',
                         question='问题', llm=lambda *a, **k: {})
        messages = [
            {'role': 'system', 'content': '规则说明' * 500},
            {'role': 'user', 'content': '中文问题' * 2500},
        ]
        with patch.object(loop.model_client, 'budget', return_value=Budget()):
            fitted = loop._fit_messages(messages, current_turn=len(messages)-1)
        self.assertEqual(len(fitted), 2)
        self.assertEqual(len(fitted[1]['content']), len('中文问题' * 2500))
        self.assertIn('规则说明', fitted[0]['content'])

    def test_continuation_previews_are_bounded(self):
        loop = AgentLoop(project_root=self.root, data_dir=self.root / 'data',
                         question='排序有几种版本', llm=lambda *a, **k: {})
        loop.observations = [{
            'tool': 'read_file', 'operation_id': str(index),
            'value': {'text': '很长的正文' * 5000}, 'step': index,
        } for index in range(8)]
        messages = loop._continuation_messages('input_budget_exhausted')
        payload = json.dumps(messages, ensure_ascii=False)
        self.assertLess(len(payload), 16000)
        self.assertIn('source_refs', payload)
        self.assertIn('coverage', payload)
        self.assertNotIn('很长的正文' * 400, payload)

    def test_fit_messages_respects_safety_margin(self):
        class Budget:
            input_limit = 22000

        loop = AgentLoop(project_root=self.root, data_dir=self.root / 'data',
                         question='问题', llm=lambda *a, **k: {})
        messages = [
            {'role': 'system', 'content': '系统规则' * 6000},
            {'role': 'user', 'content': '历史消息' * 6000},
            {'role': 'user', 'content': '当前问题'},
        ]
        with patch.object(loop.model_client, 'budget', return_value=Budget()):
            fitted = loop._fit_messages(messages, current_turn=len(messages)-1)
        self.assertFalse(loop._context_fit.ok)
        self.assertEqual(loop._context_fit.error_code, 'input_too_large')
        self.assertEqual(fitted[0]['content'], messages[0]['content'])
        self.assertEqual(fitted[-1]['content'], '当前问题')

    def test_failed_loop_never_starts_hidden_evidence_synthesis(self):
        replies = [{'ok': True, 'content': '排序算法共有 6 类：插入、选择、堆、归并、快速、希尔。',
                    'message': {'role': 'assistant', 'content': 'x'}, 'tool_calls': []}]

        def fake_llm(messages, **kwargs):
            return replies.pop(0)

        loop = AgentLoop(project_root=self.root, data_dir=self.root / 'data',
                         question='排序算法有几种版本', llm=fake_llm)
        loop.observations = [{
            'tool': 'search_project_knowledge', 'arguments': {'query': '排序'},
            'value': {'chunks': [{'text': '插入排序\n选择排序\n堆排序\n归并排序\n快速排序\n希尔排序'}]},
            'step': 1,
        }]
        final = loop._fallback_final()
        self.assertEqual(final['answer_status'], 'partial')
        self.assertEqual(len(replies), 1)
        self.assertFalse(final['answered'])
        self.assertNotIn('我查到的相关原文如下', final['answer'])

    def test_partial_fallback_preserves_evidence_without_forcing_count(self):
        loop = AgentLoop(project_root=self.root, data_dir=self.root / 'data',
                         question='排序算法有几种版本',
                         llm=lambda *a, **k: {'ok': False, 'error_code': 'network'})
        loop.observations = [{
            'tool': 'search_project_knowledge', 'arguments': {'query': '排序'},
            'value': {'chunks': [{'text': '插入排序\n选择排序\n快速排序'}]},
            'step': 1,
        }]
        final = loop._fallback_final()
        self.assertIn('插入排序', loop._evidence_set().render())
        self.assertEqual(final['answer_status'], 'partial')
        self.assertNotIn('直接计数', final['answer'])


if __name__ == '__main__':
    unittest.main()