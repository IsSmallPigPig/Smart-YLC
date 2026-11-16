"""应用入口：python -m app.main [args]

  （无参数）      打开主窗口
  --tray          托盘后台模式：无窗口，每日定时检查 + 系统通知
  --silent-install <包路径>  安装助手：静默安装并通知用户重启（内部使用）
  --smoke-test    打包冒烟检查：真正构造主窗口后退出，供 CI 验证冻结产物
"""

from __future__ import annotations

import sys


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

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)

    from app.setup import setup_app

    setup_app()

    from app.ui.brand import app_icon

    app.setWindowIcon(app_icon())

    from app.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    app.processEvents()
    w.close()
    app.processEvents()
    print("SMOKE OK", flush=True)
    # 主窗口带后台线程（批量构建、定时器），走 Qt 正常退出会撞在线程析构顺序上；
    # 冒烟只要「构造成功」这个结论，输出后直接硬退，退出码照常 0
    import os

    os._exit(0)
    return 0


def run() -> int:
    if "--tray" in sys.argv:
        from app.tray import run_tray

        return run_tray()

    if "--smoke-test" in sys.argv:
        return _smoke_test()

    if "--silent-install" in sys.argv:
        from app.services.silent_update import install_and_notify

        index = sys.argv.index("--silent-install")
        if index + 1 >= len(sys.argv):
            print("缺少更新包路径", file=sys.stderr)
            return 2
        return install_and_notify(sys.argv[index + 1])

    from PySide6.QtCore import QEventLoop, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    # 内嵌网页（账号网页标签页）要求共享 OpenGL 上下文，必须在 QApplication 之前设置
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    # HiDPI：125% / 150% 这类分数缩放按实际比例渲染，不取整到 100%/200%
    # （不设时 Qt6 默认也是 PassThrough，这里固定住，避免被环境变量改回 Round）
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)

    # 只留一个实例：第二次启动把已有窗口叫出来就退出，不再建一套页面与后台线程
    from app.services.single_instance import SingleInstance

    guard = SingleInstance()
    if not guard.attach():
        return 0
    app.aboutToQuit.connect(guard.close)

    # 先放启动画面，再做后面那些慢的事（导入 qfluentwidgets 约 0.4s、构造主窗口
    # 与六个页面约 0.3s，Windows 冷启动还要叠上运行时加载与杀毒扫描）。
    # 这一步只用到 PySide6；顺序反了就又回到「双击后几秒黑屏」。
    from app.ui.boot import BootSplash

    boot = BootSplash()
    boot.show()
    # 只处理绘制相关事件：让启动画面立刻上屏，又不把排队中的用户输入提前吃掉
    QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents
                               | QEventLoop.ProcessEventsFlag.ExcludeSocketNotifiers)
    boot.start()

    try:
        from app.setup import setup_app

        setup_app()

        from app.ui.brand import app_icon

        app.setWindowIcon(app_icon())  # 窗口与任务栏图标（Windows 尤其需要）

        from app.ui.main_window import MainWindow

        w = MainWindow()

        def _show_existing(_command: str = ""):
            w.showNormal()
            w.raise_()
            w.activateWindow()

        guard.set_handler(_show_existing)
        w.show()
        # 主窗口自己的启动界面（整窗不透明遮罩）与这张卡同尺寸同底色，交出首帧后
        # 换手看不到接缝；统计快照回来时由 MainWindow 负责淡出那一层。
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        boot.finish()
    except BaseException:
        boot.close()
        raise

    from app.services.update_check import check_on_startup

    check_on_startup(parent=w)
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
