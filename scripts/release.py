"""发布准备：更新版本号、生成 CHANGELOG 条目、可选更新 Release 前导文本。

只改本地文件，绝不 push / 打 tag / 触发 CI 发布——发布时机由人决定。
默认连 git commit 也不做，方便先 review；确认后加 --commit。

用法：
    python scripts/release.py --version 1.0.1
    python scripts/release.py --version 1.0.1 --intro "本次下载说明…"
    python scripts/release.py --version 1.0.1 --intro-file notes.md
    python scripts/release.py --version 1.0.1 --contributors "@IsSmallPigPig"
    python scripts/release.py --version 1.0.1 --dry-run          # 只预览，不写文件

CHANGELOG 条目从 git log 提取：取 --since 指定 tag（默认最近 tag）到 HEAD 之间
的 feat / fix 提交，每行格式为 `- [短hash] <commit subject>`。commit subject 本身
就是 `feat(模块): 描述` / `fix: 描述` 的 conventional 格式，直接沿用，保证简洁严谨。
文末按 --contributors 列贡献者；不指定则用这些提交的 author 去重生成。

前导文本写进 docs/release-intro.md，由 .github/workflows/release.yml 读取并贴在
CHANGELOG 之前。--intro / --intro-file 都不给时保留原文件，即沿用上一版。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "app" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
INTRO_FILE = ROOT / "docs" / "release-intro.md"

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
# conventional commit 前缀：feat / fix，允许 (scope) 与 ! 破坏性标记
COMMIT_RE = re.compile(r"^(feat|fix)(\([^)]+\))?!?:")
FIELD = "\x1f"                       # git log 字段分隔符，正文几乎不会出现


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def latest_tag() -> str:
    return git("describe", "--tags", "--abbrev=0")


def collect_commits(since: str, types: tuple[str, ...]) -> list[tuple[str, str, str]]:
    """返回 [(短hash, author, subject)]，仅保留指定类型的 conventional commit。"""
    out = git("log", f"{since}..HEAD", f"--format=%h{FIELD}%an{FIELD}%s")
    result = []
    for line in out.splitlines():
        if not line:
            continue
        parts = line.split(FIELD)
        if len(parts) != 3:
            continue
        sha, author, subject = parts
        prefix = subject.split(":", 1)[0]
        kind = re.split(r"[\(!]", prefix, maxsplit=1)[0]
        if kind in types:
            result.append((sha, author, subject))
    return result


def render_block(version: str, commits: list[tuple[str, str, str]],
                 contributors: list[str]) -> str:
    """生成版本段落：标题 + 带编号的条目 + 文末 @ 贡献者。"""
    lines = [f"## v{version}", ""]
    lines += [f"- [{sha}] {subject}" for sha, _author, subject in commits]
    lines += ["", "@" + " @".join(contributors), ""]
    return "\n".join(lines)


def prepare_release(
    version: str,
    *,
    since: str | None = None,
    intro: str | None = None,
    intro_file: Path | None = None,
    contributors: list[str] | None = None,
    include_types: tuple[str, ...] = ("feat", "fix"),
    dry_run: bool = False,
) -> str:
    """改文件内容，返回变更摘要。dry_run 时只算不写。"""
    if not VERSION_RE.match(version):
        raise ValueError(f"版本号须为 X.Y.Z 形式：{version!r}")

    since = since or latest_tag()
    commits = collect_commits(since, include_types)
    if not commits:
        raise ValueError(f"{since}..HEAD 之间没有 feat/fix 提交，无需发布")

    if contributors is None:
        contributors = list(dict.fromkeys(author for _s, author, _c in commits))

    # 版本号 + CHANGELOG
    version_src = VERSION_FILE.read_text(encoding="utf-8")
    new_version_src = re.sub(
        r'__version__\s*=\s*"[^"]+"', f'__version__ = "{version}"', version_src, count=1)
    if new_version_src == version_src:
        raise ValueError(f"在 {VERSION_FILE} 里没找到 __version__ 赋值")

    block = render_block(version, commits, contributors)
    changelog_src = CHANGELOG.read_text(encoding="utf-8")
    marker = "## v"
    if marker not in changelog_src:
        raise ValueError(f"{CHANGELOG} 里没有版本段落")
    pos = changelog_src.index(marker)
    new_changelog = changelog_src[:pos] + block + "\n" + changelog_src[pos:]

    # 前导文本：给了才写，否则保持原文件（沿用上一版）
    intro_text = None
    if intro_file is not None:
        intro_text = intro_file.read_text(encoding="utf-8")
    elif intro is not None:
        intro_text = intro.rstrip("\n") + "\n"

    old_version = re.search('"([^"]+)"', version_src).group(1)
    who = " ".join("@" + c for c in contributors)
    intro_status = "更新" if intro_text else "沿用上一版"
    summary = (
        f"版本 {version}（原 {old_version}）\n"
        f"范围 {since}..HEAD，{len(commits)} 条 feat/fix\n"
        f"贡献者 {who}\n"
        f"前导文本：{intro_status}\n\n"
        + block
    )

    if not dry_run:
        VERSION_FILE.write_text(new_version_src, encoding="utf-8")
        CHANGELOG.write_text(new_changelog, encoding="utf-8")
        if intro_text is not None:
            INTRO_FILE.write_text(intro_text, encoding="utf-8")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="发布准备（只改本地文件，不发布）")
    ap.add_argument("--version", required=True, help="新版本号 X.Y.Z")
    ap.add_argument("--since", help="起始 tag，默认最近 tag")
    ap.add_argument("--intro", help="Release 前导文本；不写则沿用上一版")
    ap.add_argument("--intro-file", type=Path, help="从文件读前导文本（覆盖 --intro）")
    ap.add_argument("--contributors", help="文末 @ 的人，空格分隔；默认取提交作者")
    ap.add_argument("--include-types", default="feat,fix",
                    help="收录的 commit 类型，逗号分隔（默认 feat,fix）")
    ap.add_argument("--dry-run", action="store_true", help="只预览不写文件")
    args = ap.parse_args()

    try:
        summary = prepare_release(
            args.version,
            since=args.since,
            intro=args.intro,
            intro_file=args.intro_file,
            contributors=(args.contributors.split() if args.contributors else None),
            include_types=tuple(t.strip() for t in args.include_types.split(",") if t.strip()),
            dry_run=args.dry_run,
        )
    except (ValueError, subprocess.CalledProcessError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1

    print(summary)
    if args.dry_run:
        print("\n（dry-run，未写文件）")
    else:
        print("\n文件已更新。检查 git diff 后自行 commit / push；发布需另行打 tag。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
