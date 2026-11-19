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
