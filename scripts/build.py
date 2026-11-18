"""PyInstaller 打包：python scripts/build.py [--dry-run]

产物落点固定，CI 与本地都按同一套路径取件：

  Windows   dist/SmartYouth/智慧团委.exe        （目录名 ASCII，安装脚本按它取文件）
  macOS     dist/智慧团委.app                    （包内可执行文件仍是 ASCII 的 SmartYouth）
  Linux     dist/SmartYouth/                     （onedir 目录；workflow 用 dpkg-deb 打 .deb）

为什么换回 PyInstaller：
  PyMuPDF 2.x 把 SWIG 绑定写成一份 2.7MB 的 pymupdf/mupdf.py。Nuitka 把所有被导入的
  Python 模块编译成 C，单个模块就生成 100MB+ 的 module.pymupdf.mupdf.c，编译要 30 分钟
  以上，还常在 CI 上把内存吃光（Nuitka issue #3243）。排除它又会导致运行期缺 stdlib 模块
  （mupdf.py 自己 import statistics、pathlib 等，Nuitka standalone 不落这些 .py）。
  PyInstaller 不把 Python 编成 C：按字节码收集模块，由 pymupdf 的专用 hook 把已编译好的
  libmupdf.dylib / .so / .dll 原样拷进包，构建从 30+ 分钟降到十几秒，功能不变。
  用 onedir（不是 --onefile）：运行时铺在安装目录里，启动只加载 DLL；单文件每次要先解压到
  临时目录，冷启动明显慢。

必须显式喂进去的三样东西（少了任何一样，运行时才报错、打包时不会）：
  * app/assets        界面图标、托盘图标（brand.assets_dir() 按 _MEIPASS/assets 找）
  * qfluentwidgets    包内自带字体/资源，PyInstaller 默认不收第三方包数据
  * PyQt5.QtSvg     brand 里在函数内才 import，显式带上更稳
  pymupdf 由 PyInstaller 的 hook 自动收集原生库，无需手写。
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


def version_file_path() -> Path:
    """Windows 可执行文件版本资源；PyInstaller 要一份 VSVersionInfo 文本。"""
    return ROOT / "build" / "file_version.txt"


def _write_version_file(ver: str) -> Path:
    """生成 Windows 版本资源，把版本号写进 exe（资源管理器属性里能看到）。"""
    path = version_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    nums = [int(p) if p.isdigit() else 0 for p in ver.split(".")]
    nums += [0] * (4 - len(nums))
    a, b, c, d = nums[:4]
    path.write_text(
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers=({a},{b},{c},{d}), prodvers=({a},{b},{c},{d}),\n"
        "    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),\n"
        "  kids=[\n"
        "    StringFileInfo([StringTable('040904B0', [\n"
        f"      StringStruct('CompanyName', '{DISPLAY_NAME}'),\n"
        f"      StringStruct('FileDescription', '{DISPLAY_NAME}'),\n"
        f"      StringStruct('FileVersion', '{ver}'),\n"
        f"      StringStruct('ProductName', '{DISPLAY_NAME}'),\n"
        f"      StringStruct('ProductVersion', '{ver}'),\n"
        "    ])]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])]),\n"
        "  ],\n"
        ")\n",
        encoding="utf-8",
    )
    return path


def build_command() -> list[str]:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        f"--name={APP_NAME}",
        # qfluentwidgets 包内带字体和资源，PyInstaller 默认不收第三方包数据
        "--collect-all=qfluentwidgets",
        "--hidden-import=PyQt5.QtSvg",
        # PyInstaller 把数据放到 _MEIPASS/assets；brand.assets_dir() 认这个落点
        f"--add-data=app/assets{os.pathsep}assets",
    ]

    if sys.platform == "win32":
        # 浏览器页在函数内部才 import QtWebEngineWidgets，静态分析看不到；
        # 少了它 PyInstaller 的 QtWebEngine hook 不会跑，包里就没有
        # QtWebEngineProcess.exe 和 resources，运行时才报错。
        import PyQt5

        qt = Path(PyQt5.__file__).resolve().parent / "Qt5"
        cmd += [
            f"--icon={ROOT / 'app/assets/smart-ylc.ico'}",
            f"--version-file={version_file_path()}",
            "--hidden-import=PyQt5.QtWebEngineWidgets",
            "--hidden-import=PyQt5.QtWebEngineCore",
            "--hidden-import=PyQt5.QtWebChannel",
            "--hidden-import=PyQt5.QtNetwork",
        ]
        process = qt / "bin" / "QtWebEngineProcess.exe"
        if process.is_file():
            cmd.append(f"--add-binary={process}{os.pathsep}PyQt5/Qt5/bin")
        resources = qt / "resources"
        if resources.is_dir():
            cmd.append(f"--add-data={resources}{os.pathsep}PyQt5/Qt5/resources")
        locales = qt / "translations" / "qtwebengine_locales"
        if locales.is_dir():
            cmd.append(f"--add-data={locales}{os.pathsep}PyQt5/Qt5/translations/qtwebengine_locales")
    elif sys.platform == "darwin":
        # 原生通知走 PyObjC 的 UserNotifications；这些模块在函数内按需导入，
        # PyInstaller 静态分析看不到，显式带上，避免发布版点通知没反应。
        cmd += [
            "--hidden-import=objc",
            "--hidden-import=Foundation",
            "--hidden-import=AppKit",
            "--hidden-import=UserNotifications",
            f"--osx-bundle-identifier={BUNDLE_ID}",
        ]
        icns = ROOT / "build" / f"{APP_NAME}.icns"
        if icns.is_file():
            cmd.append(f"--icon={icns}")

    cmd.append("main.py")
    return cmd


def _strip_previous() -> None:
    for target in (
        ROOT / f"{APP_NAME}.spec",       # PyInstaller 把 spec 写在 cwd（仓库根）
        ROOT / "build" / APP_NAME,       # PyInstaller 的分析缓存
        ROOT / "dist" / APP_NAME,
        ROOT / "dist" / DISPLAY_NAME,
    ):
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
    # onedir 产物固定是 dist/SmartYouth/SmartYouth.exe
    dist = ROOT / "dist"
    target = dist / APP_NAME
    if not target.is_dir():
        return None
    exe = target / f"{APP_NAME}.exe"
    if exe.is_file():
        exe.rename(target / f"{DISPLAY_NAME}.exe")

    # QtWebEngine 找不到资源时的回退位置是 exe 同级目录。中文安装路径会让
    # QLibraryInfo 推导的路径在 local8Bit 转换中被弄坏（日志里的 ???? 即此），
    # 把三件套复制到 exe 旁边，让回退路径必定命中。
    import PyQt5

    qt = Path(PyQt5.__file__).resolve().parent / "Qt5"
    internal = target / "_internal" / "PyQt5" / "Qt5"
    src = qt if (qt / "resources").is_dir() else internal
    for name in ("resources", "translations/qtwebengine_locales"):
        folder = src / name
        if folder.is_dir():
            dest = target / name
            if not dest.exists():
                shutil.copytree(folder, dest)
    proc = src / "bin" / "QtWebEngineProcess.exe"
    if proc.is_file() and not (target / "QtWebEngineProcess.exe").is_file():
        shutil.copyfile(proc, target / "QtWebEngineProcess.exe")
    return target


def _postprocess_linux() -> Path | None:
    # Linux 上应用图标按 Freedesktop 规范走 hicolor 主题，打进 onedir 目录
    # 随包分发；桌面环境会把 512 的图缩到需要的尺寸，不用逐档复制。
    dist = ROOT / "dist"
    target = dist / APP_NAME
    if not target.is_dir():
        return None
    src = ROOT / "app/assets/smart-ylc@512.png"
    if src.is_file():
        d = target / "share/icons/hicolor/512x512/apps"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, d / f"{APP_NAME}.png")
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
    if sys.platform == "win32":
        _write_version_file(version())

    print("PyInstaller 开始打包…", flush=True)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print(f"PyInstaller 打包失败，退出码 {result.returncode}", file=sys.stderr)
        return result.returncode

    if sys.platform == "win32":
        final = _postprocess_windows()
    elif sys.platform == "darwin":
        final = _postprocess_macos()
    else:
        final = _postprocess_linux()
    if final is None:
        print("没找到打包产物（dist 下既没有 SmartYouth 也没有 *.app）", file=sys.stderr)
        return 1

    print(f"产物：{final}  约 {size_mb(final):.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
