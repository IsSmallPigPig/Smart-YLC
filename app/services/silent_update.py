"""静默更新：下载完成后在独立进程里装好，再通知用户重启。

安装必须发生在主程序退出之后——正在运行的 exe 会被占用。做法是拉起一个独立进程：
它等主程序让出文件后执行安装，完成后发系统通知。用户全程看不到安装窗口。

支持的安装包：
  Windows  exe（Inno Setup）/ msi  —— 全静默参数，不弹界面、不自动重启
  macOS   dmg（挂载后复制 .app 到「应用程序」）/ pkg
其它类型或没有权限时，明确通知用户手动安装，不假装成功。

Windows 有两条硬约束，决定了这里的实现：
  1. 安装程序要替换正在运行的 exe。任何还活着的 SmartYouth 进程都会锁住它，
     Inno 的「文件被占用」对话框在 /SUPPRESSMSGBOXES 下默认选「中止」，
     安装器就以退出码 5 结束——这正是「托盘还在、更新失败」的现场。
  2. 因此安装助手不能是自家 exe：从安装目录再起一个 SmartYouth.exe，等于
     自己锁自己。改用 cmd 延迟脚本拉起安装器，进程模型与本程序彻底脱钩。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLAG = "--silent-install"


class SilentInstallError(Exception):
    """无法静默安装；message 里写清原因和下一步。"""


def _packaged() -> bool:
    """打包后的可执行文件名不是 python，直接用自身重入；源码版走 -m app.main。"""
    return bool(
        getattr(sys, "frozen", False)
        or getattr(sys, "__compiled__", False)
        or not Path(sys.executable).name.lower().startswith("python")
    )


def helper_command(path: str) -> list[str]:
    """返回拉起安装助手的命令行；助手与主程序用同一个入口，靠隐藏参数分流。"""
    if _packaged():
        return [sys.executable, FLAG, path]
    return [sys.executable, "-m", "app.main", FLAG, path]


def start(path: str) -> bool:
    """拉起安装流程；主程序随后退出。Windows 与 macOS 走不同进程模型。"""
    target = Path(path)
    if not target.is_file():
        raise SilentInstallError("更新包不存在，可能已被移动或删除")
    if sys.platform == "win32":
        return _start_windows(target)

    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    # 源码版要用 -m app.main 找到模块；打包后 __file__ 在产物里，cwd 没有意义
    if not _packaged() and ROOT.is_dir():
        kwargs["cwd"] = str(ROOT)
    if sys.platform == "win32":
        flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(helper_command(str(target)), **kwargs)
    return True


# 更新标记：安装前写下目标版本，装完新版本启动时据此提示「已更新」。
def _marker_path() -> Path:
    return Path(os.path.expanduser("~")) / ".gd12355-helper" / "updated.json"


def mark_pending(version: str) -> None:
    """安装前记录目标版本；新版本第一次启动时消费它。"""
    path = _marker_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": version}), encoding="utf-8")


def save_pending_installer(path: str, version: str = "") -> None:
    """记下已下载、等用户确认重启的更新包；跨会话保留以便下次启动再提示。"""
    from app.services import store

    store.update(lambda cfg: cfg.__setitem__(
        "pendingInstaller", {"path": str(path), "version": str(version or "")}))


def pending_installer() -> dict:
    """读取待安装包；文件已被移走或删除时返回空并顺手清掉记录。"""
    from app.services import store

    item = (store.load() or {}).get("pendingInstaller") or {}
    path = str(item.get("path") or "")
    if not path or not Path(path).is_file():
        if path:
            clear_pending_installer()
        return {}
    return {"path": path, "version": str(item.get("version") or "")}


def clear_pending_installer() -> None:
    from app.services import store

    store.update(lambda cfg: cfg.pop("pendingInstaller", None))


def take_updated(version: str) -> str:
    """如果这次启动是更新后的第一次，返回被替换的旧版本号，并清掉标记。

    只有当前版本 >= 标记版本才消费——安装失败时标记留着，
    下次启动还能提醒去手动装。
    """
    path = _marker_path()
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        target = str(data.get("version") or "")
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return ""
    from app.services.updater import parse_version

    if target and parse_version(version) >= parse_version(target):
        path.unlink(missing_ok=True)
        return target
    return ""


def _start_windows(target: Path) -> bool:
    """直接拉起安装器，然后由调用方退出主程序。

    不用「自家 exe 当助手」：从安装目录再起一个 SmartYouth.exe，等于自己锁住
    正在被替换的文件，安装器的「文件被占用」在静默模式下默认选中止，退出码 5。

    也不再写 .cmd 做延迟：路径里可能有中文用户名，批处理按控制台代码页解析，
    写错编码就静默失败。安装器的 /CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS
    本来就会等（必要时强制关闭）仍占用文件的进程，比脚本 sleep 更可靠。

    安装器单独拉起、不与后续启动串成一条命令：串起来一旦 cmd 解析出错，
    连安装都不会执行。启动另挂延迟任务，最坏只是没自动打开，安装照常完成。
    """
    log = Path(os.path.expanduser("~")) / ".gd12355-helper" / "updates" / "install.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    args = ["/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES", "/NOCANCEL",
            "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS", f"/LOG={log}"]
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    subprocess.Popen([str(target), *args], cwd=str(target.parent),
                     stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, close_fds=True)
    _schedule_windows_relaunch()
    return True


def _schedule_windows_relaunch(delay: int = 12) -> None:
    """装完后把新版本重新打开，让「重启」真正回到应用。"""
    exe = Path(sys.executable)
    if not exe.name.lower().endswith(".exe"):
        return      # 源码版（python.exe）不自动重开
    command = f'timeout /t {delay} /nobreak >nul & start "" "{exe}"'
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        subprocess.Popen(["cmd", "/c", command],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True)
    except OSError:
        pass        # 自动重开失败不影响安装，用户可从桌面图标打开


def _relaunch_macos() -> bool:
    """macOS：从当前可执行文件推断 .app 包路径并打开。"""
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:
        if parent.suffix == ".app":
            try:
                subprocess.Popen(["open", str(parent)],
                                 stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return True
            except OSError:
                return False
    return False


def installer_command(path: str) -> list[str] | None:
    """返回静默安装命令；dmg 没有现成安装器，返回 None 走复制分支。"""
    suffix = Path(path).suffix.lower()
    if sys.platform == "win32":
        if suffix == ".exe":
            return [
                path, "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES",
                "/NOCANCEL", "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS",
            ]
        if suffix == ".msi":
            return ["msiexec", "/i", path, "/qn", "/norestart"]
    if sys.platform == "darwin" and suffix == ".pkg":
        return ["installer", "-pkg", path, "-target", "/"]
    return None


def _install_dmg(path: str) -> None:
    """挂载 dmg，把里面的 .app 复制到「应用程序」，最后卸载镜像。"""
    proc = subprocess.run(
        ["hdiutil", "attach", "-nobrowse", "-noautoopen", path],
        capture_output=True, check=True,
    )
    # hdiutil 输出按 UTF-8 解：卷名可能就是「智慧团委」，text=True 会按
    # locale 编码解（Windows 上是 cp936 之外的某些环境），中文卷名变乱码
    stdout = proc.stdout.decode("utf-8", "replace")
    mount = None
    for line in stdout.splitlines():
        candidate = line.split("\t")[-1].strip()
        if candidate.startswith("/Volumes/"):
            mount = Path(candidate)
            break
    if mount is None or not mount.is_dir():
        raise SilentInstallError("无法找到 dmg 的挂载目录")

    try:
        apps = sorted(mount.glob("*.app")) or sorted(mount.rglob("*.app"))
        if not apps:
            raise SilentInstallError("dmg 里没有找到 .app")
        source = apps[0]
        destination = Path("/Applications") / source.name
        # 给主程序留出退出时间，避免正在运行时替换失败
        time.sleep(2)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, symlinks=True)
    finally:
        subprocess.run(["hdiutil", "detach", str(mount)],
                       capture_output=True, check=False)


def install(path: str) -> None:
    """执行静默安装；失败抛 SilentInstallError，由助手转成通知。"""
    target = Path(path)
    if not target.is_file():
        raise SilentInstallError("更新包不存在，可能已被移动或删除")

    command = installer_command(str(target))
    if command is not None:
        try:
            result = subprocess.run(command, check=False)
        except OSError as e:
            raise SilentInstallError(f"无法启动安装程序：{e}") from e
        # 3010 / 1641 是 MSI 的「成功但需要重启」，这里由用户自己决定何时重启
        if result.returncode not in (0, 3010, 1641):
            raise SilentInstallError(f"安装程序退出码 {result.returncode}")
        return

    if sys.platform == "darwin" and target.suffix.lower() == ".dmg":
        try:
            _install_dmg(str(target))
        except (OSError, subprocess.SubprocessError) as e:
            raise SilentInstallError(f"复制应用失败：{e}") from e
        return
    raise SilentInstallError(f"{target.suffix or '未知格式'} 更新包不支持静默安装")


def _notify(title: str, body: str) -> None:
    """完成通知走系统通道；Windows 的托盘气泡要事件循环才能送达。"""
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from app.services.notify import send

    send(title, body)
    QTimer.singleShot(1200, app.quit)
    app.exec()


def install_and_notify(path: str) -> int:
    """隐藏入口：装完后自动打开新版本；失败也给出可执行的下一步。"""
    try:
        install(path)
    except SilentInstallError as e:
        _notify("更新未完成", f"{e}。请到「系统配置 - 关于项目」手动安装。")
        return 1
    if sys.platform == "darwin" and _relaunch_macos():
        return 0
    # 自动打开失败（或非 mac）：至少让用户知道该手动打开
    _notify("更新已完成", "智慧团委已更新，重启应用后生效。")
    return 0
