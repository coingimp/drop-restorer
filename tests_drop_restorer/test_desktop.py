import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from drop_restorer.desktop import default_workspace, read_health, start_local_service, user_data_directory
from drop_restorer.runtime import is_packaged_application, python_module_command
from scripts.build_desktop import clean_build_environment


class DesktopPackagingTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows DLL search is different.")
    def test_windows_freezer_cannot_collect_unrelated_path_dlls(self):
        with patch.dict(os.environ, {
            "PATH": r"C:\UnrelatedRuntime\bin",
            "PYTHONPATH": r"C:\UnrelatedRuntime\python",
            "QT_PLUGIN_PATH": r"C:\UnrelatedRuntime\plugins",
        }):
            env = clean_build_environment()
        self.assertNotIn("UnrelatedRuntime", env["PATH"])
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("QT_PLUGIN_PATH", env)
        self.assertIn("System32", env["PATH"])

    def test_windows_user_data_folder_is_outside_installation(self):
        result = user_data_directory({"LOCALAPPDATA": "/tmp/win-local"}, "/tmp/win-home", "win32")
        self.assertEqual(result, Path("/tmp/win-local/DropRestorer"))

    def test_macos_uses_application_support(self):
        result = user_data_directory({}, "/Users/tester", "darwin")
        self.assertEqual(result, Path("/Users/tester/Library/Application Support/DropRestorer"))

    def test_linux_respects_xdg_data_home(self):
        result = user_data_directory({"XDG_DATA_HOME": "/tmp/user-data"}, "/home/tester", "linux")
        self.assertEqual(result, Path("/tmp/user-data/DropRestorer"))

    def test_explicit_workspace_is_resolved_and_wins(self):
        with patch.dict(os.environ, {"DROP_RESTORER_WORKSPACE": "/tmp/env-workspace"}):
            self.assertEqual(default_workspace("/tmp/explicit-workspace"), Path("/tmp/explicit-workspace").resolve())

    def test_environment_workspace_is_resolved(self):
        with patch.dict(os.environ, {"DROP_RESTORER_WORKSPACE": "/tmp/env-workspace"}):
            self.assertEqual(default_workspace(), Path("/tmp/env-workspace").resolve())

    def test_health_check_requires_the_expected_app_and_library(self):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"app": "DropRestorer Web", "workspace": "/tmp/workspace"}

        class Session:
            @staticmethod
            def get(url, timeout):
                return Response()

        self.assertTrue(read_health(Session(), "http://127.0.0.1:8780", Path("/tmp/workspace")))
        self.assertFalse(read_health(Session(), "http://127.0.0.1:8780", Path("/tmp/other")))

    def test_frozen_child_process_routes_through_desktop_entrypoint(self):
        with patch("drop_restorer.runtime.sys.frozen", True, create=True), \
             patch("drop_restorer.runtime.sys.executable", "/app/DropRestorer.exe"), \
             patch("drop_restorer.runtime.python_executable", return_value=Path("/wrong/workspace/venv/python")):
            self.assertEqual(
                python_module_command(Path("/tmp/workspace"), "drop_restorer.web.capture", "/tmp/build", "--cover"),
                [str(Path("/app/DropRestorer.exe").resolve()), "--capture", "/tmp/build", "--cover"],
            )

    def test_nuitka_compiled_runtime_uses_packaged_dispatch(self):
        with patch.dict("drop_restorer.runtime.__dict__", {"__compiled__": object()}):
            self.assertTrue(is_packaged_application())
            self.assertEqual(
                python_module_command(Path("/tmp/workspace"), "drop_restorer.web", "--port", "18878"),
                [str(Path(sys.executable).resolve()), "--server", "--port", "18878"],
            )

    def test_source_child_process_uses_python_module(self):
        with patch("drop_restorer.runtime.sys.frozen", False, create=True), patch("drop_restorer.runtime.sys.executable", "/venv/bin/python"):
            self.assertEqual(
                python_module_command(Path("/tmp/workspace"), "drop_restorer.web.site_probe", "/tmp/build"),
                [str(Path("/venv/bin/python").resolve()), "-X", "utf8", "-m", "drop_restorer.web.site_probe", "/tmp/build"],
            )

    def test_server_start_failure_reports_child_exit_and_log_without_masking_it(self):
        process = MagicMock()
        process.poll.return_value = 7
        with tempfile.TemporaryDirectory() as workspace, \
             patch("drop_restorer.desktop.PORT_ATTEMPTS", 1), \
             patch("drop_restorer.desktop.read_health", return_value=False), \
             patch("drop_restorer.desktop.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "завершился с кодом 7"):
                start_local_service(Path(workspace), start_port=18878, timeout=0.01, reuse=False)


if __name__ == "__main__":
    unittest.main()
