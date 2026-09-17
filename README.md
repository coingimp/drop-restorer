# DropRestorer

Чистый экспорт инструмента восстановления архивных страниц Wayback в WordPress.
Папка подготовлена как самостоятельный Git‑репозиторий: в ней находятся исходный
код, тесты и лаунчеры для Windows и Linux. Личные сборки, архивы сайтов, ключи,
логи и виртуальное окружение сюда не копируются.

Подробная пошаговая инструкция установки находится в [`INSTALL.md`](INSTALL.md).

## Linux

Поддерживается Linux x86_64 с Python 3.11 или новее. На Debian/Ubuntu установите
Python и модуль виртуальных окружений, затем выполните:

```bash
sudo apt install python3 python3-venv python3-pip \
  libgl1 libegl1 libnss3 libxkbcommon-x11-0 libxcomposite1 \
  libxdamage1 libxrandr2 libxtst6 libxi6 libfontconfig1 libpulse0
bash install.sh
bash start-drop-restorer.sh
```

Панель откроется на `http://127.0.0.1:8780/`. На сервере без рабочего стола
используйте `bash start-drop-restorer.sh --no-browser`: сам сервис остаётся
доступен локально, а браузер можно открыть через SSH‑туннель или на рабочем
компьютере. Для пользовательского автозапуска с перезапуском:

```bash
bash enable-drop-restorer-autostart.sh
# для запуска до входа в графическую сессию, если это разрешено системой:
bash enable-drop-restorer-autostart.sh --linger
```

Проверить сервис можно командой `systemctl --user status drop-restorer-8780.service`.
Отключить автозапуск без удаления сборок: `bash disable-drop-restorer-autostart.sh`.
Скрипт создаёт пользовательский systemd unit и не требует прав root; `sudo` нужен
только для установки системных пакетов.

## Windows

Оставлены существующие PowerShell‑лаунчеры:

```powershell
.\Install-DropRestorer.ps1
.\Enable-DropRestorer-Autostart.ps1
```

Они используют отдельное `.venv-drop-restorer` и Планировщик Windows. Linux‑скрипты
не зависят от PowerShell и не изменяют Windows‑настройки.

## Данные и ключи

После установки рабочие данные сохраняются в `var/drop-restorer`. Каталоги `var/`,
`backups/`, `.venv-drop-restorer/`, логи и архивы добавлены в `.gitignore` и не должны
публиковаться. Ключ OpenRouter вводится в локальной панели и не записывается в Git;
не добавляйте ключи в `.env`, исходники, issue или историю коммитов. Сервер по умолчанию
слушает только `127.0.0.1`; для общего удалённого доступа сначала нужны аутентификация,
HTTPS и отдельное хранилище данных, одного Git для этого недостаточно.

## Проверка перед публикацией

```bash
.venv-drop-restorer/bin/python -m unittest discover -s tests_drop_restorer -v
python3 -m compileall -q drop_restorer tests_drop_restorer
bash -n install.sh start-drop-restorer.sh enable-drop-restorer-autostart.sh disable-drop-restorer-autostart.sh
```

Полная проверка браузера требует рабочего Qt WebEngine и графического окружения либо
настроенного headless‑режима. После запуска панели доступен `/health`; он должен вернуть
HTTP 200 и рабочую папку этого checkout.

## Публикация в Git

Эта папка не содержит вложенного `.git`, чтобы её можно было сначала проверить. После
проверки создайте отдельный репозиторий (для начала лучше private):

```bash
git init -b main
git add .
git commit -m "Initial DropRestorer release"
git remote add origin <URL-репозитория>
git push -u origin main
```

Перед переводом в public выберите лицензию, проверьте права на исходники и не добавляйте
снимки чужих сайтов или данные пользователей. Для раздачи без Git собирайте из этой же
папки версионный ZIP только после прохождения тестов и проверки содержимого архива.

В `.github/workflows/ci.yml` находится Linux‑проверка для каждого push и pull request:
она ставит системные библиотеки Qt WebEngine, создаёт окружение, проверяет shell‑скрипты,
компилирует Python и запускает unit‑тесты.
