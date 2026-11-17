"""Isolated runner for synthetic workspace + RagIndex integration acceptance."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch


def main():
    if len(sys.argv) != 3:
        raise SystemExit('usage: python -I test_workspace_rag_runner.py REPOSITORY ACCEPTANCE_ROOT')
    repo, acceptance = [Path(value).resolve() for value in sys.argv[1:]]
    if not acceptance.is_dir():
        raise SystemExit('Acceptance directory must already exist')
    log = acceptance / 'workspace_real_rag_tests.log'
    summary_file = acceptance / 'workspace_real_rag_summary.json'
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(repo / 'src'))
    sys.path.insert(0, str(repo / 'tests'))
    original_cwd = Path.cwd()
    violations = []
    with tempfile.TemporaryDirectory(prefix='workspace-real-rag-', dir=acceptance) as temp:
        isolated = Path(temp).resolve()
        cwd, appdata, local = (isolated / name for name in ('cwd', 'appdata', 'localappdata'))
        for directory in (cwd, appdata, local, isolated / 'home', isolated / 'tmp'):
            directory.mkdir()
        os.chdir(cwd)
        # Do not copy credentials into test configuration. No env loader is invoked.
        for key in list(os.environ):
            if key.startswith('MEETINGVAR_') or any(token in key.upper() for token in ('API_KEY', 'TOKEN', 'PASSWORD', 'SECRET')):
                del os.environ[key]
        os.environ.update({'APPDATA': str(appdata), 'LOCALAPPDATA': str(local),
                           'USERPROFILE': str(isolated / 'home'), 'HOME': str(isolated / 'home'),
                           'TEMP': str(isolated / 'tmp'), 'TMP': str(isolated / 'tmp'),
                           'MEETINGVAR_MODELS': str(isolated / 'absent-models'),
                           'MEETINGVAR_SYNTHETIC_WORKSPACE_TESTS': '1',
                           'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                           'HF_HOME': str(isolated / 'model-cache')})
        tempfile.tempdir = str(isolated / 'tmp')
        allowed_writes = [str(isolated).casefold(), str(log).casefold(), str(summary_file).casefold()]
        repo_key = str(repo).replace(chr(92), '/').casefold()
        def violation(reason):
            violations.append(reason)
            raise AssertionError(reason)
        def audit(event, args):
            if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'socket.bind', 'subprocess.Popen', 'os.system'}:
                violation('blocked external action: ' + event)
            if event not in {'open', 'os.listdir', 'os.scandir'} or not args:
                return
            target = args[0]
            if not isinstance(target, (str, bytes, os.PathLike)):
                return
            text = os.fsdecode(target).replace(chr(92), '/')
            lowered = text.casefold()
            name = lowered.rsplit('/', 1)[-1]
            if name == '.env' or name.startswith('.env.') or lowered.endswith('meetingvar.env.enc'):
                violation('blocked credential file access')
            if (lowered.startswith(repo_key + '/data/') or lowered == repo_key + '/data'
                    or lowered.startswith(repo_key + '/meetingvar/')
                    or '/onedrive/desktop/keys/' in lowered
                    or (name == 'ui_config.json' and not lowered.startswith(str(isolated).replace(chr(92), '/').casefold()))):
                violation('blocked nonfixture data or credential access')
            if event == 'open':
                mode = args[1] if len(args) > 1 else ''
                flags = args[2] if len(args) > 2 else 0
                writing = (isinstance(mode, str) and any(char in mode for char in 'wax+')) or (isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)))
                if writing:
                    absolute = os.path.abspath(os.fsdecode(target)).casefold()
                    if not any(absolute == item or absolute.startswith(item + os.sep) for item in allowed_writes):
                        violation('blocked write outside synthetic fixtures and acceptance logs')
        sys.addaudithook(audit)
        def no_network(*args, **kwargs):
            violation('blocked network call')
        try:
            with ExitStack() as stack:
                stack.enter_context(patch('meetingvar.env.load_local_env', return_value=None))
                stack.enter_context(patch.object(socket, 'create_connection', side_effect=no_network))
                stack.enter_context(patch.object(socket.socket, 'connect', side_effect=no_network))
                stack.enter_context(patch.object(socket.socket, 'connect_ex', side_effect=no_network))
                from meetingvar.agent import embeddings
                from test_workspace_rag_integration import SyntheticEmbedding
                stack.enter_context(patch.object(embeddings, '_GLOBAL_PROVIDER', SyntheticEmbedding()))
                stack.enter_context(patch.object(embeddings.EmbeddingProvider, '_load', side_effect=AssertionError('real embedding model prohibited')))
                modules = ['test_workspace_jobs', 'test_workspace_probe', 'test_workspace_desktop',
                           'test_workspace_memory', 'test_workspace_rag_integration']
                suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
                def flatten(node):
                    for child in node:
                        if isinstance(child, unittest.TestSuite):
                            yield from flatten(child)
                        else:
                            yield child.id()
                ids = list(flatten(suite))
                if len(ids) != len(set(ids)):
                    raise AssertionError('duplicate test ids in suite')
                integration_count = sum(item.startswith('test_workspace_rag_integration.') for item in ids)
                with log.open('w', encoding='utf-8') as stream:
                    stream.write('COMMAND: ' + ' '.join(sys.orig_argv) + '\n')
                    stream.write(f'Python: {sys.version}\nIsolated cwd: {cwd}\nAPPDATA: {appdata}\nLOCALAPPDATA: {local}\n')
                    stream.write('New real-RAG integration tests: no RagIndex/extractor/SQLite/report mocks; model transport + embedding only are doubles. Existing unit regressions retain their original doubles.\n')
                    stream.write(f'Unique tests: {len(ids)}; new real-RAG integration: {integration_count}\n\n')
                    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
                    stream.write('\nSafety violations: ' + json.dumps(violations) + '\n')
                passed = result.wasSuccessful() and not violations and result.testsRun == len(ids) and not result.skipped
                summary = {'unique_tests': len(ids), 'real_rag_integration_tests': integration_count,
                           'existing_regression_tests': len(ids) - integration_count,
                           'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                           'skipped': len(result.skipped), 'safety_violations': violations, 'passed': passed,
                           'log': str(log), 'command': sys.orig_argv,
                           'limitations': ['deterministic embedding stub; no real embedding quality claim',
                                           'injected CallableModelClient transport; no provider/network/model quality claim',
                                           'threaded live-lock integration, not real desktop restart or process kill',
                                           'no application, packaging, installation, build, or real project data used']}
                summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(summary, ensure_ascii=False))
        finally:
            os.chdir(original_cwd)
            tempfile.tempdir = None
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
