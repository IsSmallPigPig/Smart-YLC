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
; 自签名打包时 workflow 传 /DSelfSigned=1：多出一个「信任证书」可选项
#ifndef SelfSigned
  #define SelfSigned 0
#endif
; 产物后缀与位宽：workflow 按矩阵传入（Windows-x64 / Windows-x86 / Win7-x64 / Win7-x86）。
; 本地直接双击编译时不带这些，落回原有的 64 位命名，行为不变。
#ifndef ArtifactSuffix
  #define ArtifactSuffix "Windows-x64"
#endif
; 位宽用数值而不是字符串：ISPP 的 #if 按数值比较最稳，不依赖字符串比较的支持
#ifndef BitWidth
  #define BitWidth 64
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
OutputBaseFilename=SmartYouth-{#ArtifactSuffix}-setup
SetupIconFile=app\assets\smart-ylc.ico
UninstallDisplayIcon={app}\{#AppName}.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
#if BitWidth == 64
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
#else
; 32 位安装包能装进任何 Windows（x86 处理器与 x64 处理器都跑得下 32 位程序），
; 所以不设 ArchitecturesAllowed 门槛；安装模式保持 32 位，WOA-on-ARM 除外
ArchitecturesAllowed=x86 x64 arm64
#endif
DisableWelcomePage=no

[Languages]
; 只用内置英文（Inno 不自带简体中文语言包，引用不存在的 isl 会让编译失败）
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
#if SelfSigned
; 安装时可选把本安装包的签名证书装进「受信任的根证书颁发机构」，
; 之后运行不再提示「未知发布者」。需要管理员权限，所以单独提权、单独一步。
Name: "trustcert"; Description: "信任本次安装包的签名证书（会请求一次管理员授权，之后不再提示「未知发布者」）"; Flags: unchecked
#endif

[Files]
; onedir 产物：主程序 + _internal 运行时，整份复制进安装目录
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; GPLv3 要求随二进制分发许可证全文
Source: "LICENSE"; DestDir: "{app}"; DestName: "LICENSE-GPLv3.txt"; Flags: ignoreversion
#if SelfSigned
; 证书只在勾选「信任证书」时用到；装到临时目录，安装完删掉
Source: "dist\installer\SmartYouth-CodeSigning.cer"; DestDir: "{tmp}"; Flags: deleteafterinstall
#endif

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
{ 勾选了「信任证书」时，单独提权跑 certutil：安装本身仍是每用户、不弹 UAC，
  只有装证书这一步会请求管理员授权，取消也不影响应用可用 }
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  CertPath: String;
begin
  if CurStep <> ssPostInstall then
    Exit;
#if SelfSigned
  if not WizardIsTaskSelected('trustcert') then
    Exit;

  CertPath := ExpandConstant('{tmp}\SmartYouth-CodeSigning.cer');
  if not FileExists(CertPath) then
  begin
    MsgBox('安装包里没有找到签名证书，跳过证书安装。', mbInformation, MB_OK);
    Exit;
  end;

  if ShellExec('runas', ExpandConstant('{sys}\certutil.exe'),
               '-addstore -f Root "' + CertPath + '"', '', SW_HIDE,
               ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode = 0 then
      MsgBox('证书已装进「受信任的根证书颁发机构」，之后打开应用不再提示未知发布者。',
             mbInformation, MB_OK)
    else
      MsgBox('证书安装失败（certutil 返回 ' + IntToStr(ResultCode) +
             '）。可以手动双击安装目录里的 SmartYouth-CodeSigning.cer 导入。',
             mbError, MB_OK);
  end
  else
    MsgBox('已取消管理员授权，证书未安装；应用本身可以正常使用。',
           mbInformation, MB_OK);
#endif
end;
