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
