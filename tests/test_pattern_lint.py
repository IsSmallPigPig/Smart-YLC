"""代码反模式 lint 的回归测试。

每条规则都要证明两件事：能拦住当初那次真实故障的写法，且当前代码库是干净的——
只加规则不验证，lint 迟早变成跑一次红一次、然后被关掉的摆设。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import lint_patterns  # noqa: E402

TMP = ROOT / "tests" / "_lint_tmp"


def check(src: str, name: str = "sample.py") -> list[str]:
    TMP.mkdir(exist_ok=True)
    p = TMP / name
    p.write_text(src, encoding="utf-8")
    try:
        return lint_patterns.check_file(p)
    finally:
        p.unlink(missing_ok=True)


# 每条 = (能触发旧写法的源码, 期望的问题关键字)
BAD_PATTERNS = [
    # 本次：getfqdn 反解卡 30 秒
    ('import socket\nresult = socket.getfqdn("0.0.0.0")\n', "getfqdn 阻塞"),
    # 本次：HTTPServer 直接绑通配地址
    ('from http.server import HTTPServer\ns = HTTPServer(("0.0.0.0", 8765), H)\n',
     "通配绑定阻塞"),
    # 本次：裸 QDialog 弹窗
    ('from PyQt5.QtWidgets import QDialog\nclass SyncDialog(QDialog):\n    pass\n',
     "裸 QDialog 弹窗"),
    # 中文路径：osascript 拼串
    ('s = \'display notification "已收到" with title "手机"\'\n', "osascript 拼串"),
    # 中文路径：text=True 无编码
    ('subprocess.run(["hdiutil", "attach", p], capture_output=True, text=True)\n',
     "subprocess 文本解码"),
    # 性能：extract_image 取宽高
    ('info = doc.extract_image(xref)\nw = info.get("width")\n', "整图解码取尺寸"),
    # Windows 白屏：用了材质没清背板
    ('if windows_material_available(m):\n    apply_windows_effect(self, m)\n',
     "材质残留"),
    # 并发乱序
    ('from concurrent.futures import ThreadPoolExecutor, as_completed\n'
     'prepared = []\n'
     'with ThreadPoolExecutor() as pool:\n'
     '    for f in as_completed({pool.submit(c, j): j for j in jobs}):\n'
     '        prepared.append(f.result())\n',
     "并发乱序"),
]


@pytest.mark.parametrize("src,marker", BAD_PATTERNS)
def test_rule_catches_old_pattern(src, marker):
    problems = check(src)
    assert any(marker in p for p in problems), (marker, problems)


def test_api_endpoints_require_session_auth():
    """配对换 token 后，文件/状态端点必须校验会话，不能让配对码长期有效。"""
    p = ROOT / "app" / "services" / "lan_share.py"
    problems = lint_patterns.check_file(p)
    assert not any("端点缺会话鉴权" in x for x in problems), problems

    # 规则本身要能拦住"有 pair 和 upload 端点但没校验会话"的旧写法
    bad = ('if path == "/api/pair":\n    pair()\n'
           'if path == "/api/upload":\n    accept(name, data)\n')
    assert any("端点缺会话鉴权" in x for x in check(bad, "lan_share.py"))


def test_pair_must_check_config_code():
    """配对必须校验 configCode，只允许相同配置的设备。"""
    p = ROOT / "app" / "services" / "lan_share.py"
    assert not any("配对缺配置校验" in x for x in lint_patterns.check_file(p))

    bad = ('if path == "/api/pair":\n    pair(pin)\n'
           'if path == "/api/upload":\n    session(token)\n')
    assert any("配对缺配置校验" in x for x in check(bad, "lan_share.py"))


def test_relay_pull_must_be_chunked():
    """中转拉取必须分片，整份拉取会撞 Netlify 6MB 响应墙。"""
    p = ROOT / "app" / "services" / "relay_receive.py"
    assert not any("中转整份拉取" in x for x in lint_patterns.check_file(p))

    bad = ('params={"action": "pull", "file": fid}\n'
           'return resp.content\n')
    assert any("中转整份拉取" in x for x in check(bad, "relay_receive.py"))


def test_opencv_dep_lines_pin_numpy():
    """规则 15：opencv 4.8.x 的 ABI 故障——每条安装行必须同时钉 numpy<2。"""
    # 当前库干净：requirements.txt 与 release.yml 全过
    assert lint_patterns.check_deploy_configs() == []

    # 能拦住旧写法：装了 opencv 4.8.1 却没钉 numpy（release.yml 风格的 deps 行）
    TMP.mkdir(exist_ok=True)
    bad = TMP / "release.yml"
    good = TMP / "release-good.yml"
    bad.write_text(
        'deps: \'"opencv-python-headless==4.8.1.78" qrcode\'\n', encoding="utf-8")
    good.write_text(
        'deps: \'"numpy<2" "opencv-python-headless==4.8.1.78" qrcode\'\n',
        encoding="utf-8")
    try:
        assert any("numpy 未钉" in x
                   for x in lint_patterns.check_deploy_configs((bad,)))
        assert lint_patterns.check_deploy_configs((good,)) == []
    finally:
        bad.unlink(missing_ok=True)
        good.unlink(missing_ok=True)


def test_smoke_checks_cv2_abi():
    """规则 16：冒烟自检必须 import cv2，否则 ABI 故障漏到用户端。"""
    p = ROOT / "app" / "main.py"
    assert not any("冒烟漏检 cv2" in x for x in lint_patterns.check_file(p))

    bad = ('def _smoke_test():\n'
           '    from cryptography.hazmat.primitives.ciphers.aead import AESGCM\n'
           '    AESGCM(b"0" * 32).encrypt(b"0" * 12, b"smoke", None)\n')
    assert any("冒烟漏检 cv2" in x for x in check(bad, "main.py"))


def test_upload_uses_work_copy():
    """规则 17：上传必须筛 work_path，不能退回原件。"""
    p = ROOT / "app" / "ui" / "upload_page.py"
    assert not any("上传绕开副本" in x for x in lint_patterns.check_file(p))

    bad = ('def _start_upload(self):\n'
           '    usable = [s for s in self.statuses if s.usable]\n'
           '    jobs = [{"path": s.path, "account": s.account} for s in usable]\n')
    assert any("上传绕开副本" in x for x in check(bad, "upload_page.py"))


def test_scan_skips_work_dir():
    """规则 18：扫描必须跳过工作目录子树。"""
    p = ROOT / "app" / "domain" / "archive_scan.py"
    assert not any("扫描工作目录" in x for x in lint_patterns.check_file(p))

    bad = ('def scan_folder(root):\n'
           '    for dirpath, dirnames, files in os.walk(root):\n'
           '        pass\n')
    assert any("扫描工作目录" in x for x in check(bad, "archive_scan.py"))


def test_check_cache_must_use_content_digest():
    """规则 20：检查缓存不能按路径 + mtime，否则改名/重压/重启后全部失效。"""
    p = ROOT / "app" / "ui" / "upload_page.py"
    assert not any("检查缓存按路径" in x for x in lint_patterns.check_file(p))

    bad = ('class Page:\n'
           '    def _cell_key(self, path):\n'
           '        return (path, os.path.getmtime(path))\n')
    assert any("检查缓存按路径" in x for x in check(bad, "upload_page.py"))


def test_download_must_not_auto_install():
    """规则 19：下载完成不能自动安装退出，等用户确认重启。"""
    p = ROOT / "app" / "ui" / "about_card.py"
    assert not any("下载即自动安装" in x for x in lint_patterns.check_file(p))

    bad = ('def _on_downloaded(self, path):\n'
           '    silent_update.start(path)\n'
           '    self._quit_app()\n')
    assert any("下载即自动安装" in x for x in check(bad, "about_card.py"))


def test_build_collect_all_enforced(monkeypatch):
    p = ROOT / "scripts" / "build.py"
    problems = lint_patterns.check_file(p)
    assert not any("打包漏库" in x for x in problems), problems

    # 能拦住旧写法：缺 --collect-all 的构建脚本（规则按 scripts/build.py 路径生效）
    monkeypatch.setattr(lint_patterns, "_rel", lambda path: "scripts/build.py")
    bad = 'cmd = ["pyinstaller", "--onefile", "app.py"]\n'
    assert any("打包漏库" in x for x in check(bad, "build.py"))


def test_ui_hex_color_rejected(monkeypatch):
    """规则 8：app/ui 下硬编码 hex 色——语义色必须从 palette 取。"""
    # 规则按 app/ui/ 路径前缀生效，临时文件在 tests/ 下，伪装一下路径
    monkeypatch.setattr(lint_patterns, "_rel", lambda path: "app/ui/sample.py")
    assert check('from app.ui.palette import P\ncolor = P.BRAND\n') == []
    assert any("硬编码色" in x for x in check('w.setStyleSheet("color: #0078d4;")\n'))
    # 注释里的示例色不算命中
    assert check('# 参照 #0078d4 取色\n') == []


def test_store_never_returns_empty_on_corruption():
    p = ROOT / "app" / "services" / "store.py"
    assert "raise RuntimeError" in p.read_text(encoding="utf-8")


def test_naming_normalizes_before_parse():
    p = ROOT / "app" / "domain" / "naming.py"
    s = p.read_text(encoding="utf-8")
    assert "unicodedata.normalize" in s and "_INVISIBLE_RE" in s


def test_current_codebase_is_clean():
    assert lint_patterns.run(quiet=True) == []


def test_body_in_exception_message():
    """站点返回 HTML 时，整页会被塞进提示框。"""
    source = 'throw SiteException("站点返回无法解析：${body.take(120)}")'
    problems = lint_patterns._kt_problems(source, "Sample.kt")
    assert any("响应体进提示" in p for p in problems), problems


def test_readable_hint_is_allowed():
    source = 'throw SiteException("站点返回了网页，登录可能已失效")'
    assert lint_patterns._kt_problems(source, "Sample.kt") == []


# —— 手机端：配对与提示相关的真实缺陷 ——

def test_analyzer_created_inside_frame_callback():
    source = """
        analysis.setAnalyzer(executor) { image ->
            QrAnalyzer { text -> onQr(text) }.analyze(image)
        }
    """
    problems = lint_patterns._kt_problems(source, "Sample.kt")
    assert any("分析器每帧重建" in p for p in problems), problems


def test_shared_analyzer_is_allowed():
    source = """
        val analyzer = remember { QrAnalyzer { text -> onQr(text) } }
        analysis.setAnalyzer(executor, analyzer)
    """
    assert lint_patterns._kt_problems(source, "Sample.kt") == []


def test_cleartext_lan_requires_config():
    only_loopback = '<network-security-config><domain-config cleartextTrafficPermitted="true">'
    assert lint_patterns._network_problems(only_loopback, "net.xml")
    good = '<network-security-config><base-config cleartextTrafficPermitted="true" />'
    assert lint_patterns._network_problems(good, "net.xml") == []


# —— 线程模型：QThread.wait 当 sleep、无事件循环线程建 QTimer（i016/i017）——

def test_qthread_wait_as_sleep_rejected():
    """能拦住旧写法：QThread 子类 run() 里 self.wait(毫秒)。"""
    bad = '''\
from PyQt5.QtCore import QThread
class Worker(QThread):
    def run(self):
        while True:
            self.do_work()
            self.wait(5000)
'''
    assert any("wait 当 sleep" in p for p in check(bad)), check(bad)


def test_qthread_wait_cond_is_allowed():
    """当前库干净：QWaitCondition 的正确写法不该命中。"""
    good = '''\
from PyQt5.QtCore import QThread, QMutex, QWaitCondition
class Worker(QThread):
    def run(self):
        while True:
            self._wakeup.wait(self._mutex, 5000)
'''
    assert check(good) == []


def test_timer_in_http_callback_rejected():
    """能拦住旧写法：_on_received 里用 QTimer.singleShot 切回 UI。"""
    bad = '''\
from PyQt5.QtCore import QTimer
class Dlg:
    def _on_received(self, item):
        QTimer.singleShot(0, lambda: self._show_received(item))
    def _show_received(self, item):
        pass
'''
    assert any("_on_received 用 QTimer" in p for p in check(bad)), check(bad)


def test_signal_in_http_callback_is_allowed():
    """当前库干净：emit 信号回到 GUI 线程的写法不该命中。"""
    good = '''\
class Dlg:
    def _on_received(self, item):
        self._http_received.emit(item)
    def _show_received(self, item):
        pass
'''
    assert check(good) == []


def test_save_config_silent_pass_rejected():
    """能拦住旧写法：保存配置时 except: pass 静默丢用户设置。"""
    bad = '''\
def _save_cloud_state(self, patch):
    try:
        store.update(lambda cfg: cfg.update(patch))
    except Exception:
        pass
'''
    assert any("保存静默失败" in p for p in check(bad)), check(bad)


def test_save_config_with_log_is_allowed():
    """当前库干净：失败时记日志+提示就不算静默。"""
    good = '''\
def _save_cloud_state(self, patch):
    try:
        store.update(lambda cfg: cfg.update(patch))
    except Exception as e:
        app_log.error("写盘失败：%s", e)
        InfoBar.error("设置没保存", str(e), parent=self)
'''
    assert check(good) == []
