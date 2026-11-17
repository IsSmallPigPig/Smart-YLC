"""账号健康状态枚举 + 调用方账号过滤（纯函数）。

批量检查/组织状况/组织管理/系统消息/批量上传/后台快刷等页面会以账号身份
调用站点接口，只用健康状态可用的账号；被明确标记为异常（密码错误/需验证码/
登录冷却/需改密/需姓名校验/网络错误）的账号不得调用。

「未检测」（还没测过活）放行：测活是唯一能把账号变成可用的途径，
调用方过滤不能把新导入的账号锁死。调用方拿到排除名单后必须在界面
明示哪些账号被跳过及原因，不允许静默过滤。
"""

from __future__ import annotations

HEALTH_LABEL = {
    "ok": "正常",
    "failed": "密码错误",
    "captcha": "需验证码（先去网页端登录一次）",
    "cooldown": "登录冷却（验证码/风控，1 小时后自动重试）",
    "passwordChange": "需改密（EXPIREDPASS/WEAKPASS）",
    "needMemberName": "需姓名校验",
    "network": "网络错误",
    "unknown": "未检测",
}

# 健康状态 → 语义色角色名。颜色值交给 UI 层的 palette 按明暗主题解析，
# 领域层不持有具体色值，避免同一个状态在两种主题下写成两套 hex。
HEALTH_ROLE = {
    "ok": "success",
    "failed": "danger",
    "captcha": "warning",
    "cooldown": "muted",
    "passwordChange": "warning",
    "needMemberName": "warning",
    "network": "muted",
    "unknown": "muted",
}

USABLE_STATUSES = {"ok", "unknown", ""}


def account_status(acc: dict) -> str:
    return str((acc.get("health") or {}).get("status") or "")


def split_accounts(accounts: list[dict]) -> tuple[list[dict], list[dict]]:
    """按健康状态拆分：返回 (可调用账号, 被排除账号)。保持原顺序。"""
    usable: list[dict] = []
    excluded: list[dict] = []
    for a in accounts:
        (usable if account_status(a) in USABLE_STATUSES else excluded).append(a)
    return usable, excluded


def excluded_summary(excluded: list[dict]) -> str:
    """通知里的一句话概括，只报数量。

    逐个账号带原因铺进弹窗会撑成一屏字、还看不到重点；账号明细在系统配置的
    账号状态列里查。
    """
    return f"已跳过 {len(excluded)} 个异常账号"
