"""Unified runtime contracts; temp projects and injected models only."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
with patch('meetingvar.env.load_local_env'):
    from meetingvar.agent.chat import ChatAgent, ChatStore
    from meetingvar.agent.context import build_context
    from meetingvar.agent.loop import AgentLoop
    from meetingvar.agent.operations import OperationStore
    from meetingvar.agent.sessions import SessionCatalog
    from meetingvar.agent.tools import ToolContext, ToolRegistry, ToolSpec
    from meetingvar.llm import chat_messages_stream


class RuntimeContracts(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='mv-runtime-contract-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.env = patch.dict('os.environ', {'APPDATA': str(self.root / 'appdata')})
        self.env.start()
        self.addCleanup(self.env.stop)

    def agent(self):
        return ChatAgent(self.root, data_dir=self.root / 'data', llm=lambda _: 'summary')

    def proposal(self, agent, kind='write_file', payload=None):
        action = {'id': 'a' * 32, 'type': kind, 'label': '待确认操作',
                  'payload': payload or {'path': 'draft.txt', 'content': 'actual content'}, 'status': 'pending'}
        agent.store.add_actions(agent.conversation_id, [action])
        return action

    def test_message_sequences_survive_old_cap_and_summary_keeps_advancing(self):
        agent = self.agent()
        for index in range(270):
            agent.store.append(agent.conversation_id, {'role': 'user', 'content': 'message ' + str(index)})
        with patch.object(agent, '_llm_summary', return_value=None):
            agent._roll_summary()
        first = agent.store.load(agent.conversation_id)
        self.assertEqual(len(first['messages']), 270)
        self.assertGreater(first['summary_until_seq'], 240)
        for index in range(30):
            agent.store.append(agent.conversation_id, {'role': 'user', 'content': 'later ' + str(index)})
        with patch.object(agent, '_llm_summary', return_value=None):
            agent._roll_summary()
        second = agent.store.load(agent.conversation_id)
        self.assertGreater(second['summary_until_seq'], first['summary_until_seq'])
        self.assertEqual(second['messages'][-1]['seq'], 300)

    def test_pending_is_not_generated_and_completed_file_is_verified(self):
        agent = self.agent()
        action = self.proposal(agent)
        before = agent.operations.get(action['id'])
        self.assertEqual(before['status'], 'awaiting_approval')
        self.assertEqual(before['artifacts'], [])
        result = agent.execute_action(action['id'])
        self.assertTrue(result['ok'])
        self.assertEqual(agent.operations.get(action['id'])['status'], 'succeeded')
        self.assertFalse(agent.execute_action(action['id'])['ok'])
        (self.root / 'draft.txt').unlink()
        restarted = ChatAgent(self.root, conversation_id=agent.conversation_id)
        context_rows = restarted._context()
        self.assertIn('missing', json.dumps(context_rows, ensure_ascii=False))
        self.assertIn(str(self.root), context_rows[0]['content'])

    def test_failed_write_is_a_failed_receipt_not_a_success_message(self):
        agent = self.agent()
        action = self.proposal(agent)
        with patch('meetingvar.agent.file_ops.write_text_file', side_effect=ValueError('write failed')):
            result = agent.execute_action(action['id'])
        self.assertFalse(result['ok'])
        self.assertEqual(agent.operations.get(action['id'])['status'], 'failed')
        self.assertEqual(agent.operations.artifacts(action['id']), [])

    def test_changed_approval_payload_is_rejected(self):
        agent = self.agent()
        action = self.proposal(agent)
        saved = agent.store.load(agent.conversation_id)
        saved['pending_actions'][0]['payload']['content'] = 'changed after proposal'
        agent.store._path(agent.conversation_id).write_text(json.dumps(saved), encoding='utf-8')
        result = agent.execute_action(action['id'])
        self.assertFalse(result['ok'])
        self.assertFalse((self.root / 'draft.txt').exists())

    def test_ui_only_requires_real_final_receipt(self):
        agent = self.agent()
        action = self.proposal(agent, kind='start_meeting', payload={'project': str(self.root)})
        accepted = agent.execute_action(action['id'])
        self.assertTrue(accepted['ui_only'])
        self.assertEqual(agent.operations.get(action['id'])['status'], 'running')
        self.assertFalse(agent.execute_action(action['id'])['ok'])
        done = agent.finalize_action(action['id'], {'ok': False, 'error': 'microphone unavailable'})
        self.assertFalse(done['ok'])
        self.assertEqual(agent.operations.get(action['id'])['status'], 'failed')
        self.assertFalse(agent.finalize_action(action['id'], {'ok': True})['ok'])

    def test_all_agent_requests_share_the_same_execution_path(self):
        for question in ['生成的文件在本地哪个路径', '现在生成', '继续改刚才那个', '任意未预设措辞']:
            agent = self.agent()
            agent.store.append(agent.conversation_id, {'role': 'assistant', 'content': 'prior-context-marker'})
            with patch('meetingvar.agent.chat.AgentLoop') as cls:
                cls.return_value.run.return_value = {'answer': 'result', 'status': 'completed', 'ui_actions': []}
                agent.ask(question, run_id='run-test')
                call = cls.call_args.kwargs
                self.assertIn('prior-context-marker', json.dumps(call['history']))
                self.assertEqual(call['run_id'], 'run-test')
                self.assertEqual(call['thread_id'], agent.conversation_id)

    def test_chat_mode_does_not_create_or_call_agent_tools(self):
        agent = self.agent()
        with patch('meetingvar.agent.chat.AgentLoop') as cls:
            answer = agent.ask('自由聊天', mode='chat')
        cls.assert_not_called()
        self.assertEqual(answer['message']['mode'], 'chat')

    def test_planner_executor_and_continuation_keep_shared_context(self):
        loop = AgentLoop(project_root=self.root, data_dir=self.root / 'data', question='continue',
                         history=[{'role': 'system', 'content': 'pinned-memory'},
                                  {'role': 'user', 'content': 'prior-goal'}], thread_id='thread')
        self.assertIn('pinned-memory', json.dumps(loop._initial_messages()))
        loop.observations = [{'tool': 'read_file', 'arguments': {'path': str(i)},
                              'value': {'text': 'observation-' + str(i)}, 'text': 'value', 'step': i}
                             for i in range(45)]
        content = json.dumps(loop._continuation_messages('budget'))
        self.assertIn('observation-44', content)
        self.assertIn('pinned-memory', content)
        loop.task = loop.runtime.create('test')
        loop.ui_actions = [{'type': 'write_file', 'action_id': 'b' * 32, 'payload': {'path': 'a.txt'}}]
        loop._save_state('waiting_approval', 'action_pending')
        state = loop._load_state(loop.task.task_id)
        self.assertEqual(len(state['observations']), 45)
        self.assertEqual(state['ui_actions'], loop.ui_actions)

    def test_registry_result_is_persisted_beyond_presentation_truncation(self):
        registry = ToolRegistry()
        registry.register(ToolSpec(name='sample', description='sample',
                                  input_schema={'type': 'object'}, max_output_chars=10),
                          lambda args, context: {'payload': 'x' * 1000})
        result = registry.invoke('sample', {}, context=ToolContext(project_root=self.root,
            metadata={'thread_id': 'thread', 'run_id': 'run', 'tool_call_id': 'call-zero'}))
        self.assertTrue(result.truncated)
        row = OperationStore(self.root).get(result.operation_id)
        self.assertEqual(len(row['result']['value']['payload']), 1000)
        self.assertEqual(row['tool_call_id'], 'call-zero')

    def test_scoped_catalog_rejects_other_project_session(self):
        pool = self.root / 'data' / 'pool' / 'foreign'
        pool.mkdir(parents=True)
        (pool / 'transcript.json').write_text(json.dumps({'transcript': [{'text': 'foreign evidence'}]}), encoding='utf-8')
        catalog = SessionCatalog(self.root / 'data', project_root=self.root)
        self.assertEqual(catalog.list_sessions(), [])
        with self.assertRaises(ValueError):
            catalog.load('foreign')

    def test_shared_context_includes_summary_and_receipts_with_recent_tail(self):
        rows = [{'seq': i + 1, 'role': 'user', 'content': 'message'} for i in range(40)]
        context = build_context(self.root, 'persistent-summary', rows, 'receipt-marker')
        self.assertIn('persistent-summary', json.dumps(context, ensure_ascii=False))
        self.assertNotIn('persistent-summary', context[0]['content'])
        self.assertIn('receipt-marker', json.dumps(context, ensure_ascii=False))
        self.assertNotIn('receipt-marker', context[0]['content'])
        self.assertIn('seq=40', context[-1]['content'])

    def test_sse_zero_index_fragments_keep_identity_and_stream_content(self):
        chunks = [
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call-zero', 'function': {'name': 'read_file', 'arguments': '{'}}]}}]},
            {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"path":"a.txt"}'}}]}}]},
            {'choices': [{'delta': {'content': 'visible', 'reasoning_content': 'private-reasoning'}, 'finish_reason': 'tool_calls'}]}]
        lines = [('data: ' + json.dumps(chunk)).encode() for chunk in chunks] + [b'data: [DONE]']
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def __iter__(self): return iter(lines)
        profile = {'endpoint': 'https://example.invalid/v1', 'model': 'fake',
                   'context_tokens': 16000, 'max_output_tokens': 2048, 'thinking': ''}
        deltas = []
        with patch('meetingvar.llm.client_profile', return_value=profile), \
             patch('meetingvar.llm.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = Response()
            result = chat_messages_stream([{'role': 'user', 'content': 'test'}],
                on_delta=lambda kind, text: deltas.append((kind, text)))
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['tool_calls']), 1)
        self.assertEqual(result['tool_calls'][0]['id'], 'call-zero')
        self.assertEqual(json.loads(result['tool_calls'][0]['function']['arguments']), {'path': 'a.txt'})
        self.assertEqual(deltas, [('content', 'visible')])
        self.assertNotIn('private-reasoning', str(result))


if __name__ == '__main__':
    unittest.main()
