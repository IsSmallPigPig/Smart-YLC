"""局域网直传的测试：真实起服务、真实发 HTTP 请求。

一律把落盘目录指到 tmp_path，绝不碰用户的「同步自手机」目录。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from app.services import phone_sync


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """起一个服务，落盘目录重定向到 tmp_path。"""
    monkeypatch.setattr(phone_sync, "sync_dir", lambda today=None: str(tmp_path))
    monkeypatch.setattr(phone_sync, "register_source_dir", lambda path, add_dir_fn=None: None)

    from app.services.lan_share import LanShare

    share = LanShare(port=0)
    url = share.start()
    base = url.split("/?")[0]
    yield share, base
    share.stop()


def _post(base: str, pin: str, name: str, data: bytes):
    req = urllib.request.Request(
        f"{base}/upload?k={pin}&name={urllib.parse.quote(name)}",
        data=data, headers={"Content-Type": "application/pdf"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode())


def test_page_and_ping(server):
    share, base = server
    with urllib.request.urlopen(base + "/", timeout=5) as resp:
        html = resp.read().decode()
    assert resp.status == 200
    assert "type=\"file\"" in html and "application/pdf" in html
    # 页面不引外部资源：手机可能没有外网
    assert "http://" not in html.split("<script>")[0] or "cdn" not in html

    with urllib.request.urlopen(base + "/ping", timeout=5) as resp:
        assert json.loads(resp.read().decode())["ok"] is True


def test_upload_saves_pdf_and_rejects_wrong_pin(server, tmp_path):
    import urllib.parse  # noqa: F401 - 供 _post 用

    share, base = server
    data = b"%PDF-1.4 fake"
    status, payload = _post(base, share.pin, "张三+13800000000+入团志愿书.pdf", data)
    assert status == 200 and payload["ok"] is True
    assert payload["status"] == "ok"
    saved = list(tmp_path.glob("*.pdf"))
    assert [p.name for p in saved] == ["张三+13800000000+入团志愿书.pdf"]
    assert saved[0].read_bytes() == data

    with pytest.raises(urllib.error.HTTPError) as err:
        _post(base, "000000", "x.pdf", data)
    assert err.value.code == 403


def test_same_content_twice_is_duplicate(server, tmp_path):
    import urllib.parse  # noqa: F401

    share, base = server
    data = b"%PDF-1.4 same"
    _post(base, share.pin, "李四+13900000000+入团志愿书.pdf", data)
    status, payload = _post(base, share.pin, "李四+13900000000+入团志愿书.pdf", data)
    assert status == 200 and payload["status"] == "duplicate"
    assert len(list(tmp_path.glob("*.pdf"))) == 1


def test_non_pdf_is_rejected_with_reason(server, tmp_path):
    import urllib.parse  # noqa: F401

    share, base = server
    status, payload = _post(base, share.pin, "照片.jpg", b"\xff\xd8\xff")
    assert status == 200
    assert payload["ok"] is False and "PDF" in payload["note"]
    assert not list(tmp_path.glob("*.jpg"))


def test_bad_filename_still_saved_but_flagged(server, tmp_path):
    """命名不合规也要落盘并提示改名，不能静默丢。"""
    import urllib.parse  # noqa: F401

    share, base = server
    status, payload = _post(base, share.pin, "随便起的名字.pdf", b"%PDF-1.4 x")
    assert status == 200 and payload["status"] == "need_rename"
    assert (tmp_path / "随便起的名字.pdf").exists()
    assert "改名" in payload["note"]


def test_oversize_upload_is_refused(server, monkeypatch):
    from app.services import lan_share

    monkeypatch.setattr(lan_share, "MAX_UPLOAD", 1024)
    import urllib.parse  # noqa: F401

    share, base = server
    with pytest.raises(urllib.error.HTTPError) as err:
        _post(base, share.pin, "大文件.pdf", b"x" * 4096)
    assert err.value.code == 413


def test_lan_addresses_are_private():
    from app.services.lan_share import _is_private, lan_addresses

    assert _is_private("192.168.1.5")
    assert _is_private("10.0.0.2")
    assert _is_private("172.20.3.4")
    assert not _is_private("8.8.8.8")
    assert not _is_private("172.40.1.1")
    for addr in lan_addresses():
        assert _is_private(addr)


def test_dialog_builds_and_toggles(qapp_session, tmp_path, monkeypatch):
    """对话框能建出来：开启后给出地址，关闭后服务真的停了。"""
    monkeypatch.setattr(phone_sync, "sync_dir", lambda today=None: str(tmp_path))
    monkeypatch.setattr(phone_sync, "register_source_dir", lambda path, add_dir_fn=None: None)

    from PyQt5.QtWidgets import QWidget

    from app.ui.phone_sync_dialog import PhoneSyncDialog

    host = QWidget()
    host.resize(900, 700)
    dlg = PhoneSyncDialog(host, auto_start=False)
    assert dlg.share.running is False
    dlg._toggle()
    if dlg.share.running:
        assert dlg.addr_label.text().startswith("http://") or dlg.addr_label.text() == "—"
        assert "Wi-Fi" in dlg.state_label.text()
        dlg._toggle()
        assert dlg.share.running is False
        assert dlg.toggle_btn.text() == "开始接收"
    dlg.close()
    dlg.deleteLater()
    host.deleteLater()


def test_start_does_not_do_reverse_dns(monkeypatch):
    """0.0.0.0 的 PTR 查询在无 DNS 环境下卡 30 秒——start 绝不能触发它。

    回归：HTTPServer.server_bind 默认调 getfqdn('0.0.0.0')，用户按「开始接收」
    就卡半分钟。LanShare 用自己的 server 子类跳过反解。
    """
    import socket

    from app.services import lan_share

    def explode(host):
        raise AssertionError(f"start 不该做反向 DNS 解析：{host!r}")

    monkeypatch.setattr(socket, "getfqdn", explode)
    share = lan_share.LanShare(port=0)
    url = share.start()
    try:
        assert url.startswith("http://")
    finally:
        share.stop()


def test_dialog_is_fluent_panel_not_toplevel(qapp_session, tmp_path, monkeypatch):
    """手机同步必须是居中 Fluent 弹窗，不再是带系统标题栏的独立 QDialog。"""
    monkeypatch.setattr(phone_sync, "sync_dir", lambda today=None: str(tmp_path))

    from qfluentwidgets import MessageBoxBase

    from app.ui.phone_sync_dialog import PhoneSyncDialog

    from PyQt5.QtWidgets import QWidget

    host = QWidget()
    host.resize(900, 700)
    dlg = PhoneSyncDialog(host, auto_start=False)
    try:
        assert isinstance(dlg, MessageBoxBase), "应复用 Fluent 弹窗基类"
        # MaskDialogBase 会铺满父窗口做遮罩，所以 parent 必须就是主窗口
        assert dlg.parent() is host
    finally:
        dlg._cleanup()
