; Inno Setup 安装脚本：把 PyInstaller onedir 产物打成一个正式的 Windows 安装包。
;
; 为什么用安装包而不是单文件 exe：
;   单文件（PyInstaller --onefile）每次启动都要把整个运行时解压到临时目录，冷启动
;   明显慢。onedir 直接把文件铺在安装目录里，启动只是加载 DLL，快得多。
;
; 为什么是「每用户安装」：
;   PrivilegesRequired=lowest + 装到 {localappdata}\Programs 下，安装时不需要管理员、
;   不弹 UAC，也就不会出现「未知发布者」的 UAC 提示。这对内部分发的工具最省事。
;
; 版本号、发布者由 workflow 用 /D 传入；本地手动编译时走下面的默认值。

#ifndef AppName
  #define AppName "智慧团委"
#endif
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef AppPublisher
  #define AppPublisher "智慧团委"
#endif
#ifndef SourceDir
  #define SourceDir "dist\SmartYouth"
#endif
#ifndef OutputDir
  #define OutputDir "dist\installer"
#endif

[Setup]
; 固定的 AppId：升级时靠它识别「这是同一个应用」，不要改
AppId={{9F3C2A74-6B1E-4E8A-9D2C-7A5B1E3F4D60}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 每用户安装：不需要管理员、不弹 UAC，从根上避开「未知发布者」的提权提示
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=SmartYouth-Windows-x64-setup
SetupIconFile=app\assets\smart-ylc.ico
UninstallDisplayIcon={app}\{#AppName}.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 只出 64 位：PySide6 6.x 没有 32 位轮子
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
DisableWelcomePage=no

[Languages]
; 只用内置英文（Inno 不自带简体中文语言包，引用不存在的 isl 会让编译失败）
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; onedir 产物：主程序 + _internal 运行时，整份复制进安装目录
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; GPLv3 要求随二进制分发许可证全文
Source: "LICENSE"; DestDir: "{app}"; DestName: "LICENSE-GPLv3.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
