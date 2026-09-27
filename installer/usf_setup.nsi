; Universal Scene Forge — NSIS 安装向导脚本
; 前置: pyinstaller installer/UniversalSceneForge.spec --noconfirm
; 构建: makensis installer\usf_setup.nsi
; 产物: installer\UniversalSceneForge_Setup.exe

Unicode true                ; 中文界面必需
ManifestDPIAware true
SetCompressor /SOLID lzma   ; 固体 LZMA 压缩: 单文件安装包体积最优

!define APPNAME     "Universal Scene Forge"
!define APPVERSION  "0.9.5-preview"
!define COMPANY     "USF Project"
!define APPID       "{8F4B2C1D-9A7E-4D3F-B5C6-1E2A3B4C5D6E}"
!define DISTDIR     "..\dist\UniversalSceneForge"

Name "${APPNAME} ${APPVERSION}"
OutFile "UniversalSceneForge_0.9.5_Preview_Setup.exe"
InstallDir "$PROGRAMFILES64\${APPNAME}"
InstallDirRegKey HKLM "Software\${APPNAME}" "InstallDir"
RequestExecutionLevel admin              ; 需管理员: 写 Program Files + 注册表

!include "MUI2.nsh"
!include "LogicLib.nsh"

; ---- MUI 外观 ----
!define MUI_ABORTWARNING
!define MUI_ICON "usf.ico"               ; 缺图标时会警告但不阻断, 可注释
!define MUI_FINISHPAGE_RUN "$INSTDIR\UniversalSceneForge.exe"
!define MUI_FINISHPAGE_RUN_TEXT "立即启动 ${APPNAME}"

; ---- 安装页面 ----
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"   ; 开源合规: 向导展示 MIT 许可证
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; ---- 版本信息 ----
VIProductVersion "0.9.5.0"
VIAddVersionKey /LANG=2052 "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=2052 "FileDescription" "3DGS 场景重建与 DCC 自动化导出工具"
VIAddVersionKey /LANG=2052 "LegalCopyright" "${COMPANY}"
VIAddVersionKey /LANG=2052 "FileVersion" "0.9.5-preview"

Section "主程序" SecMain
    SectionIn RO
    ; 快捷方式装到「所有用户」(C-07): 提权安装时当前用户上下文可能指向
    ; 管理员账户, 开始菜单入口因此「丢失」; all 视图对所有账户可见。
    SetShellVarContext all
    ; 清理旧版遗留在 32 位视图(WOW6432Node)的键, 统一迁移到 64 位视图
    SetRegView 32
    DeleteRegKey HKLM "Software\${APPNAME}"
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}"
    SetRegView 64

    SetOutPath "$INSTDIR"
    ; 用户数据保护(BUG-001/002): 不覆盖安装目录内已有的 external (可能含
    ; 用户自建训练 venv) 与遗留 settings.json (旧版用户配置)。
    File /r /x external /x settings.json "${DISTDIR}\*.*"
    ${IfNot} ${FileExists} "$INSTDIR\external\gaussian-splatting\train.py"
        File /r "${DISTDIR}\external"      ; 全新安装才同步训练仓库源码
    ${EndIf}

    ; 开源合规: 许可证与第三方清单随包落盘（LGPL/GPL 组件声明义务, 见
    ; THIRD_PARTY_LICENSES.md §4）
    File "..\LICENSE"
    File "..\THIRD_PARTY_LICENSES.md"
    File "..\NOTICE"
    File "..\CHANGELOG.md"

    ; 开始菜单 + 桌面快捷方式
    CreateDirectory "$SMPROGRAMS\${APPNAME}"
    CreateShortcut  "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" \
                    "$INSTDIR\UniversalSceneForge.exe"
    CreateShortcut  "$SMPROGRAMS\${APPNAME}\卸载 ${APPNAME}.lnk" \
                    "$INSTDIR\Uninstall.exe"
    CreateShortcut  "$DESKTOP\${APPNAME}.lnk" \
                    "$INSTDIR\UniversalSceneForge.exe"

    ; 注册表: 卸载信息 + 安装路径（供探测/升级用）
    WriteRegStr HKLM "Software\${APPNAME}" "InstallDir" "$INSTDIR"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
                     "DisplayName" "${APPNAME}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
                     "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
                     "InstallLocation" "$INSTDIR"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
                     "DisplayVersion" "${APPVERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
                     "Publisher" "${COMPANY}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
                     "DisplayIcon" "$INSTDIR\UniversalSceneForge.exe"
    WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

Section "VC++ 运行库检测 (缺失时提示)" SecVC
    ; PyInstaller 产物依赖 VC Redist; 不强制捆绑, 缺失时给出引导
    ClearErrors
    GetDLLVersion "$SYSDIR\vcruntime140.dll" $R0 $R1
    IfErrors 0 +3
        MessageBox MB_ICONINFORMATION|MB_OK "未检测到 VC++ 2015-2022 运行库。$\r$\n\
            首次启动失败时请安装:$\r$\n\
            https://aka.ms/vs/17/release/vc_redist.x64.exe"
SectionEnd

Section "Firewall 例外（UE5 本地预览, 可选）" SecFirewall
    ; Live Link 本地回环预览时 Windows 可能弹防火墙; 预登记规则
    ExecWait 'netsh advfirewall firewall add rule name="${APPNAME} Preview" dir=out action=allow program="$INSTDIR\UniversalSceneForge.exe" enable=yes' $0
SectionEnd

Function .onInit
    SetRegView 64   ; 本程序为 64 位, 注册表读写统一走 64 位视图(而非 WOW6432Node)

    ; 预先结束可能占用安装目标文件的进程(BUG-006 缓解): 否则提权后弹出的
    ; "无法打开要写入的文件" 对话框无法被自动化/普通权限操作关闭。
    nsExec::Exec 'taskkill /F /IM UniversalSceneForge.exe'
    Pop $0

    ; 旧安装路径检测(BUG-003): 依次读 64 位主键 → 32 位遗留主键 → 卸载表
    ; InstallLocation, 命中且目录有效时把目录页默认值切到旧路径, 实现"原地
    ; 覆盖升级", 避免 C:/D: 两处安装并存、旧目录被遗弃成孤儿。
    ReadRegStr $R0 HKLM "Software\${APPNAME}" "InstallDir"
    ${If} $R0 == ""
        SetRegView 32
        ReadRegStr $R0 HKLM "Software\${APPNAME}" "InstallDir"
        SetRegView 64
    ${EndIf}
    ${If} $R0 == ""
        ReadRegStr $R0 HKLM \
            "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}" \
            "InstallLocation"
    ${EndIf}
    ${If} $R0 != ""
    ${AndIf} ${FileExists} "$R0\UniversalSceneForge.exe"
        StrCpy $INSTDIR "$R0"
        MessageBox MB_OKCANCEL|MB_ICONQUESTION \
            "检测到已安装 ${APPNAME}:$\r$\n$R0$\r$\n$\r$\n\
            将原地覆盖升级。用户训练环境 (external/.venv) 与配置将被保留。" IDOK +2
        Abort
    ${EndIf}
FunctionEnd

Section "Uninstall"
    SetShellVarContext all          ; 与安装侧一致, 清理所有用户的快捷方式
    Delete "$SMPROGRAMS\${APPNAME}\*.*"
    RMDir  "$SMPROGRAMS\${APPNAME}"
    Delete "$DESKTOP\${APPNAME}.lnk"
    RMDir /r "$INSTDIR"
    SetRegView 64
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}"
    DeleteRegKey HKLM "Software\${APPNAME}"
    SetRegView 32
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPID}"
    DeleteRegKey HKLM "Software\${APPNAME}"
    SetRegView 64
SectionEnd
