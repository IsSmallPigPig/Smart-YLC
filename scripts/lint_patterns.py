"""代码反模式检查：每次修复过的故障，都在这里留下一条能自动复现的检查。

和 lint_copy.py 的分工：那边查用户能看到的文案（AI 腔、重复说明），这边查
开发者写下的代码模式。每条规则都标注了它来自哪次真实故障——规则不是凭空
定的，是「同样的错不能再犯一次」的固化。

用法：
    python scripts/lint_patterns.py            # 打印问题并返回非零
    python scripts/lint_patterns.py --quiet    # 只返回码，供测试调用

新增规则的方式：在 RULES 里加一条，并在 tests/test_pattern_lint.py 里
同时加「能拦住旧写法」和「当前库干净」的断言。只加规则不加测试，lint 会
变成跑一次就红的摆设。
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
    # 规则（2026-11-20 扫码无反应）：setAnalyzer 的回调里现场 new 分析器。
    # 该回调每帧执行，分析器里的节流与解码器状态全部作废，帧率被打满。
    for m in re.finditer(r"setAnalyzer\([^)]*\)\s*\{([\s\S]{0,400}?)\}", source):
        if re.search(r"Analyzer\s*[({]", m.group(1)):
            line = source.count("\n", 0, m.start()) + 1
            problems.append(
                f"{rel}:{line} [分析器每帧重建] setAnalyzer 回调里新建 Analyzer，"
                "节流与解码状态每帧丢失；把分析器提到 remember 里，只 setAnalyzer(executor, analyzer)")

    # 规则（2026-11-21 旋转闪退）：显示中的位图一旦交给 Compose 状态，
    # 绘制队列可能还在用；此时 recycle() 会让下一帧直接崩在渲染线程。
    # 临时位图（source/result/bitmaps 等局部对象）回收是合法的，不在此列。
    for m in re.finditer(r"\b(?:preview|display\w*|shown|visible)\w*\??\.recycle\(\)", source):
        line = source.count("\n", 0, m.start()) + 1
        problems.append(
            f"{rel}:{line} [显示位图提前回收] 显示中的位图不能 recycle()："
            "渲染队列仍可能读它，旋转时会闪退；让 Compose 缓存自然释放")

    # 规则（2026-11-21 侧拍照片）：相机 JPEG 带 EXIF 方向，ScanSession 是唯一的
    # 入库解码点；这里不应用方向，侧拍照片进入裁切时就是歪的。
    # 已校正图的再次解码（CropEditor）、验证码（LoginScreen）不走这条。
    if rel.endswith("ScanSession.kt") and re.search(r"BitmapFactory\.decode(File|Stream|ByteArray)", source) \
            and "ExifInterface" not in source:
        line = source.count("\n", 0, re.search(r"BitmapFactory\.decode", source).start()) + 1
        problems.append(
            f"{rel}:{line} [EXIF 未应用] 相机 JPEG 解码后必须过 ExifInterface 旋转/翻转，"
            "否则侧拍照片进入裁切时就是歪的")

    # 规则（2026-11-21 旋转后识别框错位）：影响渲染效果的 remember/LaunchedEffect
    # 若只以页面 id 为 key，旋转后不会重算预览与识别框，框与图错位。
    if "page.rotation" in source:
        for m in re.finditer(r"LaunchedEffect\(([^)]*)\)", source):
            key = m.group(1)
            if "page.id" in key and "rotation" not in key:
                line = source.count("\n", 0, m.start()) + 1
                problems.append(
                    f"{rel}:{line} [旋转未进 effect key] LaunchedEffect 用了 page.id 但漏了 rotation，"
                    "点旋转后预览/识别框不重算会错位；key 里补上 page.rotation")

    # 规则（2026-11-22 切页卡顿）：remember 里同步扫目录或读文件。
    # 页面在 pager 里跨页跳转时才首次组合，IO 正好落在切页那一帧的预算里，
    # 表现为"点底栏后要顿一下才动"，与处理器无关。
    for m in re.finditer(r"remember(?:\s*\([^()]*\))?\s*\{([^{}]{0,300})", source):
        if re.search(r"DocStore\.list\(|\.listFiles\(|\.readText\(|\.readBytes\(", m.group(1)):
            line = source.count("\n", 0, m.start()) + 1
            problems.append(
                f"{rel}:{line} [组合期读盘] remember 里扫目录或读文件，切页那一帧会阻塞主线程；"
                "挪到 LaunchedEffect + withContext(Dispatchers.IO)")
    return problems


def check_kotlin_patterns() -> list[str]:
    """手机端代码反模式。每条同样对应一次真实缺陷。"""
    problems: list[str] = []
    if not KT_ROOT.exists():
        return problems
    for path in sorted(KT_ROOT.rglob("*.kt")):
        source = path.read_text(encoding="utf-8", errors="replace")
        problems.extend(_kt_problems(source, _rel(path)))
    return problems


def _update_install_problems(source: str, rel: str) -> list[str]:
    """更新流程里下载成功不得自动拉起安装。

    桌面端规则 19 的 Kotlin 镜像：下载完只停在 Ready，安装必须由用户点。
    否则用户以为在「检查更新」，手机已经弹出安装器——尤其在静默检查后。
    """
    problems: list[str] = []
    for m in re.finditer(r"fun\s+download\s*\(", source):
        # 粗粒度函数体切片：从 fun 的 '{' 配到同层 '}'，够识别自动安装调用
        open_at = source.find("{", m.end())
        if open_at < 0:
            continue
        depth = 0
        end_at = open_at
        for i in range(open_at, len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    end_at = i
                    break
        body = source[open_at:end_at]
        if re.search(r"startActivity\s*\(|installIntent\s*\(|ACTION_MANAGE_UNKNOWN_APP_SOURCES", body):
            line = source.count("\n", 0, m.start()) + 1
            problems.append(
                f"{rel}:{line} [下载后自动安装] download() 里拉起安装器或权限页："
                "下载完只能停在 Ready，安装必须由用户点；把跳转放进 installReady()")
    return problems


def check_update_no_auto_install() -> list[str]:
    manager = KT_ROOT / "update" / "UpdateManager.kt"
    if not manager.exists():
        return []
    return _update_install_problems(manager.read_text(encoding="utf-8"), _rel(manager))


def check_android_network_config() -> list[str]:
    """局域网配对走明文 HTTP，系统默认拒绝非回环明文。"""
    xml = KT_ROOT / "res" / "xml" / "network_security_config.xml"
    if not xml.exists():
        return []
    return _network_problems(xml.read_text(encoding="utf-8"), _rel(xml))


def _network_problems(body: str, rel: str) -> list[str]:
    # 只认 base-config：domain-config 只能列具体域名/IP，覆盖不了任意局域网地址
    if not re.search(r'<base-config[^>]*cleartextTrafficPermitted="true"', body):
        return [
            f"{rel} [局域网明文被拦] 电脑配对请求 http://局域网IP，"
            "系统会直接拒绝；network_security_config 需要放开 cleartext"]
    return []


def _roster_key_problems(repo_src: str, parser_src: str) -> list[str]:
    written = set(re.findall(r'\.put\("([A-Za-z]+)"', repo_src))
    block = re.search(r"private val aliases[\s\S]*?\n\s*\)", parser_src)
    # 只看 listOf(...) 里的值：map 左侧的键名是内部字段名，不代表解析器认这个键
    known: set[str] = set()
    if block:
        for m in re.finditer(r"to listOf\(([^)]*)\)", block.group(0)):
            known |= set(re.findall(r'"([A-Za-z]+)"', m.group(1)))
    missing = sorted(written - known)
    if not missing:
        return []
    return [
        "mobile/app/src/main/java/com/smallylc/smartyouth/data/Repo.kt "
        f"[缓存键无人识别] 写入的 {', '.join(missing)} 不在 MemberParser 别名表里，"
        "回读时这些字段会变成空值，界面把正常团员判成异常"
    ]


def check_roster_cache_keys() -> list[str]:
    """名单缓存写进去的键，解析器必须认得。

    规则（2026-11-20 真机注入测试）：Repo.saveCache 写 `reportStatus`，
    MemberParser 的别名表只有 `auditStatus`/`reportDataStatus`，断网或注入的名单
    回读时报到状态全部为空，界面把所有人标成「未报到」。写入键与解析别名必须同源，
    否则缓存看起来有数据、判定全部失真。
    """
    repo = KT_ROOT / "data" / "Repo.kt"
    parser = KT_ROOT / "site" / "MemberParser.kt"
    if not repo.exists() or not parser.exists():
        return []
    return _roster_key_problems(
        repo.read_text(encoding="utf-8"),
        parser.read_text(encoding="utf-8"),
    )


def run(quiet: bool = False) -> list[str]:
    problems: list[str] = []
    for path in _iter_py():
        problems.extend(check_file(path))
    problems.extend(check_deploy_configs())
    problems.extend(check_kotlin_patterns())
    problems.extend(check_android_network_config())
    problems.extend(check_roster_cache_keys())
    problems.extend(check_update_no_auto_install())
    if not quiet:
        for line in problems:
            print(line)
        print(f"\n共 {len(problems)} 处" if problems else "\n没有发现问题")
    return problems


if __name__ == "__main__":
    sys.exit(1 if run(quiet="--quiet" in sys.argv) else 0)
