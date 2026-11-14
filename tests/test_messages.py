"""站内信分类、手机号提取、支部账号定位。"""

from app.domain.messages import (
    MessageView,
    build_member_index,
    classify_message,
    extract_mobile,
    locate_account,
    strip_html,
)


def test_classify_report():
    # 站点把「报到」错打成「报道」，两种都要认
    assert classify_message("[系统通知][报道认证审核提醒]", "谢欣雨向您所在团组织提交了团员报到申请，请您尽快审核。") == "report"
    assert classify_message("[系统通知]", "请尽快完成报到认证") == "report"


def test_classify_transfer():
    assert classify_message("[系统通知]", "王瑜向您所在团组织提交了团员关系转接申请，请您尽快审核。") == "transfer"
    # 转接优先于报到（内容可能同时出现两个词）
    assert classify_message("报到提醒", "该团员已发起转接申请") == "transfer"


def test_classify_other():
    assert classify_message("[系统通知][换届提醒]", "您的支部即将换届，请及时处理。") == "other"


def test_extract_mobile():
    html = "<a data-path='view/x.html' data-mobile='19925691060'>点击查看</a>"
    assert extract_mobile(html) == "19925691060"
    assert extract_mobile("联系 13800000001 审核处理") == "13800000001"
    assert extract_mobile("无手机号") == ""


def test_strip_html():
    assert strip_html("审核<a href='javascript:;'>点击查看</a>。") == "审核点击查看。"


def test_build_views_and_locate():
    rows = [{
        "receiveId": 1, "title": "[系统通知][报道认证审核提醒]",
        "content": "谢欣雨向您所在团组织提交了团员报到申请<a data-mobile='19925691060'>查看</a>",
        "status": 0, "statusStr": "未读", "sendTime": "2026-10-28 17:47:54",
    }]
    views = [v for v in __import__("app.domain.messages", fromlist=["build_message_views"]).build_message_views(rows, "ssfz12407")]
    v = views[0]
    assert v.kind == "report" and v.mobile == "19925691060" and v.status_str == "未读"

    accounts = [{"userName": "ssfz12407", "remark": "普通高中高三七班"}]
    index = build_member_index([], {"ssfz12407": {"members": [{"mobile": "19925691060"}]}}, [])
    assert locate_account(v, accounts, index) == "ssfz12407"

    # 系统缓存没命中时走 本地班级编号 → 编号规则 → 账号备注
    index2 = build_member_index([], {}, [])
    index2["19925691060"] = ("", "12407")
    assert locate_account(v, accounts, index2) == "ssfz12407"
