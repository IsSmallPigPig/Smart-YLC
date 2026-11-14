"""检查规则警告级与职业身份启发式测试。"""

from app.domain.check_rules import (
    MemberRecord,
    TemplateField,
    CheckTemplate,
    compare_with_template,
    build_identity_results,
)


def _member(**kw) -> MemberRecord:
    base = dict(name="张三", mobile="13800000000", real_name_status="通过",
                report_data_status="审核通过", ebook_status="已上传",
                ecard_photo_status="未上传")
    base.update(kw)
    return MemberRecord(**base)


def test_warning_only_diff_is_not_error():
    m = _member(occupation="")
    tpl = CheckTemplate("identity", "报到身份", [
        TemplateField("occupation", "职业", "", "notEmpty", True, "warning"),
    ])
    diffs = compare_with_template(m, tpl)
    assert diffs and diffs[0].severity == "warning"
    # 结果不含任何 error → is_error 为 False
    results = build_identity_results([m], tpl)
    assert results and not results[0].is_error


def test_default_template_not_flag_everyone_for_ecard():
    """全员团员证照片未上传不应被判为信息错误（站点语义：显示「无异常」）。"""
    members = [_member() for _ in range(3)]
    from app.ui.check_page import default_identity_template
    results = build_identity_results(members, default_identity_template())
    # 实名通过、职业未填只是警告 → 不产生任何错误级结果
    assert not any(r.is_error for r in results)


def test_occupation_conflict_is_error():
    """学历高中、单位是学校、职业填社会职业 → 信息错误（对齐用户截图案例）。"""
    m = _member(full_time_education="高中", work_unit="广州天省实验学校",
                occupation="普通企业职工")
    from app.ui.check_page import default_identity_template
    results = build_identity_results([m], default_identity_template())
    assert results and results[0].is_error
    assert any("职业" in d.label for d in results[0].diffs)


def test_occupation_student_not_conflict():
    m = _member(full_time_education="高中", work_unit="广州天省实验学校", occupation="学生")
    from app.ui.check_page import default_identity_template
    assert not build_identity_results([m], default_identity_template())


def test_real_name_failure_is_error():
    m = _member(real_name_status="校验失败")
    from app.ui.check_page import default_identity_template
    results = build_identity_results([m], default_identity_template())
    assert results and results[0].is_error


def test_worker_outside_school_not_conflict():
    """毕业多年的高中学历社会人员（单位不是学校）不误判。"""
    m = _member(full_time_education="高中", work_unit="某某科技有限公司", occupation="工程师")
    from app.ui.check_page import default_identity_template
    assert not any(r.is_error for r in build_identity_results([m], default_identity_template()))
