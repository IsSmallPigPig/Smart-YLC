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
