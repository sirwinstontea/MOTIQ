; MOTIQ Windows installer (Inno Setup 6.3+ / 7). Build with installer\build.ps1, not by hand: it stages the files first.
; One click, no admin: copies MOTIQ to %LOCALAPPDATA%\Programs\MOTIQ and opens MOTIQ.exe, whose setup wizard asks the
; questions (robot, run mode, camera, key, ...), installs Python/Node packages and starts the app.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6F3D2B8E-4C1A-4E7B-9A55-2D7C0E1B9F41}
AppName=MOTIQ
AppVersion={#AppVersion}
AppPublisher=MOTIQ
AppPublisherURL=https://github.com/sirwinstontea/MOTIQ
AppSupportURL=https://github.com/sirwinstontea/MOTIQ/issues
DefaultDirName={localappdata}\Programs\MOTIQ
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=MOTIQ-Setup
SetupIconFile=motiq.ico
UninstallDisplayIcon={app}\MOTIQ.exe
UninstallDisplayName=MOTIQ
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Files]
; everything from a clean checkout, except the cell files a user may have edited (kept on upgrade, see below)
Source: "stage\*"; DestDir: "{app}"; Excludes: "\robot-chooser\hardware.yaml,\robot-chooser\catalog.yaml"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "stage\robot-chooser\hardware.yaml"; DestDir: "{app}\robot-chooser"; Flags: onlyifdoesntexist
Source: "stage\robot-chooser\catalog.yaml"; DestDir: "{app}\robot-chooser"; Flags: onlyifdoesntexist

[Icons]
Name: "{autoprograms}\MOTIQ"; Filename: "{app}\MOTIQ.exe"
Name: "{autoprograms}\MOTIQ Settings"; Filename: "{app}\MOTIQ.exe"; Parameters: "--setup"
Name: "{autoprograms}\Stop MOTIQ"; Filename: "{app}\MOTIQ.exe"; Parameters: "--stop"
Name: "{autodesktop}\MOTIQ"; Filename: "{app}\MOTIQ.exe"

[Run]
; no Finished page, so this runs straight after copying: the MOTIQ setup wizard opens
Filename: "{app}\MOTIQ.exe"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\MOTIQ.exe"; Parameters: "--stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopMotiq"

[UninstallDelete]
; venvs, node_modules, downloaded tools, settings and keys (.motiq) were created after install
Type: filesandordirs; Name: "{app}"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { upgrading while MOTIQ runs: stop its services first so their files can be replaced }
  if FileExists(ExpandConstant('{app}\MOTIQ.exe')) then
    Exec(ExpandConstant('{app}\MOTIQ.exe'), '--stop', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;
