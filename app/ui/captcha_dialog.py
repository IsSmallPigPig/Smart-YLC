"""验证码登录弹窗：密码 + 图形验证码，登录成功直接恢复该账号。

站点密码错几次后要求图片验证码。以前只能让用户去网页端手动登录一次；
这里把验证码图直接取到弹窗里，用户双击账号行就能改密码、填验证码。

网络请求放到线程池，结果由 QTimer 回主线程——弹窗关闭时请求最多再跑
20 秒，不会挡住界面，也不会因为 QThread 析构把进程带走。
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

from PyQt5.QtCore import QTimer, Qt, pyqtSignal as Signal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from qfluentwidgets import CaptionLabel, LineEdit, PushButton, SubtitleLabel

from app.services.api import SiteClient
from app.ui.dialogs import FluentPanelDialog
from app.ui.page import note_label

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="captcha")
_POLL_MS = 80


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class CaptchaLoginDialog(FluentPanelDialog):
    """填密码和验证码，成功后回调 on_success(password, client)。

    调用方负责把新密码、登录状态和会话写回配置；弹窗只做登录本身。
    """

    def __init__(self, client: SiteClient, account: str, reason: str = "",
                 parent=None, on_success=None, on_browser=None):
        super().__init__(parent)
        self.client = client
        self.account = account
        self.on_success = on_success or (lambda password, client: None)
        self.on_browser = on_browser
        self._future: Future | None = None
        self._handler = None
        self._closed = False

        self.widget.setMinimumWidth(430)
        self.viewLayout.setSpacing(10)
        self.viewLayout.addWidget(SubtitleLabel("验证码登录", self.widget))
        text = f"{account}：{reason}" if reason else f"{account}：需要输入图片验证码"
        self.hint = note_label(text)
        self.viewLayout.addWidget(self.hint)

        form = QWidget(self.widget)
        lay = QVBoxLayout(form)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)

        self.password_edit = LineEdit(form)
        self.password_edit.setPlaceholderText("登录密码")
        # 不遮：这行密码就是账号表里那条，用户要能立刻看清有没有填错
        self.password_edit.setText(str(getattr(client, "password", "") or ""))
        lay.addWidget(self._field("密码", self.password_edit, form))

        captcha_row = QWidget(form)
        row = QHBoxLayout(captcha_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.image = _ClickableLabel(captcha_row)
        self.image.setFixedSize(120, 40)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setCursor(Qt.CursorShape.PointingHandCursor)
        self.image.setStyleSheet(
            f"QLabel{{background:{self.palette().base().color().name()};"
            f"border:1px solid {self.palette().mid().color().name()};border-radius:4px;}}")
        self.image.setToolTip("点图片换一张")
        self.image.clicked.connect(self.refresh_captcha)
        row.addWidget(self.image)

        self.captcha_edit = LineEdit(captcha_row)
        self.captcha_edit.setPlaceholderText("图片验证码")
        self.captcha_edit.setMaxLength(8)
        self.captcha_edit.returnPressed.connect(self._submit)
        row.addWidget(self.captcha_edit, 1)

        if on_browser is not None:
            web_btn = PushButton("网页登录", captcha_row)
            web_btn.clicked.connect(self._open_browser)
            row.addWidget(web_btn)
        lay.addWidget(self._field("验证码", captcha_row, form))
        self.viewLayout.addWidget(form)

        self.error = CaptionLabel("", self.widget)
        self.error.setMinimumHeight(20)
        self.error.setWordWrap(True)
        self.viewLayout.addWidget(self.error)

        self.yesButton.setText("登录")
        self.cancelButton.setText("取消")
        self.finished.connect(self._on_finished)
        self.refresh_captcha()

    def _field(self, title: str, widget: QWidget, parent: QWidget) -> QWidget:
        box = QWidget(parent)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        label = CaptionLabel(title, box)
        label.setFixedWidth(56)
        lay.addWidget(label)
        lay.addWidget(widget, 1)
        return box

    # ---------- 异步请求 ----------

    def _run(self, fn, handler):
        if self._closed or self._future is not None:
            return
        self._handler = handler
        self._future = _POOL.submit(fn)
        self._timer = getattr(self, "_timer", None)
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(_POLL_MS)
            self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _poll(self):
        future = self._future
        if future is None or not future.done():
            return
        self._future = None
        self._timer.stop()
        handler, self._handler = self._handler, None
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001 - 网络/解析异常都交给弹窗展示
            result = exc
        if self._closed or handler is None:
            return
        handler(result)

    # ---------- 验证码 ----------

    def _placeholder(self, text: str):
        """图片位置显示文字：先清掉旧图，QLabel 有 pixmap 时不会显示文字。"""
        self.image.clear()
        self.image.setText(text)

    def refresh_captcha(self):
        if self._future is not None:
            return
        self._placeholder("加载中…")
        self._run(self.client.fetch_captcha, self._show_captcha)

    def _show_captcha(self, data):
        if isinstance(data, Exception):
            self._placeholder("点击重试")
            self.error.setText(f"验证码取不到：{data}")
            return
        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data):
            self._placeholder("点击重试")
            self.error.setText("验证码取不到，点图片重新加载")
            return
        self.image.setPixmap(pixmap.scaled(
            self.image.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        self.captcha_edit.setFocus()

    # ---------- 登录 ----------

    def validate(self) -> bool:
        self._submit()
        return False                 # 等异步结果，成功后再 accept()

    def _submit(self):
        if self._future is not None:
            return
        password = self.password_edit.text()
        code = self.captcha_edit.text().strip()
        if not password:
            self.error.setText("请填写登录密码")
            self.password_edit.setFocus()
            return
        if len(code) < 2:
            self.error.setText("请填写图片验证码")
            self.captcha_edit.setFocus()
            return
        self.yesButton.setEnabled(False)
        self.error.setText("正在登录…")
        self.client.password = password

        def login():
            return self.client.login(code)

        self._run(login, lambda result: self._after_login(result, password))

    def _after_login(self, result, password: str):
        self.yesButton.setEnabled(True)
        if isinstance(result, Exception):
            self.error.setText(f"登录失败：{result}")
            return
        kind = str((result.data or {}).get("kind", "")) if isinstance(result.data, dict) else ""
        if result.ok:
            self.on_success(password, self.client)
            self.accept()
            return
        self.error.setText(result.message or "登录失败")
        if kind == "captcha":
            self.captcha_edit.clear()
            self.refresh_captcha()

    def _open_browser(self):
        if self.on_browser is not None:
            self.on_browser()
        self.reject()

    def _on_finished(self, _code: int):
        self._closed = True
        timer = getattr(self, "_timer", None)
        if timer is not None:
            timer.stop()
        self.deleteLater()
