"""Run the product's isolated Chromium inspection without visible windows."""
import os
from pathlib import Path
import subprocess
from .models import RestorationError
from ..runtime import python_executable, workspace_root


def failure_message(log_path, returncode):
    details = [line.removeprefix('ERROR: ').strip() for line in
               log_path.read_text(encoding='utf-8', errors='replace').splitlines()
               if line.startswith('ERROR: ')]
    reason = ' '.join(details[-3:]) if details else f'Процесс браузера завершился с кодом {returncode}.'
    return 'Проверка браузера не завершена. ' + reason + ' Подробности в browser-checks.log сборки.'


def run(build, cancel, progress=lambda value,message:None):
    workspace=workspace_root(__file__)
    executable=python_executable(workspace)
    progress(97,'Проверяем страницы и меню в Desktop, Tablet и Mobile…')
    with (build.root/'browser-checks.log').open('w',encoding='utf-8') as output:
        process=subprocess.Popen([str(executable),'-X','utf8','-m','drop_restorer.web.site_probe',str(build.root)],
            cwd=workspace,stdout=output,stderr=output,env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu'),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            while process.poll() is None:
                if cancel.wait(.5):
                    process.terminate();raise RestorationError('Проверка в браузере отменена.')
            if process.returncode:
                raise RestorationError(failure_message(build.root/'browser-checks.log', process.returncode))
        finally:
            if process.poll() is None: process.terminate()
            process.wait(timeout=10)
