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

    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    from app.setup import setup_app

    setup_app()

    from app.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    from app.services.update_check import check_on_startup

    check_on_startup(parent=w)
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
