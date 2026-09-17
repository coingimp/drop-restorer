"""Start or reuse this workspace's local service without cmd.exe."""
import argparse
from pathlib import Path
import os
import subprocess
import sys
import time
import webbrowser

from bs4 import BeautifulSoup
import requests

from ..runtime import python_executable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8780)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parents[2]
    origin = f'http://127.0.0.1:{args.port}'
    session = requests.Session()
    session.trust_env = False

    def available():
        try:
            response = session.get(origin + '/health', timeout=2)
        except requests.ConnectionError:
            return False
        if response.status_code != 200:
            raise RuntimeError(f'Порт {args.port} занят другим приложением.')
        try:
            status = response.json()
        except ValueError as error:
            raise RuntimeError(f'Порт {args.port} занят другим приложением.') from error
        if status.get('app') != 'DropRestorer Web' or Path(status.get('workspace', '')).resolve() != workspace:
            raise RuntimeError(f'Порт {args.port} занят другим приложением.')
        return True

    if not available():
        python = python_executable(workspace)
        output = workspace / 'var' / 'drop-restorer'
        output.mkdir(parents=True, exist_ok=True)
        with (output / 'web-server.log').open('a', encoding='utf-8') as log:
            child = subprocess.Popen([str(python), '-X', 'utf8', '-m', 'drop_restorer.web', '--port', str(args.port)],
                                     cwd=workspace, stdout=log, stderr=log,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        deadline = time.monotonic() + 30
        while not available():
            if child.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Не удалось запустить локальную панель. Подробности: var/drop-restorer/web-server.log')
            time.sleep(0.2)
    if args.project:
        project = args.project.resolve()
        if project.parent != workspace / 'var' / 'drop-restorer':
            raise RuntimeError('Сборка должна находиться в этой папке проекта.')
        token = BeautifulSoup(session.get(origin, timeout=10).text, 'html.parser').select_one('meta[name="csrf-token"]')['content']
        response = session.post(origin + '/api/projects/open', json={'id': project.name},
                                headers={'X-DropRestorer-Token': token, 'Origin': origin}, timeout=10)
        if not response.ok:
            raise RuntimeError(response.json().get('error', 'Не удалось открыть сборку.'))
    if not args.no_browser:
        webbrowser.open(origin)
    if sys.stdout:
        print(origin)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
