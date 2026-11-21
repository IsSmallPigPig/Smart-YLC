import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.domain.naming import analyze_file_name, build_naming_report, chunk_list, Roster, RosterRow


def test_good_name():
    r = analyze_file_name("张三+13800000000+入团志愿书.pdf")
    assert r.severity == "ok" and r.codes == ["OK"]
    assert r.parsed == ("张三", "13800000000")


def test_alternate_separator_warn():
    """allow_alternate_separator=True 时 - 只是提醒（站点可上传），不判问题。"""
    r = analyze_file_name("张三-13800000000-入团志愿书.pdf", allow_alternate_separator=True)
    assert r.severity == "warn"
    assert "ALTERNATE_SEPARATOR" not in r.codes


def test_alternate_separator_issue_when_strict():
    r = analyze_file_name("张三-13800000000-入团志愿书.pdf", allow_alternate_separator=False)
    assert r.severity == "issue" and "ALTERNATE_SEPARATOR" in r.codes


def test_fullwidth_separator():
    r = analyze_file_name("张三＋13800000000＋入团志愿书.pdf")
    assert "FULLWIDTH_SEPARATOR" in r.codes and r.severity == "issue"


def test_mixed_separator():
    r = analyze_file_name("张三+13800000000-入团志愿书.pdf")
    assert "MIXED_SEPARATOR" in r.codes


def test_structure_mismatch():
    r = analyze_file_name("张三13800000000入团志愿书.pdf")
    assert "STRUCTURE_MISMATCH" in r.codes and r.parsed is None


def test_mobile_invalid():
    r = analyze_file_name("张三+13800+入团志愿书.pdf")
    assert "MOBILE_INVALID" in r.codes


def test_suffix_mismatch():
    r = analyze_file_name("张三+13800000000+志愿书.pdf")
    assert "SUFFIX_MISMATCH" in r.codes


def test_space():
    r = analyze_file_name("张三 +13800000000+入团志愿书.pdf")
    assert "HAS_SPACE" in r.codes


def test_not_pdf():
    r = analyze_file_name("张三+13800000000+入团志愿书.docx")
    assert "EXT_NOT_PDF" in r.codes


def test_chunk_list():
    assert chunk_list(list(range(53)), 50) == [list(range(50)), [50, 51, 52]]


def test_duplicate_member_detection():
    # 两个不同的原始文件名，去掉空格后解析到同一名团员
    issues = [
        analyze_file_name("张三+13800000000+入团志愿书.pdf"),
        analyze_file_name("张三 +13800000000+入团志愿书.pdf"),
    ]
    report = build_naming_report("/tmp", issues)
    assert report.summary.duplicates == 1
    assert all(i.severity == "issue" for i in issues)


def test_roster_cross_check():
    issues = [analyze_file_name("张三+13800000000+入团志愿书.pdf")]
    roster = Roster("r1", [RosterRow("李四", "13900000000")])
    report = build_naming_report("/tmp", issues, roster)
    assert issues[0].codes[-1] == "FILE_NOT_ON_ROSTER"
    assert report.summary.extra_files == [issues[0].original_name]
    assert report.summary.missing_files == ["李四+13900000000+入团志愿书.pdf"]


def test_roster_mobile_mismatch():
    issues = [analyze_file_name("张三+13800000000+入团志愿书.pdf")]
    roster = Roster("r1", [RosterRow("张三", "13900000000")])
    build_naming_report("/tmp", issues, roster)
    assert "ROSTER_NAME_MOBILE_MISMATCH" in issues[0].codes


def test_hidden_characters_and_fullwidth_digits_are_cleaned():
    """微信/QQ 存下的文件名带零宽字符、BOM、全角数字，不该被判「无法解析」。"""
    from app.domain.naming import analyze_file_name

    for name in (
        "\ufeff张三+13800138000+入团志愿书.pdf",
        "张三+13800138000+入团志愿书\u200b.pdf",
        "张三+13800138000+入团志愿书\u202e.pdf",
    ):
        parsed = analyze_file_name(name).parsed
        assert parsed == ("张三", "13800138000"), name

    parsed = analyze_file_name("张三＋１３８００１３８０００＋入团志愿书.pdf").parsed
    assert parsed == ("张三", "13800138000")


def test_illegal_char_in_name():
    r = analyze_file_name("张/三+13800000000+入团志愿书.pdf")
    assert "ILLEGAL_CHAR" in r.codes


def test_name_with_digit_is_invalid():
    r = analyze_file_name("张三1+13800000000+入团志愿书.pdf")
    assert "NAME_INVALID" in r.codes
