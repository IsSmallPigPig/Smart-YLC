## 下载

- **macOS（Apple Silicon / M 系列）**：`SmartYouth-macOS-arm64.dmg`
- **Windows 10 / 11（64 位）**：`SmartYouth-Windows10-x64-setup.exe`
- **Windows 10 / 11（32 位）**：`SmartYouth-Windows10-x86-setup.exe`
- **Windows 7（64 位）**：`SmartYouth-Windows7-x64-setup.exe`
- **Windows 7（32 位）**：`SmartYouth-Windows7-x86-setup.exe`
- **Linux（x86_64）**：`SmartYouth-Linux-x64.deb`（装好后命令 `smartyouth` 或应用列表里的「智慧团委」；面向 Ubuntu 22.04+/Debian 12+）

> macOS 仅支持 Apple Silicon。Windows 按系统与位数选包：Win10/11 用 Windows10-x64 或 Windows10-x86，Windows 7 用 Windows7-x64 或 Windows7-x86（Win7 包基于 Python 3.8，无法使用 Win10/11 包）。安装包用英文名（GitHub 下载文件名不支持中文），装好后的应用名是「智慧团委」。

首次运行：macOS 若提示「无法验证开发者」，右键 → 打开，或执行 `xattr -cr /Applications/智慧团委.app`；
Windows 双击安装包，SmartScreen 提示时点「更多信息 → 仍要运行」。

Windows 若附带 `SmartYouth-CodeSigning.cer`，内部分发可把它装进「受信任的根证书颁发机构」以消除「未知发布者」。
