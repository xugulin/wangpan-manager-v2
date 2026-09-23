# =============================================================================
#  安装 / 卸载「网盘管理 V2」快捷方式（Windows）
# =============================================================================
#  用法（在本目录下开 PowerShell）：
#      powershell -ExecutionPolicy Bypass -File 工具\安装快捷方式.ps1
#      powershell -ExecutionPolicy Bypass -File 工具\安装快捷方式.ps1 -卸载
#      powershell -ExecutionPolicy Bypass -File 工具\安装快捷方式.ps1 -只开始菜单
#
#  做两件事：桌面 + 开始菜单 各放一个 .lnk；目标用 pythonw.exe（**不弹黑窗口**），
#  参数是 启动.py，工作目录是项目根，图标用 资源\图标\网盘管理_V2.ico。
#  本文件是 **UTF-8 with BOM**：Windows PowerShell 5.1 没有 BOM 会把中文读成乱码。
# =============================================================================
param(
  [switch]$卸载,
  [switch]$只开始菜单,
  [switch]$查看
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$项目根 = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$应用名 = '网盘管理 V2'
$脚本   = Join-Path $项目根 '启动.py'
$批处理 = Join-Path $项目根 '启动.bat'
$图标   = Join-Path $项目根 '资源\图标\网盘管理_V2.ico'
$pythonw = Join-Path $项目根 '运行环境\venv\Scripts\pythonw.exe'
$python  = Join-Path $项目根 '运行环境\venv\Scripts\python.exe'

$桌面 = [Environment]::GetFolderPath('Desktop')
$开始菜单 = Join-Path ([Environment]::GetFolderPath('Programs')) '网盘管理 V2.lnk'
$桌面快捷 = Join-Path $桌面 '网盘管理 V2.lnk'

function 目标与参数 {
  if (Test-Path $pythonw) { return @{ 目标 = $pythonw; 参数 = '"' + $脚本 + '"' } }
  if (Test-Path $python)  { return @{ 目标 = $python;  参数 = '"' + $脚本 + '"' } }
  if (Test-Path $批处理)  { return @{ 目标 = $批处理;  参数 = '' } }
  throw "找不到可用的目标：$pythonw / $python / $批处理 都不存在"
}

if ($查看) {
  Write-Host "项目根    ：$项目根"
  Write-Host "桌面快捷  ：$桌面快捷  $([bool](Test-Path $桌面快捷))"
  Write-Host "开始菜单  ：$开始菜单  $([bool](Test-Path $开始菜单))"
  Write-Host "图标      ：$图标  $([bool](Test-Path $图标))"
  return
}

if ($卸载) {
  foreach ($f in @($桌面快捷, $开始菜单)) {
    if (Test-Path $f) { Remove-Item -LiteralPath $f -Force; Write-Host "✓ 已删除 $f" }
  }
  return
}

if (-not (Test-Path $脚本)) { throw "项目根看起来不对（没有 启动.py）：$项目根" }
if (-not (Test-Path $图标)) {
  Write-Host "（还没有图标，先生成一下）"
  $py = if (Test-Path $python) { $python } else { 'python' }
  & $py (Join-Path $项目根 '工具\生成图标.py') | Out-Null
}

$壳 = New-Object -ComObject WScript.Shell
$信息 = 目标与参数
$放哪 = @()
if (-not $只开始菜单) { $放哪 += $桌面快捷 }
$放哪 += $开始菜单
foreach ($路径 in $放哪) {
  $目录 = Split-Path -Parent $路径
  if (-not (Test-Path $目录)) { New-Item -ItemType Directory -Path $目录 -Force | Out-Null }
  $lnk = $壳.CreateShortcut($路径)
  $lnk.TargetPath = $信息.目标
  if ($信息.参数) { $lnk.Arguments = $信息.参数 }
  $lnk.WorkingDirectory = $项目根
  $lnk.Description = '网盘管理 V2：自研播放内核 + 弹幕引擎 + TMDB 刮削 + 海报墙'
  if (Test-Path $图标) { $lnk.IconLocation = "$图标,0" }
  $lnk.Save()
  Write-Host "✓ 已创建 $路径"
}
Write-Host ''
Write-Host '装好了：桌面上双击「网盘管理 V2」即可；开始菜单里也能搜到。'
Write-Host "想卸掉：powershell -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -卸载"
