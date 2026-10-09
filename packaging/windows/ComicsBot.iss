; Inno Setup script for the Windows installer (ComicsBot-<version>-setup.exe).
;
; Build it with scripts/build_setup.ps1, which runs:
;   iscc /DAppVersion=<version> /DSourceDir=<extracted windows zip> /O<output dir> ComicsBot.iss
;
; The wizard copies the bot into %LOCALAPPDATA%\ComicsBot and then runs install.ps1 in that folder,
; which sets up Python and the dependencies, writes .env with the token (readable only by the user)
; and registers the scheduled task. The zip's install.bat does the same, so both installs are
; interchangeable and can upgrade each other.
;
; Unattended: set DISCORD_TOKEN, then run
;   ComicsBot-<version>-setup.exe /VERYSILENT [/TASKS=startatboot]
; and uninstall with unins000.exe /VERYSILENT [/PURGE].
; Add "autoupdate" to /TASKS (and set COMICSBOT_GITHUB_TOKEN) to turn on automatic updates. Leaving the
; task unchecked keeps the current setting; install.bat -DisableAutoUpdate turns them off.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #error Pass /DSourceDir=<folder with the extracted ComicsBot-<version>-windows.zip>
#endif

[Setup]
AppId={{E6949408-6B7C-479B-887F-4C546A3B2278}
AppName=ComicsBot
AppVersion={#AppVersion}
AppVerName=ComicsBot {#AppVersion}
AppPublisher=dxcufgb
AppPublisherURL=https://github.com/dxcufgb/DiscordComicsBot
AppSupportURL=https://github.com/dxcufgb/DiscordComicsBot/issues
DefaultDirName={localappdata}\ComicsBot
DisableDirPage=yes
DefaultGroupName=ComicsBot
DisableProgramGroupPage=yes
; Installs for the current user; no administrator rights needed unless "start at boot" is wanted.
PrivilegesRequired=lowest
MinVersion=10.0
; Run as a 64-bit installer so {sys} is the 64-bit System32 (and the 64-bit PowerShell).
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=ComicsBot-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=ComicsBot
CloseApplications=no

[Tasks]
Name: "startatboot"; Description: "Start the bot when Windows starts, even if nobody is logged in"; \
  Check: IsAdmin; Flags: unchecked
Name: "autoupdate"; Description: "Install new ComicsBot releases automatically (checked daily; needs a GitHub token)"; \
  Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\Restart ComicsBot"; Filename: "{app}\install.bat"; Parameters: "-Restart"; WorkingDir: "{app}"
Name: "{group}\ComicsBot log"; Filename: "{app}\discord.log"
Name: "{group}\ComicsBot settings (.env)"; Filename: "{sys}\notepad.exe"; Parameters: """{app}\.env"""
Name: "{group}\Uninstall ComicsBot"; Filename: "{uninstallexe}"

[Code]
var
  TokenPage: TInputQueryWizardPage;
  GitHubTokenPage: TInputQueryWizardPage;
  PurgeData: Boolean;

function SetEnvironmentVariable(lpName, lpValue: String): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

function InstallDir(): String;
begin
  Result := ExpandConstant('{localappdata}\ComicsBot');
end;

{ An upgrade keeps the existing .env, so the token is only needed for a first install. }
function HasSettings(): Boolean;
begin
  Result := FileExists(InstallDir() + '\.env');
end;

function Token(): String;
begin
  Result := Trim(TokenPage.Values[0]);
  if Result = '' then
    Result := Trim(GetEnv('DISCORD_TOKEN'));
end;

{ Automatic updates keep their GitHub token here (readable only by the user); see install.ps1. }
function HasGitHubToken(): Boolean;
begin
  Result := FileExists(InstallDir() + '\updater\github-token');
end;

function GitHubToken(): String;
begin
  Result := Trim(GitHubTokenPage.Values[0]);
  if Result = '' then
    Result := Trim(GetEnv('COMICSBOT_GITHUB_TOKEN'));
end;

function PowerShell(Params: String; Show: Integer; var Code: Integer): Boolean;
begin
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass ' + Params, ExpandConstant('{tmp}'), Show, ewWaitUntilTerminated, Code);
end;

procedure InitializeWizard();
begin
  TokenPage := CreateInputQueryPage(wpSelectTasks, 'Discord bot token',
    'The token lets ComicsBot log in to Discord.',
    'Create an application at https://discord.com/developers/applications, open Bot and click ' +
    'Reset Token, then paste the token here. It is stored in %LOCALAPPDATA%\ComicsBot\.env, ' +
    'readable only by your Windows account.' + #13#10#13#10 +
    'On the same Bot page, turn on "Server Members Intent" under Privileged Gateway Intents and save. ' +
    'Without it update notices go to server owners only, not to all server admins.');
  TokenPage.Add('Bot token:', True);
  GitHubTokenPage := CreateInputQueryPage(TokenPage.ID, 'GitHub token for automatic updates',
    'New releases are downloaded from the private GitHub repository dxcufgb/DiscordComicsBot.',
    'Create a fine-grained token at https://github.com/settings/personal-access-tokens/new with access to ' +
    'only dxcufgb/DiscordComicsBot and the permission Contents: Read-only, then paste it here. It is stored ' +
    'in %LOCALAPPDATA%\ComicsBot\updater, readable only by your Windows account.');
  GitHubTokenPage.Add('GitHub token:', True);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = TokenPage.ID then
    Result := HasSettings() or (Trim(GetEnv('DISCORD_TOKEN')) <> '');
  if PageID = GitHubTokenPage.ID then
    Result := not WizardIsTaskSelected('autoupdate') or HasGitHubToken() or
      (Trim(GetEnv('COMICSBOT_GITHUB_TOKEN')) <> '');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = TokenPage.ID) and (Token() = '') then
  begin
    MsgBox('Please paste the bot token.', mbError, MB_OK);
    Result := False;
  end;
  if (CurPageID = GitHubTokenPage.ID) and (GitHubToken() = '') then
  begin
    MsgBox('Please paste the GitHub token, or go back and uncheck automatic updates.', mbError, MB_OK);
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Result := '';
  if not HasSettings() and (Token() = '') then
  begin
    Result := 'A Discord bot token is required. For an unattended install, set the DISCORD_TOKEN environment variable.';
    exit;
  end;
  { The wizard asks for the token on its own page; this is for /TASKS=autoupdate without COMICSBOT_GITHUB_TOKEN. }
  if WizardIsTaskSelected('autoupdate') and not HasGitHubToken() and (GitHubToken() = '') and
    (Pos('AUTOUPDATE', Uppercase(GetCmdTail())) > 0) then
  begin
    Result := 'Automatic updates need a GitHub token. For an unattended install, set the COMICSBOT_GITHUB_TOKEN environment variable.';
    exit;
  end;
  { Stop a running bot before its files are replaced. }
  if FileExists(InstallDir() + '\install.ps1') and DirExists(InstallDir() + '\.venv') then
    PowerShell('-File "' + InstallDir() + '\install.ps1" -InstallDir "' + InstallDir() + '" -Stop', SW_HIDE, Code);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Params: String;
  Code: Integer;
begin
  if CurStep <> ssPostInstall then
    exit;
  { The token reaches install.ps1 through the environment, never on a command line. }
  if Token() <> '' then
    SetEnvironmentVariable('DISCORD_TOKEN', Token());
  Params := '-File "' + ExpandConstant('{app}') + '\install.ps1" -InstallDir "' + ExpandConstant('{app}') + '" -Yes';
  if WizardIsTaskSelected('startatboot') then
    Params := Params + ' -AtStartup';
  { Unchecked keeps the current setting. A box remembered from an earlier setup run is ignored when the
    token has been removed since (install.bat -DisableAutoUpdate) and no new one was given. }
  if WizardIsTaskSelected('autoupdate') and (HasGitHubToken() or (GitHubToken() <> '')) then
  begin
    if GitHubToken() <> '' then
      SetEnvironmentVariable('COMICSBOT_GITHUB_TOKEN', GitHubToken());
    Params := Params + ' -EnableAutoUpdate';
  end;
  Log('Running install.ps1 with: ' + Params);
  if not PowerShell(Params, SW_SHOW, Code) then
    Code := -1;
  SetEnvironmentVariable('DISCORD_TOKEN', '');
  SetEnvironmentVariable('COMICSBOT_GITHUB_TOKEN', '');
  Log(Format('install.ps1 exit code: %d (see install.log in the install folder)', [Code]));
  if Code <> 0 then
    SuppressibleMsgBox('ComicsBot was installed, but the bot did not log in to Discord.' + #13#10#13#10 +
      'Check the token in "ComicsBot settings (.env)" in the Start menu, then use "Restart ComicsBot". ' +
      'Details are in "ComicsBot log" and in ' + ExpandConstant('{app}') + '\install.log.', mbError, MB_OK, IDOK);
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if UninstallSilent() then
    PurgeData := Pos('/PURGE', Uppercase(GetCmdTail())) > 0
  else
    PurgeData := MsgBox('Also delete the bot token, settings and data in ' + InstallDir() + '?' + #13#10#13#10 +
      'Choose No to keep them for a later reinstall.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  { install.ps1 stops the bot, removes the scheduled task and the virtual environment. }
  if CurUninstallStep = usUninstall then
    PowerShell('-File "' + ExpandConstant('{app}') + '\install.ps1" -InstallDir "' + ExpandConstant('{app}') + '" -Uninstall',
      SW_HIDE, Code);
  if (CurUninstallStep = usPostUninstall) and PurgeData then
    DelTree(ExpandConstant('{app}'), True, True, True);
end;
