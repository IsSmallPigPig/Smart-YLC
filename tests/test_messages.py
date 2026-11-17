"""站内信分类、手机号提取、姓名索引。"""

from app.domain.messages import (
    MessageView,
    build_member_name_index,
    classify_message,
    clean_title,
    extract_mobile,
    strip_html,
)


def test_clean_title():
    # 站点标题第一层是系统通知前缀，第二层才是标题
    assert clean_title("[系统通知][报道认证审核提醒]") == "报道认证审核提醒"
    assert clean_title("[系统通知][组织关系转接审核提醒]") == "组织关系转接审核提醒"
    assert clean_title("[系统通知]换届提醒") == "换届提醒"
    assert clean_title("换届提醒") == "换届提醒"


def test_member_name_index():
    cache = {"ssfz12407": {"members": [{"mobile": "19925691060", "name": "谢欣雨"}]}}
    assert build_member_name_index(cache, [])["19925691060"] == "谢欣雨"


def test_classify_report():
    # 站点把「报到」错打成「报道」，两种都要认
    assert classify_message("[系统通知][报道认证审核提醒]", "谢欣雨向您所在团组织提交了团员报到申请，请您尽快审核。") == "report"
    assert classify_message("[系统通知]", "请尽快完成报到认证") == "report"


def test_classify_transfer():
    assert classify_message("[系统通知]", "王瑜向您所在团组织提交了团员关系转接申请，请您尽快审核。") == "transfer"
    # 站点标题可能是「转出支部审核提醒」/「转入支部审核提醒」，不带「转接」二字
    assert classify_message("[系统通知][转出支部审核提醒]", "谢维恩向所在团组织提交了团员转出申请，请尽快审核。") == "transfer"
    assert classify_message("[系统通知][转入支部审核提醒]", "李四向所在团组织提交了团员转入申请，请尽快审核。") == "transfer"
    # 转接类优先于报到（内容可能同时出现两个词）
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
    # 点击消息直接用拉取来源账号（支部即账号），不再经团员定位
    assert v.account == "ssfz12407"
