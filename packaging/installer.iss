; Inno Setup script: dist\Label-Miner\ -> build\installer\Label-Miner-Setup.exe
;
; Run packaging\build.ps1, which builds the manual, the app and this installer.
; Per-user install (no admin password), Danish, Start Menu entry, desktop
; shortcut and a normal uninstaller.
; Saved as UTF-8 with BOM: without it Inno Setup reads the Danish letters wrongly.

#define MyAppName "Label Miner"
; Keep in sync with __version__ in src\label_miner\__init__.py.
#define MyAppVersion "2.2"
#define MyAppExeName "Label-Miner.exe"

[Setup]
; Identifies "this program" to Windows across versions -- never change it,
; so a newer installer upgrades the existing install in place.
AppId={{09EE7432-3D99-43A7-8436-825E93FA21DB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; An upgrade goes into the previous version's folder, so there's nothing to choose
DisableDirPage=yes
OutputDir={#SourcePath}..\build\installer
OutputBaseFilename=Label-Miner-Setup
SetupIconFile={#SourcePath}..\src\label_miner\assets\label_miner.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
; onnxruntime only ships 64-bit wheels for Windows
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
; Close a running Label Miner before upgrading it
CloseApplications=yes
; Refreshes Windows' icon cache, so the shortcuts pick up a changed icon
ChangesAssociations=yes

[Languages]
Name: "danish"; MessagesFile: "compiler:Languages\Danish.isl"

[Tasks]
Name: "desktopicon"; Description: "Opret genvej på skrivebordet"; GroupDescription: "Genveje:"

[InstallDelete]
; Remove the previous version's program files first, so libraries an older
; build bundled can't linger in _internal\
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\{#MyAppExeName}"
; v1.0's installer was English: its uninstall shortcut had another name
Type: files; Name: "{group}\Uninstall {#MyAppName}.lnk"

[Files]
Source: "{#SourcePath}..\dist\Label-Miner\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; The "? Hjælp" buttons look for the manual next to Label-Miner.exe
Source: "{#SourcePath}..\build\Label-Miner-Vejledning.pdf"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} – Vejledning"; Filename: "{app}\Label-Miner-Vejledning.pdf"
Name: "{group}\Afinstaller {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent
