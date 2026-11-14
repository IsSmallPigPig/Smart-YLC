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

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    # 内嵌网页（账号网页标签页）要求共享 OpenGL 上下文，必须在 QApplication 之前设置
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    # HiDPI：125% / 150% 这类分数缩放按实际比例渲染，不取整到 100%/200%
    # （不设时 Qt6 默认也是 PassThrough，这里固定住，避免被环境变量改回 Round）
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    from app.setup import setup_app

    setup_app()

    from app.ui.brand import app_icon

    app.setWindowIcon(app_icon())  # 窗口与任务栏图标（Windows 尤其需要）

    from app.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    from app.services.update_check import check_on_startup

    check_on_startup(parent=w)
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
