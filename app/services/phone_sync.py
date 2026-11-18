"""手机同步落盘：把从备份站拉下来的 PDF 存到「用户目录/同步自手机/YYYY-MM-DD/」。

设计要点（用户明确要求）：
* 固定落点：~/同步自手机/<当日日期>/，不依赖工作目录配置，换机也能找到；
* 幂等：同名同大小同内容的文件重复同步不会落第二份；
* 自动入列：落盘后把路径写进 workspaceDirs（来源目录），上传页下一次扫描
  就会把它当成来源目录自动发现——不用用户手动添加；
* 不静默丢：命名不合规的 PDF 也会被保存（用原名），但记录到 rejected，
  让调用方显示「这几份文件名不对，需要改名」，不假装成功。

文件名解析规则复用 app.domain.naming.analyze_file_name，与上传检查一致。
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from datetime import date

from app.domain.naming import analyze_file_name

SYNC_FOLDER = "同步自手机"


@dataclass
class SyncResult:
    saved: list[str] = field(default_factory=list)      # 新落盘的文件
    duplicates: list[str] = field(default_factory=list)  # 内容相同已存在
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (文件名, 原因)
    directory: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.saved) and not self.rejected


def sync_dir(today: date | None = None) -> str:
    """返回当日的同步目录，不存在就创建。"""
    day = (today or date.today()).isoformat()
    path = os.path.join(os.path.expanduser("~"), SYNC_FOLDER, day)
    os.makedirs(path, exist_ok=True)
    return path


def _digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def receive_pdf(src: str, *, name: str = "", today: date | None = None,
                dest_dir: str | None = None) -> tuple[str | None, str]:
    """把一份收到的 PDF 落到当日同步目录。

    返回 (落盘路径或 None, 结果说明)。同名文件用 SHA-256 判重，内容相同则跳过。
    """
    if not os.path.isfile(src):
        return None, "源文件不存在"
    filename = name or os.path.basename(src)
    if not filename.lower().endswith(".pdf"):
        return None, "不是 PDF 文件"
    analysis = analyze_file_name(filename)
    if not analysis.parsed:
        # 命名不合规不允许静默改：保留原名落盘，由调用方提示用户改名
        pass

    target_dir = dest_dir or sync_dir(today)
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, filename)

    if os.path.exists(target):
        if os.path.getsize(target) == os.path.getsize(src) and _digest(target) == _digest(src):
            return target, "duplicate"
        # 同名不同内容：加序号，绝不覆盖已存在的档案
        stem, ext = os.path.splitext(filename)
        i = 2
        while os.path.exists(target):
            target = os.path.join(target_dir, f"{stem}({i}){ext}")
            i += 1

    shutil.copyfile(src, target)
    return target, ("ok" if analysis.parsed else "need_rename")


def register_source_dir(path: str, add_dir_fn=None) -> None:
    """把同步目录登记为来源目录，上传页扫描时自动发现。

    add_dir_fn 用于注入（测试/解耦），默认走 workspace.save_workspace_dirs。
    """
    if add_dir_fn is not None:
        add_dir_fn([path])
        return
    from app.services.workspace import load_workspace_dirs, save_workspace_dirs

    dirs = load_workspace_dirs()
    if path in dirs:
        return
    save_workspace_dirs(dirs + [path])


def receive_batch(sources: list[tuple[str, str]], *, today: date | None = None) -> SyncResult:
    """批量落盘。sources = [(源文件路径, 显示文件名), ...]。"""
    target = sync_dir(today)
    result = SyncResult(directory=target)
    for src, name in sources:
        path, status = receive_pdf(src, name=name, today=today, dest_dir=target)
        if path is None:
            result.rejected.append((name, status))
        elif status == "duplicate":
            result.duplicates.append(path)
        elif status == "need_rename":
            result.saved.append(path)
            result.rejected.append((name, "文件名不符合「姓名+手机号+入团志愿书.pdf」，请改名"))
        else:
            result.saved.append(path)
    if result.saved:
        register_source_dir(target)
    return result
