"""代码反模式检查：每次修复过的故障，都在这里留下一条能自动复现的检查。

和 lint_copy.py 的分工：那边查用户能看到的文案（AI 腔、重复说明），这边查
开发者写下的代码模式。每条规则都标注了它来自哪次真实故障——规则不是凭空
定的，是「同样的错不能再犯一次」的固化。

规则索引（编号 = 注释里的「规则 N」，每条在 tests/test_pattern_lint.py 里
都有「拦住旧写法」+「当前库干净」两个断言）：

     1 getfqdn 反解卡 30 秒          11 配置损坏返回 {} 会抹掉整份
     2 直接绑 0.0.0.0 触发反解       12 文件名未 NFC 归一化
     3 裸 QDialog 弹窗               13 端点缺会话鉴权 / 配对缺 configCode
     4 osascript 拼串丢通知          14 中转整份拉取撞 6MB 墙
     5 subprocess text=True 乱码     15 opencv 4.8.x 未钉 numpy<2
     6 extract_image 整图解码取尺寸  16 冒烟自检漏 import cv2
     7 Windows 材质残留刷白          17 上传绕开压缩副本 work_path
     8 app/ui 硬编码 hex 色          18 扫描未跳过工作目录
     9 打包漏 --collect-all 原生包   19 下载完成自动安装退出
    10 as_completed 收集未重排       20 检查缓存按路径而非内容摘要
    21 QThread.wait() 当 sleep      22 无事件循环线程建 QTimer
    23 配置保存静默吞写盘异常       24 Windows 材质按深浅关掉
    25 readyRead 前未补读缓冲       26 拖放路径未归一

结构：规则 1–14、16–23 在 check_file() 里按编号注释块排布；规则 15 扫非
.py 的部署配置（check_deploy_configs）；手机端 Kotlin 模式与网络安全配置
各有独立检查函数（_kt_problems / _network_problems）。

用法：
    python scripts/lint_patterns.py            # 打印问题并返回非零
    python scripts/lint_patterns.py --quiet    # 只返回码，供测试调用

新增规则的方式：在 check_file()（或对应的独立检查函数）里加一个编号注释块，
并在 tests/test_pattern_lint.py 里同时加「能拦住旧写法」和「当前库干净」的
断言。只加规则不加测试，lint 会变成跑一次就红的摆设。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = (ROOT / "app", ROOT / "scripts")

# 这些文件里的写法是规则允许的例外，理由写在规则上。
# - palette.py：语义色的唯一定义处，hex 只能在它这里出现
# - lan_page.py：发给手机浏览器的内嵌 HTML/CSS，不在 Qt palette 体系内
HEX_ALLOW = {"app/ui/palette.py", "app/services/lan_page.py", "app/ui/widgets.py"}
# widgets.py 里 #ffffff / #1c1208 是标签的固定对比前景色（白字或深字），
# 不是语义色；语义背景色都从 P.TONES/P.BRAND 取。整体豁免该文件。
# page.py 的 banner_text_colors/箭头色、browser_page.py 的 2px 进度条色也是
# 具体绘制用色（渐变头图对比色/fluent 没有的细进度条），不是语义色——见下方
# 的逐行豁免。
# git 命令的输出只有 ASCII 的 commit 元数据（SHA/类型/作者），text=True 无解码风险；
# 真正踩过坑的是 hdiutil attach（卷名是中文），那处已改成显式 UTF-8 解码。
TEXT_ALLOW = {"scripts/release.py"}


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _iter_py():
    for folder in SCAN_DIRS:
        for p in sorted(folder.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            yield p


def _call_name(node: ast.AST) -> str:
    if not isinstance(node, ast.Call):
        return ""
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def check_file(path: Path) -> list[str]:
    """返回该文件的问题列表。规则按修复来源分组，便于追溯。"""
    rel = _rel(path)
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    problems: list[str] = []

    # 规则 1（2026-11-20 手机同步卡死）：绑定通配地址时调用 getfqdn 做反向 DNS。
    # 无 DNS 的局域网里 PTR 查询等满 30s，主线程直接卡死。
    # 允许：函数名/注释里提到 getfqdn（说明为什么跳过它）。
    if re.search(r"socket\.getfqdn\s*\(", source):
        for i, line in enumerate(lines, 1):
            if re.search(r"socket\.getfqdn\s*\(", line) and not line.lstrip().startswith("#"):
                # 真正调用（非注释）才算；注释行和 docstring 内的说明不算命中
                problems.append(
                    f"{rel}:{i} [getfqdn 阻塞] 调用 socket.getfqdn 会做反向 DNS，"
                    "无 DNS 网络卡 30 秒；绑定 0.0.0.0 时只取 server_port，不要反解")

    # 规则 2（2026-11-20）：HTTPServer/ThreadingHTTPServer 直接绑定 0.0.0.0。
    # 默认 server_bind 会走 getfqdn。必须用覆盖过 server_bind 的子类。
    # 例外：子类定义行本身（`class _LanHTTPServer(ThreadingHTTPServer)`）不算。
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{rel}:{exc.lineno} 语法错误，跳过代码检查"]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = _dotted_name(node.func)
        if fn not in ("HTTPServer", "ThreadingHTTPServer") or not node.args:
            continue
        addr = node.args[0]
        if not isinstance(addr, (ast.Tuple, ast.List)) or not addr.elts:
            continue
        first = addr.elts[0]
        if isinstance(first, ast.Constant) and first.value == "0.0.0.0":
            # 允许带 server_bind 覆盖的子类：在本文件里定义了覆盖方法
            has_override = any(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "server_bind" for n in ast.walk(tree))
            if not has_override:
                problems.append(
                    f"{rel}:{node.lineno} [通配绑定阻塞] {fn} 直接绑定 0.0.0.0 会触发 "
                    "getfqdn 反解卡 30 秒；用覆盖过 server_bind 的子类（见 lan_share._LanHTTPServer）")

    # 规则 3（2026-11-20）：自定义弹窗继承裸 QDialog。
    # 项目约定弹窗用 FluentPanelDialog/MessageBoxBase，遮罩/圆角/主题统一，
    # 裸 QDialog 就是那个带系统标题栏、深色发灰的独立窗口。
    # 例外：dialogs.py 里的 QDialog 基类调用（done() 里跳过动画用的是 QDialog.done）。
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                if _dotted_name(base) == "QDialog" and rel != "app/ui/dialogs.py":
                    problems.append(
                        f"{rel}:{node.lineno} [裸 QDialog 弹窗] 自定义弹窗 {node.name} 继承 QDialog，"
                        "会出现带系统标题栏的独立窗口；改用 FluentPanelDialog/MessageBoxBase")

    # 规则 4（中文路径故障）：osascript 把标题/正文拼进 AppleScript 字符串。
    # 中文里的引号、换行会让脚本语法错误，通知静默丢失；必须走 argv。
    if re.search(r"display\s+notification\s*[\"']", source):
        if "on run argv" not in source:
            problems.append(
                f"{rel} [osascript 拼串] display notification 用字符串拼接，"
                "中文引号/换行会让脚本语法错误；改用 `on run argv` + argv 传参")

    # 规则 5（中文路径故障）：subprocess 输出用 text=True 且未显式指定编码。
    # locale 解码会把中文卷名/文件名解成乱码（hdiutil attach 踩过）。
    # 精确判定：同一 subprocess.run 调用里出现 text=True 但没有 encoding=。
    if rel not in TEXT_ALLOW:      # git 输出纯 ASCII，见顶部白名单说明
        for node in ast.walk(tree):
            if _call_name(node) not in ("run", "check_output", "Popen"):
                continue
            kws = {kw.arg for kw in node.keywords}
            if "text" in kws and "encoding" not in kws:
                text_kw = next(kw for kw in node.keywords if kw.arg == "text")
                if isinstance(text_kw.value, ast.Constant) and text_kw.value.value is True:
                    problems.append(
                        f"{rel}:{node.lineno} [subprocess 文本解码] text=True 按 locale 解码，"
                        "中文输出会乱码；改用 capture_output + 显式 decode('utf-8','replace')")

    # 规则 6（性能故障）：用 extract_image 的结果取图像宽高。
    # 它会解码整张 JPEG（单页 ~20ms），只读尺寸应查 xref 的 Width/Height 键。
    # 判定：文件里既有 extract_image、又对它的返回值取 width/height（大小写都算）。
    if "extract_image(" in source and re.search(
            r"\.get\(\s*[\"'](width|height)[\"']|\[[\"'](width|height)[\"']\]", source):
        for node in ast.walk(tree):
            if _call_name(node) == "extract_image":
                problems.append(
                    f"{rel}:{node.lineno} [整图解码取尺寸] extract_image 会解码整张图，"
                    "只读宽高改用 doc.xref_get_key(xref, 'Width'/'Height')")
                break

    # 规则 7（Windows 白屏故障）：窗口材质判决后没清掉旧的 Windows 系统背板。
    # 从浅色切深色时，若不 remove_windows_effect，浅色挂的 DWM 背板继续刷白。
    # 检查：有 windows_material_available 调用的窗口文件，必须能找到 remove_windows_effect。
    if "windows_material_available" in source and "remove_windows_effect" not in source:
        problems.append(
            f"{rel} [材质残留] 用了 Windows 材质却没调用 remove_windows_effect，"
            "切深色时旧背板会继续刷白整窗")

    # 规则 8（文案/代码整洁）：app/ui 下硬编码 hex 色。
    # 语义色统一从 palette 取；palette.py 是定义处，lan_page.py 是手机网页内嵌 CSS。
    if rel.startswith("app/ui/") and rel not in HEX_ALLOW:
        for i, line in enumerate(lines, 1):
            if re.search(r"#[0-9a-fA-F]{6}\b", line) and not line.lstrip().startswith("#"):
                # 排除字符串里刚好像颜色的 URL 片段等极少情况：要求引号内有 #xxxxxx
                if (re.search(r"[\"'][^\"']*#[0-9a-fA-F]{6}\b", line)
                    and "lint-patterns: allow-hex" not in line):
                    problems.append(
                        f"{rel}:{i} [硬编码色] UI 里写死 hex，改从 app.ui.palette 的 token 取")

    # 规则 9（打包故障）：numpy/cv2/pymupdf 这类带原生扩展的包必须整包收集。
    # 只靠 PyInstaller hook 会漏子模块与 dll，Windows 运行期报 multiarray 导入失败。
    if rel == "scripts/build.py":
        for pkg in ("numpy", "cv2", "pymupdf"):
            if f"--collect-all={pkg}" not in source:
                problems.append(
                    f"{rel} [打包漏库] 缺少 --collect-all={pkg}，原生扩展子模块/dll 会漏收，"
                    "Windows 冻结包运行期报导入失败")

    # 规则 10（并发故障）：并发任务收集结果后必须按原始次序重排再提交。
    # 压缩并发回来的顺序不等于用户看到的顺序，不重排会让同支部内提交乱序。
    if "ThreadPoolExecutor" in source and "as_completed" in source and "prepared" in source:
        if "prepared.sort" not in source:
            problems.append(
                f"{rel} [并发乱序] 用 as_completed 收集结果却没按原始次序重排，"
                "用户看到的顺序会乱；保留原始 index，收集后 sort 再提交")

    # 规则 11（静默丢数据）：配置读取失败时不能把 {} 交给写入方。
    # store.load 损坏时必须从备份恢复或显式抛错，否则下一次 save 抹掉整份配置。
    if rel == "app/services/store.py":
        if "raise RuntimeError" not in source:
            problems.append(
                f"{rel} [静默丢数据] 配置读不出来时要显式抛错/从备份恢复，"
                "不能返回 {} 让 save 覆盖原数据")

    # 规则 12（中文文件名故障）：命名解析入口必须先做 NFC 归一化与不可见字符清洗。
    if rel == "app/domain/naming.py":
        if "unicodedata.normalize" not in source or "_INVISIBLE_RE" not in source:
            problems.append(
                f"{rel} [文件名未清洗] 解析前要 NFC 归一化并去掉零宽字符/BOM/全角数字，"
                "否则微信/QQ 存下的文件名会被判「无法解析」")

    # 规则 13（App 配对协议）：配对换 token 后，除 /api/pair 外的端点必须校验会话，
    # 不能用长期有效的配对码直接鉴权文件与状态端点。否则二维码被拍到就能反复上传。
    if path.name == "lan_share.py":
        if "/api/pair" in source and "/api/upload" in source:
            # 每个受保护端点分支里都要出现会话校验（_session(...) 或 share.session）
            if "_session(query)" not in source or "def session(" not in source:
                problems.append(
                    f"{rel} [端点缺会话鉴权] /api/upload 等端点必须用配对后的 token 鉴权，"
                    "不能让配对码直接长期有效")
            # 配对必须校验配置提取码（相同配置的设备才放行），且拒绝原因分开报
            if "/api/pair" in source and "configCode" not in source:
                problems.append(
                    f"{rel} [配对缺配置校验] 配对必须校验 configCode，"
                    "只允许相同配置的设备；脱落这条等于任何扫到码的设备都能传文件")

    # 规则 14（中转故障）：Netlify 响应墙 6MB，中转拉取必须按 part 分片。
    # 整份 pull 单文件超过 6MB 会被网关拒绝，用户看到的是"传了一部分就断"。
    if path.name == "relay_receive.py":
        if 'action": "pull"' in source or "'action': 'pull'" in source:
            if "part" not in source:
                problems.append(
                    f"{rel} [中转整份拉取] Netlify 响应体 6MB，pull 必须按 part 分片拼接，"
                    "整份拉取会超网关限制")

    # 规则 16（2026-11-20 Windows 查空格 ARRAY_API NOTFOUND）：
    # opencv 4.8.1 的 ABI 故障只有走到 cv2 才暴露，窗口能起不代表冻结包没坏。
    # 冒烟自检里若只 import cryptography、没 import cv2，这类错误会漏到用户手上。
    if path.name == "main.py" and "_smoke_test" in source:
        if "import cv2" not in source:
            problems.append(
                f"{rel} [冒烟漏检 cv2] 冒烟自检必须 import cv2 并跑一次原生调用，"
                "否则 numpy 2.x 与 opencv 4.8.1 的 ABI 冲突要等用户点「查空格」才暴露")

    # 规则 17（2026-11-20 预检查流程改造）：上传必须走工作副本 work_path。
    # 原件只读、压缩副本落在「工作目录/压缩」，上传与查空格都读副本；
    # 若退回直接上传 s.path，等于绕过压缩与异常隔离，超限原件会传上去。
    if path.name == "upload_page.py" and "def _start_upload" in source:
        start = source.index("def _start_upload")
        block = source[start:start + 1200]
        if "work_path" not in block:
            problems.append(
                f"{rel} [上传绕开副本] _start_upload 必须筛选并上传 work_path（压缩副本），"
                "原件只读、异常副本不上传；直接用 s.path 会把超限或异常档案传上去")

    # 规则 18（2026-11-20）：扫描来源目录必须跳过「工作目录」子树。
    # 副本是派生产物，与原件同名；扫进去会一份算两遍、越扫越多。
    if path.name == "archive_scan.py" and "def scan_folder" in source:
        if "WORK_DIR_NAME" not in source:
            problems.append(
                f"{rel} [扫描工作目录] 扫描必须跳过工作目录（work_area.WORK_DIR_NAME），"
                "否则压缩副本被当原件重复计数")

    # 规则 19（2026-11-20）：下载完成不得自动安装退出。
    # 安装要退出主程序，用户会在没确认时突然被关掉；改为常驻提示 + 重启按钮，
    # 由用户点「重启」才走 silent_update.start。_on_downloaded 里自己安装就是回退。
    if path.name == "about_card.py" and "def _on_downloaded" in source:
        start = source.index("def _on_downloaded")
        end = source.find("\n    def ", start + 1)
        block = source[start:end if end != -1 else len(source)]
        if "silent_update.start(" in block:
            problems.append(
                f"{rel} [下载即自动安装] _on_downloaded 不能直接调 silent_update.start，"
                "下载完要弹常驻提示等用户点「重启」，否则程序会在用户没确认时自动退出")

    # 规则 20（2026-11-20）：检查缓存必须按内容摘要，不能按路径。
    # 原件会被规范化改名、压缩副本会随参数重做、程序重开会清空内存；
    # 用 (路径, mtime) 当键，上面任一情况都让几百份档案白跑一遍。
    if path.name == "upload_page.py" and "def _cell_key" in source:
        start = source.index("def _cell_key")
        end = source.find("\n    def ", start + 1)
        block = source[start:end if end != -1 else len(source)]
        if "getmtime" in block and "fingerprint" not in block:
            problems.append(
                f"{rel} [检查缓存按路径] _cell_key 必须用内容摘要（check_cache.fingerprint），"
                "用 getmtime 会在改名、重压、重启后全部失效，几百份档案重查一遍")

    # 规则 21（2026-11-21）：QThread.wait() 不能当 sleep 用。
    # wait() 是"等另一个线程结束"（join），从线程自身 run() 里调用属未定义行为：
    # 要么立即返回变成忙轮询，要么阻塞整个周期使 stop() 无法唤醒。周期等待用
    # QWaitCondition（或退一步用 msleep）。只看 QThread 子类的 run 块。
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef)
                and any(isinstance(b, ast.Name) and b.id == "QThread" for b in node.bases)):
            continue
        for fn in node.body:
            if not (isinstance(fn, ast.FunctionDef) and fn.name == "run"):
                continue
            for call in ast.walk(fn):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "wait"
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == "self" and call.args):
                    problems.append(
                        f"{rel}:{call.lineno} [wait 当 sleep] QThread 子类 run() 里 "
                        "self.wait(毫秒) 是 join 语义、从自身调用未定义；周期等待改用 "
                        "QWaitCondition + QMutex，stop() 才能立即唤醒")

    # 规则 22（2026-11-21）：无 Qt 事件循环的线程里不能建 QTimer。
    # LanShare 的 on_received 跑在 ThreadingHTTPServer 请求线程，那里建的
    # QTimer.singleShot 永不触发，收件界面不更新。跨线程回 GUI 用 Signal emit。
    # 用 AST 找真实调用，避免规则自身注释/字符串里的同名文本自命中。
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_on_received"):
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "singleShot"
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "QTimer"):
                problems.append(
                    f"{rel}:{call.lineno} [_on_received 用 QTimer] HTTP 请求线程无 Qt 事件循环，"
                    "QTimer.singleShot 回调永不触发；改用 Signal emit 让 Qt 排回 GUI 线程")

    # 规则 23（2026-11-21）：保存用户设置的写盘失败不能静默吞掉。
    # 配置写不进去（磁盘满/只读/占用）时界面看似保存成功，重启后全丢且无从排查。
    # _save_cloud_state 曾 except: pass，与规则 11「配置读失败不返回 {}」同源。
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef)
                and node.name == "_save_cloud_state"):
            continue
        for handler in [n for n in ast.walk(node) if isinstance(n, ast.ExceptHandler)]:
            if (len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass)):
                problems.append(
                    f"{rel}:{handler.lineno} [保存静默失败] _save_cloud_state 的写盘异常不能 "
                    "except: pass；要写日志并 InfoBar 提示用户，否则设置看似保存、重启全丢")

    # 规则 24（2026-11-21）：Windows 材质不能按深浅模式关掉。
    # 早期 acrylic 走 ACCENT_ENABLE_ACRYLICBLURBEHIND 时深色会刷白，判断里因此加了
    # `and not _dark()`；现在改成 DWMWA_SYSTEMBACKDROP_TYPE 与 HOSTBACKDROP，暗色
    # tint 由 DWM 自己上，这条限制只会把可用的能力关掉（Win11 用户报「模糊没工作」）。
    if re.search(r"windows_material_available\([^)]*\)\s*and\s+not\s+\w*dark\w*\s*\(",
                 source, re.I):
        problems.append(
            f"{rel} [材质被深浅关掉] windows_material_available(...) and not <dark>："
            "深色下材质同样该挂，暗色 tint 由 DWM 处理；旧刷白结论只适用于已废弃的 "
            "ACCENT_ENABLE_ACRYLICBLURBEHIND 通路")

    # 规则 25（2026-11-21）：连 readyRead 之前先看缓冲区里有没有数据。
    # 对端可能在连上信号前写完并断开，此后再无新数据，readyRead 一次都不发，
    # 命令就丢了（Windows 命名管道写得更快，单实例通知必现）。
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        connects_ready = any(
            isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "connect"
            and isinstance(call.func.value, ast.Attribute)
            and call.func.value.attr == "readyRead"
            for call in ast.walk(node))
        if not connects_ready:
            continue
        checks_buffer = any(
            isinstance(n, ast.Attribute) and n.attr == "bytesAvailable"
            for n in ast.walk(node))
        if not checks_buffer:
            problems.append(
                f"{rel}:{node.lineno} [readyRead 未补读] {node.name} 连了 readyRead 却没查 "
                "bytesAvailable：数据若在连信号之前就到了，信号不会再来，命令直接丢失")

    # 规则 26（2026-11-21）：拖放得到的路径必须归一。
    # Qt 在 Windows 上统一把 toLocalFile() 给成正斜杠，而 os.path 产出反斜杠；
    # 不 normpath，同一个目录会被当成两个加进来源列表，档案出现重复行。
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_drop_payload"):
            continue
        uses_local = any(
            isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "toLocalFile"
            for call in ast.walk(node))
        normalized = any(
            isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "normpath"
            for call in ast.walk(node))
        if uses_local and not normalized:
            problems.append(
                f"{rel}:{node.lineno} [拖放路径未归一] _drop_payload 用了 toLocalFile() 但没 "
                "os.path.normpath：Windows 上是正斜杠，与内部路径比较会失配、目录被重复添加")

    return problems


def check_deploy_configs(configs: tuple[Path, ...] | None = None) -> list[str]:
    """规则 15（2026-11-20 Windows 查空格 ARRAY_API NOTFOUND）：
    每条装 opencv-python-headless 4.8.x 的依赖行都必须同时钉 numpy 到 1.x。

    opencv 4.8.1 按 numpy 1.x 的 C ABI 编译；pip 一旦装到 numpy 2.x，冻结包
    import cv2 抛 `_ARRAY_API not found`，逐格查空格整批失效。Win7 两条线
    本来就钉了 numpy<1.25，是这次唯一没中招的——其余四条漏钉。配置不是 .py，
    不在 _iter_py 范围内，单独扫。
    """
    problems: list[str] = []
    # 一条依赖行：同一 pip install / deps 字符串里必须既有 opencv 又有 numpy 钉法
    opencv_re = re.compile(r"opencv-python-headless\s*==\s*4\.8\.")
    numpy_pin_re = re.compile(r"numpy\s*<\s*(?:2|1\.25)")
    if configs is None:
        configs = (ROOT / "requirements.txt",
                   ROOT / ".github" / "workflows" / "release.yml")
    for cfg in configs:
        if not cfg.exists():
            continue
        source = cfg.read_text(encoding="utf-8")
        for i, line in enumerate(source.splitlines(), 1):
            if not opencv_re.search(line):
                continue
            # requirements.txt 里 numpy 钉法是独立一行，按整个文件判断；
            # release.yml 里每条 deps/pip 行自带钉法，按行判断。
            if cfg.name == "requirements.txt":
                ok = numpy_pin_re.search(source) is not None
            else:
                ok = numpy_pin_re.search(line) is not None
            if not ok:
                problems.append(
                    f"{_rel(cfg)}:{i} [numpy 未钉] 装了 opencv-python-headless 4.8.x "
                    "却没钉 numpy<2；numpy 2.x 与 4.8.1 的 ABI 不兼容，"
                    "冻结包 import cv2 抛 _ARRAY_API not found（查空格整批失效）")
    return problems


KT_ROOT = ROOT / "mobile" / "app" / "src" / "main"


def _kt_problems(source: str, rel: str) -> list[str]:
    problems: list[str] = []
    # 扫码无反应：分析器在每帧回调里新建，节流与解码状态每帧丢失
    for m in re.finditer(r"setAnalyzer\([^)]*\)\s*\{([\s\S]{0,400}?)\}", source):
        if re.search(r"Analyzer\s*[({]", m.group(1)):
            line = source.count("\n", 0, m.start()) + 1
            problems.append(
                f"{rel}:{line} [分析器每帧重建] setAnalyzer 回调里新建 Analyzer，"
                "节流与解码状态每帧丢失；把分析器提到 remember 里，只 setAnalyzer(executor, analyzer)")

    # 提示框撑爆：把响应体拼进异常，站点返回 HTML 时整页灌进界面
    for m in re.finditer(r'Exception\([^)]*\$\{?\s*body', source):
        line = source.count("\n", 0, m.start()) + 1
        problems.append(
            f"{rel}:{line} [响应体进提示] 异常信息里直接拼 body，"
            "站点返回网页时会把整页塞进界面；按内容类型给一句可读的原因")
    return problems


def check_kotlin_patterns() -> list[str]:
    problems: list[str] = []
    if not KT_ROOT.exists():
        return problems
    for path in sorted(KT_ROOT.rglob("*.kt")):
        source = path.read_text(encoding="utf-8", errors="replace")
        problems.extend(_kt_problems(source, _rel(path)))
    return problems


def _network_problems(body: str, rel: str) -> list[str]:
    # 只认 base-config：domain-config 覆盖不了任意局域网地址
    if not re.search(r'<base-config[^>]*cleartextTrafficPermitted="true"', body):
        return [
            f"{rel} [局域网明文被拦] 电脑配对请求 http://局域网IP，"
            "系统会直接拒绝；network_security_config 需要放开 cleartext"]
    return []


def check_android_network_config() -> list[str]:
    xml = KT_ROOT / "res" / "xml" / "network_security_config.xml"
    if not xml.exists():
        return []
    return _network_problems(xml.read_text(encoding="utf-8"), _rel(xml))


def run(quiet: bool = False) -> list[str]:
    problems: list[str] = []
    for path in _iter_py():
        problems.extend(check_file(path))
    problems.extend(check_deploy_configs())
    problems.extend(check_kotlin_patterns())
    problems.extend(check_android_network_config())
    if not quiet:
        for line in problems:
            print(line)
        print(f"\n共 {len(problems)} 处" if problems else "\n没有发现问题")
    return problems


if __name__ == "__main__":
    sys.exit(1 if run(quiet="--quiet" in sys.argv) else 0)
