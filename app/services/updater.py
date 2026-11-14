"""版本检查与更新包下载。

数据来源固定为 GitHub 仓库 IsSmallPigPig/Smart-YLC：
  * 有 Release 时读 /releases/latest，取 tag、说明和安装包
  * 没有 Release 时读 main 分支的 app/__init__.py 版本号，下载源码压缩包
下载可经 ghproxy.com 加速；加速失败会明确报出原因，由调用方决定是否回退直连。
本模块不含 Qt 依赖，网络失败一律抛 UpdateError，不静默返回空结果。
"""

from __future__ import annotations

import json
import os
import platform as platform_module
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app import __version__

REPO = "IsSmallPigPig/Smart-YLC"
REPO_URL = "https://github.com/IsSmallPigPig/Smart-YLC.git"
REPO_PAGE = "https://github.com/" + REPO
AUTHOR_URL = "https://github.com/IsSmallPigPig"
ISSUES_URL = REPO_PAGE + "/issues"
GH_PROXY = "https://ghproxy.com/"
BRANCH = "main"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
VERSION_RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/app/__init__.py"
SOURCE_ZIP = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
UPDATE_DIR = os.path.join(os.path.expanduser("~"), ".gd12355-helper", "updates")
TIMEOUT = 8
USER_AGENT = f"Smart-YLC/{__version__} (+{REPO_PAGE})"

DEFAULT_AUTO_CHECK = True
DEFAULT_PROXY = "ghproxy"
PROXY_LABELS = ("ghproxy.com 加速", "直连 GitHub")


class UpdateError(Exception):
    """版本检查或下载失败，message 里带具体 URL 与原因。"""


@dataclass
class UpdateInfo:
    version: str
    notes: str
    download_url: str
    filename: str
    source: str  # release | branch


def parse_version(text: str) -> tuple[int, ...]:
    """'0.2.2-qt' / 'v1.3' → (0, 2, 2) / (1, 3, 0)，忽略后缀。"""
    nums = [int(n) for n in re.findall(r"\d+", text or "")]
    return tuple((nums + [0, 0, 0])[:3])


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def proxy_url(url: str, use_proxy: bool = True) -> str:
    """ghproxy.com 用法：把完整 GitHub / raw 链接跟在代理根路径后面。"""
    if not use_proxy:
        return url
    return GH_PROXY + url


MACOS_KEYCHAINS = (
    "/System/Library/Keychains/SystemRootCertificates.keychain",
    "/Library/Keychains/System.keychain",
)


def _ssl_context():
    """macOS 上 Python 不读系统钥匙串，本机安装的根证书会缺失。

    实测本机 API 直连报 CERTIFICATE_VERIFY_FAILED（curl 正常，因为它读钥匙串）。
    这里把系统信任的根证书补进 SSLContext，证书校验保持开启；其它平台沿用
    urllib 的默认验证。
    """
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(
            ["/usr/bin/security", "find-certificate", "-a", "-p", *MACOS_KEYCHAINS],
            capture_output=True, timeout=8, check=True).stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise UpdateError(f"读取 macOS 系统根证书失败：{e}") from e
    if len(out) < 1000:
        raise UpdateError("macOS 系统根证书为空，无法建立安全连接")
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cadata=out.decode("ascii", "ignore"))
    return ctx


def _urlopen(req, timeout):
    ctx = _ssl_context()
    if ctx is None:
        return urllib.request.urlopen(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _read(url: str, timeout: int = TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/vnd.github+json"})
    try:
        with _urlopen(req, timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", None) or getattr(e, "code", None) or str(e)
        raise UpdateError(f"{url} 请求失败：{reason}") from e


INSTALLABLE = (".zip", ".dmg", ".pkg", ".exe", ".msi")


def _pick_asset(assets: list[dict], platform: str | None = None) -> dict | None:
    """按平台挑安装包：macOS 优先 dmg，Windows 优先 exe，其它平台优先 zip。

    Release 工作流会同时上传 dmg 和 exe，GitHub 返回顺序不保证，不能取第一个；
    macOS 的 dmg 还分 arm64 / x86_64 两份，要挑和本机芯片匹配的那份。
    """
    p = platform or sys.platform
    if p == "darwin":
        prefs = (".dmg", ".pkg", ".zip")
    elif p == "win32":
        prefs = (".exe", ".msi", ".zip")
    else:
        prefs = INSTALLABLE
    pool = [a for a in (assets or [])
            if str(a.get("name", "")).lower().endswith(INSTALLABLE)]
    # mac 上优先挑同架构的安装包：M 系列认 arm64，Intel 认 x86_64
    arch_marker = ("arm64" if platform_module.machine().startswith("arm") else "x86_64") \
        if p == "darwin" else ""
    for ext in prefs:
        for a in pool:
            name = str(a.get("name", "")).lower()
            if name.endswith(ext) and (not arch_marker or arch_marker in name):
                return a
        # 同扩展名里没有标着本机架构的（旧版 Release 只有一份 dmg），退回第一个
        for a in pool:
            if str(a.get("name", "")).lower().endswith(ext):
                return a
    return pool[0] if pool else None


def _release_info() -> UpdateInfo | None:
    """没有发布 Release 时返回 None；其它 HTTP 错误直接抛出，不吞掉。"""
    req = urllib.request.Request(RELEASES_API, headers={"User-Agent": USER_AGENT})
    try:
        with _urlopen(req, TIMEOUT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise UpdateError(f"GitHub Release 接口返回 {e.code}：{e.reason}") from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise UpdateError(f"GitHub Release 接口读取失败：{e}") from e

    tag = str(data.get("tag_name") or data.get("name") or "")
    if not tag:
        return None
    asset = _pick_asset(data.get("assets") or [])
    if asset:
        url = str(asset["browser_download_url"])
        filename = str(asset["name"])
    else:
        url = str(data.get("zipball_url") or f"{REPO_PAGE}/archive/refs/tags/{tag}.zip")
        filename = f"Smart-YLC-{tag}.zip"
    notes = (str(data.get("body") or "").strip() or f"发布版本 {tag}")
    return UpdateInfo(tag, notes[:300], url, filename, "release")


def _branch_info() -> UpdateInfo:
    text = _read(VERSION_RAW).decode("utf-8", "replace")
    m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", text)
    if not m:
        raise UpdateError(f"{VERSION_RAW} 里没有找到 __version__")
    version = m.group(1)
    return UpdateInfo(
        version=version,
        notes=f"{BRANCH} 分支最新源码，下载后手动替换应用文件",
        download_url=SOURCE_ZIP,
        filename=f"Smart-YLC-{version}.zip",
        source="branch",
    )


def check_update() -> UpdateInfo | None:
    """返回可更新的版本信息；已是最新返回 None；失败抛 UpdateError。

    版本检查走直连（api.github.com / raw.githubusercontent.com 响应快），
    ghproxy.com 只用于下载更新包，避免开机检查被不可用的代理拖住。"""
    info = _release_info() or _branch_info()
    return info if is_newer(info.version) else None


def download(info: UpdateInfo, use_proxy: bool = True, progress=None, cancel=None) -> str:
    """下载更新包到 ~/.gd12355-helper/updates/，返回本地路径。

    progress(0..100) 逐块回调；cancel() 返回 True 时中止并删除临时文件。
    临时文件 .part + os.replace 原子落地，中途失败不会留下半个包。
    """
    url = proxy_url(info.download_url, use_proxy)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    folder = Path(UPDATE_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / info.filename
    tmp = folder / (info.filename + ".part")
    try:
        with _urlopen(req, TIMEOUT * 2) as resp, open(tmp, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(99, int(done * 100 / total)))
                if cancel and cancel():
                    raise UpdateError("下载已取消")
        os.replace(tmp, target)
        if progress:
            progress(100)
        return str(target)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        tmp.unlink(missing_ok=True)
        reason = getattr(e, "reason", None) or getattr(e, "code", None) or str(e)
        raise UpdateError(f"{'加速下载' if use_proxy else '直连下载'}失败：{reason}（{url}）") from e
    except UpdateError:
        tmp.unlink(missing_ok=True)
        raise
