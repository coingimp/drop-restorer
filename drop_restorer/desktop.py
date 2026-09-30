"""Native desktop shell around the full local DropRestorer web interface."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
import webbrowser

import requests

from .runtime import is_packaged_application


APP_NAME = "DropRestorer"
DEFAULT_PORT = 8780
PORT_ATTEMPTS = 20


def user_data_directory(environ=None, home=None, system=None) -> Path:
    """Return a writable per-user data directory on Windows, macOS, or Linux."""
    env = os.environ if environ is None else environ
    home_path = Path.home() if home is None else Path(home)
    os_name = sys.platform if system is None else system
    if os_name == "win32":
        base = env.get("LOCALAPPDATA") or env.get("APPDATA") or str(home_path / "AppData" / "Local")
    elif os_name == "darwin":
        base = str(home_path / "Library" / "Application Support")
    else:
        base = env.get("XDG_DATA_HOME") or str(home_path / ".local" / "share")
    return Path(base).expanduser() / APP_NAME


def default_workspace(explicit=None) -> Path:
    """Keep developer checkouts in place and store installed-app data per user."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    override = os.environ.get("DROP_RESTORER_WORKSPACE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if is_packaged_application():
        return user_data_directory().resolve()
    source_root = Path(__file__).resolve().parents[1]
    if (source_root / "pyproject.toml").is_file() and (source_root / "drop_restorer").is_dir():
        return source_root
    return user_data_directory().resolve()


def local_origin(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def read_health(session, origin: str, expected_workspace: Path):
    try:
        response = session.get(origin + "/health", timeout=1.5)
    except requests.RequestException:
        return False
    if response.status_code != 200:
        return False
    try:
        result = response.json()
        return (result.get("app") == "DropRestorer Web"
                and Path(result.get("workspace", "")).resolve() == expected_workspace.resolve())
    except (ValueError, OSError, TypeError):
        return False


def _server_command(port: int) -> list[str]:
    if is_packaged_application():
        command = [sys.executable, "--server"]
    else:
        command = [sys.executable, "-X", "utf8", "-m", "drop_restorer.desktop", "--server"]
    command.extend(("--port", str(port), "--background", "--desktop-service"))
    return command


def _stop_process(process, origin=None):
    if process is None or process.poll() is not None:
        return
    if origin:
        session = requests.Session()
        session.trust_env = False
        try:
            from bs4 import BeautifulSoup

            page = session.get(origin, timeout=2)
            token = BeautifulSoup(page.text, "html.parser").select_one('meta[name="csrf-token"]')
            if page.ok and token:
                session.post(origin + "/api/desktop/shutdown", json={},
                             headers={"X-DropRestorer-Token": token["content"], "Origin": origin}, timeout=2)
                process.wait(timeout=8)
                return
        except (requests.RequestException, subprocess.TimeoutExpired, AttributeError, TypeError):
            pass
        finally:
            session.close()
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def start_local_service(workspace: Path, start_port=DEFAULT_PORT, timeout=35, reuse=True):
    """Start the bundled service or reuse one already serving this same library."""
    workspace = Path(workspace).resolve()
    session = requests.Session()
    session.trust_env = False
    last_process = None
    last_log_path = None
    for offset in range(PORT_ATTEMPTS):
        port = start_port + offset
        if reuse and read_health(session, local_origin(port), workspace):
            session.close()
            return local_origin(port), None

        env = os.environ.copy()
        env["DROP_RESTORER_WORKSPACE"] = str(workspace)
        output = workspace / "var" / "drop-restorer"
        output.mkdir(parents=True, exist_ok=True)
        log_path = output / "web-server.log"
        last_log_path = log_path
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                _server_command(port),
                cwd=workspace if is_packaged_application() else Path(__file__).resolve().parents[1],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                start_new_session=(os.name != "nt"),
            )
        last_process = process
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if read_health(session, local_origin(port), workspace):
                session.close()
                return local_origin(port), process
            if process.poll() is not None:
                break
            time.sleep(0.2)
        _stop_process(process, local_origin(port))
    session.close()
    message = (
        f"Не удалось запустить локальную панель на портах {start_port}–{start_port + PORT_ATTEMPTS - 1}. "
        f"Журнал: {last_log_path or (workspace / 'var' / 'drop-restorer' / 'web-server.log')}."
    )
    if last_process is not None:
        return_code = last_process.poll()
        if return_code is not None:
            message += f" Последний процесс сервера завершился с кодом {return_code}."
    if last_log_path and last_log_path.is_file():
        try:
            tail = last_log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]
        except OSError:
            tail = []
        if tail:
            message += "\nПоследние строки журнала:\n" + "\n".join(tail)
    raise RuntimeError(message)


def run_smoke_check(workspace: Path, port=DEFAULT_PORT) -> int:
    """Exercise the frozen server process and its bundled HTML/static assets."""
    workspace = Path(workspace).resolve()
    origin, process = start_local_service(workspace, start_port=port, reuse=False)
    if process is None:
        raise RuntimeError("Проверка пропущена: smoke-тест должен запустить собственный сервер приложения.")
    session = requests.Session()
    session.trust_env = False
    try:
        page = session.get(origin + "/", timeout=10)
        script = session.get(origin + "/static/app.js", timeout=10)
        health = session.get(origin + "/health", timeout=10).json()
        if page.status_code != 200 or "DropRestorer" not in page.text:
            raise RuntimeError("Главная страница приложения не прошла проверку.")
        if script.status_code != 200 or "async function refreshToken" not in script.text:
            raise RuntimeError("Статические ресурсы веб-панели не попали в приложение.")
        if health.get("workspace") != str(workspace):
            raise RuntimeError("Локальная панель открыла неверную папку данных.")
        print(f"PASS: {origin}, page=200, app.js=200, workspace={workspace}")
        return 0
    finally:
        session.close()
        _stop_process(process, origin)


def run_smoke_ui(workspace: Path, port=DEFAULT_PORT) -> int:
    """Exercise the bundled Qt WebEngine window against the local interface."""
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow

    workspace = Path(workspace).resolve()
    origin, process = start_local_service(workspace, start_port=port, reuse=False)
    if process is None:
        raise RuntimeError("Проверка пропущена: UI smoke-тест должен запустить собственный сервер приложения.")
    allowed_port = int(origin.rsplit(":", 1)[1])
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    view = QWebEngineView()

    class SmokePage(QWebEnginePage):
        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            return url.scheme() in {"data", "about", "blob"} or (
                url.scheme() == "http" and url.host() == "127.0.0.1" and url.port() == allowed_port
            )

    view.setPage(SmokePage(QWebEngineProfile.defaultProfile(), view))
    window = QMainWindow()
    window.setCentralWidget(view)
    window.resize(1200, 800)
    window.show()
    outcomes = {"finished": False, "ok": False, "checks": {}}

    def finish(ok, checks):
        outcomes.update(finished=True, ok=bool(ok), checks=checks)
        app.quit()

    def inspect_page(success):
        if not success:
            finish(False, {"page_loaded": False})
            return
        script = """(() => {
          const panels = [...document.querySelectorAll('.panel')].map(el => el.id);
          return JSON.stringify({
            title: document.title.includes('DropRestorer'),
            projects: panels.includes('projects'), restore: panels.includes('restore'),
            metadata: panels.includes('metadata'), agents: panels.includes('agents'),
            logs: panels.includes('logs'), checks: panels.includes('checks'),
            stylesheet: !!document.querySelector('link[href="/static/app.css"]')
          });
        })()"""

        def result(value):
            import json

            try:
                checks = json.loads(value or "{}")
            except (TypeError, ValueError):
                checks = {"javascript_result": str(value)}
            finish(bool(checks) and all(checks.values()), checks)

        view.page().runJavaScript(script, result)

    view.loadFinished.connect(inspect_page)
    view.setUrl(QUrl(origin))
    QTimer.singleShot(45000, lambda: finish(False, {"timeout": True}))
    try:
        app.exec()
        report = workspace / "var" / "drop-restorer" / "desktop-smoke-report.json"
        report.write_text(
            __import__("json").dumps({"ok": outcomes["ok"], "origin": origin, "checks": outcomes["checks"]}, indent=2),
            encoding="utf-8",
        )
        if outcomes["ok"]:
            print(f"PASS: embedded desktop UI {origin}; {outcomes['checks']}")
            return 0
        raise RuntimeError(f"Встроенное окно не прошло проверку: {outcomes['checks']}")
    finally:
        window.close()
        _stop_process(process, origin)


def run_desktop(workspace: Path, port=DEFAULT_PORT, project=None) -> int:
    from PySide6.QtCore import QUrl, Qt
    from PySide6.QtGui import QAction, QDesktopServices, QIcon
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox, QToolBar

    class LocalOnlyPage(QWebEnginePage):
        def __init__(self, profile, parent, local_port):
            super().__init__(profile, parent)
            self.local_port = local_port

        def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
            if url.scheme() in {"data", "about", "blob"}:
                return True
            if (url.scheme() == "http" and url.host() == "127.0.0.1"
                    and url.port() == self.local_port):
                return True
            if navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
                QDesktopServices.openUrl(url)
            return False

    class DesktopWindow(QMainWindow):
        def __init__(self, origin, server_process):
            super().__init__()
            self.origin = origin
            self.server_process = server_process
            self.setWindowTitle("DropRestorer — восстановление архивных сайтов")
            self.resize(1440, 960)
            self.setMinimumSize(900, 650)
            icon_path = Path(__file__).resolve().parent / "web" / "static" / "drop-restorer.png"
            if icon_path.is_file():
                self.setWindowIcon(QIcon(str(icon_path)))

            self.view = QWebEngineView(self)
            self.view.setPage(LocalOnlyPage(QWebEngineProfile.defaultProfile(), self.view, int(origin.rsplit(":", 1)[1])))
            self.setCentralWidget(self.view)

            toolbar = QToolBar("Основные действия", self)
            toolbar.setMovable(False)
            self.addToolBar(toolbar)
            refresh = QAction("Обновить", self)
            refresh.triggered.connect(self.view.reload)
            toolbar.addAction(refresh)
            open_browser = QAction("Открыть в браузере", self)
            open_browser.triggered.connect(lambda: webbrowser.open(self.origin))
            toolbar.addAction(open_browser)

            profile = self.view.page().profile()
            profile.downloadRequested.connect(self.save_download)
            self.view.setUrl(QUrl(self.origin))

        def save_download(self, download):
            suggested = download.downloadFileName() or "DropRestorer-download.zip"
            target, _ = QFileDialog.getSaveFileName(self, "Сохранить файл", str(Path.home() / suggested))
            if not target:
                download.cancel()
                return
            target_path = Path(target)
            download.setDownloadDirectory(str(target_path.parent))
            download.setDownloadFileName(target_path.name)
            download.accept()

        def closeEvent(self, event):
            _stop_process(self.server_process, self.origin)
            event.accept()

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(True)
    try:
        origin, process = start_local_service(workspace, start_port=port)
    except Exception as error:
        QMessageBox.critical(None, APP_NAME, str(error))
        return 1
    window = DesktopWindow(origin, process)
    window.show()

    if project:
        def open_project():
            import requests
            from bs4 import BeautifulSoup

            session = requests.Session()
            session.trust_env = False
            html = session.get(origin, timeout=10).text
            token = BeautifulSoup(html, "html.parser").select_one('meta[name="csrf-token"]')["content"]
            response = session.post(
                origin + "/api/projects/open",
                json={"id": Path(project).name},
                headers={"X-DropRestorer-Token": token, "Origin": origin},
                timeout=10,
            )
            if not response.ok:
                QMessageBox.warning(window, APP_NAME, response.json().get("error", "Не удалось открыть сборку."))
            window.view.reload()
            session.close()

        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, open_project)
    return app.exec()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--server" in argv:
        sys.argv = [sys.argv[0], *(value for value in argv if value != "--server")]
        from .web.__main__ import main as server_main

        return server_main()
    if "--capture" in argv:
        sys.argv = [sys.argv[0], *(value for value in argv if value != "--capture")]
        from .web.capture import main as capture_main

        return capture_main()
    if "--site-probe" in argv:
        sys.argv = [sys.argv[0], *(value for value in argv if value != "--site-probe")]
        from .web.site_probe import main as site_probe_main

        return site_probe_main()

    parser = argparse.ArgumentParser(description="DropRestorer desktop application")
    parser.add_argument("--workspace", help="Папка с локальной библиотекой и настройками")
    parser.add_argument("--project", help="Открыть сборку при запуске")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--smoke-check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-ui", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Порт должен быть от 1024 до 65535.")
    workspace = default_workspace(args.workspace)
    if args.smoke_check:
        return run_smoke_check(workspace, args.port)
    if args.smoke_ui:
        return run_smoke_ui(workspace, args.port)
    return run_desktop(workspace, args.port, args.project)


if __name__ == "__main__":
    raise SystemExit(main())
