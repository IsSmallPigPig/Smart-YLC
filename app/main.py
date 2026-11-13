"""应用入口：python -m app.main"""

from __future__ import annotations

import sys


def run() -> int:
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import setTheme, Theme

    app = QApplication(sys.argv)
    app.setFont(QFont("PingFang SC", 13))
    # 跟随系统深浅色模式
    setTheme(Theme.AUTO)

    from app.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
