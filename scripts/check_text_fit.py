"""排版体检：扫真实页面，找出被控件裁掉的文本。

换字体（Allo + MiSans 回落）后字宽与行高都会变，固定宽高的控件容易把字
裁掉。这里把主窗口真实构造出来，逐个数：单行文本比可用宽度宽、多行文本
比可用高度高、按钮文字压到边框。

    python scripts/check_text_fit.py            # 打印溢出清单，有则退出码 1
    python scripts/check_text_fit.py --shots    # 同时把每页截图存到 /tmp

用演示数据启动（隔离 HOME），不读不改用户配置。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# constants 在 import 时就求值配置路径，必须先换 HOME
if "--real-home" not in sys.argv:
    os.environ["HOME"] = tempfile.mkdtemp(prefix="smartyouth-fit-")
    os.environ["USERPROFILE"] = os.environ["HOME"]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QRect, Qt  # noqa: E402
from PyQt5.QtGui import QFontMetrics  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QComboBox,
    QLabel,
    QWidget,
)


def _plain(text: str) -> str:
    """去掉富文本标签；富文本按 QLabel 自己的布局算，不在这里判断。"""
    if "<" in text and ">" in text:
        return ""
    return text.strip()


def _checks(widget: QWidget) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if not widget.isVisible() or widget.width() <= 1 or widget.height() <= 1:
        return out
    if isinstance(widget, QLabel):
        text = _plain(widget.text())
        if not text:
            return out
        fm = QFontMetrics(widget.font())
        rect = widget.contentsRect()
        if widget.wordWrap():
            need = fm.boundingRect(QRect(0, 0, rect.width(), 10_000),
                                   Qt.TextWordWrap | Qt.AlignLeft, text)
            if need.height() > rect.height() + 1:
                out.append(("竖向", f"需要 {need.height()}px，只有 {rect.height()}px | {text}"))
        else:
            advance = fm.horizontalAdvance(text)
            if advance > rect.width() + 1:
                out.append(("横向", f"需要 {advance}px，只有 {rect.width()}px | {text}"))
    elif isinstance(widget, QAbstractButton):
        text = _plain(widget.text())
        if not text:
            return out
        need = QFontMetrics(widget.font()).horizontalAdvance(text)
        if need > widget.width() - 8:
            out.append(("按钮", f"需要 {need}px，控件 {widget.width()}px | {text}"))
    elif isinstance(widget, QComboBox):
        text = _plain(widget.currentText())
        if not text:
            return out
        need = QFontMetrics(widget.font()).horizontalAdvance(text)
        if need > widget.width() - 34:
            out.append(("下拉框", f"需要 {need}px，控件 {widget.width()}px | {text}"))
    return out


def _describe(widget: QWidget) -> str:
    parent = widget.parentWidget()
    chain = []
    node = widget
    while node is not None and len(chain) < 3:
        chain.append(node.__class__.__name__ + (f"[{node.objectName()}]" if node.objectName() else ""))
        node = node.parentWidget()
    return " < ".join(chain)


class _Settings:
    accounts: list[dict] = []
    rosters: dict = {}
    roster = None

    def expected_pages(self) -> int:
        return 0

    def org_rule(self):
        from app.domain.org_rule import OrgRule

        return OrgRule()


class _Window(QWidget):
    """页面构造函数要的宿主接口，取测试冒烟里那套最小集合。"""

    def __init__(self):
        super().__init__()
        self.settings_page = _Settings()
        self.upload_page = None
        self.messages_page = None
        self.org_manage_page = None

    def switchTo(self, page):
        pass


def _pages(host: QWidget) -> list[QWidget]:
    from app.ui.home_page import HomePage
    from app.ui.messages_page import MessagesPage
    from app.ui.org_manage_page import OrgManagePage
    from app.ui.org_page import OrgPage
    from app.ui.settings_page import SettingsPage
    from app.ui.upload_page import UploadPage

    made = []
    for factory in (lambda: HomePage(host), lambda: UploadPage(host),
                    lambda: MessagesPage(host), lambda: SettingsPage(),
                    lambda: OrgPage(host), lambda: OrgManagePage(host)):
        try:
            made.append(factory())
        except Exception as exc:  # noqa: BLE001 - 缺演示数据时跳过该页，不算溢出
            print(f"[跳过] {exc}")
    return made


def _dialogs(host: QWidget) -> list[QWidget]:
    """弹窗宽度是固定的，字宽一变最先在这里挤。"""
    from app.ui.export_dialog import ExportDialog
    from app.ui.phone_sync_dialog import PhoneSyncDialog

    host.resize(900, 620)
    made = []
    for factory in (lambda: ExportDialog("branch", host),
                    lambda: PhoneSyncDialog(host, auto_start=False)):
        try:
            made.append(factory())
        except Exception as exc:  # noqa: BLE001 - 缺依赖时跳过，不算溢出
            print(f"[跳过] {exc}")
    return made


def main(argv: list[str]) -> int:
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication(argv)

    # 与真实启动同序：先落演示配置，setup_app 读到的就是深色主题
    from app.services import demo_data, store

    if not store.load():
        cfg = demo_data.build_config()
        cfg["themeMode"] = "dark"
        store.save(cfg)

    from app.setup import setup_app

    setup_app()

    problems: list[tuple[str, str, str]] = []
    shots = "--shots" in argv
    # 宿主必须一直活着：被父控件带走的弹窗会连 C++ 对象一起析构
    host = _Window()
    for page in _pages(host) + _dialogs(host):
        page.resize(1180, 760)
        page.show()
        # 首页首帧盖着「正在加载概览…」，等它收掉再量，否则量的是浮层
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            app.processEvents()
            overlay = getattr(page, "_loading", None)
            if overlay is None or not hasattr(overlay, "isHidden") or overlay.isHidden():
                break
            time.sleep(0.02)
        for _ in range(3):
            app.processEvents()
        name = page.__class__.__name__
        if shots:
            # 页面背景是透明的（由主窗口画），直接 grab 会得到白底，
            # 深色主题下文字看着像消失了。垫一层页面底色再抓。
            from PyQt5.QtGui import QColor, QPixmap
            from qfluentwidgets import isDarkTheme

            canvas = QPixmap(page.size())
            canvas.fill(QColor("#202020" if isDarkTheme() else "#f3f3f3"))
            page.render(canvas)
            canvas.save(f"/tmp/fit-{name}.png")
        for widget in page.findChildren(QWidget):
            for kind, detail in _checks(widget):
                problems.append((name, kind, f"{_describe(widget)} | {detail}"))
        page.close()

    for page, kind, detail in problems:
        print(f"[{kind}] {page}: {detail}")
    print(f"\n共 {len(problems)} 处" if problems else "\n没有发现溢出")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
