"""应用入口：python -m app.main [args]

  （无参数）      打开主窗口
  --tray          托盘后台模式：无窗口，每日定时检查 + 系统通知
"""

from __future__ import annotations

import sys


def run() -> int:
    if "--tray" in sys.argv:
        from app.tray import run_tray

        return run_tray()

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
