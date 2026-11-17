# PySide6 → PyQt5 迁移说明

基于最新版 v1.6.5（436633b）移植。

## 迁移概述

将 Smart-YLC 项目从 PySide6 迁移到 PyQt5，采用**最小改动**策略：只改导入和少量 API，不重写业务逻辑。

## 关键发现

1. **Fluent Widgets 包名相同**：`PySide6-Fluent-Widgets` 和 `PyQt-Fluent-Widgets` 都安装为 `qfluentwidgets` 包，`from qfluentwidgets import ...` 无需改动。
2. **PyQt5 5.15 支持 Qt6 风格枚举**：`Qt.AlignmentFlag.AlignVCenter`、`QDialog.DialogCode.Accepted` 等作用域枚举在 PyQt5 5.15 中全部可用，无需改写为 `Qt.AlignVCenter`。
3. **大部分 Qt 类位置相同**：`QGuiApplication`、`QDesktopServices`、`QPolygonF` 等在两边都在 `QtGui`；只有 `QAction` 不同（PySide6 在 `QtGui`，PyQt5 在 `QtWidgets`）。

## 改动清单（55 个文件，+230/-218 行）

### 1. 导入替换（50 个文件）
- `PySide6` → `PyQt5`（所有 `from PySide6.QtX import ...`）
- `Signal` → `pyqtSignal as Signal`（16 处，保持后续 `Signal(...)` 写法不变）

### 2. API 位置修正（1 处）
- `app/ui/main_window.py`：`QAction` 从 `QtGui` 改为 `QtWidgets` 导入

### 3. Qt6 专属 API 替换（4 处）
- `app/ui/boot.py`：`QGuiApplication.styleHints().colorScheme()`（Qt6 专属）→ `darkdetect.isDark()`（PyQt-Fluent-Widgets 的依赖）
- `app/ui/browser_page.py`：`e.globalPosition().toPoint()`（Qt6 专属）→ `e.globalPos()`（PyQt5）
- `app/ui/browser_page.py`：`QWebEngineProfile(f"gd-{user}", self)`（Qt6 带存储名构造）→ `QWebEngineProfile(self)`（PyQt5，存储路径靠 `setPersistentStoragePath` 设置）
- `tests/test_hidpi.py`：`QPixmap.deviceIndependentSize()`（Qt6 专属）→ `width() / devicePixelRatio()` 计算

### 4. 测试修正（2 处）
- `tests/test_notify.py`：`icon is QSystemTrayIcon.MessageIcon.Information` → `icon == ...`（PyQt5 枚举是 int，`is` 身份比较失败）
- `tests/test_ui_smoke.py`：拖窗测试 mock 从 `globalPosition` 改为 `globalPos`

### 5. 依赖与构建（4 个文件）
- `requirements.txt`：
  ```
  PySide6>=6.12 → PyQt5>=5.15
  PySide6-Fluent-Widgets>=1.11 → PyQt-Fluent-Widgets>=1.11
  新增 PyQtWebEngine>=5.15（内嵌浏览器）
  ```
- `scripts/build.py`：`--hidden-import=PySide6.QtSvg` → `PyQt5.QtSvg`
- `.github/workflows/release.yml`：3 处 pip install 命令更新
- `installer.iss`、`README.md`、`DESIGN.md`：注释中的 PySide6 字样更新

## 验证结果

- **282 个非 UI 测试**：全部通过（含新增 `test_materials.py`）
- **UI 测试**：单独运行通过；一起运行有 `qconfig` 单例隔离问题（迁移前 PySide6 也存在，非本次引入）
- **冒烟测试**：`MainWindow` 构造 + 显示正常

## 注意事项

1. **PyQtWebEngine 需要系统库**：Linux 上需要 `libsmime3`（`apt install libsmime3`），否则 `QWebEngineView` 导入失败。Windows/macOS 轮子自带，无需额外处理。
2. **Qt 版本下限**：PyQt5 5.15 支持 32 位 Windows（PySide6 6.x 不支持），如需出 32 位包可直接用。
3. **qframelesswindow**：PyQt5 用 `PyQt5-Frameless-Window`（PyQt-Fluent-Widgets 的依赖），与 PySide6 版 API 一致，`main_window.py` 中的 macOS 钩子无需改动。
4. **上游评估文档**：`docs/QT5_MIGRATION.md` 是上游对 PySide2 路线的评估（为 Win7 x86），本次用的是 PyQt5 路线，两者依赖不同但改动思路一致。
