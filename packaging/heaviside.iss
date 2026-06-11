; Inno Setup script for the Heaviside Windows installer.
;
; Wraps the PyInstaller one-dir build (dist\Heaviside\) into a single
; HeavisideSetup.exe that installs per-user (no UAC prompt), adds Start Menu and
; (optional) Desktop shortcuts, an uninstaller / Add-or-Remove-Programs entry,
; and a `.hv` file association so double-clicking a schematic opens it in
; Heaviside.
;
; It is driven by scripts\make_installer.py, which passes the values below on the
; iscc command line so nothing here is hardcoded to a version or path:
;
;   AppVersion  - the app version (app.version.__version__)
;   SourceDir   - the PyInstaller onedir folder (default: dist\Heaviside)
;   OutputDir   - where to write the installer (default: dist)
;   OutputBase  - installer file name without extension (default: HeavisideSetup)
;
; Build manually:
;   iscc /DAppVersion=0.2.0 packaging\heaviside.iss
; (defaults fill in the rest.)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Heaviside"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef OutputBase
  #define OutputBase "HeavisideSetup"
#endif

#define AppName "Heaviside"
#define AppPublisher "Heaviside"
#define AppURL "https://github.com/whileman133/Heaviside"
#define AppExe "Heaviside.exe"

[Setup]
; A stable GUID identifies the app across versions (upgrades replace in place).
AppId={{8B2F6F3E-7C2A-4E2B-9C1E-0A1B2C3D4E5F}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install by default (no admin / UAC); the user may still elevate to
; install for all users from the privileges dialog.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
LicenseFile=..\LICENSE
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBase}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "assoc"; Description: "Associate .hv schematic files with Heaviside"; GroupDescription: "File associations:"

[Files]
; The entire PyInstaller onedir folder (the executable plus its DLLs and data).
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; .hv file association (HKA = HKLM for an all-users install, HKCU for per-user),
; written only when the "assoc" task is selected and removed on uninstall.
Root: HKA; Subkey: "Software\Classes\.hv"; ValueType: string; ValueName: ""; ValueData: "Heaviside.Schematic"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\Heaviside.Schematic"; ValueType: string; ValueName: ""; ValueData: "Heaviside Schematic"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\Heaviside.Schematic\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\Heaviside.Schematic\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: assoc

[Run]
; Offer to launch the app at the end of setup.
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
