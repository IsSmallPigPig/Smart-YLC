"""命令行入口：不开界面跑档案检查，给脚本与 AI 用。

稳定契约（改动要同步 README 与 tests/test_cli.py）：

* 每条命令都返回一个 JSON 对象，`--json` 时写到标准输出，别的内容不混进去：
  `{"schema": 1, "ok": true, "command": "scan", "data": {...}, "warnings": []}`
  失败时是 `{"schema": 1, "ok": false, "command": ..., "error": {"code", "message"}}`。
* 退出码：0 成功，2 参数错误，3 前置条件不满足（目录不在、配置读不出），
  4 运行期失败。
* 默认只读：检查不改名、不压缩、不写缓存、不碰网络。要动的操作各自需要显式开关，
  目前还没有会写的命令。
* 输出里的路径一律绝对路径；列表按路径排序，同样的输入得到同样的输出。
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

    statuses = inspect(
        files, expected, accounts, roster,
        auto_fix=False,                      # 只读：改名留给界面
        counter=pdf_page_count,              # 不写页数缓存
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
            "branch": status.branch,
            "account": status.account,
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
        "counts": counts,
        "branches": sorted(groups.values(), key=lambda g: (-g["files"], g["branch"])),
        "files": rows,
        "skipped": sorted(skipped, key=lambda r: r["path"]),
    }
    return data, warnings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py", description="智慧团委命令行：无界面跑检查，给脚本与 AI 用。")
    parser.add_argument("--json", action="store_true", help="输出 JSON（给程序读）")
    parser.add_argument("--config-dir", help="用指定目录的配置，不读用户配置")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="版本、字体与配置路径").set_defaults(func=cmd_version)
    sub.add_parser("doctor", help="环境自检").set_defaults(func=cmd_doctor)

    scan = sub.add_parser("scan", help="检查来源目录里的档案（只读）")
    scan.add_argument("dirs", nargs="+", help="要检查的目录，可给多个")
    scan.add_argument("--expected-pages", type=int, default=None,
                      help="预期页数；不给就用配置里的值")
    scan.add_argument("--roster", help="名单 xlsx；不给就用配置里当前学期的")
    scan.set_defaults(func=cmd_scan)
    return parser


def _text(command: str, data: dict, warnings: list[str]) -> str:
    lines: list[str] = []
    if command == "version":
        lines.append(f"版本 {data['version']}（Python {data['python']}）")
        lines.append(f"字体 {data['font']['latin']} + {data['font']['cjk']}"
                     f"{'' if data['font']['bundled'] else '（文件缺失）'}")
        lines.append(f"配置 {data['config_file']}")
    elif command == "doctor":
        for check in data["checks"]:
            mark = "OK  " if check["ok"] else ("FAIL" if check["level"] == "error" else "WARN")
            lines.append(f"[{mark}] {check['id']}: {check['detail']}")
    elif command == "scan":
        counts = data["counts"]
        lines.append(f"共 {counts['total']} 份：正常 {counts['ok']}，已纠正 {counts['fixed']}，"
                     f"缺团员 {counts['missing']}，异常 {counts['error']}，"
                     f"重复 {counts['duplicate']}，已有档案 {counts['archive']}，"
                     f"未纳入 {counts['skipped']}")
        for group in data["branches"]:
            lines.append(f"  {group['branch']}（{group['account']}）{group['files']} 份")
        for row in data["files"]:
            if row["usable"] and row["naming"] == "ok" and not row["problem"]:
                continue
            lines.append(f"  [{row['status']}] {row['path']} {row['problem']}".rstrip())
        for item in data["skipped"]:
            lines.append(f"  [未纳入] {item['path']} {item['reason']}")
    for warning in warnings:
        lines.append(f"注意：{warning}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:          # argparse 自己会打用法并退出 2
        return int(exc.code) if exc.code else EXIT_USAGE

    _prepare_config_dir(args.config_dir)
    try:
        data, warnings = args.func(args)
    except CliError as exc:
        payload = {"schema": SCHEMA, "ok": False, "command": args.command,
                   "error": {"code": exc.code, "message": str(exc)}}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"错误（{exc.code}）：{exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:           # noqa: BLE001 - 未预期异常也要给机器可读的结果
        payload = {"schema": SCHEMA, "ok": False, "command": args.command,
                   "error": {"code": "internal", "message": str(exc)}}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"错误（internal）：{exc}", file=sys.stderr)
        return EXIT_FAILURE

    if args.json:
        print(json.dumps({"schema": SCHEMA, "ok": True, "command": args.command,
                          "data": data, "warnings": warnings}, ensure_ascii=False))
    else:
        print(_text(args.command, data, warnings))
    if args.command == "doctor" and data["blocked"]:
        return EXIT_PRECONDITION
    return EXIT_OK
