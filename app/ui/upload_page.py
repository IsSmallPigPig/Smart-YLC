"""上传档案：工作区取件 → 自动检查命名与页数 → 按支部预组织 → 压缩并逐支部提交。

流程：
  1. 选择工作目录：目录里的 PDF 与待上传列表合并去重
  2. 文档检查：命名能解析但格式不标准的直接改成标准名；页数不符、命名解析
     不出、路由不到支部的标为异常，上传前拦下
  3. 结果用状态列表呈现，并按团支部收进折叠卡片，未通过的单列一组
  4. 点「开始上传」：先把超限文件压到目标大小，再逐个支部登录提交、自动审核
"""

from __future__ import annotations

import os
import shutil
import tempfile

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QFileDialog, QVBoxLayout, QWidget

from qfluentwidgets import (
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    PrimaryPushButton,
    PushButton,
    ToolButton,
)

from app.constants import LIMITS, MB
from app.domain.account_health import excluded_note, split_accounts
from app.domain.naming import analyze_file_name
from app.domain.upload_inspect import (group_by_branch, inspect, member_index,
                                       split_branch_label, summarize)
from app.domain.upload_routing import upload_skip_reason
from app.services import store
from app.services.workspace import load_pending, remove_pending
from app.ui.page import CollapsibleCard, FluentPage, PageHeader, note_label, set_tip
from app.ui.widgets import StatusList

TARGETS = (
    ("压到 19.5MB", LIMITS["PDF_20MB_PRESET_BYTES"]),
)
MAX_BATCH = LIMITS["MAX_UPLOAD_PER_BATCH"]
RESULT_TONE = {"成功": "ok", "跳过": "warn", "失败": "error"}


class InspectWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, files, expected_pages: int, accounts: list[dict], roster,
                 members: dict | None = None):
        super().__init__()
        self.files = files
        self.expected_pages = expected_pages
        self.accounts = accounts
        self.roster = roster
        self.members = members

    def run(self):
        try:
            self.done.emit(inspect(self.files, self.expected_pages,
                                   self.accounts, self.roster,
                                   members=self.members))
        except Exception as e:  # 整体失败要显式报出来，不静默留空列表
            self.failed.emit(str(e))


class UploadWorker(QThread):
    """先压缩到目标大小，再按支部分组提交；单文件失败不中断整批。"""

    row_ready = Signal(str, str, str, str)  # account, path, result, reason
    stage = Signal(str)
    all_done = Signal(int, int, int, int, int)  # ok, skipped, total, audit_ok, audit_manual

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
            return None, "需要验证码（密码失败过多），请先在网页端登录一次"
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

    def _compress(self, job: dict, tmpdir: str) -> str | None:
        """超过目标大小的压到临时副本；返回上传用路径，失败返回 None。"""
        path = job["path"]
        if os.path.getsize(path) <= self.target:
            return path
        from app.services.compress import compress_pdf

        tmp = os.path.join(tmpdir, os.path.basename(path))
        try:
            result = compress_pdf(path, tmp, self.target)
        except Exception as e:
            self.row_ready.emit(job["account"], path, "失败", f"压缩失败：{e}")
            return None
        if result.get("over"):
            self.row_ready.emit(
                job["account"], path, "失败",
                f"压到最低参数仍 {result['dst_size'] / MB:.1f}MB，超过站点限制")
            return None
        return tmp

    def run(self):
        ok = skipped = audit_ok = audit_manual = 0
        tmpdir = tempfile.mkdtemp(prefix="gd-upload-")
        try:
            # 一、压缩：先全部处理完，再开始提交
            prepared = []
            total = len(self.jobs)
            for i, job in enumerate(self.jobs):
                if self._stop:
                    break
                self.stage.emit(f"压缩 {i + 1}/{total}：{os.path.basename(job['path'])}")
                upload_path = self._compress(job, tmpdir)
                if upload_path is not None:
                    prepared.append({"path": job["path"], "account": job["account"],
                                     "upload_path": upload_path})
            if self._stop:
                self.all_done.emit(ok, skipped, total, audit_ok, audit_manual)
                return

            # 二、按支部提交
            groups: dict[str, list[dict]] = {}
            for job in prepared:
                groups.setdefault(job["account"], []).append(job)

            for index, (account, jobs) in enumerate(groups.items(), 1):
                if self._stop:
                    break
                if len(jobs) > MAX_BATCH:
                    for job in jobs:
                        self.row_ready.emit(account, job["path"], "失败",
                                            f"该支部单批超过 {MAX_BATCH} 份，请分批")
                    continue
                self.stage.emit(f"提交 {index}/{len(groups)}：{account}")
                client, err = self._login(account)
                if client is None:
                    for job in jobs:
                        self.row_ready.emit(account, job["path"], "失败", f"登录失败：{err}")
                    continue
                member_map = self._member_map(client, account)

                for job in jobs:
                    if self._stop:
                        break
                    path = job["path"]
                    name = os.path.basename(path)
                    analysis = analyze_file_name(name)
                    member = member_map.get(analysis.parsed) if analysis.parsed else None
                    if member is not None:
                        reason = upload_skip_reason(member.report_data_status,
                                                    member.ebook_status)
                        if reason:
                            self.row_ready.emit(account, path, "跳过", reason)
                            skipped += 1
                            continue

                    self.stage.emit(f"上传 {name} → {account}")
                    result = client.upload_file(job["upload_path"])
                    if not result.ok:
                        self.row_ready.emit(account, path, "失败", result.message)
                        continue
                    ok += 1
                    if member is not None and str(member.member_id).isdigit():
                        self.stage.emit(f"审核 {name}")
                        audit = client.audit_voluntary_book(member.member_id)
                        if audit.ok:
                            audit_ok += 1
                            self.row_ready.emit(account, path, "成功",
                                                "已上传，审核已自动通过")
                        else:
                            audit_manual += 1
                            self.row_ready.emit(
                                account, path, "成功",
                                f"已上传，自动审核失败：{audit.message}（请到网页端手动审核）")
                    else:
                        audit_manual += 1
                        self.row_ready.emit(
                            account, path, "成功",
                            "已上传，未匹配到团员记录，请到网页端手动审核")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        self.all_done.emit(ok, skipped, len(self.jobs), audit_ok, audit_manual)


class UploadPage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__("page-upload", parent)
        self.window_ref = window
        self.worker: UploadWorker | None = None
        self.inspector: InspectWorker | None = None
        self.statuses = []
        self._branch_rows: dict[str, tuple[StatusList, int]] = {}

        lay = self.vBoxLayout

        # 标题与工具条直接写在背景上；说明小字承担原来两行设置项的职责
        self.header = PageHeader(
            "上传档案", "先选择工作目录，里面的 PDF 会自动加入待处理")
        self.pick_btn = PushButton(FIF.FOLDER, "选择目录")
        self.pick_btn.clicked.connect(self._pick_dir)
        self.rescan_btn = PushButton(FIF.SYNC, "刷新目录")
        set_tip(self.rescan_btn, "重新检查工作目录")
        self.rescan_btn.clicked.connect(self.refresh)
        self.target_combo = ComboBox()
        for label, _ in TARGETS:
            self.target_combo.addItem(label)
        self.target_combo.setFixedWidth(126)
        set_tip(self.target_combo, "上传前把超过站点限制的 PDF 压到这个大小")
        self.upload_btn = PrimaryPushButton(FIF.CLOUD, "开始上传")
        self.upload_btn.setEnabled(False)
        self.upload_btn.clicked.connect(self._start_upload)
        self.stop_btn = PushButton(FIF.PAUSE, "停止上传")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(lambda: self.worker and self.worker.stop())
        for w in (self.pick_btn, self.rescan_btn, self.target_combo,
                  self.upload_btn, self.stop_btn):
            self.header.add_control(w)
        lay.addWidget(self.header)

        self.summary = note_label("")
        lay.addWidget(self.summary)

        # 上方不再放不按支部分组的扁平列表：文件只按团支部收进下方折叠卡片，
        # 单一入口避免「同一份档案上面一张卡、下面又一张卡」的重复。
        self._empty_text = "工作目录里还没有 PDF"

        self.branch_box = QWidget()
        self.branch_lay = QVBoxLayout(self.branch_box)
        self.branch_lay.setContentsMargins(0, 0, 0, 0)
        self.branch_lay.setSpacing(10)
        lay.addWidget(self.branch_box)
        lay.addStretch(1)

    # ---------- 数据来源 ----------

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def _workspace_dir(self) -> str:
        path = str(store.load().get("workspaceDir") or "").strip()
        return path if path and os.path.isdir(path) else ""

    def _expected_pages(self) -> int:
        return self.window_ref.settings_page.expected_pages()

    def _accounts(self) -> list[dict]:
        return list(self.window_ref.settings_page.accounts)

    def _roster(self):
        return self.window_ref.settings_page.roster

    def _gather(self, directory: str) -> list[str]:
        files = [os.path.join(directory, name) for name in sorted(os.listdir(directory))
                 if name.lower().endswith(".pdf")
                 and os.path.isfile(os.path.join(directory, name))]
        for entry in load_pending():
            path = entry.get("path", "")
            if path and os.path.isfile(path) and path not in files:
                files.append(path)
        return files

    # ---------- 检查 ----------

    def _pick_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "选择工作目录")
        if not directory:
            return
        store.update(lambda cfg: cfg.__setitem__("workspaceDir", directory))
        settings = self.window_ref.settings_page
        settings.ws_edit.setText(directory)
        settings._setup_watcher(directory)
        self.refresh()

    def refresh(self):
        directory = self._workspace_dir()
        if not directory:
            self.header.set_desc("先选择工作目录，里面的 PDF 会自动加入待处理")
            self.statuses = []
            self._empty_text = "还没有选择工作目录"
            self.summary.setText("")
            self.upload_btn.setEnabled(False)
            self._rebuild_branches()
            return
        files = self._gather(directory)
        self.header.set_desc(
            f"正在监听 {os.path.basename(directory) or directory}，{len(files)} 份待处理")
        if not files:
            self.statuses = []
            self._empty_text = "工作目录里还没有 PDF"
            self.summary.setText("")
            self.upload_btn.setEnabled(False)
            self._rebuild_branches()
            return
        self.rescan_btn.setEnabled(False)
        self.summary.setText(f"正在检查 {len(files)} 份档案…")
        cached_members = (store.load().get("memberCache") or {})
        self.inspector = InspectWorker(files, self._expected_pages(),
                                       self._accounts(), self._roster(),
                                       member_index(cached_members))
        self.inspector.done.connect(self._on_inspected)
        self.inspector.failed.connect(self._on_inspect_failed)
        self.inspector.start()

    def _on_inspected(self, statuses):
        QTimer.singleShot(0, lambda: self._apply_statuses(list(statuses)))

    def _on_inspect_failed(self, message: str):
        self.rescan_btn.setEnabled(True)
        self.summary.setText("")
        InfoBar.error("档案检查失败", message, duration=8000, parent=self)

    def _apply_statuses(self, statuses):
        self.rescan_btn.setEnabled(True)
        self.statuses = statuses
        stats = summarize(statuses)
        parts = [f"正常 {stats['ok']}"]
        if stats["fixed"]:
            parts.append(f"已纠正 {stats['fixed']}")
        if stats["missing"]:
            parts.append(f"缺团员 {stats['missing']}")
        parts.append(f"异常 {stats['error']}")
        self.summary.setText(
            f"共 {stats['total']} 份：" + "，".join(parts)
            + f"，分 {stats['branches']} 个支部")
        self.upload_btn.setEnabled(any(s.usable for s in statuses))
        self._rebuild_branches()
        if stats["missing"]:
            InfoBar.warning(
                "支部名单缺团员",
                f"{stats['missing']} 份档案的团员不在对应支部名单里，仍可上传；"
                "名单请到组织管理核对",
                duration=9000, parent=self)

    def set_search(self, text: str):
        """标题栏搜索框：按文件名、支部或问题说明过滤下方折叠卡片。"""
        text = (text or "").strip()
        if text == getattr(self, "_search", ""):
            return
        self._search = text
        self._rebuild_branches()

    def _row_item(self, status) -> dict:
        """把一份档案的状态翻译成折叠卡片里一行的展示数据。"""
        meta = []
        if status.pages:
            meta.append(f"{status.pages} 页")
        note = status.problem
        if not note and status.renamed_from:
            note = f"原名 {status.renamed_from}，已自动改名"
        if not note:
            note = status.notice
        # 可路由的档案标出目标团支部，让「按班级分流到对应账号」的逻辑可见，
        # 而不是被「只通知错误」吞掉
        if status.branch and not status.problem:
            target = f"目标支部：{status.branch}"
            note = f"{note}　{target}" if note else target
        elif status.branch:
            meta.append(status.branch)
        return {
            "title": status.name,
            "note": note,
            "tone": status.tone,
            "status": status.status_text,
            "meta": meta,
        }

    def _rebuild_branches(self):
        while self.branch_lay.count():
            item = self.branch_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._branch_rows = {}

        needle = getattr(self, "_search", "").lower()
        statuses = self.statuses
        if needle:
            statuses = [s for s in statuses
                        if needle in str(s.name).lower()
                        or needle in str(s.problem or "").lower()
                        or needle in str(s.notice or "").lower()
                        or (s.branch and needle in str(s.branch).lower())]

        groups = group_by_branch(statuses)
        if not groups:
            empty = ("没有匹配的档案" if needle
                     else getattr(self, "_empty_text", "工作目录里还没有 PDF"))
            self.branch_lay.addWidget(note_label(empty))
            return
        for branch, bstatuses in groups.items():
            stage, name = split_branch_label(branch)
            tags = ([(stage, "gray")] if stage else []) + [(f"{len(bstatuses)} 份", "gray")]
            todo = sum(1 for s in bstatuses if not s.usable)
            if todo:
                tags.append((f"{todo} 待处理", "orange"))
            missing = sum(1 for s in bstatuses if s.usable and s.member == "missing")
            if missing:
                tags.append((f"缺团员 {missing}", "orange"))
            card = CollapsibleCard(name or branch, expanded=True, tags=tags)
            listing = StatusList()
            listing.set_items([self._row_item(s) for s in bstatuses])
            card.body_lay.addWidget(listing)
            self.branch_lay.addWidget(card)
            for row_index, s in enumerate(bstatuses):
                self._branch_rows[s.path] = (listing, row_index)

    # ---------- 上传 ----------

    def _start_upload(self):
        usable = [s for s in self.statuses if s.usable]
        if not usable:
            InfoBar.warning("没有可上传档案", "先处理状态列表里的异常文件",
                            duration=4000, parent=self)
            return
        accounts, excluded = split_accounts(self._accounts())
        if excluded:
            InfoBar.warning("跳过异常账号",
                            f"{excluded_note(excluded)} 不参与上传；测活恢复正常后自动纳入",
                            duration=6000, parent=self)
        creds = {a["userName"]: a.get("password", "")
                 for a in accounts if a.get("password")}
        jobs = [{"path": s.path, "account": s.account}
                for s in usable if s.account in creds]
        missing = sum(1 for s in usable if s.account not in creds)
        if missing:
            InfoBar.warning("部分支部不可用",
                            f"{missing} 份档案的账号没有密码，本轮跳过",
                            duration=6000, parent=self)
        if not jobs:
            InfoBar.warning("没有可提交的档案", "所选支部的账号都不可用",
                            duration=4000, parent=self)
            return

        for status in self.statuses:
            if status.usable:
                ref = self._branch_rows.get(status.path)
                if ref is not None:
                    ref[0].set_row_state(ref[1], "等待", "muted")
        self.upload_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.rescan_btn.setEnabled(False)
        target = TARGETS[max(0, self.target_combo.currentIndex())][1]
        self.worker = UploadWorker(creds, jobs, target)
        self.worker.row_ready.connect(self._on_row)
        self.worker.stage.connect(self.summary.setText)
        self.worker.all_done.connect(self._on_done)
        self.worker.start()

    def _on_row(self, account: str, path: str, result: str, reason: str):
        QTimer.singleShot(0, lambda: self._apply_row(account, path, result, reason))

    def _apply_row(self, account: str, path: str, result: str, reason: str):
        ref = self._branch_rows.get(path)
        if ref is not None:
            ref[0].set_row_state(
                ref[1], result, RESULT_TONE.get(result, "muted"), reason)
        if result == "成功":
            remove_pending(path)

    def _on_done(self, ok: int, skipped: int, total: int,
                 audit_ok: int, audit_manual: int):
        QTimer.singleShot(0, lambda: self._apply_done(ok, skipped, total,
                                                      audit_ok, audit_manual))

    def _apply_done(self, ok: int, skipped: int, total: int,
                    audit_ok: int, audit_manual: int):
        """整批结束只发一条消息：成功份数在前，失败与跳过作为限定条件。"""
        self.upload_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.rescan_btn.setEnabled(True)
        message = f"{ok} 份档案已导入"
        if audit_manual:
            message += f"，{audit_manual} 份需到网页端手动审核"
        failed = total - ok - skipped
        if failed or skipped:
            extra = []
            if failed:
                extra.append(f"{failed} 份失败")
            if skipped:
                extra.append(f"{skipped} 份跳过")
            message += "；" + "，".join(extra) + "，原因见列表"
            InfoBar.warning("上传结束", message, duration=9000, parent=self)
        else:
            InfoBar.success("上传完成", message, duration=5000, parent=self)
        self.refresh()
