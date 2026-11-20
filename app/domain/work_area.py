"""工作目录：每个来源目录下一份「异常 / 压缩」的工作副本区。

设计（2026-11-20 预检查流程改造）：
  来源目录（用户选的工作区）里的原始档案只读，绝不改名、不移动、不删除。
  检查通过的文件生成副本落到「工作目录/压缩」，供逐格查空格与上传使用；
  被标记异常的把副本挪到「工作目录/异常」，取消标记再挪回压缩。

  每个来源目录各建一个工作目录（用户选择），互不混淆。扫描来源目录时
  「工作目录」整棵子树跳过——里面全是派生的副本，扫进去会重复计数。

纯路径计算，不碰磁盘。文件名沿用原件的标准名，同名不同班靠相对子目录区分
（工作目录/压缩/高一1班/张三...pdf），不会被压平后互相覆盖。
"""

from __future__ import annotations

import os

WORK_DIR_NAME = "工作目录"
ABNORMAL_DIR_NAME = "异常"
COMPRESSED_DIR_NAME = "压缩"


def work_dir(root: str) -> str:
    return os.path.join(root, WORK_DIR_NAME)


def abnormal_dir(root: str) -> str:
    return os.path.join(work_dir(root), ABNORMAL_DIR_NAME)


def compressed_dir(root: str) -> str:
    return os.path.join(work_dir(root), COMPRESSED_DIR_NAME)


def is_inside_work_dir(path: str) -> bool:
    """路径是否落在任意一层的工作目录下（扫描时跳过整棵子树）。"""
    return WORK_DIR_NAME in os.path.normpath(path).split(os.sep)


def is_abnormal_copy(path: str) -> bool:
    """工作副本是否已进「异常」区：路径里出现过名为「异常」的一层即可。"""
    return ABNORMAL_DIR_NAME in os.path.normpath(path).split(os.sep)


def copy_target(root: str, original: str, *, abnormal: bool = False) -> str:
    """原件在工作目录里的副本落点：保留它相对来源目录的子路径。

    root/高一1班/张三.pdf → root/工作目录/压缩/高一1班/张三.pdf
    abnormal=True 时落到「异常」区同名位置。原件不在 root 下时退回文件名。
    """
    try:
        rel = os.path.relpath(original, root)
    except ValueError:      # 不同盘符（Windows）
        rel = os.path.basename(original)
    if rel.startswith(".." + os.sep) or rel == "..":
        rel = os.path.basename(original)
    area = abnormal_dir(root) if abnormal else compressed_dir(root)
    return os.path.join(area, rel)


def sibling_area(path: str) -> str:
    """副本在「异常」与「压缩」之间切换时的另一侧落点。"""
    parent = os.path.dirname(path)
    sibling = ABNORMAL_DIR_NAME if os.path.basename(parent) == COMPRESSED_DIR_NAME \
        else COMPRESSED_DIR_NAME
    return os.path.join(os.path.dirname(parent), sibling, os.path.basename(path))


def ensure_work_dirs(root: str) -> None:
    os.makedirs(abnormal_dir(root), exist_ok=True)
    os.makedirs(compressed_dir(root), exist_ok=True)
