#!/bin/sh
# =============================================================================
#  网盘管理 V2 一键启动（Linux；双击也能跑）
# =============================================================================
#  为什么是 POSIX sh 而不是 bash：
#  某些文件管理器 / 包装器会用别的 shell 解析这个脚本，中文变量名会被当成命令执行
#  （V1 上踩过："候选: 不是有效的标识符"）。所以这里**只用 ASCII 变量名**，
#  语法也只用 `$(…)`、`[ ]`、`case` 这些 POSIX 的东西。
#
#  用法：
#    ./一键启动_网盘管理_V2.sh                  # 正常启动
#    ./一键启动_网盘管理_V2.sh 某个视频.mp4      # 参数透传给 启动.py（直接开播）
#    ./一键启动_网盘管理_V2.sh --check          # 只自检（解释器 / PySide6 / libav*），不启动
#    ./一键启动_网盘管理_V2.sh --安装快捷方式    # 装桌面图标 + 菜单项
#    V2_HOME=/别的/路径 ./一键启动_网盘管理_V2.sh
#
#  出错时：在终端里就等回车；双击运行（没有终端）就弹对话框，免得一闪而过。
# =============================================================================
set -u

# ---- 出错别让窗口一闪而过 ----
_pause() {
  # $1 = 给对话框看的一句话（可选）
  if [ -t 0 ] || [ -t 2 ]; then
    printf '按回车关闭…'
    read -r _ || true
    return 0
  fi
  if command -v zenity >/dev/null 2>&1; then
    zenity --info --title='网盘管理 V2' --width=420 \
      --text="${1:-启动失败。可以在终端里跑：一键启动_网盘管理_V2.sh --check 看自检结果}" \
      >/dev/null 2>&1 || true
  elif command -v kdialog >/dev/null 2>&1; then
    kdialog --title '网盘管理 V2' --msgbox \
      "${1:-启动失败。可以跑 一键启动_网盘管理_V2.sh --check 看自检结果}" \
      >/dev/null 2>&1 || true
  else
    sleep 3
  fi
}

HERE=$(cd -- "$(dirname -- "$0")" && pwd)
PROJ=${V2_HOME:-"$HERE"}

# 认项目本体：必须有 启动.py 与 wangpan/（V2 的特征），避免指错目录
if [ ! -f "$PROJ/启动.py" ] || [ ! -d "$PROJ/wangpan" ]; then
  echo "[X] 这里不像是 网盘管理 V2 的项目目录：$PROJ" >&2
  echo "    应该有 启动.py 与 wangpan/；也可以用 V2_HOME=/路径 $0 指定。" >&2
  _pause "找不到 网盘管理 V2 的项目目录：$PROJ"
  exit 1
fi
PROJ=$(cd -- "$PROJ" && pwd)
cd -- "$PROJ" || exit 1

# 项目自带的解释器（V2 有自己的 运行环境/venv，不依赖系统 python）
PY="$PROJ/运行环境/venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "[!] 找不到项目自带解释器：$PY" >&2
  echo "    V2 自带运行环境（与 V1、系统 python 都不共享）。先建环境：" >&2
  echo "      python3 -m venv 运行环境/venv" >&2
  echo "      运行环境/venv/bin/pip install PySide6" >&2
  _pause "缺少项目自带的 Python 环境（运行环境/venv）。先按 README 建好环境。"
  exit 1
fi

case "${1:-}" in
  # ---- 只自检，不起界面（排查"双击没反应"最有用）----
  --check*)
    echo "项目目录：$PROJ"
    echo "解释器  ：$PY"
    "$PY" - <<'PYCODE'
import sys
print("Python  ：", sys.version.split()[0])
try:
    import PySide6
    print("PySide6 ：", PySide6.__version__)
except Exception as exc:                       # noqa: BLE001
    print("PySide6 ： 缺失（", exc, "）")
    raise SystemExit(1)
sys.path.insert(0, ".")
try:
    from wangpan.ffmpeg import 加载
    可用 = 加载.可用()
    print("libav*  ：", "可用" if 可用 else ("不可用：" + str(加载.不可用原因())))
    if 可用:
        print("版本    ：", {k: 加载.版本文本(v) for k, v in 加载.库版本().items()})
    else:
        raise SystemExit(1)
except SystemExit:
    raise
except Exception as exc:                       # noqa: BLE001
    print("libav*  ： 检查失败（", exc, "）")
    raise SystemExit(1)
PYCODE
    RC=$?
    if [ "$RC" -eq 0 ]; then
      echo "自检结果：通过"
    else
      echo "自检结果：有问题（见上面几行）"
    fi
    _pause "自检完成，退出码 $RC（详见终端输出）"
    exit "$RC"
    ;;
  # ---- 装快捷方式：交给专门的脚本（桌面图标 + 菜单项 + 图标主题）----
  --安装快捷方式|--install-shortcut)
    shift || true
    exec "$PROJ/工具/安装快捷方式.sh" "$@"
    ;;
esac

# ---- 正常启动 ----
if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
  echo "[X] 没有图形会话（DISPLAY / WAYLAND_DISPLAY 都是空的）。" >&2
  echo "    V2 是桌面程序，请在图形界面里运行。" >&2
  _pause "没有图形会话：V2 是桌面程序，请在图形界面里运行。"
  exit 2
fi

echo "▶ 启动 网盘管理 V2（$PROJ）"
"$PY" "$PROJ/启动.py" "$@"
RC=$?
if [ "$RC" -ne 0 ]; then
  echo "[X] 启动失败（退出码 $RC）。可以跑自检：$0 --check" >&2
  _pause "启动失败（退出码 $RC）。跑一下 一键启动_网盘管理_V2.sh --check 看自检。"
fi
exit "$RC"
