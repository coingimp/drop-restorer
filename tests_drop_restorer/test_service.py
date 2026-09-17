from argparse import Namespace
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from drop_restorer.web import __main__ as entrypoint
from drop_restorer.web.service import AlreadyRunning, server_lock


class ServiceTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'var/drop-restorer/development-qa/service-fixtures'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)

    def test_second_instance_is_rejected_and_lock_is_reusable(self):
        with server_lock(self.workspace, 8780):
            with self.assertRaises(AlreadyRunning):
                with server_lock(self.workspace, 8780):
                    self.fail('A second server acquired the same lock.')
            with server_lock(self.workspace, 8781):
                pass
            with server_lock(self.workspace / 'other-workspace', 8780):
                pass
        with server_lock(self.workspace, 8780):
            pass

    def test_killed_process_releases_lock_without_deleting_lock_file(self):
        code = (
            'from pathlib import Path; import sys, time; '
            'from drop_restorer.web.service import server_lock; '
            'lock = server_lock(Path(sys.argv[1]), 8780); lock.__enter__(); '
            'print("LOCKED", flush=True); time.sleep(60)'
        )
        child = subprocess.Popen(
            # On Windows the venv executable is a waiting launcher, not the
            # interpreter that owns the lock. Kill the actual test process.
            [getattr(sys, '_base_executable', sys.executable), '-X', 'utf8', '-u', '-c', code, str(self.workspace)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), 'LOCKED')
            with self.assertRaises(AlreadyRunning):
                with server_lock(self.workspace, 8780):
                    self.fail('A second process acquired the same lock.')
            child.kill()
            child.wait(timeout=10)
            self.assertTrue((self.workspace / 'var/drop-restorer/web-server-8780.lock').is_file())
            with server_lock(self.workspace, 8780):
                pass
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            child.stdout.close()
            child.stderr.close()

    def test_duplicate_start_does_not_initialize_workspace_or_gui(self):
        with patch.object(entrypoint, 'server_lock', side_effect=AlreadyRunning), \
                patch.object(entrypoint, 'run_server') as run, \
                patch.object(sys, 'argv', ['drop_restorer.web']):
            self.assertEqual(entrypoint.main(), 0)
            run.assert_not_called()

    def test_background_failure_is_logged_and_propagated_for_scheduler(self):
        args = Namespace(port=8780, project=None, background=True)
        with patch.object(entrypoint.argparse.ArgumentParser, 'parse_args', return_value=args), \
                patch.object(entrypoint, '__file__', str(self.workspace / 'drop_restorer/web/__main__.py')), \
                patch.object(entrypoint, 'run_server', side_effect=RuntimeError('synthetic startup failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic startup failure'):
                entrypoint.main()
        log = (self.workspace / 'var/drop-restorer/web-server.log').read_text(encoding='utf-8')
        self.assertIn('Starting DropRestorer PID', log)
        self.assertIn('Traceback', log)
        self.assertIn('synthetic startup failure', log)
        with server_lock(self.workspace, 8780):
            pass


if __name__ == '__main__':
    unittest.main()
