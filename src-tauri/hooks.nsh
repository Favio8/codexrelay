; These paths are shared by install, upgrade and uninstall. A test scope isolates
; actual NSIS runs without changing the user's registrations or shortcuts.
Var RelayLocalBase
Var RelayProductKey
Var RelayUninstallKey
Var RelayStartMenu
Var RelayDesktop
!define RELAY_MAINTENANCE_SOURCE "${__FILEDIR__}\..\scripts\installer-maintenance.ps1"
!define RELAY_UPGRADE_SOURCE "${__FILEDIR__}\..\engine\codexrelay\upgrade.py"
!define MUI_CUSTOMFUNCTION_ABORT RelayAbortUpgrade

!macro RELAY_INIT_PATHS
  StrCpy $RelayLocalBase "$LOCALAPPDATA"
  StrCpy $RelayProductKey "Software\codexrelay\CodexRelay"
  StrCpy $RelayUninstallKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\CodexRelay"
  StrCpy $RelayStartMenu "$SMPROGRAMS"
  StrCpy $RelayDesktop "$DESKTOP"
  ReadEnvStr $0 "CODEXRELAY_INSTALL_TEST_ROOT"
  ${If} $0 != ""
    StrCpy $RelayLocalBase "$0\Local"
    StrCpy $RelayStartMenu "$0\StartMenu"
    StrCpy $RelayDesktop "$0\Desktop"
    ${GetFileName} "$0" $1
    StrCpy $RelayProductKey "Software\CodexRelay\InstallerTests\$1\Product"
    StrCpy $RelayUninstallKey "Software\CodexRelay\InstallerTests\$1\Uninstall"
  ${EndIf}
!macroend

!macro NSIS_HOOK_ONINIT
  !insertmacro RELAY_INIT_PATHS
  InitPluginsDir
  SetOutPath $PLUGINSDIR
  File /oname=installer-maintenance.ps1 "${RELAY_MAINTENANCE_SOURCE}"
  File /oname=upgrade.py "${RELAY_UPGRADE_SOURCE}"
!macroend

!macro NSIS_HOOK_UNINIT
  !insertmacro RELAY_INIT_PATHS
!macroend

!macro RELAY_MAINTENANCE action
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\installer-maintenance.ps1" -Action ${action} -InstallDirectory "$INSTDIR" -PlanPath "$PLUGINSDIR\codexrelay-upgrade.json" -UpgradeScript "$PLUGINSDIR\upgrade.py" -ExpectedVersion "${VERSION}"'
  Pop $0
  Pop $1
!macroend

Function RelayAbortUpgrade
  IfFileExists "$PLUGINSDIR\codexrelay-upgrade.json" 0 relay_abort_done
  !insertmacro RELAY_MAINTENANCE Abort
  relay_abort_done:
FunctionEnd

Function .onInstFailed
  Call RelayAbortUpgrade
FunctionEnd

!macro NSIS_HOOK_PREINSTALL
  !insertmacro RELAY_MAINTENANCE Prepare
  ${If} $0 != "0"
    Call RelayAbortUpgrade
    MessageBox MB_OK|MB_ICONEXCLAMATION "升级未开始：$1" /SD IDOK
    Abort
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTINSTALL
  !insertmacro RELAY_MAINTENANCE Finish
  ${If} $0 != "0"
    Call RelayAbortUpgrade
    MessageBox MB_OK|MB_ICONEXCLAMATION "升级未完成：$1" /SD IDOK
    Abort
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ${If} $UpdateMode = 1
    ExecWait '"$INSTDIR\runtime\python.exe" -I -X utf8 -m codexrelay check-idle' $0
  ${Else}
    ExecWait '"$INSTDIR\runtime\python.exe" -I -X utf8 -m codexrelay uninstall' $0
  ${EndIf}
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONEXCLAMATION "请先打开 CodexRelay，结束运行中的任务并处理待核验修改，再重试卸载或更新。"
    Abort
  ${EndIf}
  ${If} $UpdateMode <> 1
    IfSilent relay_keep_keys
    MessageBox MB_YESNO|MB_ICONQUESTION "是否同时删除 CodexRelay 保存的供应商 API Key、任务历史与配置备份？选择否可在重新安装后继续使用。" IDNO relay_keep_keys
    ExecWait '"$INSTDIR\runtime\python.exe" -I -X utf8 -m codexrelay purge' $0
    relay_keep_keys:
  ${EndIf}
!macroend
