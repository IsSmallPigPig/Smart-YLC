"""版本检查与更新包下载。

数据来源固定为 GitHub 仓库 IsSmallPigPig/Smart-YLC：
  * 有 Release 时读 /releases/latest，取 tag、说明和安装包
  * 没有 Release 时读 main 分支的 app/__init__.py 版本号，下载源码压缩包
下载可经公开镜像站（MIRRORS，逐个回退）；失败会明确报出原因，
由调用方决定是否换下一个站。镜像源有三种选择：自动（先测速、按延迟排队）、
指定某一个站、自定义站址。老 ghproxy.com 域名已停服，不再收录。
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
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app import BUILD_TARGET_ARCH, BUILD_TARGET_OS, __version__

REPO = "IsSmallPigPig/Smart-YLC"
REPO_URL = "https://github.com/IsSmallPigPig/Smart-YLC.git"
REPO_PAGE = "https://github.com/" + REPO
AUTHOR_URL = "https://github.com/IsSmallPigPig"
ISSUES_URL = REPO_PAGE + "/issues"
BRANCH = "main"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
VERSION_RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/app/__init__.py"
SOURCE_ZIP = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
UPDATE_DIR = os.path.join(os.path.expanduser("~"), ".gd12355-helper", "updates")
TIMEOUT = 8
USER_AGENT = f"Smart-YLC/{__version__} (+{REPO_PAGE})"

DEFAULT_AUTO_CHECK = True

# 镜像站：公开加速站按「站址 + 原始 GitHub 链接」拼接，直连前缀为空。
# 顺序即回退优先级，最后一条必须是直连兜底。站址按社区整理的公开清单维护
# （2026-11 核对），加速站生命周期普遍不长，失效了改这一处即可。
MIRRORS = (
    {"key": "gh-proxy.com", "label": "gh-proxy.com", "prefix": "https://gh-proxy.com/"},
    {"key": "gh-proxy.org", "label": "gh-proxy.org", "prefix": "https://gh-proxy.org/"},
    {"key": "cdn.gh-proxy.org", "label": "cdn.gh-proxy.org", "prefix": "https://cdn.gh-proxy.org/"},
    {"key": "edgeone.gh-proxy.org", "label": "edgeone.gh-proxy.org",
     "prefix": "https://edgeone.gh-proxy.org/"},
    {"key": "hk.gh-proxy.org", "label": "hk.gh-proxy.org", "prefix": "https://hk.gh-proxy.org/"},
    {"key": "ghfast.top", "label": "ghfast.top", "prefix": "https://ghfast.top/"},
    {"key": "ghfile.geekertao.top", "label": "ghfile.geekertao.top",
     "prefix": "https://ghfile.geekertao.top/"},
    {"key": "gh.chjina.com", "label": "gh.chjina.com", "prefix": "https://gh.chjina.com/"},
    {"key": "gh.monlor.com", "label": "gh.monlor.com", "prefix": "https://gh.monlor.com/"},
    {"key": "gh.jasonzeng.dev", "label": "gh.jasonzeng.dev", "prefix": "https://gh.jasonzeng.dev/"},
    {"key": "ghproxy.monkeyray.net", "label": "ghproxy.monkeyray.net",
     "prefix": "https://ghproxy.monkeyray.net/"},
    {"key": "github.ednovas.xyz", "label": "github.ednovas.xyz",
     "prefix": "https://github.ednovas.xyz/"},
    {"key": "gh.nxnow.top", "label": "gh.nxnow.top", "prefix": "https://gh.nxnow.top/"},
    {"key": "ghproxy.cxkpro.top", "label": "ghproxy.cxkpro.top",
     "prefix": "https://ghproxy.cxkpro.top/"},
    {"key": "fastgit.cc", "label": "fastgit.cc", "prefix": "https://fastgit.cc/"},
    {"key": "gh.zwy.one", "label": "gh.zwy.one", "prefix": "https://gh.zwy.one/"},
    {"key": "gitproxy.mrhjx.cn", "label": "gitproxy.mrhjx.cn", "prefix": "https://gitproxy.mrhjx.cn/"},
    {"key": "github.boki.moe", "label": "github.boki.moe", "prefix": "https://github.boki.moe/"},
    {"key": "gh.xxooo.cf", "label": "gh.xxooo.cf", "prefix": "https://gh.xxooo.cf/"},
    {"key": "gh.llkk.cc", "label": "gh.llkk.cc", "prefix": "https://gh.llkk.cc/"},
    {"key": "wget.la", "label": "wget.la", "prefix": "https://wget.la/"},
)
DIRECT = {"key": "direct", "label": "直连 GitHub", "prefix": ""}
GATEWAYS = MIRRORS + (DIRECT,)

# 镜像源选择：自动（测速后选最快的）/ 指定某个站 / 自定义站址
AUTO = "auto"
CUSTOM = "custom"
DEFAULT_MODE = AUTO

# 老配置里的写法映射到新 key：ghproxy.com 域名已停服，旧值一律指向首站
LEGACY_KEYS = {"gh-proxy": "gh-proxy.com", "ghproxy": "gh-proxy.com"}

# 测速用的探测目标：版本文件很小，能同时验证「站通」与「拿到的是对的文件」
PROBE_URL = VERSION_RAW
PROBE_TIMEOUT = 4
PROBE_TTL = 600
_PROBE: dict[str, tuple[int | None, str]] = {}
_PROBE_AT = 0.0


def gateway_by_key(key: str) -> dict | None:
    key = LEGACY_KEYS.get(key or "", key)
    for g in GATEWAYS:
        if g["key"] == key:
            return g
    return None


def gateway_keys() -> tuple[str, ...]:
    return tuple(g["key"] for g in GATEWAYS)


def gateway_labels() -> tuple[str, ...]:
    return tuple(g["label"] for g in GATEWAYS)


def normalize_prefix(prefix: str) -> str:
    """把用户填的站址补成可用前缀：缺协议补 https，缺尾斜杠补上。"""
    text = (prefix or "").strip()
    if not text:
        return ""
    if not text.startswith(("http://", "https://")):
        text = "https://" + text
    return text if text.endswith("/") else text + "/"


def custom_gateway(prefix: str) -> dict | None:
    """自定义站址当作一个镜像站；填得没法用就返回 None。"""
    url = normalize_prefix(prefix)
    if not url:
        return None
    host = urllib.parse.urlsplit(url).netloc
    return {"key": CUSTOM, "label": host or url, "prefix": url}


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


def proxy_url(url: str, gateway: dict | str | bool = True) -> str:
    """把原始 GitHub 链接拼到镜像站前缀后面；直连（空前缀/False）原样返回。

    gateway 可以传镜像站 dict、key 字符串，或兼容旧调用的 True/False。"""
    if isinstance(gateway, dict):
        prefix = gateway.get("prefix", "")
    elif isinstance(gateway, str):
        g = gateway_by_key(gateway)
        prefix = (g or DIRECT)["prefix"]
    else:
        prefix = GATEWAYS[0]["prefix"] if gateway else ""
    return prefix + url if prefix else url


def _as_gateway(gateway) -> dict:
    """把 dict / key / 旧式 True/False 收敛成一条镜像站记录。"""
    if isinstance(gateway, dict):
        return gateway
    if isinstance(gateway, str):
        found = gateway_by_key(gateway)
        if found:
            return found
        order = resolve_order(gateway)
        return order[0]
    return GATEWAYS[0] if gateway else DIRECT


def _probe_rank(gw: dict) -> tuple[int, int]:
    """排序键：可用的按延迟升序，限流的次之，真的不通的垫后。"""
    ms, reason = _PROBE.get(gw["key"], (None, ""))
    if ms is not None:
        return (0, ms)
    return (1, 0) if is_rate_limited(reason) else (2, 0)


def is_rate_limited(reason: str) -> bool:
    """站是通的、只是嫌请求太密（GitHub raw 常见 429）。

    这类站下载安装包通常没问题，不能当成失效站踢到队尾——排在实测可用的
    站后面即可。"""
    text = (reason or "").lower()
    return "429" in text or "too many requests" in text


def ranked_gateways() -> list[dict]:
    """按最近一次测速排序；没测过就按清单顺序（直连排最后兜底）。"""
    if not _PROBE:
        return list(GATEWAYS)
    return sorted(GATEWAYS, key=_probe_rank)


def probe_mirror(gw: dict, ctx=None, timeout: float = PROBE_TIMEOUT) -> int:
    """量一个镜像站拿文件要多久（毫秒）；不通就抛 UpdateError。"""
    url = proxy_url(PROBE_URL, gw)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    start = time.perf_counter()
    try:
        with _urlopen(req, timeout, ctx) as resp:
            resp.read(256)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", None) or getattr(e, "code", None) or str(e)
        raise UpdateError(f"{gw['label']} 不可用：{reason}") from e
    return max(1, int((time.perf_counter() - start) * 1000))


def remember_probe(key: str, ms: int | None, reason: str = "") -> None:
    """记下一次测速结果；UI 逐条回填延迟，下载时也按它排队。"""
    global _PROBE_AT
    _PROBE[key] = (ms, reason)
    _PROBE_AT = time.time()


def probe_result(key: str) -> tuple[int | None, str]:
    """某个站最近一次测速结果：(毫秒, 失败原因)；没测过是 (None, "")。"""
    return _PROBE.get(key, (None, ""))


def probe_age() -> float:
    """上次测速距今多少秒；从没测过返回一个很大的数。"""
    return time.time() - _PROBE_AT if _PROBE_AT else 1e9


def probe_fresh() -> bool:
    return _PROBE_AT > 0 and probe_age() < PROBE_TTL


def resolve_order(mode: str = DEFAULT_MODE, custom_prefix: str = "") -> list[dict]:
    """按镜像源选择给出下载尝试顺序（首个为目标站，其余依次兜底）。

    自动：先测速（有十分钟内的结果就直接用），可用的按延迟升序排在前面。
    自定义：用户填的站址排第一，其余镜像站依次兜底。
    指定某个站：它排第一。任何情况下直连都在兜底序列里。
    """
    if mode == CUSTOM:
        custom = custom_gateway(custom_prefix)
        if custom:
            rest = [g for g in GATEWAYS if g["prefix"] != custom["prefix"]]
            return [custom] + rest
        mode = AUTO
    if mode == AUTO:
        if not _PROBE:
            probe_all()
        return ranked_gateways()
    head = gateway_by_key(mode)
    if head is None:
        return ranked_gateways() if _PROBE else list(GATEWAYS)
    return [head] + [g for g in GATEWAYS if g["key"] != head["key"]]


def probe_all(targets=None, workers: int = 8) -> dict[str, tuple[int | None, str]]:
    """并发测速全部镜像站，结果写进缓存并返回。单个站失败不影响其它站。

    用线程池而不是顺序请求：二十多个站顺序量，光是超时就要按分钟算。
    """
    from concurrent.futures import ThreadPoolExecutor

    gws = list(targets if targets is not None else GATEWAYS)
    try:
        ctx = _ssl_context()
    except UpdateError:
        ctx = None  # 证书读不出来时让每个请求自己报错，别整批失败

    def _one(gw):
        try:
            return gw["key"], probe_mirror(gw, ctx), ""
        except UpdateError as e:
            return gw["key"], None, str(e)
        except Exception as e:      # noqa: BLE001 - 测速失败一律记成不可用
            return gw["key"], None, str(e) or "不可用"

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for key, ms, reason in pool.map(_one, gws):
            remember_probe(key, ms, reason)
    return dict(_PROBE)


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


def _urlopen(req, timeout, ctx=None):
    ctx = _ssl_context() if ctx is None else ctx
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


INSTALLABLE = (".zip", ".dmg", ".pkg", ".exe", ".msi", ".deb")


def _pick_asset(assets: list[dict], platform: str | None = None) -> dict | None:
    """按平台挑安装包：macOS 优先 dmg，Windows 优先 exe，其它平台优先 zip。

    Release 工作流会同时上传多个平台的产物，GitHub 返回顺序不保证，不能取第一个。
    同一平台还分架构：macOS 有 arm64 / x86_64 的 dmg，Windows 有 Win-x64 /
    Win-x86 / Win7-x64 / Win7-x86 的 setup，必须挑和本机系统与位数匹配的那份——
    在 64 位 Windows 上装到 32 位包不致命，但在 32 位系统上装 64 位包会直接失败。
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
    markers = _arch_markers(p)
    for ext in prefs:
        # 先按架构标记挑；同一档里优先没有 win7 前缀的（Win10+ 机器上别拿到旧系统包）
        for marker in markers:
            for a in pool:
                name = str(a.get("name", "")).lower()
                if name.endswith(ext) and marker in name:
                    return a
        # 同扩展名里没有标着本机架构的（旧版 Release 只有一份），退回第一个
        for a in pool:
            if str(a.get("name", "")).lower().endswith(ext):
                return a
    return pool[0] if pool else None


def _arch_markers(p: str) -> tuple[str, ...]:
    """本机该优先匹配的文件名片段，按优先级排列。

    来源优先级：
      1. 打包时写进代码的构建目标（BUILD_TARGET_OS / BUILD_TARGET_ARCH）——
         装的是哪一份，升级就拿同一份，不靠运行时系统反推；
      2. 源码运行时没有构建目标，按当前系统与位数推断。

    macOS：M 系列认 arm64，Intel 认 x86_64（沿用历史命名）。
    Windows：产物名形如 SmartYouth-Windows10-x64-setup.exe。先认同名变体，
    再退到同世代另一位宽（Win10 x64 机器能跑 Win10 x86 包），绝不跨世代
    往下退——Win7 包在 Win10 上能跑，但拿它升级等于把系统要求降级。"""
    if p == "darwin":
        if BUILD_TARGET_ARCH == "arm64":
            return ("arm64",)
        if BUILD_TARGET_ARCH == "x64":
            return ("x86_64",)
        return ("arm64",) if platform_module.machine().startswith("arm") else ("x86_64",)
    if p == "win32":
        generation, arch = _windows_variant()
        other = "x86" if arch == "x64" else "x64"
        return (f"{generation}-{arch}", f"{generation}-{other}")
    return ()


def _windows_variant() -> tuple[str, str]:
    """(windows10|windows7, x64|x86)：先看构建目标，源码运行再按系统推断。"""
    generation = ""
    if BUILD_TARGET_OS in ("Windows10", "Windows7"):
        generation = BUILD_TARGET_OS.lower()
    if not generation:
        generation = "windows7" if _is_windows7() else "windows10"
    arch = BUILD_TARGET_ARCH if BUILD_TARGET_ARCH in ("x64", "x86") else ""
    if not arch:
        arch = "x64" if platform_module.architecture()[0] == "64bit" else "x86"
    return generation, arch


def _is_windows7() -> bool:
    """当前是否是 Windows 7（build < 10.0）。非 Windows 恒为 False。"""
    if not sys.platform.startswith("win"):
        return False
    try:
        return sys.getwindowsversion().major < 10
    except Exception:
        return False


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


def launch_installer(path: str) -> str:
    """把下载好的更新包交给系统执行。

    返回 "run" 表示安装程序/镜像已经启动，"reveal" 表示这种包没有安装动作、
    只在文件管理器里定位（源码 zip）。Windows 的安装程序要替换正在运行的 exe，
    调用方启动后应当退出应用；macOS 的 dmg 只是挂载，主程序可以继续开着。
    """
    target = Path(path)
    if not target.exists():
        raise UpdateError("更新包不存在，可能已被移动或删除")

    suffix = target.suffix.lower()
    try:
        if sys.platform == "win32" and suffix in (".exe", ".msi"):
            os.startfile(str(target))  # noqa: S606 - 用户主动下载并确认的安装包
            return "run"
        if sys.platform == "darwin" and suffix in (".dmg", ".pkg"):
            _spawn("open", target)
            return "run"
        if sys.platform not in ("win32", "darwin") and suffix in (".appimage", ".deb", ".rpm"):
            _spawn("xdg-open", target)
            return "run"
        _reveal(target)
        return "reveal"
    except OSError as e:
        raise UpdateError(f"无法启动安装程序：{e}") from e


def _spawn(command: str, target: Path) -> None:
    subprocess.Popen([command, str(target)])


def _reveal(target: Path) -> None:
    """在文件管理器里定位文件；Windows 打开所在文件夹（explorer 的 /select 在
    未加引号的参数里不可靠）。"""
    if sys.platform == "darwin":
        _spawn("open", target.parent)
    elif sys.platform == "win32":
        os.startfile(str(target.parent))  # noqa: S606 - 只打开文件夹
    else:
        _spawn("xdg-open", target.parent)


def check_update() -> UpdateInfo | None:
    """返回可更新的版本信息；已是最新返回 None；失败抛 UpdateError。

    版本检查走直连（api.github.com / raw.githubusercontent.com 响应快），
    镜像站只用于下载更新包，避免开机检查被不可用的代理拖住。"""
    info = _release_info() or _branch_info()
    return info if is_newer(info.version) else None


def download(info: UpdateInfo, gateway: dict | str | bool = True, progress=None, cancel=None) -> str:
    """下载更新包到 ~/.gd12355-helper/updates/，返回本地路径。

    gateway 决定走哪个镜像站（dict/key/模式名，或兼容旧调用的 True/False）。
    progress(0..100) 逐块回调；cancel() 返回 True 时中止并删除临时文件。
    临时文件 .part + os.replace 原子落地，中途失败不会留下半个包。
    """
    gw = _as_gateway(gateway)
    url = proxy_url(info.download_url, gw)
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
        via = f"经 {gw['label']} " if gw["prefix"] else "直连"
        raise UpdateError(f"{via}下载失败：{reason}（{url}）") from e
    except UpdateError:
        tmp.unlink(missing_ok=True)
        raise
