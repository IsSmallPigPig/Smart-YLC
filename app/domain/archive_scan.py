"""来源目录扫描：递归找出档案 PDF，并把与档案无关的文件挡在外面。

来源目录允许是「整个年级」这种带层次的文件夹，所以扫描要进子目录。子目录里
通常混着通知、名单、公示这类 PDF，它们没法从文件名解析出「姓名+手机号+入团志愿书」，
判定为无关文件、不进上传计划——但不能悄悄丢掉：`FolderScan.ignored` 会把它们
留给界面列出来，让人看得见「这 3 个 PDF 因为命名不是档案格式被排除了」。

纯函数：只读磁盘，不改名、不压缩。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.domain import work_area
from app.domain.naming import analyze_file_name

# 系统/编辑器留下的垃圾目录，进不进都一样拖慢扫描
SKIP_DIRS = {"__MACOSX", ".git", ".idea", ".vscode", "node_modules"}

ABNORMAL_DIR_NAME = work_area.ABNORMAL_DIR_NAME
LEGACY_ABNORMAL_DIR_NAME = "异常档案"
"""升级前原件被移进的旧目录名；新异常区在工作目录/异常下，见 work_area。"""


def is_abnormal_path(path: str) -> bool:
    """路径是否落在异常区：新的「工作目录/异常」或旧的「异常档案」都算。

    原件现在永不移动，异常标记改搬工作副本；保留旧判定是为了让升级前
    已经移进「异常档案」的档案仍被识别为异常，不会误传。"""
    parts = os.path.normpath(path).split(os.sep)
    return (work_area.is_abnormal_copy(path)
            or ABNORMAL_DIR_NAME in parts
            or LEGACY_ABNORMAL_DIR_NAME in parts)


@dataclass
class FolderScan:
    root: str
    files: list[str] = field(default_factory=list)    # 命名可解析：参与检查与上传
    ignored: list[str] = field(default_factory=list)  # 命名不是档案格式：排除
    missing: bool = False                             # 目录不在了（拔盘、改名、移走）

    @property
    def name(self) -> str:
        """文件夹名：标题用它，路径用完整路径。"""
        trimmed = self.root.rstrip(os.sep)
        return os.path.basename(trimmed) or trimmed or self.root

    @property
    def total(self) -> int:
        return len(self.files)


def _skip_dir(name: str) -> bool:
    return name.startswith(".") or name in SKIP_DIRS


def scan_folder(root: str) -> FolderScan:
    """递归扫描一个来源目录，按文件名把档案与无关文件分开。"""
    if not os.path.isdir(root):
        return FolderScan(root=root, missing=True)

    files: list[str] = []
    ignored: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 「工作目录」里全是本程序生成的副本：原件已经在上面几层，扫进去
        # 会把一份档案算两遍，越扫越多。整棵子树跳过。
        dirnames[:] = sorted(d for d in dirnames
                             if not _skip_dir(d) and d != work_area.WORK_DIR_NAME)
        for name in sorted(filenames):
            if name.startswith(".") or name.startswith("~$"):
                continue
            if not name.lower().endswith(".pdf"):
                continue
            path = os.path.join(dirpath, name)
            if analyze_file_name(name).parsed:
                files.append(path)
            else:
                ignored.append(path)
    return FolderScan(root=root, files=files, ignored=ignored)


def scan_folders(roots: list[str]) -> list[FolderScan]:
    return [scan_folder(root) for root in roots]


def relative_to(root: str, path: str) -> str:
    """文件在来源目录里的相对位置：「高一1班/张三....pdf」；不在目录里就返回文件名。"""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return os.path.basename(path)
    return os.path.basename(path) if rel.startswith("..") else rel


def abnormal_target(path: str) -> str:
    """把档案移进「异常档案」后的目标路径：就地放——它现在在哪个文件夹，
    异常文件夹就在哪个文件夹下（高一1班/张三.pdf → 高一1班/异常档案/张三.pdf）。"""
    folder = os.path.dirname(path)
    return os.path.join(folder, ABNORMAL_DIR_NAME, os.path.basename(path))


def normal_target(path: str) -> str:
    """把档案移出「异常档案」后的目标路径：回到异常文件夹的上一层。
    同名已存在时加 (2)(3)…，与 phone_sync 的落盘规则一致。"""
    abnormal_folder = os.path.dirname(path)
    folder = os.path.dirname(abnormal_folder)
    base, ext = os.path.splitext(os.path.basename(path))
    target = os.path.join(folder, base + ext)
    n = 2
    while os.path.exists(target):
        target = os.path.join(folder, f"{base}({n}){ext}")
        n += 1
    return target
