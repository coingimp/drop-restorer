from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
APP_PNG = ROOT / "drop_restorer" / "web" / "static" / "drop-restorer.png"


def run(*command: str, cwd: Path = ROOT, env: dict[str, str] | None = None):
    print("+", subprocess.list2cmdline([str(part) for part in command]), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def clean_build_environment() -> dict[str, str]:
    """Keep unrelated PATH DLLs out of the frozen Windows application."""
    env = os.environ.copy()
    for name in ("PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        env.pop(name, None)
    if os.name == "nt":
        windows = Path(env.get("SystemRoot", r"C:\Windows"))
        paths = [
            Path(sys.executable).parent,
            Path(sys.prefix),
            Path(sys.base_prefix),
            Path(sys.base_prefix) / "DLLs",
            windows / "System32",
            windows,
        ]
        env["PATH"] = os.pathsep.join(dict.fromkeys(str(path.resolve()) for path in paths))
    return env


def make_icon_files() -> tuple[Path, Path]:
    APP_PNG.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((20, 20, 492, 492), radius=112, fill="#142e47")
    draw.rounded_rectangle((46, 46, 466, 466), radius=92, outline="#274b6b", width=7)
    draw.line((137, 355, 232, 260, 299, 327, 390, 218), fill="#4bd3c5", width=36, joint="curve")
    draw.line((326, 218, 390, 218, 390, 282), fill="#4bd3c5", width=36, joint="curve")
    try:
        font = ImageFont.truetype("arialbd.ttf", 84)
    except OSError:
        font = ImageFont.load_default()
    label = "DR"
    bounds = draw.textbbox((0, 0), label, font=font)
    draw.text(((512 - (bounds[2] - bounds[0])) / 2, 366), label, font=font, fill="#f4fbff",
              stroke_width=2, stroke_fill="#142e47")
    image.save(APP_PNG, optimize=True)

    icon_dir = DIST / "build-resources"
    icon_dir.mkdir(parents=True, exist_ok=True)
    ico_path = icon_dir / "DropRestorer.ico"
    image.save(ico_path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icns_path = icon_dir / "DropRestorer.icns"
    if sys.platform == "darwin":
        iconset = icon_dir / "DropRestorer.iconset"
        iconset.mkdir(exist_ok=True)
        for size in (16, 32, 128, 256, 512):
            image.resize((size, size), Image.Resampling.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
            image.resize((size * 2, size * 2), Image.Resampling.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
        run("iconutil", "-c", "icns", str(iconset), "-o", str(icns_path))
    return ico_path, icns_path


def build_bundle():
    ico_path, icns_path = make_icon_files()
    icon = ico_path if os.name == "nt" else icns_path if sys.platform == "darwin" else None
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", "DropRestorer", "--collect-data", "drop_restorer",
        "--hidden-import", "PySide6.QtWebEngineCore", "--hidden-import", "PySide6.QtWebEngineWidgets",
        "--hidden-import", "PySide6.QtPdf",
    ]
    if icon:
        command.extend(("--icon", str(icon)))
    command.append(str(ROOT / "DesktopApp.py"))
    build_env = clean_build_environment()
    run(*command, env=build_env)
    if os.name == "nt":
        executable = DIST / "DropRestorer" / "DropRestorer.exe"
    elif sys.platform == "darwin":
        executable = DIST / "DropRestorer.app" / "Contents" / "MacOS" / "DropRestorer"
    else:
        executable = DIST / "DropRestorer" / "DropRestorer"
    smoke_workspace = DIST / "build-smoke"
    run(str(executable), "--smoke-check", "--workspace", str(smoke_workspace), "--port", "18878", env=build_env)


def build_windows_installer():
    program_files = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    candidates = [
        os.environ.get("INNO_SETUP_COMPILER"),
        shutil.which("ISCC.exe"),
        shutil.which("iscc"),
        program_files / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    ]
    compiler = next((str(candidate) for candidate in candidates if candidate and Path(candidate).is_file()), None)
    if not compiler:
        raise RuntimeError("Не найден Inno Setup Compiler (ISCC.exe). Установите Inno Setup 6.")
    version = read_version()
    output = DIST / "installers"
    output.mkdir(parents=True, exist_ok=True)
    run(compiler, "/Qp", f"/DAppVersion={version}", f"/O{output}", str(ROOT / "packaging" / "windows" / "DropRestorer.iss"))


def build_macos_dmg():
    version = read_version()
    app = DIST / "DropRestorer.app"
    if not app.is_dir():
        raise RuntimeError(f"PyInstaller не создал macOS-приложение: {app}")
    if shutil.which("codesign"):
        run("codesign", "--force", "--deep", "--sign", "-", str(app))
    staging = DIST / "dmg-staging"
    staging.mkdir(parents=True, exist_ok=True)
    shutil.copytree(app, staging / "DropRestorer.app", dirs_exist_ok=True)
    applications = staging / "Applications"
    if not applications.exists():
        applications.symlink_to("/Applications", target_is_directory=True)
    output = DIST / "installers"
    output.mkdir(parents=True, exist_ok=True)
    arch = platform.machine().lower()
    arch_label = "arm64" if arch in {"arm64", "aarch64"} else "intel-x64"
    dmg = output / f"DropRestorer-{version}-macOS-{arch_label}.dmg"
    run("hdiutil", "create", "-volname", "DropRestorer", "-srcfolder", str(staging), "-ov", "-format", "UDZO", str(dmg))


def read_version() -> str:
    for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version = "):
            return line.split('"', 2)[1]
    raise RuntimeError("Не удалось определить версию приложения из pyproject.toml")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "installer"
    if target not in {"bundle", "installer"}:
        raise SystemExit("Использование: python scripts/build_desktop.py [bundle|installer]")
    build_bundle()
    if target == "bundle":
        return
    if os.name == "nt":
        build_windows_installer()
    elif sys.platform == "darwin":
        build_macos_dmg()
    else:
        raise SystemExit("Нативные установщики собираются на Windows или macOS; выберите целевой runner.")


if __name__ == "__main__":
    main()
