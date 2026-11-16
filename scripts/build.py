"""Nuitka 打包：python scripts/build.py [--dry-run]

产物落点固定，CI 与本地都按同一套路径取件：

  Windows   dist/SmartYouth/智慧团委.exe        （目录名 ASCII，安装脚本按它取文件）
  macOS     dist/智慧团委.app                    （包内可执行文件仍是 ASCII 的 SmartYouth）

为什么换掉 PyInstaller：
  PyInstaller 的冻结导入器每次启动都要遍历归档、逐个重建模块对象，`--collect-all
  PySide6` 还会把 QtWebEngine、Qt3D、Designer 这些没用的模块一起塞进包里，冷启动
  要先被扫一遍。Nuitka 把 Python 编译成 C，导入走原生扩展加载，并只收真正被引用的
  模块——启动更快，包也小一圈。

必须显式喂进去的三样东西（少了任何一样，运行时才报错、打包时不会）：
  * app/assets        界面图标、托盘图标（brand.assets_dir 按可执行文件目录找）
  * qfluentwidgets    包内自带字体/资源，Nuitka 的 pyside6 插件不认识第三方资源
  * PySide6.QtSvg     brand 里在函数内才 import，静态分析能跟上，但写出来更稳
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_NAME = "SmartYouth"           # 文件名/包名，保持 ASCII
DISPLAY_NAME = "智慧团委"
BUNDLE_ID = "com.smallylc.smartyouth"


def version() -> str:
    from app import __version__

    return __version__


def build_command() -> list[str]:
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--enable-plugin=pyside6",
        "--assume-yes-for-downloads",
        "--remove-output",
        "--output-dir=dist",
        f"--output-filename={APP_NAME}",
        "--include-data-dir=app/assets=app/assets",
        "--include-package-data=qfluentwidgets",
        "--include-module=PySide6.QtSvg",
        # 只跑测试用的包不必进产物
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=_pytest",
    ]
    ver = version()

    if sys.platform == "win32":
        ico = ROOT / "app/assets/smart-ylc.ico"
        cmd += [
            "--windows-console-mode=disable",
            f"--windows-icon-from-ico={ico}",
            "--company-name=智慧团委",
            f"--product-name={DISPLAY_NAME}",
            f"--file-description={DISPLAY_NAME}",
            f"--file-version={ver}",
            f"--product-version={ver}",
            "--copyright=GPL-3.0",
        ]
    elif sys.platform == "darwin":
        icns = ROOT / "build" / f"{APP_NAME}.icns"
        cmd += [
            "--macos-create-app-bundle",
            f"--macos-app-name={DISPLAY_NAME}",
            f"--macos-signed-app-name={BUNDLE_ID}",
            f"--macos-app-version={ver}",
            "--macos-app-mode=gui",
        ]
        if icns.is_file():
            cmd.append(f"--macos-app-icon={icns}")

    cmd.append("main.py")
    return cmd


def _strip_previous() -> None:
    for name in (f"{APP_NAME}.dist", f"{APP_NAME}.build", f"{APP_NAME}.app", APP_NAME):
        target = ROOT / "dist" / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.is_file():
            target.unlink()


def _postprocess_macos() -> Path | None:
    dist = ROOT / "dist"
    bundles = sorted(p for p in dist.glob("*.app") if p.is_dir())
    if not bundles:
        return None
    bundle = bundles[0]
    final = dist / f"{DISPLAY_NAME}.app"
    if final.exists():
        shutil.rmtree(final, ignore_errors=True)
    bundle.rename(final)

    # Finder 与启动台读的是包名和 Info.plist 里的显示名；包内可执行文件仍叫
    # SmartYouth，签名与路径都不受影响
    plist = final / "Contents/Info.plist"
    for key, value in (("CFBundleName", DISPLAY_NAME), ("CFBundleDisplayName", DISPLAY_NAME)):
        args = ["/usr/libexec/PlistBuddy", "-c", f"Set :{key} {value}", str(plist)]
        if subprocess.run(args, capture_output=True).returncode != 0:
            subprocess.run(["/usr/libexec/PlistBuddy", "-c", f"Add :{key} string {value}", str(plist)])
    return final


def _postprocess_windows() -> Path | None:
    dist = ROOT / "dist"
    dirs = sorted(p for p in dist.glob("*.dist") if p.is_dir())
    if not dirs:
        return None
    target = dist / APP_NAME
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    dirs[0].rename(target)

    exe = target / f"{APP_NAME}.exe"
    if exe.is_file():
        exe.rename(target / f"{DISPLAY_NAME}.exe")
    return target


def size_mb(path: Path) -> float:
    if path.is_file():
        return path.stat().st_size / 1024 / 1024
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 1024 / 1024


def main() -> int:
    dry = "--dry-run" in sys.argv
    cmd = build_command()
    if dry:
        print(" ".join(cmd))
        return 0

    _strip_previous()
    print("Nuitka 开始编译（首次要编译 C，几分钟起）…", flush=True)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print(f"Nuitka 编译失败，退出码 {result.returncode}", file=sys.stderr)
        return result.returncode

    final = _postprocess_windows() if sys.platform == "win32" else _postprocess_macos()
    if final is None:
        print("没找到打包产物（dist 下既没有 *.dist 也没有 *.app）", file=sys.stderr)
        return 1

    print(f"产物：{final}  约 {size_mb(final):.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
