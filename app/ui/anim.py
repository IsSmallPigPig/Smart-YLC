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
    """旧视图淡出后切换可见性，新视图淡入。两者同属一个布局、同一位置。"""
    old_ani = getattr(old, "_switch_ani", None)
    new_ani = getattr(new, "_switch_ani", None)
    for ani in (old_ani, new_ani):
        if ani is not None:
            ani.stop()

    eff_old = _opacity_effect(old)
    eff_old.setOpacity(1.0)
    out = QPropertyAnimation(eff_old, b"opacity", old)
    out.setDuration(int(duration * 0.38))
    out.setStartValue(1.0)
    out.setEndValue(0.0)
    out.setEasingCurve(QEasingCurve.Type.InCubic)

    group = QSequentialAnimationGroup(old)
    group.addAnimation(out)

    def _swap():
        old.setGraphicsEffect(None)
        old.setVisible(False)
        new.setVisible(True)
        fade_in(new, duration=int(duration * 0.66), dy=6)

    def _done():
        old._switch_ani = None
        new._switch_ani = None

    group.finished.connect(_swap)
    group.finished.connect(_done)
    old._switch_ani = group
    new._switch_ani = group
    group.start()
    return group


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
    """高度展开/收起，同时淡入淡出；收起完成后隐藏 widget。"""
    prev = getattr(widget, "_expand_ani", None)
    if prev is not None:
        prev.stop()

    eff = _opacity_effect(widget)
    target = _content_height(widget) if show else 0
    start = widget.height() if widget.isVisible() else 0

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

    def _done():
        if show:
            # 高度上限放开后强制布局按完整尺寸重排：动画期间被挤矮的子控件
            # 必须回到 sizeHint，否则下一次展开会把缩水后的高度当成起点。
            widget.setMaximumHeight(QWIDGETSIZE_MAX)
            lay = widget.layout()
            if lay is not None:
                lay.invalidate()
                lay.activate()
        else:
            # 先隐藏再放开高度上限，别让父布局在 visible=True 的瞬间按完整
            # 高度把卡片撑大，留下收起后仍比原来高十几像素的空白。
            widget.setVisible(False)
            widget.setMaximumHeight(QWIDGETSIZE_MAX)
        widget.setGraphicsEffect(None)
        widget._expand_ani = None
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
    group.start()
    return group


def value_animation(widget: QWidget, start: float, end: float,
                    on_value, duration: int = 520) -> QVariantAnimation:
    """数值补间；用于统计数字、条形图、hover 强调条。"""
    prev = getattr(widget, "_value_ani", None)
    if prev is not None:
        prev.stop()
    ani = QVariantAnimation(widget)
    ani.setDuration(duration)
    ani.setStartValue(start)
    ani.setEndValue(end)
    ani.setEasingCurve(QEasingCurve.Type.OutCubic)
    ani.valueChanged.connect(on_value)

    def _done():
        widget._value_ani = None

    ani.finished.connect(_done)
    widget._value_ani = ani
    ani.start()
    return ani
