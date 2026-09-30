# DropRestorer desktop installers

DropRestorer is packaged as a native desktop window that hosts the complete current local web interface. The app includes the server and Chromium-based Qt WebEngine runtime; users do not need to install Python, a browser, or project dependencies.

## Installers

The build workflow produces:

- `DropRestorer-<version>-Windows-x64-Setup.exe` — per-user Windows installer; administrator rights are not required.
- `DropRestorer-<version>-macOS-arm64.dmg` — Apple Silicon.
- `DropRestorer-<version>-macOS-intel-x64.dmg` — Intel Macs.

On macOS, open the DMG and drag DropRestorer into Applications. The current build is ad-hoc signed, not Developer ID signed or notarized; macOS may require Control-click → Open on first launch. A Developer ID certificate and Apple notarization credentials are needed for a no-warning public release.

## User data and local service

Installed builds store the library, settings, reports, and logs in the current user's application-data directory:

- Windows: `%LOCALAPPDATA%\DropRestorer`
- macOS: `~/Library/Application Support/DropRestorer`
- Linux source launch: `$XDG_DATA_HOME/DropRestorer` or `~/.local/share/DropRestorer`

Uninstalling the Windows app leaves this directory alone. A developer checkout keeps using its existing `var/drop-restorer` directory. `--workspace PATH` (or `DROP_RESTORER_WORKSPACE`) selects a different library folder for an installed app.

The desktop app starts the local panel on `127.0.0.1:8780`; if that port belongs to another process or another DropRestorer library, it checks the next 19 ports and opens the matching local service inside its own window. Closing the app stops only the server process that the app itself started. Nothing is exposed on the network.

## Build locally

Use Python 3.12 for the tested release build. The build target must match the OS being built; PyInstaller does not produce macOS apps from Windows. The Windows build isolates its DLL search path so unrelated software cannot inject incompatible Qt dependencies. The build script starts the frozen local server and checks its page and assets before creating an installer.

```powershell
python -m pip install -e . pyinstaller==6.22.2
choco install innosetup --yes --no-progress
python scripts/build_desktop.py installer
```

The Windows setup file is written to `dist/installers/`.
If Inno Setup is installed outside its default directory, set `INNO_SETUP_COMPILER` to the full path of `ISCC.exe` before running the build command.

On macOS, install the project and PyInstaller, then run:

```bash
python -m pip install -e . pyinstaller==6.22.2
python scripts/build_desktop.py installer
```

The DMG is written to `dist/installers/`. On a runner matching the desired Mac CPU architecture, the output matches that runner.

## GitHub build

`.github/workflows/desktop-installers.yml` builds Windows x64, macOS Apple Silicon, and macOS Intel on native GitHub-hosted runners. Pushes to `main` and manual workflow runs publish temporary downloadable artifacts in Actions. Pushing a `v*` tag also creates a GitHub Release containing the three installers.

The workflow runs a packaged-process smoke test against the bundled app, UI assets, local health route, and selected library path. It does not perform a macOS Gatekeeper/notarization check or certify a Windows signature. Public Windows code signing likewise requires a signing certificate.
