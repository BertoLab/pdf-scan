#ifndef BundleRoot
  #error BundleRoot must be defined by build_installer.py
#endif
#ifndef ReleaseId
  #error ReleaseId must be defined by build_installer.py
#endif
#ifndef Version
  #error Version must be defined by build_installer.py
#endif
#ifndef MarkerFile
  #error MarkerFile must be defined by build_installer.py
#endif
#ifndef ProductId
  #define ProductId "EstrattoreScanFatture.Desktop"
#endif
#ifndef DataRoot
  #define DataRoot "{localappdata}\EstrattoreScanFatture"
#endif
#ifndef BuildSuffix
  #define BuildSuffix ""
#endif
#ifndef ShortcutName
  #define ShortcutName "Estrattore Scan Fatture"
#endif
#ifndef InstallRoot
  #define InstallRoot "{localappdata}\Programs\EstrattoreScanFatture"
#endif
#ifndef SmokeBuild
  #define SmokeBuild 0
#endif

[Setup]
AppId={#ProductId}
AppName=Estrattore Scan Fatture
AppVersion={#Version}
DefaultDirName={#InstallRoot}
#if SmokeBuild
UsePreviousAppDir=no
#else
UsePreviousAppDir=yes
#endif
DisableDirPage=yes
PrivilegesRequired=lowest
SetupArchitecture=x64
ArchitecturesAllowed=x64os
MinVersion=10.0
AppMutex=Local\EstrattoreScanFattureDesktop
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\releases\{#ReleaseId}\EstrattoreScanFatture.exe
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
OutputBaseFilename=EstrattoreScanFatture-Setup-{#ReleaseId}{#BuildSuffix}

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "Crea un collegamento sul desktop"; Flags: unchecked

[Files]
Source: "{#BundleRoot}\*"; DestDir: "{app}\releases\{#ReleaseId}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MarkerFile}"; DestDir: "{app}\releases\{#ReleaseId}"; DestName: "release.complete"; Flags: ignoreversion; BeforeInstall: ConfigureRuntimeAcl

[Icons]
Name: "{autoprograms}\{#ShortcutName}"; Filename: "{app}\releases\{#ReleaseId}\EstrattoreScanFatture.exe"; Parameters: "--data-dir ""{#DataRoot}"""
Name: "{autodesktop}\{#ShortcutName}"; Filename: "{app}\releases\{#ReleaseId}\EstrattoreScanFatture.exe"; Parameters: "--data-dir ""{#DataRoot}"""; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\{#ProductId}"; ValueType: string; ValueName: "ActiveRelease"; ValueData: "{#ReleaseId}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\{#ProductId}"; ValueType: string; ValueName: "PreviousRelease"; ValueData: "{code:GetPreviousRelease}"

[UninstallDelete]
Type: filesandordirs; Name: "{#DataRoot}\config\webview-profile"

[Code]
var
  RemoveBusinessData: Boolean;
  PreviousRelease: String;
  ActiveRelease: String;

function InitializeSetup: Boolean;
begin
  Result := True;
  RegQueryStringValue(HKCU, 'Software\{#ProductId}', 'ActiveRelease', ActiveRelease);
  if ActiveRelease = '{#ReleaseId}' then
    RegQueryStringValue(HKCU, 'Software\{#ProductId}', 'PreviousRelease', PreviousRelease)
  else
    PreviousRelease := ActiveRelease;
end;

function GetPreviousRelease(Param: String): String;
begin
  Result := PreviousRelease;
end;

#if SmokeBuild
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if CompareText(RemoveBackslashUnlessRoot(ExpandConstant('{app}')),
      RemoveBackslashUnlessRoot(ExpandConstant('{#InstallRoot}'))) <> 0 then
    Result := 'Il pacchetto di prova deve usare la propria cartella isolata.';
end;
#endif

procedure PruneOldReleases;
var
  RootPath, ReleasePath: String;
  Entry: TFindRec;
begin
  RootPath := ExpandConstant('{app}\releases');
  if not FileExists(RootPath + '\{#ReleaseId}\release.complete') then
    Exit;
  if FindFirst(RootPath + '\*', Entry) then
  try
    repeat
      if (Entry.Name <> '.') and (Entry.Name <> '..') and
         (Entry.Name <> '{#ReleaseId}') and (Entry.Name <> PreviousRelease) then
      begin
        ReleasePath := RootPath + '\' + Entry.Name;
        if DirExists(ReleasePath) and not DelTree(ReleasePath, True, True, True) then
          Log('Could not remove old release: ' + ReleasePath);
      end;
    until not FindNext(Entry);
  finally
    FindClose(Entry);
  end;
end;

procedure RemoveInterruptedUninstallLogs;
var
  AppPath, ActiveUninstaller, DataPath, ExePath: String;
  Entry: TFindRec;
begin
  AppPath := AddBackslash(ExpandConstant('{app}'));
  ActiveUninstaller := ExpandConstant('{uninstallexe}');
  if not FileExists(ActiveUninstaller) then
    Exit;
  if FindFirst(AppPath + 'unins*.dat', Entry) then
  try
    repeat
      ExePath := AppPath + Copy(Entry.Name, 1, Length(Entry.Name) - 4) + '.exe';
      if (Entry.SizeHigh = 0) and (Entry.SizeLow = 0) and
         (CompareText(ExePath, ActiveUninstaller) <> 0) then
      begin
        DataPath := AppPath + Entry.Name;
        if DeleteFile(DataPath) then
        begin
          if FileExists(ExePath) and not DeleteFile(ExePath) then
            Log('Could not remove interrupted uninstaller: ' + ExePath);
        end
        else
          Log('Could not remove interrupted uninstall log: ' + DataPath);
      end;
    until not FindNext(Entry);
  finally
    FindClose(Entry);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    PruneOldReleases
  else if CurStep = ssDone then
    RemoveInterruptedUninstallLogs;
end;

procedure ConfigureRuntimeAcl;
var
  RuntimePath: String;
  ExitCode: Integer;
begin
  RuntimePath := ExpandConstant('{app}\releases\{#ReleaseId}\_internal\vendor\webview2');
  if not DirExists(RuntimePath) then
    RaiseException('Il motore WebView2 non e presente nel pacchetto.');
  if not Exec(ExpandConstant('{sys}\icacls.exe'),
    '"' + RuntimePath + '" /grant *S-1-15-2-2:(OI)(CI)(RX) *S-1-15-2-1:(OI)(CI)(RX) /T',
    '', SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
    RaiseException('Impossibile configurare i permessi del motore WebView2.');
end;

function HasRemoveDataOption: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/REMOVEAPPDATA') = 0 then
      Result := True;
end;

function InitializeUninstall: Boolean;
var
  DataPath: String;
begin
  Result := True;
  DataPath := ExpandConstant('{#DataRoot}');
  RemoveBusinessData := HasRemoveDataOption;
  if (not RemoveBusinessData) and (not UninstallSilent) and DirExists(DataPath) then
    RemoveBusinessData := MsgBox(
      'Vuoi eliminare anche tutti i dati dell''app? Questa operazione cancella fatture, PDF archiviati, anagrafica e bozze da:' + #13#10 + DataPath + #13#10 + #13#10 +
      'Scegli No per conservare i dati.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataPath: String;
begin
  if (CurUninstallStep <> usPostUninstall) or (not RemoveBusinessData) then
    Exit;
  DataPath := ExpandConstant('{#DataRoot}');
  if DirExists(DataPath) and not DelTree(DataPath, True, True, True) then
    MsgBox('Non sono riuscito a eliminare tutti i dati dell''app in ' + DataPath +
      '. Controlla la cartella manualmente.', mbError, MB_OK);
end;
