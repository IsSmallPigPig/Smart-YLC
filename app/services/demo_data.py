"""演示数据：一键把界面灌成「像一个真实学校」的规模，供截图与手工验收。

一条两条数据看不出问题：列表不滚动、分页不触发、每张卡片数字都是 1、
长支部名不会撞到右侧统计、异常与正常的比例是 1:0 看着像全坏。

这里按固定种子生成 6 个支部、约 260 名团员，**故意**覆盖各类真实分布：
七成合规不动，其余分散到「没上传档案 / 身份信息缺项 / 志愿书未审 / 团费欠缴」
四类，另有手机号重复、超长姓名、超长支部名。

字段取值照站点的真实枚举来（`real_name_status="通过"`、`income="学生无收入"`、
`report_data_status="审核通过"`），否则全员都会被判异常，反而看不出问题。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

SEED = 20261117

# 6 个支部：三个年级各两个班，最后一个用超长名字压一压卡片排版
BRANCHES = [
    ("高中2024级1班", "高一1班"),
    ("高中2024级2班", "高一2班"),
    ("高中2025级1班", "高二1班"),
    ("高中2025级3班", "高二3班"),
    ("高中2026级2班", "高三2班"),
    ("高中2026级实验班（创新人才培养改革试点）", "高三实验班"),
]

SURNAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜")
GIVEN = ["子涵", "雨桐", "浩然", "诗琪", "嘉怡", "俊杰", "梓萱", "思远", "欣悦",
         "博文", "雅静", "天成", "若曦", "宇轩", "静怡", "泽宇", "梦琪", "子墨",
         "慧敏", "建国", "晓峰", "海燕", "文涛", "丽娜", "志强", "秀兰", "永强",
         "春梅", "立新", "玉华"]

# 极少数人用超长姓名：表格省略、导出列宽、通知文案都要能扛
LONG_NAMES = ["欧阳上官司马诸葛夏侯", "阿依古丽·买买提·热合曼", "李童（复姓测试）小龙"]

MESSAGE_TEMPLATES = [
    ("档案审核结果", "report", "你的团员档案已审核，请查看结果。"),
    ("团费缴纳提醒", "fees", "本月团费尚未缴纳，请及时完成缴纳，避免影响团员状态。"),
    ("组织关系转接", "transfer", "收到组织关系转接申请，请在系统消息中处理。"),
    ("志愿书退回修改", "report", "志愿书填写有误已退回，请按意见修改后重新提交。"),
    ("系统公告", "other", "系统将于本周日凌晨维护，期间可能短暂无法访问。"),
]


def _mobile(rng: random.Random) -> str:
    return f"1{rng.choice('3578')}{rng.randint(0, 9)}{rng.randint(10000000, 99999999)}"


def _member(rng: random.Random, branch: str, idx: int, kind: str) -> dict:
    """按 kind 生成不同健康程度的成员：ok / nofile / identity / report / fees。"""
    name = (LONG_NAMES[idx] if idx < len(LONG_NAMES)
            else rng.choice(SURNAMES) + rng.choice(GIVEN))
    m = {
        "member_id": str(100000 + abs(hash((branch, idx))) % 899999),
        "name": name,
        "mobile": _mobile(rng),
        "id_type": "居民身份证",
        # 下面三个是检查模板的判定字段，值必须与站点枚举一致
        "real_name_status": "通过",
        "income": "学生无收入",
        "report_data_status": "审核通过",
        "political_status": "共青团员",
        "full_time_education": rng.choice(["高中", "高中", "高中", "中专"]),
        "occupation": "学生",
        "work_unit": branch,
        "join_date": f"202{rng.randint(3, 5)}-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}",
        "report_apply_at": "2025-09-12 10:00:00",
        "report_audit_at": "2025-09-20 15:30:00",
        "profile_modify_at": "2025-10-01 09:00:00",
        "profile_modify_audit_at": "2025-10-02 11:00:00",
        "ebook_status": "已上传",
        "ecard_photo_status": "已采集",
        "award_status": "",
        "unpaid_total": 0.0,
        "unpaid_months": 0,
        "oid": f"o{idx:04d}{branch[:4]}",
        "org_name": branch,
    }

    if kind == "nofile":
        # 三样都没做：上传页的「未上传」一栏才有内容
        m.update({"ebook_status": "", "ecard_photo_status": "",
                  "report_data_status": ""})
    elif kind == "identity":
        pick = idx % 3
        if pick == 0:
            m["mobile"] = ""                       # 缺手机号
        elif pick == 1:
            m["real_name_status"] = "未通过"        # 实名没过
        else:
            m["income"] = ""                       # 收入没填
        if idx % 3 == 0:
            m["mobile"] = "13800138000"            # 与他人重复
    elif kind == "report":
        if idx % 2 == 0:
            m["report_apply_at"] = ""              # 没提交
            m["report_data_status"] = "已填写"
        else:
            m["report_audit_at"] = ""               # 提交未审
            m["report_data_status"] = "已填写"
    elif kind == "archive":
        # 人已报到，缺的是电子志愿书档案 —— 单独一类，不与「没有报道」混算
        m["report_data_status"] = "电子志愿书待上传"
        m["ebook_status"] = ""
    elif kind == "fees":
        months = rng.choice([1, 2, 3, 6, 12, 18])
        m["unpaid_months"] = months
        m["unpaid_total"] = round(months * rng.choice([0.5, 1.0, 2.0, 3.0]), 2)
    return m


def build_config(member_count: int = 260) -> dict:
    """生成一份完整配置（可直接 store.save）。

    时间字段统一用本地 naive 时间（`datetime.now().isoformat()`），与
    settings_page 写 `checkedAt` 的格式一致；用带时区的值会让
    `login_gate.status_fresh` 拿 aware 减 naive 抛 TypeError。
    """
    rng = random.Random(SEED)
    now = datetime.now()

    accounts, member_cache, messages = [], {}, {}
    total_target = max(len(BRANCHES), member_count)
    # 各支部人数不一样：真实学校里班级人数本来就有差（37~52 人）。
    # 每个支部都取同一个数时，列表上六个「43 团员」长得一模一样，看不出排版问题。
    weights = [0.16, 0.17, 0.18, 0.15, 0.14, 0.20]
    sizes = [max(6, round(total_target * w)) for w in weights]

    for bi, (org_name, remark) in enumerate(BRANCHES):
        user = f"demo{bi + 1:02d}"
        accounts.append({
            "userName": user,
            "password": "演示账号-请勿用于真实登录",
            "remark": remark,
            # 演示账号必须全部是 ok：status 非 ok 会被 split_accounts 踢出统计，
            # 剩下的账号去真实登录又必然失败，组织状况就整片是 0。
            # checkedAt 设成刚刚刷新，自动统计按 4 小时节流直接跳过网络那一轮，
            # 数据从 memberCache 离线算出来——演示不联网也能有完整统计。
            "health": {
                "status": "ok",
                "message": "演示数据",
                "checkedAt": now.isoformat(timespec="seconds"),
            },
            "orgName": org_name,
        })

        members = []
        for i in range(sizes[bi]):
            # 异常比例按支部错开：有的班管理得好（异常少），有的班一堆问题。
            rate = (0.08, 0.14, 0.20, 0.28, 0.11, 0.33)[bi]
            roll = rng.random()
            if roll < 1 - rate:
                kind = "ok"
            else:
                # 异常类型在四类里轮转，保证每类都有样本
                kind = ("nofile", "identity", "report", "fees", "archive")[i % 5]
            members.append(_member(rng, org_name, i, kind=kind))

        member_cache[user] = {
            "branch": org_name,
            "fetchedAt": (now - timedelta(minutes=15 * bi)).isoformat(),
            "members": members,
        }

        items = []
        for k in range(12):
            title, kind, content = MESSAGE_TEMPLATES[k % len(MESSAGE_TEMPLATES)]
            when = now - timedelta(days=k, hours=bi)
            items.append({
                "receive_id": f"{user}-{k:03d}",
                "title": title,
                # 内容长度拉开：卡片要能容纳一两行与四五行的差别
                "content": content * (1 + k % 3),
                "kind": kind,
                "status": 1 if k % 3 else 0,        # 0 未读 / 1 已读
                "status_str": when.strftime("%Y-%m-%d %H:%M"),
                "time_str": when.strftime("%Y-%m-%d %H:%M"),
                "sendTime": when.strftime("%Y-%m-%d %H:%M"),
                "account": user,
            })
        messages[user] = {"fetchedAt": now.isoformat(), "messages": items}

    return {
        "accounts": accounts,
        "memberCache": member_cache,
        "messagesCache": messages,
        # orgStatsCache 故意留空：让组织状况页从 memberCache 如实离线算出。
        # 手写一份缓存要同时对上 by_kind / error_rows 的结构，漏一个字段
        # 就变成「总人数对、各项统计全 0」——不如让它自己算。
        "orgStatsCache": {},
        "rosters": {},
        "workspaceDir": "",
        "themeMode": "dark",
        "translucentMaterial": "acrylic",
        "notifyMode": "interval",
        "notifyInterval": 4,
        "headerImage": "",
    }


def summary(cfg: dict) -> str:
    """一句话说明生成了多少数据，给界面提示用。"""
    branches = len(cfg.get("accounts") or [])
    members = sum(len(b.get("members") or [])
                  for b in (cfg.get("memberCache") or {}).values())
    msgs = sum(len(v.get("messages") or [])
               for v in (cfg.get("messagesCache") or {}).values())
    return f"{branches} 个支部、{members} 名团员、{msgs} 条站内信"
