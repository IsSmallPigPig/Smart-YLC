"""批量上传页：一键上传到智慧团建，逐文件反馈上传情况。

流程（点「开始上传」后自动执行，无需手动检查）：
  1. 命名检查：解析「姓名+手机号码+入团志愿书.pdf」，不合格的文件标记跳过
  2. 按名单班级路由：12407 → ssfz12407；解析不到班级/账号的标记失败，不影响其余文件
  3. 压缩：超过 9.5MB 的自动压到阈值内（压临时副本，不动原文件）
  4. 逐账号登录（先探测会话，失效才登录）后上传；上传前按团员状况预检
     （优先复用 4 小时内的成员缓存，不重复请求）：未完成报到、线上已显示
     审核通过（电子志愿书已上传）的团员直接跳过并说明原因，不浪费一次失败请求
  5. 结束后反馈：成功 / 跳过 / 失败 各多少，明细见表格
"""

from __future__ import annotations

import os
import shutil
import tempfile

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QTableWidgetItem

from qfluentwidgets import (
    BodyLabel,
    InfoBar,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    TableWidget,
    ToolButton,
    FluentIcon as FIF,
)

from app.ui.page import FluentPage, note_label
from app.constants import LIMITS, MB
from app.domain.chunk_protocol import check_site_limits
from app.domain.naming import analyze_file_name
from app.domain.org_manage import record_from_dict
from app.domain.upload_routing import (
    resolve_file_targets,
    roster_class_lookup,
    upload_skip_reason,
)
from app.services import login_gate, store
from app.services.workspace import load_pending

TARGET = LIMITS["PDF_DEFAULT_TARGET_BYTES"]
MAX_BATCH = LIMITS["MAX_UPLOAD_PER_BATCH"]

RESULT_COLOR = {"成功": "#2e7d32", "失败": "#c62828", "跳过": "#ef6c00"}


class UploadWorker(QThread):
    """按账号分组上传；上传前自动压缩超限文件（临时副本，不动原文件），未报到团员跳过。"""

    row_ready = Signal(int, str, str, str, str)  # row, account, name, result(成功/失败/跳过), reason
    stage = Signal(str)
    all_done = Signal(int, int, int, int, int)  # ok, skipped, total, audit_ok, audit_manual

    def __init__(self, creds: dict[str, str], jobs: list[dict]):
        super().__init__()
        self.creds = creds  # account -> password
        self.jobs = jobs  # [{row, path, account}]
        self._stop = False

    def stop(self):
        self._stop = True

    def _login(self, account: str):
        """登录指定账号：复用已保存会话探测，失效才登录（带验证码/风控冷却）。
        返回 (client, 错误消息)。"""
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

    def _member_map(self, client, account: str) -> dict[tuple[str, str], object]:
        """该团支部的 (姓名, 手机号) → 团员记录（含报到/志愿书状态与 mid）。

        优先复用「组织状况」维护的成员缓存（4 小时内拉取过就不重复请求，
        也不动缓存数据）；缓存缺失或过期才在线拉取。"""
        from datetime import datetime, timezone

        blob = (store.load().get("memberCache") or {}).get(account) or {}
        ts = blob.get("fetchedAt", "")
        fresh = False
        if ts:
            try:
                age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 3600
                fresh = 0 <= age_h < login_gate.FRESH_HOURS
            except ValueError:
                fresh = False
        if fresh:
            try:
                m = {(d["name"], d["mobile"]): record_from_dict(d)
                     for d in blob.get("members", [])}
                if m:
                    return m
            except (KeyError, TypeError):
                pass  # 缓存结构异常 → 回落在线拉取
        try:
            members, _ = client.get_all_members()
        except Exception:
            return {}  # 拉不到名单就不预检，交给站点返回的错误
        return {(m.name, m.mobile): m for m in members}

    def run(self):
        ok = skipped = 0
        audit_ok = audit_manual = 0
        tmpdir = tempfile.mkdtemp(prefix="gd12355-upload-")
        try:
            groups: dict[str, list[dict]] = {}
            for j in self.jobs:
                groups.setdefault(j["account"], []).append(j)

            for account, acc_jobs in groups.items():
                if self._stop:
                    break
                if len(acc_jobs) > MAX_BATCH:
                    for j in acc_jobs:
                        self.row_ready.emit(j["row"], account, os.path.basename(j["path"]),
                                            "失败", f"该账号单批超过 {MAX_BATCH} 份，请分批")
                    continue
                self.stage.emit(f"登录 {account}…")
                client, err = self._login(account)
                if client is None:
                    for j in acc_jobs:
                        self.row_ready.emit(j["row"], account, os.path.basename(j["path"]), "失败", f"登录失败：{err}")
                    continue
                self.stage.emit(f"核对 {account} 团员状况…")
                member_map = self._member_map(client, account)

                for j in acc_jobs:
                    if self._stop:
                        break
                    path, name = j["path"], os.path.basename(j["path"])
                    # 预检：未完成报到拦截；线上已审核通过（志愿书已上传）跳过重复上传
                    a = analyze_file_name(name)
                    member = member_map.get(a.parsed) if a.parsed else None
                    if member is not None:
                        reason = upload_skip_reason(member.report_data_status, member.ebook_status)
                        if reason:
                            self.row_ready.emit(j["row"], account, name, "跳过", reason)
                            skipped += 1
                            continue
                    upload_path = path
                    # 自动压缩：超过 9.5MB → 压临时副本
                    if os.path.getsize(path) > TARGET:
                        self.stage.emit(f"压缩 {name}…")
                        from app.services.compress import compress_pdf

                        tmp = os.path.join(tmpdir, name)
                        try:
                            r = compress_pdf(path, tmp, TARGET)
                            if r.get("over"):
                                self.row_ready.emit(j["row"], account, name, "失败",
                                                    f"压缩到最低参数仍 {r['dst_size'] / MB:.1f}MB，超过站点 10MB 限制")
                                continue
                            upload_path = tmp
                        except Exception as e:
                            self.row_ready.emit(j["row"], account, name, "失败", f"压缩失败：{e}")
                            continue
                    self.stage.emit(f"上传 {name} → {account}…")
                    result = client.upload_file(upload_path)
                    if not result.ok:
                        self.row_ready.emit(j["row"], account, name, "失败", result.message)
                        continue
                    ok += 1
                    # 上传入库成功 → 自动调用网页端同款「审核」接口（status=2 通过）
                    if member is not None and str(member.member_id).isdigit():
                        self.stage.emit(f"审核 {name}…")
                        audit = client.audit_voluntary_book(member.member_id)
                        if audit.ok:
                            audit_ok += 1
                            self.row_ready.emit(j["row"], account, name, "成功", "已上传，审核已自动通过")
                        else:
                            audit_manual += 1
                            self.row_ready.emit(j["row"], account, name, "成功",
                                                f"已上传，自动审核失败：{audit.message}（请到网页端手动审核）")
                    else:
                        audit_manual += 1
                        self.row_ready.emit(j["row"], account, name, "成功",
                                            "已上传，未匹配到团员记录，请到网页端手动审核")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        self.all_done.emit(ok, skipped, len(self.jobs), audit_ok, audit_manual)


class UploadPage(FluentPage):
    def __init__(self, window, parent=None):
        super().__init__('page-upload', parent)
        self.window_ref = window
        self.worker: UploadWorker | None = None
        lay = self.vBoxLayout

        row = QHBoxLayout()
        self.dir_edit = LineEdit()
        self.dir_edit.setPlaceholderText("选择待上传 PDF 所在文件夹（单批 ≤50 份、单文件 ≤10MB）")
        self.dir_btn = ToolButton(FIF.FOLDER)
        self.dir_btn.clicked.connect(self._pick_dir)
        row.addWidget(self.dir_edit, 1)
        row.addWidget(self.dir_btn)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self.start_btn = PrimaryPushButton(FIF.CLOUD, "开始上传")
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = PushButton(FIF.PAUSE, "停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(lambda: self.worker and self.worker.stop())
        self.template_btn = PushButton(FIF.DOCUMENT, "下载名单模板")
        self.template_btn.clicked.connect(self._download_template)
        row2.addWidget(self.start_btn)
        row2.addWidget(self.stop_btn)
        row2.addWidget(self.template_btn)
        row2.addStretch(1)
        lay.addLayout(row2)

        self.stat = BodyLabel("点「开始上传」即一键执行：命名检查 → 按班级分流 → 压缩 → 预检报到 → 上传 → 自动审核")
        lay.addWidget(self.stat)
        self.ws_note = note_label("")
        lay.addWidget(self.ws_note)

        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["文件", "目标账号", "结果", "说明"])
        self.add_table(self.table, 1)

    # ---------- 入口 ----------

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_ws_note()

    def _refresh_ws_note(self):
        pending = load_pending()
        if pending:
            names = "、".join(e["name"] for e in pending[:3])
            self.ws_note.setText(f"工作区待上传 {len(pending)} 个：{names}{'…' if len(pending) > 3 else ''}")
        else:
            self.ws_note.setText("工作区暂无待上传文件（在「系统配置」设置工作区目录后，新 PDF 自动加入）")

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d:
            self.dir_edit.setText(d)

    def _download_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存名单模板", "团员名单模板.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        from app.services.excel import save_roster_template

        save_roster_template(path)
        InfoBar.success("模板已保存", path, duration=4000, parent=self)

    # ---------- 上传主流程 ----------

    def _gather_files(self) -> list[str]:
        """待上传文件 = 所选目录 PDF + 工作区待上传列表（按路径去重）。"""
        files: list[str] = []
        d = self.dir_edit.text().strip()
        if d and os.path.isdir(d):
            files.extend(
                os.path.join(d, f) for f in sorted(os.listdir(d))
                if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(d, f))
            )
        for e in load_pending():
            p = e.get("path", "")
            if p and os.path.isfile(p) and p not in files:
                files.append(p)
        return files

    def _set_row(self, i: int, name: str, account: str, result: str, reason: str):
        self.table.setItem(i, 0, QTableWidgetItem(name))
        self.table.setItem(i, 1, QTableWidgetItem(account))
        self.table.setItem(i, 2, QTableWidgetItem(result))
        self.table.setItem(i, 3, QTableWidgetItem(reason))
        color = RESULT_COLOR.get(result)
        if color:
            self.table.item(i, 2).setForeground(QColor(color))

    def _start(self):
        files = self._gather_files()
        if not files:
            InfoBar.warning("没有 PDF", "所选目录和工作区里都没有 .pdf 文件", duration=2500, parent=self)
            return

        # 1) 命名检查（纯函数，快）
        parsed: dict[str, tuple[str, str]] = {}
        for p in files:
            a = analyze_file_name(os.path.basename(p))
            if a.parsed:
                parsed[os.path.basename(p)] = a.parsed

        # 2) 按班级分流；解析不到的标记失败行，不弹窗阻断一键上传
        sp = self.window_ref.settings_page
        accounts = sp.accounts
        creds = {a["userName"]: a["password"] for a in accounts if a.get("password")}
        targets = resolve_file_targets(
            [os.path.basename(p) for p in files], parsed, list(creds), roster_class_lookup(sp.roster))
        target_by_name = {t.filename: t for t in targets}

        # 3) 站点硬限（未解析的不检查，直接失败行）
        checkable = [p for p in files if os.path.basename(p) in parsed]
        over = check_site_limits([{"name": os.path.basename(p), "size": os.path.getsize(p)} for p in checkable])
        over_names = {r.split("：")[0] for r in over}

        rows: list[dict] = []
        for i, p in enumerate(files):
            name = os.path.basename(p)
            t = target_by_name.get(name)
            if t is None or t.problem:
                rows.append({"row": i, "path": p, "account": "", "name": name,
                             "reason": t.problem if t else "命名不合规"})
            elif name in over_names:
                rows.append({"row": i, "path": p, "account": t.account, "name": name,
                             "reason": "超过站点 10MB 限制"})
            else:
                rows.append({"row": i, "path": p, "account": t.account, "name": name, "reason": ""})

        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            if r["reason"]:
                self._set_row(i, r["name"], r["account"], "失败", r["reason"])
            else:
                self._set_row(i, r["name"], r["account"], "…", "-")
        bad = [r for r in rows if r["reason"]]
        jobs = [{"row": r["row"], "path": r["path"], "account": r["account"]}
                for r in rows if not r["reason"]]
        if bad:
            self.stat.setText(f"{len(bad)} 个文件被拦下（命名/班级/超限）；可上传 {len(jobs)} 个")
        if not jobs:
            InfoBar.warning("没有可上传文件", "全部文件都被命名检查或分流拦截，按表格里的说明修正后重试", duration=4000, parent=self)
            return

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.worker = UploadWorker(creds, jobs)
        self.worker.row_ready.connect(self._on_row)
        self.worker.stage.connect(lambda s: self.stat.setText(s))
        self.worker.all_done.connect(self._on_done)
        self.worker.start()

    def _on_row(self, i: int, account: str, name: str, result: str, reason: str):
        self._set_row(i, name, account, result, reason)
        self.stat.setText(f"已处理 {i + 1}/{self.table.rowCount()}：{name}")

    def _on_done(self, ok: int, skipped: int, total: int, audit_ok: int, audit_manual: int):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._refresh_ws_note()
        failed = total - ok - skipped
        parts = [f"成功 {ok}"]
        if audit_ok:
            parts.append(f"审核自动通过 {audit_ok}")
        if audit_manual:
            parts.append(f"{audit_manual} 个需网页端手动审核（原因见表格说明）")
        if skipped:
            parts.append(f"跳过 {skipped}（未完成报到/线上已审核通过等，原因见表格）")
        parts.append(f"失败 {failed}")
        InfoBar.success("上传批次结束", "、".join(parts), duration=8000, parent=self)
