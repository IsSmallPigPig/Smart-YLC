"""命令行的对外契约：JSON 形状、退出码、只读。

AI 与脚本按这些字段做判断，字段一改就得在这里同步；顺带锁住「检查不改名、
不写缓存」，避免有人把界面的自动纠正顺手带进 CLI。
"""

from __future__ import annotations

import json
import os

import pytest


def run(capsys, argv):
    from app.cli import main

    code = main(argv)
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip().startswith("{") else None), out


def run_json(argv):
    """不走 pytest capsys：用 redirect_stdout 拿到这一组参数对应的 JSON。"""
    import contextlib
    import io

    from app.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(argv)
    return code, json.loads(buf.getvalue()), buf.getvalue()


@pytest.fixture()
def pdf(tmp_path):
    import fitz

    def make(name: str, pages: int = 1):
        doc = fitz.open()
        for _ in range(pages):
            doc.new_page()
        path = tmp_path / name
        doc.save(str(path))
        doc.close()
        return path

    return make


def test_version_is_json_contract(capsys):
    code, payload, _ = run(capsys, ["--json", "version"])
    assert code == 0
    assert payload["schema"] == 1 and payload["ok"] is True
    assert payload["command"] == "version"
    assert payload["data"]["font"]["latin"] == "Allo"
    assert payload["data"]["font"]["cjk"] == "MiSans"


def test_scan_reports_counts_and_keeps_names(capsys, pdf, tmp_path):
    """检查只读：不规范的文件名只报告不改名，无法解析的 PDF 单独列出。"""
    good = pdf("张三+13800000001+入团志愿书.pdf")
    dashed = pdf("李四-13900000002-入团志愿书.pdf")
    odd = pdf("通知.pdf")

    code, payload, _ = run(
        capsys, ["--json", "scan", str(tmp_path), "--expected-pages", "1"])
    assert code == 0
    data = payload["data"]
    assert data["counts"]["total"] == 2
    assert data["counts"]["skipped"] == 1
    dashed_row = next(r for r in data["files"] if r["name"].startswith("李四"))
    assert dashed_row["naming"] == "fixed", "分隔符不规范应报「已纠正」，但不落盘"
    assert dashed.exists() and dashed.name == "李四-13900000002-入团志愿书.pdf"
    assert data["skipped"][0]["path"].endswith("通知.pdf")
    assert good.exists() and odd.exists()


def test_scan_missing_dir_exits_3(capsys, tmp_path):
    missing = tmp_path / "not-there"
    code, payload, _ = run(capsys, ["--json", "scan", str(missing)])
    assert code == 3
    assert payload["ok"] is False
    assert payload["error"]["code"] == "missing_dir"


def test_scan_marks_page_mismatch(capsys, pdf, tmp_path):
    pdf("李四+13900000002+入团志愿书.pdf", pages=2)
    code, payload, _ = run(
        capsys, ["--json", "scan", str(tmp_path), "--expected-pages", "13"])
    assert code == 0
    row = payload["data"]["files"][0]
    assert row["page"] == "error"
    assert "页" in row["problem"]
    assert row["usable"] is False


def test_doctor_flags_missing_accounts(capsys, tmp_path, monkeypatch):
    from app.services import store

    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(store, "CONFIG_FILE", str(cfg))

    code, payload, _ = run(capsys, ["--json", "doctor"])
    checks = {c["id"]: c for c in payload["data"]["checks"]}
    assert checks["accounts"]["ok"] is False
    assert code == 3
    assert payload["data"]["blocked"] is True


def test_json_stdout_is_pure(tmp_path):
    """真进程里跑一次：标准输出必须只有 JSON，没有组件库横幅之类的杂物。

    `--json` 的意义就是能被 json.loads 直接吃下；一旦有人把带欢迎横幅的模块
    拉进 import 链，这里立刻红，而不是等调用方的解析器崩在用户那边。
    """
    import subprocess
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [sys.executable, os.path.join(root, "cli.py"), "--json", "version"],
        capture_output=True, text=True, timeout=60, cwd=root)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True


# ---------- 扩展后的命令树 ----------

@pytest.fixture()
def cfg_file(tmp_path, monkeypatch):
    """把配置指到临时目录，避免测试碰真实账号密码。"""
    from app.services import store

    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(store, "CONFIG_FILE", str(path))
    return path


@pytest.fixture()
def fake_accounts(cfg_file):
    from app.services import store

    store.update(lambda cfg: cfg.update({
        "accounts": [
            {"userName": "ssfz12401", "password": "p1", "remark": "班一"},
            {"userName": "ssfz12402", "password": "p2", "remark": "班二"},
        ],
        "activeUser": "acc1",
        "expectedPages": 1,
    }))
    return cfg_file


def test_every_readonly_command_emits_pure_json(tmp_path):
    """所有只读命令都得能在真进程里被 json.loads 直接吃下。"""
    import subprocess
    import sys

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    commands = [
        ["version"], ["doctor"], ["rule", "show"], ["rule", "explain", "12407"],
        ["config", "get"], ["config", "dirs", "list"], ["backup", "list"],
        ["pair", "list"], ["org", "stats"], ["messages", "list"],
        ["messages", "list", "--unread"], ["autostart", "status"],
    ]
    for argv in commands:
        proc = subprocess.run(
            [sys.executable, os.path.join(root, "cli.py"), "--json", *argv],
            capture_output=True, text=True, timeout=60, cwd=root,
            env={**os.environ, "HOME": str(tmp_path)})
        payload = json.loads(proc.stdout)
        assert payload["ok"] is True, f"{argv}: {payload}"
        assert payload["command"] in (" ".join(argv[:1]), " ".join(argv[:2])), \
            f"{argv}: {payload['command']}"
        assert payload["schema"] == 1 and payload["warnings"] == []


def test_write_commands_default_to_dry_run(fake_accounts):
    """写操作不加 --yes 不能动盘：脚本先看到计划，再决定是否确认。"""
    cfg_file = fake_accounts
    before = cfg_file.read_text(encoding="utf-8")
    cases = [
        (["config", "set", "expectedPages", "3"], "config set"),
        (["config", "export", str(cfg_file.parent / "out.json")], "config export"),
        (["messages", "read", "--all"], "messages read"),
        (["org", "sync"], "org sync"),
        (["messages", "pull"], "messages pull"),
        (["upload", str(cfg_file.parent)], "upload"),
        (["cache", "clear"], "cache clear"),
        (["pair", "forget", "手机"], "pair forget"),
        (["roster", "template", "--out", str(cfg_file.parent / "t.xlsx")], "roster template"),
    ]
    for argv, expected in cases:
        code, data, _ = run_json(["--json", *argv])
        assert code == 0, argv
        assert data["command"] == expected
        assert data["data"]["dry_run"] is True, argv
    assert cfg_file.read_text(encoding="utf-8") == before


def test_config_set_yes_persists_json_value(cfg_file):
    code, payload, _ = run_json(["--json", "config", "set", "expectedPages", "7", "--yes"])
    assert code == 0 and payload["data"]["set"] == "expectedPages"
    from app.services import store

    assert store.load()["expectedPages"] == 7


def test_config_export_import_roundtrip(cfg_file, tmp_path, monkeypatch):
    from app.services import store

    store.update(lambda cfg: cfg.update({"accounts": [{"userName": "u", "password": "p"}],
                                         "expectedPages": 5}))
    out = tmp_path / "backup.json"
    code, payload, _ = run_json(["--json", "config", "export", str(out), "--yes"])
    assert code == 0 and payload["data"]["accounts"] == 1
    assert json.loads(out.read_text(encoding="utf-8"))["expectedPages"] == 5

    store.update(lambda cfg: cfg.update({"expectedPages": 99}))
    code, payload, _ = run_json(["--json", "config", "import", str(out), "--yes"])
    assert code == 0 and payload["data"]["imported"] is True
    assert store.load()["expectedPages"] == 5
    assert os.path.isfile(payload["data"]["backup"])


def test_config_import_rejects_non_backup(cfg_file, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"hello": 1}', encoding="utf-8")
    code, payload, _ = run_json(["--json", "config", "import", str(bad), "--yes"])
    assert code == 3 and payload["error"]["code"] == "bad_backup"


def test_scan_fix_requires_yes(cfg_file, pdf, tmp_path):
    p = pdf("李四-13900000002-入团志愿书.pdf")
    code, payload, _ = run_json(["--json", "scan", str(tmp_path), "--fix"])
    assert code == 3 and payload["error"]["code"] == "needs_yes"
    assert p.exists() and p.name == "李四-13900000002-入团志愿书.pdf"

    code, payload, _ = run_json(["--json", "scan", str(tmp_path), "--fix", "--yes",
                                 "--expected-pages", "1"])
    assert code == 0
    assert payload["data"]["renamed"], payload
    assert (tmp_path / "李四+13900000002+入团志愿书.pdf").exists()


class FakeClient:
    """headless 的假站点：只实现被 CLI 调用到的那几个方法。"""

    def __init__(self, user="", password=""):
        self.user = user
        self.uploaded: list[tuple[str, str]] = []
        self.audited: list[str] = []

    def session_account(self):
        from app.services.api import ApiResult
        return ApiResult(True, "会话有效")

    def get_all_members(self):
        from app.domain.check_rules import MemberRecord
        m = MemberRecord(member_id="1001", name="张三", mobile="13800000001",
                         oid="o1", report_data_status="待审核", ebook_status="未上传")
        return [m], []

    def get_messages_page(self, page_no=1, page_size=100, msg_type=0):
        if page_no > 1:
            return {"listData": []}
        return {"listData": [{
            "receiveId": 5001, "title": "[系统通知][报道认证审核提醒]",
            "content": "<p>张三 13800000001</p>", "status": 0,
            "statusStr": "未读", "sendTime": "2026-11-23 10:00:00",
        }]}

    def get_org_tree(self):
        return [{"oid": "o1", "orgName": "班一", "parentOid": ""}], []

    def upload_file(self, path, progress=None):
        from app.services.api import ApiResult
        self.uploaded.append(path)
        return ApiResult(True, "已上传")

    def audit_voluntary_book(self, mid, status=2, refuse=""):
        from app.services.api import ApiResult
        self.audited.append(mid)
        return ApiResult(True, "通过")


@pytest.fixture()
def fake_site(monkeypatch):
    """把 headless 的站点与登录闸门换成假的，不发任何网络请求。"""
    from app.services import headless, login_gate

    clients: list[FakeClient] = []

    def make_client(user="", password=""):
        c = FakeClient(user, password)
        clients.append(c)
        return c

    from app.services import api

    monkeypatch.setattr(headless, "SiteClient", make_client)
    monkeypatch.setattr(api, "SiteClient", make_client)
    monkeypatch.setattr(login_gate, "ensure_logged_in",
                        lambda client, allow_login=True: ("ok", "会话有效"))
    from app.domain.transfer import TransferSummary
    monkeypatch.setattr(headless, "fetch_transfer_applies",
                        lambda *a, **k: TransferSummary())
    return clients


def test_login_probe_persists_health(fake_accounts, fake_site):
    code, payload, _ = run_json(["--json", "login", "probe", "--yes"])
    assert code == 0 and payload["data"]["persisted"] is True
    assert payload["data"]["counts"]["ok"] == 2
    from app.services import store

    for acc in store.load()["accounts"]:
        assert acc["health"]["status"] == "ok"
        assert acc["health"]["checkedAt"]


def test_org_sync_writes_member_cache(fake_accounts, fake_site):
    code, payload, _ = run_json(["--json", "org", "sync", "--yes"])
    assert code == 0
    assert payload["data"]["counts"]["members"] == 2
    from app.services import store

    cache = store.load()["memberCache"]
    assert set(cache) == {"ssfz12401", "ssfz12402"}
    assert cache["ssfz12401"]["members"][0]["name"] == "张三"
    assert "fetchedAt" in cache["ssfz12401"]


def test_messages_pull_and_read(fake_accounts, fake_site):
    code, payload, _ = run_json(["--json", "messages", "pull", "--yes"])
    assert code == 0 and payload["data"]["counts"]["ok"] == 2
    code, payload, _ = run_json(["--json", "messages", "list", "--unread"])
    assert code == 0 and len(payload["data"]["messages"]) == 2
    key = payload["data"]["messages"][0]["key"]

    code, payload, _ = run_json(["--json", "messages", "read", key, "--yes"])
    assert code == 0 and payload["data"]["marked"] == 1
    code, payload, _ = run_json(["--json", "messages", "list", "--unread"])
    assert payload["data"]["total"] == 1


def test_org_stats_online_persists_cache(fake_accounts, fake_site):
    code, payload, _ = run_json(["--json", "org", "stats", "--online", "--yes"])
    assert code == 0 and payload["data"]["persisted"] == 2
    row = payload["data"]["accounts"][0]
    assert row["total"] == 1 and row["status"] == "ok"
    from app.services import store

    cache = store.load()["orgStatsCache"]
    assert len(cache) == 2
    assert "error_rows" in cache["ssfz12401"] and "transfer" in cache["ssfz12401"]


def test_org_tree_requires_unique_account(fake_accounts, fake_site):
    code, payload, _ = run_json(["--json", "org", "tree", "班一"])
    assert code == 0 and len(payload["data"]["nodes"]) == 1
    code, payload, _ = run_json(["--json", "org", "tree", "ssfz"])
    assert code == 3 and payload["error"]["code"] == "ambiguous_account"


def test_upload_plan_and_execute(fake_accounts, fake_site, pdf, tmp_path, monkeypatch):
    """上传前预演只读；--yes 走完整的检查→压缩副本→提交→审核。"""
    from app.services import prepare

    # 只验证上传链路：压缩副本的落盘行为交给 prepare 自己的测试覆盖
    def fake_prepare(statuses, roots, *a, **kw):
        for s in statuses:
            if s.usable:
                s.work_path = s.path
        return statuses

    monkeypatch.setattr(prepare, "prepare_all", fake_prepare)
    # 路由要真名单：班级 12401 → 账号 ssfz12401
    from openpyxl import Workbook
    from app.services import store
    roster = tmp_path / "名单.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["班级", "姓名", "手机号"])
    ws.append(["12401", "张三", "13800000001"])
    wb.save(str(roster))
    store.update(lambda cfg: cfg.update({"rosters": {"测试": str(roster)},
                                         "rosterSemester": "测试"}))
    good = pdf("张三+13800000001+入团志愿书.pdf", pages=1)
    code, payload, _ = run_json(["--json", "upload", str(tmp_path)])
    assert code == 0 and payload["data"]["dry_run"] is True
    assert good.exists()

    code, payload, _ = run_json(["--json", "upload", str(tmp_path), "--yes"])
    assert code == 0 and payload["data"]["summary"]["ok"] == 1, payload
    assert payload["data"]["summary"]["audit_ok"] == 1
    assert fake_site and fake_site[0].uploaded


def test_cells_and_cache_clear(cfg_file, monkeypatch, tmp_path):
    from app.services import check_cache, form_cells

    monkeypatch.setattr(form_cells, "check", lambda path, max_pages=None:
                        type("R", (), {"pages": 2, "checked_pages": [1, 2],
                                       "blanks": [], "skipped": "",
                                       "summary": lambda self: "表格未发现空格"})())
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"pdf")
    code, payload, _ = run_json(["--json", "cells", str(pdf)])
    assert code == 0 and payload["data"]["counts"]["clean"] == 1

    cache = tmp_path / "check_cache.json"
    cache.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(check_cache, "CACHE_FILE", str(cache))
    code, payload, _ = run_json(["--json", "cache", "clear", "--yes"])
    assert code == 0 and payload["data"]["cleared"] is True
    assert not cache.exists()


def test_backup_list_push_pull(cfg_file, tmp_path, monkeypatch):
    from app.services import cloud_backup, store

    profile = {"name": "本机", "server": "https://example.com",
               "code": "ABC234", "ownerKey": "k" * 64}
    store.update(lambda cfg: cfg.update({"cloudProfiles": [profile]}))
    written: list[dict] = []
    monkeypatch.setattr(cloud_backup, "upload",
                        lambda server, code, envelope, owner_key: written.append(envelope))
    monkeypatch.setattr(cloud_backup, "probe", lambda server, code: True)
    monkeypatch.setattr(cloud_backup, "download",
                        lambda server, code: {"ciphertext": "x"})
    monkeypatch.setattr(cloud_backup, "decrypt_config",
                        lambda envelope, code, password: {"accounts": [{"userName": "u"}]})

    code, payload, _ = run_json(["--json", "backup", "list"])
    assert code == 0 and payload["data"]["profiles"][0]["owned"] is True
    code, payload, _ = run_json(["--json", "backup", "probe"])
    assert payload["data"]["exists"] is True

    code, payload, _ = run_json(["--json", "backup", "push", "--password", "1234"])
    assert code == 0 and payload["data"]["dry_run"] is True
    code, payload, _ = run_json(["--json", "backup", "push", "--password", "1234", "--yes"])
    assert code == 0 and written

    out = tmp_path / "cloud.json"
    code, payload, _ = run_json(["--json", "backup", "pull", "--out", str(out), "--yes"])
    assert code == 0 and json.loads(out.read_text(encoding="utf-8"))["accounts"]


def test_pair_serve_dry_run_and_forget(cfg_file, tmp_path, monkeypatch):
    from app.services import paired_devices

    paired_file = tmp_path / "paired.json"
    monkeypatch.setattr(paired_devices, "PAIRED_FILE", str(paired_file))
    monkeypatch.setattr(paired_devices, "_load_raw",
                        lambda: json.loads(paired_file.read_text(encoding="utf-8"))
                        if paired_file.exists() else [])
    monkeypatch.setattr(paired_devices, "_save_raw",
                        lambda items: paired_file.write_text(
                            json.dumps([i.to_dict() if hasattr(i, "to_dict") else i
                                        for i in items], ensure_ascii=False),
                            encoding="utf-8"))
    paired_devices.remember("手机A", "192.168.1.2")
    code, payload, _ = run_json(["--json", "pair", "list"])
    assert code == 0 and payload["data"]["devices"][0]["name"] == "手机A"

    code, payload, _ = run_json(["--json", "pair", "forget", "手机A"])
    assert code == 0 and payload["data"]["dry_run"] is True
    assert paired_devices.list_devices()
    code, payload, _ = run_json(["--json", "pair", "forget", "手机A", "--host", "192.168.1.2", "--yes"])
    assert code == 0 and not paired_devices.list_devices()


def test_roster_import_and_secretary_import(cfg_file, tmp_path):
    from app.services import store
    from openpyxl import Workbook

    roster = tmp_path / "2026秋.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["班级", "姓名", "手机号"])
    ws.append(["12401", "张三", "13800000001"])
    wb.save(str(roster))

    code, payload, _ = run_json(["--json", "roster", "import", str(roster), "--yes"])
    assert code == 0 and payload["data"]["rows"] == 1
    assert store.load()["rosters"]["2026秋"] == str(roster)
    code, payload, _ = run_json(["--json", "roster", "use", "2026秋", "--yes"])
    assert store.load()["rosterSemester"] == "2026秋"
    code, payload, _ = run_json(["--json", "roster", "delete", "2026秋", "--yes"])
    assert "2026秋" not in store.load()["rosters"]

    sec = tmp_path / "团支书.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["支部", "团支书", "手机号"])
    ws.append(["12401", "李四", "13900000002"])
    wb.save(str(sec))
    code, payload, _ = run_json(["--json", "secretary", "import", str(sec), "--yes"])
    assert code == 0 and payload["data"]["branches"] == 1


def test_templates_export_and_logs(cfg_file, tmp_path):
    from app.domain.org_stats import ErrorMemberRow
    from app.services import store

    out = tmp_path / "template.xlsx"
    code, payload, _ = run_json(["--json", "roster", "template", "--out", str(out), "--yes"])
    assert code == 0 and out.exists()
    out2 = tmp_path / "secretary.xlsx"
    code, payload, _ = run_json(["--json", "secretary", "template", "--out", str(out2), "--yes"])
    assert code == 0 and out2.exists()

    # export 需要成员缓存，没有时明确报失败，不静默出空文件
    code, payload, _ = run_json(["--json", "export", "--contents", "members",
                                 "--out", str(tmp_path / "e.xlsx"), "--yes"])
    assert code == 4 and payload["error"]["code"] == "export_failed"

    log = tmp_path / "logs.txt"
    code, payload, _ = run_json(["--json", "logs", "--out", str(log), "--yes"])
    assert code == 0 and payload["data"]["out"] == str(log) + ".log"


def test_autostart_update_announce_and_daily(cfg_file, monkeypatch):
    from app.services import announcements, autostart, daily, updater

    monkeypatch.setattr(autostart, "is_installed", lambda: False)
    monkeypatch.setattr(autostart, "install", lambda: "/tmp/agent.plist")
    monkeypatch.setattr(autostart, "uninstall", lambda: None)
    code, payload, _ = run_json(["--json", "autostart", "status"])
    assert payload["data"]["installed"] is False
    code, payload, _ = run_json(["--json", "autostart", "on", "--yes"])
    assert payload["data"]["installed"] is True

    monkeypatch.setattr(updater, "check_update",
                        lambda: updater.UpdateInfo("9.9.9", "新功能", "url", "a.dmg", "release"))
    monkeypatch.setattr(updater, "__version__", "1.0.0")
    code, payload, _ = run_json(["--json", "update", "check"])
    assert payload["data"]["available"] is True

    monkeypatch.setattr(announcements, "fetch_announcements",
                        lambda: [announcements.Announcement("id1", "标题", ("内容",), "2026-11-23")])
    monkeypatch.setattr(announcements, "seen_ids", lambda: set())
    monkeypatch.setattr(announcements, "pending", lambda items: items)
    code, payload, _ = run_json(["--json", "announce", "fetch"])
    assert payload["data"]["pending"] == 1

    monkeypatch.setattr(daily, "run_daily_check", lambda: (True, "检查完成"))
    code, payload, _ = run_json(["--json", "daily-check"])
    assert payload["data"]["check_ok"] is True


def test_config_dirs_add_remove(cfg_file, tmp_path):
    d = tmp_path / "来源"
    d.mkdir()
    code, payload, _ = run_json(["--json", "config", "dirs", "add", str(d)])
    assert payload["data"]["dry_run"] is True
    code, payload, _ = run_json(["--json", "config", "dirs", "add", str(d), "--yes"])
    assert str(d) in payload["data"]["dirs"]
    code, payload, _ = run_json(["--json", "config", "dirs", "remove", str(d), "--yes"])
    assert str(d) not in payload["data"]["dirs"]
