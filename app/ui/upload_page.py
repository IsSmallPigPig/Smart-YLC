"""上传档案：来源目录取件 → 自动检查命名与页数 → 按支部预组织 → 压缩并逐支部提交。

流程：
  1. 来源目录：可以把多个文件夹拖到窗口里（也可以「添加文件夹」逐个加），子文件夹
     一并扫描；文件名解析不出「姓名+手机号」的 PDF 判定为无关文件，只计入忽略数，
     不进上传计划
  2. 文档检查：命名能解析但格式不标准的直接改成标准名；页数不符、命名解析
     不出、路由不到支部的标为异常，上传前拦下
  3. 重复：同一团员有多份档案时每组只留一份，其余标「重复」并排除
  4. 来源目录收在一张折叠卡里，导入的文件夹只是普通卡片；文件统一按团支部
     收进下方折叠卡，未通过的单列一组
  5. 点「开始上传」：先把超限文件压到目标大小，再逐个支部登录提交、自动审核
"""

from __future__ import annotations

import os
import shutil
import tempfile

from PyQt5.QtCore import QRectF, Qt, QThread, QTimer, pyqtSignal as Signal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    FluentIcon as FIF,
    IconWidget,
    InfoBar,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    ToolButton,
    isDarkTheme,
)

from app.constants import LIMITS, MB
from app.domain.account_health import excluded_summary, split_accounts
from app.domain.archive_scan import (FolderScan, abnormal_target, is_abnormal_path,
                                     normal_target, relative_to, scan_folders)
from app.domain.naming import analyze_file_name
from app.domain.upload_inspect import (archived_index, group_by_branch, inspect,
                                       member_index,
                                       split_branch_label, summarize)
from app.domain.upload_routing import upload_skip_reason
from app.services import store
from app.services.workspace import (load_pending, load_workspace_dirs,
                                    remove_pending, save_workspace_dirs)
from app.ui import palette as P
from app.ui.page import (BatchedBuild, BusyOverlay, CollapsibleCard, FluentPage,
                         PageBanner, note_label, page_bg_color,
                         section_title, set_tip)
from app.ui.widgets import PillLabel, ProgressToast, StatusList


class _DropZone(QWidget):
    """空态拖放区：虚线框 + 两行说明，点一下等同「添加文件夹」。"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(112)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(4)
        title = note_label("拖入文件夹")
        f = title.font()
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.DemiBold)
        title.setFont(f)
        # 这是空态的主文案，不能跟说明一样灰：毛玻璃上会整行消失
        title.setTextColor(QColor("#20242b"), QColor("#e6e9ee"))
        lay.addWidget(title, 0, Qt.AlignmentFlag.AlignHCenter)
        hint = note_label("支持多选，自动扫描子文件夹")
        hint.setWordWrap(False)   # 居中单行，折行会把这个框撑成两截
        lay.addWidget(hint, 0, Qt.AlignmentFlag.AlignHCenter)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(P.MUTED))
        pen.setWidth(1)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setColor(QColor(154, 160, 172) if not isDarkTheme() else QColor(96, 102, 112))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class _DropOverlay(QWidget):
    """拖动文件夹经过窗口时盖在内容区的提示层：虚线框 + 一句话说明松手会发生什么。

    文字直接画在 paintEvent 里（不用子控件）：这一层只活几百毫秒，
    交给布局反而会因为它自己的尺寸变化把文案裁掉半行。"""

    TITLE = "松开添加这些文件夹"
    HINT = "子文件夹自动扫描，重复目录跳过"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        box = self.rect().adjusted(10, 10, -10, -10)
        p.setPen(Qt.PenStyle.NoPen)
        # 不透明页面底色：底下那层空态提示的字会从半透明里透出来，两层字叠在一起
        p.setBrush(page_bg_color())
        p.drawRoundedRect(box, 14, 14)

        pen = QPen(QColor(P.BRAND))
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(box.adjusted(2, 2, -2, -2), 14, 14)

        ink = QColor("#12212e") if not isDarkTheme() else QColor("#f2f2f2")
        dim = QColor("#33495e") if not isDarkTheme() else QColor("#c8c8c8")

        title_font = QFont(self.font())
        title_font.setPixelSize(15)
        title_font.setWeight(QFont.Weight.DemiBold)
        hint_font = QFont(self.font())
        hint_font.setPixelSize(12)

        # 标题底边、说明顶边各自贴住中线留 4px：两行才不会挤到一起去
        title_rect = QRectF(box.left(), box.top(), box.width(), box.height() / 2 - 4)
        p.setFont(title_font)
        p.setPen(ink)
        p.drawText(title_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                   self.TITLE)

        hint_rect = QRectF(box.left(), box.center().y() + 4, box.width(), 40)
        p.setFont(hint_font)
        p.setPen(dim)
        p.drawText(hint_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   self.HINT)


class _FolderCard(CardWidget):
    """来源目录里的一张普通卡片：文件夹名、完整路径、份数与移除。

    折叠只发生在「来源目录」这一层；具体文件夹不再各自展开一份文件清单——
    文件已经在下面的支部分组里，再列一遍只会让页面更长。
    """

    def __init__(self, name: str, path: str, tags: list[tuple[str, str]],
                 on_remove=None, parent=None):
        super().__init__(parent)
        self.setBorderRadius(P.RADIUS["control"])
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 10, 10)
        row.setSpacing(12)

        icon = IconWidget(FIF.FOLDER)
        icon.setFixedSize(20, 20)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.title_label = StrongBodyLabel(name or "未命名目录")
        f = self.title_label.font()
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.DemiBold)
        self.title_label.setFont(f)
        text.addWidget(self.title_label)
        self.desc_label = note_label(path)
        text.addWidget(self.desc_label)
        row.addLayout(text, 1)

        self._tags = []
        for text_value, tone in tags:
            pill = PillLabel(str(text_value), tone)
            row.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
            self._tags.append(pill)

        if on_remove is not None:
            remove = ToolButton(FIF.DELETE)
            set_tip(remove, "移除这个来源目录（磁盘文件不动）")
            remove.clicked.connect(lambda _=False: on_remove())
            row.addWidget(remove, 0, Qt.AlignmentFlag.AlignVCenter)


# 站点单文件上限 10MB，统一压到 9.5MB 留余量；不再提供可选项
COMPRESS_TARGET = LIMITS["PDF_DEFAULT_TARGET_BYTES"]
MAX_BATCH = LIMITS["MAX_UPLOAD_PER_BATCH"]
RESULT_TONE = {"成功": "ok", "跳过": "warn", "失败": "error"}


class ScanWorker(QThread):
    """后台扫描：递归收 PDF → 按命名过滤 → 逐份检查（含重复团员）。

    走子目录、读页数、改名都可能慢（几百份档案），放主线程会把界面顶住。"""

    done = Signal(object, object)   # scans, statuses
    failed = Signal(str)

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
            statuses = inspect(files, self.expected_pages, self.accounts, self.roster,
                               members=self.members, archived=self.archived,
                               roots=roots)
            self.done.emit(scans, statuses)
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

                    self.stage.emit(f"上传 {name} 到 {account}")
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
        self.inspector: ScanWorker | None = None
        self.statuses = []
        # 一份档案可能在「来源目录」卡片和「按支部」卡片里各出现一次，
        # 上传进度要同时更新两处，所以每个路径记一组行号
        self._branch_rows: dict[str, list[tuple[StatusList, int]]] = {}
        # 支部卡片分批建：档案多时一次性建几十个卡片会卡住界面（200 份约 580ms）
        self._branch_builder = BatchedBuild(self, batch=2)
        self._busy: BusyOverlay | None = None  # 加载遮罩（渲染/检查期间盖住内容，避免闪屏）
        self._drop_overlay: _DropOverlay | None = None
        # 被标记「存在异常」的档案不参与上传；结构检查发现的异常也会自动加进来
        self._abnormal_paths: set[str] = set()
        self._preview_view = None
        self._phone_dialog = None
        # 逐格检查结果：{路径: 空格数}；-1 表示这项没查（缺 OpenCV 等）
        self._cell_counts: dict[str, int] = {}
        # 按 (路径, 修改时间) 缓存：同一份档案没改过就不重复扫
        self._cell_cache: dict[tuple[str, float], int] = {}
        self._cell_worker = None
        self._cell_total = 0
        self._cell_done = 0
        self._cell_skipped = 0
        self._cell_skip_reason = ""
        self._cell_toast: ProgressToast | None = None
        # 被用户忽略漏填的档案路径：只影响提示，不影响能不能上传
        self._ignored_blanks = {str(p) for p in (store.load().get("ignoredBlanks") or [])}
        self._dirs: list[str] = []          # 来源目录（配置文件里的 workspaceDirs）
        self._scans: list[FolderScan] = []  # 每个来源目录的扫描结果
        self._folder_cards: dict[str, CollapsibleCard] = {}

        lay = self.vBoxLayout
        # 顶部留白让出头图位置（头图通栏贴顶、覆盖在内容之上），其余三边与内容对齐
        lay.setContentsMargins(32, PageBanner.HEIGHT + 16, 32, 24)

        # 头图通栏贴顶（overlay，不占布局），标题与操作控件叠在 banner 上
        self.header = PageBanner("上传档案", "拖入文件夹，自动检查并按支部分流", self.scrollWidget)
        self.banner = self.header
        self.header.set_image_path(store.load().get("headerImage", ""))
        self._place_banner()

        # 窗口任意位置都能接住拖进来的文件夹（拖动时盖一层虚线提示）
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

        self.pick_btn = PushButton(FIF.FOLDER, "添加文件夹")
        self.pick_btn.clicked.connect(self._pick_dirs)
        set_tip(self.pick_btn, "添加来源文件夹；也可以直接把文件夹拖进窗口")
        self.rescan_btn = PushButton(FIF.SYNC, "重新扫描")
        set_tip(self.rescan_btn, "重新扫描所有来源目录，含子文件夹")
        self.rescan_btn.clicked.connect(self.refresh)
        self.upload_btn = PrimaryPushButton(FIF.CLOUD, "开始上传")
        self.upload_btn.setEnabled(False)
        self.upload_btn.clicked.connect(self._start_upload)
        self.stop_btn = PushButton(FIF.PAUSE, "停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(lambda: self.worker and self.worker.stop())
        # 电脑端是管理端，只收不发：手机扫码把档案汇总进来，不提供往手机推文件
        self.phone_btn = PushButton(FIF.DOWNLOAD, "手机接收")
        set_tip(self.phone_btn, "同一个 Wi-Fi 下，手机扫码把 PDF 传进这台电脑（只接收）")
        self.phone_btn.clicked.connect(self._open_phone_sync)
        # 功能按钮统一落在 banner 右下角（不居中）。查空格不设按钮：扫描完自动跑，
        # 进度挂在右下角的浮动进度条上（见 _start_cell_check）
        for w in (self.pick_btn, self.rescan_btn, self.upload_btn,
                  self.stop_btn, self.phone_btn):
            self.header.add_control(w)

        # 已知问题公告（banner 下方）：上传检查出的问题收进这里，不再重复弹窗
        from app.ui.notice_panel import NoticePanel

        self._notice_center = getattr(window, "notice_center", None)
        self.notice_panel = NoticePanel(self._on_notice_action, self)
        lay.addWidget(self.notice_panel)
        if self._notice_center is not None:
            self._notice_center.changed.connect(self._sync_notices)
        self._sync_notices()

        # 概览卡：把「共 N 份：正常…，异常…」那句话换成四个数字。
        # 用户先看数字，再决定要不要往下看清单；说明文字能省就省。
        self.summary_card = CardWidget()
        self.summary_card.setBorderRadius(P.RADIUS["card"])
        summary_lay = QHBoxLayout(self.summary_card)
        summary_lay.setContentsMargins(20, 12, 20, 12)
        summary_lay.setSpacing(0)
        self.stat_values: dict[str, BodyLabel] = {}
        stat_defs = (
            ("total", "档案", "brand"),
            ("ready", "可上传", "success"),
            ("error", "异常", "danger"),
            ("branches", "支部", "muted"),
        )
        for index, (key, label, role) in enumerate(stat_defs):
            if index:
                line = QWidget(self.summary_card)
                line.setFixedWidth(1)
                line.setStyleSheet(
                    "background-color: rgba(255,255,255,26);"
                    if isDarkTheme() else "background-color: rgba(0,0,0,24);")
                summary_lay.addWidget(line)
            block = QVBoxLayout()
            block.setSpacing(2)
            num = BodyLabel("0")
            f = num.font()
            f.setPixelSize(20)
            f.setWeight(QFont.Weight.DemiBold)
            num.setFont(f)
            c = P.fg(role)
            num.setTextColor(c, c)
            block.addWidget(num, 0, Qt.AlignmentFlag.AlignHCenter)
            block.addWidget(note_label(label), 0, Qt.AlignmentFlag.AlignHCenter)
            summary_lay.addLayout(block, 1)
            self.stat_values[key] = num
        lay.addWidget(self.summary_card)

        self.status_line = note_label("")
        self.status_line.setWordWrap(False)
        lay.addWidget(self.status_line)

        # ---------- 来源目录 ----------
        # 折叠发生在「来源目录」这一层；每个导入的文件夹只是下面的一张普通卡片。
        self.source_card = CollapsibleCard("来源目录", expanded=True, parent=self.scrollWidget)
        self.folder_lay = self.source_card.body_lay
        self.folder_lay.setContentsMargins(0, 0, 0, 0)
        self.folder_lay.setSpacing(8)
        lay.addWidget(self.source_card)

        self.empty_box = QWidget()
        empty_lay = QVBoxLayout(self.empty_box)
        empty_lay.setContentsMargins(0, 0, 0, 0)
        self.drop_zone = _DropZone(self.empty_box)
        self.drop_zone.clicked.connect(self._pick_dirs)
        empty_lay.addWidget(self.drop_zone)
        lay.addWidget(self.empty_box)

        # ---------- 按支部 ----------
        self.branch_title = section_title("待上传档案")
        lay.addSpacing(6)
        lay.addWidget(self.branch_title)
        self._empty_text = "还没有添加来源目录"

        self.branch_box = QWidget()
        self.branch_lay = QVBoxLayout(self.branch_box)
        self.branch_lay.setContentsMargins(0, 0, 0, 0)
        self.branch_lay.setSpacing(10)
        lay.addWidget(self.branch_box)
        lay.addStretch(1)
        # 首帧先按空态显示；showEvent 的 refresh 会根据 workspaceDirs 决定展开哪一块
        self.summary_card.setVisible(False)
        self.source_card.setVisible(False)
        self.branch_title.setVisible(False)

    # ---------- 数据来源 ----------

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_banner()
        self._place_busy()
        self._place_drop_overlay()

    def showEvent(self, event):
        super().showEvent(event)
        self._place_banner()
        self._place_busy()
        self.refresh()

    def _place_banner(self):
        """头图几何：铺满页面宽度、贴顶，跟随窗口宽度变化。"""
        if getattr(self, "header", None) is not None:
            self.header.setGeometry(0, 0, self.scrollWidget.width(), PageBanner.HEIGHT)

    # ---------- 加载遮罩 ----------

    def _show_busy(self, text: str = "正在加载…"):
        """检查/渲染期间盖住内容区，避免空白与闪屏。"""
        if self._busy is None:
            self._busy = BusyOverlay(self, text)
        self._busy.set_text(text)
        self._place_busy()
        self._busy.ring.start()
        self._busy.show()
        self._busy.raise_()

    def _hide_busy(self):
        if self._busy is not None:
            self._busy.ring.stop()
            self._busy.hide()

    def _place_busy(self):
        if self._busy is None:
            return
        # 只盖住 banner 以下的内容区，头图保持可见
        top = PageBanner.HEIGHT
        self._busy.setGeometry(0, top, self.viewport().width(),
                               max(0, self.viewport().height() - top))
        self._busy.raise_()

    def _expected_pages(self) -> int:
        return self.window_ref.settings_page.expected_pages()

    def _accounts(self) -> list[dict]:
        return list(self.window_ref.settings_page.accounts)

    def _roster(self):
        return self.window_ref.settings_page.roster

    # ---------- 来源目录 ----------

    def _pick_dirs(self):
        """选文件夹加进来源列表；系统面板支持多选时一次能加多个。"""
        dlg = QFileDialog(self, "选择来源文件夹（可多选）")
        dlg.setFileMode(QFileDialog.FileMode.Directory)
        dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
        if dlg.exec():
            self.add_dirs(dlg.selectedFiles())

    def add_dirs(self, paths: list[str]) -> int:
        """把文件夹加进来源列表并重扫；返回真正新增的个数。

        已在列表里的、以及位于已添加目录之下的都跳过——重复添加只会让同一份
        档案在列表里出现两次，重复团员的提示就没法看了。"""
        current = list(self._dirs) or load_workspace_dirs()
        added, skipped = [], 0
        for raw in paths:
            path = os.path.abspath(str(raw or "").strip())
            if not path or not os.path.isdir(path):
                continue
            covered = any(path == d or path.startswith(d.rstrip(os.sep) + os.sep)
                          for d in current + added)
            if covered:
                skipped += 1
                continue
            added.append(path)

        if not added:
            if skipped:
                InfoBar.info("没有添加新目录",
                             "选中的文件夹已在列表里，或被已添加的目录包含",
                             duration=4000, parent=self)
            return 0

        self._dirs = save_workspace_dirs(current + added)
        self._sync_settings_dirs()
        if skipped:
            InfoBar.info("已跳过重复目录", f"新增 {len(added)} 个，跳过 {skipped} 个",
                         duration=4000, parent=self)
        self.refresh()
        return len(added)

    def remove_dir(self, path: str):
        """从列表移除一个来源目录；磁盘上的文件一个都不动。"""
        self._dirs = save_workspace_dirs([d for d in self._dirs if d != path])
        self._sync_settings_dirs()
        self.refresh()

    def _sync_settings_dirs(self):
        """把目录列表的变更同步给设置页：那里只显示第一个目录、负责挂文件监听。

        不这么做，设置页缓存的还是旧的单值，下一次保存设置就会拿它反推列表。
        """
        settings = getattr(self.window_ref, "settings_page", None)
        if settings is None or not hasattr(settings, "_setup_watcher"):
            return
        settings.workspace_dir = self._dirs[0] if self._dirs else ""
        settings._setup_watcher(self._dirs)

    # 拖放：窗口任意位置都能接住文件夹（也接住 PDF，按它所在的文件夹算）

    @staticmethod
    def _drop_payload(mime) -> tuple[list[str], list[str]]:
        dirs: list[str] = []
        parents: list[str] = []
        if not mime.hasUrls():
            return dirs, parents
        for url in mime.urls():
            path = url.toLocalFile()
            if not path:
                continue
            if os.path.isdir(path):
                dirs.append(path)
            elif path.lower().endswith(".pdf") and os.path.isfile(path):
                parents.append(os.path.dirname(path))
        return dirs, parents

    def _has_drop(self, mime) -> bool:
        dirs, parents = self._drop_payload(mime)
        return bool(dirs or parents)

    def dragEnterEvent(self, e):
        if self._has_drop(e.mimeData()):
            e.acceptProposedAction()
            self._show_drop_hint(True)
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._has_drop(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._show_drop_hint(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self._show_drop_hint(False)
        dirs, parents = self._drop_payload(e.mimeData())
        if not dirs and not parents:
            e.ignore()
            return
        e.acceptProposedAction()
        self.add_dirs(dirs + parents)

    def _show_drop_hint(self, active: bool):
        if active:
            if self._drop_overlay is None:
                self._drop_overlay = _DropOverlay(self)
            self._place_drop_overlay()
            self._drop_overlay.show()
            self._drop_overlay.raise_()
        elif self._drop_overlay is not None:
            self._drop_overlay.hide()

    def _place_drop_overlay(self):
        """只盖内容区：头图（以及上面的标题与按钮）保持在提示层之外。"""
        if self._drop_overlay is None:
            return
        top = PageBanner.HEIGHT
        self._drop_overlay.setGeometry(0, top, self.viewport().width(),
                                       max(0, self.viewport().height() - top))
        self._drop_overlay.raise_()

    # ---------- 扫描 ----------

    def _set_stats(self, total: int, ready: int, error: int, branches: int):
        for key, value in (("total", total), ("ready", ready),
                           ("error", error), ("branches", branches)):
            self.stat_values[key].setText(str(value))

    def _set_status(self, text: str):
        self.status_line.setText(text or "")
        self.status_line.setVisible(bool(text))

    def refresh(self):
        dirs = load_workspace_dirs()
        self._dirs = dirs
        self._clear_inspect_error()
        if not dirs:
            self._scans = []
            self.statuses = []
            # 空态只说一遍：拖放区已经说明了下一步，概览与分区标题都收起来
            self._empty_text = ""
            self.summary_card.setVisible(False)
            self._set_status("")
            self.branch_title.setVisible(False)
            self.upload_btn.setEnabled(False)
            self._rebuild_sources()
            self._rebuild_branches()
            return

        self.branch_title.setVisible(True)
        self.summary_card.setVisible(True)
        # 目录卡片按已保存的目录列表先画出来，不等扫描：扫描线程失败时（缺库、
        # 目录被占用）它照样在，否则用户看到的是一片空态，以为目录没保存
        self._rebuild_sources()
        self._set_stats(0, 0, 0, 0)
        self.rescan_btn.setEnabled(False)
        self._set_status("正在扫描来源目录…")
        self._show_busy("正在扫描与检查档案…")
        cached_members = (store.load().get("memberCache") or {})
        worker = ScanWorker(dirs, self._expected_pages(),
                            self._accounts(), self._roster(),
                            member_index(cached_members),
                            archived_index(cached_members))
        self.inspector = worker
        worker.done.connect(
            lambda scans, statuses, w=worker: self._on_inspected(w, scans, statuses))
        worker.failed.connect(lambda msg, w=worker: self._on_inspect_failed(w, msg))
        worker.start()

    def _on_inspected(self, worker, scans, statuses):
        if worker is not self.inspector:
            return      # 连点刷新时，先发的那次晚回来，不能盖掉新结果
        QTimer.singleShot(0, lambda: self._apply_statuses(list(scans), list(statuses)))

    def _on_inspect_failed(self, worker, message: str):
        """检查失败不弹一次就没了：这类错误会一直存在，收进公告等它被修好。"""
        if worker is not self.inspector:
            return
        self._hide_busy()
        self.rescan_btn.setEnabled(True)
        self._set_status("")
        self._set_inspect_error(message)

    def _set_inspect_error(self, message: str):
        center = getattr(self, "_notice_center", None)
        if center is None:
            InfoBar.error("档案检查失败", message, duration=8000, parent=self)
            return
        from app.services.notices import Notice

        center.upsert(Notice(
            key="upload:inspect", title="档案检查失败", detail=message,
            action="", action_text="", tone="error", silent=True, scope="upload"))

    def _clear_inspect_error(self):
        center = getattr(self, "_notice_center", None)
        if center is not None:
            center.remove("upload:inspect")

    def _apply_statuses(self, scans, statuses):
        self.rescan_btn.setEnabled(True)
        self._scans = scans
        self.statuses = statuses
        self.summary_card.setVisible(True)
        self.source_card.setVisible(True)
        self.branch_title.setVisible(True)
        self.empty_box.setVisible(False)
        stats = summarize(statuses)
        ignored = sum(len(scan.ignored) for scan in scans)
        missing_dirs = [scan for scan in scans if scan.missing]

        ready = sum(1 for s in statuses if s.usable)
        self._set_stats(stats["total"], ready, stats["error"], stats["branches"])
        notes = []
        if ignored:
            notes.append(f"另有 {ignored} 个 PDF 已忽略")
        if missing_dirs:
            notes.append(f"{len(missing_dirs)} 个目录已不存在")
        self._set_status("，".join(notes))

        self._empty_text = "还没有可上传的档案"
        self.upload_btn.setEnabled(any(s.usable for s in statuses))
        self._sync_abnormal(statuses)
        alive = {str(s.path) for s in statuses}
        self._cell_counts = {p: n for p, n in self._cell_counts.items() if p in alive}
        self._cell_cache = {k: n for k, n in self._cell_cache.items() if k[0] in alive}
        self._rebuild_sources()
        # 卡片建完再收起加载遮罩，避免中途露出半截内容导致闪屏
        self._rebuild_branches(on_done=self._after_branches)
        self._set_missing_notice(stats["missing"])
        self._set_duplicate_notice(stats["duplicate"])

    def _after_branches(self):
        """卡片建完：收起加载遮罩，接着自动跑逐格检查（不占按钮、不锁交互）。"""
        self._hide_busy()
        self._start_cell_check()

    def _sync_abnormal(self, statuses):
        """异常以磁盘位置为准：「异常档案」文件夹里的一律算异常。

        标记异常会把文件移进去，所以每次扫描都按路径重建；移动失败（只读盘等）
        时退回内存标记，这里把还存在的旧路径保留下来，别被重扫冲掉。"""
        auto = {str(s.path) for s in statuses if is_abnormal_path(str(s.path))}
        keep = {p for p in self._abnormal_paths if os.path.isfile(p)}
        self._abnormal_paths = auto | keep

    # ---------- 来源目录 ----------

    def _rebuild_sources(self):
        """来源目录是一张折叠卡，里面的每个文件夹只是普通卡片。"""
        while self.folder_lay.count():
            item = self.folder_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._folder_cards = {}

        if not self._dirs:
            self.source_card.setVisible(False)
            self.empty_box.setVisible(True)
            return
        self.source_card.setVisible(True)
        self.empty_box.setVisible(False)
        self.source_card.set_tags([(f"{len(self._dirs)} 个", "gray")])
        for scan in self._scans or [FolderScan(root=d) for d in self._dirs]:
            self._add_folder_card(scan)

    def _add_folder_card(self, scan: FolderScan):
        statuses = [s for s in self.statuses if s.root == scan.root]
        if scan.missing:
            tags = [("目录不存在", "orange")]
        else:
            tags = [(f"{len(statuses)} 份", "gray")]
            if scan.ignored:
                tags.append((f"忽略 {len(scan.ignored)}", "gray"))
        card = _FolderCard(
            scan.name, scan.root, tags,
            on_remove=(lambda r=scan.root: self.remove_dir(r)) if scan.root else None,
            parent=self.source_card.body)
        self.folder_lay.addWidget(card)
        self._folder_cards[scan.root] = card

    def _set_missing_notice(self, missing: int):
        """缺团员只进公告列表（不重复弹窗）；数量变 0 时移除。"""
        center = getattr(self, "_notice_center", None)
        if center is None:
            return
        if not missing:
            center.remove("upload:missing")
            return
        from app.services.notices import Notice

        center.upsert(Notice(
            key="upload:missing",
            title=f"名单缺团员 {missing} 份",
            detail="仍可上传；名单可到组织管理核对",
            action="org",
            action_text="去组织管理",
            tone="warning",
            silent=True,
            scope="upload",
        ))

    def _set_duplicate_notice(self, count: int):
        """重复档案只进公告列表：数量变 0 时移除。"""
        center = getattr(self, "_notice_center", None)
        if center is None:
            return
        if not count:
            center.remove("upload:duplicate")
            return
        from app.services.notices import Notice

        center.upsert(Notice(
            key="upload:duplicate",
            title=f"重复档案 {count} 份",
            detail="每组只保留一份；删掉多余的再重新扫描",
            action="",
            action_text="",
            tone="warning",
            silent=True,
            scope="upload",
        ))

    def _sync_notices(self):
        """只列与上传有关的问题：账号异常在系统配置处理，不该出现在这页。"""
        center = getattr(self, "_notice_center", None)
        panel = getattr(self, "notice_panel", None)
        if center is None or panel is None:
            return
        panel.set_notices([n for n in center.snapshot()
                           if n.scope in ("", "upload")])

    def _on_notice_action(self, action: str):
        handler = getattr(self.window_ref, "handle_notice_action", None)
        if handler is not None:
            handler(action)

    def set_search(self, text: str):
        """标题栏搜索框：按文件名、支部或问题说明过滤下方折叠卡片。"""
        text = (text or "").strip()
        if text == getattr(self, "_search", ""):
            return
        self._search = text
        self._rebuild_branches()

    # ---------- 逐格检查（扫描后自动跑） ----------

    def _cell_key(self, path: str) -> tuple[str, float]:
        """缓存键：路径 + 修改时间。档案没改过就不重复扫几百页。"""
        try:
            return (path, os.path.getmtime(path))
        except OSError:
            return (path, 0.0)

    def _start_cell_check(self):
        """逐格查表格空格：扫描完自动在后台跑，结果写进每行说明。

        进度挂在右下角浮动进度条上（ProgressToast），不占页面、不锁交互。
        同一份档案按 (路径, 修改时间) 缓存，没改过就跳过。"""
        from app.services.form_cells import check as check_cells

        paths = [str(s.path) for s in self.statuses
                 if self._cell_key(str(s.path)) not in self._cell_cache]
        if not paths:
            return
        if self._cell_worker is not None and self._cell_worker.isRunning():
            return

        self._cell_total = len(paths)
        self._cell_done = 0
        self._cell_skipped = 0
        self._cell_skip_reason = ""
        self._cell_toast = ProgressToast("查空格", self.window())
        self._cell_toast.set_progress(0, self._cell_total)
        self._cell_toast.show()

        class _Worker(QThread):
            one = Signal(str, int, str)   # path, 空格数（-1=没查成）, 跳过原因
            finished_all = Signal()

            def run(inner):
                for path in paths:
                    try:
                        result = check_cells(path)
                        count = -1 if result.skipped else len(result.blanks)
                        reason = result.skipped
                    except Exception as exc:   # noqa: BLE001 - 单份失败不拖垮整批
                        count, reason = -1, str(exc)
                    inner.one.emit(path, count, reason)
                inner.finished_all.emit()

        worker = _Worker(self)
        worker.one.connect(self._on_cell_count)
        worker.finished_all.connect(self._on_cells_done)
        self._cell_worker = worker
        worker.start()

    def _on_cell_count(self, path: str, count: int, reason: str = ""):
        if count >= 0:
            self._cell_counts[path] = count
            self._cell_cache[self._cell_key(path)] = count
        else:
            self._cell_skipped += 1
            self._cell_skip_reason = reason or self._cell_skip_reason
        self._cell_done += 1
        if self._cell_toast is not None:
            self._cell_toast.set_progress(self._cell_done, self._cell_total)

    def _on_cells_done(self):
        if self._cell_toast is not None:
            self._cell_toast.finish()
            self._cell_toast = None
        # 用已有扫描结果重排卡片，不重新扫目录
        self._rebuild_branches()
        checked = self._cell_total - self._cell_skipped
        flagged = sum(1 for n in self._cell_counts.values() if n > 0)
        if not checked:
            # 一份都没查成是环境问题（缺渲染组件），不能报「没有空格」
            InfoBar.warning("逐格检查没跑起来",
                            self._cell_skip_reason or "缺少 PDF 渲染组件或 OpenCV",
                            duration=6000, parent=self)
        elif flagged:
            InfoBar.info("逐格检查完成", f"{flagged} 份档案的表格里有空格，说明见每行下方",
                         duration=5000, parent=self)
        else:
            InfoBar.success("逐格检查完成", "没有发现空格", duration=4000, parent=self)

    # ---------- 手机同步 ----------

    def _open_phone_sync(self):
        """局域网直传：手机扫码上传，收到的 PDF 自动进待上传列表。"""
        from app.ui.phone_sync_dialog import PhoneSyncDialog

        if self._phone_dialog is not None and self._phone_dialog.isVisible():
            self._phone_dialog.raise_()
            self._phone_dialog.activateWindow()
            return
        dlg = PhoneSyncDialog(self.window())
        dlg.file_received.connect(self._on_phone_file)
        self._phone_dialog = dlg
        dlg.show()

    def _on_phone_file(self, path: str):
        """手机传来的文件落到「同步自手机」后，立刻刷新列表让它出现在待上传里。"""
        self.refresh()
        InfoBar.success("已收到手机档案", os.path.basename(path),
                        duration=4000, parent=self)

    # ---------- 档案预览详情 ----------

    def _open_preview(self, path: str):
        """点档案行 -> 全屏详情页：面包屑 + PDF 预览 + 结构检查 + 异常标记。"""
        from app.ui.archive_preview import ArchivePreviewView

        status = next((s for s in self.statuses if str(s.path) == path), None)
        name = status.name if status else os.path.basename(path)
        self._show_preview(path, name)

    def _show_preview(self, path: str, name: str):
        """点档案行 -> 上传页内的子页面：面包屑 + PDF 预览 + 结构检查 + 异常标记。

        不开独立窗口：子页面压进主窗口的页面栈，返回走面包屑「上传档案」。
        这一层不挂 banner——头图只属于上传列表。
        """
        from app.ui.archive_preview import ArchivePreviewView

        if not os.path.isfile(path):
            InfoBar.warning("文件不在了", path, duration=4000, parent=self)
            return
        if self._preview_view is not None:
            self._close_preview()
        view = ArchivePreviewView(
            self, path, name, expected_pages=self._expected_pages(),
            on_back=self._close_preview,
            on_mark_abnormal=lambda p, bad: self._mark_abnormal(p, bad),
            on_ignore_blanks=self.ignore_blanks,
            blanks_ignored=path in self._ignored_blanks,
        )
        if path in self._abnormal_paths:
            view._set_abnormal(True)
        window = self.window_ref
        window.stackedWidget.addWidget(view)
        window.switchTo(view)
        self._preview_view = view

    def _close_preview(self):
        """从详情页回到上传列表，并把子页面连同后台线程一起收掉。"""
        view = self._preview_view
        self._preview_view = None
        if view is None:
            return
        window = self.window_ref
        if window.stackedWidget.currentWidget() is view:
            window.switchTo(self)
        view.stop_workers()
        window.stackedWidget.removeWidget(view)
        view.deleteLater()

    def ignore_blanks(self, path: str):
        """忽略这份档案的漏填提示：只影响提示，不影响上传判断。"""
        path = str(path)
        if path in self._ignored_blanks:
            return
        self._ignored_blanks.add(path)
        store.update(lambda cfg: cfg.__setitem__(
            "ignoredBlanks", sorted(self._ignored_blanks)))
        self._rebuild_branches()

    def _mark_abnormal(self, path: str, bad: bool):
        """标记/取消异常：文件跟着搬进或搬出「异常档案」子文件夹。

        异常状态不另存清单——文件夹就是清单：拷走来源目录、换电脑都带着走，
        扫描时该文件夹下的一律归入异常。"""
        if bad == is_abnormal_path(path):
            return
        target = self._relocate(path, bad)
        if target is None:
            # 移动失败（只读盘、文件被占用）退回内存标记：至少这一轮不上传
            if bad:
                self._abnormal_paths.add(path)
            else:
                self._abnormal_paths.discard(path)
            return

        if self._preview_view is not None and self._preview_view.path == path:
            self._preview_view.retarget(target)
        self._cell_counts.pop(path, None)      # 路径变了，旧的空格结果作废
        InfoBar.success("已移入「异常档案」" if bad else "已移出「异常档案」",
                        os.path.basename(target), duration=3000, parent=self)
        self.refresh()

    def _relocate(self, path: str, bad: bool) -> str | None:
        """把档案搬进/搬出「异常档案」；返回新路径，失败提示并返回 None。"""
        import shutil

        target = abnormal_target(path) if bad else normal_target(path)
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(path, target)
        except OSError as exc:
            InfoBar.error("移动失败", f"{os.path.basename(path)}：{exc}",
                          duration=6000, parent=self)
            return None
        return target

    def _row_item(self, status) -> dict:
        """把一份档案的状态翻译成折叠卡片里一行的展示数据。"""
        meta = []
        if status.pages:
            meta.append(f"{status.pages} 页")
        if status.root:
            sub = os.path.dirname(relative_to(status.root, status.path))
            if sub:
                meta.append(sub)   # 子文件夹里的档案：标出它在哪一层
        note = status.problem
        if note and "名单中找不到" in note:
            note = "名单中未找到该团员"
        if not note and status.renamed_from:
            note = "已自动改名"
        if not note:
            note = status.notice
        blanks = self._cell_counts.get(str(status.path))
        ignored_blanks = str(status.path) in self._ignored_blanks
        extra_pills = []
        if blanks is not None:
            if blanks <= 0:
                hint = "表格无空格"
            elif ignored_blanks:
                hint = f"已忽略 {blanks} 处空格"
            else:
                hint = f"表格 {blanks} 处空格"
            note = f"{note}，{hint}" if note else hint
            if blanks > 0 and not ignored_blanks:
                # 漏填单独一个黄标签：档案本身可能只是「名单里没找到」，
                # 跟表格漏填是两件事，混在一个红标签里看不出来
                extra_pills.append(("漏填", "warning", "warning"))
        # 已经按支部分组，行里不再重复「目标支部」；只有归不了组的才把支部当元信息
        if status.branch and status.problem:
            meta.append(status.branch)
        marked = str(status.path) in self._abnormal_paths
        if marked:
            note = f"{note}，已标记异常" if note else "已标记异常，不参与上传"
        # 黄底只表示「这一行有漏填/被人工标记」。普通的结构异常（例如名单里
        # 没找到该团员）已经有红色「异常」标签，整片铺黄反而把真正要看的
        # 漏填盖住了。
        abnormal = marked or (blanks is not None and blanks > 0 and not ignored_blanks)
        return {
            "title": status.name,
            "path": str(status.path),
            "note": note,
            # tone 只决定标签颜色，必须用 PillLabel 认识的取值（ok/warn/error/
            # muted）。写 "warning" 会落进默认的灰色，标记异常的档案就认不出来
            "tone": status.tone,
            # 「已标记」只在真的移进异常档案时出现；结构异常仍是「异常」
            "status": "已标记" if marked else status.status_text,
            "meta": meta,
            "abnormal": abnormal,
            "extra_pills": extra_pills,
        }

    def _rebuild_branches(self, on_done=None):
        self._branch_builder.cancel()
        while self.branch_lay.count():
            item = self.branch_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
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
            if empty:      # 空态文案为空表示「已经由上面的拖放区说明过了」
                self.branch_lay.addWidget(note_label(empty))
            if on_done is not None:
                on_done()
            return
        # 卡片分批建：首批同步（首帧就有内容），其余按帧补齐；整批建完回调 on_done
        self._branch_builder.start(groups.items(), self._add_branch_card, on_done=on_done)

    def _add_branch_card(self, group: tuple[str, list]):
        """建一个支部折叠卡片；由 BatchedBuild 逐批调用。"""
        branch, bstatuses = group
        stage, name = split_branch_label(branch)
        tags = ([(stage, "gray")] if stage else []) + [(f"{len(bstatuses)} 份", "gray")]
        archived = sum(1 for s in bstatuses if s.archive)
        if archived:
            tags.append((f"已有档案 {archived}", "gray"))
        todo = sum(1 for s in bstatuses if not s.usable and not s.archive)
        if todo:
            tags.append((f"{todo} 待处理", "orange"))
        missing = sum(1 for s in bstatuses if s.usable and s.member == "missing")
        if missing:
            tags.append((f"缺团员 {missing}", "orange"))
        card = CollapsibleCard(name or branch, expanded=True, tags=tags)
        listing = StatusList()
        listing.set_items([self._row_item(s) for s in bstatuses])
        listing.rowClicked.connect(self._open_preview)
        card.body_lay.addWidget(listing)
        self.branch_lay.addWidget(card)
        for row_index, s in enumerate(bstatuses):
            self._branch_rows.setdefault(s.path, []).append((listing, row_index))

    # ---------- 上传 ----------

    def _start_upload(self):
        usable = [s for s in self.statuses
                  if s.usable and str(s.path) not in self._abnormal_paths]
        skipped = len([s for s in self.statuses if s.usable]) - len(usable)
        if skipped:
            InfoBar.info("已跳过异常档案", f"{skipped} 份被标记为异常，未加入本次上传",
                         duration=4000, parent=self)
        if not usable:
            InfoBar.warning("没有可上传档案", "先处理状态列表里的异常文件",
                            duration=4000, parent=self)
            return
        accounts, excluded = split_accounts(self._accounts())
        if excluded:
            InfoBar.warning("跳过异常账号",
                            f"{excluded_summary(excluded)}，本轮不参与上传；测活恢复正常后自动纳入",
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
                for listing, row_index in self._branch_rows.get(status.path, []):
                    listing.set_row_state(row_index, "等待", "muted")
        self.upload_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.rescan_btn.setEnabled(False)
        target = COMPRESS_TARGET
        self.worker = UploadWorker(creds, jobs, target)
        self.worker.row_ready.connect(self._on_row)
        self.worker.stage.connect(self._set_status)
        self.worker.all_done.connect(self._on_done)
        self.worker.start()

    def _on_row(self, account: str, path: str, result: str, reason: str):
        QTimer.singleShot(0, lambda: self._apply_row(account, path, result, reason))

    def _apply_row(self, account: str, path: str, result: str, reason: str):
        for listing, row_index in self._branch_rows.get(path, []):
            listing.set_row_state(row_index, result,
                                  RESULT_TONE.get(result, "muted"), reason)
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
