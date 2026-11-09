#ifndef AppVersion
#define AppVersion "0.19.0"
#endif
[Setup]
AppId={{F5CD38AF-0851-4CC3-BBF2-BD91DD429051}
AppName=Webix Lead Desk
AppVersion={#AppVersion}
AppPublisher=Webix Solutions
DefaultDirName={localappdata}\Programs\WebixLeadDesk
DefaultGroupName=Webix Lead Desk
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist\installer
OutputBaseFilename=Webix-Lead-Desk-Setup-{#AppVersion}
SetupIconFile=webix.ico
UninstallDisplayIcon={app}\WebixLeadDesk.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
AppMutex=Local\WebixLeadDeskDesktop
CloseApplications=no
RestartApplications=no
[Files]
Source: "..\..\dist\WebixLeadDesk\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\Webix Lead Desk"; Filename: "{app}\WebixLeadDesk.exe"
Name: "{autodesktop}\Webix Lead Desk"; Filename: "{app}\WebixLeadDesk.exe"
[Run]
Filename: "{app}\WebixLeadDesk.exe"; Description: "Open Webix Lead Desk"; Flags: nowait postinstall skipifsilent
[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "WebixLeadDeskWorker"; Flags: uninsdeletevalue
; Private settings/backups live outside {app}; uninstall never deletes them.
