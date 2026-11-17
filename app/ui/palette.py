"""统一配色中心（Fluent 2）。

语义色与中性文字色按 Fluent 2 的明暗两套值集中在这里；异常、警告、成功、
信息等状态在两种主题下用各自对应的色值，不再靠 .darker() 猜一个暗色版本。

用法：
    P.fg("danger")                 # 当前主题下的 QColor，给画笔/数字用
    P.text_colors("danger")        # (浅色, 深色) 两个 QColor，给 setTextColor 用
    P.solid("danger")              # 当前主题下的 hex 字符串，给自绘标签用
    P.KIND["fees"]                 # 分类色（非语义色，用于区分异常类别）

分类色 KIND 刻意保持四种不同色相：Fluent 2 规定语义色只用于传达状态，
不能拿四种语义色当装饰给类别上色，否则「红色=异常」的约定会被稀释。

Fluent 2 语义色参考：https://fluent2.microsoft.design/color
"""

from __future__ import annotations

from PyQt5.QtGui import QColor
from qfluentwidgets import isDarkTheme

# 语义色 / 中性文字色 / 品牌色：(浅色, 深色)
# 中性色对应 Fluent 的 Neutral Foreground 1/2/3。
# fees 沿用原钢蓝分类色的亮版，保证深色主题下欠缴数字读得清。
ROLES: dict[str, tuple[str, str]] = {
    "success": ("#0F7B0F", "#6CCB5F"),
    "warning": ("#9D5D00", "#FCE100"),
    "danger": ("#C42B1C", "#FF99A4"),
    "info": ("#0078D4", "#4CC2FF"),
    "brand": ("#2563EB", "#8AB4FF"),
    "fees": ("#4F7CAC", "#9DC6EF"),
    "fg": ("#242424", "#FFFFFF"),
    "fg2": ("#424242", "#C8C8C8"),
    "fg3": ("#616161", "#8A8A8A"),
    "muted": ("#616161", "#8A8A8A"),
}

# 兼容旧调用：实心徽章与图表条用的是饱和底 + 白字，两种主题都取浅色档的饱和值。
# 这里换成 Fluent 2 对应语义色的饱和档（白字对比度足够），不再是 Tailwind 色。
BRAND = ROLES["brand"][0]
BRAND_SOFT = "#3B82F6"
OK = ROLES["success"][0]
DANGER = ROLES["danger"][0]
WARNING = ROLES["warning"][0]
MUTED = ROLES["muted"][0]
TRANSFER = BRAND

# 异常分类色阶：四种色相区分不同异常类别，属于分类色而非语义色。
KIND = {
    "identity": "#2563EB",
    "report": "#0891B2",
    "archive": "#0D9488",
    "fees": "#4F7CAC",
}

# 组织状况概览圆环的分色：信息错误=黄（待核对）、欠缴团费=红（需补缴），
# 这两类是用户要动手处理的；没有报道/没有档案用中性灰，只占位不抢注意力。
RING = {
    "identity": "#EAB308",   # 黄
    "fees": "#DC2626",       # 红
    "report": "#64748B",     # 灰（线上多出、还没报到）
    "archive": "#94A3B8",    # 浅灰
}

SOURCE = {"local": MUTED, "system": BRAND, "both": OK}

# 提示卡片的背景色（Fluent 2 tint）：深浅各一套，浅色是很淡的彩色纸，深色是饱和暗底。
# 背景负责传达「需关注到什么程度」，文字仍用 fg/text_colors 的语义色——
# 「异常团员」是错误，但底色用 warning 黄提示需要关注，而不是 danger 红制造警报。
TINT: dict[str, tuple[str, str]] = {
    "warning": ("#FFF4CE", "#463414"),
    "danger": ("#FDE7E9", "#442324"),
    "success": ("#DFF6DD", "#12351A"),
    "info": ("#DEECF9", "#10304B"),
    "fees": ("#E9F0F8", "#1A3045"),
    "muted": ("#F1F1F1", "#2C2C2C"),
}
TINT_ALPHA = (200, 170)  # (浅色, 深色)：透出 mica，又不盖掉文字


def tint(role: str, alpha: int | None = None) -> QColor:
    """当前主题下提示卡片的背景色。role 写错会立刻暴露，不静默回退。"""
    light, dark = TINT[role]
    c = QColor(dark if isDarkTheme() else light)
    if alpha is None:
        alpha = TINT_ALPHA[1] if isDarkTheme() else TINT_ALPHA[0]
    c.setAlpha(alpha)
    return c


def pair(role: str) -> tuple[str, str]:
    """返回某个角色的 (浅色, 深色) hex。角色名写错会立即暴露，不静默回退。"""
    return ROLES[role]


def text_colors(role: str) -> tuple[QColor, QColor]:
    """给 CaptionLabel/BodyLabel.setTextColor 用的 (浅色, 深色) 颜色对。"""
    light, dark = ROLES[role]
    return QColor(light), QColor(dark)


def fg(role: str) -> QColor:
    """当前主题下单个 QColor，给画笔、数字、表格前景用。"""
    light, dark = ROLES[role]
    return QColor(dark if isDarkTheme() else light)


def solid(role: str) -> str:
    """当前主题下的 hex 字符串，给自绘标签 tint 用。"""
    light, dark = ROLES[role]
    return dark if isDarkTheme() else light


# ---------- 字号阶梯 ----------
# 全站只留四档，靠字重与灰阶拉层级，不再靠加字号。原来散落着 12/13/14/15/16/19/22/26/
# 28/30/36 十一种像素值，同一页里字号互相打架、视觉焦点乱跳。
# 12px 是硬下限：Windows 100% 缩放、1080p 屏上 11px 的笔画会丢。
FONT = {
    "caption": 12,   # 说明、单位、元信息
    "body": 14,      # 正文、列表主文本
    "title": 16,     # 卡片标题、区块标题
    "metric": 28,    # 大数字（统计值）
    "page": 36,      # 页面主标题（仅头图里那一个）
}


# ---------- 圆角阶梯 ----------
# 收敛成三档：控件（8）、卡片（10）、大卡/浮层（12）。
# 嵌套时按同心圆取内层：R_inner = max(0, R_outer - padding)，
# 例如卡片 10 内缩 10px 的列表项用 0～2，不再硬写 10 造成四角不平行。
RADIUS = {"control": 8, "card": 10, "panel": 12}
