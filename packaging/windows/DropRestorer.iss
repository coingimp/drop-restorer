#ifndef AppVersion
#define AppVersion "0.1.0"
#endif

#define AppName "DropRestorer"
#define AppExe "DropRestorer.exe"

[Setup]
AppId={{F32D7C25-6AC0-45C2-88C0-20D372B319F4}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=DropRestorer
DefaultDirName={localappdata}\Programs\DropRestorer
DefaultGroupName=DropRestorer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
OutputBaseFilename=DropRestorer-{#AppVersion}-Windows-x64-Setup
SetupIconFile=..\..\dist\build-resources\DropRestorer.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
Uninstallable=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "..\..\dist\DropRestorer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\DropRestorer"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\DropRestorer"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Запустить DropRestorer"; Flags: postinstall nowait skipifsilent
