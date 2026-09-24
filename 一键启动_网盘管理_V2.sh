#!/bin/sh
# =============================================================================
#  网盘管理 一键启动（Linux；双击也能跑）
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
    zenity --info --title='网盘管理' --width=420 \
      --text="${1:-启动失败。可以在终端里跑：一键启动_网盘管理_V2.sh --check 看自检结果}" \
      >/dev/null 2>&1 || true
  elif command -v kdialog >/dev/null 2>&1; then
    kdialog --title '网盘管理' --msgbox \
      "${1:-启动失败。可以跑 一键启动_网盘管理_V2.sh --check 看自检结果}" \
      >/dev/null 2>&1 || true
  else
    sleep 3
  fi
}

HERE=$(cd -- "$(dirname -- "$0")" && pwd)
PROJ=${V2_HOME:-"$HERE"}

# 认项目本体：要有 启动.py + wangpan/（自研内核）+ v8_3/（界面层），避免指错目录
if [ ! -f "$PROJ/启动.py" ] || [ ! -d "$PROJ/wangpan" ] || [ ! -d "$PROJ/v8_3" ]; then
  echo "[X] 这里不像是 网盘管理 的项目目录：$PROJ" >&2
  echo "    应该有 启动.py、wangpan/、v8_3/；也可以用 V2_HOME=/路径 $0 指定。" >&2
  _pause "找不到 网盘管理 的项目目录：$PROJ"
  exit 1
fi
PROJ=$(cd -- "$PROJ" && pwd)
cd -- "$PROJ" || exit 1

# ---- 便携自愈：把被解压工具丢掉的符号链接与 pyvenv.cfg 补回来 ----
# 发布包里的 venv 建在**包内自带的独立 CPython** 上，解释器是符号链接
# （运行环境/venv/bin/python3.14 -> ../../python/bin/python3.14）。
# `7z x` 出于安全会**拒绝**创建"指向目录之外"的链接（实测丢了 302 个），
# 解压出来的包就会报 `No module named 'encodings'`；图形解压器/unzip 行为也各不相同。
# 这里在启动 python 之前按实际位置补一次（幂等、几毫秒），用户用什么解压都能用。
# 关掉：V2_SKIP_ENV_FIX=1
BASE_PY="$PROJ/运行环境/python/bin/python3.14"
fix_one_venv() {
  _v="$PROJ/$1"
  [ -d "$_v" ] || return 0
  [ -e "$BASE_PY" ] || return 0
  _l="$_v/bin/python3.14"
  # 只修坏掉的链接（详见 启动.sh 里的说明）：7z 拒绝创建时会留个 0 字节普通文件
  if [ ! -L "$_l" ] || [ ! -e "$_l" ]; then
    ln -sfn "$2" "$_l" 2>/dev/null || true
    [ -e "$_v/bin/python" ] || ln -sfn python3.14 "$_v/bin/python" 2>/dev/null || true
    [ -e "$_v/bin/python3" ] || ln -sfn python3.14 "$_v/bin/python3" 2>/dev/null || true
  fi
  _cfg="$_v/pyvenv.cfg"
  [ -f "$_cfg" ] || return 0
  _cur=$(sed -n 's/^home = //p' "$_cfg" 2>/dev/null | head -1)
  if [ -n "$_cur" ] && [ ! -x "$_cur/python3.14" ] && [ ! -x "$_cur/python3" ] \
     && [ ! -x "$_cur/python" ]; then
    sed -i "s|^home = .*|home = $PROJ/运行环境/python/bin|" "$_cfg" 2>/dev/null || true
  elif [ -z "$_cur" ]; then
    printf 'home = %s\n' "$PROJ/运行环境/python/bin" >> "$_cfg" 2>/dev/null || true
  fi
}

if [ "${V2_SKIP_ENV_FIX:-}" != "1" ]; then
  fix_one_venv "运行环境/venv" "../../python/bin/python3.14"
  fix_one_venv "运行环境/语音识别/venv" "../../../python/bin/python3.14"
fi

# 项目自带的解释器（本项目有自己的 运行环境/venv，不依赖系统 python）
PY="$PROJ/运行环境/venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "[!] 找不到项目自带解释器：$PY" >&2
  if [ -e "$BASE_PY" ]; then
    echo "    包内有自带 CPython，但 venv 的链接没建起来 —— 试试：" >&2
    echo "      ln -sfn ../../python/bin/python3.14 运行环境/venv/bin/python3.14" >&2
  else
    echo "    这里是源码树（不是发布包）：先按 README 建好自带运行环境。" >&2
    echo "      python3 -m venv 运行环境/venv" >&2
    echo "      运行环境/venv/bin/pip install -r requirements.txt" >&2
  fi
  _pause "缺少项目自带的 Python 环境（运行环境/venv）。"
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

# ---- V1 界面/网盘/AI 那一层要用的第三方（缺失时对应功能降级，不是致命）----
缺 = []
for 名, 说明 in (("httpx", "网盘适配器与 AI 请求"),
              ("ahocorasick", "敏感词匹配（缺失会退化成纯 Python）"),
              ("pypinyin", "安全词生成（缺失会退化成 Unicode 码点）"),
              ("qrcode", "登录二维码（缺失只给链接）")):
    try:
        __import__(名)
    except Exception:                          # noqa: BLE001
       缺.append(f"{名}（{说明}）")
print("界面层  ：", "依赖齐全" if not 缺 else "缺 " + "、".join(缺))

# ---- 自研内核 ----
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

# ---- 界面层与适配器 ----
try:
    import v8_3.配置 as _配置
    print("界面层  ： v8_3 可导入（项目根", _配置.项目根, "）")
except Exception as exc:                       # noqa: BLE001
    print("界面层  ： v8_3 导入失败（", exc, "）")
    raise SystemExit(1)
try:
    实例们 = _配置.网盘实例列表(_配置.加载配置())
    启用 = [x for x in 实例们 if x["启用"]]
    print("网盘    ：", f"{len(启用)} 个启用 / {len(实例们)} 个已配置")
    for 项 in 启用:
        规格 = _配置.适配器规格表(_配置.加载配置()).get(项["标识"])
        if 规格 is None:
            continue
        好, 说明 = type(规格).校验目录(规格.路径)
        print(f"  - {项['标识']}：{'目录 OK' if 好 else 说明}")
except Exception as exc:                       # noqa: BLE001
    print("网盘    ： 检查失败（", exc, "）")
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
  echo "    这是桌面程序，请在图形界面里运行。" >&2
  _pause "没有图形会话：这是桌面程序，请在图形界面里运行。"
  exit 2
fi

echo "▶ 启动 网盘管理（$PROJ）"
"$PY" "$PROJ/启动.py" "$@"
RC=$?
if [ "$RC" -ne 0 ]; then
  echo "[X] 启动失败（退出码 $RC）。可以跑自检：$0 --check" >&2
  _pause "启动失败（退出码 $RC）。跑一下 一键启动_网盘管理_V2.sh --check 看自检。"
fi
exit "$RC"
