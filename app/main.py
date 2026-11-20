"""应用入口：python -m app.main [args]

  （无参数）      打开主窗口
  --tray          托盘后台模式：无窗口，每日定时检查 + 系统通知
  --demo          用演示数据启动（隔离的临时配置，不碰真实配置）
  --silent-install <包路径>  安装助手：静默安装并通知用户重启（内部使用）
  --smoke-test    打包冒烟检查：真正构造主窗口后退出，供 CI 验证冻结产物
"""

from __future__ import annotations

import sys


_DEMO_REAL_HOME: str | None = None


def _enter_demo_home() -> str:
    """--demo：把配置目录指到一份隔离的 HOME，必要时写入演示数据。

    必须在 import app 之前设环境变量：constants 里 CONFIG_FILE 是
    `expanduser("~/.gd12355-helper/config.json")` 在 import 时求值的，
    晚一步设就还写到真实配置上。

    目录固定在 `~/.smartyouth-demo`，不再用 mkdtemp：每次启动换一个空目录时，
    演示模式里加的来源目录、改过的设置关掉就没了（正式版不受影响——这里是
    另一份 HOME）。要重置演示数据，删掉这个目录即可。
    """
    import json
    import os

    global _DEMO_REAL_HOME
    if _DEMO_REAL_HOME is None:
        _DEMO_REAL_HOME = os.path.expanduser("~")
    home = os.path.join(_DEMO_REAL_HOME, ".smartyouth-demo")
    os.makedirs(home, exist_ok=True)
    os.environ["HOME"] = home
    os.environ["USERPROFILE"] = home

    from app.constants import CONFIG_FILE
    from app.services import demo_data

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
        if cfg:
            print(f"[demo] 配置目录 {home}；沿用已有数据", flush=True)
            return home

    cfg = demo_data.build_config()
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"[demo] 配置目录 {home}；{demo_data.summary(cfg)}", flush=True)
    return home


def _write_smoke_result(text: str) -> None:
    """冒烟结论落盘：`--smoke-result <路径>` 指定位置，默认不写。

    不写死路径——打包产物在 CI 里读写仓库目录之外的位置更安全，也便于本地排查。
    """
    argv = sys.argv
    if "--smoke-result" not in argv:
        return
    index = argv.index("--smoke-result")
    if index + 1 >= len(argv):
        return
    try:
        with open(argv[index + 1], "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass


def _smoke_test() -> int:
    """构造 QApplication + MainWindow 走一遍真实启动链路，然后退出。

    CI（尤其 Linux）上 PyInstaller 分析期 import 成功不代表冻结产物能跑：
    缺系统库、缺插件都在这一步才暴露。HOME 指到临时目录——constants 里
    CONFIG_FILE 按 expanduser 在 import 时求值，先设环境变量再 import app 模块，
    冒烟过程读写不到用户真实配置。

    平台插件不在这里强设：无头 CI 由 xvfb-run 提供真实 X11 显示。
    offscreen 不能替代——qframelesswindow 的无边框初始化在无真实窗口系统时段错误。
    """
    import os
    import tempfile

    tmp_home = tempfile.mkdtemp(prefix="smartyouth-smoke-")
    os.environ["HOME"] = tmp_home
    os.environ["USERPROFILE"] = tmp_home

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)

    from app.setup import setup_app

    setup_app()

    from app.ui.brand import app_icon

    app.setWindowIcon(app_icon())

    from app.ui.main_window import MainWindow

    # 云端备份的 cryptography 在函数体内才 import，静态分析收不到；上一次
    # CI 依赖列表漏装，冻结包要到用户点「云端备份」才炸。冒烟直接 import
    # 真用到的原语并做一次性加解密——缺模块或 Rust 扩展没打进包里就红。
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        AESGCM(b"0" * 32).encrypt(b"0" * 12, b"smoke", None)
    except Exception as exc:  # noqa: BLE001 - 冻结产物里缺什么都有可能
        print(f"SMOKE FAIL: 加密模块不可用：{exc}", flush=True)
        _write_smoke_result(f"SMOKE FAIL: cryptography unavailable: {exc}")
        import os

        os._exit(1)

    # 逐格查空格的 opencv 4.8.1 按 numpy 1.x ABI 编译；冻结包里若 numpy 被装成
    # 2.x，import cv2 抛 `_ARRAY_API not found`（2026-11-20 Windows 故障）。
    # 这一路径只有用户点「查空格」才走到，窗口能起不代表没坏——冒烟直接 import
    # 并跑一次连通域，缺扩展或 ABI 不匹配在这里就红，不等用户报障。
    try:
        import cv2
        import numpy as np

        _smoke_img = np.zeros((32, 32), dtype=np.uint8)
        cv2.connectedComponentsWithStats(_smoke_img, connectivity=8)
    except Exception as exc:  # noqa: BLE001 - 冻结产物里缺什么都有可能
        print(f"SMOKE FAIL: OpenCV/NumPy ABI 不可用：{exc}", flush=True)
        _write_smoke_result(f"SMOKE FAIL: cv2/numpy ABI unavailable: {exc}")
        import os

        os._exit(1)

    w = MainWindow()
    w.show()
    app.processEvents()
    w.close()
    app.processEvents()
    print("SMOKE OK", flush=True)
    # Windows 的 --windowed 产物没有控制台：调用方从管道拿不到 stdout，也拿不到
    # 可靠的退出码。把结论同时写一份文件，CI 读文件判断，两边都成立。
    _write_smoke_result("SMOKE OK")
    # 主窗口带后台线程（批量构建、定时器），走 Qt 正常退出会撞在线程析构顺序上；
    # 冒烟只要「构造成功」这个结论，输出后直接硬退，退出码照常 0
    import os

    os._exit(0)
    return 0


def run() -> int:
    # --demo 要赶在任何 app 模块 import 之前改 HOME（config 路径在 import 时求值）
    demo = "--demo" in sys.argv
    if demo:
        _enter_demo_home()

    # 引擎路径必须在任何 QtWebEngine import 之前钉好：打包后 Qt 按 QLibraryInfo
    # 推出的前缀指向构建机，资源一缺，Windows 上是 int3 崩进程而不是抛异常
    from app.services.webengine import available, prepare, reason, resources_dir

    prepare()

    if "--smoke-test" in sys.argv:
        return _smoke_test()

    # 日志最早初始化：Qt 导入、QApplication 构造、插件加载都可能原生崩溃。
    # --demo 已经把 HOME 指到临时目录，这里建出的日志目录也跟着走。
    from app.services import app_log
    from app.services.app_log import setup_logging

    setup_logging()
    if available():
        app_log.info(f"WebEngine 就绪：{resources_dir() or '开发环境'}")
    else:
        app_log.warning(f"WebEngine 不可用，网页功能将停用：{reason()}")

    if "--tray" in sys.argv:
        from app.tray import run_tray

        return run_tray()

    if "--silent-install" in sys.argv:
        from app.services.silent_update import install_and_notify

        index = sys.argv.index("--silent-install")
        if index + 1 >= len(sys.argv):
            print("缺少更新包路径", file=sys.stderr)
            return 2
        return install_and_notify(sys.argv[index + 1])

    from PyQt5.QtCore import QEventLoop, QTimer, Qt
    from PyQt5.QtGui import QGuiApplication
    from PyQt5.QtWidgets import QApplication

    # 内嵌网页（账号网页标签页）要求共享 OpenGL 上下文，必须在 QApplication 之前设置
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    # HiDPI：125% / 150% 这类分数缩放按实际比例渲染，不取整到 100%/200%
    # （不设时 Qt6 默认也是 PassThrough，这里固定住，避免被环境变量改回 Round）
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)

    # 崩溃处理器要在 QApplication 之后装：更早崩没有应用实例，弹不出窗口。
    # 日志本身在 --demo 之后才初始化，路径跟着 demo 的临时 HOME 走。
    from app.services.app_log import install_crash_handler

    install_crash_handler()

    # 只留一个实例：第二次启动把已有窗口叫出来就退出，不再建一套页面与后台线程
    from app.services.single_instance import SingleInstance

    if demo:
        # 演示实例与正式实例各用一把锁，互不顶掉
        from app.services import single_instance

        single_instance.SUFFIX = "demo"

    guard = SingleInstance()
    if not guard.attach():
        return 0
    app.aboutToQuit.connect(guard.close)
    from app.services.app_log import info as log_info

    log_info("单实例锁已获取，开始构造界面")

    # 原生崩溃不会经过 excepthook，最后一条阶段日志就是定位现场的唯一线索。
    log_info("启动阶段：QApplication 已创建")
    log_info("启动阶段：准备显示启动画面")

    # 先放启动画面，再做后面那些慢的事（导入 qfluentwidgets 约 0.4s、构造主窗口
    # 与六个页面约 0.3s，Windows 冷启动还要叠上运行时加载与杀毒扫描）。
    # 这一步只用到 PyQt5；顺序反了就又回到「双击后几秒黑屏」。
    from app.ui.boot import BootSplash

    boot = BootSplash()
    boot.show()
    # 只处理绘制相关事件：让启动画面立刻上屏，又不把排队中的用户输入提前吃掉
    QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                               | QEventLoop.ProcessEventsFlag.ExcludeSocketNotifiers)
    boot.start()

    def _pump():
        """把这一拍交还事件循环。

        启动画面是独立窗口，主线程一直闷头构造时它就冻在那儿（转圈停住、
        系统还可能标「未响应」）。每个重步骤之间让一拍，画面就能继续动。"""
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                                   | QEventLoop.ProcessEventsFlag.ExcludeSocketNotifiers)

    try:
        from app.setup import setup_app

        setup_app()
        _pump()
        log_info("字体与主题已就绪")

        from app.ui.brand import app_icon

        app.setWindowIcon(app_icon())  # 窗口与任务栏图标（Windows 尤其需要）

        from app.ui.main_window import MainWindow

        w = MainWindow()
        log_info("主窗口构造完成")
        log_info("启动阶段：准备展示主窗口")

        def _show_existing(_command: str = ""):
            w.showNormal()
            w.raise_()
            w.activateWindow()

        guard.set_handler(_show_existing)
        w.show()
        # 主窗口自己的启动界面（整窗不透明遮罩）与这张卡同尺寸同底色，交出首帧后
        # 换手看不到接缝；统计快照回来时由 MainWindow 负责淡出那一层。
        _pump()
        boot.finish()
        log_info("启动阶段：主窗口已展示")
    except BaseException:
        boot.close()
        raise

    # 静默更新装完后，安装器会直接拉起新版本：消费标记，告诉用户已经更新。
    # 标记只在「当前版本 >= 目标版本」时消费，安装失败时留着，下次启动仍能提醒。
    from app import __version__ as _running_version
    from app.services.silent_update import take_updated

    _updated_from = take_updated(_running_version)
    if _updated_from:
        log_info(f"更新完成：{_updated_from} → {_running_version}")

        def _announce_updated():
            from qfluentwidgets import InfoBar

            InfoBar.success("已更新", f"已从 {_updated_from} 更新到 {_running_version}",
                            duration=6000, parent=w)

        QTimer.singleShot(1800, _announce_updated)

    # 启动公告：经 GitHub 加速站拉 JSON，有新公告才弹窗；拉取失败只记日志
    from app.services.announcement_check import check_on_login

    check_on_login(w, delay_ms=4000)
    log_info("启动阶段：公告检查已安排")

    # 更新检查要联网、建窗口，等界面真的立起来再问，别跟启动抢那几百毫秒
    from app.services.update_check import check_on_startup

    QTimer.singleShot(1200, lambda: check_on_startup(parent=w))
    log_info("启动阶段：更新检查已安排")
    # macOS：界面立起来就主动弹系统授权框，不等第一条提醒再问。
    # 提醒只走原生通道（没有应用内浮窗兜底），没授权就没有任何后台提醒。
    if sys.platform == "darwin":
        from app.services import macos_notify

        QTimer.singleShot(1500, macos_notify.request_permission)
    log_info("启动阶段：进入事件循环")
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
