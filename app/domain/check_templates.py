"""三项检查的默认模板（纯数据，界面与导出共用，不依赖 Qt）。"""

from __future__ import annotations

from app.domain.check_rules import CheckTemplate, TemplateField


def default_identity_template() -> CheckTemplate:
    # 判定词对照站点真实枚举：isRealName→通过。
    # 站点语义：团员证照片未上传不算异常（站点自身显示「无异常」），志愿书状态只用于上传进度。
    # 职业检查在 check_rules._occupation_diff（未填→警告，非学生→错误）；
    # 这里只放收入一致性：收入应为「职业为学生无收入」，不一致（含为空）→ 错误。
    return CheckTemplate("identity", "报到身份", [
        TemplateField("real_name_status", "实名认证", "通过", "contains", True, "error"),
        TemplateField("income", "收入", "学生无收入", "contains", True, "error"),
    ])


def default_report_template() -> CheckTemplate:
    return CheckTemplate("report", "报到情况", [
        TemplateField("report_data_status", "报到资料状态", "审核通过", "contains", True, "error"),
    ])
