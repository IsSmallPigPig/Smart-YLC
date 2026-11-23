"""字段别名表 + 站点整数枚举 → 中文状态（对照网页端 league_member.js 实测）。

字段键来自站点团员列表真实响应（POST /api/v1/admin/members/page，参数 page/pageSize）；
状态列全是整数枚举，映射表抄自前端 league_member.js 的formatter变量。
"""

from __future__ import annotations

from app.domain.check_rules import Diagnostic, MemberRecord

# 网页端 league_member.js 的状态枚举（逐字对照）
IS_REAL_NAME_NAME = {"10": "通过", "1": "校验中", "2": "校验失败", "3": "线下验证通过", "4": "校验失败"}
AUDIT_STATUS_NAME = {
    "0": "报到被撤回", "1": "报到待审核", "2": "报到被退回", "3": "审核通过",
    "4": "修改资料待审核", "5": "修改资料被退回", "6": "已报到,电子志愿书未上传",
    "7": "电子志愿书待上传", "8": "待省级管理员审核",
}
VOLUNTEER_BOOK_STATUS_NAME = {
    "0": "-", "1": "不通过", "2": "通过", "3": "修改待审核", "4": "未上传", "5": "已上传",
}
ECARD_PHOTO_STATUS_NAME = {"0": "上传待审核", "1": "修改待审核", "2": "审核通过", "3": "审核不通过"}
DEGREE_OF_EDUCATION_NAME = {
    "1": "初中", "2": "高中", "3": "大专", "4": "本科", "5": "硕士", "6": "博士",
    "7": "中职", "8": "中职中专在读", "9": "初中在读", "10": "高中在读",
    "11": "大专在读", "12": "本科在读", "13": "硕士在读", "14": "博士在读", "15": "小学",
}
OCCUPATION_NAME = {
    "1": "国有企业职工", "2": "非公企业职工", "3": "机关事业单位职工", "4": "社会组织员工",
    "5": "农民", "6": "学生", "7": "自由职业者", "8": "公办高校教职工", "9": "公办中学教职工",
    "10": "公办中职教职工", "11": "民办高校教职工", "12": "民办中学教职工",
    "13": "民办中职教职工", "14": "其他",
}

ALIASES: dict[str, list[str]] = {
    "member_id": ["mid", "memberId", "id", "userId", "编号"],
    "name": ["name", "memberName", "userName", "姓名"],
    "mobile": ["mobile", "phone", "phoneNo", "tel", "手机号码"],
    "id_type": ["idCardType", "idType", "cardType", "certificateType", "证件类型"],
    "real_name_status": ["isRealName", "realNameStatus", "authenticationStatus", "实名认证状态"],
    "political_status": ["politicalOutlook", "politicalStatus", "政治面貌"],
    "full_time_education": ["degreeOfEducation", "highestEducation", "fullTimeEducation", "全日制学历"],
    "occupation": ["occupation", "job", "职业"],
    "income": ["incomeStr", "income", "收入"],
    "work_unit": ["learningUnit", "workUnit", "workLearnUnit", "工作学习单位"],
    "join_date": ["leagueForYears", "joinDate", "入团时间"],
    "report_apply_at": ["createTimeStr", "reportApplyAt", "报到申请时间"],
    "report_audit_at": ["auditTimeStr", "reportAuditAt", "报到审核时间"],
    "profile_modify_at": ["editApplyTime", "profileModifyAt", "资料修改时间"],
    "profile_modify_audit_at": ["editAuditTime", "profileModifyAuditAt", "资料修改审核时间"],
    "report_data_status": ["auditStatus", "reportDataStatus", "报到资料状态"],
    "ebook_status": ["volunteerBookModifyStatus", "ebookStatus", "电子志愿书状态"],
    "ecard_photo_status": ["electronicHeadPictureStatus", "ecardPhotoStatus", "电子团员证照片状态"],
    "award_status": ["rewardStatusStr", "awardStatus", "奖励状态"],
    "unpaid_total": ["fees", "unpaidTotal", "notPayTotal", "未交团费总额（元）", "未交团费总额(元)", "未交团费总额"],
    "unpaid_months": ["feesMonth", "unpaidMonths", "累计未交月份", "累计交月份数"],
    "oid": ["oid", "orgId", "leagueId", "组织编号"],
    "org_name": ["fullName", "orgName", "leagueName", "所属团支部", "团支部名称"],
}

_CRITICAL = ("real_name_status", "report_data_status", "ebook_status", "ecard_photo_status")

# 枚举字段：解析时把原始 int 转成前端同款中文，未知值保留原文并出诊断
_ENUM_FIELDS = {
    "real_name_status": IS_REAL_NAME_NAME,
    "report_data_status": AUDIT_STATUS_NAME,
    "ebook_status": VOLUNTEER_BOOK_STATUS_NAME,
    "ecard_photo_status": ECARD_PHOTO_STATUS_NAME,
    "full_time_education": DEGREE_OF_EDUCATION_NAME,
    "occupation": OCCUPATION_NAME,
}


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


def _has(raw: dict, keys: list[str]) -> bool:
    """字段是否真的没返回；返回 null 也算返回了，交给枚举翻译处理。"""
    lower = {str(k).lower() for k in raw}
    return any(k in raw or k.lower() in lower for k in keys)


def _str(v) -> str:
    if v is None or isinstance(v, (dict, list)):
        return ""
    return str(v).strip()


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _ms_to_date(v) -> str:
    """入团时间是毫秒时间戳 → YYYY-MM-DD；无法解析返回原字符串。"""
    s = _str(v)
    try:
        from datetime import datetime

        return datetime.fromtimestamp(int(s) / 1000).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return s


def _translate_enum(key: str, raw_value, row: dict) -> tuple[str, str | None]:
    """枚举字段 → 前端同款中文；返回 (值, 诊断原因)。"""
    s = _str(raw_value)
    table = _ENUM_FIELDS.get(key)
    if table is None:
        return s, None
    if s == "":
        # 电子团员证照片：状态与原图都为空 → 前端显示「未上传」
        if key == "ecard_photo_status" and row.get("electronicHeadPicture") is None:
            return "未上传", None
        # 电子志愿书：状态为空时按报到资料状态派生（前端语义：6=已报到,电子志愿书未上传；7=电子志愿书待上传）
        if key == "ebook_status" and _str(row.get("auditStatus")) in ("6", "7"):
            return "未上传", None
        return "", None
    if s in table:
        return table[s], None
    # 特例：报到资料状态——关键信息待审核时前端显示「实名信息待审核」
    if key == "report_data_status" and _str(row.get("keyInformationStatus")) == "4":
        return "实名信息待审核", None
    return s, f"状态值 {s} 不在站点枚举表中，请复核（{key}）"


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
        # 字段在、值是 null 是正常的「未采集」；只有键真的消失才报接口漂移。
        if not _has(raw, ALIASES.get(key, [key])):
            diagnostics.append(Diagnostic(
                "warn", "parse", f"{name}({mobile})", "PARSE_DRIFT",
                f"接口未返回「{key}」字段，该字段按空值参与比对，可能误报；请复核字段别名表",
            ))

    def get_translated(key: str) -> str:
        value, reason = _translate_enum(key, get(key), raw)
        if reason:
            diagnostics.append(Diagnostic("warn", "parse", f"{name}({mobile})", "PARSE_DRIFT", reason))
        return value

    member = MemberRecord(
        member_id=_str(get("member_id")) or f"{name}::{mobile}",
        name=name,
        mobile=mobile,
        id_type=_str(get("id_type")),
        real_name_status=get_translated("real_name_status"),
        political_status=_str(get("political_status")),
        full_time_education=get_translated("full_time_education"),
        occupation=get_translated("occupation"),
        income=_str(get("income")),
        work_unit=_str(get("work_unit")),
        join_date=_ms_to_date(get("join_date")),
        report_apply_at=_str(get("report_apply_at")),
        report_audit_at=_str(get("report_audit_at")),
        profile_modify_at=_str(get("profile_modify_at")),
        profile_modify_audit_at=_str(get("profile_modify_audit_at")),
        report_data_status=get_translated("report_data_status"),
        ebook_status=get_translated("ebook_status"),
        ecard_photo_status=get_translated("ecard_photo_status"),
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
        for k in ("listData", "list", "rows", "records", "data", "items", "content"):
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
