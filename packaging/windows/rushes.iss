; Inno Setup script for the Rushes Windows installer.
; Built by .github/workflows/windows.yml:  ISCC /DMyAppVersion=0.2.0 packaging\windows\rushes.iss
; Expects the PyInstaller output (with FFmpeg copied in) at dist\Rushes.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "Rushes"
#define StudioExe "Rushes Studio.exe"

[Setup]
AppId={{6F1B6C2E-3B0E-4C55-9A57-5D2E3F9B7A11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Rushes contributors
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=RushesSetup-{#MyAppVersion}-x64
SetupIconFile=rushes.ico
UninstallDisplayIcon={app}\{#StudioExe}
LicenseFile=..\..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Lets the user pick "install for me only" (no admin rights needed) or "for all users".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ChangesEnvironment=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "addtopath"; Description: "Add the 'rushes' command-line tool to PATH"; GroupDescription: "Command line:"; Flags: unchecked

[Files]
Source: "..\..\dist\Rushes\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Rushes Studio"; Filename: "{app}\{#StudioExe}"
Name: "{group}\Rushes Command Prompt"; Filename: "{cmd}"; Parameters: "/K ""set ""PATH={app};%PATH%"" && rushes --help"""; WorkingDir: "{userdocs}"
Name: "{group}\Uninstall Rushes"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Rushes Studio"; Filename: "{app}\{#StudioExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#StudioExe}"; Description: "Start Rushes Studio"; Flags: nowait postinstall skipifsilent

[Code]
function EnvKeyRoot(): Integer;
begin
  if IsAdminInstallMode then Result := HKEY_LOCAL_MACHINE else Result := HKEY_CURRENT_USER;
end;

function EnvKeyPath(): String;
begin
  if IsAdminInstallMode then
    Result := 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
  else
    Result := 'Environment';
end;

function PathContains(Paths, Dir: String): Boolean;
begin
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') > 0;
end;

procedure AddToPath(Dir: String);
var Paths: String;
begin
  if not RegQueryStringValue(EnvKeyRoot(), EnvKeyPath(), 'Path', Paths) then Paths := '';
  if PathContains(Paths, Dir) then exit;
  if (Paths <> '') and (Copy(Paths, Length(Paths), 1) <> ';') then Paths := Paths + ';';
  RegWriteExpandStringValue(EnvKeyRoot(), EnvKeyPath(), 'Path', Paths + Dir);
end;

procedure RemoveFromPath(Dir: String);
var Paths: String; P: Integer;
begin
  if not RegQueryStringValue(EnvKeyRoot(), EnvKeyPath(), 'Path', Paths) then exit;
  Paths := ';' + Paths + ';';
  P := Pos(';' + Uppercase(Dir) + ';', Uppercase(Paths));
  if P = 0 then exit;
  Delete(Paths, P, Length(Dir) + 1);
  Paths := Copy(Paths, 2, Length(Paths) - 2);
  RegWriteExpandStringValue(EnvKeyRoot(), EnvKeyPath(), 'Path', Paths);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('addtopath') then
    AddToPath(ExpandConstant('{app}'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath(ExpandConstant('{app}'));
end;
