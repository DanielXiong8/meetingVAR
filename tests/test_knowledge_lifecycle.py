"""Knowledge lifecycle regressions; no production data or real model calls."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
with patch('meetingvar.env.load_local_env'):
    from meetingvar.rag import RagIndex
    from meetingvar.kg.store import GraphStore
    from meetingvar.kg.build import GraphBuilder
    from meetingvar.agent.memory import FactStore
    from meetingvar.memory.project_sessions import session_ids_for_project


class NoEmbedding:
    available=False
    name='disabled-test'
    def encode(self,texts): return None
    def encode_one(self,text): return None


class KnowledgeLifecycleTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='mv-knowledge-contract-')
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        env=patch.dict(os.environ,{'APPDATA':str(self.root/'appdata')})
        env.start();self.addCleanup(env.stop)
        embedding=patch('meetingvar.rag.get_embedding_provider',return_value=NoEmbedding())
        embedding.start();self.addCleanup(embedding.stop)

    def seed_document(self):
        path=self.root/'source.txt'
        path.write_text('alpha source evidence '+('unique project context '*12),encoding='utf-8')
        index=RagIndex(self.root,self.root/'data')
        index.index_project(include_meetings=False)
        with index._connection() as db:
            row=db.execute('SELECT id FROM chunks LIMIT 1').fetchone()
        self.assertIsNotNone(row)
        return path,index,row[0]

    def test_missing_source_is_not_returned_and_is_removed_on_index(self):
        path,index,chunk=self.seed_document()
        path.unlink()
        self.assertIsNone(index.read_chunk(chunk))
        index.index_project(include_meetings=False)
        self.assertEqual(index.search('alpha',rerank=False),[])

    def test_source_revision_changes_chunk_identity(self):
        path,index,old=self.seed_document()
        path.write_text('beta revised evidence '+('new project text '*14),encoding='utf-8')
        index.index_project(include_meetings=False)
        self.assertIsNone(index.read_chunk(old))
        with index._connection() as db:
            latest=db.execute('SELECT id FROM chunks LIMIT 1').fetchone()[0]
        self.assertNotEqual(old,latest)
        self.assertTrue(index.read_chunk(latest)['source_revision'])

    def test_empty_source_removes_previous_chunks(self):
        path,index,chunk=self.seed_document()
        path.write_text('',encoding='utf-8')
        index.index_project(include_meetings=False)
        self.assertIsNone(index.read_chunk(chunk))

    def test_short_utf8_source_is_searchable_and_touch_keeps_identity(self):
        path = self.root / '需求.md'
        text = '客户要求增加数据导出功能，负责人张三。'
        self.assertLess(len(text), 20)
        path.write_text(text, encoding='utf-8')
        index = RagIndex(self.root, self.root / 'data')
        report = index.index_project(include_meetings=False)
        self.assertEqual(report['documents'], 1)
        hits = index.search('客户要求', rerank=False)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['text'], text)
        chunk = hits[0]['chunk_id']
        self.assertEqual(index.read_chunk(chunk)['text'], text)
        info = path.stat()
        os.utime(path, (info.st_atime, info.st_mtime + 10))
        self.assertIsNone(index.read_chunk(chunk))
        self.assertEqual(index.index_project(include_meetings=False)['skipped'], 1)
        self.assertEqual(index.search('客户要求', rerank=False)[0]['chunk_id'], chunk)
        self.assertEqual(index.read_chunk(chunk)['text'], text)

    def test_empty_and_whitespace_sources_clear_all_index_tables(self):
        for blank in ('', '   '):
            with self.subTest(blank=repr(blank)):
                path, index, chunk = self.seed_document()
                path.write_text(blank, encoding='utf-8')
                index.index_project(include_meetings=False)
                self.assertIsNone(index.read_chunk(chunk))
                with index._connection() as db:
                    for table in ('documents', 'chunks', 'chunks_fts', 'chunk_vectors'):
                        self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)

    def test_new_exclusion_hides_previously_indexed_document(self):
        path, index, chunk = self.seed_document()
        (self.root / '.meetingvarignore').write_text(path.name, encoding='utf-8')
        self.assertEqual(index.search('alpha', rerank=False), [])
        self.assertIsNone(index.read_chunk(chunk))
        with index._connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM documents').fetchone()[0], 1)
        self.assertTrue(path.is_file())

    def test_legacy_credential_source_is_not_recalled(self):
        path, index, chunk = self.seed_document()
        target = self.root / 'keys' / path.name
        target.parent.mkdir()
        path.rename(target)
        with index._connection() as db:
            db.execute('UPDATE documents SET path=?', (target.relative_to(self.root).as_posix(),))
        self.assertEqual(index.search('alpha', rerank=False), [])
        self.assertIsNone(index.read_chunk(chunk))
        self.assertTrue(target.is_file())

    def test_unreadable_nonempty_source_keeps_existing_index(self):
        path, index, chunk = self.seed_document()
        with patch('meetingvar.rag.read_text_safely', return_value=None):
            index.index_project(include_meetings=False)
        self.assertIsNotNone(index.read_chunk(chunk))
        self.assertEqual(index.search('alpha', rerank=False)[0]['chunk_id'], chunk)

    def test_human_merge_survives_automatic_upsert_and_rebuild(self):
        facts = FactStore(self.root)
        facts.write(name='事项', description='交付第一版', body='负责人林工',
                    fact_type='action-item', metadata={'owner': '林工'})
        builder = GraphBuilder(self.root, self.root / 'data')
        builder.build(include_meetings=False)
        graph = GraphStore(self.root)
        source = graph.resolve('林工', kind='Person')
        target = graph.upsert_node(kind='Person', name='小林')
        graph.merge_nodes(source['id'], target['id'])
        for reset in (False, True):
            with self.subTest(reset=reset):
                builder.build(include_meetings=False, reset=reset)
                self.assertEqual(graph.get_node(source['id'])['status'], 'merged')
                self.assertEqual(graph.resolve('林工', kind='Person')['id'], target['id'])
                self.assertIn('林工', graph.get_node(target['id'])['aliases'])
                action = graph.resolve('交付第一版')
                assigned = [edge for edge in graph.neighbors(action['id'])['edges']
                            if edge['relation'] == 'assigned_to']
                self.assertTrue(assigned)
                self.assertEqual({edge['dst_id'] for edge in assigned}, {target['id']})

    def test_reviewed_edge_and_deleted_node_survive_builder(self):
        facts = FactStore(self.root)
        facts.write(name='事项', description='交付第一版', body='周五交付',
                    fact_type='action-item', metadata={'due': '周五'})
        builder = GraphBuilder(self.root, self.root / 'data')
        builder.build(include_meetings=False)
        graph = GraphStore(self.root)
        action = graph.resolve('交付第一版')
        edge = next(edge for edge in graph.timeline(action['id'])['edges']
                    if edge['relation'] == 'due_on')
        graph.review_edge(edge['id'], 'retracted')
        for reset in (False, True):
            builder.build(include_meetings=False, reset=reset)
            reviewed = next(row for row in graph.edges() if row['id'] == edge['id'])
            self.assertEqual(reviewed['status'], 'retracted')
            self.assertEqual(reviewed['metadata']['human_override'], 'retracted')
        graph.delete_node(action['id'])
        for reset in (False, True):
            builder.build(include_meetings=False, reset=reset)
            self.assertEqual(graph.get_node(action['id'])['status'], 'deleted')
            self.assertIsNone(graph.resolve('交付第一版'))
            self.assertFalse(any(row['status'] == 'asserted' for row in graph.edges()
                                 if action['id'] in (row['src_id'], row['dst_id'])))

    def test_touch_without_text_change_refreshes_source_stat(self):
        path,index,chunk=self.seed_document()
        info=path.stat();os.utime(path,(info.st_atime,info.st_mtime+10))
        index.index_project(include_meetings=False)
        self.assertIsNotNone(index.read_chunk(chunk))

    def test_strict_membership_does_not_treat_corruption_as_empty(self):
        folder=self.root/'meetingvar';folder.mkdir()
        (folder/'sessions_map.json').write_text('{bad',encoding='utf-8')
        with self.assertRaises(ValueError): session_ids_for_project(self.root,strict=True)

    def test_resolve_does_not_merge_fuzzy_names(self):
        graph=GraphStore(self.root)
        graph.upsert_node(kind='Topic',name='技术方案风险')
        self.assertIsNone(graph.resolve('技术方案',kind='Topic'))
        self.assertIsNotNone(graph.resolve('技术方案风险',kind='Topic'))

    def test_human_node_survives_rebuild_and_automatic_upsert(self):
        graph=GraphStore(self.root)
        node=graph.upsert_node(kind='Person',name='审阅人',description='human description',metadata={'source':'human'})
        graph.upsert_node(kind='Person',name='审阅人',description='automatic replacement',metadata={'source':'extractor'})
        self.assertEqual(graph.get_node(node['id'])['description'],'human description')
        graph.reset()
        self.assertEqual(graph.get_node(node['id'])['description'],'human description')

    def test_deleted_node_is_not_revived_by_extraction_or_reset(self):
        graph=GraphStore(self.root)
        node=graph.upsert_node(kind='Topic',name='误识别实体')
        graph.delete_node(node['id'])
        graph.reset()
        graph.upsert_node(kind='Topic',name='误识别实体',description='automatically rediscovered')
        self.assertEqual(graph.get_node(node['id'])['status'],'deleted')
        self.assertIsNone(graph.resolve('误识别实体'))

    def test_consolidation_only_reports_candidates(self):
        graph=GraphStore(self.root)
        one=graph.upsert_node(kind='Topic',name='相同名称')
        two=graph.upsert_node(kind='Artifact',name='相同名称')
        report=graph.consolidate_duplicates()
        self.assertEqual(report['merged'],0)
        self.assertTrue(report['requires_review'])
        self.assertEqual(graph.get_node(one['id'])['status'],'active')
        self.assertEqual(graph.get_node(two['id'])['status'],'active')

    def test_cross_meeting_fact_refs_do_not_attach_old_turns_to_new_origin(self):
        facts=FactStore(self.root)
        common=dict(name='项目更新',description='项目更新',body='项目已更新',fact_type='project-fact',confidence=.9)
        facts.write(**common,source_refs=['turn_2'],origin_meeting='meeting_a')
        facts.write(**common,source_refs=['turn_7'],origin_meeting='meeting_b')
        current=facts.read('项目更新')
        self.assertEqual(current['source_refs'],['turn_7'])
        evidence=current['metadata']['evidence_refs']
        self.assertIn('meeting_a',{item['source_id'] for item in evidence})
        self.assertIn('meeting_b',{item['source_id'] for item in evidence})


if __name__=='__main__':
    unittest.main()
