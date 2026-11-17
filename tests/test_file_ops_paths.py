"""The agent may address files by project-relative or absolute in-project paths."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
with patch('meetingvar.env.load_local_env'):
    from meetingvar.agent.file_ops import list_directory, read_text_file, search_text


class FileOpsPathTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='mv-file-paths-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'notes').mkdir()
        (self.root / 'notes' / 'a.txt').write_text('项目材料', encoding='utf-8')

    def test_absolute_project_root_lists(self):
        result = list_directory(self.root, str(self.root))
        self.assertEqual(result['path'], '.')
        self.assertTrue(any(row['name'] == 'notes' for row in result['entries']))

    def test_absolute_subdirectory_lists_with_relative_paths(self):
        result = list_directory(self.root, str(self.root / 'notes'))
        self.assertEqual(result['path'], 'notes')
        self.assertEqual(result['entries'][0]['path'], 'notes/a.txt')

    def test_absolute_file_can_be_read(self):
        result = read_text_file(self.root, str(self.root / 'notes' / 'a.txt'))
        self.assertEqual(result['text'], '项目材料')
        self.assertEqual(result['path'], 'notes/a.txt')

    def test_absolute_outside_path_is_rejected(self):
        with self.assertRaises(ValueError):
            read_text_file(self.root, 'C:/Windows/win.ini')
        with self.assertRaises(ValueError):
            list_directory(self.root, 'C:/Windows')

    def test_parent_traversal_still_rejected(self):
        with self.assertRaises(ValueError):
            read_text_file(self.root, '../outside.txt')
        with self.assertRaises(ValueError):
            list_directory(self.root, '../')

    def test_absolute_search_scope_is_inside_project(self):
        result = search_text(self.root, '项目', str(self.root / 'notes'))
        self.assertEqual(result['hits'][0]['path'], 'notes/a.txt')


if __name__ == '__main__':
    unittest.main()