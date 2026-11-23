"""上传页的后台 worker 与并发常量。

从 upload_page.py 拆出：ScanWorker / UploadWorker 都是独立 QThread，
只依赖 service/domain，不碰页面 self。测试 patch 的是 upload_page.UploadWorker
这个名字，重导入后仍指向这里的同一个类对象，patch 路径不变。
"""

from __future__ import annotations

import os

from PyQt5.QtCore import QThread, pyqtSignal as Signal

from app.constants import LIMITS
from app.domain.archive_scan import scan_folders
from app.domain.naming import analyze_file_name
from app.domain.upload_inspect import inspect
from app.domain.upload_routing import upload_skip_reason
from app.services import store
from app.services.workspace import load_pending

# 站点单文件上限 10MB，统一压到 9.5MB 留余量；不再提供可选项
COMPRESS_TARGET = LIMITS["PDF_DEFAULT_TARGET_BYTES"]
MAX_BATCH = LIMITS["MAX_UPLOAD_PER_BATCH"]


def _worker_count(cap: int) -> int:
    """并发上限取 cap，同时留一个核给 UI，免得进度条卡住。"""
    return min(cap, max(1, (os.cpu_count() or 4) - 1))
RESULT_TONE = {"成功": "ok", "跳过": "warn", "失败": "error"}


class ScanWorker(QThread):
    """后台扫描：递归收 PDF → 按命名过滤 → 逐份检查（含重复团员）。

    走子目录、读页数、改名都可能慢（几百份档案），放主线程会把界面顶住。"""

    done = Signal(object, object)   # scans, statuses
    failed = Signal(str)
    stage = Signal(str)             # 检查阶段进度文案（压缩中…）

    def __init__(self, dirs: list[str], expected_pages: int, accounts: list[dict],
                 roster, members: dict | None = None, archived: dict | None = None):
        super().__init__()
        self.dirs = list(dirs)
        self.expected_pages = expected_pages
        self.accounts = accounts
        self.roster = roster
        self.members = members
        self.archived = archived

    def run(self):
        try:
            from app.services import prepare

            scans = scan_folders(self.dirs)
            roots: dict[str, str] = {}
            files: list[str] = []
            for scan in scans:
                for path in scan.files:
                    roots[path] = scan.root
                    files.append(path)
            # 设置页「工作区」自动处理过的散件也一并算上（路径不在目录里的那种）
            for entry in load_pending():
                path = entry.get("path", "")
                if path and os.path.isfile(path) and path not in roots:
                    files.append(path)
            # 一、命名 + 页数（原件只读，命名不合规就地改名沿用既有行为）
            statuses = inspect(files, self.expected_pages, self.accounts, self.roster,
                               members=self.members, archived=self.archived,
                               roots=roots)
            # 二、压缩：通过命名/页数的档案生成副本落到「工作目录/压缩」。
            # inspect 可能已改名，FileStatus.root 才是可靠的来源目录。
            roots = {s.path: s.root for s in statuses}

            def on_progress(done, total):
                self.stage.emit(f"压缩工作副本 {done}/{total}")

            prepare.prepare_all(statuses, roots, progress=on_progress)
            self.stage.emit("")
            self.done.emit(scans, statuses)
        except Exception as e:  # 整体失败要显式报出来，不静默留空列表
            self.failed.emit(str(e))


class UploadWorker(QThread):
    """先压缩到目标大小，再按支部分组提交；单文件失败不中断整批。"""

    row_ready = Signal(str, str, str, str)  # account, path, result, reason
    stage = Signal(str)
    all_done = Signal(int, int, int, int, int)  # ok, skipped, total, audit_ok, audit_manual
    # 单批上限放类属性：测试 patch 的是类对象，与 worker 在哪个模块无关
    batch_limit = MAX_BATCH

    def __init__(self, creds: dict[str, str], jobs: list[dict], target: int):
        super().__init__()
        self.creds = creds
        self.jobs = jobs  # [{path, account}]
        self.target = target
        self._stop = False

    def stop(self):
        self._stop = True

    def _login(self, account: str):
        from app.services import login_gate
        from app.services.api import SiteClient

        client = SiteClient(account, self.creds[account])
        kind, msg = login_gate.ensure_logged_in(client)
        if kind == "ok":
            return client, ""
        if kind == "cooldown":
            return None, msg
        if kind == "captcha":
            return None, "需要验证码（密码失败过多），在系统配置双击该账号填写"
        return None, msg or "登录失败"

    def _member_map(self, client, account: str) -> dict:
        """该支部的 (姓名, 手机号) → 团员记录；4 小时内的缓存直接复用。"""
        from datetime import datetime, timezone

        blob = (store.load().get("memberCache") or {}).get(account) or {}
        ts = blob.get("fetchedAt", "")
        if ts:
            try:
                age = (datetime.now(timezone.utc)
                       - datetime.fromisoformat(ts)).total_seconds() / 3600
                if 0 <= age < 4:
                    from app.domain.org_manage import record_from_dict

                    cached = {(d["name"], d["mobile"]): record_from_dict(d)
                              for d in blob.get("members", [])}
                    if cached:
                        return cached
            except (ValueError, KeyError, TypeError):
                pass
        try:
            members, _ = client.get_all_members()
        except Exception:
            return {}
        return {(m.name, m.mobile): m for m in members}

    def _upload_account(self, account: str, jobs: list[dict]) -> dict[str, int]:
        """提交一个支部的全部档案，返回这一步的计数。

        同一支部内部保持串行：站点的分片上传与审核接口按支部受理，并发只会
        互相排队并放大被限流的风险。不同支部之间才并发。
        """
        counts = {"ok": 0, "skipped": 0, "audit_ok": 0, "audit_manual": 0}
        if self._stop:
            return counts
        if len(jobs) > self.batch_limit:
            for job in jobs:
                self.row_ready.emit(account, job["path"], "失败",
                                    f"该支部单批超过 {self.batch_limit} 份，请分批")
            return counts
        client, err = self._login(account)
        if client is None:
            for job in jobs:
                self.row_ready.emit(account, job["path"], "失败", f"登录失败：{err}")
            return counts
        member_map = self._member_map(client, account)

        for job in jobs:
            if self._stop:
                break
            path = job.get("display", job["path"])   # 界面回传键：原件路径
            upload_path = job["path"]                # 上传源：压缩副本
            name = os.path.basename(path)
            analysis = analyze_file_name(name)
            member = member_map.get(analysis.parsed) if analysis.parsed else None
            if member is not None:
                reason = upload_skip_reason(member.report_data_status,
                                            member.ebook_status)
                if reason:
                    self.row_ready.emit(account, path, "跳过", reason)
                    counts["skipped"] += 1
                    continue

            self.stage.emit(f"上传 {name} 到 {account}")
            result = client.upload_file(upload_path)
            if not result.ok:
                self.row_ready.emit(account, path, "失败", result.message)
                continue
            counts["ok"] += 1
            if member is not None and str(member.member_id).isdigit():
                self.stage.emit(f"审核 {name}")
                audit = client.audit_voluntary_book(member.member_id)
                if audit.ok:
                    counts["audit_ok"] += 1
                    self.row_ready.emit(account, path, "成功", "已上传，审核已自动通过")
                else:
                    counts["audit_manual"] += 1
                    self.row_ready.emit(
                        account, path, "成功",
                        f"已上传，自动审核失败：{audit.message}（请到网页端手动审核）")
            else:
                counts["audit_manual"] += 1
                self.row_ready.emit(
                    account, path, "成功", "已上传，未匹配到团员记录，请到网页端手动审核")
        return counts

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        total = len(self.jobs)
        summary = {"ok": 0, "skipped": 0, "audit_ok": 0, "audit_manual": 0}
        if self._stop:
            self.all_done.emit(0, 0, total, 0, 0)
            return

        # 压缩已在预检查阶段完成并落盘（工作目录/压缩），这里直接提交副本。
        # 支部之间并发（每个支部一套独立会话），支部内部串行。
        groups: dict[str, list[dict]] = {}
        for job in self.jobs:
            groups.setdefault(job["account"], []).append(job)

        if groups:
            workers = min(3, len(groups))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {}
                for account, jobs in groups.items():
                    self.stage.emit(f"提交 {account}（{len(jobs)} 份）")
                    futures[pool.submit(self._upload_account, account, jobs)] = account
                for future in as_completed(futures):
                    account = futures[future]
                    self.stage.emit(f"{account} 已提交")
                    try:
                        counts = future.result()
                    except Exception as exc:      # noqa: BLE001
                        for job in groups[account]:
                            self.row_ready.emit(account, job.get("display", job["path"]),
                                                "失败", f"提交失败：{exc}")
                        continue
                    for key in summary:
                        summary[key] += counts[key]
        self.all_done.emit(summary["ok"], summary["skipped"], total,
                           summary["audit_ok"], summary["audit_manual"])


class CellCheckWorker(QThread):
    """逐格检查后台线程：PyMuPDF 渲染 + OpenCV 形态学都放 GIL，用线程池吃满余核。

    实测 47 份 4.61s→3.29s（1.4x）。从 upload_page 内联类外移，行为不变。
    """
    one = Signal(str, int, str)   # 原件路径, 空格数（-1=没查成）, 跳过原因
    finished_all = Signal()

    def __init__(self, targets, check_cells, worker_count, parent=None):
        super().__init__(parent)
        self._targets = targets
        self._check_cells = check_cells
        self._worker_count = worker_count

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=self._worker_count(4)) as pool:
            futures = {pool.submit(self._check_cells, copy): original
                       for original, copy in self._targets}
            for future in as_completed(futures):
                original = futures[future]
                try:
                    result = future.result()
                    count = -1 if result.skipped else len(result.blanks)
                    reason = result.skipped
                except Exception as exc:   # noqa: BLE001 - 单份失败不拖垮整批
                    count, reason = -1, str(exc)
                self.one.emit(original, count, reason)
        self.finished_all.emit()
