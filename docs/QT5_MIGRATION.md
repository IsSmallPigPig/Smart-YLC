# 移植到 Qt5 的评估

目的只有一个：让 Windows 7 **32 位**（x86）的机器也能装。评估结论、能改的部分、
改不了的部分都在这里，决定要不要单独维护这条分支时照这份看。

## 结论

- **Qt5 是唯一能走到 Win7 x86 的路线。** Qt6 官方的 Windows 构建表里没有 386/i686；
  PySide6 从 6.0 到最新的全部 PyPI 版本都没有 `win32` wheel（只发
  `win_amd64` 与 `win_arm64`），所以「PySide6 + 打补丁的 Qt6 DLL」这条路在 Python
  绑定层就断了——`crystalidea/qt6windows7` 只替换 Qt6 的原生库，`.pyd` 依然是
  x86_64，装不进 32 位进程。
- **Qt5 路线的技术前提成立**：`PySide2 5.15.2.1` 有 `cp35–cp310 none-win32`
  wheel（116.9 MB，体积与 `win_amd64` 的 137.4 MB 只差 15%，与「整包含
  QtWebEngine」相符），`PySide2-Fluent-Widgets 1.11.3` 与现在用的 PySide6 版是
  同一个版本号、纯 Python 包（`py3-none-any`）。
- **代价是分叉维护。** Qt5 的 PySide2 没有 macOS arm64 wheel（`macosx_10_13_intel`
  只有 x86_64，manylinux 也只有 x86_64），也就是说 **Qt5 版本没法同时是现在的
  Apple Silicon 原生版**。想两条腿走路，就必须接受 `win7-x86` 单开一条分支、
  一套依赖、一套 CI。
- **工作量估计**：以现在的代码量（app 18 458 行、tests 6 261 行），改成 Qt5 兼容
  写法约 130 处机械替换，加依赖钉版本与打包脚本，**单人 5–8 个工作日**能跑通
  主流程；真正的风险不在改代码，而在下面「改不了的」三条。

## 为什么不是 Windows 7 x64

Win7 x64 不需要换框架：`python-3.8.10-amd64` 之类的解释器 + `PySide6` 的某个
6.x 版本仍能在 Win7 上起来（社区做法是换 `qt6windows7` 的 x64 补丁 DLL），代价是
QtWebEngine 要么降级要么自己编。但 Win7 存量机器里 32 位系统占比不低，
「x64 能装、x86 装不了」通常等于没解决。**如果只需要覆盖 x64，本评估不必执行。**

## 代码面要改什么

统计基于当前仓库（`grep` 全量）：

| 项 | 处数 | 说明 |
| --- | --- | --- |
| `Signal(` / `Slot(` | 44 | PySide2 同名，**不用改**，只在 import 处换模块名 |
| `.exec()` | 4 | Qt5 有 `exec_()`/`exec()` 两种写法，`exec()` 在 5.15 可用，**多数不用改** |
| 作用域枚举（`Qt.AlignmentFlag.AlignCenter` 这类） | 85 | Qt5 只有 `Qt.AlignCenter` 形式；`QFont.Weight.DemiBold` → `QFont.DemiBold`，机械替换 |
| `QMouseEvent.globalPosition().toPoint()` | 3 | 改 `globalPos()`（都在浏览器窗口拖动处） |
| `Qt.ColorScheme`（`QGuiApplication.styleHints().colorScheme()`） | 1 | Qt6.5+ 才有；`boot.py` 判断明暗要换成读系统默认值或配置 |
| `QColorScheme` 相关导入 | 1 | 同上 |
| `QWebEngine*` 用法 | 17 | 大头是一个 profile 构造：`QWebEngineProfile("gd-<账号>", self)` 是 Qt6 写法（首参是存储分区名），Qt5 只有 `QWebEngineProfile(parent)`，要改成先建再 `setPersistentStoragePath` + `setCachePath` + `setPersistentCookiesPolicy`；`QWebEnginePage(profile, parent)`、`QWebEngineView.loadProgress/loadFinished/urlChanged` 在 5.15 同名可用 |

其余（布局、绘制、`QPainterPath`、`QTimer`、`QThread`）在 Qt5/Qt6 之间没有语义差别。
真正要逐个**肉眼回归**的是自绘控件：标签条（`browser_page.WebTab` / `PlusButton`）、
`PageBanner`、图表（`charts.py`）、`app/ui/page.py` 里手写的阴影与圆角。Qt5 的
`QPainter` 抗锯齿与 `QFontMetrics.elidedText` 结果与 Qt6 有 1–2px 级别的差异。

## 依赖矩阵（32 位 / Python 3.8 可用性，均已核对 PyPI）

| 包 | 版本 | 状态 |
| --- | --- | --- |
| PySide2 | 5.15.2.1 | `none-win32` wheel 存在，`cp35–cp310` 通用 |
| PySide2-Fluent-Widgets | 1.11.3 | 纯 Python，依赖 `PySide2>=5.15.0`、`PySide2-Frameless-Window>=0.8.0` |
| PyMuPDF | 1.23.26 或 1.24.10 | 有 `cp38 win32` wheel；1.25 起不再发 32 位 |
| pypdf | 需钉 5.x | 7.1.0 要求 Python ≥ 3.10；3.8 下 pip 会自动回退，但建议直接钉住，别让构建期解出别的版本 |
| requests | 需钉 2.32.x | 2.34.2 要求 Python ≥ 3.10，同上 |
| Pillow | 10.4.0 | 有 `cp38 win32` |
| openpyxl | 3.1.5 | 要求 ≥ 3.8，纯 Python |
| Python 解释器 | 3.8.20 x86 | PythonVista（原 PythonWin7）提供 exe 安装包；3.10 以上只发 `pythonx86.nupkg`，CI 里要先解包再注册 |

`requirements.txt` 要拆成 `requirements-win7-x86.txt`，主分支的版本号不动。

## 分阶段计划

1. **先验证最大的未知**：在 Win7 x86 虚拟机上装 Python 3.8.20 x86 +
   `pip install PySide2==5.15.2.1`，跑一行
   `python -c "from PySide2.QtWebEngineWidgets import QWebEngineView; print('ok')"`。
   这一步过不了，整条路线就地终止（这也是唯一还没有实测证据的环节）。同时打开
   `https://www.12355.net/` 看页面是否正常——Qt 5.15 的 Chromium 是 87，比现在的
   114 老三年。
2. 建 `win7-x86` 分支：`import` 全量换到 `PySide2`，机械替换 85 处枚举与 3 处
   `globalPos()`，`Qt.ColorScheme` 那处改成读配置。
3. 跑 `pytest`：现有 390 个用例里，纯函数的（`export_data`、`check_rules`、
   `org_rule`、`updater`）应全绿；UI 冒烟用例要在 Win7 x86 上重跑一遍。
4. PyInstaller 32 位打包：`onedir`（32 位进程里 `onefile` 要先解压到临时目录，
   慢且容易被杀软拦住），`--target-arch 32bit` 需 32 位 Python 解释器。
5. 手工回归清单：登录 → 同步 → 上传压缩 → 组织状况 → 导出 → 账号网页多标签。

## 改不了的（Qt5 内无解）

1. **一个 Qt5 版本不能同时服务 macOS Apple Silicon。** PySide2 5.15.2.1 的 macOS
   wheel 只有 `x86_64`，在 M 系列机器上只能走 Rosetta，且 Qt5 的 Cocoa 后端在
   macOS 14+ 上对混合层（现在的毛玻璃）支持很差。要么放弃 macOS 原生、要么分叉。
2. **Qt5.15 / PySide2 已经停更。** PySide2 最后一个版本是 2021 年的 5.15.2.1，
   Qt 5.15 开源版也早已停止维护。也就是说这条分支拿不到上游的安全修复与 macOS/
   Windows 新系统适配，只能靠我们自己在仓库里打补丁。
3. **32 位进程 4 GB 地址空间装不下现在这套浏览器用法。** 现在的账号网页窗口支持
   6 个标签，每个标签是一个 Chromium 渲染进程 + 独立 profile 目录，x86 下单个
   进程上限 2 GB（`/LARGEADDRESSAWARE` 后 3–4 GB），实测这类用法通常 2–3 个标签
   就会开始闪退。要上 x86，`MAX_TABS` 得从 6 降到 2–3，并接受「同时在线账号变少」。
4. **Qt5 的 Chromium 87 与站点长期演进的风险。** 站点若启用 Chromium 87 不支持的
   新特性（新 TLS 套件、COOP/COEP 组合、`SameSite=None` 的新行为），登录会直接
   失效，而且**我们改不了**——只能等 Qt 升级，而 Qt5 不会升了。

## 建议

- 只有确实存在 Win7 x86 的用户群时才做。先做上面第 1 步的验证，一天内能出结论。
- 做的话**不要**把主分支降级到 Qt5：新建 `win7-x86` 长期分支，CI 单独一条
  workflow，发版时两个安装包并列提供（`SmartYouth-Windows-x86-setup.exe`）。
- 如果目标是「老机器能跑」而不是「必须是 32 位」，优先考虑 Win7 x64 或
  Windows 10 精简版，代价远小于这次移植。
