; Per-user installer: needs no administrator rights and never touches saved reports.
; Built by packaging\build_installer.ps1, which passes AppVersion and RootDir.
#define AppName "Research Report Generator"

[Setup]
AppId={{7D2C4B7E-52A1-4B57-9E1B-3F0B7C6A91D4}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Cabiao Senior High School
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#RootDir}\dist\installer
OutputBaseFilename=ResearchReportGenerator-Setup-{#AppVersion}
SetupIconFile={#RootDir}\packaging\app.ico
UninstallDisplayIcon={app}\ResearchReportGenerator.exe
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern
CloseApplications=yes
; Windows 10 version 1809 or newer, so older systems get a clear message instead of a failed start.
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#RootDir}\dist\ResearchReportGenerator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; A new version must not run alongside leftover files from an older one.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\ResearchReportGenerator.exe"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\ResearchReportGenerator.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ResearchReportGenerator.exe"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
function HasWebView2(): Boolean;
var
  Version: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) or
    RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version);
  Result := Result and (Version <> '') and (Version <> '0.0.0.0');
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and not HasWebView2() then
    MsgBox('This computer needs Microsoft Edge WebView2 to show the app. It is free from microsoft.com/edge/webview2. Windows 11 already includes it.', mbInformation, MB_OK);
end;
