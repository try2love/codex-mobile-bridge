!macro customUnInstallSection
  Var CmbDataCleanupSafe

  Function un.CmbCheckDataDirectory
    Exch $R0
    Push $R1
    Push $R2
    Push $R3
    System::Call 'kernel32::GetFileAttributesW(w "$R0") i.s'
    Pop $R3
    ${if} $R3 == -1
      StrCpy $CmbDataCleanupSafe 0
    ${else}
      IntOp $R1 $R3 & 0x400
      ${if} $R1 != 0
        StrCpy $CmbDataCleanupSafe 0
      ${else}
        IntOp $R3 $R3 & 0x10
        ${if} $R3 != 0
          ClearErrors
          FindFirst $R1 $R2 "$R0\*.*"
          ${if} ${Errors}
            StrCpy $CmbDataCleanupSafe 0
          ${else}
            ${do}
              ${if} $R2 != "."
              ${andIf} $R2 != ".."
                ${if} $R2 == "claude-accounts"
                  StrCpy $CmbDataCleanupSafe 0
                ${else}
                  Push "$R0\$R2"
                  Call un.CmbCheckDataDirectory
                ${endif}
              ${endif}
              ${if} $CmbDataCleanupSafe == 0
                ${exitDo}
              ${endif}
              FindNext $R1 $R2
            ${loopUntil} ${Errors}
            FindClose $R1
          ${endif}
        ${endif}
      ${endif}
    ${endif}
    Pop $R3
    Pop $R2
    Pop $R1
    Pop $R0
  FunctionEnd

  Section /o "un.清理网关设置与缓存" SEC_REMOVE_CMB_DATA
    ${ifNot} ${isUpdated}
      StrCpy $0 "$APPDATA\codex-mobile-bridge-desktop"
      ${if} ${FileExists} "$0\gateway\gateway-control.json"
        MessageBox MB_OK|MB_ICONEXCLAMATION "网关尚有运行控制记录。为保护当前连接与资料，已保留设置目录；请先停止网关后再清理。"
        Goto un_skip_cmb_data
      ${endif}
      ${if} $APPDATA != ""
        ${if} $0 != "$APPDATA"
          System::Call 'kernel32::GetFileAttributesW(w "$0") i.r1'
          ${if} $1 != -1
            IntOp $2 $1 & 0x10
            ${if} $2 != 0
              IntOp $2 $1 & 0x400
              ${if} $2 == 0
                StrCpy $CmbDataCleanupSafe 1
                Push "$0"
                Call un.CmbCheckDataDirectory
                ${if} $CmbDataCleanupSafe == 1
                  MessageBox MB_YESNO|MB_ICONEXCLAMATION "仅删除 Codex Mobile Bridge 自己的本机设置与缓存：$\r$\n$0$\r$\nCodex 和其他 Agent 的账号、会话及配置不会删除。确认继续？" IDNO un_skip_cmb_data
                  RMDir /r "$0"
                ${else}
                  MessageBox MB_OK|MB_ICONEXCLAMATION "网关目录含有客户端账号资料、目录链接或无法核验的项目。为保护 Agent 数据，已保留该目录，请仅手动清理确认属于网关的文件。"
                ${endif}
              ${else}
                MessageBox MB_OK|MB_ICONEXCLAMATION "网关数据目录是重解析路径。为保护其他应用数据，未删除该目录。"
              ${endif}
            ${endif}
          ${endif}
        ${endif}
      ${endif}
    ${endIf}
un_skip_cmb_data:
  SectionEnd
!macroend
