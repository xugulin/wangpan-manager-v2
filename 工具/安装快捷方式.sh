#!/bin/sh
# =============================================================================
#  安装 / 卸载「网盘管理 V2」快捷方式（Linux 桌面）
# =============================================================================
#  ⚠️ 本文件的**变量名与函数名一律用 ASCII**：POSIX shell（dash 等）不认中文标识符，
#     写 `要桌面=1` 会被当成命令执行（真踩过：dash 报 "要桌面=1: 未找到命令"，
#     接着 `[ "$要桌面" -eq 1 ]` 报 "需要整数"，最后桌面图标根本没装）。
#     提示文字用中文没问题，只有标识符必须是 ASCII。
#
#  做三件事（都可重复执行，重复跑就是覆盖）：
#   1. 桌面图标：$XDG_DESKTOP_DIR（没配就找 ~/Desktop、~/桌面，最后退回 ~）
#      放一个 .desktop，双击即启动；
#   2. 应用菜单：~/.local/share/applications/网盘管理_V2.desktop；
#   3. 图标主题：把 资源/图标/hicolor/* 装进 ~/.local/share/icons/hicolor/，
#      这样菜单/任务栏显示我们自己的图标（不是通用 Python 图标）。
#  最后刷新 desktop 数据库与图标缓存（有对应工具才刷，没有就跳过）。
#
#  用法：
#    工具/安装快捷方式.sh                 # 安装（桌面图标 + 菜单项）
#    工具/安装快捷方式.sh --不桌面         # 只装菜单项
#    工具/安装快捷方式.sh --目录 ~/桌面     # 指定桌面目录
#    工具/安装快捷方式.sh --卸载           # 卸载（只删自己装的这几个文件）
#    工具/安装快捷方式.sh --查看           # 打印现在装了什么、指向哪里
# =============================================================================
set -u

HERE=$(cd -- "$(dirname -- "$0")" && pwd)
PROJ=$(cd -- "$HERE/.." && pwd)

APP_ID="网盘管理_V2"
APP_NAME="网盘管理 V2"
LAUNCHER="$PROJ/一键启动_网盘管理_V2.sh"
ICON_PNG="$PROJ/网盘管理_V2_图标.png"
ICON_ROOT="$PROJ/资源/图标"
WM_CLASS="网盘管理V2"                 # 与 启动.py 的 setApplicationName 一致（任务栏认人靠它）

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS_DIR="$DATA_HOME/applications"
ICONS_DIR="$DATA_HOME/icons/hicolor"
DESKTOP_FILE="$APPS_DIR/$APP_ID.desktop"

WANT_DESKTOP=1
DO_UNINSTALL=0
DO_SHOW=0
DESKTOP_ARG=""

while [ $# -gt 0 ]; do
  case "$1" in
    --不桌面|--no-desktop) WANT_DESKTOP=0 ;;
    --桌面|--desktop)      WANT_DESKTOP=1 ;;
    --卸载|--uninstall)    DO_UNINSTALL=1 ;;
    --查看|--show)         DO_SHOW=1 ;;
    --目录|--dir)          shift || true; DESKTOP_ARG=${1:-} ;;
    -h|--help)             sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "[!] 不认识的参数：$1（用 --help 看用法）" >&2; exit 2 ;;
  esac
  shift || true
done

# ---------------- 桌面目录 ----------------
find_desktop_dir() {
  if [ -n "$DESKTOP_ARG" ]; then printf '%s\n' "$DESKTOP_ARG"; return; fi
  # XDG 配置优先（各桌面环境都可能改过它）
  if [ -f "$HOME/.config/user-dirs.dirs" ]; then
    line=$(grep -m1 '^XDG_DESKTOP_DIR=' "$HOME/.config/user-dirs.dirs" 2>/dev/null || true)
    if [ -n "$line" ]; then
      dir=$(printf '%s' "$line" | sed 's/^XDG_DESKTOP_DIR=//; s/^"//; s/"$//')
      dir=$(printf '%s' "$dir" | sed "s#\$HOME#$HOME#g")
      # 有的环境把 Desktop 设成 $HOME 本身：那不算"桌面目录"，否则图标会撒在家目录里
      if [ -n "$dir" ] && [ "$dir" != "$HOME/" ] && [ "$dir" != "$HOME" ] && [ -d "$dir" ]; then
        printf '%s\n' "$dir"; return
      fi
    fi
  fi
  for dir in "$HOME/Desktop" "$HOME/桌面"; do
    if [ -d "$dir" ]; then printf '%s\n' "$dir"; return; fi
  done
  printf '%s\n' "$HOME"        # 真找不到就放家目录（KDE 把 Desktop 设成 $HOME 时就是这样）
}

DESKTOP_DIR=$(find_desktop_dir)
DESKTOP_LINK="$DESKTOP_DIR/一键启动_$APP_ID.desktop"

# ---------------- 查看 ----------------
if [ "$DO_SHOW" -eq 1 ]; then
  echo "项目目录   ：$PROJ"
  if [ -x "$LAUNCHER" ]; then
    echo "启动脚本   ：$LAUNCHER（可执行）"
  else
    echo "启动脚本   ：$LAUNCHER（❌ 不可执行，先 chmod +x）"
  fi
  if [ -f "$ICON_PNG" ]; then
    echo "图标文件   ：$ICON_PNG（在）"
  else
    echo "图标文件   ：$ICON_PNG（❌ 缺失，跑 工具/生成图标.py）"
  fi
  echo "桌面目录   ：$DESKTOP_DIR"
  for pair in "菜单项:$DESKTOP_FILE" "桌面图标:$DESKTOP_LINK" \
              "图标主题:$ICONS_DIR/256x256/apps/$APP_ID.png"; do
    label=${pair%%:*}
    file=${pair#*:}
    if [ -f "$file" ]; then
      echo "$label     ：$file（已装）"
    else
      echo "$label     ：$file（未装）"
    fi
  done
  exit 0
fi

# ---------------- 卸载 ----------------
if [ "$DO_UNINSTALL" -eq 1 ]; then
  n=0
  for f in "$DESKTOP_FILE" "$DESKTOP_LINK" "$DESKTOP_DIR/$APP_ID.desktop"; do
    if [ -f "$f" ]; then rm -f -- "$f" && { echo "✓ 删除 $f"; n=$((n + 1)); }; fi
  done
  for size in 16 32 48 64 128 256; do
    f="$ICONS_DIR/${size}x${size}/apps/$APP_ID.png"
    if [ -f "$f" ]; then rm -f -- "$f" && n=$((n + 1)); fi
  done
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" 2>/dev/null || true
  command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f "$ICONS_DIR" 2>/dev/null || true
  echo "共删除 $n 个文件。"
  exit 0
fi

# ---------------- 前置检查 ----------------
fail=0
if [ ! -x "$LAUNCHER" ]; then
  echo "[!] 启动脚本不可执行：$LAUNCHER" >&2
  echo "    先修一下：chmod +x '$LAUNCHER'" >&2
  fail=1
fi
if [ ! -f "$ICON_PNG" ]; then
  echo "[i] 还没生成图标，先跑一下：运行环境/venv/bin/python 工具/生成图标.py" >&2
  if [ -x "$PROJ/运行环境/venv/bin/python" ]; then
    ( cd "$PROJ" && QT_QPA_PLATFORM=offscreen 运行环境/venv/bin/python 工具/生成图标.py ) \
      >/dev/null 2>&1 || true
  fi
  if [ ! -f "$ICON_PNG" ]; then echo "[!] 图标还是没生成出来" >&2; fail=1; fi
fi
if [ "$fail" -ne 0 ]; then exit 1; fi

mkdir -p -- "$APPS_DIR" || exit 1

# ---------------- 写 .desktop ----------------
write_entry() {
  target=$1
  {
    echo '[Desktop Entry]'
    echo 'Type=Application'
    echo 'Version=1.0'
    echo "Name=$APP_NAME"
    echo 'Name[en]=Wangpan Manager V2'
    echo 'Comment=自研播放内核 + 弹幕引擎 + TMDB 刮削 + 海报墙（双击启动）'
    echo "Exec=\"$LAUNCHER\" %F"
    echo "Path=$PROJ"
    echo "Icon=$ICON_PNG"
    echo 'Terminal=false'
    echo 'StartupNotify=true'
    echo "StartupWMClass=$WM_CLASS"
    # 只留**一个主类**：AudioVideo（影音）+ 子类 Player。写两个主类（比如再加 Utility）
    # 会被 desktop-file-validate 提示 "might appear more than once in the application menu"。
    echo 'Categories=AudioVideo;Player;'
    echo 'MimeType=video/mp4;video/x-matroska;video/webm;video/quicktime;'
    echo 'Keywords=网盘;播放器;弹幕;海报墙;刮削;wangpan;video;danmaku;'
  } > "$target" || return 1
  chmod 755 -- "$target" 2>/dev/null || true
}

write_entry "$DESKTOP_FILE" || { echo "[X] 写不了 $DESKTOP_FILE" >&2; exit 1; }
echo "✓ 菜单项：$DESKTOP_FILE"

if [ "$WANT_DESKTOP" -eq 1 ]; then
  if [ -d "$DESKTOP_DIR" ] && write_entry "$DESKTOP_LINK"; then
    # 桌面（COSMIC/GNOME）把 .desktop 当"可信启动器"需要可执行位，write_entry 里已 chmod
    echo "✓ 桌面图标：$DESKTOP_LINK"
  else
    echo "[!] 桌面目录写不进去（跳过）：$DESKTOP_DIR" >&2
  fi
fi

# ---------------- 图标主题 ----------------
icon_count=0
if [ -d "$ICON_ROOT/hicolor" ]; then
  for size in 16 32 48 64 128 256; do
    src="$ICON_ROOT/hicolor/${size}x${size}/apps/$APP_ID.png"
    [ -f "$src" ] || continue
    dst_dir="$ICONS_DIR/${size}x${size}/apps"
    mkdir -p -- "$dst_dir" || continue
    if cp -f -- "$src" "$dst_dir/$APP_ID.png"; then icon_count=$((icon_count + 1)); fi
  done
  echo "✓ 图标主题：装了 $icon_count 个尺寸 → $ICONS_DIR"
else
  echo "[i] 没有 $ICON_ROOT/hicolor（图标主题跳过；快捷方式仍会用 $ICON_PNG）"
fi

# ---------------- 刷新缓存 ----------------
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f "$ICONS_DIR" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1 || true
command -v kbuildsycoca5 >/dev/null 2>&1 && kbuildsycoca5 >/dev/null 2>&1 || true

# ---------------- 校验 ----------------
if command -v desktop-file-validate >/dev/null 2>&1; then
  if desktop-file-validate "$DESKTOP_FILE"; then
    echo "✓ desktop-file-validate 通过"
  else
    echo "[!] desktop-file-validate 报了问题（见上），但文件已就位" >&2
  fi
fi

echo
echo "装好了：在「应用程序」里搜 “网盘管理” 就能看到；桌面上的图标双击即启动。"
echo "想卸掉：$0 --卸载    想看看现状：$0 --查看"
