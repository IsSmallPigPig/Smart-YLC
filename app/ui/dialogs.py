"""fluent 风格弹窗（非模态异步：show + 回调，绝不 exec 阻塞）。

两代教训：
1. borderless key-window 补丁卡死——Qt 的 QCocoaNSWindow 自管 key window，
   官方 borderless 弹窗在 macOS 本来就能点击，任何 pyobjc 改 styleMask 都会
   与 Qt 的 flags 状态机打架（详见 git 历史）。
2. exec() 的嵌套模态事件循环在 mac 上与主窗口的定时器/动画互相干扰，表现为
   弹窗点不动、界面假死。所有弹窗一律 show() 非模态打开，结果经回调续跑——
   等效"在别处异步执行"，主循环全程不阻塞。
"""

from __future__ import annotations

from qfluentwidgets import MessageBox, MessageBoxBase


def strip_center_border(dialog) -> None:
    """去掉 qfluentwidgets 给弹窗中心控件写死的 1px 描边。

    深色主题的 QSS 是 `#centerWidget { border: 1px solid rgb(58,58,58) }`，
    描在 rgb(43,43,43) 的底上；弹窗浮在更暗的背景前时，这一圈就是显眼的白边。
    弹窗本身已有圆角和底色，这圈描边是多余的。只覆盖 border，底色和圆角仍用原规则。
    """
    center = getattr(dialog, "widget", None)
    if center is not None:
        center.setStyleSheet("QWidget#centerWidget { border: none; }")


class FluentMessageBox(MessageBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        strip_center_border(self)

    def done(self, code: int) -> None:
        """跳过 MaskDialogBase 渐出动画：QGraphicsOpacityEffect finished 在部分
        环境不触发会让弹窗永远关不掉。"""
        from PySide6.QtWidgets import QDialog

        QDialog.done(self, code)


class FluentPanelDialog(MessageBoxBase):
    """fluent 面板弹层：往 self.viewLayout 里放自定义内容（表格/树等）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        strip_center_border(self)

    def done(self, code: int) -> None:
        from PySide6.QtWidgets import QDialog

        QDialog.done(self, code)


def confirm(parent, title: str, text: str, yes: str = "确定", cancel: str = "取消",
            on_result=None) -> FluentMessageBox:
    """非模态确认弹窗：show() 打开、立即返回弹窗实例。

    结果经 on_result(bool) 回调（True=点了 yes）。收口用 QDialog.finished 信号——
    按钮点击、Esc、代码调用 accept/reject 全部统一经过它，不会漏；回调执行后
    弹窗自清理。不传 on_result 时弹窗纯提示用，点按钮即关。"""
    from PySide6.QtWidgets import QDialog

    w = FluentMessageBox(title, text, parent)
    w.yesButton.setText(yes)
    w.cancelButton.setText(cancel)

    def _finish(code: int):
        w.deleteLater()
        if on_result:
            on_result(code == QDialog.Accepted)

    w.finished.connect(_finish)
    w.show()
    return w


class ConfigDialog(FluentPanelDialog):
    """配置弹窗：一列控件，只有「完成」一个按钮。

    设置页把一行里放不下的次要项收进这里——行上留一个四字主按钮和一个齿轮图标，
    齿轮打开这个弹窗。控件由调用方持有并塞进来，弹窗只负责摆。"""

    def __init__(self, title: str, parent=None, note: str = "", width: int = 420,
                 yes_text: str = "完成"):
        super().__init__(parent)
        from PySide6.QtWidgets import QVBoxLayout

        from qfluentwidgets import SubtitleLabel

        from app.ui.page import note_label

        self.widget.setMinimumWidth(width)
        self.viewLayout.setSpacing(10)
        self.viewLayout.addWidget(SubtitleLabel(title, self.widget))
        if note:
            self.viewLayout.addWidget(note_label(note))
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        self.viewLayout.addLayout(self.body)
        self.yesButton.setText(yes_text)
        self.cancelButton.hide()
        self._items: list = []
        self._orig_parents: dict[int, object] = {}
        self.finished.connect(self._release)

    def add(self, widget) -> None:
        """塞一个控件进来。控件仍归调用方所有，关窗时还回去而不是跟着销毁。"""
        self._items.append(widget)
        self._remember_parent(widget)
        widget.setParent(self.widget)
        widget.show()
        self.body.addWidget(widget)

    def _remember_parent(self, widget):
        """记下控件原来的父级。

        关窗时用 setParent(None) 还控件会把它变成顶层控件——在 macOS 上就是一个
        带交通灯按钮的独立小窗（通知频率下拉框曾这样浮在屏幕左上角）。
        正确的做法是装回原来的父级再隐藏。"""
        parent = widget.parentWidget()
        if parent is None:
            parent = self.parentWidget()
        self._orig_parents[id(widget)] = parent

    def add_row(self, *widgets, spacing: int = 8):
        """一排并排的控件；按钮横向填满弹窗宽度。"""
        from PySide6.QtWidgets import QHBoxLayout, QWidget

        box = QWidget(self.widget)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(spacing)
        for widget in widgets:
            self._items.append(widget)
            self._remember_parent(widget)
            widget.setParent(box)
            widget.show()
            lay.addWidget(widget, 1)
        self.body.addWidget(box)
        return box

    def add_field(self, label: str, widget):
        """一个带说明的输入项：说明在上，控件在下。"""
        from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

        from app.ui.page import note_label

        box = QWidget(self.widget)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        hint = note_label(label)
        lay.addWidget(hint)
        self._items.append(widget)
        self._remember_parent(widget)
        widget.setParent(box)
        widget.show()
        lay.addWidget(widget)
        self.body.addWidget(box)
        return box

    def _release(self, *_):
        """弹窗关闭：把控件装回原父级并藏起来，再销毁弹窗本身。"""
        for widget in self._items:
            parent = self._orig_parents.pop(id(widget), None)
            if parent is not None:
                widget.setParent(parent)
            widget.hide()
        self._items.clear()
        self.deleteLater()


class FormDialog(ConfigDialog):
    """带「取消 / 确认」的表单弹窗。

    确认按钮先跑调用方的校验，只有它返回 True 才关窗——校验不过就停在原地，
    错误用 InfoBar 提示，不会把刚填的内容一起关掉。"""

    def __init__(self, title: str, parent=None, note: str = "", width: int = 460,
                 on_submit=None, yes_text: str = "确认"):
        super().__init__(title, parent, note, width=width, yes_text=yes_text)
        self._on_submit = on_submit
        self.cancelButton.setText("取消")
        self.cancelButton.show()
        # MessageBoxBase 默认点确认就 accept，这里改成先校验
        try:
            self.yesButton.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.yesButton.clicked.connect(self._submit)

    def _submit(self):
        if self._on_submit is None or self._on_submit():
            self.accept()
