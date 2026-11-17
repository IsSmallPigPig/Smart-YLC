"""页面内交互动画：淡入、交叉切换、高度展开。

只做视觉过渡，不改变布局里的内容与顺序。动画对象挂在目标 widget 上，
避免 QPropertyAnimation 被 Python GC 提前回收导致动画停在半途。
"""

from __future__ import annotations

from PyQt5.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QTimer,
    QVariantAnimation,
)
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QWidget

QWIDGETSIZE_MAX = 16777215


def _opacity_effect(widget: QWidget) -> QGraphicsOpacityEffect:
    eff = widget.graphicsEffect()
    if isinstance(eff, QGraphicsOpacityEffect):
        return eff
    eff = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(eff)
    return eff


def fade_in(widget: QWidget, duration: int = 220, dy: int = 8) -> QAbstractAnimation:
    """淡入并轻微上移；用于卡片列表、详情页出现时。"""
    old = getattr(widget, "_fade_ani", None)
    if old is not None:
        old.stop()
    eff = _opacity_effect(widget)
    eff.setOpacity(0.0)

    group = QParallelAnimationGroup(widget)
    opa = QPropertyAnimation(eff, b"opacity", group)
    opa.setDuration(duration)
    opa.setStartValue(0.0)
    opa.setEndValue(1.0)
    opa.setEasingCurve(QEasingCurve.Type.OutCubic)
    group.addAnimation(opa)

    if dy:
        end = widget.pos()
        pos = QPropertyAnimation(widget, b"pos", group)
        pos.setDuration(duration)
        pos.setStartValue(QPoint(end.x(), end.y() + dy))
        pos.setEndValue(end)
        pos.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(pos)

    def _done():
        eff.setOpacity(1.0)
        widget.setGraphicsEffect(None)
        widget._fade_ani = None

    group.finished.connect(_done)
    widget._fade_ani = group
    group.start()
    return group


def fade_out(widget: QWidget, duration: int = 110, on_done=None) -> QAbstractAnimation:
    """淡出到透明后回调，用于切换主题这类「先让画面安静下来再动刀」的场景。"""
    old = getattr(widget, "_fade_ani", None)
    if old is not None:
        old.stop()
    eff = _opacity_effect(widget)
    eff.setOpacity(1.0)

    ani = QPropertyAnimation(eff, b"opacity", widget)
    ani.setDuration(duration)
    ani.setStartValue(1.0)
    ani.setEndValue(0.0)
    ani.setEasingCurve(QEasingCurve.Type.InCubic)

    def _done():
        widget._fade_ani = None
        if on_done:
            on_done()

    ani.finished.connect(_done)
    widget._fade_ani = ani
    ani.start()
    return ani


def cross_fade(widget: QWidget, apply_work, out: int = 110, back: int = 170) -> QAbstractAnimation:
    """淡出 → 执行 apply_work（换主题等重活）→ 淡入。

    apply_work 在淡出结束后运行，此时画面是透明的，一次性重算样式不会被看见，
    回来后新配色的内容再淡入，观感上没有「整窗闪一下」的顿感。
    """
    def _run():
        try:
            apply_work()
        finally:
            fade_in(widget, duration=back, dy=0)

    return fade_out(widget, duration=out, on_done=_run)


def switch_with_fade(old: QWidget, new: QWidget, duration: int = 240) -> QAbstractAnimation:
    """切换两个同位置视图，新视图淡入。

    可见性必须在**函数里**切完，不能等动画结束信号：`QAbstractAnimation.stop()`
    不触发 finished，连续点击（概览→详情→概览）时上一段被打断，
    `new.setVisible(True)` 就永远不执行，页面停在错误状态——表现为标题消失、
    卡片被挤到底部。
    """
    old_ani = getattr(old, "_switch_ani", None)
    new_ani = getattr(new, "_switch_ani", None)
    for ani in (old_ani, new_ani):
        if ani is not None:
            ani.stop()

    # 先把两个控件的残留 effect 与动画状态清干净，再切可见性
    old.setGraphicsEffect(None)
    new.setGraphicsEffect(None)
    old.setVisible(False)
    new.setVisible(True)

    ani = fade_in(new, duration=int(duration * 0.66), dy=0)
    old._switch_ani = ani
    new._switch_ani = ani
    return ani


def _content_height(widget: QWidget) -> int:
    """取内容区不受当前最大高度约束时的完整高度。

    动画期间 maximumHeight 被压到中间值，QVBoxLayout 会把子控件一起挤矮；
    此时直接读 widget.sizeHint() 会拿到被挤过的高度，连续展开/收起就会越缩越短。
    先临时放开高度上限并让布局重算，量完再恢复，避免把动画中间态当成目标尺寸。
    """
    old_max = widget.maximumHeight()
    widget.setMaximumHeight(QWIDGETSIZE_MAX)
    lay = widget.layout()
    if lay is not None:
        lay.invalidate()
        lay.activate()
    height = widget.sizeHint().height()
    widget.setMaximumHeight(old_max)
    return height


def animate_expand(widget: QWidget, show: bool, duration: int = 210, on_done=None):
    """高度展开/收起，同时淡入淡出；收起完成后隐藏 widget。

    反复连续点击时卡片会越按越歪，根因是 `QAbstractAnimation.stop()` **不触发**
    `finished`：上一段动画被打断时，收尾逻辑（放开 maximumHeight 上限、逐级
    invalidate 父布局）一次都没跑。父布局于是始终拿着动画中途的尺寸提示，
    每打断一次就积一层误差，按几次之后卡片高度、子控件位置全偏。

    所以这里把「收尾」与「动画结束」解耦：打断前先把上一段的收尾强制执行一遍，
    让状态落到确定值（要么全开、要么全收），再开始新的一段。
    """
    prev = getattr(widget, "_expand_ani", None)
    prev_finish = getattr(widget, "_expand_finish", None)
    if prev is not None:
        prev.stop()
        # stop() 不会发 finished，手动补上收尾，别让父布局停在半截尺寸上
        if prev_finish is not None:
            prev_finish()

    eff = _opacity_effect(widget)
    full = _content_height(widget)
    target = full if show else 0
    # 收尾已把状态归一，这里从「确定的当前态」起步：
    # 展开时从 0 长出来，收起时从满高缩回去，不再接中间值
    start = widget.height() if (show and widget.isVisible()) else (
        full if not show else 0)
    start = max(0, min(start, full))

    opacity_start = 0.0
    if show:
        widget.setVisible(True)
        eff.setOpacity(0.0)
        widget.setMaximumHeight(start)
    else:
        # 上一次展开动画可能停在半透明状态；收起从当前可见度开始，不再突然闪亮
        opacity_start = eff.opacity()

    height = QPropertyAnimation(widget, b"maximumHeight", widget)
    height.setDuration(duration)
    height.setStartValue(start)
    height.setEndValue(target)
    height.setEasingCurve(QEasingCurve.Type.OutCubic)

    opa = QPropertyAnimation(eff, b"opacity", widget)
    opa.setDuration(duration)
    opa.setStartValue(opacity_start)
    opa.setEndValue(1.0 if show else 0.0)
    opa.setEasingCurve(QEasingCurve.Type.OutCubic)

    group = QParallelAnimationGroup(widget)
    group.addAnimation(height)
    group.addAnimation(opa)

    finished = {"done": False}

    def _done():
        # 同一个 group 的收尾只跑一次（finished 与打断后的补调可能都到）
        if finished["done"]:
            return
        finished["done"] = True
        if show:
            widget.setVisible(True)
        else:
            # 先隐藏再放开高度上限，别让父布局在 visible=True 的瞬间按完整
            # 高度把卡片撑大，留下收起后仍比原来高十几像素的空白。
            widget.setVisible(False)
        # 无论展开还是收起都放开上限：动画期间被压住的高度限制是错位的源头，
        # 归一后由布局按 sizeHint 重新决定真实高度
        widget.setMaximumHeight(QWIDGETSIZE_MAX)
        lay = widget.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()
        widget.setGraphicsEffect(None)
        if getattr(widget, "_expand_ani", None) is group:
            widget._expand_ani = None
            widget._expand_finish = None
        # maximumHeight 动画只通知直接父布局；卡片外层（页面 ScrollArea 的布局）
        # 仍拿着动画中途的旧尺寸提示，连续点击后就会把卡片压在半高、子控件也被挤矮。
        # 必须等本轮事件循环处理完动画的最后一个 resize，再从内容区往上逐级重排。
        def _relayout():
            node = widget.parentWidget()
            while node is not None:
                node.updateGeometry()
                lay = node.layout()
                if lay is not None:
                    lay.invalidate()
                    lay.activate()
                node = node.parentWidget()

        QTimer.singleShot(0, _relayout)
        if on_done:
            on_done()

    group.finished.connect(_done)
    widget._expand_ani = group
    # 供下一次打断时强制收尾
    widget._expand_finish = _done
    group.start()
    return group


def value_animation(widget: QWidget, start: float, end: float,
                    on_value, duration: int = 520) -> QVariantAnimation:
    """数值补间；用于统计数字、条形图、hover 强调条。

    回调会被包一层存活检查：页面在这 520ms 里被销毁时（切页、关窗、测试收尾），
    动画仍会继续发 `valueChanged`，而回调闭包里抓的是已销毁的控件——PyQt5
    遇到悬空对象直接 abort，没有 Python 异常可捕。QPropertyAnimation 绑在自己的
    父控件上，Qt 会随控件一起删；回调不是，得自己看住。
    """
    prev = getattr(widget, "_value_ani", None)
    if prev is not None:
        prev.stop()
    ani = QVariantAnimation(widget)
    ani.setDuration(duration)
    ani.setStartValue(start)
    ani.setEndValue(end)
    ani.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _emit(value):
        from PyQt5 import sip

        if sip.isdeleted(widget):
            ani.stop()
            return
        on_value(value)

    ani.valueChanged.connect(_emit)

    def _done():
        widget._value_ani = None

    ani.finished.connect(_done)
    widget._value_ani = ani
    ani.start()
    return ani
