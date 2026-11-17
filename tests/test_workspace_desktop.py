"""Desktop workspace bridge tests. No real app/config/provider is used."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('workspace_desktop_app', ROOT / 'desktop' / 'app.py')
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)
bridge_spec = importlib.util.spec_from_file_location('workspace_engine_bridge', ROOT / 'desktop' / 'engine_bridge.py')
engine = importlib.util.module_from_spec(bridge_spec)
bridge_spec.loader.exec_module(engine)


class WorkspaceDesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mv-desktop-workspace-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Bypass Backend initialization entirely: it normally loads the user's config.
        self.backend = desktop.Backend.__new__(desktop.Backend)
        self.backend._cfg = {'engine_python': sys.executable, 'repo_dir': str(ROOT), 'llm_provider': 'local'}

    def test_start_without_model_still_launches_indexing(self):
        result = {'ok': True, 'project': str(self.root), 'job_id': 'j1', 'job_status': 'pending'}
        with patch.object(self.backend, '_engine_env', return_value={}), \
             patch.object(self.backend, '_provider_info', return_value={'ready': False}), \
             patch.object(self.backend, '_llm_env', side_effect=AssertionError('not configured')), \
             patch.object(desktop.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(result), returncode=0)) as run:
            actual = self.backend.workspace_start(str(self.root))
        self.assertEqual(actual['job_id'], 'j1')
        self.assertEqual(run.call_args.kwargs['env']['MEETINGVAR_WORKSPACE_MODEL_READY'], '0')
        self.assertEqual(run.call_args.args[0][-3:], ['workspace_start', str(self.root.resolve()), '0'])

    def test_manual_refresh_passes_force_while_periodic_start_does_not(self):
        with patch.object(self.backend, '_agent_bridge', return_value={'ok': True}) as bridge:
            self.backend.workspace_start(str(self.root), True)
            self.assertEqual(bridge.call_args.args[0][-1], '1')
            self.backend.workspace_start(str(self.root))
            self.assertEqual(bridge.call_args.args[0][-1], '0')

    def test_engine_paused_start_attaches_without_spawning_worker(self):
        from meetingvar.agent import workspace_jobs as jobs
        from meetingvar.agent.workspace import _atomic_json, _inventory, revision
        (self.root / 'README.md').write_text('Synthetic paused project.', encoding='utf-8')
        observed = revision(_inventory(self.root))
        state = {'project': str(self.root.resolve()), 'schema_version': jobs.SCHEMA_VERSION,
                 'job_id': 'paused-job', 'job_status': 'paused_budget', 'running': False,
                 'observed_revision': observed, 'exploration_status': 'paused_budget',
                 'model_budget': {'output_tokens': 16000}, 'stages': {'explore': {'status': 'paused_budget',
                 'errors': [{'code': 'output_budget_exhausted'}]}}}
        _atomic_json(jobs._path(self.root), state)
        with patch('subprocess.Popen', side_effect=AssertionError('paused periodic start spawned worker')):
            result = engine.cmd_workspace_start(self.root)
        self.assertTrue(result['attached'])
        self.assertEqual(result['job_status'], 'paused_budget')
        self.assertEqual(result['model_budget']['output_tokens'], 16000)

    def test_subscription_retains_project_and_job_identity(self):
        with patch.object(self.backend, '_agent_bridge', return_value={'ok': True}) as bridge:
            self.backend.workspace_subscribe(str(self.root), 'j1')
        self.assertEqual(bridge.call_args.args[0], ['workspace_status', str(self.root.resolve()), 'j1'])

    def test_invalid_model_configuration_does_not_block_local_job(self):
        with patch.object(self.backend, '_engine_env', return_value={}), \
             patch.object(self.backend, '_provider_info', return_value={'ready': True}), \
             patch.object(self.backend, '_llm_env', side_effect=ValueError('synthetic bad config')), \
             patch.object(desktop.subprocess, 'run', return_value=SimpleNamespace(stdout='{"ok":true}', returncode=0)) as run:
            self.assertTrue(self.backend.workspace_start(str(self.root))['ok'])
        self.assertEqual(run.call_args.kwargs['env']['MEETINGVAR_WORKSPACE_MODEL_READY'], '0')

    def test_explicit_configuration_is_passed_without_provider_change(self):
        configured = {'MEETINGVAR_LLM_MODEL': 'synthetic-model', 'MEETINGVAR_LLM_BASE_URL': 'http://127.0.0.1:12345/v1'}
        with patch.object(self.backend, '_engine_env', return_value={}), \
             patch.object(self.backend, '_provider_info', return_value={'ready': True}), \
             patch.object(self.backend, '_llm_env', return_value=configured) as model, \
             patch.object(desktop.subprocess, 'run', return_value=SimpleNamespace(stdout='{"ok":true}', returncode=0)) as run:
            self.backend.workspace_start(str(self.root))
        self.assertEqual(run.call_args.kwargs['env']['MEETINGVAR_LLM_MODEL'], 'synthetic-model')
        self.assertEqual(run.call_args.kwargs['env']['MEETINGVAR_WORKSPACE_MODEL_READY'], '1')
        model.assert_called_once_with(purpose='reading')


if __name__ == '__main__':
    unittest.main()
