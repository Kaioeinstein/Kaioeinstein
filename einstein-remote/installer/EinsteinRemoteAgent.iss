; EINSTEIN REMOTE AGENT — instalador para Windows (Inno Setup 6)
;
; Gerado por installer\build-agent.ps1 (que passa /DAppVersion e /DSourceDir). Manualmente:
;   ISCC /DAppVersion=1.0.0 /DSourceDir=..\dist\EinsteinRemoteAgent installer\EinsteinRemoteAgent.iss
;
; Princípios (iguais aos do agente):
;   * instala só para o usuário atual, sem pedir administrador;
;   * NÃO ativa o início automático por conta própria: é uma opção desmarcada, explicada na tela;
;   * a desinstalação remove tudo — programa, atalhos, início automático e (se o usuário quiser) o vínculo.
;   Silencioso: Setup.exe /VERYSILENT [/TASKS="autostart"]     Desinstalar: unins000.exe /VERYSILENT [/PURGE]

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\EinsteinRemoteAgent"
#endif
#define AppName "EINSTEIN REMOTE AGENT"
#define TrayExe "EinsteinRemoteAgent.exe"
#define CliExe "einstein-agent.exe"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"
#define RunValue "Einstein Remote Agent"

[Setup]
AppId={{EFAF17C3-F513-4AC6-BEF6-172EC2E25FAB}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=EINSTEIN REMOTE
AppComments=Monitoramento e controle do seu próprio computador, com sua autorização.
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Instalador do {#AppName}
DefaultDirName={localappdata}\Programs\EinsteinRemote Agent
DefaultGroupName=EINSTEIN REMOTE
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
SetupIconFile=build\einstein.ico
UninstallDisplayIcon={app}\{#TrayExe}
UninstallDisplayName={#AppName}
InfoBeforeFile=LEIA-ANTES.txt
OutputDir=..\dist
OutputBaseFilename=EinsteinRemoteAgent-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
CloseApplications=force
RestartApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[CustomMessages]
ptbr.TaskAutostart=Iniciar o agente junto com o Windows (ícone visível na bandeja; dá para desligar a qualquer momento)
ptbr.TaskDesktop=Criar atalho na Área de Trabalho
ptbr.GroupOptions=Opções:
ptbr.RunAgent=Abrir o EINSTEIN REMOTE AGENT agora (para vincular este computador)
ptbr.PurgeAsk=Remover também o vínculo e a configuração deste computador?%n%nSim: a credencial do dispositivo, as permissões escolhidas e os registros são apagados (recomendado).%nNão: mantém para uma reinstalação.%n%nLembre-se de revogar o dispositivo no painel (Dispositivos > Revogar).

[Tasks]
Name: "autostart"; Description: "{cm:TaskAutostart}"; GroupDescription: "{cm:GroupOptions}"; Flags: unchecked
Name: "desktopicon"; Description: "{cm:TaskDesktop}"; GroupDescription: "{cm:GroupOptions}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#TrayExe}"; Comment: "Agente na bandeja: status, pausar, permissões"
Name: "{autoprograms}\EINSTEIN REMOTE\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#TrayExe}"; Tasks: desktopicon

[Registry]
; mesma entrada que o menu "Iniciar com o Windows" cria — visível em Gerenciador de Tarefas > Inicializar
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "{#RunValue}"; ValueData: """{app}\{#TrayExe}"""; Tasks: autostart
; na tela, a opção mostra o estado atual; desmarcada ali, o início automático é desligado.
; Silencioso, só liga com /TASKS="autostart" e nunca desliga o que o dono ativou pela bandeja.
Root: HKCU; Subkey: "{#RunKey}"; ValueType: none; ValueName: "{#RunValue}"; Flags: deletevalue; Check: AutostartUnchecked

[Run]
Filename: "{app}\{#TrayExe}"; Description: "{cm:RunAgent}"; Flags: postinstall nowait skipifsilent

[Code]
procedure StopAgent();
var
  Code: Integer;
begin
  { o agente não guarda nada pendente: encerrá-lo é seguro. Sem /F primeiro (pede para fechar). }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#TrayExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(800);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#TrayExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM {#CliExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

var
  TasksPageSeen: Boolean;

procedure CurPageChanged(CurPageID: Integer);
begin
  { a opção reflete se o início automático está ligado agora (pode ter sido ligado pela bandeja) }
  if (CurPageID = wpSelectTasks) and not TasksPageSeen then
  begin
    TasksPageSeen := True;
    if RegValueExists(HKEY_CURRENT_USER, '{#RunKey}', '{#RunValue}') then
      WizardSelectTasks('autostart')
    else
      WizardSelectTasks('!autostart');
  end;
end;

function AutostartUnchecked(): Boolean;
begin
  Result := (not WizardSilent()) and (not WizardIsTaskSelected('autostart'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopAgent();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopAgent();
  Result := True;
end;

function HasParam(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
      Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Purge: Boolean;
begin
  if CurUninstallStep = usUninstall then
  begin
    { o início automático pode ter sido ligado depois, pela bandeja: remove sempre }
    RegDeleteValue(HKEY_CURRENT_USER, '{#RunKey}', '{#RunValue}');
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    if UninstallSilent() then
      Purge := HasParam('/PURGE')
    else
      Purge := MsgBox(CustomMessage('PurgeAsk'), mbConfirmation, MB_YESNO) = IDYES;
    if Purge then
    begin
      DelTree(ExpandConstant('{userappdata}\EinsteinRemote'), True, True, True);
      DelTree(ExpandConstant('{localappdata}\EinsteinRemote\logs'), True, True, True);
      RemoveDir(ExpandConstant('{localappdata}\EinsteinRemote'));
    end;
  end;
end;
