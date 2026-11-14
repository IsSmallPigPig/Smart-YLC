"""页面内交互动画：淡入、交叉切换、高度展开。

只做视觉过渡，不改变布局里的内容与顺序。动画对象挂在目标 widget 上，
避免 QPropertyAnimation 被 Python GC 提前回收导致动画停在半途。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QVariantAnimation,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

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


def animate_expand(widget: QWidget, show: bool, duration: int = 210, on_done=None):
    """高度展开/收起，同时淡入淡出；收起完成后隐藏 widget。"""
    prev = getattr(widget, "_expand_ani", None)
    if prev is not None:
        prev.stop()

    eff = _opacity_effect(widget)
    target = widget.sizeHint().height() if show else 0
    start = widget.height() if widget.isVisible() else 0

    if show:
        widget.setVisible(True)
        eff.setOpacity(0.0)
        widget.setMaximumHeight(start)

    height = QPropertyAnimation(widget, b"maximumHeight", widget)
    height.setDuration(duration)
    height.setStartValue(start)
    height.setEndValue(target)
    height.setEasingCurve(QEasingCurve.Type.OutCubic)

    opa = QPropertyAnimation(eff, b"opacity", widget)
    opa.setDuration(duration)
    opa.setStartValue(0.0 if show else 1.0)
    opa.setEndValue(1.0 if show else 0.0)
    opa.setEasingCurve(QEasingCurve.Type.OutCubic)

    group = QParallelAnimationGroup(widget)
    group.addAnimation(height)
    group.addAnimation(opa)

    def _done():
        widget.setMaximumHeight(QWIDGETSIZE_MAX)
        widget.setGraphicsEffect(None)
        if not show:
            widget.setVisible(False)
        widget._expand_ani = None
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
