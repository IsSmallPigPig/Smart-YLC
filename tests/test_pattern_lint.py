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


def test_build_collect_all_enforced():
    p = ROOT / "scripts" / "build.py"
    problems = lint_patterns.check_file(p)
    assert not any("打包漏库" in x for x in problems), problems


def test_store_never_returns_empty_on_corruption():
    p = ROOT / "app" / "services" / "store.py"
    assert "raise RuntimeError" in p.read_text(encoding="utf-8")


def test_naming_normalizes_before_parse():
    p = ROOT / "app" / "domain" / "naming.py"
    s = p.read_text(encoding="utf-8")
    assert "unicodedata.normalize" in s and "_INVISIBLE_RE" in s


def test_current_codebase_is_clean():
    assert lint_patterns.run(quiet=True) == []


# —— 手机端：配对传输相关的两条真实缺陷 ——

def test_analyzer_created_inside_frame_callback():
    """扫码无反应的根因：分析器在每帧回调里新建，节流失效。"""
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
    """电脑配对走 http://局域网IP，配置没放开明文时请求会被系统拒绝。"""
    only_loopback = '<network-security-config><domain-config cleartextTrafficPermitted="true">'
    assert lint_patterns._network_problems(only_loopback, "net.xml")
    good = '<network-security-config><base-config cleartextTrafficPermitted="true" />'
    assert lint_patterns._network_problems(good, "net.xml") == []


def test_roster_cache_key_unknown_to_parser():
    """注入名单后全员显示「未报到」：缓存写 reportStatus，解析表却没有这个键。"""
    repo = '.put("mid", m.mid).put("reportStatus", m.reportStatus)'
    parser = """
        private val aliases = mapOf(
            "reportStatus" to listOf("auditStatus", "reportDataStatus"),
        )
    """
    problems = lint_patterns._roster_key_problems(repo, parser)
    assert problems and "reportStatus" in problems[0], problems


def test_roster_cache_key_known():
    repo = '.put("reportStatus", m.reportStatus)'
    parser = """
        private val aliases = mapOf(
            "reportStatus" to listOf("auditStatus", "reportStatus"),
        )
    """
    assert lint_patterns._roster_key_problems(repo, parser) == []


# —— 手机端：2026-11-21 修正扫描/更新链路的四条真实缺陷 ——

def test_bitmap_recycle_while_visible():
    """旋转时闪退：显示中的位图被 LaunchedEffect 里 recycle，渲染队列还在读。"""
    source = """
        LaunchedEffect(page.id, page.rotation) {
            preview?.recycle()
            preview = render(page)
        }
    """
    problems = lint_patterns._kt_problems(source, "CropEditor.kt")
    assert any("显示位图提前回收" in p for p in problems), problems


def test_no_recycle_is_clean():
    source = """
        LaunchedEffect(page.id, page.rotation) {
            preview = render(page)
        }
    """
    assert lint_patterns._kt_problems(source, "CropEditor.kt") == []


def test_temporary_bitmap_recycle_is_allowed():
    """渲染中间的 source/result 回收是合法的，不能误伤。"""
    source = """
        val source = decode(page.file, maxEdge)
        val result = DocDetect.rectify(source, page.quad, page.filter)
        source.recycle()
        return result
    """
    assert lint_patterns._kt_problems(source, "ScanSession.kt") == []


def test_decode_without_exif():
    """侧拍照片进裁切就是歪的：BitmapFactory 解码后没应用 EXIF 方向。"""
    source = """
        val bitmap = BitmapFactory.decodeFile(path)
    """
    problems = lint_patterns._kt_problems(source, "ScanSession.kt")
    assert any("EXIF 未应用" in p for p in problems), problems


def test_decode_with_exif_is_clean():
    source = """
        val bitmap = BitmapFactory.decodeFile(path)
        val oriented = applyExifOrientation(bitmap, ExifInterface(path))
    """
    assert lint_patterns._kt_problems(source, "ScanSession.kt") == []


def test_rotation_missing_from_effect_key():
    """点旋转后识别框和图片错位：effect 只以 page.id 为 key，不重算。"""
    source = """
        val page by viewModel.page
        LaunchedEffect(page.id) { preview = render(page) }
        val turned = page.rotation
    """
    problems = lint_patterns._kt_problems(source, "CropEditor.kt")
    assert any("旋转未进 effect key" in p for p in problems), problems


def test_rotation_in_effect_key_is_clean():
    source = """
        val page by viewModel.page
        LaunchedEffect(page.id, page.rotation) { preview = render(page) }
        val turned = page.rotation
    """
    assert lint_patterns._kt_problems(source, "CropEditor.kt") == []


def test_download_auto_installs():
    """下载完自动弹安装器：用户还没确认，桌面端规则 19 在移动端重演。"""
    source = """
        fun download(info: Info) {
            scope.launch {
                GitHubUpdate.download(info).onSuccess { uri ->
                    app.startActivity(GitHubUpdate.installIntent(uri))
                }
            }
        }
    """
    problems = lint_patterns._update_install_problems(source, "UpdateManager.kt")
    assert problems and "下载后自动安装" in problems[0], problems


def test_download_leaves_install_to_user_is_clean():
    source = """
        fun download(info: Info) {
            scope.launch { _state.value = UpdateUi.Ready(info, uri) }
        }
        fun installReady(uri: Uri) { app.startActivity(GitHubUpdate.installIntent(uri)) }
    """
    assert lint_patterns._update_install_problems(source, "UpdateManager.kt") == []
