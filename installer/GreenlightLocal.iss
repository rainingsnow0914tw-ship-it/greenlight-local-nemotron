; Greenlight Local · NVIDIA Nemotron Edition — Windows installer (Inno Setup 6)
;
; Build: first package the app with PyInstaller into dist_app\GreenlightLocal, then
;   ISCC.exe installer\GreenlightLocal.iss
; Output: dist_installer\GreenlightLocal-Setup.exe
;
; Installs per user (no administrator prompt). Uninstalling removes the program only; projects and
; settings live in %LOCALAPPDATA%\GreenlightLocal and are kept.

#define AppName "Greenlight Local"
#define AppVersion "1.0.0"
#define AppExe "GreenlightLocal.exe"

[Setup]
AppId={{6E2B9C1A-4F7D-4E58-9B6A-2C1D3E4F5A60}
AppName={#AppName}
AppVerName={#AppName} · NVIDIA Nemotron Edition {#AppVersion}
AppVersion={#AppVersion}
AppPublisher=Greenlight Local
DefaultDirName={localappdata}\Programs\Greenlight Local
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist_installer
OutputBaseFilename=GreenlightLocal-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}
LicenseFile=..\LICENSE
SetupIconFile=greenlight.ico

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist_app\GreenlightLocal\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 捷徑明講用這個圖示檔：只靠 exe 內建的圖示時，Windows 會沿用同一路徑的舊快取（10-17 重裝後桌面還是舊 logo）
Source: "greenlight.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\greenlight.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\greenlight.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
