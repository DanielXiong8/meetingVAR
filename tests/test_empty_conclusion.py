"""No-evidence answers must be explicit conclusions, never empty bullet shells."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
with patch('meetingvar.env.load_local_env'):
    from meetingvar.agent.loop import AgentLoop


class EmptyConclusionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='mv-empty-conclusion-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def loop(self, question='我之前说过这句话吗'):
        return AgentLoop(project_root=self.root, data_dir=self.root / 'data',
                         question=question, llm=lambda *a, **k: {})

    def test_no_observations_cannot_establish_absence(self):
        final = self.loop()._best_effort_final()
        self.assertFalse(final['answered'])
        self.assertEqual(final['answer_status'], 'failed')
        self.assertNotIn('没有找到', final['answer'])
        self.assertFalse(final['coverage']['confirmed_absence'])
        self.assertNotIn('你说过这句话', final['answer'])
        self.assertNotIn('还不是完整核对后的结论', final['answer'])
        self.assertNotIn('\n- \n', final['answer'] + '\n')

    def test_read_file_text_is_used_instead_of_negative_template(self):
        loop = self.loop('二分排序有几个版本ppt里面')
        loop.observations = [{
            'tool': 'read_file', 'arguments': {'path': '14.排序.pdf'},
            'value': {'text': '插入排序\n快速排序：LUG版\n快速排序：LGU版\n快速划分：DUP版\n二分查找'},
            'step': 1,
        }]
        final = loop._best_effort_final()
        self.assertIn('快速排序', loop._evidence_set().render())
        self.assertIn('二分查找', loop._evidence_set().render())
        self.assertNotIn('没有找到足够依据', final['answer'])

    def test_json_only_evidence_is_not_rendered_as_empty_bullets(self):
        loop = self.loop()
        loop.observations = [{
            'tool': 'search_sessions', 'arguments': {'query': '测试'},
            'value': {'results': [{'matches': []}]},
            'text': '[{"session_id": "s1", "matches": []}]', 'step': 1,
        }]
        final = loop._best_effort_final()
        self.assertEqual(final['answer_status'], 'failed')
        self.assertNotIn('没有找到', final['answer'])
        self.assertNotIn('我先从已查到的证据里整理出这些要点', final['answer'])

    def test_readable_evidence_still_produces_points(self):
        loop = self.loop()
        loop.observations = [{
            'tool': 'search_sessions', 'arguments': {'query': '测试'},
            'value': {'results': [{'matches': [{'text': '原话：项目周五交付。'}]}]},
            'text': '', 'step': 1,
        }]
        final = loop._best_effort_final()
        self.assertIn('项目周五交付', loop._evidence_set().render())
        self.assertEqual(final['answer_status'], 'partial')

    def test_cleaner_keeps_negative_conclusion(self):
        loop = self.loop()
        final = loop._best_effort_final()
        self.assertEqual(final['answer_status'], 'failed')
        self.assertTrue(final['answer'])


if __name__ == '__main__':
    unittest.main()