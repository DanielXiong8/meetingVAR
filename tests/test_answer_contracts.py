"""Contract invariants; not a substitute for real model answer evaluation."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from meetingvar.agent.contracts import EvidenceSet,make_answer_result,normalize_tool_evidence


class EvidenceContracts(unittest.TestCase):
    def test_project_status_count_is_not_a_fact_list(self):
        result=normalize_tool_evidence('project_status',{}, {'facts':3,'project_overview':'研究目标',
            'recent_facts':[{'name':'决定一','description':'开展基线验证'}]})
        self.assertEqual(result['status'],'ok')
        self.assertIn('研究目标',json.dumps(result,ensure_ascii=False))
        self.assertTrue(any(item.get('counts',{}).get('facts')==3 for item in result['items']))
    def test_rate_limit_without_observations_is_not_absence(self):
        result=make_answer_result('本轮受限，未完成检索',status='no_evidence',
                                 errors=[{'code':'rate_limit','message':'限流'}],coverage={})
        self.assertEqual(result['answer_status'],'failed')
        self.assertFalse(result['answered'])
    def test_no_observations_never_proves_absence(self):
        evidence=EvidenceSet()
        self.assertFalse(evidence.coverage()['confirmed_absence'])
        self.assertEqual(make_answer_result(status='no_evidence',coverage=evidence.coverage())['answer_status'],'failed')
    def test_only_successful_exhaustive_search_proves_scoped_absence(self):
        evidence=EvidenceSet()
        evidence.add(normalize_tool_evidence('search_files',{'query':'term'},
            {'hits':[],'coverage':{'scope':'one-file-v1','complete':True,'exhaustive':True,'processed':1,'total':1}}))
        self.assertTrue(evidence.coverage()['confirmed_absence'])
        answer=make_answer_result('该文件v1中未找到',status='no_evidence',coverage=evidence.coverage())
        self.assertEqual(answer['answer_status'],'no_evidence')
    def test_top_k_empty_is_not_exhaustive(self):
        evidence=EvidenceSet()
        evidence.add(normalize_tool_evidence('search_project_knowledge',{'query':'term','limit':5},{'chunks':[]}))
        self.assertFalse(evidence.coverage()['confirmed_absence'])
    def test_partial_source_cannot_become_complete(self):
        row=normalize_tool_evidence('read_file',{'path':'a.pdf'}, {'text':'partial',
            'errors':[{'code':'ocr_timeout'}], 'coverage':{'complete':True,'processed':2,'total':3}})
        self.assertEqual(row['status'],'partial')
        self.assertFalse(row['coverage']['complete'])
    def test_source_validity_is_not_semantic_verification(self):
        answer=make_answer_result('某项结论',status='answered',source_validated=True)
        self.assertTrue(answer['source_validated'])
        self.assertFalse(answer['semantic_verified'])
        self.assertFalse(answer['grounded'])
    def test_render_does_not_mutate_or_lose_persistent_evidence(self):
        evidence=EvidenceSet()
        evidence.add(normalize_tool_evidence('read_file',{'path':'a.txt'},
            {'text':'正文'*10000,'path':'a.txt','revision':'v1'},operation_id='op1'))
        original=json.dumps(evidence.to_dict(),ensure_ascii=False)
        display=evidence.render(max_chars=2000)
        self.assertLessEqual(len(display),2000)
        self.assertIn('op1',display)
        self.assertEqual(original,json.dumps(evidence.to_dict(),ensure_ascii=False))
    def test_direct_source_id_cannot_forge_range_or_quote(self):
        evidence=EvidenceSet()
        evidence.add(normalize_tool_evidence('read_file',{'path':'a.pdf'}, {
            'items':[{'text':'original evidence'}],
            'source_refs':[{'id':'src1','kind':'file','path':'a.pdf','revision':'v1',
                'page_start':2,'page_end':3,'quote':'original evidence'}],
            'coverage':{'scope':'a.pdf pages2-3','complete':True}}))
        good,bad=evidence.validate_citations([{'source_ref_id':'src1','revision':'v1','page_start':7,'page_end':7}])
        self.assertFalse(good);self.assertTrue(bad)
        good,bad=evidence.validate_citations([{'source_ref_id':'src1','revision':'v1','quote':'invented'}])
        self.assertFalse(good);self.assertTrue(bad)
    def test_file_source_preserves_page_and_revision(self):
        evidence=EvidenceSet()
        evidence.add_observation({'tool':'read_file','arguments':{'path':'a.pdf'},'value':{
            'text':'expected content','path':'a.pdf','revision':'v1','page_start':2,'page_end':3}})
        source=evidence.sources()[0]
        valid,invalid=evidence.validate_citations([{'source_ref_id':source['id'],'revision':'v1','page_start':2,'page_end':3}])
        self.assertFalse(invalid);self.assertEqual(valid[0]['page_start'],2)
        self.assertFalse(valid[0]['semantic_verified'])
    def test_turn_ids_from_distinct_meetings_do_not_collide(self):
        evidence=EvidenceSet()
        for sid in ['meeting-a','meeting-b']:
            evidence.add(normalize_tool_evidence('get_transcript_window',{'session_id':sid},
                         {'session_id':sid,'turns':[{'id':'turn_1','text':sid}]}))
        self.assertEqual(len(evidence.sources()),2)
    def test_more_than_twelve_sources_not_silently_discarded(self):
        evidence=EvidenceSet()
        evidence.add(normalize_tool_evidence('search_files',{}, {'source_refs':[
            {'id':str(i),'path':f'{i}.txt','kind':'file','revision':'v1'} for i in range(20)]}))
        valid,invalid=evidence.validate_citations([{'source_ref_id':str(i)} for i in range(20)])
        self.assertEqual(len(valid),20);self.assertFalse(invalid)

if __name__=='__main__':unittest.main()
