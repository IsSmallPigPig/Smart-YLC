"""检查规则警告级与职业/收入一致性测试。"""

from app.domain.check_rules import (
    MemberRecord,
    TemplateField,
    CheckTemplate,
    compare_with_template,
    build_identity_results,
)


def _member(**kw) -> MemberRecord:
    # 正确数据基线（对齐站点参考截图）：职业=学生，收入=职业为学生无收入
    base = dict(name="张三", mobile="13800000000", real_name_status="通过",
                report_data_status="审核通过", ebook_status="已上传",
                ecard_photo_status="未上传",
                occupation="学生", income="职业为学生无收入")
    base.update(kw)
    return MemberRecord(**base)


def test_empty_occupation_is_warning_not_error():
    """职业未填 → 警告级，不计入错误。"""
    from app.ui.check_page import default_identity_template
    results = build_identity_results([_member(occupation="")], default_identity_template())
    assert results and not results[0].is_error
    assert results[0].diffs[0].severity == "warning"
    assert "职业未填" in results[0].diffs[0].reason


def test_default_template_not_flag_everyone_for_ecard():
    """全员团员证照片未上传不应被判为信息错误（站点语义：显示「无异常」）。"""
    members = [_member() for _ in range(3)]
    from app.ui.check_page import default_identity_template
    results = build_identity_results(members, default_identity_template())
    assert not any(r.is_error for r in results)


def test_non_student_occupation_is_error():
    """刘雨嘉案例：职业=非公企业职工、单位=赫基集团 → 信息错误。"""
    m = _member(occupation="非公企业职工", work_unit="赫基集团")
    from app.ui.check_page import default_identity_template
    results = build_identity_results([m], default_identity_template())
    assert results and results[0].is_error
    assert any(d.key == "occupation" and d.severity == "error" for d in results[0].diffs)


def test_income_mismatch_is_error():
    """收入不是「学生无收入」→ 错误（含为空）。"""
    from app.ui.check_page import default_identity_template
    for income in ("有固定工资收入", ""):
        m = _member(income=income)
        results = build_identity_results([m], default_identity_template())
        assert results and results[0].is_error, income
        assert any(d.key == "income" for d in results[0].diffs)


def test_correct_member_not_flagged():
    """参考截图案例：学生 + 职业为学生无收入 → 无异常。"""
    m = _member(full_time_education="高中", work_unit="无")
    from app.ui.check_page import default_identity_template
    assert not build_identity_results([m], default_identity_template())


def test_real_name_failure_is_error():
    m = _member(real_name_status="校验失败")
    from app.ui.check_page import default_identity_template
    results = build_identity_results([m], default_identity_template())
    assert results and results[0].is_error


def test_custom_template_compare_still_works():
    m = _member(occupation="")
    tpl = CheckTemplate("identity", "报到身份", [
        TemplateField("occupation", "职业", "", "notEmpty", True, "warning"),
    ])
    diffs = compare_with_template(m, tpl)
    assert diffs and diffs[0].severity == "warning"
    results = build_identity_results([m], tpl)
    assert results and not results[0].is_error
