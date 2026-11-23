"""无界面的业务操作：命令行与脚本用的「后台 worker 平行实现」。

界面里的 ScanWorker / UploadWorker / OrgWorker / HealthWorker / MessageSync /
MemberSyncWorker 都是 QThread + 信号的外壳，真正做事的每个函数都是纯
requests / 文件操作。这里剥掉信号壳、改用回调收集事件，行为与界面逐字对应：
登录闸门、成员缓存复用、跳过的判断、落盘结构都一样——两边不一致的话，
CLI 跑完界面再打开会看到两套互相覆盖的数据，那比不能用更糟。

纪律（tests/test_cli.py 有 subprocess 用例守着）：本模块及它的 import 链
不得引入任何界面组件库，否则欢迎横幅会污染 `cli.py --json` 的输出。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime

from app.domain.check_rules import (
    build_fee_results,
    build_identity_results,
    build_report_results,
)
from app.domain.check_templates import default_identity_template, default_report_template
from app.domain.naming import analyze_file_name
from app.domain.org_manage import record_from_dict
from app.domain.org_stats import (
    build_error_rows,
    duplicate_mobiles,
    ecard_missing,
    oldest_unpaid,
    summarize_members,
    unpaid_amount,
    unpaid_months_total,
)
from app.domain.transfer import fetch_transfer_applies, is_graduated
from app.domain.upload_routing import upload_skip_reason
from app.services import login_gate, store
from app.services.api import SiteClient
# 上面这些 domain/service 模块实测都不拉 Qt（tests/test_cli.py 有 subprocess
# 用例守 stdout 纯净）；会经界面组件库的（workspace、archive_scan、transfer 的
# 界面侧等）只允许在函数里局部 import。

UPLOAD_WORKERS = 3      # 支部之间并发（与 UploadWorker.run 一致），支部内部串行
STATS_WORKERS = 4       # 团员列表是重接口，并发数与 OrgWorker.MAX_WORKERS 一致
HEALTH_WORKERS = 8      # 测活是轻请求，与 HealthWorker 一致
MEMBER_SYNC_WORKERS = 4  # 与 MemberSyncWorker.MAX_WORKERS 一致


def _iso_utc() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _transfer_dict(t) -> dict:
    return {"applied": t.applied, "total": t.total, "succeeded": t.succeeded,
            "pending": t.pending, "returned": t.returned, "names": list(t.names)}


# ---------- 登录与测活 ----------

def probe_account(acc: dict) -> dict:
    """单账号测活：恢复会话 → 只读探测，失效才登录（带冷却闸门）。

    返回 {"user", "status", "message"}，status 的取值与界面测活同一张映射表，
    health 落盘由调用方决定（CLI 默认只探测不写回）。"""
    user, pwd = acc.get("userName", ""), acc.get("password", "")
    if not user or not pwd:
        return {"user": user, "status": "unknown", "message": "账号或密码为空"}
    kind_map = {"ok": "ok", "failed": "failed", "captcha": "captcha",
                "passwordChange": "passwordChange", "needMemberName": "needMemberName",
                "risk": "cooldown", "cooldown": "cooldown", "network": "network"}
    try:
        kind, message = login_gate.ensure_logged_in(SiteClient(user, pwd))
        return {"user": user, "status": kind_map.get(kind, "failed"),
                "message": message or ""}
    except Exception as exc:  # noqa: BLE001 - 单账号异常不影响其余
        return {"user": user, "status": "network", "message": f"测活异常：{exc}"}


def probe_accounts(accounts: list[dict], event=None) -> list[dict]:
    workers = min(HEALTH_WORKERS, max(1, len(accounts)))
    rows: list[dict] = []
    if not accounts:
        return rows
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(probe_account, dict(a)) for a in accounts]
        for fut in as_completed(futures):
            row = fut.result()
            rows.append(row)
            if event:
                event("health", row)
    rows.sort(key=lambda r: r["user"])
    return rows


def persist_health(rows: list[dict]) -> int:
    """把测活结果写回 accounts[].health，结构与界面 _flush_health_results 一致。"""
    stamp = datetime.now().isoformat(timespec="seconds")

    def _write(cfg: dict):
        by_user = {r["user"]: r for r in rows}
        for acc in cfg.get("accounts") or []:
            row = by_user.get(acc.get("userName", ""))
            if row:
                acc["health"] = {"status": row["status"], "message": row["message"],
                                 "checkedAt": stamp}

    store.update(_write)
    return len(rows)


# ---------- 成员数据同步（memberCache） ----------

def sync_members(accounts: list[dict], event=None, allow_login: bool = True) -> list[dict]:
    """逐账号拉团员写 memberCache；落盘结构与界面 MemberSyncWorker 完全一致。"""
    rows: list[dict] = []

    def one(acc: dict) -> dict:
        user, pwd = acc.get("userName", ""), acc.get("password", "")
        branch = acc.get("remark") or user
        if not user or not pwd:
            return {"user": user, "branch": branch, "count": 0,
                    "status": "empty", "message": "账号或密码为空"}
        try:
            client = SiteClient(user, pwd)
            kind, msg = login_gate.ensure_logged_in(client, allow_login=allow_login)
            if kind != "ok":
                return {"user": user, "branch": branch, "count": 0,
                        "status": kind, "message": msg}
            members, _ = client.get_all_members()

            def _write(cfg: dict, user=user, branch=branch, members=members):
                cache = cfg.get("memberCache", {})
                cache[user] = {"branch": branch,
                               "fetchedAt": datetime.now().isoformat(),
                               "members": [asdict(m) for m in members]}
                cfg["memberCache"] = cache

            store.update(_write)
            return {"user": user, "branch": branch, "count": len(members),
                    "status": "ok", "message": ""}
        except Exception as exc:  # noqa: BLE001
            return {"user": user, "branch": branch, "count": 0,
                    "status": "network", "message": str(exc)}

    workers = min(MEMBER_SYNC_WORKERS, max(1, len(accounts)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, dict(a)) for a in accounts]
        for fut in as_completed(futures):
            row = fut.result()
            rows.append(row)
            if event:
                event("member_sync", row)
    rows.sort(key=lambda r: r["user"])
    return rows


# ---------- 站内信 ----------

def pull_messages(accounts: list[dict], event=None, allow_login: bool = True) -> list[dict]:
    """拉取站内信并按 receive_id 合并进 messagesCache，结构与界面一致。"""
    from app.domain.messages import fetch_all_messages

    rows: list[dict] = []

    def one(acc: dict) -> dict:
        user, pwd = acc.get("userName", ""), acc.get("password", "")
        if not user or not pwd:
            return {"user": user, "count": 0, "new": 0,
                    "status": "empty", "message": "账号或密码为空"}
        try:
            client = SiteClient(user, pwd)
            kind, msg = login_gate.ensure_logged_in(client, allow_login=allow_login)
            if kind != "ok":
                return {"user": user, "count": 0, "new": 0, "status": kind, "message": msg}
            views = fetch_all_messages(client, user)
            box = [0]

            def _write(cfg: dict, user=user, views=views, box=box):
                cache = cfg.get("messagesCache", {})
                old = {m.get("receive_id") or m.get("receiveId")
                       for m in cache.get(user, {}).get("messages", [])}
                had_cache = bool(cache.get(user, {}).get("messages"))
                merged = {m.get("receive_id"): m
                          for m in cache.get(user, {}).get("messages", [])
                          if m.get("receive_id")}
                for v in views:
                    merged[v.receive_id] = v.__dict__ | {"kind": v.kind}
                    if had_cache and v.receive_id not in old:
                        box[0] += 1
                cache[user] = {"fetchedAt": _iso_utc(),
                               "messages": list(merged.values())}
                cfg["messagesCache"] = cache

            store.update(_write)
            return {"user": user, "count": len(views), "new": box[0],
                    "status": "ok", "message": ""}
        except Exception as exc:  # noqa: BLE001
            return {"user": user, "count": 0, "new": 0,
                    "status": "network", "message": str(exc)}

    workers = min(MEMBER_SYNC_WORKERS, max(1, len(accounts)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, dict(a)) for a in accounts]
        for fut in as_completed(futures):
            row = fut.result()
            rows.append(row)
            if event:
                event("messages", row)
    rows.sort(key=lambda r: r["user"])
    return rows


def mark_messages_read(keys: list[str]) -> int:
    """本地标记已读（messagesRead），键格式 account:receive_id，与界面一致。"""

    def _write(cfg: dict):
        marks = cfg.setdefault("messagesRead", {})
        for key in keys:
            marks[key] = 1

    store.update(_write)
    return len(keys)


# ---------- 组织统计（orgStatsCache） ----------

def stats_accounts(accounts: list[dict], event=None) -> list[dict]:
    """在线统计各支部：会话/登录 → 团员 → 三项检查 + 转接。

    每行结构与 OrgWorker.account_done 信号逐字对应；落盘由调用方做
    （persist_org_stats），字段布局与 OrgPagePersistMixin._persist_account 一致。"""
    rows: list[dict] = []

    def one(acc: dict) -> dict:
        user, pwd = acc.get("userName", ""), acc.get("password", "")
        branch = acc.get("remark") or user
        if not user or not pwd:
            return {"user": user, "branch": branch, "total": 0, "uploaded": 0,
                    "abnormal": 0, "by_kind": {}, "unpaid": 0.0, "extras": {},
                    "transfer": None, "error_rows": [],
                    "status": "empty", "message": "账号或密码为空", "graduated": False}
        try:
            client = SiteClient(user, pwd)
            kind, msg = login_gate.ensure_logged_in(client)
            if kind != "ok":
                return {"user": user, "branch": branch, "total": 0, "uploaded": 0,
                        "abnormal": 0, "by_kind": {}, "unpaid": 0.0, "extras": {},
                        "transfer": None, "error_rows": [],
                        "status": kind, "message": msg, "graduated": False}
            members, _ = client.get_all_members()
            id_tpl, rep_tpl = default_identity_template(), default_report_template()
            results = (
                build_identity_results(members, id_tpl)
                + build_report_results(members, rep_tpl)
                + build_fee_results(members)
            )
            s = summarize_members(members, results)
            error_rows = [vars(r) for r in build_error_rows(results, branch)]
            oid = members[0].oid if members else ""
            transfer = _transfer_dict(fetch_transfer_applies(client, oid))
            transfer["total"] = s.total
            return {"user": user, "branch": branch,
                    "total": s.total, "uploaded": s.uploaded, "abnormal": s.abnormal,
                    "by_kind": dict(s.by_kind),
                    "unpaid": round(float(unpaid_amount(results)), 2),
                    "extras": {"dupMobile": duplicate_mobiles(members),
                               "ecardMissing": ecard_missing(members),
                               "unpaidMonths": unpaid_months_total(members),
                               "oldestUnpaid": oldest_unpaid(members)},
                    "transfer": transfer, "error_rows": error_rows,
                    "status": "ok", "message": "",
                    "graduated": is_graduated(acc)}
        except Exception as exc:  # noqa: BLE001
            return {"user": user, "branch": branch, "total": 0, "uploaded": 0,
                    "abnormal": 0, "by_kind": {}, "unpaid": 0.0, "extras": {},
                    "transfer": None, "error_rows": [],
                    "status": "network", "message": str(exc), "graduated": False}

    workers = min(STATS_WORKERS, max(1, len(accounts)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, dict(a)) for a in accounts]
        for fut in as_completed(futures):
            row = fut.result()
            rows.append(row)
            if event:
                event("stats", row)
    rows.sort(key=lambda r: r["user"])
    return rows


def persist_org_stats(rows: list[dict], accounts: list[dict]) -> int:
    """统计成功的行写入 orgStatsCache；结构与界面 _persist_account 一致。"""
    valid = {a.get("userName", "") for a in accounts}
    ok_rows = [r for r in rows if r["status"] == "ok"]

    def _write(cfg: dict):
        cache = cfg.setdefault("orgStatsCache", {})
        if valid:
            for key in [k for k in cache if k not in valid]:
                del cache[k]
        for r in ok_rows:
            key = r["user"]
            existing = cache.get(key) or {}
            cache[key] = {
                "branch": r["branch"],
                "total": r["total"],
                "uploaded": r["uploaded"],
                "by_kind": {k: r["by_kind"].get(k, 0)
                            for k in ("identity", "report", "archive", "fees")},
                "transfer": r["transfer"] or existing.get("transfer"),
                "error_rows": r["error_rows"],
                "unpaidAmount": r["unpaid"],
                "extras": dict(r["extras"] or {}),
                "updatedAt": datetime.now().isoformat(timespec="seconds"),
            }

    store.update(_write)
    return len(ok_rows)


# ---------- 上传 ----------

def _login_for_upload(account: str, password: str) -> tuple[object, str]:
    client = SiteClient(account, password)
    kind, msg = login_gate.ensure_logged_in(client)
    if kind == "ok":
        return client, ""
    if kind == "cooldown":
        return None, msg
    if kind == "captcha":
        return None, "需要验证码（密码失败过多），在界面系统配置双击该账号填写"
    return None, msg or "登录失败"


def _member_map(client, account: str) -> dict:
    """(姓名, 手机号) → 团员记录；4 小时内的 memberCache 直接复用（同界面）。"""
    from datetime import timezone

    blob = (store.load().get("memberCache") or {}).get(account) or {}
    ts = blob.get("fetchedAt", "")
    if ts:
        try:
            stamp = datetime.fromisoformat(ts)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            hours = (datetime.now(timezone.utc) - stamp).total_seconds() / 3600
            if 0 <= hours < 4:
                cached = {(d["name"], d["mobile"]): record_from_dict(d)
                          for d in blob.get("members", [])}
                if cached:
                    return cached
        except (ValueError, KeyError, TypeError):
            pass
    try:
        members, _ = client.get_all_members()
    except Exception:  # noqa: BLE001 - 名单失败只影响「跳过」判断，不拦上传
        return {}
    return {(m.name, m.mobile): m for m in members}


def _upload_one_account(account: str, password: str, jobs: list[dict],
                       batch_limit: int, event=None) -> dict:
    """提交一个支部的全部档案（与 UploadWorker._upload_account 逐行等价）。"""
    counts = {"account": account, "ok": 0, "skipped": 0, "failed": 0,
              "audit_ok": 0, "audit_manual": 0}

    def row(path: str, result: str, reason: str):
        if event:
            event("upload", {"account": account, "path": path,
                             "result": result, "reason": reason})

    if len(jobs) > batch_limit:
        for job in jobs:
            row(job.get("display", job["path"]), "失败",
                f"该支部单批超过 {batch_limit} 份，请分批")
            counts["failed"] += 1
        return counts
    client, err = _login_for_upload(account, password)
    if client is None:
        for job in jobs:
            row(job.get("display", job["path"]), "失败", f"登录失败：{err}")
            counts["failed"] += 1
        return counts
    member_map = _member_map(client, account)

    for job in jobs:
        path = job.get("display", job["path"])
        upload_path = job["path"]
        analysis = analyze_file_name(os.path.basename(path))
        member = member_map.get(analysis.parsed) if analysis.parsed else None
        if member is not None:
            reason = upload_skip_reason(member.report_data_status, member.ebook_status)
            if reason:
                row(path, "跳过", reason)
                counts["skipped"] += 1
                continue

        result = client.upload_file(upload_path)
        if not result.ok:
            row(path, "失败", result.message)
            counts["failed"] += 1
            continue
        counts["ok"] += 1
        if member is not None and str(member.member_id).isdigit():
            audit = client.audit_voluntary_book(member.member_id)
            if audit.ok:
                counts["audit_ok"] += 1
                row(path, "成功", "已上传，审核已自动通过")
            else:
                counts["audit_manual"] += 1
                row(path, "成功",
                    f"已上传，自动审核失败：{audit.message}（请到网页端手动审核）")
        else:
            counts["audit_manual"] += 1
            row(path, "成功", "已上传，未匹配到团员记录，请到网页端手动审核")
    return counts


def upload_accounts(creds: dict[str, str], jobs: list[dict],
                   batch_limit: int, event=None) -> dict:
    """按支部并发提交（支部内串行），返回 {rows, summary, accounts}。

    jobs = [{path: 压缩副本, display: 原件, account}]，与界面 UploadWorker 同构。"""
    groups: dict[str, list[dict]] = {}
    for job in jobs:
        groups.setdefault(job["account"], []).append(job)

    rows: list[dict] = []

    def collect(kind: str, payload: dict):
        if kind == "upload":
            rows.append(payload)

    summary = {"ok": 0, "skipped": 0, "failed": 0, "audit_ok": 0, "audit_manual": 0}
    account_counts: list[dict] = []
    if groups:
        workers = min(UPLOAD_WORKERS, len(groups))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_upload_one_account, account,
                                   creds.get(account, ""), jobs, batch_limit, collect):
                       account for account, jobs in groups.items()}
            for fut in as_completed(futures):
                account = futures[fut]
                try:
                    counts = fut.result()
                except Exception as exc:  # noqa: BLE001 - 整支失败要报出来，不静默
                    for job in groups[account]:
                        rows.append({"account": account,
                                     "path": job.get("display", job["path"]),
                                     "result": "失败", "reason": f"提交失败：{exc}"})
                        summary["failed"] += 1
                    continue
                account_counts.append(counts)
                for key in summary:
                    summary[key] += counts[key]
    return {"rows": sorted(rows, key=lambda r: (r["account"], r["path"])),
            "summary": summary,
            "accounts": sorted(account_counts, key=lambda c: c["account"])}


# ---------- 上传前的准备（扫描 + 检查 + 压缩） ----------

def collect_files(dirs: list[str]) -> tuple[list[str], dict[str, str]]:
    """来源目录 + 待上传散件 → 文件列表与 root 映射（同界面 ScanWorker）。"""
    from app.domain.archive_scan import scan_folders
    from app.services.workspace import load_pending

    scans = scan_folders(dirs)
    files: list[str] = []
    roots: dict[str, str] = {}
    for scan in scans:
        for path in scan.files:
            roots[path] = scan.root
            files.append(path)
    for entry in load_pending():
        path = entry.get("path", "")
        if path and path not in roots:
            import os

            if os.path.isfile(path):
                files.append(path)
    return files, roots
