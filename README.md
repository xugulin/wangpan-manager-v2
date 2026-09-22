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
| M6 网盘功能 | 适配器契约 + 本地/HTTP 适配器 + 多任务传输引擎 + **多段并发下载** + 网盘页 | ✅（真实网盘适配器待接） |

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
