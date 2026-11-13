"""字段别名表（移植自 field-map.ts）：legacy ↔ v1 ↔ 中文表头 的统一别名。"""

from __future__ import annotations

from app.domain.check_rules import Diagnostic, MemberRecord

ALIASES: dict[str, list[str]] = {
    "member_id": ["memberId", "id", "mid", "userId", "编号"],
    "name": ["name", "memberName", "userName", "姓名"],
    "mobile": ["mobile", "phone", "phoneNo", "tel", "手机号码"],
    "id_type": ["idType", "cardType", "idCardType", "certificateType", "证件类型"],
    "real_name_status": ["realNameStatus", "authenticationStatus", "实名认证状态"],
    "political_status": ["politicalStatus", "political", "政治面貌"],
    "full_time_education": ["fullTimeEducation", "degreeOfEducation", "education", "全日制学历"],
    "occupation": ["occupation", "job", "jobType", "职业"],
    "income": ["income", "收入"],
    "work_unit": ["workUnit", "workLearnUnit", "workplace", "工作学习单位", "工作单位"],
    "join_date": ["joinDate", "joinLeagueTime", "入团时间"],
    "report_apply_at": ["reportApplyAt", "报到申请时间"],
    "report_audit_at": ["reportAuditAt", "报到审核时间"],
    "profile_modify_at": ["profileModifyAt", "资料修改时间"],
    "profile_modify_audit_at": ["profileModifyAuditAt", "资料修改审核时间"],
    "report_data_status": ["reportDataStatus", "auditStatus", "报到资料状态"],
    "ebook_status": ["ebookStatus", "voluntaryBookStatus", "电子志愿书状态"],
    "ecard_photo_status": ["ecardPhotoStatus", "证件照片状态", "电子团员证照片状态"],
    "award_status": ["awardStatus", "rewardStatus", "奖励状态"],
    "unpaid_total": ["unpaidTotal", "fees", "notPayTotal", "未交团费总额（元）", "未交团费总额(元)", "未交团费总额"],
    "unpaid_months": ["unpaidMonths", "feesMonth", "payStr", "累计未交月份", "累计交月份数"],
    "oid": ["oid", "orgId", "leagueId", "组织编号"],
    "org_name": ["orgName", "leagueName", "所属团支部", "团支部名称"],
}

_CRITICAL = ("political_status", "full_time_education", "occupation", "income",
             "work_unit", "report_data_status", "ebook_status")


def _pick(raw: dict, keys: list[str]):
    for k in keys:
        v = raw.get(k)
        if v is not None and v != "":
            return v
    lower = {str(k).lower(): v for k, v in raw.items()}
    for k in keys:
        v = lower.get(k.lower())
        if v is not None and v != "":
            return v
    return None


def _str(v) -> str:
    if v is None or isinstance(v, (dict, list)):
        return ""
    return str(v).strip()


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_member(raw: dict, oid: str = "") -> tuple[MemberRecord, list[Diagnostic]]:
    """字段缺失不静默补默认值：产出 PARSE_DRIFT 诊断并保留原始信息。"""
    diagnostics: list[Diagnostic] = []

    def get(key: str):
        return _pick(raw, ALIASES.get(key, [key]))

    name = _str(get("name"))
    mobile = _str(get("mobile"))
    if not name or not mobile:
        raise ValueError(f"接口返回缺少姓名或手机号码字段，无法识别团员：{str(raw)[:200]}")

    for key in _CRITICAL:
        if get(key) is None:
            diagnostics.append(Diagnostic(
                "warn", "parse", f"{name}({mobile})", "PARSE_DRIFT",
                f"接口未返回「{key}」字段，该字段按空值参与比对，可能误报；请复核字段别名表",
            ))

    member = MemberRecord(
        member_id=_str(get("member_id")) or f"{name}::{mobile}",
        name=name,
        mobile=mobile,
        id_type=_str(get("id_type")),
        real_name_status=_str(get("real_name_status")),
        political_status=_str(get("political_status")),
        full_time_education=_str(get("full_time_education")),
        occupation=_str(get("occupation")),
        income=_str(get("income")),
        work_unit=_str(get("work_unit")),
        join_date=_str(get("join_date")),
        report_apply_at=_str(get("report_apply_at")),
        report_audit_at=_str(get("report_audit_at")),
        profile_modify_at=_str(get("profile_modify_at")),
        profile_modify_audit_at=_str(get("profile_modify_audit_at")),
        report_data_status=_str(get("report_data_status")),
        ebook_status=_str(get("ebook_status")),
        ecard_photo_status=_str(get("ecard_photo_status")),
        award_status=_str(get("award_status")),
        unpaid_total=_num(get("unpaid_total")),
        unpaid_months=int(_num(get("unpaid_months"))),
        oid=_str(get("oid")) or oid,
        org_name=_str(get("org_name")),
    )
    return member, diagnostics


def pick_list(raw) -> tuple[list[dict], int]:
    """从各种信封里取列表与总数；找不到就抛漂移错误，绝不返回空列表假装成功。"""
    if not isinstance(raw, dict):
        raise ValueError(f"接口响应不是对象：{str(raw)[:200]}")
    for c in (raw.get("data"), raw.get("msg"), raw.get("result"), raw):
        if not isinstance(c, dict):
            continue
        for k in ("list", "rows", "records", "data", "items", "content"):
            if isinstance(c.get(k), list):
                rows = c[k]
                total = c.get("total") or c.get("totalCount") or c.get("count") or raw.get("total") or len(rows)
                try:
                    total = int(total)
                except (TypeError, ValueError):
                    total = len(rows)
                return rows, total
    if "EXPIRE" in str(raw.get("code") or raw.get("status") or "").upper():
        raise ValueError("登录已失效，请重新登录")
    raise ValueError(f"无法从响应中解析团员列表：{str(raw)[:300]}")
