#!/bin/bash
# 网盘管理 启动脚本（Linux）—— 用项目自带的解释器
#
# 为什么这里有一堆"自愈"代码
# ==========================
# 发布包里的 venv 是用**包内自带的独立 CPython** 建的，它的解释器是**符号链接**
# （`运行环境/venv/bin/python3.14 -> ../../python/bin/python3.14`）。
# 而解压工具对"指向目录之外的符号链接"态度不一样：
#
#   * `tar`：原样还原；
#   * `7z x`：**直接拒绝**（报 "Dangerous link path was ignored"）——
#     实测 302 个链接被丢掉，其中就包括这个解释器链接，解压出来的包直接不能用
#     （`ModuleNotFoundError: No module named 'encodings'`）；
#   * 图形解压器 / `unzip`：有的还原、有的丢。
#
# 所以启动脚本在**启动 python 之前**先按实际位置把这几样补回来（幂等，几毫秒）：
#   1. 解释器链接（丢了就用 ln 重建）；
#   2. `pyvenv.cfg` 里的 `home =`（打包时写成了占位符，要指向包内自带 python）。
# 这样不管用户用什么工具解压、把目录搬到哪儿，双击就能跑。
#
# 关闭自愈：`V2_SKIP_ENV_FIX=1 ./启动.sh`（排查问题时用）。

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || exit 1

#: 包内自带独立 CPython 的位置（venv 的基座）
BASE_PY="$HERE/运行环境/python/bin/python3.14"

#: 修一个 venv：$1 = venv 目录（相对项目根），$2 = 解释器链接应指向的相对路径
fix_one_venv() {
  _venv="$HERE/$1"
  [ -d "$_venv" ] || return 0
  [ -e "$BASE_PY" ] || return 0
  _link="$_venv/bin/python3.14"
  _want="$2"
  # 1) 解释器链接：**只修坏掉的**。
  #    - 解压工具拒绝创建时，7z 会留一个 0 字节的普通文件（不是链接）；
  #    - 有的工具会漏掉这个文件。
  #    这两种情况才重建；开发树里本来就是好的链接（-L 且能解析）绝不动它 ——
  #    否则会把开发环境从系统 python 改成包内 python，属于"修坏了"。
  if [ ! -L "$_link" ] || [ ! -e "$_link" ]; then
    ln -sfn "$_want" "$_link" 2>/dev/null || true
    [ -e "$_venv/bin/python" ] || ln -sfn python3.14 "$_venv/bin/python" 2>/dev/null || true
    [ -e "$_venv/bin/python3" ] || ln -sfn python3.14 "$_venv/bin/python3" 2>/dev/null || true
  fi
  # 2) pyvenv.cfg 的 home：**也只修指向了"不存在的地方"的**（打包时写的占位符）。
  _cfg="$_venv/pyvenv.cfg"
  [ -f "$_cfg" ] || return 0
  _cur=$(sed -n 's/^home = //p' "$_cfg" 2>/dev/null | head -1)
  if [ -n "$_cur" ] && [ ! -x "$_cur/python3.14" ] && [ ! -x "$_cur/python3" ] \
     && [ ! -x "$_cur/python" ]; then
    _home="$HERE/运行环境/python/bin"
    sed -i "s|^home = .*|home = $_home|" "$_cfg" 2>/dev/null || true
  elif [ -z "$_cur" ]; then
    printf 'home = %s\n' "$HERE/运行环境/python/bin" >> "$_cfg" 2>/dev/null || true
  fi
}

if [ "${V2_SKIP_ENV_FIX:-}" != "1" ]; then
  fix_one_venv "运行环境/venv" "../../python/bin/python3.14"
  fix_one_venv "运行环境/语音识别/venv" "../../../python/bin/python3.14"
fi

for PY in "运行环境/venv/bin/python" "运行环境/venv/bin/python3" python3; do
  if [ -x "$PY" ] || command -v "$PY" >/dev/null 2>&1; then
    exec "$PY" 启动.py "$@"
  fi
done
echo "找不到可用的 Python 解释器（试试 $HERE/一键启动_网盘管理_V2.sh --check）" >&2
exit 1
