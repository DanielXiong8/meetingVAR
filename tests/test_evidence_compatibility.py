"""Legacy payload compatibility must not weaken observed-source validation."""
import json
import unittest

from meetingvar.agent.contracts import EvidenceSet, _ref, normalize_tool_evidence


class EvidenceCompatibilityTests(unittest.TestCase):
    def evidence(self, tool, value, args=None):
        result = EvidenceSet()
        result.add(normalize_tool_evidence(tool, args or {}, value))
        return result

    def turns(self):
        return self.evidence('get_transcript_window', {
            'session_id': 's1', 'revision': 'v1', 'turns': [
                {'id': 'turn_1', 'text': '周五给初版'},
                {'id': 'turn_2', 'text': '改为下周一给'}]})

    def test_turn_normalization_is_idempotent(self):
        source = _ref({'id': 'turn_1', 'session_id': 's1', 'text': '原文'}, kind='turn')
        self.assertEqual(source['id'], 's1:turn_1')
        for _ in range(4):
            self.assertEqual(_ref(source, kind='turn'), source)
        self.assertEqual(source['turn_ids'], ['turn_1'])

    def test_explicit_source_ref_id_is_preserved(self):
        source = _ref({'source_ref_id': 'canonical', 'session_id': 's1',
                       'turn_ids': ['turn_1']}, kind='turn')
        self.assertEqual(source['id'], 'canonical')
        self.assertEqual(_ref(source, kind='turn'), source)

    def test_legacy_turn_citation_matches_observed_text(self):
        valid, invalid = self.turns().validate_citations([{'kind': 'turn',
            'session_id': 's1', 'turn_ids': ['turn_1'], 'quote': '周五给初版'}])
        self.assertFalse(invalid)
        self.assertEqual(valid[0]['source_ref_id'], 's1:turn_1')
        self.assertFalse(valid[0]['semantic_verified'])

    def test_multiple_turn_ids_all_must_be_observed(self):
        evidence = self.turns()
        valid, invalid = evidence.validate_citations([{'kind': 'turn',
            'session_id': 's1', 'turn_ids': ['turn_1', 'turn_2'], 'quote': '下周一给'}])
        self.assertFalse(invalid)
        self.assertEqual(len(valid[0]['source_ref_ids']), 2)
        valid, invalid = evidence.validate_citations([{'kind': 'turn',
            'session_id': 's1', 'turn_ids': ['turn_1', 'turn_99']}])
        self.assertFalse(valid)
        self.assertEqual(invalid[0]['reason'], 'source_not_observed')

    def test_cross_session_citation_cannot_reuse_turn(self):
        valid, invalid = self.turns().validate_citations([{'source_ref_id': 's1:turn_1',
            'kind': 'turn', 'session_id': 's2', 'turn_ids': ['turn_1']}])
        self.assertFalse(valid)
        self.assertEqual(invalid[0]['reason'], 'source_identity_mismatch')

    def test_direct_turn_id_cannot_cover_unseen_turns(self):
        valid, invalid = self.turns().validate_citations([{'source_ref_id': 's1:turn_1',
            'kind': 'turn', 'session_id': 's1', 'turn_ids': ['turn_1', 'turn_99']}])
        self.assertFalse(valid)
        self.assertTrue(invalid)

    def test_unknown_direct_id_cannot_fallback_to_valid_alias(self):
        valid, invalid = self.turns().validate_citations([{'source_ref_id': 'invented',
            'kind': 'turn', 'session_id': 's1', 'turn_ids': ['turn_1']}])
        self.assertFalse(valid)
        self.assertTrue(invalid)

    def test_forged_turn_quote_is_rejected(self):
        valid, invalid = self.turns().validate_citations([{'kind': 'turn',
            'session_id': 's1', 'turn_ids': ['turn_1'], 'quote': '下周一给'}])
        self.assertFalse(valid)
        self.assertEqual(invalid[0]['reason'], 'quote_mismatch')

    def test_invalid_turn_id_container_is_rejected(self):
        for ids in ('turn_1', [], [None], ['']):
            valid, invalid = self.turns().validate_citations([{'kind': 'turn',
                'session_id': 's1', 'turn_ids': ids}])
            self.assertFalse(valid)
            self.assertEqual(invalid[0]['reason'], 'invalid_turn_ids')

    def test_distinct_revisions_cannot_form_combined_turn_citation(self):
        evidence = self.turns()
        evidence.add(normalize_tool_evidence('get_transcript_window', {}, {
            'session_id': 's1', 'revision': 'v2', 'turns': [{'id': 'turn_3', 'text': '新版'}]}))
        valid, invalid = evidence.validate_citations([{'kind': 'turn',
            'session_id': 's1', 'turn_ids': ['turn_1', 'turn_3']}])
        self.assertFalse(valid)
        self.assertTrue(invalid)

    def test_first_turns_are_source_linked(self):
        evidence = self.evidence('get_session_overview', {'session_id': 's1',
            'first_turns': [{'id': 'turn_1', 'text': '开会'}], 'turns_total': 20})
        valid, invalid = evidence.validate_citations([{'kind': 'turn', 'session_id': 's1',
            'turn_ids': ['turn_1'], 'quote': '开会'}])
        self.assertFalse(invalid)
        self.assertEqual(len(valid), 1)
        self.assertFalse(evidence.coverage()['complete'])

    def test_compare_sessions_keeps_both_identities(self):
        evidence = self.evidence('compare_sessions', {
            'session_a': {'session_id': 'a', 'matches': [{'id': 'turn_1', 'text': '旧安排'}]},
            'session_b': {'session_id': 'b', 'matches': [{'id': 'turn_1', 'text': '新安排'}]}})
        self.assertEqual({s['id'] for s in evidence.sources()}, {'a:turn_1', 'b:turn_1'})

    def test_search_conversation_alias(self):
        evidence = self.evidence('search_conversations', {'conversations': [
            {'conversation_id': 'c1', 'snippet': '讨论第二方案'}]})
        valid, invalid = evidence.validate_citations([{'kind': 'conversation',
            'conversation_id': 'c1', 'quote': '第二方案'}])
        self.assertFalse(invalid)
        self.assertEqual(valid[0]['conversation_id'], 'c1')

    def test_read_conversation_uses_only_observed_messages(self):
        evidence = self.evidence('read_conversation', {'conversation': {
            'conversation_id': 'c1', 'summary': '阶段记录',
            'messages': [{'role': 'user', 'content': '接受第二方案'}]}})
        valid, invalid = evidence.validate_citations([{'kind': 'conversation',
            'conversation_id': 'c1', 'quote': '接受第二方案'}])
        self.assertTrue(valid)
        self.assertFalse(invalid)
        valid, invalid = evidence.validate_citations([{'kind': 'conversation',
            'conversation_id': 'c1', 'quote': '接受第三方案'}])
        self.assertFalse(valid)
        self.assertTrue(invalid)

    def test_bare_fact_body_matches_named_source(self):
        evidence = self.evidence('read_fact', {'name': '决定一', 'body': '先建立基线', 'version': 2})
        valid, invalid = evidence.validate_citations([{'kind': 'fact', 'name': '决定一',
                                                      'quote': '建立基线'}])
        self.assertTrue(valid)
        self.assertFalse(invalid)

    def test_node_and_edge_aliases_are_retained(self):
        evidence = self.evidence('search_graph', {'nodes': [{'id': 'n1', 'name': '任务'}],
                                                   'edges': [{'id': 'e1', 'quote': '负责初版'}]})
        valid, invalid = evidence.validate_citations([{'kind': 'node', 'node_id': 'n1'},
                                                       {'kind': 'edge', 'edge_id': 'e1'}])
        self.assertEqual(len(valid), 2)
        self.assertFalse(invalid)

    def test_operation_cannot_be_relabelled_fact(self):
        evidence = self.evidence('query_operations', {'operations': [{'id': 'op1', 'status': 'completed'}]})
        valid, invalid = evidence.validate_citations([{'source_ref_id': 'op1', 'kind': 'fact'}])
        self.assertFalse(valid)
        self.assertEqual(invalid[0]['reason'], 'source_kind_mismatch')

    def test_quote_without_observed_text_is_not_validated(self):
        evidence = self.evidence('search_graph', {'nodes': [{'id': 'n1', 'name': '任务'}]})
        valid, invalid = evidence.validate_citations([{'node_id': 'n1', 'quote': '周五完成'}])
        self.assertFalse(valid)
        self.assertEqual(invalid[0]['reason'], 'quote_mismatch')

    def test_file_quote_uses_full_returned_window_not_preview(self):
        text = 'a' * 500 + '可核验尾部'
        evidence = self.evidence('read_file', {'path': 'a.txt', 'revision': 'v1', 'text': text,
            'source_refs': [{'id': 'f1', 'kind': 'file', 'path': 'a.txt', 'revision': 'v1',
                             'line_start': 1, 'line_end': 1, 'quote': text[:400]}]})
        valid, invalid = evidence.validate_citations([{'source_ref_id': 'f1', 'quote': '可核验尾部'}])
        self.assertTrue(valid)
        self.assertFalse(invalid)
        self.assertEqual(len(evidence.sources()), 1)
        item = evidence.to_dict()['evidence'][0]['items'][0]
        self.assertNotIn('source_refs', item)
        self.assertNotIn('coverage', item)

    def test_end_only_and_noninteger_ranges_are_rejected(self):
        evidence = self.evidence('read_file', {'text': '正文', 'path': 'a.txt',
            'revision': 'v1', 'line_start': 2, 'line_end': 3})
        identity = evidence.sources()[0]['id']
        for fields in ({'line_end': 100}, {'line_start': '2'}, {'line_start': True},
                       {'line_start': 3, 'line_end': 2}):
            valid, invalid = evidence.validate_citations([{'source_ref_id': identity, **fields}])
            self.assertFalse(valid)
            self.assertEqual(invalid[0]['reason'], 'source_range_mismatch')

    def test_quote_must_belong_to_cited_line_not_just_file(self):
        evidence = self.evidence('read_file', {'text': '第一行\n第二行\n第三行',
            'path': 'a.txt', 'revision': 'v1', 'line_start': 1, 'line_end': 3})
        identity = evidence.sources()[0]['id']
        valid, invalid = evidence.validate_citations([{'source_ref_id': identity,
            'line_start': 2, 'line_end': 2, 'quote': '第二行'}])
        self.assertTrue(valid)
        self.assertFalse(invalid)
        valid, invalid = evidence.validate_citations([{'source_ref_id': identity,
            'line_start': 1, 'line_end': 1, 'quote': '第二行'}])
        self.assertFalse(valid)
        self.assertEqual(invalid[0]['reason'], 'quote_mismatch')

    def test_flattened_page_window_does_not_prove_single_page_quote(self):
        evidence = self.evidence('read_file', {'text': '来自第二页的内容',
            'path': 'a.pdf', 'revision': 'v1', 'page_start': 1, 'page_end': 3})
        identity = evidence.sources()[0]['id']
        valid, invalid = evidence.validate_citations([{'source_ref_id': identity,
            'page_start': 1, 'page_end': 1, 'quote': '来自第二页的内容'}])
        self.assertFalse(valid)
        self.assertTrue(invalid)

    def test_wrong_revision_is_rejected(self):
        valid, invalid = self.turns().validate_citations([{'source_ref_id': 's1:turn_1', 'revision': 'v2'}])
        self.assertFalse(valid)
        self.assertEqual(invalid[0]['reason'], 'source_revision_mismatch')

    def test_multiple_source_versions_require_explicit_revision(self):
        evidence = self.turns()
        evidence.add(normalize_tool_evidence('get_transcript_window', {}, {
            'session_id': 's1', 'revision': 'v2', 'turns': [{'id': 'turn_1', 'text': '新版安排'}]}))
        self.assertEqual(len(evidence.sources()), 3)
        valid, invalid = evidence.validate_citations([{'source_ref_id': 's1:turn_1'}])
        self.assertFalse(valid)
        self.assertEqual(invalid[0]['reason'], 'source_ambiguous')
        valid, invalid = evidence.validate_citations([{'source_ref_id': 's1:turn_1',
            'revision': 'v2', 'quote': '新版安排'}])
        self.assertTrue(valid)
        self.assertFalse(invalid)

    def test_conflicting_canonical_ids_are_rejected(self):
        valid, invalid = self.turns().validate_citations([{'source_ref_id': 's1:turn_1',
            'id': 's1:turn_2'}])
        self.assertFalse(valid)
        self.assertEqual(invalid[0]['reason'], 'source_identity_mismatch')

    def test_alias_only_graph_sources_have_refs(self):
        evidence = self.evidence('search_graph', {'nodes': [{'node_id': 'n1'}],
                                                   'edges': [{'edge_id': 'e1'}]})
        valid, invalid = evidence.validate_citations([{'node_id': 'n1'}, {'edge_id': 'e1'}])
        self.assertEqual(len(valid), 2)
        self.assertFalse(invalid)

    def test_partial_and_shortfall_never_establish_absence(self):
        for update in ({'status': 'partial'}, {'coverage': {'total': 2, 'processed': 1,
                       'complete': True, 'exhaustive': True}},
                       {'coverage': {'failed': 1, 'complete': True, 'exhaustive': True}}):
            payload = {'hits': [], 'coverage': {'complete': True, 'exhaustive': True}, **update}
            evidence = self.evidence('search_files', payload)
            self.assertFalse(evidence.coverage()['confirmed_absence'])


if __name__ == '__main__':
    unittest.main()
