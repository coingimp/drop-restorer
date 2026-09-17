import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from drop_restorer.runtime import python_executable, workspace_root


class RuntimePortabilityTests(unittest.TestCase):
    def test_uses_unix_virtualenv_layout(self):
        if os.name == 'nt':
            self.skipTest('Unix virtual environments use a different layout.')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / '.venv-drop-restorer' / 'bin' / 'python'
            executable.parent.mkdir(parents=True)
            executable.write_text('#!/bin/sh\n', encoding='utf-8')
            self.assertEqual(python_executable(root), executable.resolve())

    def test_uses_windows_virtualenv_layout(self):
        if os.name != 'nt':
            self.skipTest('Windows virtual environments use a different layout.')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / '.venv-drop-restorer' / 'Scripts' / 'pythonw.exe'
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b'')
            self.assertEqual(python_executable(root), executable.resolve())

    def test_falls_back_to_current_interpreter_without_project_venv(self):
        with tempfile.TemporaryDirectory() as directory, patch('drop_restorer.runtime.sys.executable', str(Path(directory) / 'python')):
            self.assertEqual(python_executable(Path(directory)), (Path(directory) / 'python').resolve())

    def test_explicit_module_anchor_can_select_a_temporary_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anchor = root / 'drop_restorer' / 'web' / '__main__.py'
            anchor.parent.mkdir(parents=True)
            anchor.touch()
            self.assertEqual(workspace_root(anchor), root.resolve())

    def test_environment_override_wins_over_module_location(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {'DROP_RESTORER_WORKSPACE': str(root)}):
                self.assertEqual(workspace_root(__file__), root.resolve())


if __name__ == '__main__':
    unittest.main()
