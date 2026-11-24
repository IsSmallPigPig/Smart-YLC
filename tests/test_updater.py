"""更新服务：版本比较、GitHub Release/分支回退、多网关加速与逐通道回退、下载原子写。

网络全部用假 urlopen 替身，测试不发真实请求。
"""
import io
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app import __version__
from app.services import updater, update_check, store

_REAL_SSL_CONTEXT = updater._ssl_context

# 默认加速站前缀（原 GH_PROXY 常量位置，测试里当第一个网关用）
GH_PROXY = updater.GATEWAYS[0]["prefix"]


@pytest.fixture(autouse=True)
def _no_keychain_lookup(monkeypatch):
    """网络请求测试不真的调用 macOS security 命令。证书专项用例直接调原函数。"""
    monkeypatch.setattr(updater, "_ssl_context", lambda: None)


class FakeResponse:
    """够用的 urlopen 返回体：支持 with、分块 read、响应头。"""

    def __init__(self, body: bytes, headers=None):
        self._buf = io.BytesIO(body)
        self.headers = headers or {}

    def read(self, n=-1):
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_urlopen(monkeypatch, routes: dict) -> list[str]:
    """routes: {url: bytes | Exception}；返回本次请求过的 URL 列表。"""
    seen = []

    def _open(req, timeout=None):
        url = getattr(req, "full_url", req)
        seen.append(url)
        if url not in routes:
            raise AssertionError(f"未预期的请求：{url}")
        item = routes[url]
        if isinstance(item, Exception):
            raise item
        header = {"Content-Length": str(len(item))}
        return FakeResponse(item, header)

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    return seen


def release_payload(tag: str, asset: str | None = None) -> bytes:
    data = {"tag_name": tag, "body": "修复若干问题"}
    if asset:
        data["assets"] = [{"name": asset,
                           "browser_download_url": f"https://github.com/x/y/releases/download/{tag}/{asset}"}]
    return json.dumps(data).encode("utf-8")


def release_with_assets(tag: str, names: list[str]) -> bytes:
    data = {
        "tag_name": tag,
        "body": "修复若干问题",
        "assets": [{"name": n,
                    "browser_download_url": f"https://github.com/x/y/releases/download/{tag}/{n}"}
                   for n in names],
    }
    return json.dumps(data).encode("utf-8")


def branch_payload(version: str) -> bytes:
    return f'__version__ = "{version}"\n'.encode("utf-8")


def info(version="9.9.9", url="https://github.com/IsSmallPigPig/Smart-YLC/archive/refs/heads/main.zip"):
    return updater.UpdateInfo(version, "notes", url, f"Smart-YLC-{version}.zip", "branch")


# ---------------------------------------------------------------- macOS 根证书

def test_ssl_context_off_macos_uses_urllib_defaults(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "linux")
    assert _REAL_SSL_CONTEXT() is None


def test_ssl_context_loads_system_keychain_roots(monkeypatch):
    """Python 不读 macOS 钥匙串，必须把系统信任根证书补进 SSLContext。"""
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    calls = []

    class FakeCtx:
        verify_mode = ssl.CERT_REQUIRED

        def load_verify_locations(self, cadata=None):
            self.cadata = cadata

    ctx = FakeCtx()
    monkeypatch.setattr(updater.ssl, "create_default_context", lambda: ctx)

    def _run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"X" * 1500, stderr=b"")

    monkeypatch.setattr(updater.subprocess, "run", _run)
    assert _REAL_SSL_CONTEXT() is ctx
    assert ctx.cadata == "X" * 1500
    assert all(k in calls[0] for k in updater.MACOS_KEYCHAINS)
    assert calls[0][0] == "/usr/bin/security"


def test_ssl_context_reports_keychain_failure(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "darwin")

    def _boom(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "security")

    monkeypatch.setattr(updater.subprocess, "run", _boom)
    with pytest.raises(updater.UpdateError, match="根证书"):
        _REAL_SSL_CONTEXT()


def test_ssl_context_rejects_empty_bundle(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    monkeypatch.setattr(
        updater.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b""))
    with pytest.raises(updater.UpdateError, match="为空"):
        _REAL_SSL_CONTEXT()


def test_urlopen_passes_ssl_context(monkeypatch):
    ctx = object()
    monkeypatch.setattr(updater, "_ssl_context", lambda: ctx)
    seen = {}

    def _open(req, timeout=None, **kwargs):
        seen.update(kwargs)
        return FakeResponse(b"ok")

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    assert updater._read("https://example.com/x") == b"ok"
    assert seen["context"] is ctx


# ---------------------------------------------------------------- 版本与代理

@pytest.mark.parametrize("text,expected", [
    ("0.2.2-qt", (0, 2, 2)),
    ("v1.3", (1, 3, 0)),
    ("2.0", (2, 0, 0)),
    ("1.2.3.4-beta", (1, 2, 3)),
    ("", (0, 0, 0)),
])
def test_parse_version(text, expected):
    assert updater.parse_version(text) == expected


def test_is_newer_only_when_remote_version_is_higher():
    assert updater.is_newer("0.3.0", "0.2.2-qt")
    assert not updater.is_newer("0.2.2-qt", "0.2.2-qt")
    assert not updater.is_newer("0.1.9", "0.2.2-qt")
    assert not updater.is_newer(__version__)


def test_proxy_url_prefixes_full_github_link():
    assert updater.proxy_url(updater.SOURCE_ZIP) == GH_PROXY + updater.SOURCE_ZIP
    assert updater.proxy_url(updater.SOURCE_ZIP, False) == updater.SOURCE_ZIP
    assert updater.REPO == "IsSmallPigPig/Smart-YLC"
    assert updater.REPO_URL.endswith("IsSmallPigPig/Smart-YLC.git")


# ---------------------------------------------------------------- 检查更新

def test_release_is_preferred_and_uses_asset(monkeypatch):
    seen = fake_urlopen(monkeypatch, {
        updater.RELEASES_API: release_payload("v9.1.0", "Smart-YLC.dmg"),
    })
    result = updater.check_update()
    assert seen == [updater.RELEASES_API]
    assert result.source == "release"
    assert result.version == "v9.1.0"
    assert result.filename == "Smart-YLC.dmg"
    assert result.download_url.endswith("/Smart-YLC.dmg")


def test_release_without_asset_falls_back_to_zipball(monkeypatch):
    fake_urlopen(monkeypatch, {
        updater.RELEASES_API: release_payload("v3.0.0"),
    })
    result = updater.check_update()
    assert result.filename == "Smart-YLC-v3.0.0.zip"
    assert result.download_url.endswith("v3.0.0.zip")


@pytest.mark.parametrize("platform,expected", [
    ("darwin", "SmartYouth-macOS.dmg"),
    ("win32", "SmartYouth-Windows.exe"),
    ("linux", "SmartYouth-source.zip"),
])
def test_pick_asset_matches_platform(platform, expected):
    """Release 工作流同时上传 dmg/exe，顺序不保证，必须按平台挑。"""
    assets = [
        {"name": "SmartYouth-Windows.exe", "browser_download_url": "u1"},
        {"name": "SmartYouth-macOS.dmg", "browser_download_url": "u2"},
        {"name": "SmartYouth-source.zip", "browser_download_url": "u3"},
    ]
    assert updater._pick_asset(assets, platform)["name"] == expected


def test_pick_asset_ignores_non_installable_files():
    assert updater._pick_asset([{"name": "README.md"}], "darwin") is None


@pytest.mark.parametrize("machine,expected", [
    ("arm64", "SmartYouth-macOS-arm64.dmg"),
    ("x86_64", "SmartYouth-macOS-x86_64.dmg"),
])
def test_pick_asset_prefers_matching_mac_arch(monkeypatch, machine, expected):
    """macOS 的 dmg 分 arm64/x86_64 两份，必须挑和本机芯片匹配的那份。"""
    monkeypatch.setattr(updater.platform_module, "machine", lambda: machine)
    assets = [
        {"name": "SmartYouth-macOS-x86_64.dmg", "browser_download_url": "u1"},
        {"name": "SmartYouth-macOS-arm64.dmg", "browser_download_url": "u2"},
    ]
    assert updater._pick_asset(assets, "darwin")["name"] == expected


def test_release_on_macos_never_downloads_windows_exe(monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    fake_urlopen(monkeypatch, {
        updater.RELEASES_API: release_with_assets(
            "v9.9.9", ["SmartYouth-Windows.exe", "SmartYouth-macOS.dmg"]),
    })
    result = updater.check_update()
    assert result.filename == "SmartYouth-macOS.dmg"
    assert result.download_url.endswith("SmartYouth-macOS.dmg")


def test_release_with_only_non_installable_assets_uses_zipball(monkeypatch):
    fake_urlopen(monkeypatch, {
        updater.RELEASES_API: release_with_assets("v9.9.9", ["checksums.txt"]),
    })
    result = updater.check_update()
    assert result.filename == "Smart-YLC-v9.9.9.zip"


def test_release_tag_matches_local_version_without_self_update():
    """发布的 tag 与本地版本一致时，不能一直提示发现新版本。

    断言跟着当前版本走，不要写死版本号，否则每次发版都要改测试。"""
    assert not updater.is_newer(f"v{__version__}")
    assert not updater.is_newer("v0.0.1")


def test_no_release_falls_back_to_main_branch(monkeypatch):
    err = urllib.error.HTTPError(updater.RELEASES_API, 404, "Not Found", {}, None)
    seen = fake_urlopen(monkeypatch, {
        updater.RELEASES_API: err,
        updater.VERSION_RAW: branch_payload("9.4.0"),
    })
    result = updater.check_update()
    assert seen == [updater.RELEASES_API, updater.VERSION_RAW]
    assert result.source == "branch"
    assert result.version == "9.4.0"
    assert result.download_url == updater.SOURCE_ZIP


def test_checks_go_direct_not_through_proxy(monkeypatch):
    """开机检查不能被不可用的 ghproxy 拖住。"""
    err = urllib.error.HTTPError(updater.RELEASES_API, 404, "Not Found", {}, None)
    seen = fake_urlopen(monkeypatch, {
        updater.RELEASES_API: err,
        updater.VERSION_RAW: branch_payload("9.4.0"),
    })
    updater.check_update()
    assert all(not u.startswith(GH_PROXY) for u in seen)


def test_already_latest_returns_none(monkeypatch):
    fake_urlopen(monkeypatch, {
        updater.RELEASES_API: release_payload(__version__),
    })
    assert updater.check_update() is None


def test_non_404_http_error_is_not_swallowed(monkeypatch):
    err = urllib.error.HTTPError(updater.RELEASES_API, 403, "rate limited", {}, None)
    fake_urlopen(monkeypatch, {updater.RELEASES_API: err})
    with pytest.raises(updater.UpdateError) as e:
        updater.check_update()
    assert "403" in str(e.value)


def test_missing_version_in_raw_file_is_reported(monkeypatch):
    err = urllib.error.HTTPError(updater.RELEASES_API, 404, "Not Found", {}, None)
    fake_urlopen(monkeypatch, {
        updater.RELEASES_API: err,
        updater.VERSION_RAW: b"# no version here\n",
    })
    with pytest.raises(updater.UpdateError) as e:
        updater.check_update()
    assert updater.VERSION_RAW in str(e.value)


# ---------------------------------------------------------------- 下载

def test_download_is_atomic_and_reports_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "UPDATE_DIR", str(tmp_path))
    body = b"x" * 1000
    seen = fake_urlopen(monkeypatch, {GH_PROXY + updater.SOURCE_ZIP: body})
    progress = []
    path = updater.download(info(url=updater.SOURCE_ZIP), True, progress=progress.append)

    assert seen == [GH_PROXY + updater.SOURCE_ZIP]
    assert open(path, "rb").read() == body
    assert not os.path.exists(path + ".part")
    assert progress[-1] == 100


def test_direct_download_uses_original_url(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "UPDATE_DIR", str(tmp_path))
    seen = fake_urlopen(monkeypatch, {updater.SOURCE_ZIP: b"ok"})
    path = updater.download(info(url=updater.SOURCE_ZIP), False)
    assert seen == [updater.SOURCE_ZIP]
    assert open(path, "rb").read() == b"ok"


@pytest.mark.parametrize("failure", [
    urllib.error.URLError("connection reset"),
    urllib.error.HTTPError("u", 502, "bad gateway", {}, None),
])
def test_failed_download_cleans_part_file_and_reports_url(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(updater, "UPDATE_DIR", str(tmp_path))
    url = updater.SOURCE_ZIP
    fake_urlopen(monkeypatch, {GH_PROXY + url: failure})
    with pytest.raises(updater.UpdateError) as e:
        updater.download(info(url=url), True)
    assert "下载失败" in str(e.value)
    assert GH_PROXY + url in str(e.value)
    assert list(tmp_path.glob("*.part")) == []


def test_cancelled_download_leaves_no_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "UPDATE_DIR", str(tmp_path))
    fake_urlopen(monkeypatch, {updater.SOURCE_ZIP: b"y" * 300000})
    with pytest.raises(updater.UpdateError, match="取消"):
        updater.download(info(url=updater.SOURCE_ZIP), False, cancel=lambda: True)
    assert list(tmp_path.glob("*.part")) == []


# ---------------------------------------------------------------- 镜像源

@pytest.fixture(autouse=True)
def _reset_probe_cache(monkeypatch):
    """测速缓存是模块级状态，每个用例从零开始，避免互相影响。"""
    monkeypatch.setattr(updater, "_PROBE", {})
    monkeypatch.setattr(updater, "_PROBE_AT", 0.0)


def test_mirror_list_is_unique_and_ends_with_direct():
    keys = [g["key"] for g in updater.GATEWAYS]
    assert len(keys) == len(set(keys))
    assert updater.GATEWAYS[-1] is updater.DIRECT
    assert all(g["prefix"].startswith("https://") for g in updater.MIRRORS)


def test_legacy_gateway_keys_still_resolve():
    assert updater.gateway_by_key("gh-proxy") is updater.MIRRORS[0]
    assert updater.gateway_by_key("ghproxy") is updater.MIRRORS[0]
    assert updater.gateway_by_key("gh-proxy.com") is updater.MIRRORS[0]
    assert updater.gateway_by_key("no-such-mirror") is None


def test_normalize_prefix_fills_protocol_and_slash():
    assert updater.normalize_prefix("my.mirror.cn") == "https://my.mirror.cn/"
    assert updater.normalize_prefix(" http://a.cn/gh ") == "http://a.cn/gh/"
    assert updater.normalize_prefix("") == ""
    assert updater.custom_gateway("mirror.cn")["label"] == "mirror.cn"


def test_auto_mode_puts_fastest_mirror_first():
    updater.remember_probe("gh-proxy.com", 900)
    updater.remember_probe("gh.zwy.one", 120)
    updater.remember_probe("ghfast.top", None, "timed out")
    order = [g["key"] for g in updater.resolve_order(updater.AUTO)]
    assert order[0] == "gh.zwy.one"
    assert order.index("gh-proxy.com") < order.index("ghfast.top")  # 超时的垫后
    assert "direct" in order                                        # 永远留兜底


def test_rate_limited_mirror_outranks_dead_ones():
    """429 只是嫌请求太密，站是通的：排在实测可用的后面、真不通的前面。"""
    updater.remember_probe("gh-proxy.com", 900)
    updater.remember_probe("gh.xxooo.cf", None, "HTTP Error 429: Too Many Requests")
    updater.remember_probe("fastgit.cc", None, "The read operation timed out")
    order = [g["key"] for g in updater.resolve_order(updater.AUTO)]
    assert order.index("gh-proxy.com") < order.index("gh.xxooo.cf")
    assert order.index("gh.xxooo.cf") < order.index("fastgit.cc")
    assert updater.is_rate_limited("HTTP Error 429: Too Many Requests") is True
    assert updater.is_rate_limited("timed out") is False


def test_single_mirror_mode_starts_from_that_mirror():
    order = [g["key"] for g in updater.resolve_order("gh.zwy.one")]
    assert order[0] == "gh.zwy.one"
    assert order[-1] == "direct"
    assert len(order) == len(updater.GATEWAYS)


def test_custom_mirror_leads_the_order(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    update_check.set_custom_mirror("my.mirror.cn")
    order = updater.resolve_order(updater.CUSTOM, update_check.custom_mirror())
    assert order[0]["prefix"] == "https://my.mirror.cn/"
    assert order[-1]["key"] == "direct"


def test_empty_custom_prefix_falls_back_to_auto():
    updater.remember_probe("gh.zwy.one", 100)
    assert updater.resolve_order(updater.CUSTOM, "")[0]["key"] == "gh.zwy.one"


def test_probe_cache_expires():
    updater.remember_probe("gh-proxy.com", 500)
    assert updater.probe_fresh() is True
    assert updater.probe_result("gh-proxy.com") == (500, "")
    assert updater.probe_result("wget.la") == (None, "")


def test_probe_all_records_each_mirror(monkeypatch):
    """测速整轮跑完：通的记延迟，不通的记原因，不抛异常打断其他站。"""
    def _probe(gw, ctx=None, timeout=None):
        if gw["key"] == "wget.la":
            raise updater.UpdateError("wget.la 不可用：timed out")
        return 300

    monkeypatch.setattr(updater, "probe_mirror", _probe)
    updater.probe_all([updater.gateway_by_key("gh-proxy.com"), updater.gateway_by_key("wget.la")])
    assert updater.probe_result("gh-proxy.com") == (300, "")
    assert updater.probe_result("wget.la")[0] is None
    assert "timed out" in updater.probe_result("wget.la")[1]


# ---------------------------------------------------------------- Qt 回退

def test_download_worker_walks_the_ordered_chain(monkeypatch):
    """目标站失败后自动换下一个，最终成功；每次失败都发 fallback。"""
    from PyQt5.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication([])
    chain = [updater.gateway_by_key("gh-proxy.com"), updater.DIRECT]
    monkeypatch.setattr(updater, "resolve_order", lambda *a, **k: chain)
    calls = []

    def _download(inf, gw, **kwargs):
        calls.append(gw["key"])
        if gw["key"] != "direct":
            raise updater.UpdateError(f"{gw['label']} 不可用：连接被重置")
        return "/tmp/Smart-YLC.zip"

    monkeypatch.setattr(updater, "download", _download)
    worker = update_check.DownloadWorker(info(), "gh-proxy.com")
    fallbacks, done, failed = [], [], []
    worker.fallback.connect(fallbacks.append)
    worker.done.connect(done.append)
    worker.failed.connect(failed.append)
    worker.run()  # 直接跑 run，不启动真实线程

    assert calls == ["gh-proxy.com", "direct"]
    assert len(fallbacks) == 1 and "连接被重置" in fallbacks[0]
    assert done == ["/tmp/Smart-YLC.zip"] and failed == []


def test_download_worker_auto_mode_uses_measured_order(monkeypatch):
    """自动模式：按测速结果从最快的站开始，不用再问一遍走哪个。"""
    from PyQt5.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication([])
    updater.remember_probe("gh.zwy.one", 100)
    updater.remember_probe("gh-proxy.com", 900)
    seen = []
    monkeypatch.setattr(updater, "download",
                        lambda inf, gw, **kw: seen.append(gw["key"]) or "/tmp/Smart-YLC.zip")

    worker = update_check.DownloadWorker(info(), updater.AUTO)
    done = []
    worker.done.connect(done.append)
    worker.run()
    assert seen == ["gh.zwy.one"]
    assert done == ["/tmp/Smart-YLC.zip"]


def test_download_worker_all_mirrors_fail_reports_error(monkeypatch):
    from PyQt5.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(updater, "resolve_order",
                        lambda *a, **k: [updater.gateway_by_key("wget.la"), updater.DIRECT])

    def _download(inf, gw, **kwargs):
        raise updater.UpdateError(f"经 {gw['label']} 下载失败：超时")

    monkeypatch.setattr(updater, "download", _download)
    worker = update_check.DownloadWorker(info(), "wget.la")
    failed = []
    worker.failed.connect(failed.append)
    worker.run()
    assert len(failed) == 1 and "超时" in failed[0]


def test_check_worker_reports_failure_instead_of_crashing(monkeypatch):
    from PyQt5.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication([])

    def _boom():
        raise updater.UpdateError("网络不可达")

    monkeypatch.setattr(updater, "check_update", _boom)
    worker = update_check.CheckWorker()
    found, failed = [], []
    worker.found.connect(found.append)
    worker.failed.connect(failed.append)
    worker.run()
    assert found == [] and failed == ["网络不可达"]


# ---------------------------------------------------------------- 配置记录

def test_update_config_defaults_and_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    assert update_check.auto_check_enabled() is True
    assert update_check.mirror_mode() == updater.AUTO  # 默认自动选择
    assert "尚未检查" in update_check.last_check_text()

    update_check.set_auto_check(False)
    update_check.set_mirror_mode("direct")
    update_check.record("update", "9.9.9")
    assert update_check.auto_check_enabled() is False
    assert update_check.mirror_mode() == "direct"
    assert "9.9.9" in update_check.last_check_text()


def test_invalid_mirror_mode_falls_back_to_auto(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save({"updateProxy": "something-else"})
    assert update_check.mirror_mode() == updater.AUTO


def test_legacy_ghproxy_value_maps_to_first_mirror(tmp_path, monkeypatch):
    """老配置里存过 "ghproxy" / "gh-proxy"（旧 key），迁移到对应站。"""
    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save({"updateProxy": "ghproxy"})
    assert update_check.mirror_mode() == "gh-proxy.com"
    store.save({"updateProxy": "gh-proxy"})
    assert update_check.mirror_mode() == "gh-proxy.com"


def test_custom_mirror_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    assert update_check.custom_mirror() == ""
    update_check.set_custom_mirror("my.mirror.cn")
    assert update_check.custom_mirror() == "https://my.mirror.cn/"
    update_check.set_mirror_mode(updater.CUSTOM)
    assert update_check.mirror_mode() == updater.CUSTOM


def test_startup_check_respects_switch_off(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    update_check.set_auto_check(False)
    called = []
    monkeypatch.setattr(update_check, "CheckWorker", lambda: called.append(1) or None)
    update_check.check_on_startup(delay_ms=0)
    assert called == []


def test_launch_installer_rejects_missing_package(tmp_path):
    """包被别人删了/移走了：明确报错，不去调系统打开一个不存在的文件。"""
    with pytest.raises(updater.UpdateError, match="更新包不存在"):
        updater.launch_installer(str(tmp_path / "gone.dmg"))


def test_launch_installer_hands_package_to_system(tmp_path, monkeypatch):
    """能安装的包交给系统打开；源码 zip 只在文件管理器里定位。"""
    spawned = []
    monkeypatch.setattr(updater, "sys", type("S", (), {"platform": "darwin"}))
    monkeypatch.setattr(updater, "_spawn", lambda cmd, target: spawned.append((cmd, str(target))))
    monkeypatch.setattr(updater, "_reveal", lambda target: spawned.append(("reveal", str(target))))

    dmg = tmp_path / "SmartYouth-macOS-arm64.dmg"
    dmg.write_bytes(b"x")
    assert updater.launch_installer(str(dmg)) == "run"
    assert spawned == [("open", str(dmg))]

    zip_path = tmp_path / "smart-ylc-main.zip"
    zip_path.write_bytes(b"x")
    assert updater.launch_installer(str(zip_path)) == "reveal"


# ---------------------------------------------------------------- 静默安装

class _Signal:
    """假信号：测试里同步触发，不等 Qt 事件循环。"""

    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


def _fake_sys(platform: str, executable: str = "/usr/bin/python3", **flags):
    attrs = {"platform": platform, "executable": executable,
             "frozen": False, "__compiled__": False}
    attrs.update(flags)
    return type("S", (), attrs)()


def test_installer_command_uses_silent_flags(tmp_path, monkeypatch):
    from app.services import silent_update

    exe = tmp_path / "setup.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(silent_update, "sys", _fake_sys("win32"))
    cmd = silent_update.installer_command(str(exe))
    assert cmd[0] == str(exe)
    assert "/VERYSILENT" in cmd and "/NORESTART" in cmd
    assert "/SUPPRESSMSGBOXES" in cmd and "/CLOSEAPPLICATIONS" in cmd

    msi = tmp_path / "setup.msi"
    msi.write_bytes(b"x")
    assert silent_update.installer_command(str(msi))[:2] == ["msiexec", "/i"]
    assert "/qn" in silent_update.installer_command(str(msi))


def test_helper_command_reenters_same_entrypoint(monkeypatch):
    from app.services import silent_update

    monkeypatch.setattr(silent_update, "sys", _fake_sys("darwin"))
    assert silent_update.helper_command("/tmp/a.dmg") == [
        "/usr/bin/python3", "-m", "app.main", silent_update.FLAG, "/tmp/a.dmg"]
    monkeypatch.setattr(silent_update, "sys", _fake_sys(
        "darwin", executable="/opt/SmartYouth.app/Contents/MacOS/SmartYouth", frozen=True))
    assert silent_update.helper_command("/tmp/a.dmg") == [
        "/opt/SmartYouth.app/Contents/MacOS/SmartYouth",
        silent_update.FLAG, "/tmp/a.dmg"]


def test_start_launches_detached_helper(tmp_path, monkeypatch):
    """非 Windows：把带隐藏参数的助手拉起来就返回，助手负责安装。"""
    from app.services import silent_update

    pkg = tmp_path / "setup.exe"
    pkg.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(silent_update, "sys", _fake_sys("darwin"))
    monkeypatch.setattr(silent_update.subprocess, "Popen",
                        lambda cmd, **kwargs: calls.append((cmd, kwargs)))
    assert silent_update.start(str(pkg)) is True
    cmd, kwargs = calls[0]
    assert silent_update.FLAG in cmd
    assert str(pkg) in cmd
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert "start_new_session" in kwargs


def test_start_on_windows_only_launches_installer(tmp_path, monkeypatch):
    """Windows：只起安装器。重开由 installer.iss 的 [Run] 负责，这里再起一份
    就会多出一个进程（两个托盘图标、两套窗口）。"""
    from app.services import silent_update

    pkg = tmp_path / "setup.exe"
    pkg.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(silent_update, "sys", _fake_sys("win32", executable=str(pkg)))
    monkeypatch.setattr(silent_update.subprocess, "Popen",
                        lambda cmd, **kwargs: calls.append((cmd, kwargs)))

    assert silent_update.start(str(pkg)) is True
    cmd, kwargs = calls[0]
    assert cmd[0] == str(pkg)
    assert "/VERYSILENT" in cmd and "/NORESTART" in cmd
    assert kwargs["stdout"] is subprocess.DEVNULL


def test_relaunch_skips_source_build(monkeypatch):
    """源码版的解释器也是带 .exe 的进程，不能因此把开发机也重开一遍。"""
    from app.services import silent_update

    calls = []
    monkeypatch.setattr(silent_update, "sys",
                        _fake_sys("win32", executable="/usr/bin/python3"))
    monkeypatch.setattr(silent_update.subprocess, "Popen",
                        lambda cmd, **kwargs: calls.append(cmd))
    assert silent_update._relaunch_windows() is False
    assert calls == []


def test_relaunch_starts_packaged_app_without_console(monkeypatch):
    """msi 装完直接起自家 exe：窗口程序不会带出控制台，也不经过 cmd。"""
    from app.services import silent_update

    calls = []
    monkeypatch.setattr(silent_update, "sys", _fake_sys(
        "win32", executable="/opt/智慧团委.exe", frozen=True))
    monkeypatch.setattr(silent_update.subprocess, "Popen",
                        lambda cmd, **kwargs: calls.append((cmd, kwargs)))

    assert silent_update._relaunch_windows() is True
    cmd, kwargs = calls[0]
    assert cmd == ["/opt/智慧团委.exe"], "不能再借 cmd /c ping 做延时"
    detached = getattr(subprocess, "DETACHED_PROCESS", 0)
    if detached:
        assert kwargs["creationflags"] & detached


def test_install_and_notify_restart_hint(monkeypatch):
    from app.services import silent_update

    notices = []
    monkeypatch.setattr(silent_update, "install", lambda path: None)
    monkeypatch.setattr(silent_update, "_notify", lambda title, body: notices.append((title, body)))
    assert silent_update.install_and_notify("/tmp/setup.exe") == 0
    assert notices and "重启" in notices[0][1]


def test_install_failure_notifies_manual_step(monkeypatch):
    from app.services import silent_update

    notices = []

    def _boom(path):
        raise silent_update.SilentInstallError("安装程序退出码 1")

    monkeypatch.setattr(silent_update, "install", _boom)
    monkeypatch.setattr(silent_update, "_notify", lambda title, body: notices.append((title, body)))
    assert silent_update.install_and_notify("/tmp/setup.exe") == 1
    assert notices[0][0] == "更新未完成"
    assert "手动安装" in notices[0][1]


def test_startup_check_downloads_and_waits_for_restart(tmp_path, monkeypatch):
    """后台发现新版本后下载，但不自动安装：留给用户确认重启。"""
    from PyQt5.QtCore import QCoreApplication, QTimer

    from app.services import silent_update

    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    pkg = tmp_path / "SmartYouth-setup.exe"
    pkg.write_bytes(b"x")

    class FakeCheckWorker:
        def __init__(self):
            self.found, self.failed, self.finished = _Signal(), _Signal(), _Signal()

        def start(self):
            self.found.emit(info(version="9.9.9"))
            self.finished.emit()

    class FakeDownloadWorker:
        def __init__(self, info, use_proxy):
            self.done, self.failed, self.fallback, self.finished = (
                _Signal(), _Signal(), _Signal(), _Signal())

        def start(self):
            self.done.emit(str(pkg))
            self.finished.emit()

    started = []
    monkeypatch.setattr(update_check, "CheckWorker", FakeCheckWorker)
    monkeypatch.setattr(update_check, "DownloadWorker", FakeDownloadWorker)
    monkeypatch.setattr(silent_update, "start", lambda path: started.append(path) or True)
    monkeypatch.setattr(QTimer, "singleShot", lambda ms, callback, *a, **k: callback(*a, **k))

    update_check.check_on_startup(delay_ms=0)

    assert started == []             # 不自动安装
    assert silent_update.pending_installer()["path"] == str(pkg)
    assert "等待重启" in update_check.last_check_text()


@pytest.mark.parametrize("is64,win7,expected", [
    (True, False, "SmartYouth-Windows10-x64-setup.exe"),
    (False, False, "SmartYouth-Windows10-x86-setup.exe"),
    (True, True, "SmartYouth-Windows7-x64-setup.exe"),
    (False, True, "SmartYouth-Windows7-x86-setup.exe"),
])
def test_pick_asset_matches_windows_bit_and_os(monkeypatch, is64, win7, expected):
    """Windows 有四个安装包（世代 × 位数），必须挑与本机系统、位数都匹配的那份。

    32 位系统装不了 64 位包；Win7 也装不了 Win10 包（解释器/运行时不同）。
    挑错不会在下载时报错，要等安装或启动才炸，所以在这里钉死。"""
    monkeypatch.setattr(updater.platform_module, "architecture",
                        lambda: ("64bit" if is64 else "32bit", ""))
    monkeypatch.setattr(updater, "_is_windows7", lambda: win7)
    assets = [
        {"name": "SmartYouth-Windows7-x86-setup.exe", "browser_download_url": "u1"},
        {"name": "SmartYouth-Windows7-x64-setup.exe", "browser_download_url": "u2"},
        {"name": "SmartYouth-Windows10-x86-setup.exe", "browser_download_url": "u3"},
        {"name": "SmartYouth-Windows10-x64-setup.exe", "browser_download_url": "u4"},
        {"name": "SmartYouth-macOS-arm64.dmg", "browser_download_url": "u5"},
    ]
    assert updater._pick_asset(assets, "win32")["name"] == expected


def test_pick_asset_windows_falls_back_when_only_one_package(monkeypatch):
    """老版本 Release 只有一个 Windows 包时仍要能用，不能挑不出就返回 None。"""
    monkeypatch.setattr(updater.platform_module, "architecture", lambda: ("32bit", ""))
    monkeypatch.setattr(updater, "_is_windows7", lambda: False)
    assets = [{"name": "SmartYouth-Windows10-x64-setup.exe", "browser_download_url": "u1"}]
    assert updater._pick_asset(assets, "win32")["name"] == "SmartYouth-Windows10-x64-setup.exe"


def test_windows_asset_follows_build_target(monkeypatch):
    """打包目标写在代码里：Win7-x86 的包升级时必须拿 Win7-x86。

    运行时反推在「Win10 机器上跑 Win7 包」这类情况下会拿错，装完系统要求就降级了。
    """
    from app.services import updater as up

    assets = [
        {"name": "SmartYouth-Windows10-x64-setup.exe"},
        {"name": "SmartYouth-Windows10-x86-setup.exe"},
        {"name": "SmartYouth-Windows7-x64-setup.exe"},
        {"name": "SmartYouth-Windows7-x86-setup.exe"},
    ]

    monkeypatch.setattr(up, "BUILD_TARGET_OS", "Windows7")
    monkeypatch.setattr(up, "BUILD_TARGET_ARCH", "x86")
    assert [a["name"] for a in [up._pick_asset(assets, "win32")]] == [
        "SmartYouth-Windows7-x86-setup.exe"]

    monkeypatch.setattr(up, "BUILD_TARGET_OS", "Windows10")
    monkeypatch.setattr(up, "BUILD_TARGET_ARCH", "x64")
    assert up._pick_asset(assets, "win32")["name"] == "SmartYouth-Windows10-x64-setup.exe"


def test_windows_asset_falls_back_within_same_generation(monkeypatch):
    """同世代另一位宽可以退，跨世代不行。"""
    from app.services import updater as up

    monkeypatch.setattr(up, "BUILD_TARGET_OS", "Windows10")
    monkeypatch.setattr(up, "BUILD_TARGET_ARCH", "x64")
    only_x86 = [{"name": "SmartYouth-Windows10-x86-setup.exe"},
                {"name": "SmartYouth-Windows7-x64-setup.exe"}]
    assert up._pick_asset(only_x86, "win32")["name"] == "SmartYouth-Windows10-x86-setup.exe"


def test_updated_marker_only_consumed_when_version_reached(tmp_path, monkeypatch):
    """装完才有提示：版本没到目标时标记要留着，别把失败当成功。"""
    from app.services import silent_update as su

    monkeypatch.setattr(su, "_marker_path", lambda: tmp_path / "updated.json")
    su.mark_pending("1.1.0")
    assert su.take_updated("1.0.10") == ""          # 还是旧版本：不消费
    assert (tmp_path / "updated.json").exists()
    assert su.take_updated("1.1.0") == "1.1.0"      # 已到目标：消费并返回
    assert not (tmp_path / "updated.json").exists()
    assert su.take_updated("1.1.0") == ""


def test_windows_installer_forces_close_and_logs(monkeypatch, tmp_path):
    """安装参数必须带 /FORCECLOSEAPPLICATIONS：托盘进程没退干净时由安装器强关。

    少了它，Inno 的「文件被占用」提示在 /SUPPRESSMSGBOXES 下默认选中止，
    安装器以退出码 5 结束——就是用户看到的「托盘还在、更新失败」。
    """
    import types

    from app.services import silent_update as su

    monkeypatch.setattr(su, "sys", types.SimpleNamespace(platform="win32"))
    cmd = su.installer_command(str(tmp_path / "SmartYouth-Windows10-x64-setup.exe"))
    assert cmd is not None
    assert cmd[0].endswith(".exe")
    for flag in ("/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES",
                 "/NOCANCEL", "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS"):
        assert flag in cmd, flag


def test_installer_relaunches_after_silent_install():
    """静默安装完重开新版本是安装脚本的职责。

    这一条是 silent_update 不再自己挂延时重开的前提：删掉它，更新完应用就
    不再自己回来，用户只能从桌面图标打开。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, "installer.iss"), encoding="utf-8").read()
    assert "skipifnotsilent" in text, "静默安装后要由安装器启动新版本"
