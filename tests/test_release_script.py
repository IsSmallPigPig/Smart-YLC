"""发布脚本：changelog 格式、版本号同步、前导文本沿用规则。

脚本只改本地文件、绝不 push / 打 tag，测试用临时目录跑，不碰真实仓库状态。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import scripts.release as R


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """造一份最小仓库：脚本用的三个文件路径都指到 tmp_path。"""
    app = tmp_path / "app"; app.mkdir()
    docs = tmp_path / "docs"; docs.mkdir()
    init = app / "__init__.py"; init.write_text('__version__ = "1.0.0"\n', encoding="utf-8")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# 变更日志\n\n说明行。\n\n## v1.0.0\n\n- [old] feat: 旧版\n\n@Someone\n",
        encoding="utf-8",
    )
    intro = docs / "release-intro.md"; intro.write_text("## 下载\n- 旧包\n", encoding="utf-8")
    monkeypatch.setattr(R, "VERSION_FILE", init)
    monkeypatch.setattr(R, "CHANGELOG", changelog)
    monkeypatch.setattr(R, "INTRO_FILE", intro)

    # 用假 git 历史，避免依赖真实 tag
    commits = [
        ("abc1234", "AnkoQAQ", "feat(ui): 新功能"),
        ("def5678", "Bob", "fix: 修问题"),
        ("skip000", "AnkoQAQ", "docs: 不算数"),
        ("aaa1111", "Bob", "feat: 另一功能"),
    ]
    def fake_git(*_args):
        return "\n".join(f"{h}\x1f{u}\x1f{s}" for h, u, s in commits)

    monkeypatch.setattr(R, "git", fake_git)
    return tmp_path, init, changelog, intro


def test_changelog_format_has_hash_feat_fix_and_contributors(repo):
    _root, init, changelog, intro = repo
    summary = R.prepare_release("1.0.1", since="v1.0.0")

    text = changelog.read_text(encoding="utf-8")
    assert "## v1.0.1" in text
    # 每条带 commit 短编号，且以 feat/fix 开头
    assert "- [abc1234] feat(ui): 新功能" in text
    assert "- [def5678] fix: 修问题" in text
    assert "- [aaa1111] feat: 另一功能" in text
    # docs 被排除
    assert "skip000" not in text
    # 贡献者去重、文末 @
    assert "@AnkoQAQ @Bob" in text
    assert init.read_text(encoding="utf-8").strip() == '__version__ = "1.0.1"'
    # 前导文本不写则沿用
    assert intro.read_text(encoding="utf-8") == "## 下载\n- 旧包\n"
    assert "沿用上一版" in summary


def test_intro_is_replaced_when_provided(repo, tmp_path):
    _root, _init, _changelog, intro = repo
    src = tmp_path / "new_intro.md"
    src.write_text("## 下载\n- 新包\n", encoding="utf-8")
    R.prepare_release("1.0.2", since="v1.0.0", intro_file=src)
    assert intro.read_text(encoding="utf-8") == "## 下载\n- 新包\n"


def test_contributors_override(repo):
    _root, _init, changelog, _intro = repo
    R.prepare_release("1.0.1", since="v1.0.0", contributors=["IsSmallPigPig"])
    assert "@IsSmallPigPig" in changelog.read_text(encoding="utf-8")
    assert "@AnkoQAQ" not in changelog.read_text(encoding="utf-8")


def test_invalid_version_rejected(repo):
    with pytest.raises(ValueError, match="X.Y.Z"):
        R.prepare_release("v1.0.1", since="v1.0.0")


def test_dry_run_writes_nothing(repo):
    _root, init, changelog, intro = repo
    before = (init.read_text(), changelog.read_text(), intro.read_text())
    R.prepare_release("1.0.1", since="v1.0.0", dry_run=True)
    assert (init.read_text(), changelog.read_text(), intro.read_text()) == before


def test_workflow_reads_intro_and_latest_version():
    """release body = docs/release-intro.md + 本版本段落。

    发版说明只列本版本的提交，历史版本留在仓库里备查；intro 仍从文件读，
    改文案不必动 workflow。
    """
    wf = (Path(__file__).resolve().parent.parent /
          ".github/workflows/release.yml").read_text(encoding="utf-8")
    block = wf.split("组合 Release 说明", 1)[1].split("校验主产物", 1)[0]
    assert "cat docs/release-intro.md" in block
    # 旧的硬编码 echo 已移除
    assert 'echo "## 下载"' not in block
    assert "awk '/^## v/{n++} n==1' CHANGELOG.md" in block
    assert "p=1" not in block


def test_release_body_only_latest_version():
    """按 workflow 的取法构造正文：只留第一个版本段落，更早的不进正文。"""
    root = Path(__file__).resolve().parent.parent
    lines = (root / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## v"))
    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("## v") and body:
            break
        body.append(line)
    # 文件里仍有历史版本，正文里只有一个
    assert sum(1 for line in lines[start:] if line.startswith("## v")) >= 2
    assert sum(1 for line in body if line.startswith("## v")) == 1
