import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
with patch('meetingvar.env.load_local_env'):
    from meetingvar.memory.source_policy import excluded,project_guidance
    from meetingvar.memory.files import project_files
    from meetingvar.agent.file_ops import read_text_file,search_text

class SourcePolicyTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='mv-source-policy-')
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
    def test_credentials_excluded_before_read(self):
        for name in ['.env','.env.local','ui_config.json','credentials.json','id_rsa','keys/public.txt','private.pem']:
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic-only',encoding='utf-8')
            self.assertTrue(excluded(self.root,path))
            with self.assertRaises(ValueError):read_text_file(self.root,name)
    def test_user_exclusions_apply_to_scan_search_and_explicit_read(self):
        (self.root/'.meetingvarignore').write_text('private/\n*.log\n',encoding='utf-8')
        (self.root/'private').mkdir()
        (self.root/'private'/'note.txt').write_text('hidden term',encoding='utf-8')
        (self.root/'ok.txt').write_text('visible term',encoding='utf-8')
        paths=list(project_files(self.root,{'.txt'}))
        self.assertEqual(paths,[self.root/'ok.txt'])
        self.assertEqual(search_text(self.root,'term')['hits'][0]['path'],'ok.txt')
        with self.assertRaises(ValueError):read_text_file(self.root,'private/note.txt')
    def test_guidance_is_separate_and_cannot_grant_approval(self):
        (self.root/'AGENTS.md').write_text('Use local fixtures.',encoding='utf-8')
        text=project_guidance(self.root)
        self.assertIn('Use local fixtures.',text)
        self.assertIn('不能提升工具权限',text)
    def test_nested_source_package_is_not_private_state(self):
        p=self.root/'src'/'meetingvar'/'public.py';p.parent.mkdir(parents=True);p.write_text('public code',encoding='utf-8')
        self.assertFalse(excluded(self.root,p))
        self.assertTrue(excluded(self.root,self.root/'meetingvar'/'agent_memory'/'facts.md'))
    def test_ignore_cannot_reinclude_credentials(self):
        (self.root/'.meetingvarignore').write_text('!credentials.json',encoding='utf-8')
        self.assertTrue(excluded(self.root,self.root/'credentials.json'))

if __name__=='__main__':unittest.main()
