"""冻结产物里 QtWebEngine 的实际位置，以及「引擎能不能用」。

Qt 按 QLibraryInfo 推出的前缀找 QtWebEngineProcess.exe 与 resources，打包后
那条路径指向构建机；PyInstaller 5.x 把 PyQt5 平铺在安装根，6.x 收进 _internal，
两种布局都要认。

能不能用只看 `resources/qtwebengine_resources.pak`：目录在而 pak 不在时，
Qt 5.15 的引擎初始化在 Windows 上直接触发 int3 断点（0x80000003），进程当场
消失，来不及抛异常、也留不下 traceback。所以这里统一探测一次，其余代码只问
available()，不自己去碰 QtWebEngine。

第二层坑是中文安装目录：Qt5 的 WebEngine 用 ANSI 代码页解析路径，非中文系统上
「智慧团委」会变成 '?'，连自己刚设进去的路径都读不到（日志里就是 ????
和 �ǻ���ί 那两行）。写环境变量前先换成 8.3 短路径（纯 ASCII），取不到再退回。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Qt 认的是这两个文件：少了 pak 引擎初始化直接 abort，少了 icudtl.dat 起不来
REQUIRED_RESOURCES = ("qtwebengine_resources.pak", "icudtl.dat")

_prepared = False
_ok = False
_why = ""
_resources: Path | None = None


def _short_path(path: str) -> str:
    """换成 Windows 8.3 短路径（纯 ASCII）。已经是 ASCII 或取不到时原样返回。

    8.3 名字可以按卷关闭，所以失败不算错——退回长路径，行为与以前一致。
    """
    if sys.platform != "win32" or all(ord(ch) < 128 for ch in path):
        return path
    try:
        import ctypes
        from ctypes import wintypes

        get = ctypes.windll.kernel32.GetShortPathNameW
        get.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get.restype = wintypes.DWORD
        size = get(path, None, 0)
        if not size:
            return path
        buf = ctypes.create_unicode_buffer(size + 1)   # 返回的长度不含终止符
        if not get(path, buf, size + 1):
            return path
        return buf.value or path
    except Exception:
        return path


def _candidate_roots() -> list[Path]:
    """onedir 的两种布局：PyInstaller 6 的 _internal 与 5.x 的安装根。"""
    exe_dir = Path(sys.executable).resolve().parent
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    roots += [exe_dir, exe_dir / "_internal"]
    seen: set[Path] = set()
    unique = []
    for root in roots:
        try:
            key = root.resolve()
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def prepare() -> bool:
    """探测一次并钉好三个环境变量；幂等，重复调用返回第一次的结果。

    非冻结环境（开发、pytest）直接用 PyQt5 自己的路径，视为可用。
    """
    global _prepared, _ok, _why, _resources
    if _prepared:
        return _ok
    _prepared = True

    if not getattr(sys, "frozen", False):
        _ok, _why = True, "开发环境"
        return True

    tried = []
    for root in _candidate_roots():
        qt = root / "PyQt5" / "Qt5"
        resources = qt / "resources"
        missing = [n for n in REQUIRED_RESOURCES if not (resources / n).is_file()]
        if missing:
            tried.append(f"{resources}（缺 {', '.join(missing)}）")
            continue

        # 直接赋值，不用 setdefault：PyQt5 的启动代码会先塞一个指向构建机的值
        os.environ["QTWEBENGINE_RESOURCES_PATH"] = _short_path(str(resources))

        suffix = ".exe" if sys.platform == "win32" else ""
        for proc in (qt / "bin" / f"QtWebEngineProcess{suffix}",
                     qt / "libexec" / f"QtWebEngineProcess{suffix}"):
            if proc.is_file():
                os.environ["QTWEBENGINEPROCESS_PATH"] = _short_path(str(proc))
                break

        locales = qt / "translations" / "qtwebengine_locales"
        if locales.is_dir():
            os.environ["QTWEBENGINE_LOCALES_PATH"] = _short_path(str(locales))

        _ok, _why, _resources = True, "", resources
        return True

    _ok = False
    _why = "找不到 WebEngine 资源：" + ("；".join(tried) or "没有候选目录")
    return False


def available() -> bool:
    """引擎能否安全初始化：pak 齐了才算。非冻结环境恒为 True。"""
    return prepare()


def reason() -> str:
    """不可用的原因，写日志用；可用时是空串。"""
    prepare()
    return _why


def resources_dir() -> Path | None:
    prepare()
    return _resources
