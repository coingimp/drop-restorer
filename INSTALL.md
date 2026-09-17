# Установка DropRestorer

Инструкция рассчитана на чистый checkout из Git. Сборки и ключи каждого пользователя
хранятся локально в его каталоге и между компьютерами через Git не передаются.

## Linux

Требуется Linux x86_64 и Python 3.11 или новее. На Debian/Ubuntu установите Python,
модуль виртуального окружения и библиотеки Qt WebEngine:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip \
  libgl1 libegl1 libnss3 libxkbcommon-x11-0 libxcomposite1 \
  libxdamage1 libxrandr2 libxtst6 libxi6 libfontconfig1 libpulse0
```

Если команда `python3 --version` показывает версию младше 3.11, установите Python 3.11+
из репозитория своего дистрибутива и передайте его установщику явно:

```bash
PYTHON_BIN=python3.11 bash install.sh
```

Клонируйте репозиторий и установите приложение:

```bash
git clone https://github.com/coingimp/drop-restorer.git drop-restorer
cd drop-restorer
bash install.sh
```

Установка создаёт `.venv-drop-restorer` и ставит пакет в editable‑режиме. Системный
Python и глобальные библиотеки не изменяются. Обычный запуск:

```bash
bash start-drop-restorer.sh
```

Панель открывается на `http://127.0.0.1:8780/`. На сервере без рабочего стола используйте:

```bash
bash start-drop-restorer.sh --no-browser
```

Сервис остаётся на loopback‑адресе. Для доступа с другого компьютера используйте
защищённый SSH‑туннель или отдельный reverse proxy с авторизацией; прямое открытие
порта в Интернет не включается этим проектом.

### Автозапуск Linux

Для пользовательского systemd создайте и включите unit с автоматическим перезапуском:

```bash
bash enable-drop-restorer-autostart.sh
systemctl --user status drop-restorer-8780.service
```

Если сервис должен работать до входа пользователя в графическую сессию и система
разрешает linger:

```bash
bash enable-drop-restorer-autostart.sh --linger
```

Логи запуска находятся в `var/drop-restorer/web-server.log`. Отключить автозапуск,
не удаляя сборки:

```bash
bash disable-drop-restorer-autostart.sh
```

После обновления исходников выполните `git pull`, затем снова `bash install.sh`.
Перезапуск unit подхватит новую версию:

```bash
systemctl --user restart drop-restorer-8780.service
```

## Windows

Требуется Windows 10/11 и Python 3.11 или новее. В PowerShell из корня checkout:

```powershell
.\Install-DropRestorer.ps1
.\Enable-DropRestorer-Autostart.ps1
```

Для ручного запуска используйте `Start-DropRestorer.ps1` или `DropRestorer.pyw`.
Windows‑лаунчер использует `.venv-drop-restorer\Scripts\pythonw.exe`, а Linux‑лаунчер
использует `.venv-drop-restorer/bin/python`.

## Данные и API‑ключи

После первого запуска создаётся `var/drop-restorer`. Внутри находятся проекты, логи,
отчёты, снимки и сгенерированные архивы. Каталог исключён из Git. Ключ OpenRouter
вводится в панели и очищается после подключения; не записывайте его в исходники,
`.env`, issue или историю коммитов.

## Проверка установки

```bash
.venv-drop-restorer/bin/python -m unittest discover -s tests_drop_restorer -v
python3 -m compileall -q drop_restorer tests_drop_restorer
bash -n install.sh start-drop-restorer.sh enable-drop-restorer-autostart.sh disable-drop-restorer-autostart.sh
```

После запуска проверьте `http://127.0.0.1:8780/health`: ответ должен иметь HTTP 200,
`app` равный `DropRestorer Web`, а `workspace` — путь текущего checkout.

## Частые ошибки

- **Python младше 3.11.** Укажите совместимый интерпретатор через `PYTHON_BIN` и
  повторите `install.sh`.
- **Ошибка Qt/Chromium или отсутствующая библиотека.** Установите перечисленные
  системные библиотеки; для headless‑сервера запускайте с `--no-browser`.
- **Порт 8780 занят.** Запустите `bash start-drop-restorer.sh --port 8790` и открывайте
  `http://127.0.0.1:8790/`. Для постоянного запуска такого порта передайте `--port 8790`
  скрипту автозапуска.
- **Сервис не стартует.** Проверьте `systemctl --user status drop-restorer-8780.service`
  и `journalctl --user -u drop-restorer-8780.service -n 100`.
