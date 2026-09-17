import sys
import argparse
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from .ui.main_window import MainWindow
from .runtime import workspace_root


def desktop_main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path)
    arguments, _ = parser.parse_known_args()
    application = QApplication(sys.argv)
    application.setApplicationName('DropRestorer')
    application.setOrganizationName('DropRestorer')
    workspace = workspace_root(__file__)
    window = MainWindow(workspace)
    window.show()
    if arguments.project:
        QTimer.singleShot(0, lambda: window.open_project(arguments.project))
    return application.exec()


def main():
    # Browser is the default entry point; keep the previous interface recoverable.
    if '--desktop' in sys.argv:
        sys.argv.remove('--desktop')
        return desktop_main()
    from .web.launcher import main as launch_web
    return launch_web()


if __name__ == '__main__':
    sys.exit(main())
