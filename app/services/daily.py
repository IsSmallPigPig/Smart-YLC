"""每日团员检查（后台模式用）：登录 → 拉取团员 → 三项检查 → 摘要文本。"""

from __future__ import annotations

import os

from app.domain.check_rules import (
    build_daily_summary,
    build_fee_results,
    build_identity_results,
    build_report_results,
    cross_check_roster,
)
from app.services.api import SiteClient
from app.services import store


def run_daily_check() -> tuple[bool, str]:
    """执行一次每日检查。返回 (ok, 摘要或错误信息)。"""
    cfg = store.load()
    accounts = cfg.get("accounts") or []
    if not accounts:
        return False, "未配置团支部账号，无法执行每日检查"
    acc = next((a for a in accounts if a["userName"] == cfg.get("activeUser")), None) or accounts[0]

    client = SiteClient(acc["userName"], acc.get("password", ""))
    verdict = client.session_account()
    if not verdict.ok:
        verdict = client.login()
        if not verdict.ok:
            kind = (verdict.data or {}).get("kind") if isinstance(verdict.data, dict) else ""
            if kind == "captcha":
                return False, f"账号 {acc['userName']} 需要验证码（先在网页端登录一次）"
            return False, f"账号 {acc['userName']} 登录失败：{verdict.message}"

    members, _ = client.get_all_members()

    results = []
    results += build_identity_results(members, _identity_template())
    results += build_report_results(members, _report_template())
    results += build_fee_results(members)

    roster = None
    roster_path = cfg.get("rosterPath")
    if roster_path and os.path.isfile(roster_path):
        try:
            from app.services.excel import import_roster

            roster = import_roster(roster_path)
        except Exception:
            roster = None  # 名单失败不阻断检查，摘要里会体现未匹配
    cross = cross_check_roster(members, roster, "all", [])
    return True, build_daily_summary(members, results, cross)


def _identity_template():
    from app.domain.check_rules import CheckTemplate, TemplateField

    return CheckTemplate("identity", "报到身份", [
        TemplateField("real_name_status", "实名认证", "已认证", "contains"),
        TemplateField("ebook_status", "电子志愿书", "已上传", "contains"),
        TemplateField("ecard_photo_status", "团员证照片", "已上传", "contains"),
    ])


def _report_template():
    from app.domain.check_rules import CheckTemplate, TemplateField

    return CheckTemplate("report", "报到情况", [
        TemplateField("report_data_status", "报到资料状态", "已审核", "contains"),
        TemplateField("report_apply_at", "报到申请时间", "", "notEmpty"),
        TemplateField("report_audit_at", "报到审核时间", "", "notEmpty"),
    ])
