# 网盘管理 V2（自研播放内核）

**和 V1（`../网盘管理`）完全独立**：不 import V1 的任何模块、不读 V1 的配置与数据、
不调用 V1 的适配器/桥，也不依赖 V1 的 Python 环境（V2 有自己的 `运行环境/venv`）。

V2 的核心主张是用户定的路线：**自己用 libav\* 写播放器**，而不是把窗口交给现成播放器。

## 里程碑现状（M1–M6）

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 自研内核 | 解封装/解码/缩放/重采样 + 音频主时钟 + 自绘控件 + 播放/暂停/跳转/音量/截图/统计 | ✅ |
| M2 硬解 | VAAPI / D3D11VA + 按显示尺寸缩放；失败自动回退软解 | ✅ |
| M3 网络播放 | HTTP(S) Range、自定义头（UA/Referer/Cookie）、重连、打开失败降级、中断回调 | ✅ |
| M4 字幕 | SRT/ASS 解析 + 自绘叠加（黑描边/位置/对齐/颜色）+ 自动找同名字幕 + 轨道轮换 | ✅ |
| M5 体验 | 倍速、逐帧、播放记录与续播、播放列表、**章节跳转**、**进度缩略图预览**、键盘快捷键 | ✅ |
| M6 网盘功能 | 适配器契约 + 本地/HTTP 适配器 + 多任务传输引擎 + **多段并发下载** + **上传续传** + 网盘页 + **光鸭云盘真实适配器（真机验证过直链）** | ✅ |

### 现在能跑什么

* **自研解封装 / 解码 / 缩放 / 重采样**：ctypes 直连 `libavformat` / `libavcodec` /
  `libavutil` / `libswscale` / `libswresample`，**不调 ffmpeg 命令行、不用第三方 Python 绑定**；
* **音画同步**：音频写设备（PulseAudio）当时钟，视频按帧时间戳等待/丢帧；
* **自己的渲染**：帧 → `QImage` → 控件自绘（保持比例、居中、黑边），
  所以"视频跑到别的窗口里"这种事**结构上不可能发生**；
* 播放 / 暂停 / 停止 / 跳转 / 音量 / 截图 / 键盘（空格、←→、双击全屏、Esc）；
* 统计：已解码帧、丢帧、已播音频秒、码率、轨道摘要（状态栏实时显示）。

真机实测（作者的 KDE/COSMIC + XWayland + Intel iHD，本机 FFmpeg 63/61/10/7）：

```
网盘管理 V2 2.0.0｜FFmpeg：avformat 63.1.101、avcodec 63.1.101、
                          avutil 61.1.101、swscale 10.1.101、swresample 7.1.101
[播放] 已打开：视频#0 h264 640x360 25fps、音频#1 aac 44100Hz 1声道｜时长 4.0s
播放 2 秒：播放中｜1.9/4.0s｜25fps｜丢帧 0/50｜PulseAudio（libpulse-simple）
跳转 3.2s → 暂停（0 新帧）→ 继续 → 播完（结束=True）→ 截图 PNG ✓
```

## 怎么跑

```bash
./启动.sh                       # 空窗口，点「📂 打开文件」
运行环境/venv/bin/python 启动.py 某个视频.mp4
QT_QPA_PLATFORM=offscreen 运行环境/venv/bin/python -m unittest discover -s tests -t .
```

依赖：**Python 3.11+、PySide6（V2 自己的 venv 里）、系统 libav\***（Linux 装 `ffmpeg` 包即可；
Windows 把 `avformat-*.dll` 等放进 `运行环境/libav/`）。测试造素材用系统 `ffmpeg` 命令，
**播放本身不需要它**。

### 主要验证数据（本机真机，4K60 HEVC）

| 场景 | 结果 |
|---|---|
| 4K60 硬解播放（按显示尺寸输出 1280x544） | 300 帧 / 0 丢帧 / CPU ≈0.95 核（软解约 4~5 核） |
| 4K60 走本地 HTTP 播放（带 Referer/Cookie） | 300 帧 / 0 丢帧 / CPU ≈0.5 核 / Range 请求 3 次 / 停止 0.19s |
| 字幕 | 真机截图确认：`\an8` 顶部、`\c&H0000FF&` 显示为红色 |
| 章节 + 缩略图预览 | 真机截图确认：章节下拉列出三章、进度条上方出现解码缩略图 |


## 🆕 弹幕引擎 + TMDB 刮削 + 海报墙（三块都从零实现）

> 只参考公开协议与算法思想，**没有使用任何其它项目的代码/文件**。

| 模块 | 内容 | 验证 |
|---|---|---|
| **弹幕引擎** `wangpan/danmaku/` | 数据模型（不可变弹幕/按时间二分的弹幕池）；轨道分配（**位置=帧时间纯函数**、"间隙单调 ⇒ 两端满足即不追尾"的 O(1) 判据、长弹幕按 `(宽/基准)^0.28` 提速+确定性抖动）；渲染（**每条预渲染成带描边位图**再逐帧贴图、LRU 双上限、帧时间 PLL 平滑）；源（弹弹play 官方签名/16MB MD5/`p` 串解析、本地 B站 XML 与我们自己的 JSON）；匹配（相似度打分 + 自动采纳阈值 + 人工确认）；过滤（词/正则/发送者/模式/彩色/时间窗/长度/去重 + 命中原因） | 单测 **137 条**（弹幕引擎 25 + 源/过滤/匹配 112）；**真机截图**：视频上真的飘着弹幕（绘制 838 条、位图命中 27335） |
| **TMDB 刮削** `wangpan/scrape/` | 命名解析（官方规范全覆盖 + 研究里 5 个坑的回归）；目录扫描（多版本/多段/番外归并、系统目录跳过）；TMDB 客户端（Bearer/api_key、语言回退、`include_image_language=zh,null`、429 退避、**缓存 180 天硬过期**符合官方条款、aggregate_credits、分级 CN→HK→TW→US 回退）；匹配打分；**SQLite 11 张表**；图片分级尺寸缓存（LRU+原子写）；NFO 读写；刮削流水线（本地优先、分批、可取消、失败重试） | 单测 **77 条**（TMDB/命名/打分 72 + 流水线 5 端到端）；**真机截图**：海报墙 + 详情页 |
| **海报墙** `wangpan/ui/海报墙页.py`、`详情页.py` | QListView + 自定义 Model/Delegate 虚拟滚动、分页、缩略图工作线程懒加载（去重排队 + LRU）、继续观看、搜索/筛选/排序、右键菜单、空状态；详情页背景+海报叠层、评分/影评人评分分开、分级徽章、标签、演职员（异步头像）、季集列表可点播 | 单测 14 条 + **真机截图** |

### 需要你自己提供凭据的两处（代码已就绪，只差 key）

* **弹幕**：弹弹play 现在**连 `/api/v2/match` 都要求 AppId 签名**（实测 HTTP 403
  `Missing Authentication Headers`）。到 <https://dev.dandanplay.com> 申请后写进
  `数据/弹幕源.json`（`{"dandanplay": {"appId": "…", "appSecret": "…"}}`）或设环境变量
  `V2_DANDANPLAY_APP_ID` / `V2_DANDANPLAY_APP_SECRET`。
  **不需要凭据也能用**：本地同名弹幕文件（B站 XML / `.danmaku.json`）已完整可用。
* **刮削**：TMDB 的免费 Key，到 <https://www.themoviedb.org/settings/api> 申请后写进
  `数据/刮削.json`（`{"tmdb": {"token": "…"}}`）或设 `V2_TMDB_TOKEN`。
  **没配置也能用**：扫描 + 本地 NFO/图片照样入库（验证过）。

### 数据与缓存位置

```
数据/资料库.db           SQLite（11 张表：媒体/文件/季/集/人物/媒体人物/图片/分级/标签/播放进度/刮削任务）
数据/缓存/图片/          海报等图片（分级尺寸 + LRU，默认上限 2GB，遵守 TMDB 不得缓存超 6 个月）
数据/缓存/tmdb/          TMDB 接口响应缓存（默认 TTL 7 天 + 180 天硬过期）
数据/弹幕源.json         弹弹play 凭据（可选）
数据/刮削.json           TMDB 凭据与语言（可选）
```

## 真机验证（两台机器上是两套证据）

**Linux（开发机，FFmpeg 63 / VAAPI / PulseAudio）**

```
4K60 HEVC 硬解播放：300 帧 / 0 丢帧 / CPU ≈0.95 核（软解约 4~5 核）
4K60 走本地 HTTP（带 Referer/Cookie）：300 帧 / 0 丢帧 / CPU ≈0.5 核 / 停止 0.19s
字幕：\an8 顶部、\c&H0000FF& 红色（截图确认）
章节 + 缩略图预览：章节下拉三章、进度条上方出解码缩略图（截图确认）
光鸭云盘：列出真实根目录 34 项 → 取到真实签名直链 → Range 取 64KB 得 HTTP 206
          + Content-Range: bytes 0-65535/25580674693 + 前 8 字节 52617221（"Rar!"）
```

**Windows（GitHub Actions 真机 runner，FFmpeg 63，无 GPU）** ——
仓库：<https://github.com/xugulin/wangpan-manager-v2>，工作流 `测试（Windows 真机）`

```
偏移表主版本：63｜运行时 avcodec 主版本：63       ← 大版本一致性自检
单元测试：Ran 120 tests … OK (skipped=3)
真机验收（工具/windows真机验收.py）：结论 通过
  【本地文件】帧数 20、有帧 True、尺寸 (320,180)、截图 True、暂停有效 True、跳转生效
  【网络】    帧数 20、有帧 True、尺寸 (320,180)、截图 True、暂停有效 True、跳转生效
播放诊断（工具/诊断播放.py）：结论 出画面正常
硬解：D3D11VA 在无 GPU 的 runner 上建不起来 → **自动回退软解并如实记录**
      （这正是 M2 要求的"失败回退软解"，回退路径已在真 Windows 上验证；
        真正用上 D3D11VA 需要有 GPU 的 Windows 机器）
```

## 目录

```
启动.py / 启动.sh          入口（自举到自带 venv）
wangpan/ffmpeg/            绑定：加载.py（找库）、绑定.py（原型 + 偏移读写）、偏移.py（自动生成）
wangpan/player/            播放内核：解封装.py、解码.py、音频输出.py、引擎.py
wangpan/subtitle/          字幕：模型.py、解析.py（SRT/ASS）、绘制.py
wangpan/pan/               网盘适配器：接口.py（契约）、本地.py、http.py、工厂.py
wangpan/transfer/          传输引擎：任务.py、引擎.py（并发/暂停/取消/续传）
wangpan/ui/                界面：视频控件.py（自绘）、主窗口.py、网盘页.py
tools/生成偏移.py          用 C 编译器的 offsetof 生成字段偏移（FFmpeg 换版本只需重跑它）
tools/生成素材.py          造测试素材
tests/                     绑定/解码/硬解/引擎/网络/字幕/体验/网盘传输/独立性（103 条）
docs/架构与路线图.md        设计说明与后续里程碑
```

## 为什么不用现成播放器（V1 的教训）

V1 把窗口句柄交给 libvlc 让它画，于是踩了三类平台坑：`set_xwindow` vs `set_hwnd`、
输出模块（`xcb_x11` vs `direct3d11`）钉错平台、窗口"就绪"判定在 Windows 上失效，
还有"它自己开一个顶层窗口放画面"这种结构性问题。
V2 把画面数据握在自己手里，**这一类问题从设计上消失**。

## 许可

* 本项目代码：与 V1 相同的口径（源码公开、免费使用、自带免责声明）。
* FFmpeg：**LGPLv2.1+**（动态链接、不启用 GPL 组件）；随包分发时按 FFmpeg 官方清单
  提供来源与说明（<https://ffmpeg.org/legal.html>）。
* H.264/HEVC 专利提示：与 V1 用 VLC 时同样存在（FFmpeg 官方 legal 页有说明）。
