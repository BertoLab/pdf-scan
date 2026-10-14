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
#ifndef ArtifactBaseName
  #error ArtifactBaseName must be defined by build_installer.py
#endif
#ifndef ProductId
  #define ProductId "EstrattoreScanFatture.Desktop"
#endif
#ifndef DataRoot
  #define DataRoot "{localappdata}\EstrattoreScanFatture"
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
SetupIconFile=assets\Scan_icona_trasparente.ico
DefaultDirName={#InstallRoot}
#if SmokeBuild
UsePreviousAppDir=no
#else
UsePreviousAppDir=yes
#endif
DisableDirPage=yes
DisableProgramGroupPage=yes
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
OutputBaseFilename={#ArtifactBaseName}

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "Crea un collegamento sul desktop"; Flags: unchecked

[Files]
Source: "{#BundleRoot}\*"; DestDir: "{app}\releases\{#ReleaseId}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#MarkerFile}"; DestDir: "{app}\releases\{#ReleaseId}"; DestName: "release.complete"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#ShortcutName}"; Filename: "{app}\releases\{#ReleaseId}\EstrattoreScanFatture.exe"; Parameters: "--data-dir ""{#DataRoot}"""
Name: "{autodesktop}\{#ShortcutName}"; Filename: "{app}\releases\{#ReleaseId}\EstrattoreScanFatture.exe"; Parameters: "--data-dir ""{#DataRoot}"""; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\{#ProductId}"; ValueType: string; ValueName: "ActiveRelease"; ValueData: "{#ReleaseId}"; Flags: uninsdeletekey

[UninstallDelete]
Type: filesandordirs; Name: "{#DataRoot}\config\webview-profile"

[Code]
var
  RemoveBusinessData: Boolean;
  SetupValidationFailed: Boolean;

#if SmokeBuild
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if CompareText(RemoveBackslashUnlessRoot(ExpandConstant('{app}')),
      RemoveBackslashUnlessRoot(ExpandConstant('{#InstallRoot}'))) <> 0 then
    Result := 'Il pacchetto di prova deve usare la propria cartella isolata.';
end;
#endif

function PruneOldReleases: Boolean;
var
  RootPath, ReleasePath: String;
  Entry: TFindRec;
begin
  Result := True;
  RootPath := ExpandConstant('{app}\releases');
  if not FileExists(RootPath + '\{#ReleaseId}\release.complete') then
  begin
    Result := False;
    Exit;
  end;
  if FindFirst(RootPath + '\*', Entry) then
  try
    repeat
      if (Entry.Name <> '.') and (Entry.Name <> '..') and
         (Entry.Name <> '{#ReleaseId}') then
      begin
        ReleasePath := RootPath + '\' + Entry.Name;
        if DirExists(ReleasePath) and not DelTree(ReleasePath, True, True, True) then
        begin
          Result := False;
          Log('Could not remove old release: ' + ReleasePath);
        end;
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

function ValidateInstalledRuntime: Boolean;
var
  ReleasePath, RuntimePath: String;
  ExitCode: Integer;
begin
  ReleasePath := ExpandConstant('{app}\releases\{#ReleaseId}');
  RuntimePath := ReleasePath + '\_internal\vendor\webview2';
  Result := FileExists(ReleasePath + '\EstrattoreScanFatture.exe') and
    FileExists(ReleasePath + '\release.complete') and
    FileExists(ReleasePath + '\_internal\apps\viewer\dist\index.html') and
    FileExists(ReleasePath + '\_internal\vendor\poppler\bin\pdfinfo.exe') and
    FileExists(ReleasePath + '\_internal\vendor\tesseract\tesseract.exe') and
    FileExists(RuntimePath + '\msedgewebview2.exe');
  if not Result then
    Exit;
  Result := Exec(ExpandConstant('{sys}\icacls.exe'),
    '"' + RuntimePath + '" /grant *S-1-15-2-2:(OI)(CI)(RX) *S-1-15-2-1:(OI)(CI)(RX) /T',
    '', SW_HIDE, ewWaitUntilTerminated, ExitCode) and (ExitCode = 0);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not ValidateInstalledRuntime then
    begin
      SetupValidationFailed := True;
      Log('Installed runtime validation failed; old releases retained.');
    end
    else if not PruneOldReleases then
    begin
      SetupValidationFailed := True;
      Log('Old release cleanup failed; rerun Setup to retry.');
    end
    else
      RegDeleteValue(HKCU, 'Software\{#ProductId}', 'PreviousRelease');
    if SetupValidationFailed and not WizardSilent then
      MsgBox('L''aggiornamento non e stato completato. La versione precedente e stata conservata. Ripeti l''installazione o controlla il registro dell''installer.', mbError, MB_OK);
  end
  else if CurStep = ssDone then
    RemoveInterruptedUninstallLogs;
end;

function GetCustomSetupExitCode: Integer;
begin
  if SetupValidationFailed then
    Result := 10
  else
    Result := 0;
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
