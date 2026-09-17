from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import faulthandler
import os
from pathlib import Path
import sys
import traceback

from .service import AlreadyRunning, server_lock


def main():
    parser = argparse.ArgumentParser(description='DropRestorer local browser interface')
    parser.add_argument('--port', type=int, default=8780)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--background', action='store_true', help='Write startup and error output to web-server.log.')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Port must be between 1024 and 65535.')
    workspace = Path(__file__).resolve().parents[2]
    try:
        with server_lock(workspace, args.port):
            if args.background:
                root = workspace / 'var' / 'drop-restorer'
                with (root / 'web-server.log').open('a', encoding='utf-8', buffering=1) as log:
                    with redirect_stdout(log), redirect_stderr(log):
                        faulthandler.enable(file=log)
                        try:
                            print(f'[{datetime.now(timezone.utc).isoformat()}] Starting DropRestorer PID {os.getpid()} on port {args.port}', flush=True)
                            run_server(args, workspace)
                        except BaseException:
                            traceback.print_exc()
                            raise
                        finally:
                            faulthandler.disable()
            else:
                run_server(args, workspace)
    except AlreadyRunning:
        # A manual launcher or another scheduled start can race us. Do not
        # initialize the workspace, Qt, previews, or jobs a second time.
        if sys.stdout:
            print(f'DropRestorer is already running on port {args.port}.')
        return 0
    return 0


def run_server(args, workspace):
    # Only the font service is initialized; no Qt window is created. Windows'
    # native font backend is needed because offscreen QSvg renders text as boxes.
    os.environ['QT_QPA_PLATFORM'] = 'windows' if os.name == 'nt' else 'offscreen'
    from PySide6.QtGui import QGuiApplication
    from werkzeug.serving import make_server
    from ..preview.server import QuietHandler
    from .server import create_app, write_json

    gui = QGuiApplication.instance() or QGuiApplication([])
    app = create_app(workspace, args.port)
    state = app.extensions['workspace']
    if args.project:
        root = args.project.resolve()
        if root.parent != state.root.resolve():
            raise ValueError('Project must belong to this workspace.')
        state.select(state.project(root.name))
    server = make_server('127.0.0.1', args.port, app, threaded=True, request_handler=QuietHandler)
    write_json(state.root / 'web-server.json', {'pid': os.getpid(), 'port': args.port,
               'url': f'http://127.0.0.1:{args.port}', 'workspace': str(workspace)})
    try:
        server.serve_forever()
    finally:
        server.server_close()
        state.close()
        gui.quit()


if __name__ == '__main__':
    raise SystemExit(main())
