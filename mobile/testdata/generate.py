"""生成移动端真机测试数据：20 个支部账号、123 名团员。

数据口径照抄桌面端 demo_data 与 check_rules 的枚举，保证两端判定一致：
- 已上传：ebook_status 含「已上传」
- 没有档案：report_data_status ∈ {电子志愿书待上传, 已报到,电子志愿书未上传}
- 信息错误：real_name_status 不含「通过」，或 income 不含「学生无收入」
- 欠缴：unpaid_total > 0 或 unpaid_months > 0

产出（脚本同目录）：
  accounts.csv          20 个账号，可直接导入桌面端
  members.csv           123 人，中文表头，可当名单核对
  members.json          123 人，桌面端字段名，程序消费
  roster/<账号>.json    移动端本地名单缓存格式，真机直接推入
  desktop-config.json   桌面端整份配置（含账号与成员缓存），可直接灌进演示环境
  accounts.xml          移动端 SharedPreferences 账号表，真机注入用
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

SEED = 20261120
TOTAL_MEMBERS = 123
HERE = Path(__file__).resolve().parent

# 20 个支部：普通高中三个年级 + 国际高中 + 一个超长名支部压排版
BRANCHES = [
    ("12401", "高一1班"), ("12402", "高一2班"), ("12403", "高一3班"),
    ("12404", "高一4班"), ("12405", "高一5班"), ("12406", "高一6班"),
    ("12501", "高二1班"), ("12502", "高二2班"), ("12503", "高二3班"),
    ("12504", "高二4班"), ("12505", "高二5班"),
    ("12601", "高三1班"), ("12602", "高三2班"), ("12603", "高三3班"),
    ("12604", "高三4班"),
    ("22401", "国际高一1班"), ("22402", "国际高一2班"),
    ("22501", "国际高二1班"),
    ("12605", "高三5班"),
    ("12606", "高三实验班（创新人才培养改革试点）"),
]

# 人数不均：真实班级人数本来就有差，也顺便压一压列表滚动
WEIGHTS = [8, 7, 6, 6, 5, 7, 6, 5, 6, 6, 5, 6, 5, 5, 6, 6, 5, 6, 5, 8]

SURNAMES = list("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜")
GIVEN = ["子涵", "雨桐", "浩然", "诗琪", "嘉怡", "俊杰", "梓萱", "思远", "欣悦",
         "博文", "雅静", "天成", "若曦", "宇轩", "静怡", "泽宇", "梦琪", "子墨",
         "慧敏", "建国", "晓峰", "海燕", "文涛", "丽娜", "志强", "秀兰", "永强",
         "春梅", "立新", "玉华"]

LONG_NAMES = ["欧阳上官司马诸葛夏侯", "阿依古丽·买买提·热合曼", "李童（复姓测试）小龙"]


def sizes() -> list[int]:
    """按权重分配并把差额补到人数最多的支部，保证合计正好 123。"""
    base = [max(1, round(TOTAL_MEMBERS * w / sum(WEIGHTS))) for w in WEIGHTS]
    diff = TOTAL_MEMBERS - sum(base)
    order = sorted(range(len(base)), key=lambda i: -base[i])
    i = 0
    while diff != 0:
        idx = order[i % len(order)]
        step = 1 if diff > 0 else -1
        if base[idx] + step >= 1:
            base[idx] += step
            diff -= step
        i += 1
    return base


def mobile(rng: random.Random) -> str:
    return f"1{rng.choice('3578')}{rng.randint(3, 9)}{rng.randint(10000000, 99999999)}"


def member(rng: random.Random, code: str, name_branch: str, idx: int, kind: str,
           variant: int = 0) -> dict:
    # 长名只在首个支部出现，避免跨班重名干扰「按姓名匹配团员」的验收
    name = (LONG_NAMES[idx] if idx < len(LONG_NAMES) and code == BRANCHES[0][0]
            else rng.choice(SURNAMES) + rng.choice(GIVEN))
    join_year = 2023 if code.startswith("12") else (2024 if code.startswith("125") else 2025)
    m = {
        "member_id": f"{code}{idx + 1:02d}",
        "name": name,
        "mobile": mobile(rng),
        "id_type": "居民身份证",
        "real_name_status": "通过",
        "political_status": "共青团员",
        "full_time_education": rng.choice(["高中", "高中", "高中", "中专"]),
        "occupation": "学生",
        "income": "学生无收入",
        "work_unit": name_branch,
        "join_date": f"{join_year}-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}",
        "report_apply_at": "2025-09-12 10:00:00",
        "report_audit_at": "2025-09-20 15:30:00",
        "profile_modify_at": "",
        "profile_modify_audit_at": "",
        "report_data_status": "审核通过",
        "ebook_status": "已上传",
        "ecard_photo_status": "审核通过",
        "award_status": "",
        "unpaid_total": 0.0,
        "unpaid_months": 0,
        "oid": f"o{code}",
        "org_name": name_branch,
    }

    if kind == "nofile":            # 三样都没做
        m.update({"ebook_status": "未上传", "ecard_photo_status": "未上传",
                  "report_data_status": "报到待审核"})
    elif kind == "archive":         # 已报到，缺电子志愿书
        m["report_data_status"] = "电子志愿书待上传"
        m["ebook_status"] = "未上传"
    elif kind == "identity":
        # 三类信息缺项轮换，保证每种都有样本：缺手机号 / 实名未过 / 收入未填
        if variant % 3 == 0:
            m["mobile"] = ""
        elif variant % 3 == 1:
            m["real_name_status"] = "校验失败"
        else:
            m["income"] = ""
    elif kind == "report":
        m["report_data_status"] = "已报到,电子志愿书未上传" if variant % 2 else "报到待审核"
    elif kind == "fees":
        months = [1, 2, 3, 6, 12, 18][variant % 6]
        m["unpaid_months"] = months
        m["unpaid_total"] = round(months * rng.choice([0.5, 1.0, 2.0, 3.0]), 2)
    return m


# 异常配额：总数 38，五类都留足样本，另保证每支部至少 1 人异常
QUOTA = {"nofile": 9, "archive": 8, "report": 7, "identity": 8, "fees": 6}


def plan_kinds(branch_sizes: list[int]) -> list[list[str]]:
    """先给每个支部排 1 个异常，再把剩余配额按支部人数轮转填满。"""
    plan = [["ok"] * n for n in branch_sizes]
    pending: list[str] = []
    for kind, count in QUOTA.items():
        pending.extend([kind] * count)

    # 第一轮：每支部一个，类型按支部顺序轮转，让异常类型分散到不同班
    kinds_order = list(QUOTA)
    for bi, slots in enumerate(plan):
        if slots:
            plan[bi][0] = kinds_order[bi % len(kinds_order)]
            pending.remove(plan[bi][0])

    # 第二轮：剩余配额分给人数多的支部，每支部最多占一半
    bi = 0
    while pending:
        idx = bi % len(plan)
        slots = plan[idx]
        used = sum(1 for s in slots if s != "ok")
        if used < max(1, len(slots) // 2):
            plan[idx][used_placeholder(slots)] = pending.pop(0)
        bi += 1
    return plan


def used_placeholder(slots: list[str]) -> int:
    """返回第一个还空着的位置。"""
    return slots.index("ok")



def build():
    rng = random.Random(SEED)
    now = datetime.now()
    branch_sizes = sizes()
    assert sum(branch_sizes) == TOTAL_MEMBERS, branch_sizes

    accounts, member_cache, all_members = [], {}, []
    plan = plan_kinds(branch_sizes)
    variant_counter: dict[str, int] = {}
    used_names: set[str] = set()
    for bi, ((code, short), size) in enumerate(zip(BRANCHES, branch_sizes)):
        user = f"ylc{code}"
        full_name = short
        accounts.append({
            "userName": user,
            "password": "测试账号-勿用于真实登录",
            "remark": full_name,
            "graduateYear": 2026 if code.startswith("12") else 0,
            "health": {"status": "ok", "message": "测试数据",
                       "checkedAt": now.isoformat(timespec="seconds")},
            "orgName": full_name,
        })

        rows = []
        for i in range(size):
            kind = plan[bi][i]
            v = variant_counter.get(kind, 0)
            variant_counter[kind] = v + 1
            row = member(rng, code, full_name, i, kind, variant=v)
            # 全名单内不重名：重名会让「按姓名匹配团员」无法判断命中的是谁
            if row["name"] in used_names:
                for _ in range(40):
                    cand = rng.choice(SURNAMES) + rng.choice(GIVEN)
                    if cand not in used_names:
                        row["name"] = cand
                        break
            used_names.add(row["name"])
            rows.append(row)

        # 手机号重复：取两个正常团员同号，别占用异常样本
        if bi == 0:
            ok_rows = [r for r in rows if r["real_name_status"] == "通过" and r["mobile"]]
            if len(ok_rows) >= 2:
                ok_rows[0]["mobile"] = ok_rows[1]["mobile"] = "13800138000"
        member_cache[user] = {
            "branch": full_name,
            "fetchedAt": (now - timedelta(minutes=13 * bi)).isoformat(timespec="seconds"),
            "members": rows,
        }
        all_members.extend(rows)

    assert len(all_members) == TOTAL_MEMBERS

    # 站内信：每账号 3 条，未读/已读混合
    msgs = {
        "report": ("档案审核结果", "你的团员档案已审核，请查看结果。"),
        "transfer": ("组织关系转接", "收到组织关系转接申请，请在系统消息中处理。"),
        "other": ("系统公告", "系统将于本周日凌晨维护，期间可能短暂无法访问。"),
    }
    messages = {}
    for bi, acc in enumerate(accounts):
        items = []
        for k, (kind, (title, body)) in enumerate(msgs.items()):
            when = now - timedelta(days=k + 1, hours=bi)
            items.append({
                "receive_id": f"{acc['userName']}-{k:03d}",
                "title": title,
                "content": body,
                "kind": kind,
                "status": 0 if k == 0 else 1,
                "status_str": when.strftime("%Y-%m-%d %H:%M"),
                "time_str": when.strftime("%Y-%m-%d %H:%M"),
                "sendTime": when.strftime("%Y-%m-%d %H:%M"),
                "account": acc["userName"],
            })
        messages[acc["userName"]] = {"fetchedAt": now.isoformat(timespec="seconds"),
                                     "messages": items}

    return accounts, member_cache, all_members, messages


def to_mobile_roster(rows: list[dict]) -> str:
    """移动端 Repo.saveCache 的格式：camelCase 扁平数组。"""
    out = [{
        "mid": r["member_id"],
        "name": r["name"],
        "mobile": r["mobile"],
        "oid": r["oid"],
        "orgName": r["org_name"],
        "ebookStatus": r["ebook_status"],
        "reportStatus": r["report_data_status"],
        "realNameStatus": r["real_name_status"],
        "unpaidTotal": r["unpaid_total"],
        "unpaidMonths": r["unpaid_months"],
    } for r in rows]
    return json.dumps(out, ensure_ascii=False)


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    import csv
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def accounts_xml(accounts: list[dict]) -> str:
    """移动端 SharedPreferences 的 accounts.xml。字符串里的引号要转义成 &quot;。"""
    payload = json.dumps(
        [{"userName": a["userName"], "password": a["password"], "remark": a["remark"]}
         for a in accounts],
        ensure_ascii=False, separators=(",", ":"),
    )
    escaped = (payload.replace("&", "&amp;").replace("<", "&lt;")
               .replace(">", "&gt;").replace('"', "&quot;"))
    return (
        '<?xml version=\'1.0\' encoding=\'utf-8\' standalone=\'yes\' ?>\n'
        '<map>\n'
        f'    <string name="accounts_json">{escaped}</string>\n'
        f'    <string name="active_user">{accounts[0]["userName"]}</string>\n'
        '</map>\n'
    )


def main() -> None:
    accounts, member_cache, all_members, messages = build()
    (HERE / "roster").mkdir(exist_ok=True)

    write_csv(
        HERE / "accounts.csv",
        ["账号", "密码", "备注", "届别", "健康状态"],
        [[a["userName"], a["password"], a["remark"], a["graduateYear"], "ok"] for a in accounts],
    )

    header_cn = ["团支部", "团员编号", "姓名", "手机号码", "实名认证", "报到资料状态",
                 "电子志愿书", "电子团员证照片", "政治面貌", "全日制学历", "职业",
                 "收入", "工作/学习单位", "入团时间", "报到申请时间", "报到审核时间",
                 "未交团费（元）", "累计未交月份", "奖励状态", "所属团支部"]
    write_csv(
        HERE / "members.csv",
        header_cn,
        [[r["org_name"], r["member_id"], r["name"], r["mobile"], r["real_name_status"],
          r["report_data_status"], r["ebook_status"], r["ecard_photo_status"],
          r["political_status"], r["full_time_education"], r["occupation"], r["income"],
          r["work_unit"], r["join_date"], r["report_apply_at"], r["report_audit_at"],
          r["unpaid_total"], r["unpaid_months"], r["award_status"], r["org_name"]]
         for r in all_members],
    )

    (HERE / "members.json").write_text(
        json.dumps(all_members, ensure_ascii=False, indent=1), encoding="utf-8")

    for user, cache in member_cache.items():
        (HERE / "roster" / f"{user}.json").write_text(
            to_mobile_roster(cache["members"]), encoding="utf-8")

    (HERE / "desktop-config.json").write_text(json.dumps({
        "accounts": accounts,
        "memberCache": member_cache,
        "messagesCache": messages,
        "orgStatsCache": {},
        "rosters": {},
        "workspaceDir": "",
        "themeMode": "dark",
        "translucentMaterial": "acrylic",
        "notifyMode": "interval",
        "notifyInterval": 4,
        "headerImage": "",
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    (HERE / "accounts.xml").write_text(accounts_xml(accounts), encoding="utf-8")

    # 压测包：一个账号挂全部 123 人，用来测长列表滚动、搜索与筛选的全量表现
    stress = HERE / "stress"
    (stress / "roster").mkdir(parents=True, exist_ok=True)
    (stress / "roster" / "ylc00000.json").write_text(
        to_mobile_roster(all_members), encoding="utf-8")
    (stress / "accounts.xml").write_text(accounts_xml([{
        "userName": "ylc00000", "password": "测试账号-勿用于真实登录", "remark": "压测 123 人",
    }]), encoding="utf-8")

    # 自检：口径与桌面端 org_stats 一致
    uploaded = sum(1 for m in all_members if "已上传" in m["ebook_status"])
    identity = sum(1 for m in all_members
                  if "通过" not in m["real_name_status"] or "不通过" in m["real_name_status"])
    archive = sum(1 for m in all_members
                  if m["report_data_status"] in {"电子志愿书待上传", "已报到,电子志愿书未上传"})
    report = sum(1 for m in all_members
                 if "审核通过" not in m["report_data_status"]
                 and m["report_data_status"] not in {"电子志愿书待上传", "已报到,电子志愿书未上传"})
    fees = sum(1 for m in all_members if m["unpaid_total"] > 0 or m["unpaid_months"] > 0)
    no_mobile = sum(1 for m in all_members if not m["mobile"])
    print(f"账号 {len(accounts)} 个，团员 {len(all_members)} 人")
    print(f"已上传 {uploaded} / 未上传 {len(all_members) - uploaded}")
    print(f"信息错误 {identity}，没有报到 {report}，没有档案 {archive}，欠缴 {fees}")
    print(f"缺手机号 {no_mobile}，手机号重复组 1（2 人同号）")


if __name__ == "__main__":
    main()
