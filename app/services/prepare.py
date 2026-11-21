"""预检查的压缩阶段：为通过命名/页数的档案生成工作副本，供空格检查与上传。

顺序（2026-11-20 预检查改造）：命名 → 页数 → **压缩** → 空格。
本模块负责其中「压缩」一步，以及异常副本的状态回收：

  * 原件只读，绝不改名/移动/删除；副本落到 工作目录/压缩/<相对路径>。
  * 副本已存在、体积达标、且不比原件旧时复用，刷新不重复压缩。
  * 超过站点上限压不下来的，副本落到 工作目录/异常/，这份档案不上传。
  * 手动标记异常也是把副本在 压缩↔异常 间搬运；刷新时按异常区的文件
    倒推出哪些原件被标记过，重建界面上的异常状态。

纯逻辑（压缩委托 compress.compress_pdf），可在测试里直接调用。
"""

from __future__ import annotations

import os
import shutil

from app.constants import LIMITS
from app.domain import work_area
from app.domain.upload_inspect import FileStatus

TARGET = LIMITS["PDF_DEFAULT_TARGET_BYTES"]


def _fresh_enough(copy_path: str, original: str) -> bool:
    """副本在且不比原件旧：原件改过就要重做副本。"""
    if not os.path.isfile(copy_path):
        return False
    try:
        return os.path.getmtime(copy_path) >= os.path.getmtime(original)
    except OSError:
        return False


def prepare_one(status: FileStatus, root: str,
                target: int = TARGET, force: bool = False) -> str:
    """为一份档案准备压缩副本，返回副本绝对路径。

    通过检查的文件副本进「压缩」区；压不下来（超限）的进「异常」区。
    status.work_path 会被写入。root 为空（待上传散件）时以原件所在目录为根。
    """
    root = root or os.path.dirname(status.path)
    work_area.ensure_work_dirs(root)
    normal = work_area.copy_target(root, status.path)
    abnormal = work_area.copy_target(root, status.path, abnormal=True)

    # 原件可能被手动标记过异常：异常区已有对应副本就沿用，不重复生成
    if _fresh_enough(abnormal, status.path):
        status.work_path = abnormal
        return abnormal
    if not force and _fresh_enough(normal, status.path) \
            and os.path.getsize(normal) <= target:
        status.work_path = normal
        return normal

    from app.services.compress import compress_pdf

    os.makedirs(os.path.dirname(normal), exist_ok=True)
    tmp = normal + ".tmp"
    try:
        result = compress_pdf(status.path, tmp, target)
        if result.get("over"):
            # 压不下来：副本进异常区，原件不动
            os.makedirs(os.path.dirname(abnormal), exist_ok=True)
            shutil.move(tmp, abnormal)
            status.work_path = abnormal
            return abnormal
        os.replace(tmp, normal)
        status.work_path = normal
        return normal
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def prepare_all(statuses: list[FileStatus], roots: dict[str, str],
                target: int = TARGET, force: bool = False,
                progress=None) -> list[FileStatus]:
    """并发为所有可上传档案准备副本。roots 是原件路径→来源目录。

    不可用的档案（命名/页数错误、已有档案、重复）不生成副本——它们不上传，
    也不该占工作目录。progress(done, total) 可选，用于扫描期进度反馈。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    jobs = [s for s in statuses if s.usable]
    if not jobs:
        return statuses

    def one(status: FileStatus):
        root = roots.get(status.path, "")
        return status, prepare_one(status, root, target=target, force=force)

    from app.services import app_log

    done = 0
    failed: list[str] = []
    workers = min(3, max(1, (os.cpu_count() or 4) - 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_paths = {pool.submit(one, s): s.path for s in jobs}
        for future in as_completed(future_paths):
            done += 1
            if progress:
                progress(done, len(jobs))
            try:
                future.result()
            except Exception as exc:   # noqa: BLE001 - 单份失败留空 work_path，后续当异常处理
                # 磁盘满/文件被占用时不能静默：否则用户只看到档案没处理，无从排查。
                path = future_paths[future]
                app_log.error("准备压缩副本失败 %s：%s: %s", path,
                              type(exc).__name__, exc)
                failed.append(path)
    if failed:
        app_log.warning("本轮有 %d 份档案没能准备副本，详见错误日志", len(failed))
    return statuses


def abnormal_originals(roots: dict[str, str]) -> set[str]:
    """扫描各来源目录的异常区，倒推出被标记异常的原件绝对路径集合。

    异常区里存的是副本，文件名与原件相同、相对子路径一致。按相对路径拼回
    来源目录即可定位原件。手动移走的副本不再出现，异常标记随之取消。
    """
    out: set[str] = set()
    for root in set(roots.values()):
        if not root:
            continue
        area = work_area.abnormal_dir(root)
        if not os.path.isdir(area):
            continue
        for dirpath, _dirs, files in os.walk(area):
            for name in files:
                if not name.lower().endswith(".pdf"):
                    continue
                copy_path = os.path.join(dirpath, name)
                # copy_target 的逆运算：副本相对异常区的路径 = 原件相对 root 的路径
                rel = os.path.relpath(copy_path, area)
                out.add(os.path.normpath(os.path.join(root, rel)))
    return out
