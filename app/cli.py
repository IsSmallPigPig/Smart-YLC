"""命令行入口：不开界面跑全部业务，给脚本与 AI 用。

稳定契约（改动要同步 README 与 tests/test_cli.py）：

* 每条命令都返回一个 JSON 对象，`--json` 时写到标准输出，别的内容不混进去：
  `{"schema": 1, "ok": true, "command": "scan", "data": {...}, "warnings": []}`
  失败时是 `{"schema": 1, "ok": false, "command": ..., "error": {"code", "message"}}`。
* 退出码：0 成功，2 参数错误，3 前置条件不满足（目录不在、配置读不出、缺账号），
  4 运行期失败。命令跑成功但业务没成（比如登录被冷却）仍是 0，结果看 data。
* `scan`/`cells`/`config get`/`messages list` 等检查与查询命令只读。
  会写盘或改配置的操作（改名、压缩、上传、同步、导入、恢复备份、开机自启……）
  都要显式 `--yes`；不加时返回 `{"dry_run": true, "plan": {...}}` 说明会发生什么。
* 联网命令（login/org/messages/backup/pair pull/update/announce/daily-check/upload）
  本身就要碰网络；会话保存与冷却记录是登录闸门的既有行为，与界面一致。
* 输出里的路径一律绝对路径；列表按路径或账号排序，同样的输入得到同样的输出。
* 界面专属、无法脱离 Qt 的动作不在 CLI 里：浏览器标签页、文件选择框、
  图形验证码的人机输入（要登录失败后先在网页端登一次）、目录实时监听。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

SCHEMA = 1
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_PRECONDITION = 3
EXIT_FAILURE = 4


class CliError(Exception):
    """带退出码的失败：调用方按 code 判断怎么处理，不用解析文案。"""

    def __init__(self, code: str, message: str, exit_code: int = EXIT_FAILURE):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


def _prepare_config_dir(path: str | None) -> None:
    """--config-dir 指向一份独立配置，效果等同把 HOME 换掉（--demo 同一机制）。

    必须在 import app.services.store 之前调用：CONFIG_FILE 在模块导入时求值。
    """
    if not path:
        return
    home = os.path.abspath(path)
    os.environ["HOME"] = home
    os.environ["USERPROFILE"] = home


def _config() -> dict:
    from app.services import store

    try:
        return store.load()
    except (OSError, RuntimeError) as exc:
        raise CliError("config_unreadable", f"配置读不出来：{exc}", EXIT_PRECONDITION)


def _write_config(mutate) -> dict:
    from app.services import store

    try:
        return store.update(mutate)
    except (OSError, RuntimeError) as exc:
        raise CliError("config_unwritable", f"配置写不进去：{exc}")


def _accounts(cfg: dict, only: str = "") -> list[dict]:
    """配置里的可用账号；`only` 按账号名或备注子串筛。"""
    rows = [a for a in (cfg.get("accounts") or []) if a.get("userName")]
    if only:
        rows = [a for a in rows
                if only in a["userName"] or only in (a.get("remark") or "")]
        if not rows:
            raise CliError("unknown_account",
                           f"没有账号名或备注含「{only}」的账号", EXIT_PRECONDITION)
    return rows


def _creds(accounts: list[dict]) -> dict[str, str]:
    return {a["userName"]: a.get("password", "") for a in accounts}


def _plan(args, plan: dict, warnings: list[str] | None = None):
    """没有 --yes 的写操作：报告会发生什么，不动盘。"""
    if args.yes:
        return None
    return {"dry_run": True, "plan": plan}, list(warnings or [])


def _abs_out(path: str, suffix: str = "") -> str:
    """输出文件路径：绝对化并检查父目录可建。"""
    out = os.path.abspath(os.path.expanduser(path))
    parent = os.path.dirname(out)
    if not os.path.isdir(parent):
        raise CliError("missing_dir", f"输出目录不存在：{parent}", EXIT_PRECONDITION)
    if suffix and not out.lower().endswith(suffix):
        out += suffix
    return out


# ---------- 基础 ----------

def cmd_version(args) -> tuple[dict, list[str]]:
    from app import __version__
    from app.constants import CONFIG_FILE
    from app.fonts import CJK_FAMILY, FONT_FAMILY, FONT_FILES

    fonts = Path(__file__).resolve().parent / "assets" / "fonts"
    return {
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "config_file": str(CONFIG_FILE),
        "font": {
            "latin": FONT_FAMILY,
            "cjk": CJK_FAMILY,
            "bundled": all((fonts / name).is_file() for name in FONT_FILES),
        },
    }, []


def cmd_doctor(args) -> tuple[dict, list[str]]:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, level: str = "error") -> None:
        checks.append({"id": name, "ok": ok, "level": level, "detail": detail})

    try:
        cfg = _config()
        add("config", True, "配置可读")
    except CliError as exc:
        add("config", False, str(exc))
        return {"checks": checks, "blocked": True}, []

    accounts = [a for a in (cfg.get("accounts") or []) if a.get("userName")]
    add("accounts", bool(accounts), f"{len(accounts)} 个账号")

    dirs = [d for d in (cfg.get("workspaceDirs") or []) if d]
    missing = [d for d in dirs if not os.path.isdir(d)]
    add("workspace", not missing,
        "来源目录都在" if not missing else f"找不到：{'、'.join(missing)}",
        "warning")

    from app.fonts import CJK_FAMILY, FONT_FAMILY, FONT_FILES

    fonts = Path(__file__).resolve().parent / "assets" / "fonts"
    absent = [name for name in FONT_FILES if not (fonts / name).is_file()]
    add("fonts", not absent,
        f"{FONT_FAMILY} + {CJK_FAMILY} 已打包" if not absent else f"缺字体文件：{'、'.join(absent)}")

    try:
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import fitz  # noqa: F401

        add("pymupdf", True, "页数读取可用")
    except Exception as exc:  # noqa: BLE001 - 缺库或 ABI 不匹配都会在这里
        add("pymupdf", False, f"读不了页数：{exc}")

    cache = cfg.get("memberCache") or {}
    add("member_cache", bool(cache),
        f"{len(cache)} 个支部的成员快照" if cache else "没有成员缓存，缺团员提示会失效",
        "warning")

    blocked = any(not c["ok"] and c["level"] == "error" for c in checks)
    return {"checks": checks, "blocked": blocked}, []


# ---------- 检查 ----------

def _load_roster(cfg: dict, explicit: str | None):
    """名单用于把档案对到支部；读不出就退化为按账号编号路由，并给出警告。"""
    from app.services.excel import import_roster

    path = explicit
    if not path:
        rosters = cfg.get("rosters") or {}
        current = cfg.get("rosterSemester") or ""
        path = rosters.get(current) or next(iter(rosters.values()), "")
    if not path:
        return None, "没有配置名单，按账号编号路由，无法核对团员"
    if not os.path.isfile(path):
        return None, f"名单文件不在：{path}"
    try:
        return import_roster(path), ""
    except Exception as exc:  # noqa: BLE001 - 表格格式问题一律当警告
        return None, f"名单读取失败：{exc}"


def cmd_scan(args) -> tuple[dict, list[str]]:
    from app.constants import DEFAULT_EXPECTED_PAGES
    from app.domain.archive_scan import scan_folders
    from app.domain.upload_inspect import (
        archived_index,
        inspect,
        member_index,
        pdf_page_count,
        summarize,
    )

    warnings: list[str] = []
    dirs = [os.path.abspath(d) for d in args.dirs]
    for path in dirs:
        if not os.path.isdir(path):
            raise CliError("missing_dir", f"目录不存在：{path}", EXIT_PRECONDITION)
    if args.fix and not args.yes:
        raise CliError("needs_yes", "--fix 会改名，请同时加 --yes", EXIT_PRECONDITION)

    cfg = _config()
    accounts = [a for a in (cfg.get("accounts") or []) if a.get("userName")]
    if not accounts:
        warnings.append("配置里没有账号，文件会全部落在「未归类」")

    expected = args.expected_pages
    if expected is None:
        expected = int(cfg.get("expectedPages", DEFAULT_EXPECTED_PAGES))

    roster, note = _load_roster(cfg, args.roster)
    if note:
        warnings.append(note)

    scans = scan_folders(dirs)
    roots: dict[str, str] = {}
    files: list[str] = []
    skipped: list[dict] = []
    for scan in scans:
        if scan.missing:
            warnings.append(f"目录不在：{scan.root}")
        for path in scan.files:
            roots[path] = scan.root
            files.append(path)
        for path in scan.ignored:
            skipped.append({"path": path, "reason": "文件名解析不出姓名与手机号"})

    if args.fix:
        # 与界面 ScanWorker 相同：改名 + 按内容摘要复用页数结论
        from app.domain.upload_inspect import cached_page_counter

        counter = cached_page_counter()
    else:
        counter = pdf_page_count
    statuses = inspect(
        files, expected, accounts, roster,
        auto_fix=bool(args.fix),
        counter=counter,
        members=member_index(cfg.get("memberCache") or {}),
        archived=archived_index(cfg.get("memberCache") or {}),
        roots=roots,
    )

    def item(status) -> dict:
        return {
            "path": status.path,
            "name": status.name,
            "root": status.root,
            "pages": status.pages,
            "status": status.status_text,
            "usable": status.usable,
            "naming": status.naming,
            "page": status.page,
            "member": status.member,
            "archived": status.archive,
            "account": status.account,
            "branch": status.branch,
            "duplicate_of": status.duplicate_of,
            "problem": status.problem,
        }

    rows = sorted((item(s) for s in statuses), key=lambda r: r["path"])
    groups: dict[str, dict] = {}
    for row in rows:
        if not row["usable"]:
            continue
        key = row["branch"] or "未归类"
        entry = groups.setdefault(key, {"branch": key, "account": row["account"], "files": 0})
        entry["files"] += 1

    counts = summarize(statuses)
    counts["skipped"] = len(skipped)
    data = {
        "dirs": dirs,
        "expected_pages": expected,
        "fixed": bool(args.fix),
        "renamed": sorted(({"from": s.renamed_from, "to": s.path}
                           for s in statuses if s.renamed_from), key=lambda r: r["to"]),
        "counts": counts,
        "branches": sorted(groups.values(), key=lambda g: (-g["files"], g["branch"])),
        "files": rows,
        "skipped": sorted(skipped, key=lambda r: r["path"]),
    }
    return data, warnings


def cmd_cells(args) -> tuple[dict, list[str]]:
    """逐格检查主信息表空格：与上传页同一套 form_cells 判定，只读。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from app.services import form_cells

    paths: list[str] = []
    for entry in args.paths:
        abspath = os.path.abspath(entry)
        if os.path.isdir(abspath):
            from app.domain.archive_scan import scan_folders

            for scan in scan_folders([abspath]):
                paths.extend(scan.files)
        elif os.path.isfile(abspath):
            paths.append(abspath)
        else:
            raise CliError("missing_path", f"文件或目录不存在：{abspath}", EXIT_PRECONDITION)
    paths = sorted(set(paths))

    def one(path: str) -> dict:
        result = form_cells.check(path)
        return {"path": path, "pages": result.pages,
                "checked_pages": result.checked_pages,
                "blanks": len(result.blanks),
                "blank_pages": sorted({c.page for c in result.blanks}),
                "skipped": result.skipped, "summary": result.summary()}

    rows: list[dict] = []
    workers = min(4, max(1, (os.cpu_count() or 4) - 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, p): p for p in paths}
        for fut in as_completed(futures):
            try:
                rows.append(fut.result())
            except Exception as exc:  # noqa: BLE001 - 单份失败不拖垮整批
                rows.append({"path": futures[fut], "pages": 0, "checked_pages": [],
                             "blanks": -1, "blank_pages": [], "skipped": str(exc),
                             "summary": f"检查失败：{exc}"})
    rows.sort(key=lambda r: r["path"])
    data = {
        "counts": {"total": len(rows),
                   "clean": sum(1 for r in rows if r["blanks"] == 0),
                   "blank": sum(1 for r in rows if r["blanks"] > 0),
                   "skipped": sum(1 for r in rows if r["blanks"] < 0 or r["skipped"])},
        "files": rows,
    }
    return data, []


def cmd_cache_clear(args) -> tuple[dict, list[str]]:
    from app.services import check_cache

    if dry := _plan(args, {"action": "清空检查结果缓存", "file": check_cache.CACHE_FILE}):
        return dry
    check_cache.clear()
    return {"cleared": True}, []


# ---------- 上传 ----------

def cmd_upload(args) -> tuple[dict, list[str]]:
    """预检查 → 压缩副本 → 按支部提交；与界面「检查并上传」同一套服务与顺序。"""
    from app.constants import DEFAULT_EXPECTED_PAGES, LIMITS
    from app.domain.upload_inspect import (
        archived_index,
        inspect,
        member_index,
        pdf_page_count,
        summarize,
    )
    from app.services import headless

    warnings: list[str] = []
    dirs = [os.path.abspath(d) for d in args.dirs]
    for path in dirs:
        if not os.path.isdir(path):
            raise CliError("missing_dir", f"目录不存在：{path}", EXIT_PRECONDITION)

    cfg = _config()
    all_accounts = _accounts(cfg, "")
    if not all_accounts:
        raise CliError("missing_accounts", "配置里没有账号", EXIT_PRECONDITION)
    accounts = _accounts(cfg, args.account) if args.account else all_accounts
    expected = args.expected_pages if args.expected_pages is not None \
        else int(cfg.get("expectedPages", DEFAULT_EXPECTED_PAGES))
    roster, note = _load_roster(cfg, args.roster)
    if note:
        warnings.append(note)

    files, roots = headless.collect_files(dirs)
    members = member_index(cfg.get("memberCache") or {})
    archived = archived_index(cfg.get("memberCache") or {})

    if not args.yes:
        statuses = inspect(files, expected, all_accounts, roster, auto_fix=False,
                           counter=pdf_page_count, members=members,
                           archived=archived, roots=roots)
        groups: dict[str, int] = {}
        for s in statuses:
            if s.usable:
                groups[s.account] = groups.get(s.account, 0) + 1
        return {"dry_run": True,
                "plan": {"accounts": sorted(groups.items()),
                         "counts": summarize(statuses)},
                "counts": summarize(statuses)}, warnings

    # 与界面 ScanWorker→UploadWorker 一致：纠正命名、复用页数缓存、压缩副本、提交
    from app.domain.upload_inspect import cached_page_counter
    from app.services import prepare

    statuses = inspect(files, expected, all_accounts, roster, auto_fix=True,
                       counter=cached_page_counter(), members=members,
                       archived=archived, roots=roots)
    roots2 = {s.path: s.root for s in statuses}
    prepare.prepare_all(statuses, roots2)
    creds = _creds(accounts)
    jobs = [{"path": s.work_path, "display": s.path, "account": s.account}
            for s in statuses
            if s.usable and s.work_path and s.account in creds]
    blocked = sum(1 for s in statuses if s.usable and s.work_path
                  and s.account not in creds)
    if blocked:
        warnings.append(f"{blocked} 份档案路由到所选范围之外的账号，本次未提交")
    result = headless.upload_accounts(creds, jobs, LIMITS["MAX_UPLOAD_PER_BATCH"])
    data = {"counts": summarize(statuses), "summary": result["summary"],
            "accounts": result["accounts"], "rows": result["rows"]}
    return data, warnings


# ---------- 登录状态 ----------

def cmd_login_probe(args) -> tuple[dict, list[str]]:
    """逐账号测活：会话复用、必要时登录（冷却闸门与界面一致）。"""
    from app.services import headless

    cfg = _config()
    accounts = _accounts(cfg, args.account)
    if not accounts:
        raise CliError("missing_accounts", "配置里没有账号", EXIT_PRECONDITION)
    rows = headless.probe_accounts(accounts)
    if args.yes:
        headless.persist_health(rows)
        persisted = True
    else:
        persisted = False
    return {"persisted": persisted,
            "counts": {"ok": sum(1 for r in rows if r["status"] == "ok"),
                       "abnormal": sum(1 for r in rows if r["status"] != "ok")},
            "accounts": rows}, []


# ---------- 站内信 ----------

def cmd_messages_pull(args) -> tuple[dict, list[str]]:
    from app.services import headless

    cfg = _config()
    accounts = _accounts(cfg, args.account)
    if not accounts:
        raise CliError("missing_accounts", "配置里没有账号", EXIT_PRECONDITION)
    if dry := _plan(args, {"action": "拉取站内信并合并进 messagesCache",
                           "accounts": [a["userName"] for a in accounts]}):
        return dry
    rows = headless.pull_messages(accounts)
    return {"accounts": rows,
            "counts": {"ok": sum(1 for r in rows if r["status"] == "ok"),
                       "new": sum(r["new"] for r in rows)}}, []


def cmd_messages_list(args) -> tuple[dict, list[str]]:
    cfg = _config()
    cache = cfg.get("messagesCache") or {}
    read = cfg.get("messagesRead") or {}
    items: list[dict] = []
    for account, blob in sorted(cache.items()):
        if args.account and args.account not in (account, blob.get("branch", "")):
            continue
        for m in blob.get("messages", []):
            kind = str(m.get("kind") or "")
            if args.kind and kind != args.kind:
                continue
            unread = int(m.get("status") or 0) == 0 \
                and f"{m.get('account', account)}:{m.get('receive_id')}" not in read
            if args.unread and not unread:
                continue
            items.append({
                "key": f"{m.get('account', account)}:{m.get('receive_id')}",
                "account": m.get("account", account),
                "title": str(m.get("title") or ""),
                "kind": kind,
                "unread": unread,
                "time": str(m.get("time_str") or ""),
                "mobile": str(m.get("mobile") or ""),
            })
    items.sort(key=lambda r: (r["time"], r["key"]), reverse=True)
    return {"total": len(items), "messages": items}, []


def cmd_messages_read(args) -> tuple[dict, list[str]]:
    cfg = _config()
    keys = list(args.keys)
    if args.all:
        read = cfg.get("messagesRead") or {}
        for blob in (cfg.get("messagesCache") or {}).values():
            for m in blob.get("messages", []):
                if int(m.get("status") or 0) == 0:
                    key = f"{m.get('account', '')}:{m.get('receive_id')}"
                    if key not in read:
                        keys.append(key)
    keys = sorted(set(keys))
    if dry := _plan(args, {"action": "本地标记已读", "count": len(keys),
                           "keys": keys[:20]}):
        return dry
    if not keys:
        raise CliError("nothing_to_read",
                       "没有要标记的消息（给 key 或 --all）", EXIT_PRECONDITION)
    from app.services import headless

    headless.mark_messages_read(keys)
    return {"marked": len(keys)}, []


# ---------- 组织 ----------

def cmd_org_sync(args) -> tuple[dict, list[str]]:
    """同步系统数据：拉各支部团员写 memberCache（界面「同步」的等价操作）。"""
    from app.services import headless

    cfg = _config()
    accounts = _accounts(cfg, args.account)
    if not accounts:
        raise CliError("missing_accounts", "配置里没有账号", EXIT_PRECONDITION)
    if dry := _plan(args, {"action": "拉取团员写入成员缓存",
                           "accounts": [a["userName"] for a in accounts]}):
        return dry
    rows = headless.sync_members(accounts)
    return {"accounts": rows,
            "counts": {"ok": sum(1 for r in rows if r["status"] == "ok"),
                       "members": sum(r["count"] for r in rows)}}, []


def cmd_org_stats(args) -> tuple[dict, list[str]]:
    """组织状况：默认读 orgStatsCache 汇总；--online 走在线统计（界面同款）。"""
    from app.domain.org_stats import aggregate_org_stats

    cfg = _config()
    if not args.online:
        summary = aggregate_org_stats(cfg.get("orgStatsCache") or {})
        branches = sorted(
            ({"account": k, "branch": e.get("branch", ""),
              "total": e.get("total", 0), "uploaded": e.get("uploaded", 0),
              "by_kind": e.get("by_kind", {}), "unpaid": e.get("unpaidAmount", 0),
              "updated": e.get("updatedAt", "")}
             for k, e in (cfg.get("orgStatsCache") or {}).items()),
            key=lambda r: r["account"])
        return {"source": "cache", "summary": summary, "branches": branches}, []

    accounts = _accounts(cfg, args.account)
    if not accounts:
        raise CliError("missing_accounts", "配置里没有账号", EXIT_PRECONDITION)
    if args.yes:
        from app.services import headless

        rows = headless.stats_accounts(accounts)
        persisted = headless.persist_org_stats(rows, accounts)
        return {"source": "online", "persisted": persisted,
                "accounts": rows}, []
    # 不写盘：照样在线统计，只是结果不落 orgStatsCache
    from app.services import headless

    rows = headless.stats_accounts(accounts)
    return {"source": "online", "persisted": False,
            "dry_run": True,
            "plan": {"action": "写入组织统计缓存", "accounts": len(rows)},
            "accounts": rows}, []


def cmd_org_tree(args) -> tuple[dict, list[str]]:
    """单个账号的组织树（团支部列表），只读查询。"""
    from app.services import headless, login_gate
    from app.services.api import SiteClient

    cfg = _config()
    matches = _accounts(cfg, args.account)
    if len(matches) != 1:
        raise CliError("ambiguous_account",
                       f"「{args.account}」匹配 {len(matches)} 个账号，请用完整账号",
                       EXIT_PRECONDITION)
    acc = matches[0]
    client = SiteClient(acc["userName"], acc.get("password", ""))
    kind, msg = login_gate.ensure_logged_in(client)
    if kind != "ok":
        raise CliError(f"login_{kind}", f"登录该账号失败：{msg}")
    nodes, diagnostics = client.get_org_tree()
    return {"account": acc["userName"],
            "nodes": sorted(({"oid": n["oid"], "name": n["orgName"],
                              "parent": n["parentOid"]} for n in nodes),
                            key=lambda n: n["oid"]),
            "diagnostics": [vars(d) for d in diagnostics]}, []


# ---------- 导出 ----------

_EXPORT_CONTENTS = ("members", "fee", "archive", "transfer", "identity")


def cmd_export(args) -> tuple[dict, list[str]]:
    from app.services.export_data import CONTENT_LABELS, run_export

    contents = [c.strip() for c in (args.contents or "").split(",") if c.strip()]
    bad = [c for c in contents if c not in _EXPORT_CONTENTS]
    if bad or not contents:
        raise CliError("bad_contents",
                       f"--contents 只能是 {'、'.join(_EXPORT_CONTENTS)} 的逗号组合",
                       EXIT_USAGE)
    out = _abs_out(args.out, ".xlsx")
    if os.path.exists(out):
        raise CliError("file_exists", f"目标文件已存在：{out}，换个名字或先删掉",
                       EXIT_PRECONDITION)
    if dry := _plan(args, {"action": "导出 xlsx", "out": out,
                           "contents": [CONTENT_LABELS[c] for c in contents],
                           "mode": args.mode, "scope": args.scope}):
        return dry
    cfg = _config()
    try:
        result = run_export(cfg, contents, mode=args.mode, scope=args.scope,
                            target=out)
    except ValueError as exc:
        raise CliError("export_failed", str(exc))
    return {"out": out, **result}, []


# ---------- 云端备份 ----------

def _backup_profile(cfg: dict, code: str) -> dict:
    from app.services import cloud_backup

    profiles = cloud_backup.load_profiles(cfg)
    if not profiles:
        raise CliError("no_profile", "还没配置云端备份档案（界面上先建一个，或用 config set）",
                       EXIT_PRECONDITION)
    if not code:
        return profiles[0]
    for p in profiles:
        if p["code"] == cloud_backup.normalize_code(code):
            return p
    raise CliError("unknown_profile", f"没有提取码为 {code} 的档案", EXIT_PRECONDITION)


def cmd_backup_list(args) -> tuple[dict, list[str]]:
    from app.services import cloud_backup

    cfg = _config()
    return {"profiles": [
        {"name": p["name"], "server": p["server"], "code": p["code"],
         "owned": bool(p["ownerKey"])}
        for p in cloud_backup.load_profiles(cfg)]}, []


def cmd_backup_probe(args) -> tuple[dict, list[str]]:
    from app.services import cloud_backup

    profile = _backup_profile(_config(), args.profile)
    try:
        exists = cloud_backup.probe(profile["server"], profile["code"])
    except Exception as exc:  # noqa: BLE001
        raise CliError("probe_failed", str(exc))
    return {"profile": profile["code"], "exists": exists}, []


def cmd_backup_push(args) -> tuple[dict, list[str]]:
    from app.services import cloud_backup

    cfg = _config()
    profile = _backup_profile(cfg, args.profile)
    if not profile["ownerKey"]:
        raise CliError("not_owner", "这是他人的配置档案，只有所属人能上传",
                       EXIT_PRECONDITION)
    if dry := _plan(args, {"action": "上传整份设置到云端", "profile": profile["code"],
                           "server": profile["server"]}):
        return dry
    try:
        password = cloud_backup.normalize_password(args.password)
        envelope = cloud_backup.encrypt_config(cfg, profile["code"], password)
        cloud_backup.upload(profile["server"], profile["code"], envelope,
                            profile["ownerKey"])
    except Exception as exc:  # noqa: BLE001
        raise CliError("push_failed", str(exc))
    return {"pushed": True, "profile": profile["code"]}, []


def cmd_backup_pull(args) -> tuple[dict, list[str]]:
    """拉云端备份并解密成 JSON 文件；用 `config import` 落地生效（两步，可先看再换）。"""
    from app.services import cloud_backup

    cfg = _config()
    profile = _backup_profile(cfg, args.profile)
    out = _abs_out(args.out, ".json")
    if dry := _plan(args, {"action": "下载并解密云端备份", "out": out,
                           "profile": profile["code"]}):
        return dry
    try:
        envelope = cloud_backup.download(profile["server"], profile["code"])
        restored = cloud_backup.decrypt_config(envelope, profile["code"],
                                               args.password.strip())
    except Exception as exc:  # noqa: BLE001
        raise CliError("pull_failed", str(exc))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(restored, f, ensure_ascii=False, indent=2)
    return {"out": out, "accounts": len(restored.get("accounts") or []),
            "next": f"确认后执行 config import {out} --yes"}, []


# ---------- 手机传档 ----------

def cmd_pair_serve(args) -> tuple[dict, list[str]]:
    """起局域网接收：手机扫码上传，落盘到「同步自手机」当日目录。"""
    import time

    from app.services.cloud_backup import local_config_codes
    from app.services.lan_share import LanShare

    if dry := _plan(args, {"action": "起局域网服务接收手机档案",
                           "minutes": args.minutes}):
        return dry
    received: list[dict] = []

    def on_received(item):
        received.append({"name": item.name, "path": item.path,
                         "status": item.status, "note": item.note})

    share = LanShare(on_received=on_received, allowed_codes=local_config_codes())
    url = ""
    try:
        url = share.start()
    except OSError as exc:
        raise CliError("serve_failed", str(exc))
    limit = args.minutes * 60
    deadline = time.monotonic() + limit
    print(f"接收中 {limit} 秒，手机扫码或打开：{url}", file=sys.stderr, flush=True)
    try:
        while time.monotonic() < deadline:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        share.stop()
    return {"url": url, "received": received,
            "counts": {"saved": sum(1 for r in received if r["status"] in ("ok", "need_rename")),
                       "duplicate": sum(1 for r in received if r["status"] == "duplicate"),
                       "rejected": sum(1 for r in received if r["status"] == "rejected")}}, []


def cmd_pair_pull(args) -> tuple[dict, list[str]]:
    """从云端中转站拉回手机传的档案（局域网连不上时的通道）。"""
    from app.services import relay_receive
    from app.services.lan_share import LanShare

    cfg = _config()
    relay = relay_receive.local_relay_config()
    if not relay:
        raise CliError("no_relay", "没配置云端备份档案，中转拉取不可用", EXIT_PRECONDITION)
    if dry := _plan(args, {"action": "从中转站拉回档案并落盘",
                           "server": relay["server"], "code": relay["code"]}):
        return dry
    already = set(LanShare().known_digests())
    try:
        summary = relay_receive.receive_all(relay["server"], relay["code"],
                                            already=already,
                                            owner_key=relay.get("owner_key", ""))
    except relay_receive.RelayError as exc:
        raise CliError("relay_failed", str(exc))
    return {
        "saved": summary["saved"], "duplicates": summary["duplicates"],
        "rejected": [[n, r] for n, r in summary["rejected"]],
        "skipped": summary["skipped"],
        "errors": [[n, r] for n, r in summary["errors"]],
    }, []


def cmd_pair_list(args) -> tuple[dict, list[str]]:
    from app.services import paired_devices

    return {"devices": [d.to_dict() for d in paired_devices.list_devices()]}, []


def cmd_pair_forget(args) -> tuple[dict, list[str]]:
    from app.services import paired_devices

    if dry := _plan(args, {"action": "移除已配对设备", "name": args.name,
                           "host": args.host}):
        return dry
    try:
        rest = paired_devices.forget(args.name, args.host)
    except RuntimeError as exc:
        raise CliError("paired_unreadable", str(exc))
    return {"remaining": [d.to_dict() for d in rest]}, []


# ---------- 配置 ----------

_SENSITIVE_KEYS = {"accounts", "sessionCookies", "cloudProfiles"}


def cmd_config_get(args) -> tuple[dict, list[str]]:
    cfg = _config()
    if not args.key:
        data = {k: v for k, v in sorted(cfg.items())}
        masked = sorted(set(_SENSITIVE_KEYS) & set(cfg))
        return {"keys": sorted(cfg), "values": data,
                "note": f"含敏感数据的键：{'、'.join(masked)}" if masked else ""}, []
    if args.key not in cfg:
        raise CliError("unknown_key", f"配置里没有键「{args.key}」", EXIT_PRECONDITION)
    return {"key": args.key, "value": cfg[args.key]}, []


def cmd_config_set(args) -> tuple[dict, list[str]]:
    value = args.value
    try:
        value = json.loads(args.value)
    except ValueError:
        pass  # 不是 JSON 就当字符串
    if args.key == "accounts" and not isinstance(value, list):
        raise CliError("bad_value", "accounts 必须是数组", EXIT_USAGE)

    def _write(cfg: dict):
        cfg[args.key] = value

    if dry := _plan(args, {"action": "写配置键", "key": args.key,
                           "sensitive": args.key in _SENSITIVE_KEYS}):
        return dry
    _write_config(_write)
    return {"set": args.key}, []


def cmd_config_import(args) -> tuple[dict, list[str]]:
    """整份替换配置（界面「恢复系统备份」的等价操作），先留当前配置的副本。"""
    from datetime import datetime

    from app.services import store

    path = os.path.abspath(os.path.expanduser(args.path))
    if not os.path.isfile(path):
        raise CliError("missing_file", f"备份文件不存在：{path}", EXIT_PRECONDITION)
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as exc:
        raise CliError("bad_backup", f"读取失败：{exc}")
    if not isinstance(cfg, dict) or not isinstance(cfg.get("accounts"), list):
        raise CliError("bad_backup", "JSON 里没有 accounts 列表，不是本程序导出的备份",
                       EXIT_PRECONDITION)
    if dry := _plan(args, {"action": "整份替换配置", "from": path,
                           "accounts": len(cfg.get("accounts") or [])}):
        return dry
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{store.CONFIG_FILE}.before-import-{stamp}.json"
    try:
        if os.path.exists(store.CONFIG_FILE):
            import shutil

            shutil.copyfile(store.CONFIG_FILE, backup)
        store.save(cfg)
    except OSError as exc:
        raise CliError("import_failed", str(exc))
    return {"imported": True, "backup": backup,
            "accounts": len(cfg.get("accounts") or [])}, []


def cmd_config_export(args) -> tuple[dict, list[str]]:
    cfg = _config()
    out = _abs_out(args.path, ".json")
    if dry := _plan(args, {"action": "导出整份配置", "out": out,
                           "contains_passwords": bool(cfg.get("accounts"))}):
        return dry
    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        raise CliError("export_failed", str(exc))
    return {"out": out, "accounts": len(cfg.get("accounts") or [])}, []


def cmd_config_dirs(args) -> tuple[dict, list[str]]:
    from app.services.workspace import load_workspace_dirs, save_workspace_dirs

    if args.action == "list":
        return {"dirs": load_workspace_dirs()}, []
    path = os.path.abspath(os.path.expanduser(args.path or ""))
    if not path:
        raise CliError("missing_path", "add/remove 需要一个目录路径", EXIT_USAGE)
    if args.action == "remove":
        current = load_workspace_dirs()
        if path not in current:
            raise CliError("not_listed", f"来源目录列表里没有：{path}", EXIT_PRECONDITION)
        if dry := _plan(args, {"action": "移除来源目录", "path": path}):
            return dry
        save_workspace_dirs([d for d in current if d != path])
        return {"dirs": load_workspace_dirs()}, []
    if not os.path.isdir(path):
        raise CliError("missing_dir", f"目录不存在：{path}", EXIT_PRECONDITION)
    if dry := _plan(args, {"action": "添加来源目录", "path": path}):
        return dry
    save_workspace_dirs(load_workspace_dirs() + [path])
    return {"dirs": load_workspace_dirs()}, []


# ---------- 名单与团支书 ----------

def cmd_roster_import(args) -> tuple[dict, list[str]]:
    from app.services.excel import import_roster

    path = os.path.abspath(os.path.expanduser(args.path))
    if not os.path.isfile(path):
        raise CliError("missing_file", f"名单文件不存在：{path}", EXIT_PRECONDITION)
    semester = args.semester or os.path.splitext(os.path.basename(path))[0]
    try:
        roster = import_roster(path, semester)
    except ValueError as exc:
        raise CliError("roster_invalid", str(exc))
    if dry := _plan(args, {"action": "登记名单", "semester": semester,
                           "rows": len(roster.rows), "path": path}):
        return dry

    def _write(cfg: dict):
        rosters = dict(cfg.get("rosters") or {})
        rosters[semester] = path
        cfg["rosters"] = rosters

    _write_config(_write)
    return {"semester": semester, "rows": len(roster.rows), "path": path}, []


def cmd_roster_delete(args) -> tuple[dict, list[str]]:
    cfg = _config()
    rosters = dict(cfg.get("rosters") or {})
    if args.semester not in rosters:
        raise CliError("unknown_semester",
                       f"没有学期「{args.semester}」的名单，现有：{'、'.join(sorted(rosters)) or '无'}",
                       EXIT_PRECONDITION)
    if dry := _plan(args, {"action": "移除名单登记（不删文件）",
                           "semester": args.semester, "path": rosters[args.semester]}):
        return dry
    rosters.pop(args.semester)
    _write_config(lambda cfg: cfg.__setitem__("rosters", rosters))
    return {"deleted": args.semester}, []


def cmd_roster_use(args) -> tuple[dict, list[str]]:
    cfg = _config()
    rosters = cfg.get("rosters") or {}
    if args.semester not in rosters:
        raise CliError("unknown_semester", f"没有学期「{args.semester}」的名单",
                       EXIT_PRECONDITION)
    if dry := _plan(args, {"action": "切换当前学期", "semester": args.semester}):
        return dry
    _write_config(lambda cfg: cfg.__setitem__("rosterSemester", args.semester))
    return {"rosterSemester": args.semester}, []


def _cmd_template(kind: str, args) -> tuple[dict, list[str]]:
    from app.services import excel

    out = _abs_out(args.out, ".xlsx")
    if os.path.exists(out):
        raise CliError("file_exists", f"目标文件已存在：{out}", EXIT_PRECONDITION)
    if dry := _plan(args, {"action": f"生成{kind}模板", "out": out}):
        return dry
    writer = {"roster": excel.save_roster_template,
              "secretary": excel.save_secretary_template,
              "account": excel.save_account_template}[kind]
    rows = writer(out)
    return {"out": out, "sample_rows": rows}, []


def cmd_roster_template(args) -> tuple[dict, list[str]]:
    return _cmd_template("roster", args)


def cmd_secretary_template(args) -> tuple[dict, list[str]]:
    return _cmd_template("secretary", args)


def cmd_secretary_import(args) -> tuple[dict, list[str]]:
    from app.services.excel import import_secretaries

    path = os.path.abspath(os.path.expanduser(args.path))
    if not os.path.isfile(path):
        raise CliError("missing_file", f"名单文件不存在：{path}", EXIT_PRECONDITION)
    try:
        rows = import_secretaries(path)
    except ValueError as exc:
        raise CliError("secretary_invalid", str(exc))
    if dry := _plan(args, {"action": "导入团支书名单", "branches": len(rows)}):
        return dry
    _write_config(lambda cfg: cfg.__setitem__("secretaries", rows))
    return {"branches": len(rows)}, []


# ---------- 系统 ----------

def cmd_autostart(args) -> tuple[dict, list[str]]:
    from app.services import autostart

    if args.action == "status":
        return {"installed": autostart.is_installed()}, []
    if args.action == "on":
        if dry := _plan(args, {"action": "开启开机自启"}):
            return dry
        try:
            where = autostart.install()
        except OSError as exc:
            raise CliError("autostart_failed", str(exc))
        return {"installed": True, "entry": where}, []
    if dry := _plan(args, {"action": "关闭开机自启"}):
        return dry
    autostart.uninstall()
    return {"installed": False}, []


def cmd_rule(args) -> tuple[dict, list[str]]:
    from app.domain.org_rule import OrgRule, RuleError, explain_code

    try:
        rule = OrgRule.from_config(_config())
    except RuleError as exc:
        raise CliError("bad_rule", str(exc))
    if args.action == "show":
        return {"rules": rule.rule_text().splitlines(),
                "label": rule.label_template,
                "century": rule.century,
                "year_start_month": rule.year_start_month,
                "samples": rule.samples.splitlines()}, []
    info, reason = explain_code(args.code, rule)
    return {"code": args.code,
            "match": None if info is None else
            {"dept": info.dept, "grade": info.grade, "class_no": info.class_no,
             "label": info.label},
            "reason": reason}, []


def cmd_update(args) -> tuple[dict, list[str]]:
    from app.services import updater

    try:
        info = updater.check_update()
    except updater.UpdateError as exc:
        raise CliError("update_check_failed", str(exc))
    if args.action == "check":
        if info is None:
            return {"available": False, "version": updater.__version__}, []
        return {"available": True, "version": info.version, "notes": info.notes,
                "filename": info.filename, "source": info.source,
                "local_version": updater.__version__}, []
    if info is None:
        raise CliError("already_latest", f"已是最新（{updater.__version__}）",
                       EXIT_PRECONDITION)
    if dry := _plan(args, {"action": "下载更新包", "version": info.version,
                           "filename": info.filename}):
        return dry
    try:
        path = updater.download(info)
    except (updater.UpdateError, OSError) as exc:
        raise CliError("download_failed", str(exc))
    # 与界面一致：只下载，不自动安装；装不装由用户点「重启」决定
    return {"downloaded": path, "version": info.version, "installed": False}, []


def cmd_announce(args) -> tuple[dict, list[str]]:
    from app.services import announcements

    try:
        items = announcements.fetch_announcements()
    except announcements.AnnouncementError as exc:
        raise CliError("announce_failed", str(exc))
    seen = announcements.seen_ids()
    return {"items": [{"id": a.id, "title": a.title, "date": a.date,
                       "lines": list(a.items), "seen": a.id in seen}
                      for a in items],
            "pending": len(announcements.pending(items))}, []


def cmd_daily_check(args) -> tuple[dict, list[str]]:
    from app.services.daily import run_daily_check

    ok, message = run_daily_check()
    return {"check_ok": ok, "summary": message}, []


def cmd_logs(args) -> tuple[dict, list[str]]:
    from app.services.app_log import export_logs

    out = _abs_out(args.out, ".log")
    if dry := _plan(args, {"action": "导出运行日志", "out": out}):
        return dry
    try:
        count = export_logs(out)
    except OSError as exc:
        raise CliError("logs_failed", str(exc))
    return {"out": out, "files": count}, []


# ---------- 参数解析 ----------

def _add_yes(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    p.add_argument("--yes", action="store_true",
                   help="确认执行写操作；不加时只报告会发生什么")
    return p


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py", description="智慧团委命令行：无界面跑全部业务，给脚本与 AI 用。")
    parser.add_argument("--json", action="store_true", help="输出 JSON（给程序读）")
    parser.add_argument("--config-dir", help="用指定目录的配置，不读用户配置")
    sub = parser.add_subparsers(dest="command", required=True)

    def branch(name: str, help_text: str):
        p = sub.add_parser(name, help=help_text)
        return p.add_subparsers(dest="action", required=True)

    sub.add_parser("version", help="版本、字体与配置路径").set_defaults(func=cmd_version)
    sub.add_parser("doctor", help="环境自检").set_defaults(func=cmd_doctor)

    scan = sub.add_parser("scan", help="检查来源目录里的档案（默认只读）")
    scan.add_argument("dirs", nargs="+", help="要检查的目录，可给多个")
    scan.add_argument("--expected-pages", type=int, default=None,
                      help="预期页数；不给就用配置里的值")
    scan.add_argument("--roster", help="名单 xlsx；不给就用配置里当前学期的")
    scan.add_argument("--fix", action="store_true",
                      help="把不规范的文件名改成标准名（需 --yes）")
    scan.add_argument("--yes", action="store_true")
    scan.set_defaults(func=cmd_scan)

    cells = sub.add_parser("cells", help="检查主信息表空格（只读）")
    cells.add_argument("paths", nargs="+", help="PDF 或目录，可给多个")
    cells.set_defaults(func=cmd_cells)

    cache = branch("cache", "检查结果缓存")
    _add_yes(cache.add_parser("clear", help="清空缓存，下次全量重查")
             ).set_defaults(func=cmd_cache_clear)

    upload = sub.add_parser("upload", help="预检查 → 压缩 → 上传（需 --yes）")
    upload.add_argument("dirs", nargs="+", help="来源目录，可给多个")
    upload.add_argument("--expected-pages", type=int, default=None)
    upload.add_argument("--roster", help="名单 xlsx；不给用配置里的")
    upload.add_argument("--account", help="只提交账号名/备注含此串的支部")
    _add_yes(upload).set_defaults(func=cmd_upload)

    login = branch("login", "登录与账号状态")
    probe = login.add_parser("probe", help="逐账号测活（加 --yes 写回健康状态）")
    probe.add_argument("--account", help="只测账号名/备注含此串的")
    _add_yes(probe).set_defaults(func=cmd_login_probe)

    messages = branch("messages", "站内信")
    mp = messages.add_parser("pull", help="拉取站内信（需 --yes 写缓存）")
    mp.add_argument("--account")
    _add_yes(mp).set_defaults(func=cmd_messages_pull)
    ml = messages.add_parser("list", help="读本地消息缓存（只读）")
    ml.add_argument("--account", help="按账号或支部名过滤")
    ml.add_argument("--kind", choices=["report", "transfer", "other"])
    ml.add_argument("--unread", action="store_true", help="只看未读")
    ml.set_defaults(func=cmd_messages_list)
    mr = messages.add_parser("read", help="标记已读（需 --yes）")
    mr.add_argument("keys", nargs="*", help="消息 key（account:receive_id）")
    mr.add_argument("--all", action="store_true", help="标记全部未读")
    _add_yes(mr).set_defaults(func=cmd_messages_read)

    org = branch("org", "组织数据")
    osync = org.add_parser("sync", help="同步系统数据：拉团员写缓存（需 --yes）")
    osync.add_argument("--account")
    _add_yes(osync).set_defaults(func=cmd_org_sync)
    ost = org.add_parser("stats", help="组织状况：默认读缓存汇总")
    ost.add_argument("--online", action="store_true", help="在线统计（--yes 才写缓存）")
    ost.add_argument("--account")
    ost.add_argument("--yes", action="store_true")
    ost.set_defaults(func=cmd_org_stats)
    otree = org.add_parser("tree", help="单账号的组织树（只读）")
    otree.add_argument("account", help="账号名或备注（须唯一匹配）")
    otree.set_defaults(func=cmd_org_tree)

    export = sub.add_parser("export", help="导出数据到 xlsx（需 --yes）")
    export.add_argument("--contents", required=True,
                        help="逗号组合：members,fee,archive,transfer,identity")
    export.add_argument("--mode", choices=["split", "single"], default="split",
                        help="每项一表或合成一张")
    export.add_argument("--scope", choices=["merged", "branch"], default="merged",
                        help="全部合并或一班一表")
    export.add_argument("--out", required=True, help="保存路径（xlsx）")
    _add_yes(export).set_defaults(func=cmd_export)

    backup = branch("backup", "云端配置备份")
    backup.add_parser("list", help="本机备份档案（只读）").set_defaults(func=cmd_backup_list)
    bprobe = backup.add_parser("probe", help="云端有没有这份档案")
    bprobe.add_argument("--profile", default="")
    bprobe.set_defaults(func=cmd_backup_probe)
    bp = backup.add_parser("push", help="上传当前设置（需 --yes）")
    bp.add_argument("--profile", default="")
    bp.add_argument("--password", required=True)
    _add_yes(bp).set_defaults(func=cmd_backup_push)
    bl = backup.add_parser("pull", help="下载解密云端备份到文件（需 --yes）")
    bl.add_argument("--profile", default="")
    bl.add_argument("--password", default="")
    bl.add_argument("--out", required=True)
    _add_yes(bl).set_defaults(func=cmd_backup_pull)

    pair = branch("pair", "手机传档案")
    ps = pair.add_parser("serve", help="局域网接收服务，扫码上传（需 --yes）")
    ps.add_argument("--minutes", type=float, default=5, help="运行时长，默认 5 分钟")
    _add_yes(ps).set_defaults(func=cmd_pair_serve)
    pp = pair.add_parser("pull", help="从云端中转拉回手机传的档案（需 --yes）")
    _add_yes(pp).set_defaults(func=cmd_pair_pull)
    pair.add_parser("list", help="已配对设备（只读）").set_defaults(func=cmd_pair_list)
    pf = pair.add_parser("forget", help="移除配对（需 --yes）")
    pf.add_argument("name")
    pf.add_argument("--host", default="")
    _add_yes(pf).set_defaults(func=cmd_pair_forget)

    config = branch("config", "配置读写")
    cg = config.add_parser("get", help="读配置（只读）")
    cg.add_argument("key", nargs="?", default="")
    cg.set_defaults(func=cmd_config_get)
    cs = config.add_parser("set", help="写一个配置键（需 --yes）")
    cs.add_argument("key")
    cs.add_argument("value", help="JSON 值；不是 JSON 就当字符串")
    _add_yes(cs).set_defaults(func=cmd_config_set)
    ci = config.add_parser("import", help="用备份文件整份替换配置（需 --yes）")
    ci.add_argument("path")
    _add_yes(ci).set_defaults(func=cmd_config_import)
    ce = config.add_parser("export", help="导出整份配置（需 --yes）")
    ce.add_argument("path")
    _add_yes(ce).set_defaults(func=cmd_config_export)
    cd = config.add_parser("dirs", help="来源目录列表：list / add / remove")
    cd.add_argument("action", choices=["list", "add", "remove"])
    cd.add_argument("path", nargs="?", default="")
    cd.add_argument("--yes", action="store_true")
    cd.set_defaults(func=cmd_config_dirs)

    roster = branch("roster", "团员名单")
    ri = roster.add_parser("import", help="登记一份名单（需 --yes）")
    ri.add_argument("path")
    ri.add_argument("--semester", default="")
    _add_yes(ri).set_defaults(func=cmd_roster_import)
    rd = roster.add_parser("delete", help="移除名单登记（需 --yes）")
    rd.add_argument("semester")
    _add_yes(rd).set_defaults(func=cmd_roster_delete)
    ru = roster.add_parser("use", help="切换当前学期（需 --yes）")
    ru.add_argument("semester")
    _add_yes(ru).set_defaults(func=cmd_roster_use)
    rt = roster.add_parser("template", help="生成名单模板（需 --yes）")
    rt.add_argument("--out", required=True)
    _add_yes(rt).set_defaults(func=cmd_roster_template)

    secretary = branch("secretary", "团支书名单")
    si = secretary.add_parser("import", help="导入团支书名单（需 --yes）")
    si.add_argument("path")
    _add_yes(si).set_defaults(func=cmd_secretary_import)
    st = secretary.add_parser("template", help="生成团支书模板（需 --yes）")
    st.add_argument("--out", required=True)
    _add_yes(st).set_defaults(func=cmd_secretary_template)

    autostart = branch("autostart", "开机自启")
    autostart.add_parser("status", help="当前状态（只读）").set_defaults(func=cmd_autostart)
    _add_yes(autostart.add_parser("on", help="开启（需 --yes）")).set_defaults(func=cmd_autostart)
    _add_yes(autostart.add_parser("off", help="关闭（需 --yes）")).set_defaults(func=cmd_autostart)

    rule = branch("rule", "班级编号规则")
    rule.add_parser("show", help="当前规则（只读）").set_defaults(func=cmd_rule)
    re_ = rule.add_parser("explain", help="解析一个编号（只读）")
    re_.add_argument("code")
    re_.set_defaults(func=cmd_rule)

    update = branch("update", "版本更新")
    update.add_parser("check", help="检查新版本（只读）").set_defaults(func=cmd_update)
    ud = update.add_parser("download", help="下载更新包，不自动安装（需 --yes）")
    _add_yes(ud).set_defaults(func=cmd_update)

    announce = branch("announce", "公告")
    announce.add_parser("fetch", help="拉取公告（只读）").set_defaults(func=cmd_announce)

    sub.add_parser("daily-check", help="每日检查：当前账号在线体检并出摘要"
                   ).set_defaults(func=cmd_daily_check)

    logs = sub.add_parser("logs", help="导出运行日志（需 --yes）")
    logs.add_argument("--out", required=True)
    _add_yes(logs).set_defaults(func=cmd_logs)
    return parser


# ---------- 文本渲染 ----------

def _text(command: str, data: dict, warnings: list[str]) -> str:
    lines: list[str] = []
    top, _, action = command.partition(" ")
    if top == "version":
        lines.append(f"版本 {data['version']}（Python {data['python']}）")
        lines.append(f"字体 {data['font']['latin']} + {data['font']['cjk']}"
                     f"{'' if data['font']['bundled'] else '（文件缺失）'}")
        lines.append(f"配置 {data['config_file']}")
    elif top == "doctor":
        for check in data["checks"]:
            mark = "OK  " if check["ok"] else ("FAIL" if check["level"] == "error" else "WARN")
            lines.append(f"[{mark}] {check['id']}: {check['detail']}")
    elif top == "scan":
        counts = data["counts"]
        lines.append(f"共 {counts['total']} 份：正常 {counts['ok']}，已纠正 {counts['fixed']}，"
                     f"缺团员 {counts['missing']}，异常 {counts['error']}，"
                     f"重复 {counts['duplicate']}，已有档案 {counts['archive']}，"
                     f"未纳入 {counts['skipped']}")
        for group in data["branches"]:
            lines.append(f"  {group['branch']}（{group['account']}）{group['files']} 份")
        for row in data["renamed"]:
            lines.append(f"  [改名] {row['from']} → {os.path.basename(row['to'])}")
        for row in data["files"]:
            if row["usable"] and row["naming"] == "ok" and not row["problem"]:
                continue
            lines.append(f"  [{row['status']}] {row['path']} {row['problem']}".rstrip())
        for item in data["skipped"]:
            lines.append(f"  [未纳入] {item['path']} {item['reason']}")
    elif top == "cells":
        c = data["counts"]
        lines.append(f"共 {c['total']} 份：无空格 {c['clean']}，有空格 {c['blank']}，"
                     f"跳过 {c['skipped']}")
        for row in data["files"]:
            if row["blanks"] > 0:
                lines.append(f"  {row['path']}：{row['summary']}")
            elif row["skipped"]:
                lines.append(f"  [跳过] {row['path']}：{row['skipped']}")
    elif top == "upload":
        if data.get("dry_run"):
            counts = data["counts"]
            lines.append(f"预演（加 --yes 才真正上传）：可传 {counts['total'] - counts['error'] - counts['archive'] - counts['duplicate']} 份")
            for account, n in data["plan"]["accounts"]:
                lines.append(f"  {account}：{n} 份")
        else:
            s = data["summary"]
            lines.append(f"上传成功 {s['ok']}，跳过 {s['skipped']}，失败 {s['failed']}，"
                         f"自动审核 {s['audit_ok']}，需手动审核 {s['audit_manual']}")
            for row in data["rows"]:
                if row["result"] != "成功":
                    lines.append(f"  [{row['result']}] {row['account']} {row['path']} {row['reason']}")
    elif top == "login":
        lines.append(f"正常 {data['counts']['ok']} 个，异常 {data['counts']['abnormal']} 个"
                     f"{'（已写回健康状态）' if data['persisted'] else '（未写回，加 --yes 写回）'}")
        for r in data["accounts"]:
            if r["status"] != "ok":
                lines.append(f"  [{r['status']}] {r['user']}：{r['message']}")
    elif top == "messages":
        if "messages" in data:
            for m in data["messages"]:
                mark = "未读" if m["unread"] else "已读"
                lines.append(f"  [{mark}] {m['account']} {m['time']} {m['title']}（{m['key']}）")
            lines.insert(0, f"{data['total']} 条消息")
        elif "accounts" in data:
            for r in data["accounts"]:
                lines.append(f"  {r['user']}：{r['count']} 条（{r['status']}）{r['message']}")
        else:
            lines.append(f"已标记 {data['marked']} 条")
    elif top == "org":
        if action == "sync":
            for r in data["accounts"]:
                lines.append(f"  {r['user']}：{r['count']} 名团员（{r['status']}）{r['message']}")
        elif action == "stats":
            if data.get("source") == "cache":
                s = data["summary"]
                lines.append(f"缓存汇总：{s.get('total', 0)} 名团员，已上传 {s.get('uploaded', 0)}，"
                             f"异常 {s.get('abnormal', 0)}，转接申请 {s.get('applied', 0)}")
                if s.get("updated"):
                    lines.append(f"统计更新到 {s['updated']}")
            else:
                for r in data["accounts"]:
                    flag = "（已写回）" if data.get("persisted") else ""
                    lines.append(f"  {r['user']}：总数 {r['total']}，已传 {r['uploaded']}，"
                                 f"异常 {r['abnormal']}（{r['status']}）{flag}")
        elif action == "tree":
            lines.append(f"{data['account']}：{len(data['nodes'])} 个组织节点")
            for n in data["nodes"]:
                lines.append(f"  {n['oid']} {n['name']}")
    elif top == "export":
        if data.get("dry_run"):
            p = data["plan"]
            lines.append(f"预演：导出 {p['contents']} 到 {p['out']}（{p['scope']}/{p['mode']}）")
        else:
            lines.append(f"已导出 {data['rows']} 行（{data['branches']} 个支部）→ {data['out']}")
    elif top == "backup":
        if action == "list":
            lines.append(f"{len(data['profiles'])} 个备份档案")
            for p in data["profiles"]:
                lines.append(f"  {p['code']} {p['name']}（{p['server']}，"
                             f"{'本人' if p['owned'] else '他人'}）")
        elif action == "probe":
            lines.append(f"{data['profile']}：云端{'有' if data['exists'] else '没有'}档案")
        elif action == "pull":
            lines.append(f"已解密到 {data['out']}（{data['accounts']} 个账号）")
            lines.append(f"确认后执行：config import {data['out']} --yes")
        elif data.get("dry_run"):
            lines.append("预演：" + data["plan"]["action"])
        else:
            lines.append("已上传")
    elif top == "pair":
        if action == "serve":
            lines.append(f"服务地址 {data['url']}")
            lines.append(f"收到 {len(data['received'])} 份：保存 "
                         f"{data['counts']['saved']}，重复 {data['counts']['duplicate']}，"
                         f"拒绝 {data['counts']['rejected']}")
            for r in data["received"]:
                lines.append(f"  [{r['status']}] {r['name']} {r['note']}")
        elif action == "pull":
            lines.append(f"拉回 {len(data['saved'])} 份，重复 {len(data['duplicates'])} 份，"
                         f"拒绝 {len(data['rejected'])} 份，失败 {len(data['errors'])} 份")
            for n, r in data["errors"]:
                lines.append(f"  [失败] {n}：{r}")
        elif action == "list":
            for d in data["devices"]:
                lines.append(f"  {d['name']}（{d['host']}）")
        elif data.get("dry_run"):
            lines.append("预演：" + data["plan"]["action"])
        else:
            lines.append(f"剩余 {len(data['remaining'])} 台设备")
    elif top == "config":
        if action == "get":
            if "key" in data:
                lines.append(json.dumps(data["value"], ensure_ascii=False, indent=2))
            else:
                for k in data["keys"]:
                    lines.append(f"  {k}")
                if data.get("note"):
                    lines.append(f"注意：{data['note']}")
        elif data.get("dry_run"):
            lines.append("预演：" + data["plan"]["action"])
        elif "dirs" in data:
            for d in data["dirs"]:
                lines.append(f"  {d}")
        elif "imported" in data:
            lines.append(f"已替换配置，当前配置备份在 {data['backup']}")
        else:
            lines.append(json.dumps(data, ensure_ascii=False))
    elif top in ("roster", "secretary"):
        if data.get("dry_run"):
            lines.append("预演：" + data["plan"]["action"])
        else:
            lines.append(json.dumps(data, ensure_ascii=False))
    elif top == "autostart":
        lines.append("已开启" if data.get("installed") else "未开启")
    elif top == "rule":
        if action == "show":
            lines.extend(data["rules"])
        else:
            if data["match"]:
                m = data["match"]
                lines.append(f"{data['code']} → {m['label']}（{m['dept']} {m['grade']} {m['class_no']} 班）")
            else:
                lines.append(f"{data['code']} 解析不了：{data['reason']}")
    elif top == "update":
        if action == "check":
            if data["available"]:
                lines.append(f"新版本 {data['version']}（当前 {data['local_version']}）")
                lines.append(data["notes"])
            else:
                lines.append(f"已是最新（{data['version']}）")
        elif data.get("dry_run"):
            lines.append(f"预演：下载 {data['plan']['version']}")
        else:
            lines.append(f"已下载到 {data['downloaded']}（不会自动安装）")
    elif top == "announce":
        for a in data["items"]:
            mark = "已读" if a["seen"] else "未读"
            lines.append(f"  [{mark}] {a['date']} {a['title']}")
            for line in a["lines"]:
                lines.append(f"      {line}")
        if not data["items"]:
            lines.append("没有公告")
    elif top == "daily-check":
        lines.append(data["summary"])
    elif top == "logs":
        if data.get("dry_run"):
            lines.append("预演：导出日志到 " + data["plan"]["out"])
        else:
            lines.append(f"已导出 {data['files']} 份日志 → {data['out']}")
    elif data.get("dry_run"):
        lines.append("预演：" + data["plan"].get("action", ""))
    else:
        lines.append(json.dumps(data, ensure_ascii=False))
    for warning in warnings:
        lines.append(f"注意：{warning}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:          # argparse 自己会打用法并退出；--help 是 0
        return int(exc.code) if exc.code is not None else EXIT_USAGE

    _prepare_config_dir(args.config_dir)
    command = args.command
    action = getattr(args, "action", "") or ""
    if command == "config" and action in ("list", "add", "remove"):
        command = "config dirs"
    elif action:
        command = f"{command} {action}"
    try:
        data, warnings = args.func(args)
    except CliError as exc:
        payload = {"schema": SCHEMA, "ok": False, "command": command,
                   "error": {"code": exc.code, "message": str(exc)}}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"错误（{exc.code}）：{exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:           # noqa: BLE001 - 未预期异常也要给机器可读的结果
        payload = {"schema": SCHEMA, "ok": False, "command": command,
                   "error": {"code": "internal", "message": str(exc)}}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"错误（internal）：{exc}", file=sys.stderr)
        return EXIT_FAILURE

    payload = {"schema": SCHEMA, "ok": True, "command": command,
               "data": data, "warnings": warnings}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(_text(command, data, warnings))
    if args.command == "doctor" and data["blocked"]:
        return EXIT_PRECONDITION
    return EXIT_OK
