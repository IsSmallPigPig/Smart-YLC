"""使用引导：告诉用户要配置什么、能做什么。

展示时机按「学年」算：每年 9 月 1 日之后属于新学年，配置会变（新一届团支部、
新的档案目录），所以进新学年后再弹一次。判定逻辑是纯函数（school_cycle /
should_show_guide），便于测试；写回配置用 mark_guide_shown。

入口有两处：主窗口启动时自动判断一次；系统配置的「关于项目」里可随时再看。
"""

from __future__ import annotations

from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    FluentIcon as FIF,
    IconWidget,
    StrongBodyLabel,
    SubtitleLabel,
)

from app.services import store
from app.ui import palette as P
from app.ui.dialogs import FluentPanelDialog, strip_center_border
from app.ui.page import note_label, section_title

CYCLE_MONTH, CYCLE_DAY = 9, 1
"""新学年的分界：9 月 1 日。之后再次展示引导。"""


def school_cycle(now: datetime) -> int:
    """日期属于哪个学年：9 月 1 日（含）之后算当年，之前算上一学年。"""
    if (now.month, now.day) >= (CYCLE_MONTH, CYCLE_DAY):
        return now.year
    return now.year - 1


def should_show_guide(cfg: dict, now: datetime) -> bool:
    """该不该弹引导：从没弹过，或已经进入比上次展示更晚的学年。"""
    seen = str(cfg.get("guideShownAt") or "")
    if not seen:
        return True
    try:
        shown = datetime.fromisoformat(seen)
    except ValueError:
        return True
    return school_cycle(now) > school_cycle(shown)


def mark_guide_shown(now: datetime | None = None) -> None:
    moment = now or datetime.now()
    store.update(lambda cfg: cfg.__setitem__("guideShownAt", moment.isoformat()))


class _GuideRow(QWidget):
    """一条说明：图标 + 标题 + 一句解释，行距交给外层。"""

    def __init__(self, icon, title: str, desc: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 3, 0, 3)
        lay.setSpacing(12)

        self.icon = IconWidget(icon, self)
        self.icon.setFixedSize(18, 18)
        lay.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(StrongBodyLabel(title, self))
        text.addWidget(note_label(desc))
        lay.addLayout(text, 1)


class GuideDialog(FluentPanelDialog):
    """使用引导：上半「开始前配置」，下半「它能帮你做什么」。"""

    CONFIG_STEPS = (
        (FIF.PEOPLE, "团支部账号",
         "系统配置 → 添加各班团支部账号与密码。密码错误会在账号卡片上标出，可单独重登。"),
        (FIF.FOLDER, "来源文件夹",
         "上传档案 → 添加文件夹，档案按「姓名+手机号+入团志愿书」命名，子文件夹一起扫描。"),
        (FIF.CLOUD, "云端备份（可选）",
         "资源工具 → 云端备份：设提取码与密码，换电脑时能把配置拉回来；"
         "手机同步同 Wi-Fi 扫码传 PDF。"),
    )

    FEATURES = (
        (FIF.SEARCH, "上传前自检",
         "命名、页数、名单、表格空格全部自动检查；异常档案标黄并移入「异常档案」，不会误传。"),
        (FIF.SEND, "一键上传",
         "自动压到目标体积、按支部分流，失败原因逐条写清。"),
        (FIF.PEOPLE, "组织状况与管理",
         "团费、报到、档案缺口按支部汇总，可筛选、可导出名单。"),
        (FIF.MESSAGE, "系统消息",
         "站内信集中查看，重要通知可直接回填。"),
        (FIF.GLOBE, "访问支部",
         "每个支部独立登录的内嵌浏览器，多账号可同时在线。"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        strip_center_border(self)
        self.widget.setMinimumWidth(660)

        lay = self.viewLayout
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_body())

        self.yesButton.setText("开始使用")
        self.cancelButton.hide()

    def _build_body(self):
        """正文：配置项一栏、功能两栏，各自收在一张卡片里，层次与设置页一致。"""
        body = QWidget()
        inner = QVBoxLayout(body)
        inner.setContentsMargins(32, 26, 32, 10)
        inner.setSpacing(12)

        title = SubtitleLabel("欢迎使用智慧团委助手", body)
        font = title.font()
        font.setWeight(QFont.Weight.DemiBold)
        title.setFont(font)
        inner.addWidget(title)
        inner.addWidget(note_label("先把下面三项配置好，之后每学期只做「扫描 → 核对 → 上传」。"))

        config_card = self._card()
        config_lay = config_card.layout()
        config_lay.addWidget(section_title("开始前配置"))
        for icon, head, desc in self.CONFIG_STEPS:
            config_lay.addWidget(_GuideRow(icon, head, desc, config_card))
        inner.addWidget(config_card)

        feature_card = self._card()
        feature_lay = feature_card.layout()
        feature_lay.addWidget(section_title("它能帮你做什么"))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(10)
        for i, (icon, head, desc) in enumerate(self.FEATURES):
            grid.addWidget(_GuideRow(icon, head, desc, feature_card), i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        feature_lay.addLayout(grid)
        inner.addWidget(feature_card)

        inner.addWidget(note_label("每学年（9 月 1 日起）会再提示一次，提醒核对账号与文件夹。"))
        return body

    @staticmethod
    def _card():
        from qfluentwidgets import CardWidget

        card = CardWidget()
        card.setBorderRadius(P.RADIUS["card"])
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(6)
        return card
