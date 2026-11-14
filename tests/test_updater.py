"""更新服务：版本比较、GitHub Release/分支回退、ghproxy 加速与直连回退、下载原子写。

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
    assert updater.proxy_url(updater.SOURCE_ZIP) == updater.GH_PROXY + updater.SOURCE_ZIP
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
    assert all(not u.startswith(updater.GH_PROXY) for u in seen)


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
    seen = fake_urlopen(monkeypatch, {updater.GH_PROXY + updater.SOURCE_ZIP: body})
    progress = []
    path = updater.download(info(url=updater.SOURCE_ZIP), True, progress=progress.append)

    assert seen == [updater.GH_PROXY + updater.SOURCE_ZIP]
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
    fake_urlopen(monkeypatch, {updater.GH_PROXY + url: failure})
    with pytest.raises(updater.UpdateError) as e:
        updater.download(info(url=url), True)
    assert "加速下载" in str(e.value)
    assert updater.GH_PROXY + url in str(e.value)
    assert list(tmp_path.glob("*.part")) == []


def test_cancelled_download_leaves_no_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "UPDATE_DIR", str(tmp_path))
    fake_urlopen(monkeypatch, {updater.SOURCE_ZIP: b"y" * 300000})
    with pytest.raises(updater.UpdateError, match="取消"):
        updater.download(info(url=updater.SOURCE_ZIP), False, cancel=lambda: True)
    assert list(tmp_path.glob("*.part")) == []


# ---------------------------------------------------------------- Qt 回退

def test_download_worker_falls_back_to_direct(monkeypatch):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication([])
    calls = []

    def _download(inf, use_proxy, **kwargs):
        calls.append(use_proxy)
        if use_proxy:
            raise updater.UpdateError("加速下载失败：代理不可用")
        return "/tmp/Smart-YLC.zip"

    monkeypatch.setattr(updater, "download", _download)
    worker = update_check.DownloadWorker(info(), True)
    fallbacks, done, failed = [], [], []
    worker.fallback.connect(fallbacks.append)
    worker.done.connect(done.append)
    worker.failed.connect(failed.append)
    worker.run()  # 直接跑 run，不启动真实线程

    assert calls == [True, False]
    assert len(fallbacks) == 1 and "代理不可用" in fallbacks[0]
    assert done == ["/tmp/Smart-YLC.zip"] and failed == []


def test_download_worker_direct_failure_reports_error(monkeypatch):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication([])

    def _download(inf, use_proxy, **kwargs):
        raise updater.UpdateError("直连下载失败：超时")

    monkeypatch.setattr(updater, "download", _download)
    worker = update_check.DownloadWorker(info(), False)
    failed, fallback = [], []
    worker.failed.connect(failed.append)
    worker.fallback.connect(fallback.append)
    worker.run()
    assert failed == ["直连下载失败：超时"] and fallback == []


def test_check_worker_reports_failure_instead_of_crashing(monkeypatch):
    from PySide6.QtCore import QCoreApplication

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
    assert update_check.proxy_mode() == "ghproxy"
    assert "尚未检查" in update_check.last_check_text()

    update_check.set_auto_check(False)
    update_check.set_proxy_mode("direct")
    update_check.record("update", "9.9.9")
    assert update_check.auto_check_enabled() is False
    assert update_check.proxy_mode() == "direct"
    assert "9.9.9" in update_check.last_check_text()


def test_invalid_proxy_mode_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    store.save({"updateProxy": "something-else"})
    assert update_check.proxy_mode() == "ghproxy"


def test_startup_check_respects_switch_off(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CONFIG_FILE", str(tmp_path / "config.json"))
    update_check.set_auto_check(False)
    called = []
    monkeypatch.setattr(update_check, "CheckWorker", lambda: called.append(1) or None)
    update_check.check_on_startup(delay_ms=0)
    assert called == []
