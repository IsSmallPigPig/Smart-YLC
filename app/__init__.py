__version__ = "1.0.17"

# 构建目标：打包时由 scripts/build.py 写成 app/_build_target.py（不进仓库）。
# 源码运行时没有这个文件，两个值为空，更新器退回按运行时系统推断。
# 有了它，更新器不必猜「这台机器该拿哪个安装包」——CI 出的是
# Windows10-x64 / Windows7-x86 这种变体，装的就是那一份，升级也必须拿同一份。
try:
    from app._build_target import TARGET_ARCH as BUILD_TARGET_ARCH
    from app._build_target import TARGET_OS as BUILD_TARGET_OS
except Exception:  # noqa: BLE001 - 源码运行、文件被删都走这里
    BUILD_TARGET_OS = ""
    BUILD_TARGET_ARCH = ""


def build_target() -> str:
    """构建变体标签，如 Windows10-x64；源码运行返回空串。"""
    if not BUILD_TARGET_OS:
        return ""
    return f"{BUILD_TARGET_OS}-{BUILD_TARGET_ARCH}" if BUILD_TARGET_ARCH else BUILD_TARGET_OS
