"""批量上传页：命名检查 → 压缩 → 按班级分流到对应团支部账号 → 分片上传。

流程（点「开始上传」后自动执行）：
  1. 命名检查：解析「姓名+手机号码+入团志愿书.pdf」，不合格的文件标记跳过
  2. 按名单班级路由：12407 → ssfz12407；解析不到班级/账号的弹窗列出，管理员确认后只传可路由部分
  3. 压缩：超过 9.5MB 的自动压到阈值内（压临时副本，不动原文件）
  4. 按账号逐个登录上传（先探测会话，失效才登录）；上传后需网页端人工「审核」

工作区（系统配置设置）检测到的新 PDF 自动出现在待上传列表。
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
    MessageBox,
    PrimaryPushButton,
    PushButton,
    TableWidget,
    ToolButton,
    FluentIcon as FIF,
)

from app.ui.page import FluentPage, note_label
from app.constants import CONFIRM_PHRASE, LIMITS, MB
from app.domain.chunk_protocol import check_site_limits
from app.domain.naming import analyze_file_name
from app.domain.upload_routing import resolve_file_targets, roster_class_lookup
from app.services.workspace import load_pending

TARGET = LIMITS["PDF_DEFAULT_TARGET_BYTES"]
MAX_BATCH = LIMITS["MAX_UPLOAD_PER_BATCH"]


class UploadWorker(QThread):
    """按账号分组上传；上传前自动压缩超限文件（临时副本，不动原文件）。"""

    row_ready = Signal(int, str, str, bool, str)  # row, account, name, ok, reason
    stage = Signal(str)
    all_done = Signal(int, int)

    def __init__(self, creds: dict[str, str], jobs: list[dict]):
        super().__init__()
        self.creds = creds  # account -> password
        self.jobs = jobs  # [{row, path, account}]
        self._stop = False

    def stop(self):
        self._stop = True

    def _login(self, account: str):
        """登录指定账号：先探测会话，失效才登录。返回 (client, 错误消息)。"""
        from app.services.api import SiteClient

        client = SiteClient(account, self.creds[account])
        probe = client.session_account()
        if probe.ok:
            return client, ""
        verdict = client.login()
        kind = str((verdict.data or {}).get("kind", "failed")) if isinstance(verdict.data, dict) else "failed"
        if kind == "captcha":
            return None, "需要验证码（密码失败过多），请先在网页端登录一次"
        if kind != "ok":
            return None, verdict.message or "登录失败"
        return client, ""

    def run(self):
        ok = 0
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
                                            False, f"该账号单批超过 {MAX_BATCH} 份，请分批")
                    continue
                self.stage.emit(f"登录 {account}…")
                client, err = self._login(account)
                if client is None:
                    for j in acc_jobs:
                        self.row_ready.emit(j["row"], account, os.path.basename(j["path"]), False, f"登录失败：{err}")
                    continue

                for j in acc_jobs:
                    if self._stop:
                        break
                    path, name = j["path"], os.path.basename(j["path"])
                    upload_path = path
                    # 自动压缩：超过 9.5MB → 压临时副本
                    if os.path.getsize(path) > TARGET:
                        self.stage.emit(f"压缩 {name}…")
                        from app.services.compress import compress_pdf

                        tmp = os.path.join(tmpdir, name)
                        try:
                            r = compress_pdf(path, tmp, TARGET)
                            if r.get("over"):
                                self.row_ready.emit(j["row"], account, name, False,
                                                    f"压缩到最低参数仍 {r['dst_size'] / MB:.1f}MB，超过站点 10MB 限制")
                                continue
                            upload_path = tmp
                        except Exception as e:
                            self.row_ready.emit(j["row"], account, name, False, f"压缩失败：{e}")
                            continue
                    self.stage.emit(f"上传 {name} → {account}…")
                    result = client.upload_file(upload_path)
                    self.row_ready.emit(j["row"], account, name, result.ok, result.message)
                    ok += 1 if result.ok else 0
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        self.all_done.emit(ok, len(self.jobs))


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
        self.confirm_edit = LineEdit()
        self.confirm_edit.setPlaceholderText(f"真实上传前请在此手输「{CONFIRM_PHRASE}」确认")
        self.start_btn = PrimaryPushButton(FIF.CLOUD, "开始上传")
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = PushButton(FIF.PAUSE, "停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(lambda: self.worker and self.worker.stop())
        self.template_btn = PushButton(FIF.DOCUMENT, "下载名单模板")
        self.template_btn.clicked.connect(self._download_template)
        row2.addWidget(self.confirm_edit, 1)
        row2.addWidget(self.start_btn)
        row2.addWidget(self.stop_btn)
        row2.addWidget(self.template_btn)
        lay.addLayout(row2)

        self.stat = BodyLabel("点「开始上传」自动执行：命名检查 → 压缩 → 按班级分流到对应团支部账号")
        lay.addWidget(self.stat)
        self.ws_note = note_label("")
        lay.addWidget(self.ws_note)

        self.table = TableWidget()
        self.table.setBorderVisible(True)
        self.table.setBorderRadius(8)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["文件", "目标账号", "结果", "说明"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
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

    def _client(self):
        return self.window_ref.settings_page.client()

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
        if result == "成功":
            self.table.item(i, 2).setForeground(QColor("#2e7d32"))
        elif result == "失败":
            self.table.item(i, 2).setForeground(QColor("#c62828"))

    def _start(self):
        if self.confirm_edit.text().strip() != CONFIRM_PHRASE:
            InfoBar.warning("请输入确认短语", f"手输「{CONFIRM_PHRASE}」才会开始真实上传", duration=3000, parent=self)
            return
        files = self._gather_files()
        if not files:
            InfoBar.warning("没有 PDF", "所选目录和工作区里都没有 .pdf 文件", duration=2500, parent=self)
            return

        # 1) 命名检查（纯函数，快）
        parsed: dict[str, tuple[str, str]] = {}
        naming_bad: list[tuple[str, str]] = []
        for p in files:
            a = analyze_file_name(os.path.basename(p))
            if a.parsed:
                parsed[os.path.basename(p)] = a.parsed
            else:
                naming_bad.append((os.path.basename(p), a.problem))

        # 2) 按班级分流
        sp = self.window_ref.settings_page
        accounts = sp.accounts
        creds = {a["userName"]: a["password"] for a in accounts if a.get("password")}
        targets = resolve_file_targets(
            [os.path.basename(p) for p in files], parsed, list(creds), roster_class_lookup(sp.roster))
        target_by_name = {t.filename: t for t in targets}
        unresolved = [t for t in targets if t.problem]

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

        # 4) 无法分流的弹窗提示管理员
        if unresolved:
            lines = "\n".join(f"· {t.filename} — {t.problem}" for t in unresolved[:12])
            box = MessageBox(
                "部分文件无法确定团支部账号",
                f"{len(unresolved)} 个文件缺少班级或对应账号信息：\n\n{lines}"
                f"{'…' if len(unresolved) > 12 else ''}\n\n"
                "请补全名单「班级」列或添加对应账号。点击「继续」只上传可路由的部分，点击「取消」返回修改。",
                self.window(),
            )
            box.yesButton.setText("继续上传可路由部分")
            box.cancelButton.setText("取消")
            box.exec()
            if not box.result():
                return

        # 表格
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
            InfoBar.warning("没有可上传文件", "全部文件都被命名检查或分流拦截，按说明修正后重试", duration=4000, parent=self)
            return

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.worker = UploadWorker(creds, jobs)
        self.worker.row_ready.connect(self._on_row)
        self.worker.stage.connect(lambda s: self.stat.setText(s))
        self.worker.all_done.connect(self._on_done)
        self.worker.start()

    def _on_row(self, i: int, account: str, name: str, ok: bool, reason: str):
        self._set_row(i, name, account, "成功" if ok else "失败", reason)
        self.stat.setText(f"已处理 {i + 1}/{self.table.rowCount()}：{name}")

    def _on_done(self, ok: int, total: int):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._refresh_ws_note()
        InfoBar.success(
            "上传批次结束",
            f"成功 {ok}/{total}。请到智慧团建网页端逐个点击「审核」完成认证（本应用不代点审核）",
            duration=6000, parent=self,
        )
