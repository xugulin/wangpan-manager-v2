# Jellyfin 播放与自动刮削 深度技术研究报告

> 目标：把 Jellyfin（`jellyfin/jellyfin`，C#，57.4k★）的**播放（Playback）**与**自动刮削（Metadata Scraping）**两大块拆到"可直接照着实现"的粒度，并为"自己写刮削 + 播放的 Python 桌面应用"给出落地蓝图。

---

## 0. 取数与引用约定

| 项 | 值 |
|---|---|
| 仓库 | `jellyfin/jellyfin`（Server Backend & API，**不含** Web 前端；前端在 `jellyfin/jellyfin-web`） |
| 默认分支 | `master` |
| 本报告核对的 tree SHA | `fd75964da853765d63101b4889f23a4161e2758b` |
| 星数 | 57400（核对时） |
| 源码获取方式 | `curl -sL -o jf.tar.gz "https://codeload.github.com/jellyfin/jellyfin/tar.gz/refs/heads/master"` 后解包（**22 MB / 2214 个 `.cs`**）。注意 `raw.githubusercontent.com` 在本次环境不可达，但 `codeload.github.com` 可达，因此**无需** GitHub contents API、**不受** 60 次/小时匿名限额影响。 |

**引用格式**

- 源码：`https://github.com/jellyfin/jellyfin/blob/master/<路径>#L<行号>`，行号对应上述 tree SHA。
- 官方文档：直接给 `https://jellyfin.org/docs/...` 可点击链接。
- 凡标 **【未找到】**/**【不确定】** 的，是本报告**没有**在源码或官方文档中核到的内容，不做推测。

**一个重要的认知前提（后文反复用到）**

Jellyfin 服务端**不做播放决策的"最终裁决"**。它做的是：

1. **客户端上报能力**（`DeviceProfile`，随 `POST /Items/{itemId}/PlaybackInfo` 提交）；
2. 服务端用 `StreamBuilder` 把"这个文件"与"这个客户端能力"做**匹配打分**，得出 `PlayMethod`（DirectPlay / DirectStream / Transcode）与一组 `TranscodeReason`；
3. 需要转码时，服务端**按客户端给的码率/分辨率上限**拼 FFmpeg 命令行。

也就是说：**服务端没有码率阶梯表**，阶梯由客户端决定（详见 §1.3.8）。这一点是自研客户端/服务端时最容易搞反的地方。

---

## 一、播放（Playback）

### 1.1 能力协商：整个播放链路的起点

#### 1.1.1 握手机制

客户端播放一个条目时，第一步是向服务端要"播放信息"：

```http
POST /Items/{itemId}/PlaybackInfo
Content-Type: application/json

{
  "userId": "...",
  "maxStreamingBitrate": 8000000,
  "startTimeTicks": 0,
  "audioStreamIndex": 1,
  "subtitleStreamIndex": 2,
  "maxAudioChannels": 2,
  "mediaSourceId": "...",
  "deviceProfile": { ...客户端能力声明... },
  "enableDirectPlay": true,
  "enableDirectStream": true,
  "enableTranscoding": true,
  "allowVideoStreamCopy": true,
  "allowAudioStreamCopy": true,
  "alwaysBurnInSubtitleWhenTranscoding": false
}
```

请求体 DTO 定义在 `Jellyfin.Api/Models/MediaInfoDtos/PlaybackInfoDto.cs`（逐字段）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `UserId` | `Guid?` | 目标用户 |
| `MaxStreamingBitrate` | `int?` | **客户端愿意接收的最高码率**（bps）——码率"阶梯"的实际来源 |
| `StartTimeTicks` | `long?` | 起播位置（.NET ticks，1 tick = 100 ns） |
| `AudioStreamIndex` | `int?` | 指定音轨 |
| `SubtitleStreamIndex` | `int?` | 指定字幕轨 |
| `MaxAudioChannels` | `int?` | 最大声道数（如 2 = 强制立体声） |
| `MediaSourceId` | `string?` | 指定 media source（多版本时用） |
| `LiveStreamId` | `string?` | 直播流 |
| `DeviceProfile` | `DeviceProfile?` | **客户端能力声明**（核心） |
| `EnableDirectPlay` | `bool?` | 允许直连 |
| `EnableDirectStream` | `bool?` | 允许直连封装（remux） |
| `EnableTranscoding` | `bool?` | 允许转码 |
| `AllowVideoStreamCopy` | `bool?` | 视频轨可 copy |
| `AllowAudioStreamCopy` | `bool?` | 音频轨可 copy |
| `AutoOpenLiveStream` | `bool?` | 自动打开直播 |
| `AlwaysBurnInSubtitleWhenTranscoding` | `bool?` | 转码时字幕一律烧录 |

引用：<https://github.com/jellyfin/jellyfin/blob/master/Jellyfin.Api/Models/MediaInfoDtos/PlaybackInfoDto.cs>

对应的 GET 形式（无 body，纯查询串）在同一控制器的 `[HttpGet("Items/{itemId}/PlaybackInfo")]`：
<https://github.com/jellyfin/jellyfin/blob/master/Jellyfin.Api/Controllers/MediaInfoController.cs#L72>

服务端还有一个 `GET /Playback/BitrateTest`（码率测速用）：
<https://github.com/jellyfin/jellyfin/blob/master/Jellyfin.Api/Controllers/MediaInfoController.cs#L329>

#### 1.1.2 `DeviceProfile`：客户端能力声明模型

`MediaBrowser.Model/Dlna/DeviceProfile.cs` —— 这是**整个判定逻辑的输入契约**：

```csharp
public class DeviceProfile
{
    public string? Name { get; set; }
    public Guid? Id { get; set; }
    public int? MaxStreamingBitrate { get; set; } = 8000000;      // 8 Mbps
    public int? MaxStaticBitrate { get; set; } = 8000000;
    public int? MusicStreamingTranscodingBitrate { get; set; } = 128000;
    public int? MaxStaticMusicBitrate { get; set; } = 8000000;
    public DirectPlayProfile[]   DirectPlayProfiles  { get; set; } = [];  // 能直接播什么
    public TranscodingProfile[]  TranscodingProfiles { get; set; } = [];  // 转码输出成什么
    public ContainerProfile[]    ContainerProfiles   { get; set; } = [];  // 容器级约束
    public CodecProfile[]        CodecProfiles       { get; set; } = [];  // 编解码级约束
    public SubtitleProfile[]     SubtitleProfiles    { get; set; } = [];  // 字幕交付方式
}
```

引用：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/Dlna/DeviceProfile.cs>

**五个 Profile 子模型的字段与语义**

`DirectPlayProfile`（"我能直接播什么容器+编码"）：

```csharp
[XmlAttribute("container")]  public string Container   { get; set; } = string.Empty; // 逗号分隔列表，如 "mkv,mp4"
[XmlAttribute("audioCodec")] public string? AudioCodec { get; set; }                  // 逗号分隔，如 "aac,mp3,ac3"
[XmlAttribute("videoCodec")] public string? VideoCodec { get; set; }                  // 逗号分隔，如 "h264,hevc"
[XmlAttribute("type")]       public DlnaProfileType Type { get; set; }                // Video / Audio / Photo
```

三个判定函数就是**大小写不敏感的逗号列表包含**：
```csharp
SupportsContainer(container) => ContainerHelper.ContainsContainer(Container, container);
SupportsVideoCodec(codec)    => Type == DlnaProfileType.Video && ContainerHelper.ContainsContainer(VideoCodec, codec);
SupportsAudioCodec(codec)    => (Type == Audio || Type == Video) && ContainerHelper.ContainsContainer(AudioCodec, codec);
```
引用：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/Dlna/DirectPlayProfile.cs>

`TranscodingProfile`（"转码请输出成这样"）：`Container`、`Type`、`VideoCodec`、`AudioCodec`、`Protocol`（默认 `http`）、`EstimateContentLength`、`EnableMpegtsM2TsMode`、`TranscodeSeekInfo`、`CopyTimestamps`、`Context`、`EnableSubtitlesInManifest`、`MaxAudioChannels`、`MinSegments`、`SegmentLength`、`Conditions`、`EnableAudioVbrEncoding`。
引用：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/Dlna/TranscodingProfile.cs>

`CodecProfile`（"在这种容器下，这个编码还要求满足这些条件"）：`Type`（`CodecType`）、`Codec`、`Container`、`SubContainer`、`Conditions[]`、`ApplyConditions[]`。注意它的 `ContainsAnyCodec` 有个 HLS 特例：当 `Container == "hls"` 且 `useSubContainer == true` 时改用 `SubContainer` 比对。
引用：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/Dlna/CodecProfile.cs>

`ContainerProfile`：`Type`、`Conditions[]`、`Container`、`SubContainer`，同样是 `hls`→`SubContainer` 的特例。
引用：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/Dlna/ContainerProfile.cs>

`SubtitleProfile`：`Format`、`Method`（`SubtitleDeliveryMethod`）、`DidlMode`、`Language`、`Container`，以及 `SupportsLanguage(subLanguage)`（`Language` 为空视为全部支持；空语言按 `"und"` 处理）。
引用：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/Dlna/SubtitleProfile.cs>

`ProfileCondition`：`Condition`（`ProfileConditionType`）、`Property`（`ProfileConditionValue`）、`Value`（字符串）、`IsRequired`（**默认 true**）。
引用：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/Dlna/ProfileCondition.cs>

#### 1.1.3 关键枚举

`PlayMethod`（注意数值顺序，序列化时用得到）：

```csharp
public enum PlayMethod { Transcode = 0, DirectStream = 1, DirectPlay = 2 }
```
引用：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/Session/PlayMethod.cs>

`TranscodeReason`（`[Flags]`，**位掩码**，是排查"为什么转码了"的钥匙）：

| 值 | 位 | 含义 |
|---|---|---|
| `ContainerNotSupported` | 1<<0 | 容器不支持 |
| `VideoCodecNotSupported` | 1<<1 | 视频编码不支持 |
| `AudioCodecNotSupported` | 1<<2 | 音频编码不支持 |
| `SubtitleCodecNotSupported` | 1<<3 | 字幕编码不支持 |
| `AudioIsExternal` | 1<<4 | 音频是外挂的 |
| `SecondaryAudioNotSupported` | 1<<5 | 次音轨不支持 |
| `VideoProfileNotSupported` | 1<<6 | H.264/HEVC profile 不支持 |
| `VideoLevelNotSupported` | 1<<7 | level 不支持 |
| `VideoResolutionNotSupported` | 1<<8 | 分辨率（宽/高）超限 |
| `VideoBitDepthNotSupported` | 1<<9 | 位深不支持 |
| `VideoFramerateNotSupported` | 1<<10 | 帧率超限 |
| `RefFramesNotSupported` | 1<<11 | 参考帧数超限 |
| `AnamorphicVideoNotSupported` | 1<<12 | 变形宽银幕 |
| `InterlacedVideoNotSupported` | 1<<13 | 隔行 |
| `AudioChannelsNotSupported` | 1<<14 | 声道数 |
| `AudioProfileNotSupported` | 1<<15 | 音频 profile |
| `AudioSampleRateNotSupported` | 1<<16 | 采样率 |
| `AudioBitDepthNotSupported` | 1<<17 | 音频位深 |
| `ContainerBitrateExceedsLimit` | 1<<18 | **总码率超限** |
| `VideoBitrateNotSupported` | 1<<19 | 视频码率 |
| `AudioBitrateNotSupported` | 1<<20 | 音频码率 |
| `UnknownVideoStreamInfo` | 1<<21 | 视频流信息未知 |
| `UnknownAudioStreamInfo` | 1<<22 | 音频流信息未知 |
| `DirectPlayError` | 1<<23 | 直连失败兜底 |
| `VideoRangeTypeNotSupported` | 1<<24 | HDR/DV 范围类型不支持 |
| `VideoCodecTagNotSupported` | 1<<25 | codec tag（如 hvc1/hev1） |
| `StreamCountExceedsLimit` | 1<<26 | 流数量超限 |
| `VideoRotationNotSupported` | 1<<27 | 画面旋转 |

引用：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/Session/TranscodeReason.cs>

`MediaOptions`（服务端内部把请求+DProfile 揉成的判定输入）：

```csharp
Context = EncodingContext.Streaming;  EnableDirectPlay = true;  EnableDirectStream = true;   // 构造默认
bool EnableDirectPlay / EnableDirectStream / ForceDirectPlay / ForceDirectStream;
bool AllowAudioStreamCopy / AllowVideoStreamCopy / AlwaysBurnInSubtitleWhenTranscoding;
Guid ItemId;  MediaSourceInfo[] MediaSources;  DeviceProfile Profile;  // Profile 是 required
string? MediaSourceId / DeviceId;  int? MaxAudioChannels / MaxBitrate;
EncodingContext Context;  int? AudioTranscodingBitrate / AudioStreamIndex / SubtitleStreamIndex;
int? GetMaxBitrate(bool isAudio);  // 见 §1.3.8
```
引用：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/Dlna/MediaOptions.cs>

---

### 1.2 DirectPlay / DirectStream / Transcode 判定逻辑

全部逻辑在 **`MediaBrowser.Model/Dlna/StreamBuilder.cs`（2493 行）**。
文件：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/Dlna/StreamBuilder.cs>

#### 1.2.1 入口与多层排序

```csharp
public StreamInfo? GetOptimalVideoStream(MediaOptions options)   // :232
{
    // 1) 若指定 MediaSourceId 则只用它，否则遍历所有 media source（多版本）
    // 2) 对每个 media source 调 BuildVideoItem -> StreamInfo
    // 3) GetOptimalStream(streams, options.GetMaxBitrate(false) ?? 0)
    //    -> SortMediaSources(...).FirstOrDefault()
}
```

`SortMediaSources`（`:262-305`）的排序键**依次**为：

1. **`PlayMethod == DirectPlay` 且 `MediaSource.Protocol == File` → 排 0**（源码注释：`Nothing beats direct playing a file`）；否则 1。
2. `PlayMethod` 为 `DirectStream` 或 `DirectPlay` → 0，其余（Transcode）→ 1。注释解释了为什么 DirectStream 与 DirectPlay 同级：`Let's assume direct streaming a file is just as desirable as direct playing a remote url`。
3. `MediaSource.Protocol == File` → 0，否则 1。
4. 若 `maxBitrate > 0` 且已知源码率 → 按 `|源码率 - maxBitrate|` **升序**（**挑最接近客户端上限的那个版本**）。
5. `streams.IndexOf`（稳定排序，保持原始顺序）。

> **对自研实现的启示**：这个"5 级排序"非常值得照搬——它天然实现了"本地文件直连 > 远程直连 > remux > 转码；同等条件下挑最贴合带宽的版本"。

#### 1.2.2 音频路径

`GetAudioDirectPlayProfile`（`:437-504`）：

1. 在 `DirectPlayProfiles` 里找第一个 `Type == Audio` 且 `IsAudioDirectPlaySupported(...)` 的 profile。
2. 找不到 → 退而找 `IsAudioDirectStreamSupported(...)`；找到则**记 `transcodeReasons |= ContainerNotSupported`**（即"编码 OK 但容器要换"）。再找不到 → 返回 `GetTranscodeReasonsFromDirectPlayProfile(...)` 给出的原因集合。
3. `if (item.SupportsDirectPlay && transcodeReasons == 0)`：再看码率是否超限
   - 未超限且 `options.EnableDirectPlay` → **`(profile, PlayMethod.DirectPlay, 0)`**
   - 超限 → `transcodeReasons |= ContainerBitrateExceedsLimit`
4. `if (item.SupportsDirectStream)`：码率未超限且 `transcodeReasons == ContainerNotSupported` → **`PlayMethod.DirectStream`**。

源码里两条**重要注释**（说明当前版本的实际行为，别被枚举名误导）：

```csharp
// Note: As of 10.10 codebase, SupportsDirectPlay is always true because the MediaSourceInfo initializes this key as true
// Need to check additionally for current transcode reasons
...
// Note: as of 10.10 codebase, the options.EnableDirectStream is always false due to
// "direct-stream http streaming is currently broken"
// Don't check that option for audio as we always assume that is supported
```
（`:467-468`、`:489-491`）

`GetTranscodeReasonsFromDirectPlayProfile`（`:506-549`）：遍历 profile，只要**存在某个** profile 同时支持容器且（无视频流或视频编码支持）且（无音频流或音频编码支持）就算通过；否则按缺失项累积 `ContainerNotSupported` / `VideoCodecNotSupported` / `AudioCodecNotSupported`。

#### 1.2.3 视频路径（核心）

`GetVideoDirectPlayProfile`（`:1288-1429`）。判定顺序：

**Step 0 — 强制开关**
```csharp
if (options.ForceDirectPlay)  return (null, PlayMethod.DirectPlay,  audioStream?.Index, 0);   // :1298
if (options.ForceDirectStream) return (null, PlayMethod.DirectStream, audioStream?.Index, 0); // :1303
```

**Step 1 — 先算三组"兼容性原因"**
```csharp
var containerProfileReasons = GetCompatibilityContainer(options, mediaSource, container, videoStream);          // :1312
var videoCodecProfileReasons = videoStream is null ? default
    : GetCompatibilityVideoCodec(options, mediaSource, container, videoStream);                                  // :1315
// 对每个候选音轨各算一次
var audioStreamMatches = candidateAudioStreams.ToDictionary(
    s => s, audioStream => GetCompatibilityAudioCodecDirect(options, mediaSource, container, audioStream, true,
                                                             mediaSource.IsSecondaryAudio(audioStream) ?? false)); // :1318
```

**Step 2 — 字幕是否阻碍直连**（`:1320-1332`）
```csharp
if (subtitleStream is not null) {
    var subtitleProfile = GetSubtitleProfile(mediaSource, subtitleStream, options.Profile.SubtitleProfiles,
                                             PlayMethod.DirectPlay, _transcoderSupport, container, null);
    if (subtitleProfile.Method != Drop && != External && != Embed) {
        _logger.LogDebug("Not eligible for {0} due to unsupported subtitles", PlayMethod.DirectPlay);
        subtitleProfileReasons |= TranscodeReason.SubtitleCodecNotSupported;
    }
}
```
> 即：**字幕交付方式为 Drop / External / Embed 时不影响直连；只有必须"Encode（烧录）"时才拉低到转码。**

**Step 3 — 遍历 `DirectPlayProfiles` 打分**（`:1338-1409`）

对每个 `Type == Video` 的 profile：

```csharp
TranscodeReason directPlayProfileReasons = 0;
TranscodeReason audioCodecProfileReasons = 0;

// (a) 容器
if (!directPlayProfile.SupportsContainer(container)) directPlayProfileReasons |= ContainerNotSupported;
else                                                containerSupported = true;

// (b) 视频编码
if (!directPlayProfile.SupportsVideoCodec(videoStream?.Codec)) directPlayProfileReasons |= VideoCodecNotSupported;

// (c) 音频编码：在候选音轨里挑第一个该 profile 支持的
if (candidateAudioStreams.Count != 0) {
    selectedAudioStream = candidateAudioStreams.FirstOrDefault(a => directPlayProfile.SupportsAudioCodec(a.Codec));
    if (selectedAudioStream is null) directPlayProfileReasons |= AudioCodecNotSupported;
    else                            audioCodecProfileReasons = audioStreamMatches.GetValueOrDefault(selectedAudioStream);
}

var failureReasons = directPlayProfileReasons | containerProfileReasons | subtitleProfileReasons;

// (d) 只有视频编码本身没被判死，才叠加更细的 codec profile 原因
if ((failureReasons & VideoCodecNotSupported) == 0) failureReasons |= videoCodecProfileReasons;
if ((failureReasons & AudioCodecNotSupported) == 0) failureReasons |= audioCodecProfileReasons;

// (e) 关键：DirectStream 允许"容器不对"，所以把容器类原因从 directStream 判定里剔掉
var directStreamFailureReasons = failureReasons & (~DirectStreamReasons);

PlayMethod? playMethod = null;
if (failureReasons == 0 && isEligibleForDirectPlay && mediaSource.SupportsDirectPlay)
    playMethod = PlayMethod.DirectPlay;
else if (directStreamFailureReasons == 0 && isEligibleForDirectStream && mediaSource.SupportsDirectStream)
    playMethod = PlayMethod.DirectStream;

var ranked = GetRank(ref failureReasons, rankings);
return (Result: (directPlayProfile, playMethod, selectedAudioStream?.Index, failureReasons), Order: order, Rank: ranked);
```

**Step 4 — 挑选结果**（`:1401-1428`）

```csharp
TranscodeReason[] rankings = [ TranscodeReason.VideoCodecNotSupported, VideoCodecReasons,
                               TranscodeReason.AudioCodecNotSupported, AudioCodecReasons, ContainerReasons ];  // :1335

var analyzedProfiles = ... .OrderByDescending(a => a.Result.PlayMethod)   // DirectPlay(2) > DirectStream(1) > null(0)
                            .ThenByDescending(a => a.Rank)
                            .ThenBy(a => a.Order)
                            .ToArray().ToLookup(a => a.Result.PlayMethod is not null);

var profileMatch = analyzedProfiles[true].Select(a => a.Result).FirstOrDefault();
if (profileMatch.Profile is not null) return profileMatch;   // 成功：拿到 PlayMethod

// 全部失败：挑一个"最有代表性"的失败原因回给客户端
var failureReasons = analyzedProfiles[false].Select(a => a.Result)
    .Where(r => !containerSupported || !r.TranscodeReason.HasFlag(ContainerNotSupported))
    .FirstOrDefault().TranscodeReason;
if (failureReasons == 0) failureReasons = TranscodeReason.DirectPlayError;
return (Profile: null, PlayMethod: null, AudioStreamIndex: null, TranscodeReasons: failureReasons);
```

**判定逻辑速记**（可直接写成伪代码）：

```
对每个 media source：
  对每个 video DirectPlayProfile：
    reasons = 容器不符? ContainerNotSupported : 0
            | 视频编码不符? VideoCodecNotSupported : 0
            | 无可用音轨? AudioCodecNotSupported : 0
            | 细粒度 container/condition/profile 不符
            | 字幕必须烧录? SubtitleCodecNotSupported : 0
    if reasons == 0:                                  -> DirectPlay
    elif (reasons 去掉容器类原因) == 0:                 -> DirectStream
    else:                                              -> 该 profile 失败
  取 PlayMethod 值最大、其次失败原因最"轻微"的结果
若全部失败 -> PlayMethod = null（走转码），并把 failureReasons 原样返回
```

**`DirectStreamReasons`** 这个掩码（决定"哪些原因可以用 remux 抹平"）的定义位置不在 `StreamBuilder.cs` 里（见 §1.2.5 的说明）。

#### 1.2.4 失败原因 → `TranscodeReason` 的映射表

`GetTranscodeReasonForFailedCondition`（`:307-398`）把 `ProfileConditionValue` 映射为 `TranscodeReason`。**带 `// TODO` 的项表示目前返回 0（即不产生原因，静默通过）**：

| `ProfileConditionValue` | 映射结果 |
|---|---|
| `AudioBitrate` | `AudioBitrateNotSupported` |
| `AudioChannels` | `AudioChannelsNotSupported` |
| `AudioProfile` | `AudioProfileNotSupported` |
| `AudioSampleRate` | `AudioSampleRateNotSupported` |
| `Height` | `VideoResolutionNotSupported` |
| `IsAnamorphic` | `AnamorphicVideoNotSupported` |
| `IsInterlaced` | `InterlacedVideoNotSupported` |
| `IsSecondaryAudio` | `SecondaryAudioNotSupported` |
| `NumStreams` | `StreamCountExceedsLimit` |
| `RefFrames` | `RefFramesNotSupported` |
| `VideoBitDepth` | `VideoBitDepthNotSupported` |
| `AudioBitDepth` | `AudioBitDepthNotSupported` |
| `VideoBitrate` | `VideoBitrateNotSupported` |
| `VideoCodecTag` | `VideoCodecTagNotSupported` |
| `VideoFramerate` | `VideoFramerateNotSupported` |
| `VideoLevel` | `VideoLevelNotSupported` |
| `VideoProfile` | `VideoProfileNotSupported` |
| `VideoRangeType` | `VideoRangeTypeNotSupported` |
| `VideoRotation` | `VideoRotationNotSupported` |
| `Width` | `VideoResolutionNotSupported` |
| `Has64BitOffsets` / `IsAvc` / `NumAudioStreams` / `NumVideoStreams` / `PacketLength` / `VideoTimestamp` | **`0`（TODO，不产生原因）** |
| 其它 | `0` |

条件失败时会打这样一条 Debug 日志（**排查转码原因的入口**，`:1441-1452`）：
```
Profile: {Name}, DirectPlay=false. Reason={type}.{Property} Condition: {Condition}. ConditionValue: {Value}. IsRequired: {IsRequired}. Path: {Path}
```

#### 1.2.5 判定用的位掩码常量（**必须照抄这组定义**）

`StreamBuilder.cs:22-27` 定义了所有"原因分组"，是理解 DirectStream 与 DirectPlay 分界的关键：

```csharp
internal const TranscodeReason ContainerReasons = TranscodeReason.ContainerNotSupported
                                                | TranscodeReason.ContainerBitrateExceedsLimit;

internal const TranscodeReason AudioCodecReasons = TranscodeReason.AudioBitrateNotSupported
                                                 | TranscodeReason.AudioChannelsNotSupported
                                                 | TranscodeReason.AudioProfileNotSupported
                                                 | TranscodeReason.AudioSampleRateNotSupported
                                                 | TranscodeReason.SecondaryAudioNotSupported
                                                 | TranscodeReason.AudioBitDepthNotSupported
                                                 | TranscodeReason.AudioIsExternal;

internal const TranscodeReason AudioReasons = TranscodeReason.AudioCodecNotSupported | AudioCodecReasons;

internal const TranscodeReason VideoCodecReasons = TranscodeReason.VideoResolutionNotSupported
                                                 | TranscodeReason.AnamorphicVideoNotSupported
                                                 | TranscodeReason.InterlacedVideoNotSupported
                                                 | TranscodeReason.VideoBitDepthNotSupported
                                                 | TranscodeReason.VideoBitrateNotSupported
                                                 | TranscodeReason.VideoFramerateNotSupported
                                                 | TranscodeReason.VideoLevelNotSupported
                                                 | TranscodeReason.RefFramesNotSupported
                                                 | TranscodeReason.VideoRangeTypeNotSupported
                                                 | TranscodeReason.VideoProfileNotSupported
                                                 | TranscodeReason.VideoRotationNotSupported;

internal const TranscodeReason VideoReasons = TranscodeReason.VideoCodecNotSupported | VideoCodecReasons;

// ★ 这行是 DirectStream 的定义：凡是"能靠重新封装抹平"的原因，都允许降级为 DirectStream 而不是转码
internal const TranscodeReason DirectStreamReasons = AudioReasons
                                                   | TranscodeReason.ContainerNotSupported
                                                   | TranscodeReason.VideoCodecTagNotSupported;

private const string ManifestContainers = "hls,applehttp,dash";

// HLS 输出时允许哪些编码
private static readonly string[] _supportedHlsVideoCodecs  = ["h264", "hevc", "vp9", "av1"];
private static readonly string[] _supportedHlsAudioCodecsTs  = ["aac", "ac3", "eac3", "mp3"];
private static readonly string[] _supportedHlsAudioCodecsMp4 = ["aac", "ac3", "eac3", "mp3", "alac", "flac", "opus", "dts", "truehd"];
```

> **一句话总结 DirectStream 的语义**：`DirectStreamReasons` 里的位 = "改封装就能解决"。容器不对、音频编码不对、codec tag 不对 → **remux 即可**；而视频编码不对、分辨率/位深/HDR 不对 → **必须真转码**。

`GetRank`（`:2339-2355`）：按 `rankings` 数组顺序，返回第一个命中的分组序号（从 1 开始）；都没命中返回 `rankings.Length + 1`。用于在同为 `PlayMethod` 的候选里挑"失败得最轻"的那个 profile。

#### 1.2.5b 前置门槛：`isEligibleForDirectPlay` / `isEligibleForDirectStream`

在进入 profile 遍历**之前**，`BuildVideoItem`（`:710-742`）先算两把闸门：

```csharp
var bitrateLimitExceeded = IsBitrateLimitExceeded(item, options.GetMaxBitrate(false) ?? 0);
var isEligibleForDirectPlay   = options.EnableDirectPlay   && (options.ForceDirectPlay   || !bitrateLimitExceeded);
var isEligibleForDirectStream = options.EnableDirectStream && (options.ForceDirectStream || !bitrateLimitExceeded);
TranscodeReason transcodeReasons = 0;

// 强制对 BD/DVD 文件夹 remux 或转码（不能直接播 VIDEO_TS 目录）
if (item.VideoType == VideoType.Dvd || item.VideoType == VideoType.BluRay)
    isEligibleForDirectPlay = false;

// 清单文件（m3u8/mpd）不是字节流，不能当文件直接交给客户端：
// 变体与分片 URI 是相对 origin 的，无法相对 Jellyfin 的 URL 解析
if (ContainerHelper.ContainsContainer(ManifestContainers, item.Container))
    isEligibleForDirectPlay = false;

if (bitrateLimitExceeded) transcodeReasons = TranscodeReason.ContainerBitrateExceedsLimit;
```

两条"隐藏规则"值得注意：

1. **`VIDEO_TS` / `BDMV` 目录永远不能 DirectPlay**（源码注释：`Force transcode or remux for BD/DVD folders`）。
2. **`hls` / `applehttp` / `dash` 容器永远不能 DirectPlay**（因为它是清单不是字节流）。

#### 1.2.6 多版本（MediaSource）归一化

`NormalizeMediaSourceFormatIntoSingleContainer`（`:408-435`）：当 `Container` 形如 `"mkv,webm"`（多容器）时，按 `DirectPlayProfiles` 顺序找出**第一个被支持的**格式返回；否则原样返回。用于给 `StreamInfo.Container` 一个确定值。

`SetStreamInfoOptionsFromDirectPlayProfile`（`:633-646`）与 `SetStreamInfoOptionsFromTranscodingProfile`（`:594-631`）：把选定 profile 的属性（container / protocol / `TranscodeSeekInfo` / `MaxAudioChannels` / `EstimateContentLength` / `CopyTimestamps` / `EnableSubtitlesInManifest` / `EnableMpegtsM2TsMode` / `EnableAudioVbrEncoding` / `MinSegments` / `SegmentLength`）写回 `StreamInfo`。**`CopyTimestamps` 就在这里从 profile 传到播放决策**，最终影响 FFmpeg 的 `-copyts`（见 §1.3）。

#### 1.2.6 音频轨自动选择（很实用的一段）

`BuildVideoItem`（`:648-701`）里，音轨不是简单取 default，而是按用户偏好分层筛选：

```csharp
var audioStream = item.GetDefaultAudioStream(options.AudioStreamIndex ?? item.DefaultAudioStreamIndex);
ICollection<MediaStream> candidateAudioStreams = audioStream is null ? [] : [audioStream];

// 客户端显式指定 或 用户设过默认 -> 不做任何重选
if (!item.DefaultAudioIndexSource.HasFlag(AudioIndexSource.User) && (options.AudioStreamIndex is null or < 0))
{
    // 用户无偏好：允许在所有音轨里选
    if (item.DefaultAudioIndexSource == AudioIndexSource.None && audioStream is not null)
    {
        candidateAudioStreams = item.MediaStreams.Where(s => s.Type == Audio).ToArray();
        if (audioStream.IsDefault)
            candidateAudioStreams = candidateAudioStreams.Where(s => s.IsDefault).ToArray();  // 限定在 default 轨内
    }

    if (item.DefaultAudioIndexSource.HasFlag(AudioIndexSource.Language))
    {
        // 用户有语言偏好：只在同语言内选
        candidateAudioStreams = item.MediaStreams.Where(s => s.Type == Audio && s.Language == audioStream?.Language).ToArray();
        if (item.DefaultAudioIndexSource.HasFlag(AudioIndexSource.Default))
        {
            var sameLangDefault = candidateAudioStreams.Where(s => s.IsDefault).ToArray();
            // 同语言里没有 default 轨时，放宽到所有 default 轨（对应"无论语言都播默认音轨"设置）
            candidateAudioStreams = sameLangDefault.Length > 0
                ? sameLangDefault
                : item.MediaStreams.Where(s => s.Type == Audio && s.IsDefault).ToArray();
        }
    }
}
```

> **对自研播放器的启示**：这段"候选音轨集合"的思路非常值得抄——先按客户端指定 → 再按用户语言偏好 → 再按 default 标志，逐级放宽，能在"多语言多音轨"场景下显著减少用户手动切轨。

#### 1.2.7 默认字幕轨的打分选择

`GetDefaultSubtitleStreamIndex`（`:551-592`）：在所有字幕流中取 `Score` 最大者；若并列多个，优先选**交付成本最低**的那个（匹配 `SubtitleDeliveryMethod.External` 且格式与 `Codec` 相同，或有 VobSub/MKS 专用 profile）；都不行则回退 `item.DefaultSubtitleStreamIndex`。

**默认码率/音频码率封顶** `GetMaxAudioBitrateForTotalBitrate`（`:1243-1286`）：

| 总码率上限 | 音频码率上限 |
|---|---|
| ≤ 640 kbps | 128 kbps |
| ≤ 2 Mbps | 384 kbps |
| ≤ 3 Mbps | 448 kbps |
| ≤ 4 Mbps | 640 kbps |
| ≤ 5 Mbps | 768 kbps |
| ≤ 10 Mbps | 1536 kbps |
| ≤ 15 Mbps | 2304 kbps |
| ≤ 20 Mbps | 3584 kbps |
| 更高 | 7168 kbps |

（另有特例：源音频为单声道且码率 < 64 kbps 时，转码音频码率封顶 64000，避免 webm 编码失败，`:1226-1232`。）

---

### 1.3 转码：FFmpeg 参数是怎么拼出来的

#### 1.3.1 完整命令模板（**这是最有价值的一段，可逐字照抄**）

HLS 的完整命令行在 `Jellyfin.Api/Controllers/DynamicHlsController.cs:1578-1657` 的 `GetCommandLineArguments` 里用 `string.Format` 拼出：

```csharp
return string.Format(
    CultureInfo.InvariantCulture,
    "{0} {1} -map_metadata -1 -map_chapters -1 -threads {2} {3} {4} {5} "
  + "-copyts -avoid_negative_ts disabled -max_muxing_queue_size {6} "
  + "-f hls -max_delay 5000000 -hls_time {7} -hls_segment_type {8} -start_number {9}{10} "
  + "-hls_segment_filename \"{11}\" {12} -y \"{13}\"",
    inputModifier,          // {0}  输入前置参数（-ss / -copyts 相关 / hwaccel / -fflags ...）
    inputArgument,          // {1}  输入参数（-hwaccel ... -i "file"）
    threads,                // {2}  -threads N
    mapArgs,                // {3}  -map 0:v -map 0:a ... / -sn / -vn
    videoArguments,         // {4}  -codec:v:0 ... + 质量 + 关键帧 + 滤镜
    audioArguments,         // {5}  -codec:a:0 ... -ab ... -ac ... -ar ...
    maxMuxingQueueSize,     // {6}  默认 128，可用 EncodingOptions.MaxMuxingQueueSize 调大
    segmentLength,          // {7}  -hls_time（秒）
    segmentFormat,          // {8}  -hls_segment_type mpegts | fmp4[+ -hls_fmp4_init_filename ...]
    startNumber,            // {9}  -start_number
    baseUrlParam,           // {10} 仅 event playlist： -hls_base_url "hls/<name>/"
    outputTsArg,            // {11} <prefix>%d<ext>
    hlsArguments,           // {12} -hls_playlist_type vod|event -hls_list_size 0 [+ fmp4 movflags]
    outputPath);            // {13} m3u8 输出路径
```

引用：<https://github.com/jellyfin/jellyfin/blob/master/Jellyfin.Api/Controllers/DynamicHlsController.cs#L1640-L1656>

**展开成实际可用的一行（VOD / TS 分段 / CPU 转码）**：

```bash
ffmpeg \
  -ss 00:12:34.000 -copyts -avoid_negative_ts disabled -max_muxing_queue_size 128 \
  -i "/media/Movies/Film (2020)/Film (2020).mkv" \
  -map_metadata -1 -map_chapters -1 -threads 0 \
  -map 0:0 -map 0:1 -map -0:s \
  -codec:v:0 libx264 -preset veryfast -maxrate 3000000 -bufsize 6000000 \
  -profile:v:0 high -level:v:0 41 -force_key_frames:0 "expr:gte(t,n_forced*6)" -sc_threshold:v:0 0 \
  -codec:a:0 aac -ac 2 -ab 192000 -ar 48000 \
  -copyts -avoid_negative_ts disabled -max_muxing_queue_size 128 \
  -f hls -max_delay 5000000 -hls_time 6 -hls_segment_type mpegts -start_number 0 \
  -hls_segment_filename "/cache/transcodes/abc1230.ts" \
  -hls_playlist_type vod -hls_list_size 0 \
  -y "/cache/transcodes/abc123.m3u8"
```

（上面 `-codec:v`/`-bufsize`/关键帧等的具体值由下列各节的分支逻辑决定；`-copyts -avoid_negative_ts disabled -max_muxing_queue_size` 在模板里**固定出现**。）

#### 1.3.2 `-copyts` 与时间戳（模板中固定）

`-copyts -avoid_negative_ts disabled` **硬编码**在模板里，对所有 HLS 输出生效。这与 `TranscodingProfile.CopyTimestamps` 是两个不同层面的东西：

- 模板里的 `-copyts`：保证 ffmpeg 保留源时间戳，便于按 `-ss` 定位与分片对齐。
- `StreamInfo.CopyTimestamps`（来自 profile）：只影响**字幕滤镜**是否需要自己修正 PTS。见 `GetTextSubtitlesFilter`（`EncodingHelper.cs:1924`）：

```csharp
// hls always copies timestamps
var setPtsParam = state.CopyTimestamps || state.TranscodingType != TranscodingJobType.Progressive
    ? string.Empty
    : string.Format(CultureInfo.InvariantCulture, ",setpts=PTS -{0}/TB", seconds);
```

**另外两处 `-start_at_zero`**（用于让 `-ss` 的目标位置可被正确判定）：

| 场景 | 是否加 `-start_at_zero` | 位置 |
|---|---|---|
| 纯 copy（video codec 是 copy） | **总是加** | `DynamicHlsController.cs:1856` |
| 转码 + 有字幕流 | 加，**但外挂图形字幕除外** | `DynamicHlsController.cs:1881-1888` |

```csharp
args += " -start_at_zero";                                  // copy 分支，:1856
...
if (state.SubtitleStream is not null)
{
    // Disable start_at_zero for external graphical subs
    if (!(state.SubtitleStream.IsExternal && !state.SubtitleStream.IsTextSubtitleStream))
        args += " -start_at_zero";
}
```

#### 1.3.3 输入段：`GetInputArgument` + `GetInputModifier`

`GetInputArgument`（`EncodingHelper.cs:1250-1350`）负责**输入侧**：

```csharp
public string GetInputArgument(EncodingJobInfo state, EncodingOptions options, string segmentContainer)
{
    var arg = new StringBuilder();
    var inputVidHwaccelArgs = GetInputVideoHwaccelArgs(state, options);   // -hwaccel / -hwaccel_output_format ...
    if (!string.IsNullOrEmpty(inputVidHwaccelArgs)) arg.Append(inputVidHwaccelArgs);

    var canvasArgs = GetGraphicalSubCanvasSize(state);                    // 图形字幕画布尺寸
    if (!string.IsNullOrEmpty(canvasArgs)) arg.Append(canvasArgs);

    if (DVD || BluRay) {
        // 生成 concat 文件（把 VOB/M2TS 分片串起来），写在 <CachePath>/concat/<mediaSourceId>.concat
        arg.Append(" -f concat -safe 0 -i \"").Append(concatFilePath).Append("\" ");
    } else {
        arg.Append(" -i ").Append(_mediaEncoder.GetInputPathArgument(state));
    }

    if (NeedsExternalSubtitleMuxing(state)) { /* 把外挂字幕作为**第二个输入** -i file:"sub.srt" */ }
    if (state.AudioStream is not null && state.AudioStream.IsExternal) { /* 外挂音轨作为额外输入 */ }

    // 关闭硬件解码器的自动插入 SW scaler（避免分辨率变化时被偷偷缩放）
    var isSwDecoder = string.IsNullOrEmpty(GetHardwareVideoDecoder(state, options));
    if (!isSwDecoder) arg.Append(" -noautoscale");

    return arg.ToString();
}
```

**多输入约定**（`GetMapArgs` 依赖它，见 §1.3.6）：

| 输入序号 | 内容 |
|---|---|
| `0` | 主媒体文件 |
| `1` | 外挂字幕 **或** 外挂音轨（二者只会有其一出现在 `1`，除非…） |
| `2` | 当**同时**有外挂字幕和外挂音轨时，外挂音轨变成 `2` |

源码依据（`GetMapArgs`）：`int externalAudioMapIndex = hasExternalSubAsInput ? 2 : 1;`

外挂字幕以 `-i file:"..."` 形式加入；dvdsub/vobsub 的 `.sub` 会自动换成同名 `.idx`：

```csharp
// dvdsub/vobsub graphical subtitles use .sub+.idx pairs
var subtitleExtension = Path.GetExtension(subtitlePath.AsSpan());
if (subtitleExtension.Equals(".sub", StringComparison.OrdinalIgnoreCase))
{
    var idxFile = Path.ChangeExtension(subtitlePath, ".idx");
    if (File.Exists(idxFile)) subtitlePath = idxFile;
}
```

`GetInputModifier`（`EncodingHelper.cs:7320-7480`）负责**输入前置参数**，顺序为：

1. `-analyzeduration`（来自 `GetFfmpegAnalyzeDurationArg`）
2. `-probesize`（`GetFfmpegProbesizeArg`；配置项 `FFmpeg:probesize` 默认 `"1G"`）
3. `-user_agent` / `-referer`（远程 http 源）
4. **`-ss <seek>`** ← 由 `GetFastSeekCommandLineParameter` 生成
5. `-rtsp_transport tcp+udp -rtsp_flags prefer_tcp`（RTSP 源）
6. `-async <n>`（`InputAudioSync`）
7. `-re`（原生帧率读取）**或** `-readrate 10`（分段删除 + copy + HLS 时）
8. `-readrate_catchup <readrate*100>`
9. `-fflags +igndts/+ignidx/+genpts/+discardcorrupt/+fastseek`（按需组合）
10. `-f <inputFormat>`（仅在开了硬件加速且是 VideoFile 时）
11. `-stream_loop -1 -reconnect_at_eof 1 -reconnect_streamed 1 -reconnect_delay_max 2`（`MediaSource.RequiresLooping`）

`GetFastSeekCommandLineParameter`（`EncodingHelper.cs:3054-3085`）—— **seek 逻辑有个很实用的技巧**：

```csharp
var time = state.BaseRequest.StartTimeTicks ?? 0;
var maxTime = state.RunTimeTicks ?? 0;
if (time > 0)
{
    // 对 direct stream / remux，HLS 分片从关键帧开始。
    // 但 ffmpeg 在输入精确帧时刻时会 seek 到**前一个**关键帧，
    // 因此在 seek 时间上加 0.5s 偏移以命中关键帧（多数视频有效）。这有助于字幕同步。
    var isHlsRemuxing = state.IsVideoRequest && state.TranscodingType is TranscodingJobType.Hls
                        && IsCopyCodec(state.OutputVideoCodec);
    var seekTick = isHlsRemuxing ? time + 5000000L : time;      // +0.5 秒（5,000,000 ticks）

    // 越过 EOF 的 seek 没有意义：夹到 [0, RuntimeTicks - 5.0s]
    if (maxTime > 0)
        seekTick = Math.Clamp(seekTick, 0, Math.Max(maxTime - 50000000L, 0));

    seekParam = string.Format(CultureInfo.InvariantCulture, "-ss {0}", _mediaEncoder.GetTimeParameter(seekTick));
}
```

> **可直接照搬的两条规则**：① remux 场景 seek 目标 `+0.5s` 以命中关键帧；② seek 上限夹在 `时长 - 5s`，避免 muxer 拿不到包而报错退出。
> （ticks 换算：1 tick = 100 ns，`1 秒 = 10,000,000 ticks`，`5,000,000 ticks = 0.5 s`，`50,000,000 ticks = 5 s`。）

#### 1.3.4 视频段：编码器选择、质量参数、关键帧

**（a）编码器选择** —— `GetVideoEncoder`（`EncodingHelper.cs:481`）配合 `GetH264Encoder` / `GetH265Encoder` / `GetAv1Encoder`（`:203-243`）：

```csharp
public string GetH264Encoder(EncodingJobInfo state, EncodingOptions encodingOptions)
    => GetH26xOrAv1Encoder("libx264", "h264", state, encodingOptions);
public string GetH265Encoder(...) => GetH26xOrAv1Encoder("libx265", "hevc", state, encodingOptions);
public string GetAv1Encoder(...)  => GetH26xOrAv1Encoder("libsvtav1", "av1", state, encodingOptions);

private string GetH26xOrAv1Encoder(string defaultEncoder, string hwEncoder, EncodingJobInfo state, EncodingOptions encodingOptions)
{
    // 开了硬件编码 + 该编码允许 + 硬件支持 -> 返回 "<codec>_<hwaccel>"，如 h264_vaapi / hevc_nvenc
    // 否则回退 defaultEncoder（libx264 / libx265 / libsvtav1）
}
```

`HardwareAccelerationType` 枚举（`MediaBrowser.Model/Entities/HardwareAccelerationType.cs`，**小写是刻意的向后兼容**）：

```csharp
public enum HardwareAccelerationType { none = 0, amf = 1, qsv = 2, nvenc = 3, v4l2m2m = 4, vaapi = 5, videotoolbox = 6, rkmpp = 7 }
```

**（b）码率/质量参数** —— `GetVideoBitrateParam`（`EncodingHelper.cs:1634-1731`）**按编码器分支**，这段差异极大，务必按表实现：

```csharp
if (state.OutputVideoBitrate is null) return string.Empty;
int bitrate = state.OutputVideoBitrate.Value;
if (videoCodec == "h264_qsv") bitrate = Math.Max(bitrate, 1000);        // QSV 不允许 < 1000k
int bufsize = (int)Math.Min((long)bitrate * 2, int.MaxValue);          // 非 QSV 统一 bufsize = 2 × bitrate
```

| 编码器 | 生成的参数（逐字） |
|---|---|
| `libsvtav1` | ` -b:v {bitrate} -bufsize {bufsize}` |
| `libx264` / `libx265` | ` -maxrate {bitrate} -bufsize {bufsize}`（**注意：没有 `-b:v`**） |
| `h264_qsv` / `hevc_qsv` / `av1_qsv` | `{mbbrc} -b:v {bitrate} -maxrate {bitrate+1} -rc_init_occupancy {bitrate*factor} -bufsize {bitrate*2*factor}`；`mbbrc = " -mbbrc 1"`（仅 h264/hevc qsv）；`factor` 默认 2，但当 `ActualOutputVideoCodec == h264` 且 请求 level < 51 时 `factor = 1`（注释：`Some less powerful H.264 HW decoders require strict CPB size`） |
| `h264_amf` / `hevc_amf` | ` -rc cbr -qmin 0 -qmax 32 -b:v {bitrate} -maxrate {bitrate} -bufsize {bufsize}` |
| `h264_vaapi` / `hevc_vaapi` / `av1_vaapi` | Intel i965 驱动：` -rc_mode CBR -b:v {bitrate} -maxrate {bitrate} -bufsize {bufsize}`；其它：` -rc_mode VBR -b:v {bitrate} -maxrate {bitrate} -bufsize {bufsize}` |
| `h264_videotoolbox` / `hevc_videotoolbox` | ` -b:v {bitrate} -qmin -1 -qmax -1`（注释：`maxrate`/`bufsize` 会导致性能退化甚至编码器挂起） |
| 其它（兜底） | ` -b:v {bitrate} -maxrate {bitrate} -bufsize {bufsize}` |

引用：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Controller/MediaEncoding/EncodingHelper.cs#L1634-L1731>

**（c）preset / CRF** —— `GetEncoderParam`（`:1733`）与 `EncodingOptions`：

| `EncoderPreset` 枚举 | 值 |
|---|---|
| `auto` | 0 |
| `placebo` | 1 |
| `veryslow` | 2 |
| `slower` | 3 |
| `slow` | 4 |
| `medium` | 5 |
| `fast` | 6 |
| `faster` | 7 |
| `veryfast` | 8 |
| `superfast` | 9 |
| `ultrafast` | 10 |

`EncodingOptions` 里与质量相关的默认值：`H264Crf = 23`、`H265Crf = 28`、`EncoderPreset = auto`、`EncodingThreadCount = -1`、`AllowHevcEncoding = false`、`AllowAv1Encoding = false`、`EnableHardwareEncoding = true`。

Jellyfin Dashboard 传给 `DefaultVodEncoderPreset` / `DefaultEventEncoderPreset` 两个默认值（`DynamicHlsController.cs:1860` 调用处）：
```csharp
args += _encodingHelper.GetVideoQualityParam(state, codec, _encodingOptions,
        isEventPlaylist ? DefaultEventEncoderPreset : DefaultVodEncoderPreset);
```

**（d）关键帧 / GOP** —— `GetHlsVideoKeyFrameArguments`（`EncodingHelper.cs:2012-2096`），**强制关键帧对齐分片**：

```csharp
var keyFrameArg = $" -force_key_frames:0 \"expr:gte(t,n_forced*{segmentLength})\"";

var framerate = state.VideoStream?.RealFrameRate;
if (framerate.HasValue)
{
    // 仅强制关键帧不够：若编码器检测到场景切换插入关键帧，
    // 下一个强制关键帧会落在分片之外，破坏 seek。故同时限制 GOP 长度。
    gopArg = $" -g:v:0 {Math.Ceiling(segmentLength * framerate.Value)} -keyint_min:v:0 {Math.Ceiling(segmentLength * framerate.Value)}";
}
```

按编码器分三类：

| 编码器 | 使用 |
|---|---|
| `h264_qsv`, `h264_nvenc`, `h264_amf`, `h264_rkmpp`, `hevc_qsv`, `hevc_nvenc`, `hevc_rkmpp`, `av1_qsv`, `av1_nvenc`, `av1_amf`, `libsvtav1` | **仅 `gopArg`**（注释：`Unable to force key frames using these encoders`） |
| `libx264`, `libx265`, `h264_vaapi`, `hevc_vaapi`, `av1_vaapi` | **仅 `keyFrameArg`**；其中 `libx264` 额外加 ` -sc_threshold:v:0 0`（`prevent the libx264 from post processing to break the set keyframe`） |
| 其它 | **两者都加** |

还有一个 AMD 专用 workaround：`hevc_vaapi` + AMD 设备时追加
` -flags:v -global_header -bsf:v extract_extradata=remove=0`（band-in Parameter Sets 与 extradata 不一致导致 hvc1 解码失败）。

**（e）Dolby Vision / codec tag**（`DynamicHlsController.cs:1804-1836`）：
```csharp
if (hevc || av1) {
    var clientSupportsDoVi = requestedRange.Contains("DOVI");
    var videoIsDoVi = EncodingHelper.IsDovi(state.VideoStream);
    if (IsCopyCodec(codec) && videoIsDoVi && clientSupportsDoVi && !IsDoviRemoved(state)) {
        // hevc: DOVIWithHLG -> hvc1，否则 dvh1；av1 -> dav1
        args += $" -tag:v:0 {codecTag} -strict -2";
    } else if (hevc) {
        args += " -tag:v:0 hvc1";    // Prefer hvc1 to hev1
    }
}
```
（注释解释：8.4 用 `hvc1`，与 Dolby 官方样片一致；标 `dvh1` 会让 Safari 这类严格检查 tag 的播放器失败。）

**（f）位数 / 线程**：`-threads` 来自 `EncodingHelper.GetNumberOfThreads(state, encodingOptions, videoCodec)`；`EncodingThreadCount = -1` 表示用 `-threads 0`（ffmpeg 自动）。

#### 1.3.5 音频段

`GetAudioArguments`（`DynamicHlsController.cs:1664-1778`）：

**纯音频（`!state.IsOutputVideo`）**：
```csharp
audioTranscodeParams += "-vn";
if (IsCopyCodec(audioCodec)) return audioTranscodeParams + " -acodec copy" + bitStreamArgs + strictArgs;
audioTranscodeParams += " -acodec " + audioCodec + bitStreamArgs + strictArgs;
if (audioBitrate.HasValue && !LosslessAudioCodecs.Contains(actualOutputAudioCodec)) {
    // EnableAudioVbr && state.EnableAudioVbrEncoding && vbrParam != null -> 用 VBR 参数
    // 否则 -ab <bitrate>
}
if (audioChannels.HasValue)   audioTranscodeParams += " -ac " + audioChannels.Value;
if (OutputAudioSampleRate)    audioTranscodeParams += " -ar " + OutputAudioSampleRate.Value;
```

**带视频（转码）**：
```csharp
var args = "-codec:a:0 " + audioCodec + bitStreamArgs + strictArgs;

var useDownMixAlgorithm = DownMixAlgorithmsHelper.AlgorithmFilterStrings
    .ContainsKey((_encodingOptions.DownMixStereoAlgorithm, DownMixAlgorithmsHelper.InferChannelLayout(state.AudioStream)));

if (channels.HasValue && (channels.Value != 2 || (state.AudioStream?.Channels is not null && !useDownMixAlgorithm)))
    args += " -ac " + channels.Value;

if (bitrate.HasValue && !LosslessAudioCodecs.Contains(actualOutputAudioCodec)) { /* VBR 或 -ab */ }

if (state.OutputAudioSampleRate.HasValue) args += " -ar " + state.OutputAudioSampleRate.Value;
else if (state.AudioStream?.CodecTag == "ac-4")
    args += " -ar 48000";   // ac-4 采样率极怪，会搞挂编码器，强制重采样

args += _encodingHelper.GetAudioFilterParam(state, _encodingOptions);   // downmix 等滤镜
```

**`-strict -2` 的触发条件**（`:1674-1684`）—— mp4 muxer 里这些编码是 experimental：
```csharp
if (opus || dts || truehd || (flac && EncoderVersion < _minFFmpegFlacInMp4))
    strictArgs = " -strict -2";
```

`GetAudioBitStreamArguments`（`EncodingHelper.cs:1565`）：HLS + TS 容器时的音频比特流滤镜（如 `aac_adtstoasc` 类处理）。

#### 1.3.6 `-map` 规则（**极易出错，务必照抄**）

`GetMapArgs`（`EncodingHelper.cs:3087-3200`）：

```csharp
// 无任何媒体信息：视频用 -sn 丢字幕；否则返回空
if (state.VideoStream is null && state.AudioStream is null) return state.IsInputVideo ? "-sn" : string.Empty;

// 有媒体信息但不知道流索引：同样丢字幕
if (state.VideoStream is not null && state.VideoStream.Index == -1) return "-sn";
if (state.AudioStream is not null && state.AudioStream.Index == -1) return state.IsInputVideo ? "-sn" : string.Empty;
```

然后按序：

| 情况 | 生成的参数 |
|---|---|
| 有视频流 | `-map 0:{videoStreamIndex}`（索引来自 `FindIndex(state.MediaSource.MediaStreams, state.VideoStream)`） |
| 无视频流 | `-vn` |
| 有音频流且**外挂** | ` -map {1 或 2}:{audioStreamIndex}`（有外挂字幕输入时为 2，否则 1） |
| 有音频流且内嵌 | ` -map 0:{audioStreamIndex}` |
| 无音频流 | ` -map -0:a`（负映射 = 排除） |
| 无字幕流 **或** 交付方式为 `Hls` | ` -map -0:s` |
| 交付方式为 `Embed` + 外挂字幕 | ` -map 1:{inFileIndex}`，其中 `inFileIndex` = 同一字幕文件里、索引排在该流之前的流数量（MKS 多轨时用；单流 SRT/ASS/VTT 恒为 0） |
| 交付方式为 `Embed` + 内嵌字幕 | ` -map 0:{subtitleStreamIndex}` |
| 外挂且**非文本**字幕（图形，需烧录） | ` -map 1:{externalSubtitleStreamIndex} -sn` |

> **关键点**：`-map` 用的是"**在 `MediaStreams` 数组里的位置**"（`FindIndex`），不是 ffmpeg 的绝对流号。因为 `MediaStreams` 是探测后合并排序过的列表。自研实现里如果直接用 ffprobe 的 `index` 字段，要注意与 Jellyfin 的语义差异。

`GetNegativeMapArgsByFilters`（`:3205`）：当视频滤镜链需要独占某个流时，生成对应的负映射（`-map -0:v:...` 之类），避免同一流被用两次。

#### 1.3.7 fMP4 vs TS 分段

`GetCommandLineArguments`（`DynamicHlsController.cs:1591-1625`）：

```csharp
var segmentFormat = string.Empty;
var segmentContainer = outputExtension.TrimStart('.');
var hlsArguments = $"-hls_playlist_type {(isEventPlaylist ? "event" : "vod")} -hls_list_size 0";

if (segmentContainer == "ts") {
    segmentFormat = "mpegts";
}
else if (segmentContainer == "mp4") {
    var outputFmp4HeaderArg = OperatingSystem.IsWindows()
        // Windows 上必须给出 fmp4 init 段路径
        ? " -hls_fmp4_init_filename \"" + outputPrefix + "-1" + outputExtension + "\""
        // Linux/Unix 上 ffmpeg 会把 init 段生成到 m3u8 同目录
        : " -hls_fmp4_init_filename \"" + outputFileNameWithoutExtension + "-1" + outputExtension + "\"";

    var useLegacySegmentOption = _mediaEncoder.EncoderVersion < _minFFmpegHlsSegmentOptions;

    if (state.VideoStream is not null && state.IsOutputVideo) {
        // fMP4 需要 frag_discont 才能把音频首包延迟写进 MOOF::TRAF::TFDT；
        // HLS 不用 SIDX，跳过它可避免 FFmpeg 重写 open-GOP 边界包的 PTS
        hlsArguments += $" {(useLegacySegmentOption ? "-hls_ts_options" : "-hls_segment_options")} movflags=+frag_discont+skip_sidx";
    }
    segmentFormat = "fmp4" + outputFmp4HeaderArg;
}
else {
    _logger.LogError("Invalid HLS segment container: {SegmentContainer}, default to mpegts", segmentContainer);
    segmentFormat = "mpegts";   // 兜底
}
```

分段文件扩展名：`GetSegmentFileExtension`（`EncodingHelper.cs:1624-1632`）——**非空就返回 `"." + segmentContainer`，空则默认 `.ts`**。
分段文件名模板：`outputPrefix + "%d" + outputExtension`（`DynamicHlsController.cs:1589`），即 `abc1230.ts`、`abc1231.ts`…（`-start_number 0`）。

**分段就绪判定**（`GetSegmentResult`，`DynamicHlsController.cs:1915-1970`）——这是 HLS 服务端的关键工程细节，两条规则：

```csharp
// 规则 1：转码任务已退出 -> 认为所有已存在文件都好了
if (segmentExists && transcodingJob is not null && transcodingJob.HasExited) return ...;

// 规则 2：请求的分片索引 < 当前转码到的索引 -> 无法倒退转码，认为已就绪
var currentTranscodingIndex = GetCurrentTranscodingIndex(playlistPath, segmentExtension);
if (segmentIndex < currentTranscodingIndex) return ...;

// 否则：轮询等待，判定"就绪"的条件是 —— 文件存在 且（转码已结束 或 下一个分片也已存在）
while (!cancellationToken.IsCancellationRequested && !transcodingJob.HasExited) {
    if (segmentExists) {
        if (transcodingJob.HasExited || File.Exists(nextSegmentPath)) return ...;   // 就绪
    } else {
        segmentExists = File.Exists(segmentPath);
        if (segmentExists) continue;
    }
    await Task.Delay(100, cancellationToken);
}
```

还有两个特例（`DynamicHlsController.cs:1891-1895`）：
```csharp
// TODO why was this not enabled for VOD?
if (isEventPlaylist && segmentContainer == "ts") args += " -flags -global_header";
```

#### 1.3.8 码率阶梯：**服务端没有阶梯，客户端说了算**

这是最容易被误解的一点。证据链：

1. 官方文档（<https://jellyfin.org/docs/general/post-install/transcoding/>）原文：
   > "Transcoding of local sources is always requested by the client." … "The output resolution is determined by input resolution, input framerate, target output bitrate, input and output codecs, and client constraints. **There is no way to manually override the resolution.**"

2. 客户端在 `PlaybackInfo` 里传 `maxStreamingBitrate`（`PlaybackInfoDto.MaxStreamingBitrate`）。

3. 服务端只是把它折算成一个"上限"，用来判定是否直连：

```csharp
// MediaBrowser.Model/Dlna/MediaOptions.cs
public int? GetMaxBitrate(bool isAudio)
{
    if (MaxBitrate.HasValue) return MaxBitrate;          // 客户端显式给的
    if (Profile is null) return null;
    if (Context == EncodingContext.Static)
        return isAudio && Profile.MaxStaticMusicBitrate.HasValue
            ? Profile.MaxStaticMusicBitrate : Profile.MaxStaticBitrate;
    return Profile.MaxStreamingBitrate;                   // DeviceProfile 默认 8_000_000
}
```

4. `IsBitrateLimitExceeded` 命中 → `TranscodeReason.ContainerBitrateExceedsLimit` → 直连被否，走转码。

5. `DeviceProfile` 的码率默认值：`MaxStreamingBitrate = 8000000`、`MaxStaticBitrate = 8000000`、`MusicStreamingTranscodingBitrate = 128000`、`MaxStaticMusicBitrate = 8000000`。

**所以：所谓"码率阶梯"（如 4K/1080p/720p/480p 多档）是客户端 UI 里那张列表，客户端选一档 → 传 `maxStreamingBitrate` → 服务端按这个上限转码一次。** 服务端不生成多档 ABR 变体（`master.m3u8` 里通常只有一个 variant，见 §1.3.9）。

> **对自研实现的启示**：如果你想做 ABR（多档自适应），Jellyfin 这套**不**提供参考；你需要自己额外实现多 variant 输出。若只是"让用户选画质"，照 Jellyfin 做即可（一档一转码）。

#### 1.3.9 HLS 清单与 API 路由

`DynamicHlsController` 类声明为 `[Route("")]`，路由是**根路径绝对路由**（无 `/api` 前缀）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/Videos/{itemId}/live.m3u8` | 直播 HLS 清单 |
| GET | `/Videos/{itemId}/master.m3u8` | 主清单（variant 列表） |
| HEAD | `/Videos/{itemId}/master.m3u8` | 同上（Name = `HeadMasterHlsVideoPlaylist`） |
| GET | `/Audio/{itemId}/master.m3u8` | 音频主清单 |
| HEAD | `/Audio/{itemId}/master.m3u8` | Name = `HeadMasterHlsAudioPlaylist` |
| GET | `/Videos/{itemId}/main.m3u8` | 主播放清单（分片列表） |
| GET | `/Audio/{itemId}/main.m3u8` | 音频主播放清单 |
| GET | `/Videos/{itemId}/hls1/{playlistId}/{segmentId}.{container}` | 分片 |
| GET | `/Audio/{itemId}/hls1/{playlistId}/{segmentId}.{container}` | 音频分片 |

引用：<https://github.com/jellyfin/jellyfin/blob/master/Jellyfin.Api/Controllers/DynamicHlsController.cs#L164-L1268>

**路由前缀的重要事实**：`BaseJellyfinApiController` 声明为
```csharp
[ApiController]
[Route("[controller]")]
```
而 `DynamicHlsController`、`MediaInfoController`、`PlaystateController` 等都用 `[Route("")]` **覆盖**掉，因此完整路径就是方法上的字符串本身。**没有 `/api` 或 `/emby` 前缀**（路由前缀相关代码在 `Jellyfin.Server/Startup.cs:235-250` 只是 `UseRouting()` / `MapControllers()`）。
引用：<https://github.com/jellyfin/jellyfin/blob/master/Jellyfin.Api/BaseJellyfinApiController.cs>

**HLS 的 codec 字符串**（`CODECS=` 属性）由 `Jellyfin.Api/Helpers/HlsCodecStringHelpers.cs`（370 行）生成；`HlsHelpers.cs`（132 行）与 `DynamicHlsHelper.cs`（999 行）负责清单文本拼装。

#### 1.3.10 硬件加速：输入参数与设备映射

**（a）设备参数构造器**（`EncodingHelper.cs:830-982`）——每个加速后端一个：

| 方法 | 行 | 生成的参数 |
|---|---|---|
| `GetRkmppDeviceArgs(alias)` | 830 | RKMPP 设备 |
| `GetVideoToolboxDeviceArgs(alias)` | 838 | VideoToolbox |
| `GetCudaDeviceArgs(deviceIndex, alias)` | 846 | `-init_hw_device cuda=...` |
| `GetVulkanDeviceArgs(...)` | 860 | `-init_hw_device vulkan=...` |
| `GetOpenclDeviceArgs(...)` | 880 | `-init_hw_device opencl=...` |
| `GetD3d11vaDeviceArgs(deviceIndex, vendorId, alias)` | 900 | `-init_hw_device d3d11va=...` |
| `GetVaapiDeviceArgs(renderNodePath, driver, kernelDriver, vendorId, srcDeviceAlias, alias)` | 915 | `-init_hw_device vaapi=...` |
| `GetDrmDeviceArgs(renderNodePath, alias)` | 940 | `-init_hw_device drm=...` |
| `GetQsvDeviceArgs(renderNodePath, alias)` | 952 | `-init_hw_device qsv=...` |
| `GetFilterHwDeviceArgs(alias)` | 976 | `-filter_hw_device <alias>` |

**（b）解码器选择** —— `GetHardwareVideoDecoder`，按后端各有一个：
`GetQsvHwVidDecoder`（`:6771`）、`GetNvdecVidDecoder`（`:6854`）、`GetAmfVidDecoder`（`:6927`）、`GetVaapiVidDecoder`（`:6983`）、`GetVideotoolboxVidDecoder`（`:7056`）、`GetRkmppVidDecoder`（`:7121`）。
通用辅助：`GetHwDecoderName(options, decoderPrefix, decoderSuffix, videoCodec, bitDepth)`（`:6560`）、`GetHwaccelType(state, options, videoCodec, bitDepth, outputHwSurface)`（`:6618`）。
软件解码器名：`GetDecoderFromCodec(codec)`（`:653`）。

**（c）输入侧 hwaccel 参数**：`GetInputVideoHwaccelArgs(state, options)`（`:1016-1249`）—— 生成 `-hwaccel <type>`、`-hwaccel_output_format <fmt>`、`-init_hw_device ...`、`-hwaccel_flags ...` 等，被 `GetInputArgument` 放在**最前面**（在 `-i` 之前）。

**（d）滤镜链**：`GetVideoProcessingFilterParam(state, options, vidEncoder)`（`:6262`）是总入口，内部产出四段：
```csharp
return (mainFilters, subFilters, overlayFilters);   // 见 :3969-3985
```
- `mainFilters`：缩放（`GetSwScaleFilter:3474` / `GetHwScaleFilter:3339`）、去隔行（`GetSwDeinterlaceFilter:3633` / `GetHwDeinterlaceFilter:3643`）、色调映射（`GetHwTonemapFilter:3699` / `GetLibplaceboFilter:3780`）、HDR/SDR 参数（`GetInputHdrParam:6387` / `GetOutputSdrParam:6399`）、旋转（`GetVideoTransposeDirection:3851`）等；
- 若**有文本字幕** → `mainFilters.Add(GetTextSubtitlesFilter(state, false, false))`（注释示例：`subtitles=f='*.ass':alpha=0`）；
- 若**有图形字幕** → `subFilters` 加 `GetGraphicalSubPreProcessFilters(...)`，`overlayFilters` 加 `overlay=eof_action=pass:repeatlast=0`。

**（e）HDR → SDR**：官方文档明确（<https://jellyfin.org/docs/general/post-install/transcoding/>）：
> "When the source video is in HDR, it will need to be tone-mapped to SDR when transcoding, as **Jellyfin currently doesn't support HDR to HDR tone-mapping, or passing through HDR metadata.**"

相关配置项：`EnableTonemapping`（默认 `false`）、`EnableVppTonemapping`、`EnableVideoToolboxTonemapping`、`TonemappingAlgorithm`（默认 `bt2390`）、`TonemappingMode`（默认 `auto`）、`TonemappingRange`（默认 `auto`）、`TonemappingDesat = 0`、`TonemappingPeak = 100`、`TonemappingParam = 0`、`VppTonemappingBrightness = 16`、`VppTonemappingContrast = 1`。

**（f）各加速后端的能力矩阵（来自官方文档，非源码）**

厂商 × 操作系统选型（逐字，<https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/>）：

| GPU Vendor | Windows | macOS | Linux |
|---|---|---|---|
| AMD | AMF | VideoToolbox | VAAPI |
| Apple | N/A | VideoToolbox | N/A |
| Intel | QSV | VideoToolbox | QSV |
| Nvidia | NVENC | N/A | NVENC |
| Rockchip | N/A | N/A | RKMPP |

NVIDIA 并发 NVENC 会话数上限（逐字，<https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/known-issues>）：

| NVIDIA driver | NVENC concurrent sessions |
|---|---|
| 590 and newer | Up to 12 encoding sessions |
| 550 to 58x | Up to 8 encoding sessions |
| 530 to 546 | Up to 5 encoding sessions |
| pre-530 | Up to 3 encoding sessions |

关键编解码门槛（逐字摘录）：

| 后端 | 关键结论 |
|---|---|
| Intel QSV/VA-API | H.264 8-bit 编解码：任何支持 QSV 的 GPU；HEVC 8-bit：Gen 9 Skylake (6th Gen) +；HEVC 10-bit：Gen 9.5 Kaby Lake (7th Gen)/Apollo Lake/Gemini Lake +；AV1 解码：Tiger Lake (11th Gen) +；AV1 编码：DG2/ARC A、Meteor Lake + |
| NVIDIA | H.264 8-bit：任何 NVENC/NVDEC；HEVC 8-bit：Maxwell 2nd Gen (GM206) +；HEVC 10-bit 解码：GM206 +，编码：Pascal +；AV1 解码：Ampere +，编码：Ada Lovelace + |
| AMD | H.264 8-bit：任何 AMF/VA-API；HEVC 8-bit 编码：Polaris (RX 400) +；HEVC 10-bit 编码：Renoir/RX 5000 (Navi 1x) +；AV1 解码：RX 6000 (Navi 2x)/Ryzen 6000 mobile +（**RX 6400/6500 除外**）；AV1 编码：RX 7000 (Navi 3x)/Ryzen 7000+ |
| Apple | VideoToolbox 是唯一方法；HEVC 编解码需 2017 及以后 Mac（**排除 MacBook Air 13" 2017**）；AV1 **仅解码**，M3 及以后 |
| Rockchip | RKMPP 需 Jellyfin 10.9+，仅 Linux，仅 RK3588/3588S 实测；"As of the RK3588 series, there is no Rockchip SoC that supports AV1 encoder." |

**通用硬限制**（逐字）："The hardware decoding of H.264 / AVC 10-bit (High 10 profile) video is **not supported by any Intel, NVIDIA and AMD GPU**. It is only supported by Apple Silicon and Rockchip."

**（g）本仓库内的配置默认值**（`MediaBrowser.Model/Configuration/EncodingOptions.cs` 构造函数）：

```csharp
EnableFallbackFont = false;              EnableAudioVbr = false;
DownMixAudioBoost = 2;                   DownMixStereoAlgorithm = DownMixStereoAlgorithms.None;
MaxMuxingQueueSize = 2048;               EnableThrottling = false;      ThrottleDelaySeconds = 180;
EnableSegmentDeletion = false;           SegmentKeepSeconds = 720;
EncodingThreadCount = -1;                VaapiDevice = "/dev/dri/renderD128";   QsvDevice = "";
EnableTonemapping = false;               EnableVppTonemapping = false;  EnableVideoToolboxTonemapping = false;
TonemappingAlgorithm = TonemappingAlgorithm.bt2390;  TonemappingMode = auto;  TonemappingRange = auto;
H264Crf = 23;                            H265Crf = 28;                  EncoderPreset = auto;
DeinterlaceDoubleRate = false;           DeinterlaceMethod = yadif;
EnableDecodingColorDepth10Hevc = true;   EnableDecodingColorDepth10Vp9 = true;
EnableDecodingColorDepth10HevcRext = false;  EnableDecodingColorDepth12HevcRext = false;
EnableEnhancedNvdecDecoder = true;       PreferSystemNativeHwDecoder = true;
EnableIntelLowPowerH264HwEncoder = false;  EnableIntelLowPowerHevcHwEncoder = false;
EnableHardwareEncoding = true;           AllowHevcEncoding = false;     AllowAv1Encoding = false;
EnableSubtitleExtraction = true;         SubtitleExtractionTimeoutMinutes = 30;
AllowOnDemandMetadataBasedKeyframeExtractionForExtensions = ["mkv"];
HardwareDecodingCodecs = ["h264", "vc1"];                 // ★ 默认只有这两个走硬解
HlsAudioSeekStrategy = HlsAudioSeekStrategy.TrimCopiedAudio;
```

注意 `HardwareDecodingCodecs` 默认是 `["h264", "vc1"]` —— 即**默认只有 H.264 与 VC1 硬解**，HEVC/VP9/AV1 需要用户显式勾选。

另有两条来自 `ConfigurationOptions` 的全局配置（官方文档 <https://jellyfin.org/docs/general/administration/configuration>）：
```
FFmpeg:probesize       = "1G"
FFmpeg:analyzeduration = "200M"
```

#### 1.3.11 转码限速（Throttling）与任务生命周期

**限速开关**：`EncodingOptions.EnableThrottling`（默认 `false`）、`ThrottleDelaySeconds`（默认 `180`）。
**实现**：`MediaBrowser.Controller/MediaEncoding/TranscodingThrottler.cs` —— 一个 5 秒周期的定时器，比较"客户端下载到的位置"与"ffmpeg 转码到的位置"，超前太多就**通过向 ffmpeg 的 stdin 写按键**来暂停/恢复，而不是杀进程：

```csharp
public void Start() => _timer = new Timer(TimerCallback, null, 5000, 5000);   // 每 5 秒检查

public async Task UnpauseTranscoding()
{
    if (_isPaused)
    {
        _logger.LogDebug("Sending resume command to ffmpeg");
        var resumeKey = _mediaEncoder.IsPkeyPauseSupported ? "u" : Environment.NewLine;
        await _job.Process!.StandardInput.WriteAsync(resumeKey).ConfigureAwait(false);
        _isPaused = false;
    }
}
// :118  if (options.EnableThrottling && IsThrottleAllowed(_job, Math.Max(options.ThrottleDelaySeconds, 60)))
//        -> 注意：实际生效阈值是 max(ThrottleDelaySeconds, 60)，即最低 60 秒
```
引用：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Controller/MediaEncoding/TranscodingThrottler.cs>

**分段清理**：`EnableSegmentDeletion`（默认 false）+ `SegmentKeepSeconds`（默认 720）；实现见 `MediaBrowser.Controller/MediaEncoding/TranscodingSegmentCleaner.cs`。开启后 `GetInputModifier` 会给 copy 型 HLS 加 `-readrate 10`，防止 ffmpeg 因磁盘太快而提前退出（`EncodingHelper.cs:7381-7393`）。

**任务生命周期**：`MediaBrowser.MediaEncoding/Transcoding/TranscodeManager.cs`
```csharp
private readonly List<TranscodingJob> _activeTranscodingJobs = new();     // :48
public void PingTranscodingJob(string playSessionId, bool? isUserPaused)  // :118
public Task KillTranscodingJobs(string deviceId, string? playSessionId, Func<string, bool> deleteFiles)  // :194
public TranscodingJob? OnTranscodeBeginRequest(string path, TranscodingJobType type)  // :688
```
- **心跳**：`POST /Sessions/Playing/Ping?playSessionId=...` → `_transcodeManager.PingTranscodingJob(playSessionId, null)`（`PlaystateController.cs:233-239`）。
- **停止即杀**：`POST /Sessions/Playing/Stopped` 时若带 `PlaySessionId`，服务端**立刻杀掉对应转码任务**（`PlaystateController.cs:247-259`）：
  ```csharp
  if (!string.IsNullOrWhiteSpace(playbackStopInfo.PlaySessionId))
      await _transcodeManager.KillTranscodingJobs(User.GetDeviceId()!, playbackStopInfo.PlaySessionId, s => true);
  ```
- **进度事件也会 ping**：`TranscodeManager.OnPlaybackProgress` 订阅了 `ISessionManager` 的 `PlaybackProgress` / `PlaybackStart` 事件，用事件里的 `PlaySessionId` 调 `PingTranscodingJob`（`:709-716`）。

**转码临时目录**：`EncodingOptions.TranscodingTempPath`；清理由定时任务 `DeleteTranscodeFileTask` 负责（`IntervalTicks = TimeSpan.FromHours(24)`，`Emby.Server.Implementations/ScheduledTasks/Tasks/DeleteTranscodeFileTask.cs:76`）。

#### 1.3.12 进度式（Progressive）输出 —— 非 HLS 的整文件转码

除 HLS 外还有一条 `TranscodingJobType.Progressive` 路径（用于"下载/离线转码/边转边发的 mp4"）：

`GetProgressiveVideoFullCommandLine`（`EncodingHelper.cs:7677-7705`）：
```csharp
if (Path.GetExtension(outputPath).Equals(".mp4", OrdinalIgnoreCase) && state.BaseRequest.Context == EncodingContext.Streaming)
    // 边转边发的 mp4 必须用 fragmented mp4
    format = " -f mp4 -movflags frag_keyframe+empty_moov+delay_moov";

return string.Format(
    CultureInfo.InvariantCulture,
    "{0} {1}{2} {3} {4} -map_metadata -1 -map_chapters -1 -threads {5} {6}{7}{8} -y \"{9}\"",
    inputModifier,                 // {0}
    GetInputArgument(...),         // {1}
    keyFrame,                      // {2}
    GetMapArgs(state),             // {3}
    GetProgressiveVideoArguments(...),  // {4}
    threads,                       // {5}
    GetProgressiveVideoAudioArguments(...),  // {6}
    GetSubtitleEmbedArguments(state),        // {7} 字幕 mux 进容器
    format,                        // {8}
    outputPath);                   // {9}
```
音频整转：`GetProgressiveAudioFullCommandLine`（`:7867`）。

> **对自研实现的启示**：`-movflags frag_keyframe+empty_moov+delay_moov` 是"边转边播 mp4"的标准做法，直接抄。

---

### 1.4 字幕

#### 1.4.1 五种交付方式

`MediaBrowser.Model/Dlna/SubtitleDeliveryMethod.cs`（**注意路径在 `Dlna/` 而不是 `Entities/`**，且有显式赋值）：

```csharp
public enum SubtitleDeliveryMethod { Encode = 0, Embed = 1, External = 2, Hls = 3, Drop = 4 }
```
| 值 | 含义 |
|---|---|
| `Encode` (0) | **烧录**进视频画面 |
| `Embed` (1) | 混流进输出容器/流 |
| `External` (2) | 作为独立文件单独提供（客户端自己渲染） |
| `Hls` (3) | 作为独立 HLS 字幕流（`EXT-X-MEDIA:TYPE=SUBTITLES`） |
| `Drop` (4) | 丢弃 |

引用：<https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/Dlna/SubtitleDeliveryMethod.cs>

**交付方式的决策顺序**（`StreamBuilder.GetSubtitleProfile`，`StreamBuilder.cs:1465-1543`）：

```
若 CanConsiderEmbedSubtitle(...)：        # 见下
    1) 找 SubtitleProfiles 中 Method==Embed 的 profile：
         SupportsLanguage ok? profile.Container 含 outputContainer? （转码时还要求 IsSubtitleEmbedSupported(outputContainer)）
         subtitleStream.IsTextSubtitleStream == IsTextFormat(profile.Format) 且 profile.Format == subtitleStream.Codec  -> 返回
    2) 找 Method==Embed 且可转换的 profile：subtitleStream.IsTextSubtitleStream && SupportsSubtitleConversionTo(profile.Format) -> 返回
3) GetExternalSubtitleProfile(allowConversion=false)
   ?? GetExternalSubtitleProfile(allowConversion=true)
   ?? new SubtitleProfile { Method = Encode, Format = subtitleStream.Codec }     # 兜底：烧录
```

两个门槛函数：

```csharp
private static bool IsSubtitleEmbedSupported(string? transcodingContainer)
{
    if (!string.IsNullOrEmpty(transcodingContainer))
    {
        if (ContainerHelper.ContainsContainer(transcodingContainer, "ts,mpegts,mp4")) return false;   // 这三种不能 Embed
        if (ContainerHelper.ContainsContainer(transcodingContainer, "mkv,matroska")) return true;
    }
    return false;
}

private static bool CanConsiderEmbedSubtitle(MediaStream subtitleStream, PlayMethod playMethod,
                                             MediaStreamProtocol? transcodingSubProtocol, string? outputContainer)
{
    if (subtitleStream.IsExternal)
        return playMethod == PlayMethod.Transcode
            && transcodingSubProtocol != MediaStreamProtocol.hls
            && IsSubtitleEmbedSupported(outputContainer);

    return playMethod != PlayMethod.Transcode || transcodingSubProtocol != MediaStreamProtocol.hls;
}
```

> **关键结论**：**TS/MP4 输出不能 Embed 字幕**（只有 MKV 可以）；HLS 输出时内部字幕也不能 Embed。

**一个特殊修正**（`EncodingHelper.NormalizeSubtitleEmbed`，`:7640-7653`）：DVBSUB 若被选为 `Embed`，会被**强制改成 `Encode`（烧录）**：
```csharp
// This is tricky to remux in, after converting to dvdsub it's not positioned correctly
// Therefore, let's just burn it in
if (string.Equals(state.SubtitleStream.Codec, "DVBSUB", StringComparison.OrdinalIgnoreCase))
    state.SubtitleDeliveryMethod = SubtitleDeliveryMethod.Encode;
```

#### 1.4.2 文本 vs 图形字幕的判定

`MediaStream`（`MediaBrowser.Model/Entities/MediaStream.cs`）三个计算属性：

```csharp
public bool IsTextSubtitleStream  => Type == Subtitle && (IsExternal || !string.IsNullOrEmpty(Codec)) && IsTextFormat(Codec);   // :610
public bool IsPgsSubtitleStream   => ... && IsPgsFormat(Codec);      // :629
public bool IsVobSubSubtitleStream=> ... && IsVobSubFormat(Codec);   // :648
public bool IsExtractableSubtitleStream => IsTextSubtitleStream || IsPgsSubtitleStream || IsVobSubSubtitleStream;   // :672
```

静态判定（`:741-770`）：
- `IsTextFormat(codec)`：含 `"microdvd"` 即文本；否则**排除**含 `pgs`/`dvdsub`/`vobsub`/`dvbsub` 的，以及等于 `sup`/`sub` 的。
- `IsPgsFormat(codec)`、`IsVobSubFormat(codec)` 各自正匹配。
- `SupportsSubtitleConversionTo(format)`（`:772+`）：`ass`/`ssa` 既不能作源也不能作目标。

**文本格式常量**在 `MediaBrowser.Model/MediaInfo/SubtitleFormat.cs:5-14`：
`SRT="srt"`, `SUBRIP="subrip"`, `SSA="ssa"`, `ASS="ass"`, `VTT="vtt"`, `WEBVTT="webvtt"`, `TTML="ttml"`。

#### 1.4.3 字幕抽取：**只有抽取用 FFmpeg，格式转换是纯托管的**

这一点与很多人的直觉相反。`MediaBrowser.MediaEncoding/Subtitles/SubtitleEncoder.cs`：

**（a）FFmpeg 只在这些场景被调用**

| 场景 | 代码位置 | 逐字参数 |
|---|---|---|
| 从主文件抽取内部流（批量） | `SubtitleEncoder.cs:647-705`（参数字符串在 654-657、687-693） | `-y -i {inputPath} -map 0:{streamIndex} -an -vn -c:s {outputCodec}{ -f matroska} -flush_packets 1 "{outputPath}"` |
| 从外部 `.mks` 抽取 | `:568-645`（597-600、629-635） | 同上格式，索引用 `EncodingHelper.FindIndex` |
| 外部不支持扩展名 → 转 srt | `:433-466`（457） | `-y { -sub_charenc XXX} -i "{inputPath}" -c:s srt "{outputPath}"` |
| 取字符集时的内部转换 | `:825-850`（837-843，**全文件唯一带 `-copyts` 处**） | `-y -i {inputPath} -copyts -map 0:{subtitleIndex} -an -vn -c:s {outputCodec} "{outputPath}"` |

要点：
- `outputCodec` = `copy` 或 `srt`；`{ -f matroska}` **仅 VobSub 时加**（源码注释：ffmpeg 没有 `.idx`/`.sub` 的 muxer，`:674`、`:616-617`）。
- 抽取进程参数前缀固定 `"-nostdin "`（`:879`），stdin 被关闭（`:899`），stderr 异步 drain（`:902`），超时用 `EncodingOptions.SubtitleExtractionTimeoutMinutes`（**默认 30 分钟**）。
- 失败判定：`exitCode == -1` 或产物不存在/0 字节 → 删文件并抛 `FfmpegException`（`:707-783`）。
- 抽取出的 `.ass` 会调 `SetAssFont`（`:937-966`）：把样式里的 `,Arial,` 替换为 `,Arial Unicode MS,`。
- 可抽取格式判定 `IsCodecCopyable`（`:504-512`）：`ass/ssa/srt/subrip/pgssub/vobsub`。
- 缓存新鲜度用旁路 `.meta` 文件存 `"<size>:<mtimeTicks>"`（`IsCachedSubtitleFresh:361-397`、`WriteCacheMeta:399-420`）。

**（b）格式转换（srt↔vtt↔ass↔json）是纯 C# 实现**

`ConvertSubtitles`（`:77-95`）：用 **SubtitleEdit（NuGet `libse` 4.0.12）** 解析 → `FilterEvents` → `GetWriter(outputFormat).ToText` → UTF-8 MemoryStream。**完全不调用 FFmpeg。**

- `SubtitleEditParser.cs:31-78`：按扩展名取候选 `SubtitleFormat` 类型列表，逐个 `LoadSubtitle`，`ErrorCount == 0` 即停；`Paragraphs == 0` 抛异常。
- `SubtitleEditParser.cs:84-120`：**反射扫描** `Nikse.SubtitleEdit.Core.SubtitleFormats.SubtitleFormat` 的全部非抽象子类，按 `Extension.TrimStart('.')` 建字典（大小写不敏感）。
- 自定义输出格式 `JsonWriter.cs:15-53`（"JSON Jellyfin"）：输出 `TrackEvents[{Id,Text,StartPositionTicks,EndPositionTicks}]`。**这就是 `format=json`（`format=js` 是别名）的来源。**

**（c）字符编码检测**：本分支已**不再用 ffprobe 探测**，改用 `UTF.Unknown`（UtfUnknown）库：
`GetSubtitleStream(SubtitleInfo)`（`:167-201`）—— 仅当 `IsExternal && IsTextFormat(Format)` 时用 `CharsetDetector` 探测（`:1006-1030`）；UTF-8/ASCII 直接短路返回文件流；其它编码用 `StreamReader` 重编码为 UTF-8。探测不到时返回**空串**（`:991`）；**全仓库不存在字面量 `"Unknown"` 作为编码回退值**（这与旧版 Jellyfin 不同，请注意）。

**（d）时间轴平移**（这是最接近"字幕偏移"的服务端能力，但不是用户可调的偏移）
`FilterEvents`（`:97-117`）：丢弃 `start` 之前已结束的 cue；若 `endTimeTicks > 0` 则丢弃 `start > end` 的；**`!preserveTimestamps` 时把 Start/End 各减去 `startPositionTicks`**。这个 `preserveTimestamps` 就是 API 查询参数 `copyTimestamps`。

#### 1.4.4 字幕偏移（delay）：**服务端完全没有**

全树 grep（含非 `.cs` 文件）`SubtitleOffset|subtitle-offset|itsoffset|SetSubtitleOffset|subtitle_delay|subdelay` → **零命中**。

唯一"看起来像"的是 `EncodingJobInfo.cs:87` 的 `InternalSubtitleStreamOffset`，它在 `EncodingHelper.cs:7527` 被赋值后**全树从未被读取**（死代码）。

> **结论：字幕延迟/偏移是 100% 的客户端功能**（jellyfin-web 等在本地渲染时自行加减时间轴）。自研播放器需要自己实现。

#### 1.4.5 烧录（burn-in）：`subtitles=` 滤镜 + libass

**触发条件**（`EncodingHelper.cs:8000-8004`）：
```csharp
private static bool ShouldEncodeSubtitle(EncodingJobInfo state)
{
    return state.SubtitleDeliveryMethod == SubtitleDeliveryMethod.Encode
           || (state.BaseRequest.AlwaysBurnInSubtitleWhenTranscoding && !IsCopyCodec(state.OutputVideoCodec));
}
```
即：`AlwaysBurnInSubtitleWhenTranscoding=true` 且视频**不是** copy 时，**即使** method 本来是 External/Embed 也会烧录。

**滤镜生成**（`GetTextSubtitlesFilter`，`EncodingHelper.cs:1924-1986`）：

```csharp
public string GetTextSubtitlesFilter(EncodingJobInfo state, bool enableAlpha, bool enableSub2video)
{
    var seconds = Math.Round(TimeSpan.FromTicks(state.StartTimeTicks ?? 0).TotalSeconds);

    // hls always copies timestamps
    var setPtsParam = state.CopyTimestamps || state.TranscodingType != TranscodingJobType.Progressive
        ? string.Empty
        : string.Format(CultureInfo.InvariantCulture, ",setpts=PTS -{0}/TB", seconds);

    var alphaParam    = enableAlpha    ? ":alpha=1"    : string.Empty;
    var sub2videoParam= enableSub2video? ":sub2video=1": string.Empty;

    var fontPath  = _pathManager.GetAttachmentFolderPath(state.MediaSource.Id);
    var fontParam = fontPath is null ? string.Empty
        : string.Format(CultureInfo.InvariantCulture, ":fontsdir='{0}'", _mediaEncoder.EscapeSubtitleFilterPath(fontPath));

    if (state.SubtitleStream.IsExternal)
    {
        var charsetParam = string.Empty;
        if (!string.IsNullOrEmpty(state.SubtitleStream.Language))
        {
            var charenc = _subtitleEncoder.GetSubtitleFileCharacterSet(...).GetAwaiter().GetResult();
            if (!string.IsNullOrEmpty(charenc)) charsetParam = ":charenc=" + charenc;
        }
        return string.Format(CultureInfo.InvariantCulture, "subtitles=f='{0}'{1}{2}{3}{4}{5}",
            EscapeSubtitleFilterPath(state.SubtitleStream.Path), charsetParam, alphaParam, sub2videoParam, fontParam, setPtsParam);
    }

    var subtitlePath = _subtitleEncoder.GetSubtitleFilePath(state.SubtitleStream, state.MediaSource, CancellationToken.None)...;
    return string.Format(CultureInfo.InvariantCulture, "subtitles=f='{0}'{1}{2}{3}{4}",
        EscapeSubtitleFilterPath(subtitlePath), alphaParam, sub2videoParam, fontParam, setPtsParam);
}
```

> **重点：Jellyfin 只用 `subtitles=`（即 libass）滤镜。**
> **没有** `force_style`、**没有** `original_size`、**没有** `ass=` 滤镜（全树 grep 零命中）。
> `EscapeSubtitleFilterPath`（`MediaEncoder.cs:1224-1234`）的转义规则：`\`→`/`；`:`→`\:`；`'`→`'\\\''`；`"`→`\"`（注释说明 ffmpeg filtergraph 需要**双重转义**）。

**滤镜链如何装配**（`GetVideoProcessingFilterParam`，`EncodingHelper.cs:6262-6375`）：
- `overlayFilters.Count == 0`（纯文本字幕）→ ` -vf "{main}"`（`:6326`）
- 否则（图形字幕 / 硬件叠加）→ `-filter_complex`（`:6348-6370`）：
```
 -filter_complex "[{mapPrefix}:{subIdx}]{sub}[sub];[0:{vidIdx}]{main}[main];[main][sub]{overlay}"
 -filter_complex "{sub}[sub];[0:{vidIdx}]{main}[main];[main][sub]{overlay}"       # 纯文本字幕变体
```
其中 `mapPrefix = Convert.ToInt32(state.SubtitleStream.IsExternal)`（`:6344`），索引由 `GetSubtitleStreamIndexForFfmpeg`（`:7977-7993`）算出（BluRay 时需补偿隐藏的 trueHD/atmos 流）。
配套：`GetNegativeMapArgsByFilters`（`:3205-3221`）在出现 `-filter_complex` 时输出 `-map -0:{videoIndex} `，避免同一视频流被映射两次。

**图形字幕（PGS/VobSub）烧录**：
- 画布尺寸：`GetGraphicalSubCanvasSize`（`:983-1008`）→ ` -canvas_size {W}x{H}`，**显式排除 DVBSUB**（注释：`DVBSUB uses the fixed canvas size 720x576`）。该参数在 `GetInputArgument` 中被注入**两次**：主输入前（`:1260-1264`）和外挂字幕输入前（`:1321-1324`）。
- 预处理滤镜：`GetGraphicalSubPreProcessFilters`（`:3389-3434`）：
```
scale,scale=-1:{H}:fast_bilinear,crop,pad=max({W}\,iw):max({H}\,ih):(ow-iw)/2:(oh-ih)/2:black@0,crop={W}:{H}
```
  （当视频 DAR 与字幕 DAR 差 < 0.01 时退化为 `scale,scale={W}:{H}:fast_bilinear`）
- overlay：`overlay=eof_action=pass:repeatlast=0`

**硬件路径上的烧录**（比软件复杂，需构造 alpha 源）：NVIDIA/AMD 用
`alphasrc=s={W}x{H}:r={r}:start='{t}'`（`:3440-3472`）+ `format=yuva420p` + `subtitles=(alpha=1,sub2video=1)` + `hwupload=derive_device=cuda` + `overlay_cuda`（`:4149-4178`）；Intel/VAAPI/VideoToolbox/RKMPP 结构相同（`:5354` / `:5533` / `:5928` / `:6217`）。

**禁止视频 copy**：`CanStreamCopyVideo`（`:2431-2436`）—— 有字幕烧录时不允许 `-codec:v copy`。

**Embed 的参数**（`GetSubtitleEmbedArguments`，`:7655-7675`）：
```csharp
return " -codec:s:0 " + codec + " -disposition:s:0 default";
// codec = "copy"（format 为空 或 format == 源 codec）否则取 SupportedSubtitleCodecs.First()
```

#### 1.4.6 字体与 libass：**dump 到磁盘，绝不 mux 进容器**

这是 Jellyfin 字幕方案里最值得学的一处设计。

**字体来源**：MKV 里内嵌的字体附件（`MediaAttachment`）。抽取实现 `MediaBrowser.MediaEncoding/Attachments/AttachmentExtractor.cs`：

| 场景 | 位置 | 逐字参数 |
|---|---|---|
| 逐个抽取（文件名不安全时） | `:121-253`（参数 158-180） | `-dump_attachment:{index} "{path}" [-f concat -safe 0] {inputPath} [-t 0 -f null null]` |
| 全部抽取 | `:255-364`（参数 289-294） | `-dump_attachment:t "" -y [-f concat -safe 0] {inputPath} [-t 0 -f null null]` |
| 单个附件 | `:407-499`（参数 422-428） | 同上模式 |

- `WorkingDirectory` 设为输出目录（`:307`）。
- `GetAttachment`（`:62-95`）**拒绝 `mjpeg`** 类型（`:86-89`）。
- 有音视频流时加 `-t 0 -f null null`；否则省略（注释 `:282-286`：此时 ffmpeg 以 exit 1 结束属于**预期**行为）。

**存储目录**（`Emby.Server.Implementations/Library/PathManager.cs`）：

| 内容 | 路径 | 行 |
|---|---|---|
| 字体/附件缓存根 | `<DataPath>/attachments`（`AttachmentCachePath`） | `:41` |
| 某条目的字体目录 | `attachments/<id[..2]>/<id>`（`GetAttachmentFolderPath`） | `:63-73`（非 GUID 返回 null） |
| 单个附件 | `GetAttachmentPath`，经 `PathHelper.GetSafeLeafFileName` 防目录穿越 | `:44-60` |
| 字幕缓存 | `subtitles/<id[..2]>/<id>/<streamIndex><ext>` | `:39`、`:76-93` |

**何时触发字体抽取**（`MediaBrowser.MediaEncoding/Transcoding/TranscodeManager.cs:398-415`）：
```csharp
// 仅当有字幕流 且（交付方式是 Encode 或 AlwaysBurnInSubtitleWhenTranscoding）时才抽
// DVD/BluRay 源用 concat 文件（:401-405）；外部 .mks 字幕再抽一次（:411-414）
```

**libass 如何拿到字体**：只通过 `:fontsdir='<attachments/xx/yyy>'`（`EncodingHelper.cs:1941`）。

> **全树 grep `-attach` 零命中** —— Jellyfin **从不**把字体 mux 进输出容器，而是 dump 到磁盘目录再让 libass 通过 `fontsdir` 加载。这个设计让转码进程保持无状态、可复用，也避免了重复 mux。

**另有一条 HTTP 通路**：MKV 内嵌字体可通过 `GET /Videos/{videoId}/{mediaSourceId}/Attachments/{index}` 下载（`VideoAttachmentsController.cs:50`，**无额外权限限制**）。

**Web 客户端字体回退（fallback fonts）**：`ServerConfiguration`/`EncodingOptions.FallbackFontPath` + `EnableFallbackFont`（**默认 false**），仅供**客户端** SubtitlesOctopus 渲染 ASS 用，**转码/烧录路径完全不用它**：
- `GET /FallbackFont/Fonts`（`SubtitleController.cs:497-540`）：枚举 `FallbackFontPath` 下的 `.woff/.woff2/.ttf/.otf`，**总大小上限 20971520 字节（20 MB）**，超出即 `yield break` 并告警；路径未设时告警并**把 `EnableFallbackFont` 置为 false**（有副作用）。
- `GET /FallbackFont/Fonts/{name}`（`:548-579`）：按文件名返回；未找到返回 `Ok()`（注释：返回 204 会破坏 SubtitlesOctopus）。
- 官方文档佐证（<https://jellyfin.org/docs/general/administration/configuration>）：Fonts 段说明 fallback fonts 用于 Web 客户端渲染 ASS 字幕，**总大小限制 20 MB**，建议 woff2。

#### 1.4.7 字幕 API 与 URL 约定

**字幕流 URL 格式**（`MediaBrowser.Model/Dlna/StreamInfo.cs:1230-1285`）：
```
{base}/Videos/{itemId}/{mediaSourceId}/Subtitles/{index}/{startPositionTicks}/Stream.{format}
```
- 外部 `http(s)` 直链时 `IsExternalUrl = true`；否则追加 `?ApiKey=...`。
- `SubtitleStreamIndex` 只有在"`AlwaysBurnInSubtitleWhenTranscoding` 或 `Method != External` 且 `!= -1`"时才写进转码 URL（`StreamInfo.cs:955-959`）。

**`SubtitleController` 完整路由表**（`Jellyfin.Api/Controllers/SubtitleController.cs`）：

| 方法 | 路径 | 授权策略 | 关键参数 | 行为 |
|---|---|---|---|---|
| DELETE | `/Videos/{itemId}/Subtitles/{index}` | `RequiresElevation` | route | 删外部字幕文件；204 / 404（`:91-107`） |
| GET | `/Items/{itemId}/RemoteSearch/Subtitles/{language}` | `SubtitleManagement` | `isPerfectMatch` | 远程搜索字幕（`:118-134`） |
| POST | `/Items/{itemId}/RemoteSearch/Subtitles/{subtitleId}` | `SubtitleManagement` | route | 下载远程字幕并 `QueueRefresh`（`:144-171`） |
| GET | `/Providers/Subtitles/Subtitles/{subtitleId}` | — | route | 返回字幕流，`MimeTypes.GetMimeType("file." + format)`（`:179-189`） |
| GET | `/Videos/{routeItemId}/{routeMediaSourceId}/Subtitles/{routeIndex}/Stream.{routeFormat}` | — | `endPositionTicks`、`copyTimestamps=false`、`addVttTimeMap=false`、`startPositionTicks=0`；`format=="js"`→`"json"` | 主字幕端点（`:208-275`） |
| GET | `/Videos/{routeItemId}/{routeMediaSourceId}/Subtitles/{routeIndex}/{routeStartPositionTicks}/Stream.{routeFormat}` | — | 同上 | 转发到上一个（`:295-326`） |
| GET | `/Videos/{itemId}/{mediaSourceId}/Subtitles/{index}/subtitles.m3u8` | `[Authorize]` | **必需** `segmentLength` | 生成 HLS 字幕清单（`:338-410`） |
| POST | `/Videos/{itemId}/Subtitles` | `SubtitleManagement` | body `UploadSubtitleDto` | 上传字幕（Base64→FromBase64Transform→CryptoStream）（`:420-457`） |
| GET | `/FallbackFont/Fonts` | `[Authorize]` | — | 列出回退字体，20 MB 上限（`:497-540`） |
| GET | `/FallbackFont/Fonts/{name}` | `[Authorize]` | route | 下载回退字体（`:548-579`） |

细节：
- `format` 为空 → 直接 `PhysicalFile` 返回**原字幕文件**（`:236-248`）。
- `vtt` + `addVttTimeMap=true` 时注入 `X-TIMESTAMP-MAP=MPEGTS:900000,LOCAL:00:00:00.000`（`:250-263`）——**HLS 字幕对齐的关键**。
- 内部 `EncodeSubtitles`（`:470-490`）→ `ISubtitleEncoder.GetSubtitles(item, mediaSourceId, index, format, start, end ?? 0, copyTimestamps, None)`。
- HLS 字幕清单体（`:338-410`）：
```
#EXTM3U / #EXT-X-TARGETDURATION / #EXT-X-VERSION:3 / #EXT-X-MEDIA-SEQUENCE:0 / #EXT-X-PLAYLIST-TYPE:VOD
... 每段: stream.vtt?CopyTimestamps=true&AddVttTimeMap=true&StartPositionTicks=..&EndPositionTicks=..&ApiKey=..
```
- `GetSubtitlePlaylist` 用 `item.GetToken()` 作为 `ApiKey`（`:382`）。

**HLS 侧的字幕声明**（`Jellyfin.Api/Helpers/DynamicHlsHelper.cs:675-711`）：
```
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",...,URI="{mediaSourceId}/Subtitles/{index}/subtitles.m3u8?SegmentLength=30&ApiKey=..."
```
只有 `Method == Hls` 时才标 `DEFAULT`（`:682`）。

#### 1.4.8 相关开关汇总

| 开关 | 位置 | 默认 | 作用 |
|---|---|---|---|
| `EnableSubtitleExtraction` | `EncodingOptions.cs:61` | `true` | false 时内部字幕流不走 External 抽取路径（经 `MediaEncoder.CanExtractSubtitles:1373-1376` → `StreamBuilder.cs:1597`） |
| `SubtitleExtractionTimeoutMinutes` | `EncodingOptions.cs:62` | `30` | 抽取超时 |
| `FallbackFontPath` / `EnableFallbackFont` | `EncodingOptions.cs:81/86` | `""` / `false` | **仅客户端** ASS 渲染用，不进转码链 |
| `AllowSubtitleConversion` | — | — | **全树不存在此配置**；等价能力由 `MediaStream.SupportsSubtitleConversionTo` + `StreamBuilder.GetExternalSubtitleProfile(allowConversion)` 实现 |
| `RequirePerfectSubtitleMatch` | `LibraryOptions.cs:110` | `true` | **仅影响自动下载字幕**（`FFProbeVideoInfo.cs:561`、`SubtitleScheduledTask.cs:184`），与播放无关 |
| `AlwaysBurnInSubtitleWhenTranscoding` | `BaseEncodingJobOptions.cs:200`；DTO 入口 `PlaybackInfoDto.cs:89` | — | 转码时一律烧录；在 `StreamInfo.ToUrl:955`、`MediaInfoHelper.cs:315-318/337-340` 追加 `&alwaysBurnInSubtitleWhenTranscoding=true` |
| `SkipSubtitlesIfEmbeddedSubtitlesPresent` | `LibraryOptions.cs` | — | 自动下载时跳过 |
| `SkipSubtitlesIfAudioTrackMatches` | `LibraryOptions.cs:24`（ctor 默认 `true`） | `true` | 自动下载时跳过 |
| `AllowEmbeddedSubtitles` | `LibraryOptions.cs:23`（ctor 默认 `AllowAll`） | `AllowAll` | `EmbeddedSubtitleOptions` |
| `SubtitleStreamIndex`（用户记忆） | `UserItemData.SubtitleStreamIndex:88` | — | 记住用户上次选的字幕轨 |
| `VideosController` 默认字幕方式 | `VideosController.cs:407-408` | `Encode`（烧录） | 直连 stream 端点默认烧录 |
| `DynamicHlsController` 默认字幕方式 | `DynamicHlsController.cs:254` | `External` | HLS 默认外挂 |

---

### 1.5 进度与续播

#### 1.5.1 三个上报端点

全部在 `Jellyfin.Api/Controllers/PlaystateController.cs`（类声明 `[Route("")]`）：

| 方法 | 路径 | 请求体 | 行 |
|---|---|---|---|
| POST | `/Sessions/Playing` | `PlaybackStartInfo` | `:201` |
| POST | `/Sessions/Playing/Progress` | `PlaybackProgressInfo` | `:217` |
| POST | `/Sessions/Playing/Ping` | 查询参数 `playSessionId`（必需） | `:233` |
| POST | `/Sessions/Playing/Stopped` | `PlaybackStopInfo` | `:247` |

（另有 `GET /Sessions`、`POST /Sessions/{sessionId}/Playing`、`POST /Sessions/{sessionId}/Playing/{command}`、`POST /Sessions/Capabilities` 等远程控制端点，见 §2.9。）

实现：

```csharp
[HttpPost("Sessions/Playing")]                                    // :201
public async Task<ActionResult> ReportPlaybackStart([FromBody] PlaybackStartInfo playbackStartInfo)
{
    playbackStartInfo.PlayMethod = ValidatePlayMethod(playbackStartInfo.PlayMethod, playbackStartInfo.PlaySessionId);
    playbackStartInfo.SessionId  = await RequestHelpers.GetSessionId(_sessionManager, _userManager, HttpContext);
    await _sessionManager.OnPlaybackStart(playbackStartInfo);
    return NoContent();        // 204
}

[HttpPost("Sessions/Playing/Progress")]                           // :217
public async Task<ActionResult> ReportPlaybackProgress([FromBody] PlaybackProgressInfo playbackProgressInfo) { ... 同上 ... }

[HttpPost("Sessions/Playing/Ping")]                               // :233
public ActionResult PingPlaybackSession([FromQuery, Required] string playSessionId)
{
    _transcodeManager.PingTranscodingJob(playSessionId, null);    // ★ 续命转码任务
    return NoContent();
}

[HttpPost("Sessions/Playing/Stopped")]                            // :247
public async Task<ActionResult> ReportPlaybackStopped([FromBody] PlaybackStopInfo playbackStopInfo)
{
    if (!string.IsNullOrWhiteSpace(playbackStopInfo.PlaySessionId))
        await _transcodeManager.KillTranscodingJobs(User.GetDeviceId()!, playbackStopInfo.PlaySessionId, s => true);   // ★ 立刻杀转码
    playbackStopInfo.SessionId = await RequestHelpers.GetSessionId(...);
    await _sessionManager.OnPlaybackStopped(playbackStopInfo);
    return NoContent();
}
```

> **三条都返回 204 No Content**，且 **`SessionId` 由服务端从当前认证上下文推导并覆盖**，客户端传什么都会被改写。

**旧版（已废弃）端点**仍然存在（`[Obsolete]` + `ApiExplorerSettings(IgnoreApi = true)`）：
`POST /PlayingItems/{itemId}`（`:275`）、`POST /Users/{userId}/PlayingItems/{itemId}`（`:321`）、`POST /PlayingItems/{itemId}/Progress`（`:355`）、`POST /Users/{userId}/PlayingItems/{itemId}/Progress`（`:413`）、`DELETE /PlayingItems/{itemId}`（`:445`）、`DELETE /Users/{userId}/PlayingItems/{itemId}`（`:490`）。

已看/未看标记：`POST /UserPlayedItems/{itemId}`（`:72`）、`POST /Users/{userId}/PlayedItems/{itemId}`（`:120`）、`DELETE /UserPlayedItems/{itemId}`（`:139`）、`DELETE /Users/{userId}/PlayedItems/{itemId}`（`:185`）。

#### 1.5.2 `PlaybackProgressInfo` 逐字段

`MediaBrowser.Model/Session/PlaybackProgressInfo.cs`：

```csharp
public class PlaybackProgressInfo
{
    public bool CanSeek { get; set; }
    public BaseItemDto Item { get; set; }              // 可选，完整条目 DTO
    public Guid ItemId { get; set; }
    public string SessionId { get; set; }              // 服务端会覆盖
    public string MediaSourceId { get; set; }
    public int? AudioStreamIndex { get; set; }
    public int? SubtitleStreamIndex { get; set; }
    public bool IsPaused { get; set; }
    public bool IsMuted { get; set; }
    public long? PositionTicks { get; set; }           // ★ 播放位置（关键）
    public long? PlaybackStartTimeTicks { get; set; }
    public int? VolumeLevel { get; set; }
    public int? Brightness { get; set; }
    public string AspectRatio { get; set; }
    public PlayMethod PlayMethod { get; set; }         // 0=Transcode 1=DirectStream 2=DirectPlay
    public string LiveStreamId { get; set; }
    public string PlaySessionId { get; set; }          // ★ 与转码任务关联的键
    public RepeatMode RepeatMode { get; set; }
    public PlaybackOrder PlaybackOrder { get; set; }
    public QueueItem[] NowPlayingQueue { get; set; }
    public string PlaylistItemId { get; set; }
}
```

`PlaybackStartInfo` 就是**空继承**：`public class PlaybackStartInfo : PlaybackProgressInfo { }`（无新增字段）。

`PlaybackStopInfo`（`MediaBrowser.Model/Session/PlaybackStopInfo.cs`）：

```csharp
public class PlaybackStopInfo
{
    public BaseItemDto Item { get; set; }
    public Guid ItemId { get; set; }
    public string SessionId { get; set; }
    public string MediaSourceId { get; set; }
    public long? PositionTicks { get; set; }           // ★ 停止时的位置
    public string LiveStreamId { get; set; }
    public string PlaySessionId { get; set; }
    public bool Failed { get; set; }                   // ★ true 则不计已看
    public string NextMediaType { get; set; }
    public string PlaylistItemId { get; set; }
    public QueueItem[] NowPlayingQueue { get; set; }   // ★ 用于"连播"
}
```

#### 1.5.3 已看状态与续播点是如何落库的

`Emby.Server.Implementations/Session/SessionManager.cs:1165-1192`：

```csharp
private bool OnPlaybackStopped(User user, BaseItem item, long? positionTicks, bool playbackFailed)
{
    if (playbackFailed) return false;                              // ★ Failed=true 直接不记账

    var data = _userDataManager.GetUserData(user, item);
    bool playedToCompletion;
    if (positionTicks.HasValue)
    {
        playedToCompletion = _userDataManager.UpdatePlayState(item, data, positionTicks.Value);
    }
    else
    {
        // If the client isn't able to report this, then we'll just have to make an assumption
        data.PlayCount++;
        data.Played = item.SupportsPlayedStatus;
        data.PlaybackPositionTicks = 0;
        playedToCompletion = true;
    }

    _userDataManager.SaveUserData(user, item, data, UserDataSaveReason.PlaybackFinished, CancellationToken.None);

    // ★ 多版本联动：一个版本看完 -> 所有替代版本也标记已看并清空续播点
    if (data.Played == true && item is Video playedVideo)
        playedVideo.PropagatePlayedState(user, true);

    return playedToCompletion;
}
```

**核心阈值逻辑**在 `Emby.Server.Implementations/Library/UserDataManager.cs:439-509` 的 `UpdatePlayState`：

```csharp
public bool UpdatePlayState(BaseItem item, UserItemData data, long? reportedPositionTicks)
{
    var playedToCompletion = false;
    var runtimeTicks = item.GetRunTimeTicksForPlayState();
    var positionTicks = reportedPositionTicks ?? runtimeTicks;
    var hasRuntime = runtimeTicks > 0;

    if (positionTicks > 0 && hasRuntime && item is not AudioBook && item is not Book)
    {
        var pctIn = decimal.Divide(positionTicks, runtimeTicks) * 100;

        if (pctIn < _config.Configuration.MinResumePct)
        {
            positionTicks = 0;                       // 开头一点点 -> 不记续播点
        }
        else if (pctIn > _config.Configuration.MaxResumePct || positionTicks >= (runtimeTicks - TimeSpan.TicksPerSecond))
        {
            positionTicks = 0;
            data.Played = playedToCompletion = true; // 接近结尾 -> 标记已看，清续播点
        }
        else
        {
            var durationSeconds = TimeSpan.FromTicks(runtimeTicks).TotalSeconds;
            if (durationSeconds < _config.Configuration.MinResumeDurationSeconds)
            {
                positionTicks = 0;
                data.Played = playedToCompletion = true;   // 太短的内容 -> 直接算看完
            }
        }
    }
    else if (positionTicks > 0 && hasRuntime && item is AudioBook)
    {
        var playbackPositionInMinutes = TimeSpan.FromTicks(positionTicks).TotalMinutes;
        var remainingTimeInMinutes   = TimeSpan.FromTicks(runtimeTicks - positionTicks).TotalMinutes;

        if (playbackPositionInMinutes < _config.Configuration.MinAudiobookResume)      positionTicks = 0;
        else if (remainingTimeInMinutes < _config.Configuration.MaxAudiobookResume || positionTicks >= runtimeTicks)
        { positionTicks = 0; data.Played = playedToCompletion = true; }
    }
    else if (!hasRuntime)
    {
        data.Played = playedToCompletion = true;     // 不知道时长 -> 假设看完
        positionTicks = 0;
    }

    if (!item.SupportsPlayedStatus)        positionTicks = 0, data.Played = false;
    if (!item.SupportsPositionTicksResume) positionTicks = 0;
    ...
}
```

**阈值默认值**（`MediaBrowser.Model/Configuration/ServerConfiguration.cs`）：

| 配置项 | 默认值 | 行 | 含义 |
|---|---|---|---|
| `MinResumePct` | `5` | `:133` | 进度 < 5% 不记续播点 |
| `MaxResumePct` | `90` | `:139` | 进度 > 90% 视为看完 |
| `MinResumeDurationSeconds` | `300` | `:145` | 时长 < 5 分钟的内容直接算看完 |
| `MinAudiobookResume` | `5` | `:151` | 有声书：已听 < 5 分钟不记 |
| `MaxAudiobookResume` | `5` | `:157` | 有声书：剩余 < 5 分钟算听完 |

> **对自研实现的启示**：这套 5% / 90% / 300 秒的三段式判定非常值得照搬，它天然处理了"片头 logo 点了就走"和"片尾字幕没看完"两种情况。注意有条**容易漏掉**的规则：`MinResumeDurationSeconds` 意味着**短片（<5 分钟）永远不进"继续观看"**，只会被标记已看。

**`Video.SupportsPositionTicksResume`**（`MediaBrowser.Controller/Entities/Video.cs:63-88`）：`Sample` / `ThemeVideo` / `Trailer` 返回 `false`（即这些内容不记续播点）。

**多设备同步**：进度是**按 user + item** 存的（`UserItemData.PlaybackPositionTicks`），与设备无关。任何设备上报 `Stopped`/`Progress` 都会更新同一份数据，其他设备通过 `GET /Users/{userId}/Items/Resume` 或 `GET /Sessions` 读到最新值。**服务端没有任何"设备间实时推送"机制**——同步是"下次查询时看到"（最终一致），不是实时。

**继续观看查询**：`GET /Users/{userId}/Items/Resume`，关键参数 `MediaType`、`Limit`、`Fields`、`EnableImages` 等（完整参数表见 §2.9）。

---

### 1.6 Trickplay（进度预览小图）

#### 1.6.1 配置项与默认值

`MediaBrowser.Model/Configuration/TrickplayOptions.cs`（逐字默认值）：

```csharp
public bool EnableHwAcceleration { get; set; } = false;      // 是否用硬件加速
public bool EnableHwEncoding     { get; set; } = false;      // 是否用硬件加速 MJPEG 编码
public bool EnableKeyFrameOnlyExtraction { get; set; } = false;  // 只抽关键帧（快，但并非所有解码器/文件都兼容）
public TrickplayScanBehavior ScanBehavior { get; set; } = TrickplayScanBehavior.NonBlocking;
public ProcessPriorityClass ProcessPriority { get; set; } = ProcessPriorityClass.BelowNormal;
public int   Interval         { get; set; } = 10000;         // ★ 每张图的间隔（毫秒）
public int[] WidthResolutions { get; set; } = new[] { 320 }; // ★ 目标宽度（px）
public int   TileWidth        { get; set; } = 10;            // 每行多少张
public int   TileHeight       { get; set; } = 10;            // 每列多少张
public int   Qscale           { get; set; } = 4;             // ffmpeg 输出质量等级（1-31，1 最好）
public int   JpegQuality      { get; set; } = 90;            // tile 的 JPEG 质量
public int   ProcessThreads   { get; set; } = 1;
```

`TrickplayScanBehavior`（`TrickplayScanBehavior.cs`）：
```csharp
Blocking,      // Starts generation, only return once complete.
NonBlocking    // Start generation, return immediately.
```

`TrickplayInfoDto`（`MediaBrowser.Model/Dto/TrickplayInfoDto.cs`）——**客户端拿到的元数据**：

| 字段 | 含义 |
|---|---|
| `Width` / `Height` | 单张缩略图宽高 |
| `TileWidth` / `TileHeight` | 每行 / 每列缩略图数 |
| `ThumbnailCount` | 非黑缩略图总数 |
| `Interval` | 毫秒间隔 |
| `Bandwidth` | 峰值带宽（bps） |

DB 实体 `TrickplayInfo` 与 `TrickplayInfoDto` 字段一一对应（`src/Jellyfin.Database/Jellyfin.Database.Implementations/Entities/TrickplayInfo.cs`）。

> **官方文档缺口（已核实）**：jellyfin.org **没有任何 trickplay 设置页**。`/docs/general/post-install/transcoding/trickplay` 与 `/docs/general/server/metadata/trickplay` 均返回 **404**（各尝试 2 次）。官方文档中唯一提及在 `/docs/general/administration/backup-and-restore`（备份项 "Trickplay. All Trickplay data that is stored not alongside media."）。上述参数名与默认值来自**官方 OpenAPI 规格**（<https://api.jellyfin.org/openapi/jellyfin-openapi-stable.json>）与**服务端源码**。

#### 1.6.2 目录结构（**注意这个"宽度 - 瓦片"命名**）

`Emby.Server.Implementations/Library/PathManager.cs:96-104`（`GetTrickplayDirectory`）：
```csharp
public string GetTrickplayDirectory(BaseItem item, bool saveWithMedia = false)
{
    var id = item.Id.ToString("D", CultureInfo.InvariantCulture).AsSpan();

    return saveWithMedia
        ? Path.Combine(item.ContainingFolderPath, Path.ChangeExtension(Path.GetFileName(item.Path), ".trickplay"))
        : Path.Join(_config.ApplicationPaths.TrickplayPath, id[..2], id);   // <DataPath>/trickplay/<前2位>/<完整id>
}
```

`Jellyfin.Server.Implementations/Trickplay/TrickplayManager.cs:836-847` 再套一层**子目录**：
```csharp
public string GetTrickplayDirectory(BaseItem item, int tileWidth, int tileHeight, int width, bool saveWithMedia = false)
{
    var path = _pathManager.GetTrickplayDirectory(item, saveWithMedia);
    var subdirectory = string.Format(CultureInfo.InvariantCulture, "{0} - {1}x{2}", width, tileWidth, tileHeight);
    return Path.Combine(path, subdirectory);
}

[GeneratedRegex(@"^(\d+) - (\d+)x(\d+)$")]
private static partial Regex TrickplaySubdirRegex();      // :849-850，解析时用
```

**最终布局**：
```
<DataPath>/trickplay/<id前2位>/<完整id>/<width> - <tileW>x<tileH>/0.jpg
                                                          1.jpg
                                                          2.jpg
                                                          ...
```
例：宽 320、瓦片 10x10 → `.../320 - 10x10/0.jpg`。
开启"随媒体保存"时改为 `<媒体所在目录>/<媒体文件名>.trickplay/320 - 10x10/*.jpg`（`LibraryOptions.SaveTrickplayWithMedia`，**默认 `false`**）。

瓦片文件名 = 纯索引：`index + ".jpg"`（`GetTrickplayTilePathAsync`，`TrickplayManager.cs:751-761`）。**图片格式固定为 JPEG**（`_trickplayImgExtensions = [".jpg"]`，`TrickplayManager.cs:45`）。

#### 1.6.3 生成：两阶段（抽帧 → 拼瓦片）

**阶段 1：用 FFmpeg 抽单帧 JPG**

`MediaBrowser.MediaEncoding/Encoder/MediaEncoder.cs`（`ExtractImages` 路径）：
```csharp
// :896  目标帧率 = 1 / interval
var baseRequest = new BaseEncodingJobOptions { MaxWidth = maxWidth, MaxFramerate = (float)(1.0 / interval.TotalSeconds) };
var jobState = new EncodingJobInfo(TranscodingJobType.Progressive)
{
    IsVideoRequest = true,      // must be true for InputVideoHwaccelArgs to return non-empty value
    MediaSource = mediaSource,
    VideoStream = imageStream,
    BaseRequest = baseRequest,  // GetVideoProcessingFilterParam errors if null
    MediaPath = inputFile,
    OutputVideoCodec = "mjpeg"
};
var vidEncoder = enableHwEncoding ? encodingHelper.GetVideoEncoder(jobState, options) : jobState.OutputVideoCodec;  // "mjpeg"

var inputArg  = encodingHelper.GetInputArgument(jobState, options, container).Trim();
if (!allowHwAccel) inputArg = "-threads " + threads + " " + inputArg;

// VideoToolbox 支持低优先级解码，适合 trickplay
if (options.HardwareAccelerationType == HardwareAccelerationType.videotoolbox && _isLowPriorityHwDecodeSupported)
    inputArg = "-hwaccel_flags +low_priority " + inputArg;

// Force the video stream, otherwise ffmpeg may pick a cover image.
inputArg += " -map 0:" + streamIndex;

var filterParam = encodingHelper.GetVideoProcessingFilterParam(jobState, options, vidEncoder).Trim();

// 非"只抽关键帧"模式：归一化容器的非法 PTS
if (!enableKeyFrameOnlyExtraction)
{
    var fpsFilterIndex = filterParam.IndexOf("fps=", StringComparison.Ordinal);
    if (fpsFilterIndex >= 0)
    {
        var inputFrameRate = (imageStream.ReferenceFrameRate is > 0) ? imageStream.ReferenceFrameRate.Value : 30;
        var setPtsFilter = string.Create(CultureInfo.InvariantCulture, $"setpts=N/{inputFrameRate:F3}/TB,");
        ...   // 插到 fps= 之前
    }
}
```

**最终命令**（`MediaEncoder.cs:1029-1041`）：
```csharp
var args = string.Format(
    CultureInfo.InvariantCulture,
    "-loglevel error {0} -an -sn {1} -threads {2} -c:v {3} {4}{5}{6}-f {7} \"{8}\"",
    inputArg,                 // {0}
    filterParam,              // {1}
    outputThreads.GetValueOrDefault(_threads),   // {2}
    vidEncoder,               // {3}  默认 "mjpeg"，开了硬编则是 mjpeg_qsv/mjpeg_vaapi/mjpeg_videotoolbox/mjpeg_rkmpp
    encoderQualityOption + encoderQuality + " ", // {4}
    vidEncoder.Contains("videotoolbox") ? "-allow_sw 1 " : string.Empty,   // {5} 为某些 Intel Mac 留软回退
    EncodingHelper.GetVideoSyncOption("0", EncoderVersion).Trim() + " ",   // {6} 直通时间戳
    "image2",                 // {7}
    outputPath);              // {8}  <temp>/<guid>/%08d.jpg
```

**质量参数按编码器换算**（`MediaEncoder.cs:997-1021`）——qscale 与 jpeg quality 语义相反，必须换算：

```csharp
// ffmpeg qscale: 1(best)-31(worst)；jpeg quality: 0(worst)-100(best)
var encoderQuality = Math.Clamp(qualityScale ?? 4, 1, 31);
var encoderQualityOption = "-qscale:v ";

if (vidEncoder.Contains("vaapi") || vidEncoder.Contains("qsv"))
{
    // vaapi 与 qsv 的 mjpeg 编码器用 jpeg quality 作为输入，而非 ffmpeg 定义的 qscale
    encoderQuality = 100 - ((encoderQuality - 1) * (100 / 30));
    encoderQualityOption = "-global_quality:v ";
}
if (vidEncoder.Contains("videotoolbox"))
    // videotoolbox 的 mjpeg 用按 QP2LAMBDA(118) 缩放的 jpeg quality
    encoderQuality = 118 - ((encoderQuality - 1) * (118 / 30));
if (vidEncoder.Contains("rkmpp"))
{
    // rkmpp 的 mjpeg 用 jpeg quality（最大 99，不是 100）
    encoderQuality = 99 - ((encoderQuality - 1) * (99 / 30));
    encoderQualityOption = "-qp_init:v ";
}
```

抽帧输出到临时目录 `Path.Combine(ApplicationPaths.TempDirectory, Guid.NewGuid().ToString("N"))`，文件名 `%08d.jpg`。

**阶段 2：拼瓦片**（`TrickplayManager.cs:477-530`）
```csharp
var images = _fileSystem.GetFiles(imgTempDir, _trickplayImgExtensions, false, false)
    .Select(i => i.FullName).OrderBy(i => i).ToList();      // :503-506 按文件名排序（%08d 保证顺序）
var trickplayInfo = CreateTiles(images, actualWidth, options, outputDir.FullName);   // :511
if (trickplayInfo is not null) { trickplayInfo.ItemId = video.Id; await SaveTrickplayInfo(trickplayInfo); }
else throw new InvalidOperationException("Null trickplay tiles info from CreateTiles.");
// 失败时确保不残留：outputDir.Delete(true)   :524
```

**已有瓦片的导入/识别**（`:170-270` `GetTrickplayResolutions` 附近）——支持用户手放的瓦片：
```csharp
var tileWidth  = int.Parse(match.Groups[2].Value, ...);   // 从 "320 - 10x10" 目录名解析
var tileHeight = int.Parse(match.Groups[3].Value, ...);
var tiles = subdir.GetFiles("*.jpg")...;
// 编码器会把最后一张瓦片补齐到满 TileWidth*TileHeight 网格，
// 所以真实缩略图数量无法从瓦片尺寸读出。改为：由瓦片数与单片容量界定上下界，
// 再选一个与之相符的 interval
var thumbsPerTile = tileWidth * tileHeight;
var maxThumbs = tiles.Length * thumbsPerTile;
var minThumbs = tiles.Length > 1 ? ((tiles.Length - 1) * thumbsPerTile) + 1 : 1;
...
var firstSize = _imageEncoder.GetImageSize(tiles[0].FullName);
var thumbPxH  = Math.Max(1, (int)Math.Ceiling((double)firstSize.Height / tileHeight));
...
var bitrate = (int)Math.Ceiling((decimal)tile.Length * 8 / tileWidth / tileHeight / (interval / 1000m));
```

> **对自研实现的启示（可直接照搬）**：
> ① 抽帧用**固定帧率 + 固定宽度**（`fps=1/interval` + `scale=W:-1`），文件名用 `%08d` 保证字典序=时间序；
> ② **拼瓦片用 10×10 的雪碧图**（sprite sheet），客户端只需一次请求 + 一次 `background-position` 计算，比每 10 秒一张单独请求高效得多；
> ③ 非关键帧模式必须插 `setpts=N/<fps>/TB` 归一化 PTS，否则容器时间戳异常会导致抽帧位置错乱。

#### 1.6.4 访问方式

`Jellyfin.Api/Controllers/TrickplayController.cs`（`[Route("")]`）：

| 方法 | 路径 | 行 |
|---|---|---|
| GET | `/Videos/{itemId}/Trickplay/{width}/tiles.m3u8` | `:50` |
| GET | `/Videos/{itemId}/Trickplay/{width}/{index}.jpg` | `:79` |

**HLS 型 manifest**（`TrickplayManager.GetHlsPlaylist`，`TrickplayManager.cs:765+`）——用 HLS 的 `EXT-X-TILES`（图像瓦片）规范把"时间 → 瓦片坐标"编码进清单：
```csharp
const string urlFormat = "{0}.jpg?MediaSourceId={1}&ApiKey={2}";
var resolution = $"{trickplayInfo.Width}x{trickplayInfo.Height}";
// 生成带 EXT-X-TILES:RESOLUTION=..,LAYOUT=..,DURATION=.. 的 m3u8
```
这样客户端只要解析 m3u8 就能知道"第 N 秒对应哪张瓦片的哪个格子"，**无需自己记 interval**。这是个相当优雅的设计。

**相关库选项**（`MediaBrowser.Model/Configuration/LibraryOptions.cs`）：
`EnableTrickplayImageExtraction`、`ExtractTrickplayImagesDuringLibraryScan`、`SaveTrickplayWithMedia`（**默认 `false`**，ctor 中显式赋值）。

---

### 1.7 Intro/Outro 跳过（媒体片段）

#### 1.7.1 **重要更正：核心不含任何片头/片尾检测算法**

这是网上最容易以讹传讹的一点。核实结论：

- 全仓库 grep `IMediaSegmentProvider` 的实现类 → **只有接口本身与 `MediaSegmentManager`**，**没有任何内置 provider**。
- 全仓库 grep `"Intro"` / `IsIntro` 等非本地化字符串 → **零命中**。

也就是说：**Jellyfin 核心只提供了"媒体片段"的数据模型 + 插件接口 + 存储 + API；片头识别的算法完全由第三方插件提供**（社区实现如 `jellyfin-plugin-intro-skipper` / "Intro Skipper" 系列，以及 `jellyfin-plugin-media-segments`）。

#### 1.7.2 数据模型

`src/Jellyfin.Database/Jellyfin.Database.Implementations/Enums/MediaSegmentType.cs`（**显式赋值**）：

```csharp
public enum MediaSegmentType
{
    Unknown = 0,      // Default media type or custom one.
    Commercial = 1,   // Commercial.
    Preview = 2,      // Preview.
    Recap = 3,        // Recap.
    Outro = 4,        // Outro.
    Intro = 5         // Intro.
}
```

> **注意 `Intro = 5`、`Outro = 4`** —— 数值不是按名字顺序，写客户端时别用下标猜。

DB 实体 `src/Jellyfin.Database/Jellyfin.Database.Implementations/Entities/MediaSegment.cs`：

| 列 | 类型 | 说明 |
|---|---|---|
| `Id` | `Guid` | `[DatabaseGenerated(Identity)]` |
| `ItemId` | `Guid` | 关联条目 |
| `Type` | `MediaSegmentType` | 片段类型 |
| `StartTicks` | `long` | 起点 |
| `EndTicks` | `long` | 终点 |
| `SegmentProviderId` | `string`（`required`） | 产出该记录的 provider id |

API DTO `MediaBrowser.Model/MediaSegments/MediaSegmentDto.cs`：`Id`、`ItemId`、`Type`（`[DefaultValue(MediaSegmentType.Unknown)]`）、`StartTicks`、`EndTicks`。

生成请求 DTO `MediaBrowser.Model/MediaSegments/MediaSegmentGenerationRequest.cs`：`ItemId` + `ExistingSegments`。

#### 1.7.3 provider 接口与调度

接口 `MediaBrowser.Controller/MediaSegments/IMediaSegmentProvider.cs`（另有 `IMediaSegmentManager`）。

`Jellyfin.Server.Implementations/MediaSegments/MediaSegmentManager.cs`：

```csharp
public MediaSegmentManager(..., IEnumerable<IMediaSegmentProvider> segmentProviders)
{
    // provider 按 IHasOrder.Order 排序
    _segmentProviders = segmentProviders.OrderBy(i => i is IHasOrder hasOrder ? hasOrder.Order : 0).ToArray();
}

public async Task RunSegmentPluginProviders(BaseItem baseItem, LibraryOptions libraryOptions, bool forceOverwrite, CancellationToken cancellationToken)
{
    // 1) 过滤：库选项 DisabledMediaSegmentProviders 里禁用的跳过
    // 2) 排序：按 libraryOptions.MediaSegmentProviderOrder 的顺序（不在列表里 -> int.MaxValue）
    var providers = _segmentProviders
        .Where(e => !libraryOptions.DisabledMediaSegmentProviders.Contains(e.Name, StringComparer.OrdinalIgnoreCase))
        .OrderBy(i => { var index = libraryOptions.MediaSegmentProviderOrder.IndexOf(i.Name);
                        return index == -1 ? int.MaxValue : index; })
        .ToList();

    if (providers.Count == 0) { _logger.LogDebug("Skipping media segment extraction as no providers are enabled for {MediaPath}", baseItem.Path); return; }

    // forceOverwrite -> 先删该条目的全部已有片段
    if (forceOverwrite) await db.MediaSegments.Where(e => e.ItemId.Equals(baseItem.Id)).ExecuteDeleteAsync(cancellationToken);

    foreach (var provider in providers)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (!await provider.Supports(baseItem)) { ... continue; }

        // 非覆盖模式：只取该 provider 自己产出的片段（按 SegmentProviderId 过滤）
        IQueryable<MediaSegment> existingSegments = forceOverwrite
            ? Array.Empty<MediaSegment>().AsQueryable()
            : db.MediaSegments.Where(e => e.ItemId.Equals(baseItem.Id) && e.SegmentProviderId == GetProviderId(provider.Name));

        var requestItem = new MediaSegmentGenerationRequest { ItemId = baseItem.Id, ExistingSegments = existingSegments.Select(e => Map(e)).ToArray() };
        try
        {
            var segments = await provider.GetMediaSegments(requestItem, cancellationToken);
            // 非覆盖模式：若结果与已有片段完全一致（Start/End 都相同），则不写库（省 IO）
            if (!forceOverwrite) { /* 逐项比较 StartTicks/EndTicks，全同则跳过 */ }
            ...
        }
    }
}
```

**调度任务**：`Emby.Server.Implementations/ScheduledTasks/Tasks/MediaSegmentExtractionTask.cs`（`Key = "TaskExtractMediaSegments"`，`Name = TaskExtractMediaSegments`，`Category = TasksLibraryCategory`），扫描的条目类型：
```csharp
private static readonly BaseItemKind[] _itemTypes = [BaseItemKind.Episode, BaseItemKind.Movie, BaseItemKind.Audio, BaseItemKind.AudioBook];
```

**库级配置**（`LibraryOptions.cs:17-18, 100-102`）：`DisabledMediaSegmentProviders`、`MediaSegmentProviderOrder`（ctor 里都初始化为空数组）。

#### 1.7.4 API

`Jellyfin.Api/Controllers/MediaSegmentsController.cs`：

| 方法 | 路径 | 参数 |
|---|---|---|
| GET | `/{itemId}` | route `itemId`、查询 `includeSegmentTypes`（可选，过滤片段类型） |

```csharp
[HttpGet("{itemId}")]                                    // :46
public async Task<ActionResult<QueryResult<MediaSegmentDto>>> GetItemSegments(
    [FromRoute, Required] Guid itemId,
    [FromQuery] MediaSegmentType[]? includeSegmentTypes, ...)
```
返回 `QueryResult<MediaSegmentDto>`（含 `Items`、`TotalRecordCount`）。
引用：<https://github.com/jellyfin/jellyfin/blob/master/Jellyfin.Api/Controllers/MediaSegmentsController.cs#L46>

> 客户端拿这个接口的 `StartTicks`/`EndTicks` 自行实现"跳过片头"按钮，**服务端不做跳过动作**。

#### 1.7.5 章节（`ChapterInfo`）—— 另一条可用于跳过的路径

`MediaBrowser.Model/Entities/ChapterInfo.cs`（DTO）：

| 字段 | 类型 | 行 |
|---|---|---|
| `StartPositionTicks` | `long` | `:16` |
| `Name` | `string?` | `:22` |
| `ImagePath` | `string?` | `:28` |
| `ImageDateModified` | `DateTime` | `:30` |
| `ImageTag` | `string?` | `:32` |

DB 实体 `src/Jellyfin.Database/.../Entities/Chapter.cs`：`ItemId`、`Item`、`ChapterIndex`、`StartPositionTicks`、`Name`、`ImagePath`、`ImageDateModified`。
**注意：DB 表里没有 `ImageTag` 列**，它是运行时算出来的（`ChapterRepository.cs:110-126`）：
```csharp
if (!string.IsNullOrEmpty(chapterInfo.ImagePath))
    chapterEntity.ImageTag = _imageProcessor.GetImageCacheTag(baseItemPath, chapterEntity.ImageDateModified);
```

**章节只有在请求 `fields` 含 `Chapters` 时才返回**（`Emby.Server.Implementations/Dto/DtoService.cs:1462-1465`）：
```csharp
if (options.ContainsField(ItemFields.Chapters))
    dto.Chapters = _chapterManager.GetChapters(item.Id).ToList();
```
所以：`GET /Items?fields=Chapters`。

章节写入是**先删后全量重插**，`ChapterIndex` 按数组下标赋值（`Jellyfin.Server.Implementations/Item/ChapterRepository.cs:70-83`）。

> **实践提示**：很多第三方客户端就是靠"章节名包含 `Intro`/`Opening`/`Outro`/`Ending"这类关键词 + 章节时间点来实现跳过的，不需要 media segments 插件。Jellyfin 核心不解析章节名语义，只是把 `Name` 原样给客户端。

---

### 1.8 对"自己写桌面播放器（Python + PySide6 + FFmpeg）"的启示

下面把 Jellyfin 的设计逐条映射到自研播放器，标注**值得照搬 / 可以简化 / 不要照搬**。

| # | Jellyfin 的做法 | 出处 | 自研建议 |
|---|---|---|---|
| 1 | **能力协商两段式**：客户端先 `PlaybackInfo` 上报 `DeviceProfile`，服务端返回"怎么播 + 转码 URL" | `PlaybackInfoDto` / `StreamBuilder` | **强烈建议照搬**。自研播放器 = 客户端 + 本地服务端两个角色，仍应把"文件 ↔ 播放器能播什么"的匹配做成一个纯函数（见下方伪代码），不要写成一堆 if |
| 2 | **`DeviceProfile` 用逗号列表描述能力**（容器/视频编码/音频编码） | `DirectPlayProfile.cs` | **照搬**。极简且够用，配置可序列化成 JSON 存盘 |
| 3 | **`PlayMethod` 三态 + `TranscodeReason` 位掩码** | `PlayMethod.cs` / `TranscodeReason.cs` | **强烈建议照搬**。位掩码让"为什么转码"可解释、可打日志、可展示给用户，调试成本大幅降低 |
| 4 | **`DirectStreamReasons` 定义"改封装能解决 vs 必须真转码"** | `StreamBuilder.cs:27` | **照搬这个分类思想**。它把"remux"和"encode"分清，是省 CPU 的关键 |
| 5 | **多版本（media source）5 级排序** | `StreamBuilder.cs:262-305` | **照搬**。直接给你"本地直连 > 远程直连 > remux > 转码，同条件下挑最贴带宽"的排序 |
| 6 | **候选音轨集合逐级放宽**（客户端指定 → 用户语言 → default） | `StreamBuilder.cs:672-708` | **照搬**。多语言多音轨场景下显著减少手动切轨 |
| 7 | **默认字幕轨按 `Score` 选 + 并列时选"最省事"的** | `StreamBuilder.cs:551-592` | **照搬思路**，可简化：优先外挂/可直接渲染的，其次需抽取的，最后需烧录的 |
| 8 | **`-ss` 对 remux 场景 +0.5s 命中关键帧；seek 上限夹在 `时长-5s`** | `EncodingHelper.cs:3054-3085` | **必抄**。这两条能直接消掉"跳转后花屏/黑屏/卡死"类 bug |
| 9 | **`-copyts -avoid_negative_ts disabled` + `-start_at_zero`** | `DynamicHlsController.cs:1642`、`:1856`、`:1881-1888` | **照抄**。字幕/音画同步全靠时间戳不被打乱 |
| 10 | **HLS：`-hls_time N` + `-force_key_frames "expr:gte(t,n_forced*N)"` + `-g/-keyint_min`** | `EncodingHelper.cs:2012-2096` | **照抄**。关键帧必须对齐分片，否则 seek 会跨片；硬件编码器不能 `-force_key_frames` 时退回 GOP 限制 |
| 11 | **分段就绪判定：文件存在 且（转码结束 或 下一分片已存在）** | `DynamicHlsController.cs:1944-1970` | **照抄**。这是"边转边播不卡顿"的核心，比"等固定时间"靠谱得多 |
| 12 | **fMP4 需要 `movflags=+frag_discont+skip_sidx`** | `DynamicHlsController.cs:1614-1617` | **照抄**（若用 fMP4）。注释解释了原因：把音频首包延迟写进 TFDT、避免重写 open-GOP 边界 PTS |
| 13 | **边转边播 mp4 用 `-movflags frag_keyframe+empty_moov+delay_moov`** | `EncodingHelper.cs:7690` | **必抄**，这是"边下边播 mp4"的标准姿势 |
| 14 | **码率参数按编码器分支**（各家 `maxrate`/`bufsize`/`rc_mode` 语义不同） | `EncodingHelper.cs:1634-1731` | **照抄那张表**。尤其：libx264 用 `-maxrate` 而非 `-b:v`、QSV 有 `-mbbrc 1` 与 `maxrate=bitrate+1`、VideoToolbox 不能给 `maxrate/bufsize` |
| 15 | **编码器名 = `<codec>_<hwaccel>`，不支持则回退 `libx264`** | `EncodingHelper.cs:212-243` | **照搬**。用 `ffmpeg -encoders` 探测可用性后套同一命名规则 |
| 16 | **硬件解码需 `-noautoscale` 防被偷缩放** | `EncodingHelper.cs:1345-1350` | **照抄**。这是很隐蔽的坑 |
| 17 | **按编码器换算质量参数**（qscale↔jpeg quality↔QP2LAMBDA） | `MediaEncoder.cs:997-1021` | 若做缩略图/预览图，**照抄换算表** |
| 18 | **字幕：能用外挂就不烧录；TS/MP4 不能 Embed，只有 MKV 能** | `StreamBuilder.cs:1545-1561` | **照搬决策树**。烧录是最贵、最不可逆的操作，应作为最后手段 |
| 19 | **字体 dump 到磁盘 + `subtitles=:fontsdir=` 让 libass 加载**（绝不 mux 进容器） | `AttachmentExtractor.cs` + `EncodingHelper.cs:1941` | **强烈建议照搬**。转码进程无状态、可复用、可缓存 |
| 20 | **只用 `subtitles=`（libass），不搞 `force_style`/`ass=`** | `EncodingHelper.cs:1924-1986` | **照搬**。少一层自定义就少一堆兼容问题 |
| 21 | **字符编码：用 UtfUnknown 探测，UTF-8 短路** | `SubtitleEncoder.cs:167-201` | **照搬**。中文/日文外挂字幕几乎必然遇到 GBK/Big5/Shift-JIS |
| 22 | **文本字幕格式转换纯托管（不用 FFmpeg）** | `SubtitleEncoder.cs:77-95` | **可以简化**：Python 侧自己写 srt↔vtt↔ass 转换（`pysubs2` 之类）比调 FFmpeg 快得多、也更好控 |
| 23 | **字幕延迟（offset）是客户端行为** | 全树零命中 | **必须自己实现**。建议做成"内存中偏移量 + 渲染时加减"，不要改动原文件 |
| 24 | **进度三段式阈值（5% / 90% / 300 秒）** | `ServerConfiguration.cs:133-157` + `UserDataManager.cs:439-509` | **必抄**。含"短片直接算看完"这条容易漏的规则 |
| 25 | **`Failed=true` 的上报不记账** | `SessionManager.cs:1167-1170` | **照抄**。区分"用户中途退出"与"播放失败" |
| 26 | **播放位置按 user+item 存，与设备无关（最终一致，非实时）** | `UserItemData.PlaybackPositionTicks` | **照搬**。不要做设备级进度表，会带来大量冲突合并问题 |
| 27 | **心跳 `Ping` 续命转码任务；`Stopped` 立刻杀转码** | `PlaystateController.cs:233-259` | **照搬**。这是防止 ffmpeg 进程泄漏的关键（比超时杀进程更及时） |
| 28 | **限速用"向 stdin 写按键暂停"而非杀进程** | `TranscodingThrottler.cs` | **照搬**（若做转码服务）。杀掉再重启会丢失缓冲区、重新 seek，代价高 |
| 29 | **Trickplay：抽帧 `fps=1/interval` + `scale=W:-1` + `%08d.jpg`，再拼 10×10 雪碧图** | `MediaEncoder.cs:1024-1041` + `TrickplayManager.cs:511` | **强烈建议照搬**。雪碧图 + HLS `EXT-X-TILES` 让客户端零计算量 |
| 30 | **片头识别交给插件，核心只存"片段"数据模型** | `IMediaSegmentProvider` 无内置实现 | **照搬这种分层**。别把识别算法硬编码进播放器 |
| 31 | **章节只当"带名字的时间点"，核心不解析语义** | `ChapterInfo` + `DtoService.cs:1462` | **照搬**。想让用户能跳片头，最省事的做法是"章节名关键词 + 时间点"，不需要插件 |
| 32 | `EnableDirectStream` 默认被注释为"broken" | `StreamBuilder.cs:489-491` | **不要盲抄这个开关**。Jellyfin 自己承认 direct-stream http 有问题，自研时把这条件写清楚即可 |
| 33 | **HLS 只输出单 variant**（无 ABR 多档） | `DynamicHlsController` 主清单 | **不要照抄**。若你要 ABR，需要自己实现多 variant + 多 ffmpeg 进程 |
| 34 | 服务端**不做**码率阶梯 | 官方文档 + `MediaOptions.GetMaxBitrate` | 自研时明确：阶梯在客户端 UI，选一档 → 转一次 |

#### 1.8.1 可直接落地的"播放决策"伪代码

```python
# 把 Jellyfin StreamBuilder 的核心逻辑改写成 Python（结构一一对应）
from dataclasses import dataclass
from enum import IntFlag

class PlayMethod:  # 数值与 Jellyfin 一致，便于互通
    TRANSCODE, DIRECT_STREAM, DIRECT_PLAY = 0, 1, 2

class Reason(IntFlag):
    CONTAINER      = 1 << 0
    VIDEO_CODEC    = 1 << 1
    AUDIO_CODEC    = 1 << 2
    SUBTITLE_CODEC = 1 << 3
    VIDEO_RES      = 1 << 8
    VIDEO_BITDEPTH = 1 << 9
    VIDEO_FRAMERATE= 1 << 10
    AUDIO_CHANNELS = 1 << 14
    BITRATE_LIMIT  = 1 << 18
    VIDEO_BITRATE  = 1 << 19

# "改封装能解决"的原因集合 —— 抄 StreamBuilder.cs:27
DIRECT_STREAM_REASONS = (
    Reason.CONTAINER | Reason.AUDIO_CODEC
    | Reason.AUDIO_CHANNELS | Reason.VIDEO_BITRATE
)

def split_csv(s):                       # 抄 ContainerHelper.ContainsContainer 语义
    return {x.strip().lower() for x in (s or "").split(",") if x.strip()}

def decide(source, profile, max_bitrate, subtitle_stream=None, sub_method="external"):
    """source: 探测结果(容器/编码/码率/分辨率/音轨...);  profile: 客户端能力"""
    reasons = Reason(0)

    if max_bitrate and source.bitrate and source.bitrate > max_bitrate:
        reasons |= Reason.BITRATE_LIMIT

    best = None                            # (score, play_method, reasons)
    for dp in profile.direct_play_profiles:
        if dp.type != "video":
            continue
        r = Reason(0)
        if source.container.lower() not in split_csv(dp.container):
            r |= Reason.CONTAINER
        if source.video_codec.lower() not in split_csv(dp.video_codec):
            r |= Reason.VIDEO_CODEC
        audio = next((a for a in source.audio_streams
                      if a.codec.lower() in split_csv(dp.audio_codec)), None)
        if audio is None:
            r |= Reason.AUDIO_CODEC
        r |= check_codec_profile(source, profile, dp)       # 分辨率/位深/帧率/profile/level...

        # 抄 StreamBuilder.cs:1325-1331：只有"必须烧录"才因字幕拉低直连
        if subtitle_stream is not None and sub_method == "encode":
            r |= Reason.SUBTITLE_CODEC

        r |= reasons                                         # 码率超限

        if r == 0:
            method = PlayMethod.DIRECT_PLAY
        elif (r & ~DIRECT_STREAM_REASONS) == 0:
            method = PlayMethod.DIRECT_STREAM                 # 只需 remux
        else:
            method = None
        if method is not None:
            cand = (method, r, audio)
            if best is None or cand[0] > best[0]:
                best = cand

    if best is None:
        return PlayMethod.TRANSCODE, reasons or Reason.VIDEO_CODEC
    return best[0], best[1]
```

#### 1.8.2 可直接落地的 FFmpeg 命令构造

```python
def build_hls_cmd(src, out_dir, name, plan, seg=6, container="ts",
                  vcodec="libx264", acodec="aac",
                  vbitrate=None, abitrate=192000, channels=2, ss_ticks=0, duration_ticks=0):
    ticks = 10_000_000                                   # 1 秒 = 1e7 ticks
    args = []

    # --- 输入前置（抄 EncodingHelper.GetInputModifier / GetFastSeekCommandLineParameter）
    if ss_ticks > 0:
        t = ss_ticks
        if plan.method == PlayMethod.DIRECT_STREAM:       # remux：+0.5s 命中关键帧
            t += 5_000_000
        if duration_ticks:                                # 夹到 [0, 时长-5s]
            t = max(0, min(t, max(duration_ticks - 50_000_000, 0)))
        args += ["-ss", f"{t / ticks:.3f}"]

    args += ["-i", src]                                   # 若 DVD/BD：改用 -f concat -safe 0 -i <concat>
    # 外挂字幕作为第二输入：args += ["-i", f"file:{sub_path}"]

    # --- 全局（抄 DynamicHlsController.cs:1642 模板）
    args += ["-map_metadata", "-1", "-map_chapters", "-1", "-threads", "0"]
    args += build_map_args(plan)                          # 见 §1.3.6

    # --- 视频
    if plan.method == PlayMethod.DIRECT_STREAM:
        args += ["-codec:v:0", "copy", "-start_at_zero"]
    else:
        args += ["-codec:v:0", vcodec]
        if vbitrate:
            if vcodec in ("libx264", "libx265"):
                args += ["-maxrate", str(vbitrate), "-bufsize", str(vbitrate * 2)]
            else:
                args += ["-b:v", str(vbitrate), "-maxrate", str(vbitrate), "-bufsize", str(vbitrate * 2)]
        # 关键帧对齐分片（抄 GetHlsVideoKeyFrameArguments）
        args += ["-force_key_frames:0", f"expr:gte(t,n_forced*{seg})"]
        if vcodec == "libx264":
            args += ["-sc_threshold:v:0", "0"]            # 防 x264 后处理破坏强制关键帧
        if plan.fps:
            gop = int(-(-seg * plan.fps // 1))             # ceil
            args += ["-g:v:0", str(gop), "-keyint_min:v:0", str(gop)]
        if plan.filters:                                  # 缩放/去隔行/tonemap/字幕滤镜
            args += ["-vf", ",".join(plan.filters)]
        if plan.burn_subtitle:
            args += ["-start_at_zero"]

    # --- 音频
    if plan.audio_copy:
        args += ["-codec:a:0", "copy"]
    else:
        args += ["-codec:a:0", acodec, "-ac", str(channels)]
        if abitrate: args += ["-ab", str(abitrate)]
        args += ["-ar", "48000"]

    # --- HLS 输出（抄模板固定部分）
    args += ["-copyts", "-avoid_negative_ts", "disabled",
             "-max_muxing_queue_size", "2048",
             "-f", "hls", "-max_delay", "5000000",
             "-hls_time", str(seg),
             "-hls_segment_type", "mpegts" if container == "ts" else "fmp4",
             "-start_number", "0",
             "-hls_segment_filename", f"{out_dir}/{name}%d.{container}",
             "-hls_playlist_type", "vod", "-hls_list_size", "0"]
    if container == "mp4":
        args += ["-hls_fmp4_init_filename", f"{name}-1.mp4",
                 "-hls_segment_options", "movflags=+frag_discont+skip_sidx"]
    args += ["-y", f"{out_dir}/{name}.m3u8"]
    return args
```

**分段就绪判定**（Python 版）：
```python
def segment_ready(seg_path, next_seg_path, proc):
    if proc.poll() is not None:        # 转码已退出 -> 已存在的都算好
        return os.path.exists(seg_path)
    return os.path.exists(seg_path) and os.path.exists(next_seg_path)
```

---

## 二、自动刮削（Metadata / Scraping）

### 2.1 库与目录约定（官方文档，逐字核实）

> 权威页面：<https://jellyfin.org/docs/general/server/media/movies>、<https://jellyfin.org/docs/general/server/media/shows>、<https://jellyfin.org/docs/general/server/media/music>、<https://jellyfin.org/docs/general/server/media/books>。
> **注意**：`/docs/general/server/metadata/movies`、`/metadata/tv-shows`、`/metadata/images` 这三个"看起来该存在"的 URL **全部 404**（已复核状态码）；影片/剧集的元数据页面实际在 `media/` 下。

#### 2.1.1 电影

**目录树（官方逐字）**：
```
Movies
├── Best_Movie_Ever (2019)
│   ├── Best_Movie_Ever (2019).mp4
│   ├── Best_Movie_Ever (2019).nfo
│   ├── Best_Movie_Ever (2019).en_us.srt
│   ├── cover.png
│   └── theme.mp3
└── Movie (2021) [imdbid-tt12801262]
    ├── backdrop.jpg
    └── VIDEO_TS
        ├── VIDEO_TS.BUP
        ├── VIDEO_TS.IFO
        ├── VIDEO_TS.VOB
        ├── VTS_01_0.BUP
        ├── VTS_01_0.IFO
        ├── VTS_01_0.VOB
        ├── VTS_01_1.VOB
        └── VTS_01_2.VOB
```

**命名格式**：
```
Movie Name (year) [metadata provider id]
```
- `year` 与 `metadata provider id` 都是 **optional**，但"they will help identify media more reliably"。
- 四个官方示例：
  - `Jellyfin Documentary.mkv`
  - `Jellyfin Documentary (2030).mkv`
  - `Jellyfin Documentary [imdbid-tt00000000].mkv`
  - `Jellyfin Documentary (2030) [imdbid-tt00000000].mkv`
- **文件夹内的视频文件应与文件夹同名**（"The video files within the folder should have the same name as the folder."）。

**provider id 语法**（<https://jellyfin.org/docs/general/server/metadata/identifiers>）：
- 支持**三种括号**：`[]`、`()`、`{}`。
- 支持三个 provider：`tmdbid`（别名 `tmdb`）、`tvdbid`（别名 `tvdb`）、`imdbid`（别名 `imdb`）。
- 可同时指定多个：`Best_Movie_Ever (1994) [tmdbid-680] [imdbid-1234]`。
- 逐字示例：
```
Movies
├── Best_Movie_Ever (1994) [tmdbid-680] [imdbid-1234]
│   ├── Best_Movie_Ever (1994) [tmdbid-680].mp4
├── Best_Movie_Ever_2 (1994) {tmdb-680}
│   └── Best_Movie_Ever_2 (1994) {tmdb-680}.mp4
└── Movie (2021) [imdbid-tt12801262]
    └── Movie (2021) [imdbid-tt12801262].mp4
Shows
└── Series Name (2018) [tvdbid-79168]
    ├── Season 01
    |   ├── Series Name S01E01.mkv
    |   └── Series Name S01E02.mkv
    └── Season 02
        ├── Series Name S02E01-E02.mkv
        └── Series Name S02E03.mkv
```

**保留字符**（"Including them **WILL** cause problems"）：`<` `>` `:` `"` `/` `\` `|` `?` `*`
（这条对**从 TMDB 抓标题后回写文件名**的场景极其重要——很多影片标题带 `:`，直接落盘会出问题。）

**容器与镜像**：
- 支持 `mp4`、`mkv` 等常见格式。
- `VIDEO_TS` 与 `BDMV` 文件夹受支持，但**不支持多版本、多部分、外挂字幕/音轨**。
- `.iso` 等光盘镜像 "should work, but are not supported"；建议 remux 成 `mkv` 或解包为 `VIDEO_TS`/`BDMV`。

**多版本（Multiple Versions）**——规则很硬：
- 同一电影文件夹内的多个视频文件，靠"**文件名前缀与父文件夹基础名逐字符相同**"归为多版本。
- 逐字原文："Each file **must** begin exactly with the base name of the parent folder - including any year and/or metadata provider IDs - before adding a version label. This prefix must match character-for-character; otherwise, the files will be treated as separate items."
- 版本标签与前缀之间用 **hyphen / dot / underscore** 分隔，或用**方括号**包围；分隔符前后空格可选。
- **标签内容不预设**："Labels are not predetermined and can be made up by the user."
```
Movie (2021) [imdbid-tt12801262]
├── Movie (2021) [imdbid-tt12801262] - 2160p.mp4
├── Movie (2021) [imdbid-tt12801262] - 1080p.mp4
└── Movie (2021) [imdbid-tt12801262] - Directors Cut.mp4
```
- **排序规则**：默认按字母序；**例外**——以 `p` 或 `i` 结尾的版本名被判定为"分辨率名"，按分辨率从高到低排。**列表第一项为默认选中**。
  - 分辨率排序：`1080p, 2160p, 360p, 480p, 720p` → `2160p, 1080p, 720p, 480p, 360p`
  - 具名排序：`Extended Cut, Cinematic Cut, Director's Cut` → `Cinematic Cut, Director's Cut, Extended Cut`
- **关于 "edition"**：官方电影页**没有**独立的 Editions 机制，也**没有** `{edition-...}` 语法。`Directors Cut` / `Extended` 这类就是**版本标签**，即上面的 Multiple Versions 机制。

**多部分（Multiple Parts）**：
- part type：`cd`、`dvd`、`part`、`pt`、`disc`、`disk`
- 分隔符：空格、`.`、`-`、`_`
- part number：任意数字，或字母 `a`-`d`
- part type 与 number 之间的分隔符可省略
```
Movie Name (2010)
├── Movie Name-cd1.mkv
├── Movie Name-cd2.mkv
└── Movie Name-cd3.mkv
```
- 多部分可与多版本叠加。**当额外 part 与不同版本同时存在时，带额外 part 的版本排在选择器第一位**（与纯多版本时的排序相反）。官方示例：
```
S01E01 - 720p - Part 1.mkv
S01E01 - 720p - Part 2.mkv
S01E01 - 1080p.mkv
```
UI 选择器顺序：
```
S01E01 - 720p - Part 1.mkv
S01E01 - 1080p.mkv
```

**Extras（花絮）**——三种方式：

(a) **Extras 文件夹**（官方逐字清单，movies 与 shows 完全相同）：
`behind the scenes`、`deleted scenes`、`interviews`、`scenes`、`samples`、`shorts`、`featurettes`、`clips`、`other`（通用兜底）、`extras`（通用兜底）、`trailers`、`theme-music`、`backdrops`
```
Best_Movie_Ever (2019)
├── Best_Movie_Ever (2019).mp4
├── behind the scenes
│   └── Finding the right score.mp4
└── extras
    └── Home recreation.mp4
```

(b) **特定文件名**（单文件时）：`trailer`、`sample`
```
Best_Movie_Ever (2019)
├── Best_Movie_Ever (2019) - 1080P.mp4
└── trailer.mp4
```

(c) **文件名后缀**（强调："these suffixes **DO NOT** contain any spaces"，少数例外已注明）：
`-trailer`、`.trailer`、`_trailer`、` trailer`、`-sample`、`.sample`、`_sample`、` sample`、`-scene`、`-clip`、`-interview`、`-behindthescenes`、`-deleted`、`-deletedscene`、`-featurette`、`-short`、`-other`、`-extra`
```
Best_Movie_Ever (2019)
├── Best_Movie_Ever (2019) - 1080P.mp4
├── Preview Trailer.trailer.mp4
└── Making of The Best Movie Ever-behindthescenes.mp4
```

**Theme media**：
- 歌曲：`theme.ext`、`theme-music/*`；视频：`backdrops/*`
- 两者都有且都启用时："Theme videos will be preferred and Theme songs will not play."
- 需用户在客户端启用（User settings → Display → Libraries → Theme songs / Theme videos）。
- 推荐用 Web 标准格式（WebM / VP9 / Opus）以获得流畅播放。
```
Movies
└── Best_Movie_Ever (2019)
    ├── backdrops
    │   └── bluray-menu.ext
    ├── theme.ext
    └── theme-music
        └── awesome-soundtrack-song.ext
```

**3D 标记**（第一个标签是 `3D`，须与格式标签组合；大小写不敏感，须被空格/`-`/`.`/`_` 包围）：

| Format | Flag |
|---|---|
| half side by side | `hsbs` |
| full side by side | `fsbs` |
| half top and bottom | `htab` |
| full top and bottom | `ftab` |
| Multiview Video Coding | `mvc` |
| Anaglyph | **Not Supported** |

```
Awesome 3D Movie (2022).3D.FTAB.mp4
Awesome 3D Movie (2022)_3D_htab.mp4
Awesome 3D Movie (2022)-3d-hsbs.mp4
```

#### 2.1.2 剧集（TV Shows）

**目录树（官方逐字）**：
```
Shows
├── Series Name A (2010)
│   ├── Season 00
│   │   ├── Some Special.mkv
│   │   ├── Series Name A S00E01.mkv
│   │   └── Series Name A S00E02.mkv
│   ├── Season 01
│   │   ├── Series Name A S01E01-E02.mkv
│   │   ├── Series Name A S01E03.mkv
│   │   └── Series Name A S01E04.mkv
│   └── Season 02
│       ├── Series Name A S02E01.mkv
│       ├── Series Name A S02E02.mkv
│       ├── Series Name A S02E03 Part 1.mkv
│       └── Series Name A S02E03 Part 2.mkv
└── Series Name B (2018)
    ├── Season 01
    |   ├── Series Name B S01E01.mkv
    |   └── Series Name B S01E02.mkv
    └── Season 02
        ├── Series Name B S02E01-E02.mkv
        └── Series Name B S02E03.mkv
```

**命名与硬约束**：
- series 文件夹：`Series Name (year) [metadata provider id]`（year 与 id 可选）
- **season 文件夹必须命名为 `Season *`**，逐字原文：
  > "The Season folders should be named `Season *`, with `*` being any number. **Do not abbreviate the `Season` name to `S01` or `SE01`.** For the best results, please pad the season number with `0`s at the front to make sure each entry has the same number of digits. For example: `Season 5` -> `Season 05`. Also **do not mix** Season folders with episodes in the Shows folder."
- **多集文件**：一个文件可含多集（`S01E01-E02`），但会**显示为单个条目**、携带多集元数据。官方建议用 MKVToolNix 拆分成单独剧集。

**Specials（特别篇）**：
- 放在 **`Season 00`**。
- 若元数据提供方没有该 special 的信息，**建议用描述内容的名称**，而不是 `Series Name S00Exy.mkv`——"This is done to avoid wrong metadata being pulled for the special and to provide a proper presentation."
- "Episode numbering for specials may vary from metadata provider to metadata provider."
- 若要显示在所属季内，**需要 2 项设置**：
  1. `Dashboard -> Library -> Display` 下启用 **`Display specials within their series they aired in`**
  2. 在元数据（Metadata editor 或 NFO 的 `airsbefore_season` / `airsafter_season` / `airsbefore_episode` 标签）中设置播出位置
- 排期规则（逐字）：
  - 只设 `Airs before season`（未设 `Airs before episode`）→ 在该季开头、第一集之前播放
  - `Airs before season` = 2 且 `Airs before episode` = 7 → 在 S02E06 与 S02E07 之间
  - `Airs after series` → 显示/播放在指定季末尾；**若同时设了 before 字段，after 优先**
  - 多个 special 位置相同 → 按其在 specials 季内的存储顺序播放。例：S00E01 与 S00E03 都设 `Airs before season`=3、`Airs before episode`=7，则顺序为 `S03E06 → S00E01 → S00E03 → S03E07`
- **注意**："this will show them in both the `Specials` season, as well as the season specified."

**剧集多版本**：当多个文件**位于同一 season 文件夹内**且"按标准命名方案被识别为同一集"时归为多版本：
```
Shows
└── Series Name (2010)
    └── Season 01
        ├── Series Name S01E01 - 1080p.mkv
        ├── Series Name S01E01 - 720p.mkv
        └── Series Name S01E01 - Extended.mkv
```

**外挂字幕/音轨的 flag 语法**（movies 与 shows 两页逐字相同）：
```
Movies
└── Film (1986)
    ├── Film.mkv
    ├── Film.default.srt
    ├── Film.default.en.forced.ass
    ├── Film.forced.en.dts
    ├── Film.en.sdh.srt
    └── Film.English Commentary.en.mp3
```

| Type | Flag |
|---|---|
| Default | `default` |
| Forced | `forced`, `foreign` |
| Hearing Impaired | `sdh`, `cc`, `hi` |

- 每个 title/flag 字段可以是任意字符串或特殊 flag，用 `.` 分隔，可多个。
- **`hi` 与印地语冲突**："`hi` by itself will resolve as a Hindi language track, while `hi` in addition to another language identifier (such as `title.en.hi.srt`) will use the other language and tag it as hearing impaired."
- **"Flags are ignored on containers with more than one stream."**
- 无法解析为语言或 flag 的文本会被合并、用作**流标题**（前提是文件元数据里没有内嵌流标题）。上例最后一个文件 → 英语 mp3 音轨，标题 `English Commentary`。

#### 2.1.3 音乐

- **"Albums are organized in folders, with one folder containing one and only one album."**
- **"Jellyfin does not care how you organize albums together, as long as each album is contained within one folder."**
- **"Filenames generally do not matter since the info will be scraped from the embedded metadata of the tracks. If no other metadata was found, Jellyfin uses the file names as track titles."**
```
Music
├── Some Artist
│   ├── Album A
│   │   ├── Song 1.flac
│   │   ├── Song 2.flac
│   │   └── Song 3.flac
│   └── Album B
│       ├── Track 1.m4a
│       ├── Track 2.m4a
│       └── Track 3.m4a
└── Album X
    ├── Whatever You.mp3
    ├── Like To.mp3
    ├── Name Your.mp3
    └── Music Files.mp3
```
（第二种合法形态：不按 Artist 分层，专辑直接位于 `Music` 根下。）

- **多碟**：靠内嵌标签的 `disc number` 与 `total discs` 识别。"Place the tracks for all discs in one folder. They can optionally be separated into disc folders, but **embedded metadata takes priority**."
- **歌词**："Lyrics **must** be contained in the same folder and match the filename for their corresponding item."（如 `01 Death Eternal.lrc` / `.elrc` / `.txt`）
- **格式陷阱**（逐字）：
  - 纯音频 `.mp4` 不会被识别为音乐 → **改名为 `.m4a`**
  - 纯音频 `.mkv` / `.webm` 不会被识别为音乐 → **改名为 `.mka`**
  - `.weba` 不支持 → **改名为 `.mka`**
  - 带 WebP 内嵌图或 ID3 标签的 `.flac` 在某些浏览器可能播不动 → 客户端开 `Always remux FLAC audio files`
  - ID3v1 标签大多数字段**30 字节上限**，超长会被截断 → **升级到 ID3v2.4**
  - 转封装命令：`ffmpeg -i <Input File> -c:a copy <Output File>.mka`（注意元数据/图片可能丢失）

#### 2.1.4 图书/有声书/漫画（简要）

- "Books should be organized by type (Audiobooks, Books, Comics), then **optionally** by Author. **Each book should be in their own folder.**"
- 支持格式：`azw, azw3, cb7, cbr, cbt, cbz, epub, mobi, pdf, zip, rar, 7z`
- 内置在线 provider：GoogleBooks、ComicVine、OpenLibrary
- 外部元数据：`content.opf`、`metadata.opf`、`ComicInfo.xml`（支持 ComicRack 的 ComicInfo 与 ComicBookLover 的 ComicBookInfo 两种格式）
- **"Read-along audiobooks are not supported by Jellyfin."**
- 漫画：`<Series Name> (<year>)` 文件夹 + `Name #NNN (year).ext` 文件；**"the year of the first issue should be used"**（跨年份的漫画/杂志用第一期年份）

#### 2.1.5 混合内容（Mixed Movies and Shows）

原 `/docs/general/server/media/mixed-content` **404**；现役页面为 <https://jellyfin.org/docs/general/server/media/mixed-movies-and-shows>，全文极短，逐字：

> This library type should follow the recommended guides from the Movies and TV Shows sections. Some functions may not function correctly as they would if they were in separate movie or shows libraries, and for best results, it's recommended to use dedicated movie or shows libraries.

即：**没有独立规则，官方明确建议用专用库**。

#### 2.1.6 本地图片文件名约定（权威表）

规则："Images can either be provided as external files within the media folders, or embedded in the media files themselves. When external images are provided, they should be placed alongside the media files. **When they are provided, they will take precedence over other sources.**"
用法："all filenames can be used either **standalone** (e.g. `logo.png`) or as a **suffix** (e.g. `movie-logo.png`)"

| Filename | Type | Movies | Series | Season | Episode | Music | Artist |
|---|---|---|---|---|---|---|---|
| `poster` | Primary | ✅ | ✅ | ✅ | | ✅ | |
| `folder` | Primary | ✅ | ✅ | ✅ | | ✅ | ✅ |
| `cover` | Primary | ✅ | ✅ | ✅ | | ✅ | |
| `default` | Primary | ✅ | ✅ | ✅ | | ✅ | |
| `movie` | Primary | ✅ | | | | | |
| `show` | Primary | | ✅ | | | | |
| `jacket` | Primary | | | | | ✅ | |
| `thumb`（**作为后缀**） | Primary | | | | ✅ | | |
| `backdrop` | Backdrop | ✅ | ✅ | ✅ | | ✅ | |
| `fanart` | Backdrop | ✅ | ✅ | ✅ | | ✅ | |
| `background` | Backdrop | ✅ | ✅ | ✅ | | ✅ | |
| `art` | Backdrop | ✅ | ✅ | ✅ | | ✅ | |
| `extrafanart`（**文件夹**） | Backdrop | ✅ | ✅ | ✅ | | ✅ | |
| `banner` | Banner | ✅ | ✅ | ✅ | | ✅ | |
| `logo` | Logo | ✅ | ✅ | ✅ | | ✅ | |
| `clearlogo` | Logo | ✅ | ✅ | ✅ | | ✅ | |
| `landscape` | Thumb | ✅ | ✅ | ✅ | | ✅ | |
| `thumb` | Thumb | ✅ | ✅ | ✅ | | ✅ | |

- Episode 的 Primary 用后缀形式：`S01E01 Some Episode-thumb.jpg`
- 多张 backdrop 轮播：文件名末尾直接加数字或加连字符加数字，如 `backdrop-1.jpg`、`backdrop2.jpg`
- 标注 ✅ 的项"can also be embedded in supported media containers (e.g. mkv) and will be used when the **`Embedded Image Extractor`** source is enabled for movies."（这是文档中唯一出现的图片抓取源开关名）
- **Unused types**（官方列为未使用，但第三方客户端可能用）：`Art`、`Disc`、`Box`、`Menu`、`Chapter`、`BoxRear`、`Profile`；`Screenshot` = "Unused, Deprecated"。对应文件名：`disc`(Disc)、`cdart`(Disc)、`discart`(Disc)、`clearart`(Art)
- **官方文档没有任何图片像素尺寸建议值**（除章节图的定性说明与源码 `MinWidth = 1280` 默认值）

---

### 2.2 季集识别（命名解析引擎）

命名解析全部在 **`Emby.Naming`** 项目（4370 行，10 个子目录）。这是 Jellyfin 最"可移植"的部分——纯正则 + 纯函数，没有任何 IO 依赖，非常适合用 Python 重写。

#### 2.2.1 三个解析器入口

| 解析器 | 文件 | 作用 |
|---|---|---|
| `VideoResolver` | `Emby.Naming/Video/VideoResolver.cs`（160 行） | 电影/通用视频：抽 name、year、container、3D、extra 类型、stub |
| `EpisodeResolver` | `Emby.Naming/TV/EpisodeResolver.cs`（93 行） | 剧集：季号、集号、结束集号、剧名、日期式 |
| `SeasonPathParser` | `Emby.Naming/TV/SeasonPathParser.cs`（193 行） | 季目录/季文件名 → 季号 |

#### 2.2.2 27 条有序 `EpisodeExpressions`（**顺序即优先级**）

`EpisodeExpressions` 定义在 `Emby.Naming/Common/NamingOptions.cs:320-495`。`EpisodePathParser.Parse`（`Emby.Naming/TV/EpisodePathParser.cs:21-58`）**按数组顺序逐条试**，第一条 `Success` 即 `break`：

```csharp
foreach (var expression in _options.EpisodeExpressions)
{
    if (supportsAbsoluteNumbers.HasValue && expression.SupportsAbsoluteEpisodeNumbers != supportsAbsoluteNumbers.Value) continue;
    if (isNamed.HasValue && expression.IsNamed != isNamed.Value) continue;
    if (isOptimistic.HasValue && expression.IsOptimistic != isOptimistic.Value) continue;

    var currentResult = Parse(path, expression);
    if (currentResult.Success) { result = currentResult; break; }
}
```

**三条过滤标志**（来自 `EpisodeExpression.cs`，70 行）：

| 标志 | 含义 |
|---|---|
| `IsNamed` | "剧名+季集"式命名（可信度高） |
| `IsOptimistic` | "可能误判"的宽松式（如纯数字文件名），只在放宽模式下启用 |
| `SupportsAbsoluteEpisodeNumbers` | 是否支持绝对集号 |

`isDirectory` 为真时会给路径**追加 `.mp4`**（因为部分正则要求文件扩展名）：
```csharp
// Added to be able to use regex patterns which require a file extension.
if (isDirectory) path += ".mp4";
```

**完整表达式清单**（行号 → 正则 → 标志）。`[]` 在正则里是字符类，源码写作 `[][ ._-]` 表示"`]`、`[`、空格、`.`、`_`、`-` 之一"：

| # | 行 | 正则（逐字） | 标志 | 覆盖的命名形式 |
|---|---|---|---|---|
| 1 | 324 | `.*(\\|\/)(?<seriesname>((?![Ss]([0-9]+)[][ ._-]*[Ee]([0-9]+))[^\\\/])*)?[Ss](?<seasonnumber>[0-9]+)[][ ._-]*[Ee](?<epnumber>[0-9]+)([^\\/]*)$` | `IsNamed` | **Kodi 标准**：`foo.s01.e01`、`foo.s01_e01`、`S01E02 foo`、`S01 - E02` |
| 2 | 329 | `[\._ -]()[Ee][Pp]_?([0-9]+)([^\\/]*)$` | — | `foo.ep01`、`foo.EP_01` |
| 3 | 331 | `[^\\/]*?()\.?[Ee]([0-9]+)\.([^\\/]*)$` | — | `foo.E01.`、`foo.e01.` |
| 4 | 332 | `(?<year>[0-9]{4})[._ -](?<month>[0-9]{2})[._ -](?<day>[0-9]{2})` | `IsByDate=true` | **日期式**：`2019-01-01` |
| 5 | 342 | `(?<day>[0-9]{2})[._ -](?<month>[0-9]{2})[._ -](?<year>[0-9]{4})` | `IsByDate=true` | 日期式：`01-01-2019` |
| 6 | 356 | `.*[\\\/]((?<seriesname>[^\\/]+?)\s)?[Ss](?:eason)?\s*(?<seasonnumber>[0-9]+)\s+[Ee](?:pisode)?\s*(?<epnumber>[0-9]+).*$` | `IsNamed` | `Season 1 Episode 2`（**展开写法**） |
| 7 | 367 | `.*[\\\/](?![Ee]pisode)(?![^\\\/]*[Ss][0-9]+[][ ._-]*[Ee][0-9]+)(?<seriesname>[\w\s]+?)\s(?<epnumber>[0-9]{1,4})(-(?<endingepnumber>[0-9]{2,4}))*[^\\\/x]*$` | `IsNamed` | `Series Name 102`（**剧名+纯集号**） |
| 8 | 372 | `[\\\/\._ \[\(-]([0-9]+)x([0-9]+(?:(?:[a-i]|\.[1-9])(?![0-9]))?)([^\\\/]*)$` | `SupportsAbsoluteEpisodeNumbers=true` | **`1x01` 形式**！ |
| 9 | 379 | `.*[\\\/]?.*?(\[.*?\])+.*?(?<seriesname>[-\w\s]+?)[\s_]*-[\s_]*(?<epnumber>[0-9]+).*$` | `IsNamed` | `[SubGroup] Series - 01`（**动漫组命名**） |
| 10 | 387 | `.*[\\\/](?<seriesname>[^\\\/]+?)[\s_]+-[\s_]+(?<epnumber>[0-9]+)[\s_]*(?:\[.*?\]|\(.*?\))*[\s_]*(?:\.\w+)?$` | `IsNamed` | `Series - 01` |
| 11 | 395 | `[\\/._ -](?<seriesname>(?![0-9]+[0-9][0-9])([^\\\/_])*)[\\\/._ -](?<seasonnumber>[0-9]+)(?<epnumber>[0-9][0-9](?:(?:[a-i]|\.[1-9])(?![0-9]))?)([._ -][^\\\/]*)$` | `IsOptimistic`、`IsNamed`、`SupportsAbsoluteEpisodeNumbers=false` | `Series 1 02 Title` |
| 12 | 401 | `[\/._ -]p(?:ar)?t[_. -]()([ivx]+|[0-9]+)([._ -][^\/]*)$` | `SupportsAbsoluteEpisodeNumbers=true` | `Part 1` / `pt.1` / `part IV` |
| 13 | 409 | `[Ee]pisode (?<epnumber>[0-9]+)(-(?<endingepnumber>[0-9]+))?[^\\\/]*$` | `IsNamed` | `Episode 5` |
| 14 | 414 | `.*(\\|\/)[sS]?(?<seasonnumber>[0-9]+)[xX](?<epnumber>[0-9]+)[^\\\/]*$` | `IsNamed` | `1x01`（无剧名前缀） |
| 15 | 419 | `.*(\\|\/)[sS](?<seasonnumber>[0-9]+)[x,X]?[eE](?<epnumber>[0-9]+)[^\\\/]*$` | `IsNamed` | `s01e01` / `s01xe01` |
| 16 | 424 | `.*(\\|\/)(?<seriesname>((?![sS]?[0-9]{1,4}[xX][0-9]{1,3})[^\\\/])*)?([sS]?(?<seasonnumber>[0-9]{1,4})[xX](?<epnumber>[0-9]+))[^\\\/]*$` | `IsNamed` | `Series 1x01` |
| 17 | 429 | `.*(\\|\/)(?<seriesname>[^\\\/]*)[sS](?<seasonnumber>[0-9]{1,4})[xX\.]?[eE](?<epnumber>[0-9]+)[^\\\/]*$` | `IsNamed` | `Series.S01E01` |
| 18 | 435 | `.*[\\\/](?<epnumber>[0-9]+)(-(?<endingepnumber>[0-9]+))*\.\w+$` | `IsOptimistic`、`IsNamed` | **纯集号**（文件名只有数字） |
| 19 | 442 | `([0-9]+)-([0-9]+)` | — | 纯区间 `01-02` |
| 20 | 445 | `.*(\\|\/)(?<epnumber>[0-9]{1,3})(-(?<endingepnumber>[0-9]{2,3}))*\s?-\s?[^\\\/]*$` | `IsOptimistic`、`IsNamed` | `01 - Title` |
| 21 | 452 | `.*(\\|\/)(?<epnumber>[0-9]{1,3})(-(?<endingepnumber>[0-9]{2,3}))*\.[^\\\/]+$` | `IsOptimistic`、`IsNamed` | `01.Title.mkv` |
| 22 | 459 | `.*[\\\/][^\\\/]* - (?<epnumber>[0-9]{1,3})(-(?<endingepnumber>[0-9]{2,3}))*[^\\\/]*$` | `IsOptimistic`、`IsNamed` | `Title - 01` |
| 23 | 466 | `[Ss]eason[\._ ](?<seasonnumber>[0-9]+)[\\\/](?<epnumber>[0-9]{1,3})([^\\\/]*)$` | `IsOptimistic`、`IsNamed` | **`Season 01/01.mkv`（无剧名）** |
| 24 | 474 | `(.*(\\|\/))*(?<seriesname>.+)\/[Ss](eason)?[\. _\-]*(?<seasonnumber>[0-9]+)` | `IsNamed` | `Series/Season 1` |
| 25 | 481 | `(.*(\\|\/))*(?<seriesname>.+)[\. _\-]+[sS](eason)?[\. _\-]*(?<seasonnumber>[0-9]+)` | `IsNamed` | `Series.Season.1` |
| 26 | 489 | `(?:\[(?:[^\]]+)\]\s*)?(?<seriesname>\[[^\]]+\]|[^[\]]+)\s*\[(?<epnumber>[0-9]+)\]` | `IsNamed` | `Series [01]` |
| 27 | 777-779 | （`.Select(i => new EpisodeExpression(i) { IsNamed = true })`） | `IsNamed` | 由日期格式数组转换而来的一批 |

**多集（区间）的解析规则**（`EpisodePathParser.cs:98-120`，很实用）：
```csharp
var endingNumberGroup = match.Groups["endingepnumber"];
if (endingNumberGroup.Success)
{
    // Will only set EndingEpisodeNumber if the captured number is not followed by additional numbers
    // or a 'p' or 'i' as what you would get with a pixel resolution specification.
    // It avoids erroneous parsing of something like "series-s09e14-1080p.mkv" as a multi-episode from E14 to E108
    int nextIndex = endingNumberGroup.Index + endingNumberGroup.Length;
    if (nextIndex >= name.Length || !"0123456789iIpP".Contains(name[nextIndex], StringComparison.Ordinal))
    {
        // A range cannot end before it starts, so a lower number belongs to the episode title rather than to a range.
        if (int.TryParse(endingNumberGroup.ValueSpan, ..., out num) && num >= result.EpisodeNumber)
            result.EndingEpisodeNumber = num;
    }
}
```
> **两条必须照抄的防误判规则**：① 结束集号后面**不能**紧跟数字或 `p`/`i`（否则 `S09E14-1080p` 会被误判为"E14 到 E108"）；② 结束集号**必须 ≥ 起始集号**。

**日期式解析**（`EpisodePathParser.cs:73-97`）：`IsByDate` 的表达式会把 `_` 替换为 `-`（处理 WMC 命名），然后用 `DateTime.TryParseExact`（`DateTimeFormats`）或 `DateTime.TryParse` 解析，结果写入 `Year`/`Month`/`Day`。注意源码里有一句 **`// TODO: Only consider success if date successfully parsed?`**，且紧跟 `result.Success = true;`（**即使日期解析失败也标记成功**）——这是个已知的宽松行为。

#### 2.2.3 季号解析（`SeasonPathParser`）

`Emby.Naming/TV/SeasonPathParser.cs`（193 行）的解析顺序（`GetSeasonNumberFromPath`）：

**Step 1 — `[sS](\d{1,4})` 前缀**
```csharp
[GeneratedRegex(@"[sS](\d{1,4})(?!\d|[eE]\d)(?=\.|_|-|\[|\]|\s|$)", RegexOptions.None)]
private static partial Regex SeasonPrefix();
```
命中则直接返回该数字，`IsSeasonFolder = true`。（即 `S01/` 这样的目录也能识别——**尽管官方文档明确不建议**。）

**Step 2 — 多语言 "season" 关键词表**

```csharp
private const string SeasonKeywordPattern =
    @"시즌|シーズン|сезон" +                                          // 韩/日/俄
    @"|season|sæson|saison|staffel|series|stagione|säsong|seizoen|seasong" +  // 丹/法/德/意/瑞典/荷
    @"|sezon|sezona|sezóna|sezonul|série|séria|serie|seria|temporada|kausi";   // 波/捷/罗/葡/西/芬
```
先做 `CleanNameRegex = [ ._\-\[\]]` 去除分隔符，再把**父目录名**从文件名里剔除（避免剧名干扰），然后：

```csharp
if (supportSpecialAliases && (filename.Equals("specials") || filename.Equals("extras")))
    return (0, true);                                    // ★ Specials / Extras 目录 -> 季号 0

if (supportNumericSeasonFolders && int.TryParse(filename, out val))
    return (val, true);                                  // ★ 纯数字目录 -> 季号（如 "01/"）

// 两种顺序：数字在前（"1st Season"）或关键词在前（"Season 1"）
[GeneratedRegex(@"^\s*((?<seasonnumber>(?>\d+))(?:st|nd|rd|th|\.)*(?!\s*[Ee]\d+))\s*(?:" + SeasonKeywordPattern + @")\s*(?<rightpart>.*)$", RegexOptions.IgnoreCase)]  // ProcessPre
[GeneratedRegex(@"^\s*(?:" + SeasonKeywordPattern + @")\s*(?<seasonnumber>\d+?)(?=\d{3,4}p|[^\d]|$)(?!\s*[Ee]\d)(?<rightpart>.*)$", RegexOptions.IgnoreCase)]            // ProcessPost
```
注意 `(?=\d{3,4}p|[^\d]|$)` 这个前瞻：**防止把 `Season 1080p` 里的 `1080` 当季号**。

**混合库特例**：`isMixedLibrary = !supportNumericSeasonFolders && !supportSpecialAliases`，此时若原始文件名里**不含** season 关键词，则返回 `(null, false)`（不认）。

**调用侧传参**（决定上面三个开关的实际取值）：

| 调用点 | 行 | `supportSpecialAliases` | `supportNumericSeasonFolders` |
|---|---|---|---|
| `LibraryManager.GetSeasonNumber` | `Emby.Server.Implementations/Library/LibraryManager.cs:3287` | `true` | `true` |
| `SeriesResolver` | `.../Resolvers/TV/SeriesResolver.cs:169` | `isTvContentType` | `isTvContentType` |
| `SeasonResolver` | `.../Resolvers/TV/SeasonResolver.cs:56` | `true` | `true` |

> **结论：在"Shows"类型的库里，`Specials`/`Extras` 目录与纯数字目录都会被当作季目录**；而在混合内容库里两者都不认（更严格）。

#### 2.2.4 Extras 识别（`ExtraRuleResolver`）

`Emby.Naming/Video/ExtraRuleResolver.cs`：对每条 `ExtraRule` 做匹配，**四种规则类型**：

```csharp
bool isMatch = rule.RuleType switch
{
    ExtraRuleType.Filename      => fileNameWithoutExtension.Equals(rule.Token, StringComparison.OrdinalIgnoreCase),
    ExtraRuleType.Suffix        => trimmedFileNameWithoutExtension.EndsWith(rule.Token, StringComparison.OrdinalIgnoreCase),
    ExtraRuleType.Regex         => Regex.IsMatch(fileName, rule.Token, RegexOptions.IgnoreCase | RegexOptions.Compiled),
    ExtraRuleType.DirectoryName => directoryName.Equals(rule.Token, StringComparison.OrdinalIgnoreCase)
                                   && !string.Equals(fullDirectory, libraryRoot, StringComparison.OrdinalIgnoreCase),
    _ => false,
};
```

**一个细节**：Suffix 匹配前会**去掉文件名末尾的数字**（`fileNameWithoutExtension.TrimEnd(_digits)`），注释说明是为了识别 `-trailer2` 这类。

完整 `ExtraRule` 清单在 `NamingOptions.cs:497-700`，与官方文档的清单一致：
- `DirectoryName` 规则：`trailers`(Trailer)、`backdrops`(ThemeVideo)、`theme-music`(ThemeSong)、`behind the scenes`(BehindTheScenes)、`deleted scenes`(DeletedScene)、`interviews`(Interview)、`scenes`(Scene)、`samples`(Sample)、`shorts`(Short)、`featurettes`(Featurette)、`extras`(Unknown)、`extra`(Unknown)、`other`(Unknown)、`clips`(Clip)
- `Filename` 规则：`trailer`(Trailer)、`sample`(Sample)、`theme`(ThemeSong)
- `Suffix` 规则：`-trailer`/`.trailer`/`_trailer`/`- trailer`、`-sample`/`.sample`/`_sample`/`- sample`、`-scene`、`-clip`、`-interview`、`-behindthescenes`、`-deleted`、`-deletedscene`、`-featurette`、`-short`、`-extra`

#### 2.2.5 电影解析（`VideoResolver`）

```csharp
public static VideoFileInfo? Resolve(string? path, bool isDirectory, NamingOptions namingOptions, bool parseName = true, string? libraryRoot = "")
{
    // 1) 扩展名必须在 VideoFileExtensions 里（否则试 stub 扩展名 .disc，再否则返回 null）
    // 2) Format3DParser.Parse(path, namingOptions)        -> Is3D / Format3D
    // 3) ExtraRuleResolver.GetExtraInfo(path, ...)        -> ExtraType
    // 4) name = Path.GetFileNameWithoutExtension(path)
    // 5) if (parseName):
    //       var cleanDateTimeResult = CleanDateTime(name, namingOptions);   // 提取年份并清理
    //       name = cleanDateTimeResult.Name;  year = cleanDateTimeResult.Year;
    //       if (TryCleanString(name, namingOptions, out var newName)) name = newName;
    // 6) return new VideoFileInfo(path, container, isStub, name, year, stubType, is3D, format3D, extraType, isDirectory, extraRule)
}
```

**年份提取与名称清理的正则**（`NamingOptions.cs`）：

`CleanDateTimes`（2 条）：
```
(.+[^_\,\.\(\)\[\]\-])[_\.\(\)\[\]\-](19[0-9]{2}|20[0-9]{2})(?![0-9]+|\W[0-9]{2}\W[0-9]{2})([ _\,\.\(\)\[\]\-][^0-9]|).*(19[0-9]{2}|20[0-9]{2})*
(.+[^_\,\.\(\)\[\]\-])[ _\.\(\)\[\]\-]+(19[0-9]{2}|20[0-9]{2})(?![0-9]+|\W[0-9]{2}\W[0-9]{2})([ _\,\.\(\)\[\]\-][^0-9]|).*(19[0-9]{2}|20[0-9]{2})*
```
关键在 `(?![0-9]+|\W[0-9]{2}\W[0-9]{2})`：**排除"后面还跟数字"和"看起来像 MM-DD"的情况**，避免把 `1920x1080` 或日期误当年份。年份范围限定 `19xx`/`20xx`。

`CleanStrings`（6 条，按序尝试）——**这是"片名清洗"的核心，直接决定搜索关键词准不准**：
```
1) ^\s*(?<cleaned>.+?)[ _\,\.\(\)\[\]\-](3d|sbs|tab|hsbs|htab|mvc|HDR|HDC|UHD|UltraHD|4k|ac3|dts|custom|dc|divx|divx5|dsr|dsrip|dutch|dvd|dvdrip|dvdscr|dvdscreener|screener|dvdivx|cam|fragment|fs|hdtv|hdrip|hdtvrip|internal|limited|multi|subs|ntsc|ogg|ogm|pal|pdtv|proper|repack|rerip|retail|cd[1-9]|r5|bd5|bd|se|svcd|swedish|german|read.nfo|nfofix|unrated|ws|web-dl|telesync|ts|telecine|tc|brrip|bdrip|480p|480i|576p|576i|720p|720i|1080p|1080i|2160p|hrhd|hrhdtv|hddvd|bluray|blu-ray|x264|x265|h264|h265|xvid|xvidvd|xxx|www.www|AAC|DTS)(?=[ _\,\.\(\)\[\]\-]|$)
2) ^\s*(?<cleaned>.+?)((\s*\[[^\]]+\]\s*)+)(\.[^\s]+)?$                    # 去掉 [xxx] 标记
3) ^\s*(?<cleaned>.+?)\WE[0-9]+(-|~)E?[0-9]+(\W|$)                          # 去掉 E01-E02
4) ^\s*\[[^\]]+\](?!\.\w+$)\s*(?<cleaned>.+)                                # 去掉开头的 [xxx]
5) ^\s*(?<cleaned>.+?)\s+-\s+[0-9]+\s*$                                     # 去掉结尾的 " - 01"
6) ^\s*(?<cleaned>.+?)(([-._ ](trailer|sample))|-(scene|clip|behindthescenes|deleted|deletedscene|featurette|short|interview|other|extra))$   # 去掉 extra 后缀
```

**`VideoFileExtensions`（完整清单，`NamingOptions.cs:24-95`）**：
`.001 .3g2 .3gp .amv .asf .asx .avi .bin .bivx .divx .dv .dvr-ms .f4v .fli .flv .ifo .img .iso .m2t .m2ts .m2v .m4v .mkv .mk3d .mov .mp4 .mpe .mpeg .mpg .mts .mxf .nrg .nsv .nuv .ogm .ogv .pva .qt .rec .rm .rmvb .strm .svq3 .tp .ts .ty .viv .vob .vp3 .webm .wmv .wtv .xvid`

**`SubtitleFileExtensions`**：`.ass .mks .sami .smi .srt .ssa .sub .sup .vtt`
**`LyricFileExtensions`**：`.lrc .elrc .txt`
**`StubFileExtensions`**：`.disc`
**`VideoFlagDelimiters`**：`( ) - . _ [ ]`
**`MediaFlagDelimiters`**：`.`（单个字符）
**`MediaForcedFlags`**：`foreign`、`forced`；**`MediaDefaultFlags`**：`default`；**`MediaHearingImpairedFlags`**：`cc`、`hi`、`sdh`
**`AlbumStackingPrefixes`**：`cd`、`digital media`、`disc`、`disk`、`vol`、`volume`、`part`、`act`
**`ArtistSubfolders`**：`albums`、`broadcasts`、`bootlegs`、`compilations`、`dj-mixes`、…（`:182` 起）

**Stub 类型规则**（`.disc` 文件 + token 匹配，`NamingOptions.cs:97-138`）：

| stubType | token |
|---|---|
| `dvd` | `dvd` |
| `hddvd` | `hddvd` |
| `bluray` | `bluray`、`brrip`、`bd25`、`bd50` |
| `vhs` | `vhs` |
| `tv` | `HDTV`、`PDTV`、`DSR` |

#### 2.2.6 多部分（stack）与多版本（multi-version）

**多部分：`VideoFileStackingRules`**（`NamingOptions.cs:140-144`，2 条 `FileStackRule`）：
```
^(?<filename>.*?)(?:(?<=[\]\)\}])|[ _.-]+)[\(\[]?(?<parttype>cd|dvd|part|pt|dis[ck])[ _.-]*(?<number>[0-9]+)[\)\]]?(?:\.[^.]+)?$   -> true
^(?<filename>.*?)(?:(?<=[\]\)\}])|[ _.-]+)[\(\[]?(?<parttype>cd|dvd|part|pt|dis[ck])[ _.-]*(?<number>[a-d])[\)\]]?(?:\.[^.]+)?$    -> false
```
第二条的 `false` 是 `FileStackRule` 的第二个构造参数（`Emby.Naming/Video/FileStackRule.cs`）——表示**字母编号的只算"续集"而不算同一部影片的分片**（用于区分 `Movie-a.mkv`/`Movie-b.mkv` 这类）。

**多版本：`VideoListResolver.IsEligibleForMultiVersion`**（`Emby.Naming/Video/VideoListResolver.cs:180-203`）——**这是"多版本判定"的权威实现，比文档更精确**：

```csharp
private bool IsEligibleForMultiVersion(ReadOnlySpan<char> folderName, ReadOnlySpan<char> testFilename)
{
    if (!testFilename.StartsWith(folderName, StringComparison.OrdinalIgnoreCase))   // ★ 必须逐字符前缀匹配
        return false;

    // Remove the folder name before cleaning as we don't care about cleaning that part
    if (folderName.Length <= testFilename.Length)
        testFilename = testFilename[folderName.Length..].Trim();

    // There are no span overloads for regex unfortunately
    if (CleanStringParser.TryClean(testFilename.ToString(), _namingOptions.CleanStringRegexes, out var cleanName))
        testFilename = cleanName.AsSpan().Trim();

    // The CleanStringParser should have removed common keywords etc.
    return testFilename.IsEmpty
           || testFilename[0] == '-'
           || testFilename[0] == '_'
           || testFilename[0] == '.'
           || CheckMultiVersionRegex().IsMatch(testFilename);
}

[GeneratedRegex(@"^\[([^]]*)\]")]
private static partial Regex CheckMultiVersionRegex();          // 方括号包裹的版本标签
```

即：**去掉"文件夹名前缀"后，剩下的部分（再经 CleanString 清洗）必须为空，或以 `-`/`_`/`.` 开头，或以 `[` 开头**。

**聚合入口**：
```csharp
public IReadOnlyList<VideoInfo> Resolve(IReadOnlyList<VideoFileInfo> videoInfos, bool supportMultiVersion = true,
                                        bool parseName = true, string? libraryRoot = "", CollectionType? collectionType = null)
{
    // 先剔除 extras（否则 extras 会阻止 stack 解析，见单测 TestStackedWithTrailer）
    var nonExtras = videoInfos.Where(i => i.ExtraType is null)...;
    var stackResult = StackResolver.Resolve(nonExtras, _namingOptions).ToList();
    // … 组装 …
    if (supportMultiVersion)
        list = collectionType is CollectionType.tvshows ? GetEpisodesGroupedByVersion(list) : GetVideosGroupedByVersion(list);
    // 剩余的（extras）单独追加
}
```
剧集多版本还有额外门槛（`GetEpisodesGroupedByVersion`）：`videos.Count >= 2`，并按 `GetEpisodeVersionKey(path)` 分组——即**同一季目录内、被识别为同一集**才归并（与官方文档一致）。

#### 2.2.7 外挂字幕/音轨的 flag 解析（`ExternalPathParser`）

`Emby.Naming/ExternalFiles/ExternalPathParser.cs`（140 行）+ `ExternalPathParserResult.cs`（59 行）。解析对象是**与媒体同名的旁挂文件**，按 `.` 切分后缀段：

- 每一段先查 `MediaForcedFlags` / `MediaDefaultFlags` / `MediaHearingImpairedFlags`（`NamingOptions.cs:299-318`）
- 再查语言（ISO 639-1/2）
- 都不是则作为 **title** 累积
- **`hi` 的歧义处理**：单独 `hi` → Hindi；与其他语言标识同现（如 `title.en.hi.srt`）→ 用另一语言 + 标 HearingImpaired

> **注意一个与文档的差异**：官方文档说 "Flags are ignored on containers with more than one stream."；源码层的对应逻辑在**媒体探测阶段**（`SupportsExternalStream` / 多流容器时不再解析 flag），而不是 `ExternalPathParser` 里。

#### 2.2.8 识别失败怎么办

**（a）手动识别（Identify）**：`Jellyfin.Api/Controllers/ItemLookupController.cs`
- `GET /Items/{itemId}/ExternalIdInfos` — 列出该条目支持的 provider（含 `IExternalId` 的 `Key`/`Name`/`Type`）
- `GET /Items/{itemId}/RemoteSearch/{searchProviderName}?searchTerm=...` — 按关键词远程搜索候选
- `POST /Items/RemoteSearch/Apply/{itemId}?replaceAllImages=...` — 应用选中的候选结果并刷新

**（b）命名层面的兜底策略**（源码层的实际行为）：
1. `VideoResolver.Resolve` 返回 `null` → 该文件**完全不入库**（扩展名不在白名单且非 stub）。
2. `EpisodeResolver.Resolve` 返回 `null` → 该文件不会被识别为剧集；在 Shows 库里会退化为普通视频或不被收录。
3. 有 `parseName=false` 的调用路径（如直接按文件名当 name）——`VideoResolver.Resolve(..., parseName: false)` 时跳过 `CleanDateTime`/`CleanString`，**直接拿文件名**。
4. **剧集/季的父子关系兜底**：`Episode.FindSeasonId()` / `Season.FindSeriesId()`（`Episode.cs:216`、`Season.cs:249`）在 `SeasonId`/`SeriesId` 为空时按路径向上查找。

**（c）提高识别率的实操手段**（按性价比排序）：
1. 用 `Season 01` 而不是 `S01`（官方明确要求）
2. 年份补在片名后：`片名 (2020)`
3. 加 provider id：`片名 (2020) [tmdbid-12345]` —— **这一条对中文/日文片名几乎是最有效的**，因为它绕过了标题模糊匹配
4. 剧集补齐 `SxxExx`（不要只写集号）
5. Specials 用描述性名称而非 `S00E01`（官方建议）
6. 目录结构规范化：`Shows/剧名 (年)/Season NN/`，不要在剧集根目录散放视频

---

### 2.3 元数据源（Metadata Sources）

#### 2.3.1 官方 provider 清单

**核心内置（随服务端发布，无需装插件）** —— 官方文档 <https://jellyfin.org/docs/general/server/metadata/> 逐字：
> 默认随 Jellyfin 提供 3 个 provider：**The Movie Database (TMDb)**、**The Open Movie Database API (OMDb API)**、**Local .nfo files**。

- OMDb 脚注逐字：**"OMDb API only provides English metadata."**
- 更多官方 provider 在插件目录里 —— 逐字："There are more official providers available in our Plugin Catalog, like **TheTVDB, fanart.tv or AniDB**."
- **中国大陆提示**（逐字）："Because of external factors, certain metadata providers may not be accessible in mainland China." 官方列出不可访问清单：**The Movie Database (TMDb)**、**TheTVDB**。

**官方 Metadata 类插件**（<https://jellyfin.org/docs/general/server/plugins/>）：`Anilist`、`Anidb`、`Anisearch`、`Bookshelf`、`Kitsu`、`Fanart`；另 `TMDb Box Sets`（按 TMDb collection 自动建合集，可配置最少影片数）。

**本仓库内的 provider 目录一览**（`MediaBrowser.Providers/`，说明哪些是"内置代码"）：

| 目录 | 数据源 |
|---|---|
| `Plugins/Tmdb/` | **TMDB（内置在服务端仓库里）** ← 见 §2.3.3 详解 |
| `Plugins/Omdb/` | OMDb（IMDb 数据） |
| `Plugins/MusicBrainz/` | MusicBrainz |
| `Plugins/AudioDb/` | TheAudioDB |
| `Plugins/StudioImages/` | 制片公司 logo |
| `Plugins/ListenBrainz/` | 收听记录 |
| `Books/GoogleBooks/`、`Books/ComicVine/`、`Books/ComicBookInfo/`、`Books/ComicInfo/`、`Books/OpenPackagingFormat/`、`Books/Isbn/` | 图书/漫画 |
| `Movies/`、`TV/`、`Music/`、`MusicGenres/`、`People/`、`BoxSets/`、`Folders/`、`Genres/`、`Studios/`、`Years/`、`Photos/`、`Playlists/`、`Lyric/`、`LiveTv/`、`MediaInfo/`、`Subtitles/`、`Trickplay/` | 各类本地/派生 provider |

**本地元数据（非在线）**：`MediaBrowser.LocalMetadata/`（Jellyfin 自有 XML）与 `MediaBrowser.XbmcMetadata/`（Kodi NFO）——见 §2.6。

#### 2.3.2 provider ID 语法（权威）

官方文档 <https://jellyfin.org/docs/general/server/metadata/identifiers>，逐字要点：

- 支持的 provider **只有 3 个**：`TMDB`、`TVDB`、`OMDB`（标注 "English Only"）。
- 括号形式：**"Metadata identifiers support square brackets `[]`, parentheses `()`, and curly braces `{}`."**
- 别名：`[tmdbid-569094]` ⇔ `[tmdb-569094]`；`[tvdbid-266189]` ⇔ `[tvdb-266189]`；`[imdbid-tt9362722]` ⇔ `[imdb-tt9362722]`
- 可同时写多个：`Best_Movie_Ever (1994) [tmdbid-680] [imdbid-1234]`

**源码侧的对应实现**（`MediaBrowser.Model/Entities/ProviderIdsExtensions.cs` / `MetadataProvider` 常量）：

| 常量 | 值 | 说明 |
|---|---|---|
| `MetadataProvider.Tmdb` | `"Tmdb"` | |
| `MetadataProvider.Imdb` | `"Imdb"` | |
| `MetadataProvider.Tvdb` | `"Tvdb"` | |
| `MetadataProvider.MusicBrainzArtist/Album/ReleaseGroup/Track` | `"MusicBrainz*"` | |
| `MetadataProvider.AniDb`、`MetadataProvider.AniList`、`MetadataProvider.Kitsu`、`MetadataProvider.Zap2It`、`MetadataProvider.TvRage`、`MetadataProvider.AudioDbArtist/Album`、`MetadataProvider.Bokshelf`、`MetadataProvider.ComicVine` | 各插件 | |

> 命名解析与 provider id 的桥接在 `MediaBrowser.Controller/Providers/*LookupInfo`（如 `MovieInfo` 的 `ProviderIds`）——`Emby.Naming` 抽出的 `[tmdbid-...]` 会写进 `LookupInfo.ProviderIds`，`ProviderManager` 见到 id 就**跳过搜索直接按 id 拉取**。

#### 2.3.3 TMDB provider 详解（**内置在服务端仓库**）

文件位置：`MediaBrowser.Providers/Plugins/Tmdb/`

| 文件 | 作用 |
|---|---|
| `TmdbClientManager.cs` | 所有 TMDB API 调用的封装 + **内存缓存** |
| `TmdbUtils.cs` | 语言/图片语言归一化、标题匹配打分、provider id 解析 |
| `TmdbExternalUrlProvider.cs` | 生成 TMDB 外链 |
| `Configuration/PluginConfiguration.cs` | 插件配置（含 **`TmdbApiKey`**） |
| `Api/TmdbController.cs` | 插件自己的 API（设置/测试 key） |
| `Movies/TmdbMovieProvider.cs`、`TmdbMovieImageProvider.cs`、`TmdbMovieExternalId.cs`、`TmdbMovieSimilarProvider.cs` | 电影 |
| `TV/TmdbSeriesProvider.cs`、`TmdbSeasonProvider.cs`、`TmdbEpisodeProvider.cs`、`TmdbMissingEpisodeProvider.cs`、`TmdbUpcomingEpisodesTask.cs` | 剧集/季/集 |
| `TV/Tmdb*ImageProvider.cs`、`TV/Tmdb*ExternalId.cs` | 剧集的图片与外部 id |
| `BoxSets/` | 合集 |
| `People/TmdbPersonProvider.cs` | 人物 |

**（a）API 调用与语言参数**

用的是 NuGet 库 `TMDbLib`（不是自己拼 URL），封装在 `TmdbClientManager.cs`。**每个取数方法都接收 `language`、`imageLanguages`、`countryCode` 三个参数**，因此"元数据语言"和"图片语言"是**分开传给 TMDB** 的：

| 方法 | 行 | TMDB 调用要点 |
|---|---|---|
| `GetMovieAsync(int tmdbId, string? language, string? imageLanguages, string? countryCode, ...)` | `:67` | `_tmDbClient.GetMovieAsync(..., TmdbUtils.NormalizeLanguage(language, countryCode), ...)`（`:83-85`） |
| `GetCollectionAsync(int tmdbId, ...)` | `:107` | 合集 |
| `GetSeriesAsync(int tmdbId, ...)` | `:141` | `language: TmdbUtils.NormalizeLanguage(language, countryCode)`、`includeImageLanguage: imageLanguages`（`:159-160`） |
| `GetSeriesGroupAsync(int tvShowId, string displayOrder, ...)` | `:182` | 按 `displayOrder` 取分组（支持 airdate/dvd/absolute） |
| `GetSeasonAsync(int tvShowId, int seasonNumber, ...)` | `:238` | `includeImageLanguage: imageLanguages`（`:252`） |
| `GetEpisodeAsync(int tvShowId, int seasonNumber, long episodeNumber, string displayOrder, ...)` | `:276` | 先取 group 再定位集（`:286`） |
| `GetPersonAsync(int personTmdbId, string language, ...)` | `:324` | 人物 |

**内存缓存键**（很重要，避免重复请求）：
```csharp
var key = $"movie-{tmdbId}-{language}";        // :69
var key = $"collection-{tmdbId}-{language}";   // :109
var key = $"series-{tmdbId}-{language}";       // :143
var key = $"group-{tvShowId}-{displayOrder}-{language}";              // :199
var key = $"season-{tvShowId}-s{seasonNumber}-{language}";            // :240
var key = $"episode-{tvShowId}-s{seasonNumber}e{episodeNumber}-{displayOrder}-{language}";  // :278
var key = $"person-{personTmdbId}-{language}"; // :326
```
⇒ **缓存键含语言但不含 countryCode**（一个已知的粗粒度点）。

**（b）API Key 处理**（**这是"API Key 泄露"问题的关键**）

`TmdbClientManager.cs:50-51`：
```csharp
var apiKey = Plugin.Instance.Configuration.TmdbApiKey;
apiKey = string.IsNullOrEmpty(apiKey) ? TmdbUtils.ApiKey : apiKey;
```

`TmdbUtils.cs`：
```csharp
public const string ApiKey = "4219e299c89411838049ab0dab19ebd5";
public const string BaseTmdbUrl = "https://www.themoviedb.org/";
public const string ProviderName = "TheMovieDb";
public const string OriginalImageSize = "original";
```

> **重要事实**：Jellyfin 的 TMDB 插件**内置了一个公开的默认 API Key**（硬编码在上面的常量里），用户不填也能用；用户填了 `TmdbApiKey` 就用自己的。
> **对自研实现的启示**：这个 key 是**公开且共享**的，TMDB 侧随时可能限流/封禁。**你的应用应该让用户自备 key**，不要去"借用"这个字符串（既不道德也不可靠）。同时注意：**绝不能把 key 打进客户端分发包或提交到 Git**——这是需求里点名的坑（见 §3.8）。

**（c）语言归一化（`TmdbUtils.NormalizeLanguage`，`:432-464`）**——**这是中文用户最容易踩坑的一段，逐字**：

```csharp
public static string? NormalizeLanguage(string? language, string? countryCode = null)
{
    if (string.IsNullOrEmpty(language)) return language;

    // Handle es-419 (Latin American Spanish) by converting to regional variant
    if (string.Equals(language, "es-419", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrEmpty(countryCode))
    {
        language = string.Equals(countryCode, "AR", StringComparison.OrdinalIgnoreCase) ? "es-AR" : "es-MX";
    }

    // TMDb requires this to be uppercase
    // Everything after the hyphen must be written in uppercase due to a way TMDb wrote their API.
    // See here: https://www.themoviedb.org/talk/5119221d760ee36c642af4ad?page=3#56e372a0c3a3685a9e0019ab
    var parts = language.Split('-');

    if (parts.Length == 2)
    {
        // TMDb doesn't support Switzerland (de-CH, it-CH or fr-CH) so use the language (de, it or fr) without country code
        if (string.Equals(parts[1], "CH", StringComparison.OrdinalIgnoreCase))
        {
            return parts[0];
        }

        language = parts[0] + "-" + parts[1].ToUpperInvariant();
    }

    return language;
}
```

要点：
1. **TMDB 要求 `xx-YY` 的语言子标签必须大写**（`zh-CN` 而不是 `zh-cn`）——注释里给了 TMDB 官方讨论帖链接作为依据。
2. **瑞士特例**：`de-CH`/`it-CH`/`fr-CH` → 去掉国家码，只留 `de`/`it`/`fr`。
3. **拉美西语特例**：`es-419` + 国家码 → `es-AR` 或 `es-MX`（其他一律 `es-MX`）。
4. **中文没有特殊处理**——即 `zh-CN`/`zh-TW`/`zh-HK` 会原样传给 TMDB（只做大写化）。TMDB 对 `zh-CN`/`zh-TW`/`zh-HK` 都支持，但**繁体/简体不会互相回退**（回退由 Jellyfin 的 "English fallback" 承担，见下）。

**（d）图片语言回退链（`TmdbUtils.GetImageLanguagesParam`，`:405-423`）**——逐字：

```csharp
public static string GetImageLanguagesParam(string preferredLanguage, string? countryCode = null)
{
    var languages = new List<string>();

    if (!string.IsNullOrEmpty(preferredLanguage))
    {
        preferredLanguage = NormalizeLanguage(preferredLanguage, countryCode);
        languages.Add(preferredLanguage);
    }

    languages.Add("null");       // ★ TMDB 的 "无语言" 图片（通常是纯图形 logo/无字海报）

    // Always add English as fallback language
    if (!string.Equals(preferredLanguage, "en", StringComparison.OrdinalIgnoreCase))
    {
        languages.Add("en");
    }

    return string.Join(',', languages);
}
```

⇒ **图片语言顺序固定为：`<首选语言>, null, en`**。例如 `metadata_language=zh-CN` 时传给 TMDB 的 `include_image_language` = `"zh-CN,null,en"`。
**这个三段式回退非常值得照抄**：先要本地语言图片 → 再要"无语言"图（往往是干净的无字海报/logo） → 最后英文兜底。

**（e）标题匹配打分（`TmdbUtils.FindBestMatch`）**——避免"搜到同名片"：

```csharp
private const int TitleExactScore = 8;
private const int TitlePrefixScore = 4;
private const int YearExactScore = 2;
private const int YearAdjacentScore = 1;
```
- 比较前先 `NormalizeTitle`：`NonComparableRegex = [^\p{L}\p{N}\p{M}]+` → 替换为空格 → `Trim().ToLowerInvariant()`（即**去掉所有标点、统一小写**，对中日韩文字友好，因为 `\p{L}` 涵盖汉字/假名）
- 搜索关键词前先 `CleanName`：`NonSearchTermRegex = [^\p{L}\p{N}\p{M}·]+` → 替换为空格 → `Trim()`（注意**保留了 `·`**，因为中文译名常用间隔号）
- 评分：标题完全匹配 +8，前缀匹配 +4，年份完全相同 +2，年份相邻 +1

**（f）人物/剧组白名单**（`TmdbUtils.cs`）：
```csharp
public static readonly string[] WantedCrewTypes = { PersonType.Director, PersonType.Writer, PersonType.Producer };
public static readonly PersonKind[] WantedCrewKinds = { PersonKind.Director, PersonKind.Writer, PersonKind.Producer };
private static readonly FrozenSet<string> _writerJobs = new[] { "writer", "screenplay", "novel" }.ToFrozenSet(StringComparer.OrdinalIgnoreCase);
```
⇒ **只取 Director / Writer / Producer 三类剧组人员**（`_writerJobs` 把 TMDB 的 `screenplay`/`novel` 也归入 Writer）。

**（g）插件配置项**（`Configuration/PluginConfiguration.cs`，含默认值）：
```csharp
public string TmdbApiKey { get; set; } = string.Empty;      // ★ 空则用内置 key
public bool IncludeAdult { get; set; }                       // 是否包含成人内容
public bool ExcludeTagsSeries { get; set; }
public bool ExcludeTagsMovies { get; set; }
public bool ImportSeasonName { get; set; }
public bool ImportUnairedEpisodes { get; set; }
public bool ImportMissingEpisodes { get; set; }
public bool ImportSpecials { get; set; }
public string[] EnabledMissingEpisodeLibraries { get; set; } = [];
public int MissingEpisodeRefreshIntervalDays { get; set; } = 7;
public int UpcomingEpisodeGracePeriodDays { get; set; } = 7;
public int MaxCastMembers { get; set; } = 15;                // ★ 演员上限 15
public int MaxCrewMembers { get; set; } = 15;                // ★ 剧组上限 15
public bool HideMissingCastMembers { get; set; }
public bool HideMissingCrewMembers { get; set; }
public string? PosterSize { get; set; }                      // ★ 图片尺寸可配
public string? BackdropSize { get; set; }
public string? LogoSize { get; set; }
public string? ProfileSize { get; set; }
public string? StillSize { get; set; }
public int SimilarItemsCacheDays { get; set; } = 90;
```

> **对自研实现的启示**：`MaxCastMembers`/`MaxCrewMembers` 默认都只有 **15**。自研时如果一次拉 100 个演员，DB 写入和 UI 渲染都会明显变慢；建议同样设上限（或按 UI 需要分页）。

#### 2.3.4 OMDb / 其他源（简要）

| 源 | 位置 | 说明 |
|---|---|---|
| OMDb | `MediaBrowser.Providers/Plugins/Omdb/` | 提供 IMDb 数据（**仅英文**，见官方文档脚注）；也用于把 IMDb id 补齐 |
| MusicBrainz | `Plugins/MusicBrainz/` | 音乐元数据；`ProviderIds` 键为 `MusicBrainzArtist`/`MusicBrainzAlbum`/`MusicBrainzReleaseGroup`/`MusicBrainzTrack` |
| TheAudioDB | `Plugins/AudioDb/` | 音乐补充 |
| StudioImages | `Plugins/StudioImages/` | 制片公司 logo |
| GoogleBooks / ComicVine / OpenLibrary | `Books/` 下 | 图书、漫画 |
| fanart.tv / TheTVDB / AniDB / AniList / Kitsu / AniSearch | **外部插件仓库**（不在本仓库） | 需从插件目录安装 |

**关于 TVDB / fanart.tv / AniDB 的具体字段映射**：这些实现在**外部插件仓库**（`jellyfin/jellyfin-plugin-tvdb`、`-fanart`、`-anidb` 等）中，**本仓库不含其代码**，因此本报告不给出它们的字段映射细节（避免臆测）。所需的接口契约是 `MediaBrowser.Controller/Providers/IRemoteMetadataProvider` 系列（见 §2.7）。

---

### 2.4 图片（Images）

#### 2.4.1 `ImageType` 枚举（完整，含数值）

`MediaBrowser.Model/Entities/ImageType.cs`：

```csharp
public enum ImageType
{
    Primary = 0,  Art = 1,  Backdrop = 2,  Banner = 3,  Logo = 4,  Thumb = 5,
    Disc = 6,  Box = 7,  Screenshot = 8,  Menu = 9,  Chapter = 10,  BoxRear = 11,  Profile = 12
}
```

#### 2.4.2 官方文件名优先级表

见 §2.1.6（已按官方文档逐字列出）。补充规则：
- **本地图优先于在线图**：官方逐字 —— "When external images are provided, they should be placed alongside the media files. **When they are provided, they will take precedence over other sources.**"
- **NFO 里的图片路径优先级最高**：官方逐字 —— "Artwork defined in .nfo files with local paths or URLs have priority over remote image providers and images in the media folders."
- **每种类型只保留一张**（NFO 场景）：官方逐字 —— "Jellyfin supports only one image for each artwork type. This means that only the first `thumb` tag for each artwork type is used."

#### 2.4.3 默认下载数量与最小宽度（**官方源码默认值，文档里没有**）

`MediaBrowser.Model/Configuration/TypeOptions.cs` 里的 `DefaultImageOptions`。结构是 `ImageOption { ImageType Type; int Limit = 1; int MinWidth; }`。

| 条目类型 | Backdrop | Primary | Logo | Thumb | Banner | Art | Disc |
|---|---|---|---|---|---|---|---|
| `Movie` | **Limit=1, MinWidth=1280** | Limit=1 | Limit=1 | Limit=1 | Limit=0 | Limit=0 | Limit=0 |
| `Series` | **Limit=1, MinWidth=1280** | Limit=1 | Limit=1 | Limit=1 | Limit=1 | Limit=0 | — |
| `Season` | Limit=0, MinWidth=1280 | Limit=1 | — | Limit=0 | Limit=0 | — | — |
| `Episode` | Limit=0, MinWidth=1280 | Limit=1 | — | — | — | — | — |
| `MusicVideo` | Limit=1, MinWidth=1280 | Limit=1 | Limit=1 | Limit=1 | Limit=0 | Limit=0 | Limit=0 |
| `BoxSet` | Limit=1, MinWidth=1280 | Limit=1 | Limit=1 | Limit=1 | Limit=0 | Limit=0 | Limit=0 |
| `MusicAlbum` | Limit=0, MinWidth=1280 | — | — | — | — | — | Limit=0 |
| `MusicArtist` | Limit=1, MinWidth=1280 | — | Limit=1 | — | Limit=0 | Limit=0 | — |

> **可直接照搬的默认策略**：**Primary 1 张、Backdrop 1 张且最小宽度 1280、Logo 1 张**。这套默认值是"够用且省流量"的平衡点。`MinWidth = 1280` 意味着宽度不足 1280 的背景图会被**跳过**（这就是"图片尺寸过多/过小"问题的官方解法）。

相关配置项（`ServerConfiguration`）：
| 配置 | 默认 | 说明 |
|---|---|---|
| `MetadataPath` | `""` | "Specify a custom location for downloaded artwork and metadata" |
| `ImageSavingConvention` | — | 枚举 `Legacy` \| `Compatible`（官方 UI 文案："Save artwork into media folders" / "Save metadata and images as hidden files"） |
| `ChapterImageResolution` | `ImageResolution.MatchSource` | 章节图分辨率；枚举 `MatchSource, P144, P240, P360, P480, P720, P1080, P1440, P2160` |
| `ParallelImageEncodingLimit` | `0`（未设） | 并行图片编码上限；0 表示按 CPU 核数决定 |
| `ImageExtractionTimeoutMs` | — | 内嵌图抽取超时 |

官方文档佐证："Only JPEG and PNG files are supported."（UI 文案 `MessageImageFileTypeAllowed`）；章节图的官方说明："These images are used in a small preview window and will never take up the full screen, so setting a high resolution here is not necessary."

#### 2.4.4 图片落盘位置（**两套机制**）

**（a）随媒体保存（`saveLocally = true`）**
条件（`MediaBrowser.Providers/Manager/ImageSaver.cs:93`）：
```csharp
var saveLocally = item.SupportsLocalMetadata && item.IsSaveLocalMetadataEnabled() && !item.ExtraType.HasValue
                  && (item is AudioBook || item is not Audio);
```
路径选择在 `GetSavePaths(item, type, imageIndex, mimeType, saveLocally)`（`:339`）与 `GetSavePathForItemInMixedFolder`（`:425-631`），生成的文件名就是 §2.1.6 表格里的那些（`poster.jpg`、`fanart.jpg`、`logo.png`、`thumb.jpg`、`banner.jpg`、`landscape.jpg`、`disc.jpg`、`clearart.png`、`<name>-thumb.jpg` 等）；扩展名兜底 `extension = ".jpg"`（`:408`）。

**（b）不随媒体保存（`saveLocally = false`）→ 落到"内部元数据目录"**

`BaseItem.GetInternalMetadataPath()`（`MediaBrowser.Controller/Entities/BaseItem.cs:920-937`）：
```csharp
public virtual string GetInternalMetadataPath()
{
    var basePath = ConfigurationManager.ApplicationPaths.InternalMetadataPath;
    return GetInternalMetadataPath(basePath);
}

protected virtual string GetInternalMetadataPath(string basePath)
{
    if (SourceType == SourceType.Channel)
        return Path.Join(basePath, "channels", ChannelId.ToString("N"), Id.ToString("N"));

    ReadOnlySpan<char> idString = Id.ToString("N");
    return Path.Join(basePath, "library", idString[..2], idString);      // ★ <InternalMetadataPath>/library/<id前2位>/<id>
}
```
图片文件名在 `ImageSaver.cs:562`：`Path.Combine(item.GetInternalMetadataPath(), filename + extension)`。

**（c）其它派生图片的目录**

| 内容 | 路径 | 出处 |
|---|---|---|
| 章节图 | `Path.Combine(item.GetInternalMetadataPath(), "chapters")` | `PathManager.cs:108`（`GetChapterImageFolderPath`） |
| 章节图文件名 | `{item.DateModified.Ticks}_{chapterPositionTicks}.jpg` | `PathManager.cs`（`GetChapterImagePath`） |
| Trickplay | `<DataPath>/trickplay/<id前2位>/<id>/<width> - <tileW>x<tileH>/<index>.jpg` | 见 §1.6.2 |
| 字幕缓存 | `<DataPath>/subtitles/<id前2位>/<id>/<streamIndex><ext>` | 见 §1.4.6 |
| 附件/字体 | `<DataPath>/attachments/<id前2位>/<id>` | 见 §1.4.6 |
| 转码临时 | `<TranscodingTempPath>`（默认在 DataPath 下的 transcodes） | `EncodingOptions.TranscodingTempPath` |
| **图片缓存** | 官方 UI 有 "Image Cache" 项 | **未在源码中定位到独立"图片缓存"目录实现** → 见附录 B |

> **对自研实现的启示**：`<root>/library/<id前2位>/<id>/` 这种"两级散列 + 完整 GUID"的布局值得照抄——单个目录的文件数可控（不会因几万条媒体把目录撑爆），且天然可定位（不需要查表就能从 id 算出路径）。

#### 2.4.5 图片来源（fetcher）

| 来源 | 实现 | 说明 |
|---|---|---|
| 本地文件 | `MediaBrowser.LocalMetadata` / `MediaBrowser.XbmcMetadata` | `poster.jpg`/`fanart.jpg` 等，优先级最高 |
| 内嵌图片 | `EmbeddedImageProvider` | MKV 内嵌封面；官方文档里叫 **`Embedded Image Extractor`** 源 |
| TMDB | `TmdbMovieImageProvider` / `TmdbSeriesImageProvider` / `TmdbSeasonImageProvider` / `TmdbEpisodeImageProvider` | 见 §2.3.3 |
| 其它插件 | fanart.tv / TheTVDB 等外部插件 | 不在本仓库 |
| 制片公司 logo | `MediaBrowser.Providers/Plugins/StudioImages/` | |
| 截帧 | `MediaBrowser.Providers/MediaInfo/` 里的视频截帧 provider | 用于 Thumb/Backdrop 缺失时 |

**TMDB 图片尺寸可配**（`PluginConfiguration`）：`PosterSize`、`BackdropSize`、`LogoSize`、`ProfileSize`、`StillSize`（均可空，空则用 TMDB 默认）；常量 `TmdbUtils.OriginalImageSize = "original"`。
**图片语言链**：`GetImageLanguagesParam` = `<首选语言>,null,en`（见 §2.3.3(d)）。

---

### 2.5 评分与分级

#### 2.5.1 三个评分/分级字段（**类型完全不同，别混**）

| 字段 | C# 类型 | 含义 | 存储 |
|---|---|---|---|
| `OfficialRating` | `string` | **分级**（如 `PG-13`、`TV-14`、`R`） | DB 列 `BaseItemEntity.OfficialRating`（`BaseEntity.cs:600` → `BaseItemMapper.cs:89/:280`） |
| `CustomRating` | `string` | **用户自定义分级**，优先于 `OfficialRating` | DB 列（`BaseItem.cs:620` → `:85/:275`） |
| `CommunityRating` | `float?` | **社区/用户评分**（0–10 归一化） | DB 列（`BaseItem.cs:665` → `:84/:274`） |
| `CriticRating` | `float?` | **影评人评分** | DB 列（`BaseItem.cs:613` → `:102/:293`） |

**重要**：`OfficialRating` 与 `CustomRating` 是 **string**（直接存 "PG-13" 这类文本），不是数值、不是外键。**没有独立的"分级表"**。
`CustomRating` 的继承逻辑在 `BaseItem.CustomRatingForComparison`（`BaseItem.cs:700-727`）：本条目没设时**向上查父级**。

#### 2.5.2 值域：0–10 还是 0–100？

- `CommunityRating` / `CriticRating` 是 `float?`，**没有枚举约束、没有 CHECK**。
- TMDB 的 `vote_average` 原生就是 **0–10**（一位小数），Jellyfin 直接存入，**不做 ×10 换算**。
- 因此：**约定值域是 0–10**（与 TMDB 一致）；IMDb 也是 0–10；**RottenTomatoes 的 0–100 需要自行除以 10**（Jellyfin 核心不含 RT provider；RT 数据通常来自 OMDb 的 `Ratings` 数组，由 OMDb 插件处理）。

> **诚实标注**：我**没有**在源码中找到对 `CommunityRating` 值域的显式校验或注释说明"必须 0–10"。上面的"约定 0–10"是基于 TMDB `vote_average` 的原生范围 + 字段类型推断的，**属于推断而非源码事实**，见附录 B。

#### 2.5.3 官方分级（Official Rating）的来源与"各国认证"

- `OfficialRating` 由各 provider 从**元数据源的国家/地区分级字段**取来。TMDB 侧的对应字段是 `release_dates`/`content_ratings`（按国家给出认证），Jellyfin 依据 `metadata_country_code` 选取对应国家的认证。
- **官方文档说明**（`/docs/general/post-install/setup-wizard`）："Select a preferred language and **region** for metadata fetching as the server-wide default."
- 服务端字段：`ServerConfiguration.MetadataCountryCode`（默认 **`"US"`**）、`LibraryOptions.MetadataCountryCode`。
- **注意**：本节涉及"TMDB 具体取哪个 endpoint 的哪个字段"的部分，我**没有**逐行读完 `TmdbMovieProvider.cs` 的赋值链（它用的是 TMDbLib 的强类型对象），因此**不给出"哪个 endpoint 的哪个 JSON 字段"级别的断言**，见附录 B。

#### 2.5.4 语言与地区配置（**关键更正**）

> **更正**：需求里提到的 `metadata_language` / `metadata_country_code` **不是 Jellyfin 的配置项名**，官方文档全站也**从未出现**这两个字面量（已用文档仓库全文 grep 验证：对 `metadata_language`、`metadata_country`、`MetadataCountryCode` 均 **0 命中**）。
> **实际字段名**（源码，已亲自核实）：
> - `ServerConfiguration.PreferredMetadataLanguage` = **`"en"`**（`MediaBrowser.Model/Configuration/ServerConfiguration.cs:103`）
> - `ServerConfiguration.MetadataCountryCode` = **`"US"`**（`:109`）
> - 库级覆盖：`LibraryOptions.PreferredMetadataLanguage`（`:80`）、`LibraryOptions.MetadataCountryCode`（`:86`）—— 都是 `string?`
> - 条目级覆盖：`BaseItem.PreferredMetadataLanguage`（`BaseItem.cs:127`）、`BaseItem.PreferredMetadataCountryCode`（`:130`），且**是 DB 列**（`BaseItemEntity.cs:67/:69`）

**官方文档的唯一出处**（`/docs/general/post-install/setup-wizard`，逐字）：
> "Select a preferred language and region for metadata fetching as the server-wide default. **Metadata from other language / regions may be fetched if metadata is not available with your preferred settings. This can be further customized on a per-library basis.**"

即官方明确说明：**首选语言取不到时会回退到其他语言**（这就是"语言回退"的官方依据）。

**注意**：我**没有**在服务端源码中找到 `LibraryOptions.MetadataDownloadLanguages`（grep 零命中）—— 某个二手资料提到过这个字段名，**在本版本中不存在**，见附录 B。
`LibraryOptions.SeasonZeroDisplayName` 默认 **`"Specials"`**（ctor 中显式赋值）。

---

### 2.6 本地元数据优先（NFO 与同名文件）

#### 2.6.1 官方对"NFO 优先级"的最强表述（逐字）

> **"It's currently not possible to disable .nfo metadata. Local metadata will always be fetched and has priority over remote metadata providers like TMDb."**
> —— <https://jellyfin.org/docs/general/server/metadata/nfo>

> **注意**：现代 Jellyfin **没有**名为 "Prefer local metadata" 的开关。本地 NFO **始终**被读取且**始终**优先。这与需求里的假设不同，务必按实际写。

其它两条逐字：
- "Artwork defined in .nfo files with local paths or URLs have priority over remote image providers and images in the media folders."
- "User data importing is only possible for a single user. This user can be set in the .nfo settings."（对应 `XbmcMetadataOptions.UserId`）

#### 2.6.2 NFO 文件名（**源码实现，比文档更精确**）

来自 `MediaBrowser.XbmcMetadata/Savers/*.cs` 的 `GetLocalSavePath`：

| 类型 | 文件名 | 源码 |
|---|---|---|
| Series | `Path.Combine(item.Path, "tvshow.nfo")` | `SeriesNfoSaver.cs:42` |
| Season | `Path.Combine(item.Path, "season.nfo")` | `SeasonNfoSaver.cs:41` |
| Episode | `Path.ChangeExtension(item.Path, ".nfo")`（即 `<剧集文件名>.nfo`） | `EpisodeNfoSaver.cs:42` |
| MusicArtist | `Path.Combine(item.Path, "artist.nfo")` | `ArtistNfoSaver.cs:43` |
| MusicAlbum | `Path.Combine(item.Path, "album.nfo")` | `AlbumNfoSaver.cs:44` |
| Movie | 见下方多候选逻辑 | `MovieNfoSaver.cs:45` |

**电影 NFO 的候选顺序**（`MovieNfoSaver.GetMovieSavePaths`，`:47-69`，逐字）：
```csharp
internal static IEnumerable<string> GetMovieSavePaths(ItemInfo item)
{
    var path = item.ContainingFolderPath;
    if (item.VideoType == VideoType.Dvd && !item.IsPlaceHolder)
        yield return Path.Combine(path, "VIDEO_TS", "VIDEO_TS.nfo");

    // only allow movie object to read movie.nfo, not owned videos (which will be itemtype video, not movie)
    if (!item.IsInMixedFolder && item.ItemType == typeof(Movie))
        yield return Path.Combine(path, "movie.nfo");

    if (!item.IsPlaceHolder && (item.VideoType == VideoType.Dvd || item.VideoType == VideoType.BluRay))
        yield return Path.Combine(path, Path.GetFileName(path) + ".nfo");
    else
        yield return Path.ChangeExtension(item.Path, ".nfo");
}
```
`GetLocalSavePath` 取 `FirstOrDefault() ?? Path.ChangeExtension(item.Path, ".nfo")`。

⇒ **优先级（从高到低）**：`VIDEO_TS/VIDEO_TS.nfo` → `movie.nfo`（仅当"不在混合目录"且条目类型是 `Movie`）→ `<目录名>.nfo`（DVD/BD）或 `<视频文件名>.nfo`。

**读取侧**（`Providers/*NfoProvider.cs`）与写入侧一致：`SeriesNfoProvider.cs:61`（`tvshow.nfo`）、`SeasonNfoProvider.cs:61`（`season.nfo`）、`EpisodeNfoProvider.cs:62`（`Path.ChangeExtension(info.Path, ".nfo")`）、`ArtistNfoProvider.cs:61`（`artist.nfo`）、`AlbumNfoProvider.cs:61`（`album.nfo`）。
另有 `SeriesNfoSeasonProvider.cs:69/:79`：季目录下没有 `tvshow.nfo` 时会**向上到剧集目录**找。

#### 2.6.3 NFO 的读取/写入标签

官方两张表（读取 / 写入）已在 §2.3 引用的文档页中；**关键逐字规则**：
- **同名标签冲突时"后出现的赢"**："If there are multiple tags that map to the same internal Jellyfin data like `plot` and `review`, the last of these tags in the file will have priority."
- **provider id 标签**：读取侧 `"<PROVIDER_NAME" + "id>"`（如 `tmdbid`、`imdbid`、`tvdbid`）；写入侧 "Additional provider ids are supported as well. They will be exported with the tag `<providernameid>`."
- **可用 IMDb/TMDb/TVDb 链接帮助识别**："You can also use your .nfo files to help Jellyfin identify your media. You can just enter an IMDb, TMDb or TVDb link, to link the media to the specific provider id."
- **开启写入**：在库设置里勾选 **`Nfo saver`**（官方逐字）。
- `art` 标签仅在启用 "save image paths" 时写入（`XbmcMetadataOptions.SaveImagePathsInNfo`，**默认 `true`**）。

**`XbmcMetadataOptions` 默认值**（`MediaBrowser.Model/Configuration/XbmcMetadataOptions.cs`）：
| 配置 | 默认 |
|---|---|
| `ReleaseDateFormat` | `"yyyy-MM-dd"` |
| `SaveImagePathsInNfo` | `true` |
| `EnablePathSubstitution` | `true` |
| `UserId` | （空） |
| `EnableExtraThumbsDuplication` | — |

#### 2.6.4 本地图片与字幕

- **图片**：见 §2.1.6 + §2.4.4(a)。外挂图片**优先于其它来源**。
- **字幕同名文件**：`<视频文件名>.<flag>.<lang>.<ext>`，flag/语言解析见 §2.2.7；字幕扩展名白名单 `SubtitleFileExtensions` = `.ass .mks .sami .smi .srt .ssa .sub .sup .vtt`。
- **歌词**：`<音轨文件名>.lrc` / `.elrc` / `.txt`（官方逐字："Lyrics must be contained in the same folder and match the filename for their corresponding item."）。
- **图书**：`content.opf`、`metadata.opf`、`ComicInfo.xml`。

#### 2.6.5 库级开关（`LibraryOptions`，已亲自核实字段名与默认值）

```csharp
public bool SaveLocalMetadata { get; set; }                       // UI: "Save artwork into media folders"
public string[]? MetadataSavers { get; set; }                     // UI: "Metadata savers"
public string[] DisabledLocalMetadataReaders { get; set; }
public string[]? LocalMetadataReaderOrder { get; set; }           // UI: "Metadata readers"（"The first file found will be read."）
public bool EnableInternetProviders { get; set; }                  // 关掉后完全不联网抓
public bool EnableAutomaticSeriesGrouping { get; set; } = true;
public bool EnableEmbeddedTitles { get; set; }
public bool EnableEmbeddedExtrasTitles { get; set; }
public bool EnableEmbeddedEpisodeInfos { get; set; }
public int AutomaticRefreshIntervalDays { get; set; }
public bool EnableChapterImageExtraction { get; set; }
public bool ExtractChapterImagesDuringLibraryScan { get; set; }
public bool EnableTrickplayImageExtraction { get; set; }
public bool ExtractTrickplayImagesDuringLibraryScan { get; set; }
public bool SaveTrickplayWithMedia { get; set; }                    // ctor 显式 false
public bool SaveSubtitlesWithMedia { get; set; }                    // ctor 显式 true
public bool SaveLyricsWithMedia { get; set; }                       // ctor 显式 false
public string SeasonZeroDisplayName { get; set; } = "Specials";     // ctor 显式赋值
public string[] DisabledSubtitleFetchers / SubtitleFetcherOrder;
public string[]? SubtitleDownloadLanguages;
public bool SkipSubtitlesIfEmbeddedSubtitlesPresent { get; set; }
public bool SkipSubtitlesIfAudioTrackMatches { get; set; } = true;   // ctor
public bool RequirePerfectSubtitleMatch { get; set; } = true;        // ctor
public EmbeddedSubtitleOptions AllowEmbeddedSubtitles { get; set; } = AllowAll;  // ctor
public string[] DisabledLyricFetchers / LyricFetcherOrder;
public bool PreferNonstandardArtistsTag { get; set; } = false;
public bool UseCustomTagDelimiters { get; set; } = false;
public string[] CustomTagDelimiters = ["/", "|", ";", "\\"];         // ctor 的 _defaultTagDelimiters
public string[] DelimiterWhitelist { get; set; }
public bool AutomaticallyAddToCollection { get; set; } = false;
public MediaPathInfo[] PathInfos { get; set; }
public TypeOptions[] TypeOptions { get; set; }                       // ★ 承载 §2.4.3 的图片默认值
```

**按条目类型的 fetcher 开关**在 `MetadataOptions`（`MediaBrowser.Model/Configuration/MetadataOptions.cs`）——**注意它不在 `LibraryOptions` 上，而是通过 `TypeOptions`/`MetadataOptions` 按类型配置**：

```csharp
public class MetadataOptions
{
    public string ItemType { get; set; }
    public string[] DisabledMetadataSavers { get; set; }
    public string[] LocalMetadataReaderOrder { get; set; }
    public string[] DisabledMetadataFetchers { get; set; }
    public string[] MetadataFetcherOrder { get; set; }
    public string[] DisabledImageFetchers { get; set; }
    public string[] ImageFetcherOrder { get; set; }
}
```
（ctor 里六个数组全部初始化为空数组。）

**默认启用情况**（官方文档/源码侧）：Movie/Series/Season/Episode/BoxSet/Book **不禁用任何 fetcher**；`MusicVideo` 禁用 `"The Open Movie Database"` 的 metadata 与 image fetcher；`MusicAlbum`/`MusicArtist` 禁用 `"TheAudioDB"` 的 metadata fetcher。

---

### 2.7 刷新与增量

#### 2.7.1 `MetadataRefreshMode` 枚举（完整，含数值）

`MediaBrowser.Controller/Providers/MetadataRefreshMode.cs`：

```csharp
public enum MetadataRefreshMode
{
    None = 0,
    ValidationOnly = 1,
    Default = 2,
    FullRefresh = 3
}
```

语义（结合使用点）：
| 值 | 含义 |
|---|---|
| `None` (0) | 不刷新该层面（metadata 或 image）。`ItemRefreshController` 的默认值就是 `None` |
| `ValidationOnly` (1) | 只做校验（存在性/有效性），不重取内容 |
| `Default` (2) | 常规刷新：**只补缺失字段**，已有值不覆盖（`MetadataRefreshOptions` ctor 的默认值就是 `Default`） |
| `FullRefresh` (3) | 全量重取并覆盖；`ImageRefreshOptions.IsReplacingImage(type)` 要求 `ImageRefreshMode == FullRefresh` 才会替换图片 |

#### 2.7.2 `MetadataRefreshOptions` 与 `ImageRefreshOptions`（完整字段）

`MediaBrowser.Controller/Providers/MetadataRefreshOptions.cs`（继承 `ImageRefreshOptions`）：

```csharp
public class MetadataRefreshOptions : ImageRefreshOptions
{
    // 构造默认：MetadataRefreshMode = MetadataRefreshMode.Default
    public bool ReplaceAllMetadata { get; set; }
    public bool RegenerateTrickplay { get; set; }
    public MetadataRefreshMode MetadataRefreshMode { get; set; }
    public RemoteSearchResult SearchResult { get; set; }        // 手动识别时带候选结果
    public string[] RefreshPaths { get; set; }                  // 只刷新指定路径
    public bool ForceSave { get; set; }
    public bool EnableRemoteContentProbe { get; set; }
    public bool IsAutomated { get; set; }                       // 来自基类，ctor 默认 true

    public bool RefreshItem(BaseItem item)
    {
        if (RefreshPaths is not null && RefreshPaths.Length > 0)
            return RefreshPaths.Contains(item.Path ?? string.Empty, StringComparison.OrdinalIgnoreCase);
        return true;
    }
}
```

`MediaBrowser.Controller/Providers/ImageRefreshOptions.cs`：
```csharp
public class ImageRefreshOptions
{
    // ctor: ImageRefreshMode = MetadataRefreshMode.Default; ReplaceImages = Array.Empty<ImageType>(); IsAutomated = true;
    public MetadataRefreshMode ImageRefreshMode { get; set; }
    public IDirectoryService DirectoryService { get; private set; }
    public bool ReplaceAllImages { get; set; }
    public IReadOnlyList<ImageType> ReplaceImages { get; set; }
    public bool IsAutomated { get; set; }
    public bool RemoveOldMetadata { get; set; }

    public bool IsReplacingImage(ImageType type)
        => ImageRefreshMode == MetadataRefreshMode.FullRefresh && (ReplaceAllImages || ReplaceImages.Contains(type));
}
```

> **关键语义**：`RemoveOldMetadata` 只在 `MetadataRefreshOptions` 上（继承自基类），图片替换必须 `ImageRefreshMode == FullRefresh`。
> **注意**：需求里提到的 `EnableRemoteRefresh` / `EnableLocalRefresh` / `EnableThumbnailImageExtraction` / `EnableTrickplayImageExtraction` **不在 `MetadataRefreshOptions` 上**（我已通读该类全文）。`EnableTrickplayImageExtraction` 是 `LibraryOptions` 的字段（见 §2.6.5），不是刷新选项。见附录 B。

#### 2.7.3 刷新 API 的粒度

| 粒度 | API | 说明 |
|---|---|---|
| **全库** | `POST /Library/Refresh` | **无参数**，需管理员（`Policies.RequiresElevation`），返回 204。**没有"只刷某个库"的参数** |
| **单项** | `POST /Items/{itemId}/Refresh` | 需管理员；参数见下 |
| **部分路径** | `POST /Library/Media/Updated`（body `MediaUpdateInfoDto`） | 通知服务端"这些路径变了"，用于增量扫描 |
| **按外部 id** | `POST /Library/Movies/Added` / `Updated`（`tmdbId`/`imdbId`）、`POST /Library/Series/Added` / `Updated`（`tvdbId`） | 用于外部工具（如 Sonarr/Radarr）通知新增 |

**`POST /Items/{itemId}/Refresh` 的逐字签名**（`Jellyfin.Api/Controllers/ItemRefreshController.cs:58-68`，**类级路由 `[Route("Items")]` + `[Authorize(Policy = Policies.RequiresElevation)]`**）：

```csharp
[HttpPost("{itemId}/Refresh")]
[Description("Refreshes metadata for an item.")]
[ProducesResponseType(StatusCodes.Status204NoContent)]
[ProducesResponseType(StatusCodes.Status404NotFound)]
public ActionResult RefreshItem(
    [FromRoute, Required] Guid itemId,
    [FromQuery] MetadataRefreshMode metadataRefreshMode = MetadataRefreshMode.None,
    [FromQuery] MetadataRefreshMode imageRefreshMode = MetadataRefreshMode.None,
    [FromQuery] bool replaceAllMetadata = false,
    [FromQuery] bool replaceAllImages = false,
    [FromQuery] bool regenerateTrickplay = false)
```

行为（`:70-92`）：
```csharp
// GetItemById -> null 则 NotFound()
ForceSave        = metadataRefreshMode == FullRefresh || imageRefreshMode == FullRefresh || replaceAllImages || replaceAllMetadata;  // :82-85
RemoveOldMetadata = replaceAllMetadata;        // :87
RegenerateTrickplay = regenerateTrickplay;     // :88
ProviderManager.QueueRefresh(item.Id, refreshOptions, RefreshPriority.High);   // :91
return NoContent();
```

> **注意**：这是**入队**（`QueueRefresh`）而非同步执行，所以返回 204 只代表"已排队"。客户端若要等待结果，需要轮询 `GET /Items/{itemId}?fields=RefreshState` 或看 `DateLastRefreshed`。

**`POST /Library/Refresh` 逐字**（`Jellyfin.Api/Controllers/LibraryController.cs:337-340`）：
```csharp
[HttpPost("Library/Refresh")]
[Authorize(Policy = Policies.RequiresElevation)]
[ProducesResponseType(StatusCodes.Status204NoContent)]
public async Task<ActionResult> RefreshLibrary()
```
（类级 `[Route("")]`，因此完整路径就是 `POST /Library/Refresh`。）

#### 2.7.4 provider 架构与优先级

| 接口 | 作用 |
|---|---|
| `ILocalMetadataProvider<T>` | **本地**元数据（NFO/XML），`Order` 决定顺序 |
| `IRemoteMetadataProvider<T>` | **在线**元数据（TMDB/OMDb/TVDB…） |
| `IForcedProvider` | 当条目带 provider id 时"强制按 id 拉取"，跳过搜索 |
| `IExternalId` | 声明 provider 的 id 键名/名称/类型/URL 模板 |
| `IImageProvider` / `IRemoteImageProvider` | 图片 |
| `IMetadataService` / `ProviderManager` | 调度：按 `Order` 依次调用，先到先得 |

**优先级规则的准确表述**：
1. **本地 > 在线**（官方逐字："Local metadata will always be fetched and has priority over remote metadata providers like TMDb."）
2. **在线 provider 之间**按库设置里的 `MetadataFetcherOrder` 排序；**低优先级的只用来"填坑"**（官方 UI 文案逐字："Lower priority downloaders will only be used to fill in missing information."）
3. **本地 reader 之间**按 `LocalMetadataReaderOrder`；"**The first file found will be read.**"（官方 UI 文案逐字）
4. 条目级 `LockedFields`（`MetadataField[]`）可**锁定字段**，刷新时不覆盖。`MetadataField` 枚举成员（`MediaBrowser.Model/Entities/MetadataField.cs`）：`Cast, Genres, ProductionLocations, Studios, Tags, Name, Overview, Runtime, OfficialRating`
5. 条目级 `IsLocked`（实体的 `LockData` 只存在于 DTO）可整体锁定

#### 2.7.5 并发、限流与重试

**本地实现的机制**（源码）：
1. **入库刷新走队列**：`ProviderManager.QueueRefresh(itemId, options, priority)`，带 `RefreshPriority`（如 `High`）。库扫描也走同一个队列 → **全库刷新不会瞬间打爆在线 API**。
2. **TMDB 客户端有内存缓存**：见 §2.3.3(a) 的缓存键（`movie-{id}-{lang}` 等）——同一影片重复刷新不会重复请求。
3. **字幕抽取有信号量锁**：`_semaphoreLocks.LockAsync(outputPath, ...)`（`SubtitleEncoder.cs:337`、`:530`），**按输出路径加锁**，避免并发抽取同一文件。
4. **附件抽取有异步键控锁**：`AsyncKeyedLocker<string>`（`AttachmentExtractor.cs:33-37`，键为输出目录/路径）。
5. **转码有并发上限**：`MediaBrowser.Model/Configuration/` 下的转码并发配置 + `TranscodingJob` 管理（`TranscodeManager`）。

**限流（rate limiting）**：
> **诚实标注**：我**没有**在 `TmdbClientManager` 或 `ProviderManager` 中找到针对 TMDB 的**显式速率限制器**（如令牌桶/QPS 节流）或**失败重试**逻辑。TMDB 的 429 处理依赖 `TMDbLib` 内部行为，我**未验证**。这是一个明确的空白点，见附录 B。

**官方文档侧**（关于"刷新多少内容才生效"，逐字 UI 文案）：
> "Changing metadata settings will affect new content added going forward. To refresh existing content, open the detail screen and click the **'Refresh'** button, or do bulk refreshes using the **'Metadata Manager'**."

**自动刷新间隔**：`LibraryOptions.AutomaticRefreshIntervalDays`（库级，天数）；TMDB 插件另有 `MissingEpisodeRefreshIntervalDays = 7`、`SimilarItemsCacheDays = 90`。
**定时任务**：`Emby.Server.Implementations/ScheduledTasks/Tasks/RefreshMediaLibraryTask.cs`（`Key = "RefreshLibrary"` 一类），以及 `MediaSegmentExtractionTask`、`ChapterImagesTask`、`DeleteTranscodeFileTask`（24h）等。

---

### 2.8 数据模型

#### 2.8.1 架构前提（**这一版是新 EF Core 数据层，和老资料差异很大**）

- 唯一 DbContext：`src/Jellyfin.Database/Jellyfin.Database.Implementations/JellyfinDbContext.cs`
- **条目主表是单一的 `BaseItems` 表**（`DbSet<BaseItemEntity> BaseItems`，`JellyfinDbContext.cs:114`），**不是**每种类型一张表。
- 实体 `src/Jellyfin.Database/.../Entities/BaseItemEntity.cs`
- `src/Jellyfin.Database/.../Entities/Libraries/*`（`Movie.cs`/`Series.cs`/`Person.cs`/`Track.cs` 等）是**未启用的实验性实体**——`JellyfinDbContext.cs:176-258` 整段 DbSet 被注释掉。**不要把 `Libraries/Person.cs` 当作 `People` 的存储。**
- **条目对象有两条持久化路径**（`Jellyfin.Server.Implementations/Item/BaseItemMapper.cs`）：
  1. **关系列**：显式映射到 `BaseItemEntity` 的标量列；
  2. **JSON blob**：若类型**没有** `[RequiresSourceSerialisation]` 属性，整个对象被 JSON 序列化进 `BaseItemEntity.Data`（`BaseItemMapper.cs:266-269` 写、`:511-521` 读）：
     ```csharp
     entity.Data = JsonSerializer.Serialize(dto, dtoType, JsonDefaults.Options);
     ...
     public static bool TypeRequiresDeserialization(Type type)
         => type.GetCustomAttribute<RequiresSourceSerialisationAttribute>() == null;
     ```
  - **带该属性（不写 JSON，只靠列）的类型**：`Season`、`Person`、`Genre`、`Studio`、`Year`、`PhotoAlbum`、`Book`、`AudioBook`、`MusicGenre`、`MusicArtist`、`MusicAlbum`、`LiveTvProgram`、`PlaylistsFolder`
  - **不带该属性（非 `[JsonIgnore]` 属性全部进 `Data` JSON）**：`Movie`、`Series`、`Episode`、`Video`、`Audio`、`Folder`、`BoxSet`、`Trailer`、`MusicVideo`、`Photo`

> **对自研实现的启示**：Jellyfin 用"单表 + 关系列 + JSON blob"的混合模型，好处是加字段不用改 schema，坏处是**没法用 SQL 直接查 JSON 里的字段**。自研用 SQLite 时，**不建议照抄这个混合模型**——建议对常用查询字段（`type`、`year`、`rating`、`genres` 等）建正常列 + 索引，只把极少查询的杂项塞 JSON。

#### 2.8.2 `BaseItem` 关键字段表

类声明 `MediaBrowser.Controller/Entities/BaseItem.cs:45`：
```csharp
public abstract class BaseItem : IHasProviderIds, IHasLookupInfo<ItemLookupInfo>, IEquatable<BaseItem>
```
（**`BaseItem` 没有 partial 分文件**；全部 3090 行在一个文件里。）

| 属性 | C# 类型 | 含义 | 存储 | 位置 |
|---|---|---|---|---|
| `Id` | `Guid` | 主键 | **列** | `:239`（`[JsonIgnore]`） |
| `Name` | `string` | 显示名；setter 清 `_sortName` 缓存 | **列** | `:200-210` |
| `OriginalTitle` | `string` | 原始语言标题 | **列** | `:225` |
| `SortName` | `string` | 排序名；getter 懒计算 | **列 + 可计算** | `:540-561` |
| `ForcedSortName` | `string` | 用户强制排序名 | **列** | `:525-533` |
| `Path` | `string` | 路径（虚拟路径会展开/还原） | **列** | `:271` |
| `FileName` | — | **不存在**；只有 `FileNameWithoutExtension`（计算） | 计算 | `:388-399` |
| `Overview` | `string` | 简介 | **列** | `:627` |
| `Tagline` | `string` | 宣传语 | **列** | `:137` |
| `ProductionYear` | `int?` | 年份 | **列** | `:679` |
| `PremiereDate` | `DateTime?` | 首播/首映 | **列** | `:586` |
| `EndDate` | `DateTime?` | 结束日 | **列** | `:593` |
| `OfficialRating` | `string` | **分级** | **列** | `:600` |
| `CustomRating` | `string` | 自定义分级 | **列** | `:620`（继承比较 `:700-727`） |
| `CommunityRating` | `float?` | 社区评分 | **列** | `:665` |
| `CriticRating` | `float?` | 影评人评分 | **列** | `:613` |
| `RunTimeTicks` | `long?` | 时长 | **列** | `:672` |
| `IndexNumber` | `int?` | 集号/轨号 | **列** | `:687` |
| `ParentIndexNumber` | `int?` | 季号/碟号 | **列** | `:694` |
| `IsFolder` | `bool`（`virtual`） | 基类恒 `false`，`Folder` 恒 `true` | **列 + 计算** | `:803` / `Folder.cs:107` |
| `IsVirtualItem` | `bool` | 无实体文件的虚拟条目 | **列** | `:143` |
| `DateCreated` | `DateTime` | 入库时间 | **列（可空，`MinValue`→NULL）** | `:429` |
| `DateLastMediaAdded` | `DateTime?` | 最后新增媒体（**声明在 `Folder`**） | **列** | `Folder.cs:85` |
| `DateModified` | `DateTime` | 最后修改 | **列（可空）** | `:436` |
| `DateLastSaved` | `DateTime` | 最后保存 | **列 + JSON** | `:438` |
| `DateLastRefreshed` | `DateTime` | 最后元数据刷新 | **列** | `:441` |
| `PreferredMetadataLanguage` | `string` | 条目级语言覆盖 | **列** | `:127` |
| `PreferredMetadataCountryCode` | `string` | 条目级地区覆盖 | **列** | `:130` |
| `ProviderIds` | `Dictionary<string,string>` | 外部 id | **独立表 `BaseItemProviders`** | `:734` |
| `Tags` | `string[]` | 标签（`\|` 拼接） | **列 + `ItemValues`** | `:648` |
| `Genres` | `string[]` | 类型/流派 | **列 + `ItemValues`** | `:641` |
| `Studios` | `string[]` | 制片公司 | **列 + `ItemValues`** | `:634` |
| `People` | — | **此版本 `BaseItem` 上已无此属性**；人物走仓储 | **多对多表** | `:790`（仅 `SupportsPeople`） |
| `MediaSources` | — | **非实体属性**，`GetMediaSources(bool)` 运行期构造 | 计算 | `:1156-1190` |
| `MediaStreams` | — | **非实体属性**，经 `MediaSourceManager` | **表 `MediaStreamInfos` + 访问器** | `:1143-1149` |
| `Chapters` | — | **非实体属性**，经 `IChapterManager` | **表 `Chapters` + 访问器** | `:2442`、`:2481` |
| `ExtraType` | `ExtraType?` | 附加类型 | **列** | `:219` |
| `Container` | `string` | 容器格式 | **无列 → JSON** | `:134` |
| `RemoteTrailers` | `IReadOnlyList<MediaUrl>` | 远程预告片 | **无列 → JSON** | `:812` |
| `LockedFields` | `MetadataField[]` | 锁定的字段 | **表 `BaseItemMetadataFields`** | `:451` |
| `IsLocked` | `bool` | 整体锁定（DTO 层叫 `LockData`） | **列** | `:444` |

**另有若干"只写不读"的历史列**（`BaseItemMapper.cs:226-230` 注释直说 `Fields that are present in the DB but are never actually used`）：`CleanName`、`UnratedType`、`MediaType`、`TopParentId`。

#### 2.8.3 具体条目类的额外字段

**`Movie`**（`MediaBrowser.Controller/Entities/Movies/Movie.cs`，125 行）：
```csharp
public class Movie : Video, IHasSpecialFeatures, IHasTrailers, IHasLookupInfo<MovieInfo>, ISupportsBoxSetGrouping
public string TmdbCollectionName { get; set; }        // :39  TMDb 合集名
public string CollectionName { get; set; }            // :42-46 别名，转发 TmdbCollectionName
public IReadOnlyList<Guid> SpecialFeatureIds         // :26-29 计算
public IReadOnlyList<BaseItem> LocalTrailers          // :33 计算
```
`Movie` **没有** `PreferredMetadataLanguage`/`PreferredMetadataCountryCode` —— 这两个在 `BaseItem` 上（见上表）。

**`Series`**（`TV/Series.cs`，572 行）：
```csharp
public class Series : Folder, IHasTrailers, IHasDisplayOrder, IHasLookupInfo<SeriesInfo>, IMetadataContainer, ISupportsBoxSetGrouping
public DayOfWeek[] AirDays { get; set; }     // :34
public string AirTime { get; set; }          // :36
public string DisplayOrder { get; set; }     // :63  注释逐字："Valid options are airdate, dvd or absolute"
public SeriesStatus? Status { get; set; }    // :69
```
`SeriesStatus`（`MediaBrowser.Model/Entities/SeriesStatus.cs`，隐式）：`Continuing=0, Ended=1, Unreleased=2`。

**`Season`**（`TV/Season.cs`，297 行；**带 `[RequiresSourceSerialisation]`**）：
```csharp
[RequiresSourceSerialisation]                              // :24
public class Season : Folder, IHasSeries, IHasLookupInfo<SeasonInfo>
public Guid SeriesId { get; set; }                         // :87  ★ DB 列（真实外键）
public string SeriesName { get; set; }                     // :84  反规范化缓存列
public string SeriesPresentationUniqueKey { get; set; }    // :81  列
public override Guid DisplayParentId => SeriesId;          // :43  计算
public string SeriesPath { get; }                          // :65-78 计算
```
`IHasSeries` 接口（`MediaBrowser.Controller/Entities/IHasSeries.cs`）：`SeriesName`、`SeriesId`、`SeriesPresentationUniqueKey` + `FindSeriesName()`/`FindSeriesSortName()`/`FindSeriesId()`/`FindSeriesPresentationUniqueKey()`。

**`Episode`**（`TV/Episode.cs`，386 行）：
| 成员 | 类型 | 存储 | 位置 |
|---|---|---|---|
| `AirsBeforeSeasonNumber` | `int?` | **JSON** | `:37` |
| `AirsAfterSeasonNumber` | `int?` | **JSON** | `:39` |
| `AirsBeforeEpisodeNumber` | `int?` | **JSON** | `:41` |
| `IndexNumberEnd` | `int?` | **JSON** | `:47` |
| `AiredSeasonNumber` | `int?` | 计算（`AirsAfterSeasonNumber ?? AirsBeforeSeasonNumber ?? ParentIndexNumber`） | `:59` |
| `IsInSeasonFolder` | `bool` | 计算 | `:105` |
| `IsMissingEpisode` | `bool` | 计算（`LocationType == Virtual`） | `:131` |
| `SeasonId` / `SeriesId` | `Guid` | **列** | `:134` / `:137` |
| `SeriesName` / `SeasonName` | `string` | **列** | `:111` / `:114` |
| `SeriesPresentationUniqueKey` | `string` | **列** | `:108` |
| `ContainsEpisodeNumber(int)` | `bool` | 计算 | `:253` |

> **注意**：需求里假设的 `IsMissing` **不在** `Episode` 上（实体上是 `IsMissingEpisode`；`IsMissing` 只存在于查询对象 `InternalItemsQuery.cs:210`，对应 `GET /Items?isMissing=`）。

**`Video`**（`Entities/Video.cs`，886 行；Movie/Episode/Trailer/MusicVideo 的基类）：
| 成员 | 存储 |
|---|---|
| `PrimaryVersionId` | **列**（多版本归组的关键外键） |
| `AdditionalParts`（`string[]`） | JSON |
| `LocalAlternateVersions`（`string[]`） | JSON |
| `LinkedAlternateVersions`（`LinkedChild[]`） | 由 `LinkedChildren` 表还原 |
| `Timestamp` / `SubtitleFiles` / `AudioFiles` / `HasSubtitles` / `IsPlaceHolder` / `DefaultVideoStreamIndex` / `VideoType` / `IsoType` / `Video3DFormat` / `AspectRatio` | JSON |
| `IsStacked`（`AdditionalParts.Length > 0`）/ `Is3D` / `MediaSourceCount` / `MediaType` / `SourceType` | 计算 |
| `SupportsPositionTicksResume` | 计算：`Sample`/`ThemeVideo`/`Trailer` → `false` |
| `GetAllVersions()` / `GetAlternateVersion(Guid)` / `GetAdditionalPartIds()` / `GetAdditionalParts(User)` | 方法 |

`VideoType`（隐式）：`VideoFile=0, Iso=1, Dvd=2, BluRay=3`。

**`BoxSet`**（`Movies/BoxSet.cs`，293 行）：`DisplayOrder`（`:47`，`IHasDisplayOrder`）、`LibraryFolderIds`（`:71`，`GetLibraryFolderIds()` `:250-267`）、`LocalTrailers`（`:41`）、`SupportsPeople => true`（`:37`）。

**`Folder`**（`Folder.cs:43`）：`IsRoot`（`:56`）、`LinkedChildren`（`:62`，**独立表 `LinkedChildren`**）、`DateLastMediaAdded`（`:85`）、`IsFolder => true`（`:107`）、`Children`（`:137`）、`GetChildren`/`GetRecursiveChildren`（`:1511`/`:1612`）、`GetLinkedChildren`（`:1688`）。

**音频三兄弟**：
- `Audio`（`Audio/Audio.cs:20`）：`Artists`（`:36`，**列**，`|` 分隔）、`AlbumArtists`（`:40`，**列**）、`AlbumEntity`（`:61`，计算）、`HasLyrics`（`:73`）、`LyricFiles`（`:78`）
- `MusicAlbum`（`Audio/MusicAlbum.cs:27`，**`[RequiresSourceSerialisation]`**）：`AlbumArtists`（`:36`，列）、`Artists`（`:39`，列）、`AlbumArtist`（`:57`，计算 `AlbumArtists.FirstOrDefault()`）、`Tracks`（`:67`，计算）、`SupportsPlayedStatus => false`
- `MusicArtist`（`Audio/MusicArtist.cs:27`，**`[RequiresSourceSerialisation]`**）：`IsAccessedByName`（`:30`）、**`IsFolder => !IsAccessedByName`**（`:33`，同一类型 `IsFolder` 可变的原因）、静态 `GetPath(string name)`（`:75`）
- 接口（`Audio/IHasAlbumArtist.cs`）：`IHasAlbumArtist { IReadOnlyList<string> AlbumArtists }`、`IHasArtist { IReadOnlyList<string> Artists }`

#### 2.8.4 人物：`People` + `PersonKind`（**这是需求点名的部分**）

**`PersonKind` 枚举全成员 + 数值**（`Jellyfin.Data/Enums/PersonKind.cs`，138 行，`namespace Jellyfin.Data.Enums`）。
**该枚举无任何显式赋值**，数值 = 声明顺序 0..25（共 **26** 个成员）：

| # | 成员 | 值 | 行 | # | 成员 | 值 | 行 |
|---|---|---|---|---|---|---|---|
| 1 | `Unknown` | 0 | `:11` | 14 | `Creator` | 13 | `:76` |
| 2 | `Actor` | 1 | `:16` | 15 | `Artist` | 14 | `:81` |
| 3 | `Director` | 2 | `:21` | 16 | `AlbumArtist` | 15 | `:86` |
| 4 | `Composer` | 3 | `:26` | 17 | `Author` | 16 | `:91` |
| 5 | `Writer` | 4 | `:31` | 18 | `Illustrator` | 17 | `:96` |
| 6 | `GuestStar` | 5 | `:36` | 19 | `Penciller` | 18 | `:101` |
| 7 | `Producer` | 6 | `:41` | 20 | `Inker` | 19 | `:106` |
| 8 | `Conductor` | 7 | `:46` | 21 | `Colorist` | 20 | `:111` |
| 9 | `Lyricist` | 8 | `:51` | 22 | `Letterer` | 21 | `:116` |
| 10 | `Arranger` | 9 | `:56` | 23 | `CoverArtist` | 22 | `:121` |
| 11 | `Engineer` | 10 | `:61` | 24 | `Editor` | 23 | `:127` |
| 12 | `Mixer` | 11 | `:66` | 25 | `Translator` | 24 | `:132` |
| 13 | `Remixer` | 12 | `:71` | 26 | `Narrator` | 25 | `:137` |

旧的字符串常量版仍在部分代码中使用：`MediaBrowser.Model/Entities/PersonType.cs`（`static class PersonType`，12 个 `const string`：`Actor`/`Director`/`Composer`/`Writer`/`GuestStar`/`Producer`/`Conductor`/`Lyricist`/`Arranger`/`Engineer`/`Mixer`/`Remixer`）。

**`PersonInfo`**（**实际路径 `MediaBrowser.Controller/Entities/PersonInfo.cs`**，69 行；需求假设的 `MediaBrowser.Model/Entities/PersonInfo.cs` **不存在**）：
```csharp
public sealed class PersonInfo : IHasProviderIds      // :15，注意是 sealed
public Guid Id { get; set; }                          // :26  ctor 默认 Guid.NewGuid()
public Guid ItemId { get; set; }                      // :28
public string Name { get; set; }                      // :34
public string Role { get; set; }                      // :40
public PersonKind Type { get; set; }                  // :46
public int? SortOrder { get; set; }                   // :52
public string ImageUrl { get; set; }                  // :54
public Dictionary<string,string> ProviderIds { get; set; }   // :56  OrdinalIgnoreCase

public bool IsType(PersonKind type)                   // :67
    => Type == type || string.Equals(type.ToString(), Role, StringComparison.OrdinalIgnoreCase);
```
（`PrimaryImageTag` **不在** `PersonInfo` 上。）

**API 侧人物 DTO `BaseItemPerson`**（`MediaBrowser.Model/Dto/BaseItemPerson.cs`）——**客户端实际拿到的**：
`Name`(`:20`)、`Id`(`:26`)、`Role`(`:32`)、`Type`(`:39`，`[DefaultValue(PersonKind.Unknown)]`)、`PrimaryImageTag`(`:45`)、`ImageBlurHashes`(`:51`)、`HasPrimaryImage`(`:58`，`[JsonIgnore]` 计算)。
**没有** `ImageUrl`、`ProviderIds`、`SortOrder`。

**存储结构：三张表**
1. **`Peoples`**（`src/Jellyfin.Database/.../Entities/People.cs:11`）
   - `Id` `Guid` required（`:16`）
   - `Name` `string` required（`:21`）
   - **`PersonType` `string?`（`:26`）—— 注意是字符串，不是枚举**
   - `BaseItems` `ICollection<PeopleBaseItemMap>?`（`:31`）
   - **注释说明：`The Peoples table has one row per (Name, PersonType)`**
2. **`PeopleBaseItemMap`**（连接表，`PeopleBaseItemMap.cs:40`）
   - `SortOrder` `int?`（`:45`）、`ListOrder` `int?`（`:50`）、`Role` `string?`（`:55`）
   - `ItemId` `Guid` required（`:60`）+ `Item`（`:65`）
   - `PeopleId` `Guid` required（`:70`）+ `People`（`:75`）
   - 导航属性 `BaseItemEntity.Peoples`（`BaseItemEntity.cs:167`）
3. **`ItemValues` + `ItemValueMap`**（值字典 + 映射）
   - `ItemValue`：`ItemValueId`、`Type`（`ItemValueType`）、`Value`、`CleanValue`
   - `ItemValueMap`：`ItemId`、`ItemValueId`
   - `ItemValueType` 枚举（`ItemValueType.cs`）：`Artist=0`、`AlbumArtist=1`、`Genre=2`、`Studios=3`、`Tags=4`、**`InheritedTags=6`** —— **数值 5 缺失**（历史遗留空洞）

**读取人物的实际查询**（`Jellyfin.Server.Implementations/Item/PeopleRepository.cs`，513 行）：
```csharp
// GetPeople(InternalPeopleQuery filter)  :33-86
if (!filter.ItemId.IsEmpty())
{
    dbQuery = dbQuery.Include(p => p.BaseItems!.Where(m => m.ItemId == filter.ItemId))
        .OrderBy(e => e.BaseItems!.Where(m => m.ItemId == filter.ItemId).Min(m => m.ListOrder))   // ★ 按 ListOrder 排序
        .ThenBy(e => e.PersonType)
        .ThenBy(e => e.Name);
}
// 未指定 ItemId 时按小写姓名去重
// 最后 dbQuery.AsEnumerable().SelectMany(MapCredits)

// Map(People people, PeopleBaseItemMap? mapping)  :371-386
var personInfo = new PersonInfo() { Id = people.Id, Name = people.Name, Role = mapping?.Role, SortOrder = mapping?.SortOrder };
if (Enum.TryParse<PersonKind>(people.PersonType, out var kind)) personInfo.Type = kind;
```
- `GetPeopleByItems(IReadOnlyList<Guid> itemIds)`（`:314-358`）：**批量查询**，避免 N+1：
  ```csharp
  context.PeopleBaseItemMap.AsNoTracking().WhereOneOrMany(itemIds, m => m.ItemId).OrderBy(m => m.ListOrder)
  ```
- 批量填充进 DTO：`Emby.Server.Implementations/Dto/DtoService.cs:251-256`（`if (options.ContainsField(ItemFields.People)) peopleBatch = _libraryManager.GetPeopleByItems(peopleItemIds);`）、`:363`、`:995`
- 写入去重键：**（小写姓名 + PersonType + 小写 Role）三元组**（`UpdatePeople`，`:122-129`）
- 门面：`LibraryManager.GetPeople(BaseItem item)`（`Emby.Server.Implementations/Library/LibraryManager.cs:3602-3618`）—— `if (item.SupportsPeople) { ... } return [];`

> **对自研实现的启示**：
> ① **用两张表（person + credit 连接表）而不是把演员塞进主表**——Jellyfin 的 `Peoples` + `PeopleBaseItemMap` 就是这么做的，好处是同一演员可复用、可按 `ListOrder` 保序；
> ② `ListOrder` 与 `SortOrder` **是两个不同字段**（一个用于"在演员表里的显示顺序"，一个是业务排序），自研时至少要有一个保序字段，否则演员顺序会乱；
> ③ **务必做批量查询**（`GetPeopleByItems`），否则列表页 N 部影片会变成 N+1 次查询——Jellyfin 专门为这个写了批处理接口并有单测锁定行为。

#### 2.8.5 `MediaStream` 与 `MediaSourceInfo`

**路径更正**：`MediaSourceInfo` 实际在 **`MediaBrowser.Model/Dto/MediaSourceInfo.cs`**（263 行），**不是** `MediaBrowser.Model/Entities/`。`MediaStream` 在 `MediaBrowser.Model/Entities/MediaStream.cs`（897 行）。

**`MediaStreamType`**（`MediaBrowser.Model/Entities/MediaStreamType.cs`，**隐式**）：
```csharp
Audio = 0, Video = 1, Subtitle = 2, EmbeddedImage = 3, Data = 4, Lyric = 5
```
DB 侧 `MediaStreamTypeEntity` 有**显式同值**定义，映射为直接强转（`MediaStreamRepository.cs:201`）。

**`MediaStream` 关键字段**（`Type`/`Index`/`Codec`/`Language`/`IsDefault`/`IsForced`/`IsExternal`/`IsHearingImpaired`/`IsOriginal`/`Title`/`DisplayTitle`/`Width`/`Height`/`BitRate`/`BitDepth`/`Channels`/`ChannelLayout`/`SampleRate`/`Profile`/`Level`/`PixelFormat`/`RefFrames`/`IsInterlaced`/`IsAVC`/`IsAnamorphic`/`AspectRatio`/`AverageFrameRate`/`RealFrameRate`/`ReferenceFrameRate`/`NalLengthSize`/`CodecTag`/`TimeBase`/`CodecTimeBase`/`ColorSpace`/`ColorTransfer`/`ColorPrimaries`/`Rotation`/`Hdr10PlusPresentFlag`/`DvVersionMajor`/`DvVersionMinor`/`DvProfile`/`DvLevel`/`RpuPresentFlag`/`ElPresentFlag`/`BlPresentFlag`/`DvBlSignalCompatibilityId`/`Score`/`DeliveryMethod`/`DeliveryUrl`/`IsExternalUrl`/`SupportsExternalStream`/`Path`/`Comment`）。

**不落库的计算属性**（重要，别试图存它们）：`VideoRange`、`VideoRangeType`、`VideoDoViTitle`、`AudioSpatialFormat`、`DisplayTitle`、`ReferenceFrameRate`、`IsTextSubtitleStream`、`IsPgsSubtitleStream`、`IsVobSubSubtitleStream`、`IsExtractableSubtitleStream`、`Localized*`（运行期按语言填充）。
**不落库但运行期决策的字段**：`Score`、`DeliveryMethod`、`DeliveryUrl`、`IsExternalUrl`、`SupportsExternalStream`、`ColorRange`、`PacketLength`。

相关枚举：
- `VideoRange`：`Unknown=0, SDR=1, HDR=2`
- `VideoRangeType`（隐式 0..12）：`Unknown, SDR, HDR10, HLG, DOVI, DOVIWithHDR10, DOVIWithHLG, DOVIWithSDR, DOVIWithEL, DOVIWithHDR10Plus, DOVIWithELHDR10Plus, DOVIInvalid, HDR10Plus`
- `AudioSpatialFormat`：`None=0, DolbyAtmos=1, DTSX=2`

**DB 表 `MediaStreamInfos`**（`src/Jellyfin.Database/.../Entities/MediaStreamInfo.cs`）列清单：`ItemId`、`StreamIndex`、`StreamType`、`Codec`、`Language`、`ChannelLayout`、`Profile`、`AspectRatio`、`Path`、`IsInterlaced`(`bool?`)、`BitRate`、`Channels`、`SampleRate`、`IsDefault`、`IsForced`、`IsExternal`、`IsOriginal`、`Height`、`Width`、`AverageFrameRate`、`RealFrameRate`、`Level`(`float?`)、`PixelFormat`、`BitDepth`、`IsAnamorphic`、`RefFrames`、`CodecTag`、`Comment`、`NalLengthSize`、`IsAvc`、`Title`、`TimeBase`、`CodecTimeBase`、`ColorPrimaries`、`ColorSpace`、`ColorTransfer`、`DvVersionMajor`、`DvVersionMinor`、`DvProfile`、`DvLevel`、`RpuPresentFlag`、`ElPresentFlag`、`BlPresentFlag`、`DvBlSignalCompatibilityId`、`IsHearingImpaired`(`bool?`)、`Rotation`、`KeyFrames`、`Hdr10PlusPresentFlag`。

**`MediaSourceInfo`（运行期构造，非持久化实体）** 关键字段：
`Protocol`、`Id`、`Path`、`EncoderPath`、`EncoderProtocol`、`Type`、`Container`、`Size`、`Name`、`IsRemote`、`ETag`、`RunTimeTicks`、`ReadAtNativeFramerate`、`IgnoreDts`、`IgnoreIndex`、`GenPtsInput`、**`SupportsTranscoding`**、**`SupportsDirectStream`**、**`SupportsDirectPlay`**、`IsInfiniteStream`、`UseMostCompatibleTranscodingProfile`、`RequiresOpening`、`OpenToken`、`RequiresClosing`、`LiveStreamId`、`BufferMs`、`RequiresLooping`、`SupportsProbing`、`VideoType`、`IsoType`、`Video3DFormat`、`MediaStreams`、`MediaAttachments`、`Formats`、`Bitrate`、`FallbackMaxStreamingBitrate`、`Timestamp`、`RequiredHttpHeaders`、`TranscodingUrl`、`TranscodingSubProtocol`、`TranscodingContainer`、`AnalyzeDurationMs`、**`TranscodeReasons`**、`DefaultAudioIndexSource`、`DefaultAudioStreamIndex`、`DefaultSubtitleStreamIndex`、`HasSegments`、`VideoStream`（计算）。
方法：`InferTotalBitrate(bool)`、`GetDefaultAudioStream(int?)`、`GetMediaStream(MediaStreamType, int)`、`GetStreamCount(MediaStreamType)`、`IsSecondaryAudio(MediaStream)`。

#### 2.8.6 章节（`ChapterInfo`）

DTO（`MediaBrowser.Model/Entities/ChapterInfo.cs`）：`StartPositionTicks`(`long`)、`Name`(`string?`)、`ImagePath`(`string?`)、`ImageDateModified`(`DateTime`)、`ImageTag`(`string?`)。
DB 表 `Chapters`（`src/Jellyfin.Database/.../Entities/Chapter.cs`）：`ItemId`、`ChapterIndex`、`StartPositionTicks`、`Name`、`ImagePath`、`ImageDateModified`。
**注意：DB 表里没有 `ImageTag` 列** —— 它由 `ChapterRepository.cs:110-126` 运行时算出：
```csharp
if (!string.IsNullOrEmpty(chapterInfo.ImagePath))
    chapterEntity.ImageTag = _imageProcessor.GetImageCacheTag(baseItemPath, chapterEntity.ImageDateModified);
```
**写入是"先删后全量重插"**，`ChapterIndex = i` 按数组下标（`ChapterRepository.cs:70-83`）。
**上报只在 `fields` 含 `Chapters` 时**（`DtoService.cs:1462-1465`）→ `GET /Items?fields=Chapters`。

---

### 2.9 API 速查（做第三方客户端/刮削器会用到的）

#### 2.9.1 路由前缀与鉴权（**先搞清这两点，否则所有请求都会 404/401**）

**前缀**：`BaseJellyfinApiController` 声明为
```csharp
[ApiController]
[Route("[controller]")]
```
但**大量控制器用 `[Route("")]` 覆盖**（`ItemsController.cs:34`、`LibraryController.cs:47`、`PlaystateController.cs:25`、`SessionController.cs:25`、`MediaInfoController.cs:28`、`SubtitleController.cs:40`、`DynamicHlsController.cs:38`、`TrickplayController.cs:21`、`UserLibraryController.cs:32`、`ItemUpdateController.cs:32` 等 23 个），方法是**根路径绝对路由**。

**没有任何全局路径前缀**：`grep -rn "UsePathBase"` → **0 命中**；`Jellyfin.Server/Startup.cs:248-257` 只是 `MapControllers()` + `MapHealthChecks("/health")`。所以：
- `GET /Items`（**不是** `/api/Items`，也**不是** `/emby/Items`）
- 唯一例外是 `Jellyfin.Api/Middleware/BaseUrlRedirectionMiddleware.cs`：若配置了 `BaseUrl`，请求不以它开头会被 **302 重定向**。
- Swagger UI 在 `/api-docs/swagger`（`Jellyfin.Server/Extensions/ApiApplicationBuilderExtensions.cs:50`），**但那只影响文档站，不影响 API 路径**。

**鉴权**：
- **没有全局回退策略**（`grep "FallbackPolicy|RequireAuthorization|AuthorizeFilter"` → 0 命中）；只有 `options.DefaultPolicy`，**仅在出现 `[Authorize]` 时生效**。
- 认证方式：`Authorization: MediaBrowser Token="<token>"`（旧式）或 `Authorization: Bearer <token>`；也可用 `?ApiKey=<token>` 查询参数（字幕/图片 URL 就是这么传的）。
- 管理员策略 `Policies.RequiresElevation`（`Jellyfin.Server/Extensions/ApiServiceCollectionExtensions.cs:86-89`）：
  ```csharp
  policy => policy.AddAuthenticationSchemes(AuthenticationSchemes.CustomAuthentication)
                  .RequireClaim(ClaimTypes.Role, UserRoles.Administrator)
  ```
- ⚠️ **几个"看起来该鉴权但没有 `[Authorize]`"的端点**（源码层面未声明，实际还受 IP 校验中间件 `UseIPBasedAccessValidation()` 约束）：`GET /Videos/{itemId}/stream`、`GET /Videos/{itemId}/stream.{container}`、`GET /Videos/{...}/Subtitles/{index}/Stream.{format}`。

#### 2.9.2 核心速查表

| 方法 | 路径 | 授权 | 关键参数 | 用途 |
|---|---|---|---|---|
| **GET** | `/Items` | 用户 | 见 §2.9.3（89 个参数） | **列表/搜索主入口** |
| GET | `/Items/{itemId}` | 用户 | `userId?` | 单项详情 |
| GET | `/Items/Root` | 用户 | `userId?` | 根文件夹 |
| GET | `/Items/Latest` | 用户 | `userId?`, `parentId?`, `includeItemTypes`, `fields`, `limit=20`, `groupItems=true` | 最近添加 |
| GET | **`/UserItems/Resume`** | 用户 | `userId?`, `limit?`, `mediaTypes`, `fields`, `enableUserData`, `excludeActiveSessions=false` | **继续观看** |
| GET | `/Users/{userId}/Items/Resume` | 用户 | 同上（`userId` 走路径） | 兼容别名（`[Obsolete]`，不在 OpenAPI 里） |
| GET | `/Items/Counts` | 用户 | `userId?`, `isFavorite?` | 各类计数 |
| GET | `/Items/{itemId}/Ancestors` | 用户 | `userId?` | 面包屑 |
| GET | `/Items/{itemId}/Similar` | 用户 | `userId?`, `limit?`, `fields` | 相似推荐（另有 `/Movies/{id}/Similar`、`/Shows/{id}/Similar`、`/Albums/{id}/Similar`、`/Artists/{id}/Similar`、`/Trailers/{id}/Similar`） |
| GET | `/Items/{itemId}/ThemeSongs` / `ThemeVideos` / `ThemeMedia` | 用户 | `userId?`, `inheritFromParent=false` | 主题曲/主题视频 |
| GET | `/Items/{itemId}/LocalTrailers` | 用户 | `userId?` | 本地预告片 |
| GET | `/Items/{itemId}/SpecialFeatures` | 用户 | `userId?` | 花絮 |
| GET | `/Items/{itemId}/Intros` | 用户 | `userId?` | 片头（**用于"跳过片头"的前置内容**） |
| GET | `/Items/{itemId}/Collections` | 用户 | `userId?`, `startIndex?`, `limit?`, `fields` | 所属合集 |
| GET | `/Items/{itemId}/Download` | `Policies.Download` | — | 下载原文件 |
| GET | `/Items/{itemId}/File` | 用户 | — | 直接返回文件（`video/*`,`audio/*`） |
| GET | `/Items/{itemId}/AdditionalParts` | 用户 | `userId?` | 多部分/多版本 |
| **GET/POST** | **`/Items/{itemId}/PlaybackInfo`** | 用户 | GET: `userId?`；POST: body `PlaybackInfoDto` + 15 个 `[ParameterObsolete]` 查询参数 | **播放决策入口**（见 §1.1） |
| GET | `/Playback/BitrateTest` | 用户 | `size=102400`（`[Range(1, 100_000_000)]`） | 测速 |
| **POST** | **`/Sessions/Playing`** | 用户 | body `PlaybackStartInfo` | 开始播放 |
| **POST** | **`/Sessions/Playing/Progress`** | 用户 | body `PlaybackProgressInfo` | **进度上报** |
| **POST** | **`/Sessions/Playing/Stopped`** | 用户 | body `PlaybackStopInfo` | 停止（顺带杀转码） |
| POST | `/Sessions/Playing/Ping` | 用户 | `playSessionId`（**必需**） | 转码任务续命 |
| GET | `/Sessions` | 用户 | `controllableByUserId?`, `deviceId?`, `activeWithinSeconds?` | 会话列表（含"谁在播什么"） |
| POST | `/Sessions/{sessionId}/Playing` | 用户 | `playCommand`, `itemIds`, `startPositionTicks?`, `mediaSourceId?` | **远程投屏**（"投到那台设备播"） |
| POST | `/Sessions/{sessionId}/Playing/{command}` | 用户 | `seekPositionTicks?`, `controllingUserId?` | 远程控制（PlayPause/Stop/NextTrack…） |
| POST | `/Sessions/Capabilities` | 用户 | `playableMediaTypes`, `supportedCommands`, `supportsMediaControl` | 上报客户端能力 |
| POST | `/Sessions/Capabilities/Full` | 用户 | body `ClientCapabilitiesDto` | 同上（完整） |
| POST | `/Sessions/Logout` | 用户 | — | 登出 |
| **POST** | **`/Items/{itemId}/Refresh`** | **管理员** | `metadataRefreshMode`, `imageRefreshMode`, `replaceAllMetadata`, `replaceAllImages`, `regenerateTrickplay` | **单项刮削刷新** |
| **POST** | **`/Library/Refresh`** | **管理员** | **无参数** | **全库刷新** |
| POST | `/Library/Media/Updated` | 用户 | body `MediaUpdateInfoDto` | 通知路径变更（增量） |
| POST | `/Library/Movies/Added` / `Updated` | 用户 | `tmdbId?`, `imdbId?` | 外部工具通知 |
| POST | `/Library/Series/Added` / `Updated` | 用户 | `tvdbId?` | 外部工具通知 |
| GET | `/Library/MediaFolders` | 管理员 | `isHidden?` | 媒体库文件夹 |
| GET | `/Library/PhysicalPaths` | 管理员 | — | 物理路径 |
| GET | `/Libraries/AvailableOptions` | FirstTimeSetupOrDefault | `libraryContentType?`, `isNewLibrary=false` | 库选项元数据 |
| GET | `/Items/{itemId}/MetadataEditor` | **管理员** | — | 元数据编辑器所需字段选项 |
| POST | `/Items/{itemId}` | **管理员** | body `BaseItemDto` | **更新元数据** |
| POST | `/Items/{itemId}/ContentType` | **管理员** | `contentType?` | 改内容类型 |
| DELETE | `/Items/{itemId}` | 用户 | — | 删除条目 |
| DELETE | `/Items` | 用户 | `ids`（逗号分隔） | 批量删除 |
| POST | `/UserPlayedItems/{itemId}` | 用户 | `userId?`, `datePlayed?` | 标记已看 |
| DELETE | `/UserPlayedItems/{itemId}` | 用户 | `userId?` | 标记未看 |
| POST/DELETE | `/UserFavoriteItems/{itemId}` | 用户 | `userId?` | 收藏 |
| POST/DELETE | `/UserItems/{itemId}/Rating` | 用户 | `userId?`, `likes?`（POST） | 点赞/点踩 |
| GET/POST | `/UserItems/{itemId}/UserData` | 用户 | `userId?`；POST body `UpdateUserItemDataDto` | 用户数据读写 |
| GET | `/UserItems/{itemId}/UserData` | 用户 | — | 单条目的播放位置/已看 |
| GET | **`/Videos/{itemId}/stream`** | （无） | **51 个参数**（见 §2.9.4） | **直连/转码流** |
| GET/HEAD | `/Videos/{itemId}/stream.{container}` | （无） | 同上 | 指定容器 |
| GET | `/Videos/{itemId}/master.m3u8` / `main.m3u8` / `live.m3u8` | 用户 | — | HLS |
| GET | `/Videos/{itemId}/hls1/{playlistId}/{segmentId}.{container}` | 用户 | — | HLS 分片 |
| GET/HEAD | `/Audio/{itemId}/universal` | 用户 | `container`, `audioCodec?`, `maxStreamingBitrate?`, `transcodingContainer?`, `enableRedirection=true` | **音频通用端点**（可 302 重定向到远程直链） |
| GET | `/Audio/{itemId}/master.m3u8` / `main.m3u8` | 用户 | — | 音频 HLS |
| GET | `/Audio/{itemId}/hls1/{playlistId}/{segmentId}.{container}` | 用户 | — | 音频分片 |
| GET | **`/Videos/{itemId}/Trickplay/{width}/tiles.m3u8`** | 用户 | `mediaSourceId?` | **进度预览清单** |
| GET | `/Videos/{itemId}/Trickplay/{width}/{index}.jpg` | 用户 | `mediaSourceId?` | 瓦片图 |
| GET | `/Videos/{videoId}/{mediaSourceId}/Attachments/{index}` | （无） | — | 附件（含字体）下载 |
| GET | `/MediaSegments/{itemId}` | 用户 | `includeSegmentTypes?` | **片头/片尾等片段** |
| GET | `/Videos/{itemId}/AdditionalParts` | 用户 | `userId?` | 多部分 |
| DELETE | `/Videos/{itemId}/AlternateSources` | 管理员 | — | 删除替代版本 |
| POST | `/Videos/MergeVersions` | 管理员 | `ids`（逗号分隔） | 合并多版本 |
| GET | `/Items/{itemId}/RemoteSearch/{searchProviderName}` | 管理员 | `searchTerm`, `includeDisabledProviders?` | **手动识别：搜索候选** |
| GET | `/Items/{itemId}/ExternalIdInfos` | 管理员 | — | 该条目可用的 provider 列表 |
| POST | `/Items/RemoteSearch/Apply/{itemId}` | 管理员 | `replaceAllImages?` | **手动识别：应用候选** |
| GET | `/Items/{itemId}/RemoteSearch/Subtitles/{language}` | SubtitleManagement | `isPerfectMatch?` | 远程搜字幕 |
| POST | `/Items/{itemId}/RemoteSearch/Subtitles/{subtitleId}` | SubtitleManagement | — | 下载字幕 |
| POST | `/Videos/{itemId}/Subtitles` | SubtitleManagement | body `UploadSubtitleDto` | 上传字幕 |
| DELETE | `/Videos/{itemId}/Subtitles/{index}` | 管理员 | — | 删除字幕 |
| GET | `/Videos/{itemId}/{mediaSourceId}/Subtitles/{index}/{startPositionTicks}/Stream.{format}` | （无） | `endPositionTicks?`, `copyTimestamps=false`, `addVttTimeMap=false` | **取字幕**（`format` 可 `vtt`/`srt`/`ass`/`ssa`/`json`；`js` 是 `json` 的别名；`format` 空则直接给原文件） |
| GET | `/Videos/{itemId}/{mediaSourceId}/Subtitles/{index}/subtitles.m3u8` | 用户 | `segmentLength`（**必需**） | HLS 字幕清单 |
| GET | `/FallbackFont/Fonts` | 用户 | — | 回退字体列表（20 MB 上限） |
| GET | `/FallbackFont/Fonts/{name}` | 用户 | — | 回退字体文件 |
| GET | `/Persons`、`/Genres`、`/Studios`、`/Artists`、`/Years`、`/MusicGenres` | 用户 | 各自查询参数 | 分类浏览 |
| GET | `/Shows/{seriesId}/Seasons`、`/Shows/{seriesId}/Episodes`、`/Shows/NextUp` | 用户 | — | 剧集导航 |

> **`/Shows/NextUp`** 是"下一集"的标准入口（`TvShowsController`，类级 `[Route("Shows")]`），做剧集客户端必用。
> **`/Shows/{seriesId}/Seasons`** 与 **`/Shows/{seriesId}/Episodes`** 同理。

#### 2.9.3 `GET /Items` 的重要查询参数（完整 89 个中挑常用的）

定义在 `Jellyfin.Api/Controllers/ItemsController.cs:173-261`。**分隔符不一致，这是个坑**：

| 参数 | 类型/分隔 | 说明 |
|---|---|---|
| `userId` | Guid? | 省略则取 token 用户 |
| `parentId` | Guid? | 父级 |
| `recursive` | bool? | 递归；若父是 `ICollectionFolder` 且给了 `includeItemTypes`，会 `recursive ??= true` |
| `includeItemTypes` / `excludeItemTypes` | `BaseItemKind[]` **逗号** | 如 `Movie,Series` |
| `mediaTypes` | `MediaType[]` **逗号** | `Video`/`Audio`/`Photo`/`Book` |
| `filters` | `ItemFilter[]` **逗号** | 见下方枚举 |
| `sortBy` | `ItemSortBy[]` **逗号** | 如 `SortName,ProductionYear` |
| `sortOrder` | `SortOrder[]` **逗号** | `Ascending`/`Descending` |
| `fields` | `ItemFields[]` **逗号** | **要哪些扩展字段**（`Overview`、`People`、`MediaStreams`、`MediaSources`、`Chapters`、`ProviderIds`、`Genres`、`Studios`、`Tags`、`Path`…） |
| `searchTerm` | string | 非空时先走 `ISearchManager`，内部 `Limit = limit * 3` |
| `startIndex` / `limit` | int? | 分页 |
| `enableTotalRecordCount` | bool = **true** | 关掉可显著提速（省一次 COUNT） |
| `enableImages` / `enableUserData` | bool? | 是否需要图片 URL / 用户数据（已看、播放位置） |
| `imageTypeLimit` / `enableImageTypes` | int? / `ImageType[]` | 图片裁剪 |
| `genres` / `officialRatings` / `tags` / `studios` / `artists` / `albums` | string[] **管道符 `\|`** | ⚠️ **这几个是 `PipeDelimitedCollectionModelBinder`，用 `\|` 而不是逗号！** |
| `years` / `genreIds` / `studioIds` / `personIds` / `ids` / `artistIds` / … | int[]/Guid[] **逗号** | |
| `isPlayed` / `isFavorite` / `isMissing` / `isUnaired` / `isHd` / `is4K` / `is3D` / `isLocked` / `isPlaceHolder` | bool? | |
| `minCommunityRating` / `minCriticRating` | double? | |
| `hasOverview` / `hasImdbId` / `hasTmdbId` / `hasTvdbId` / `hasSubtitles` / `hasTrailer` / `hasThemeSong` / `hasThemeVideo` / `hasSpecialFeature` / `hasParentalRating` / `hasOfficialRating` | bool? | **"有无某字段"过滤** |
| `minPremiereDate` / `maxPremiereDate` / `minDateLastSaved` / `minDateLastSavedForUser` | DateTime? | |
| `minWidth`/`minHeight`/`maxWidth`/`maxHeight` | int? | 分辨率过滤 |
| `seriesStatus` | `SeriesStatus[]` | `Continuing`/`Ended`/`Unreleased` |
| `collapseBoxSetItems` | bool? | 合集是否折叠 |
| `adjacentTo` | Guid? | 取相邻项（下一集） |
| `indexNumber` / `parentIndexNumber` | int? | 集号/季号 |
| `nameStartsWith` / `nameStartsWithOrGreater` / `nameLessThan` | string | 字母索引 |
| `audioLanguages` / `subtitleLanguages` | string[] | |
| `personTypes` | string[] | 如 `Actor` |
| `videoTypes` | `VideoType[]` | `VideoFile`/`Iso`/`Dvd`/`BluRay` |

**`ItemFilter` 枚举（显式值，注意缺 6）**：
```csharp
IsFolder=1, IsNotFolder=2, IsUnplayed=3, IsPlayed=4, IsFavorite=5, IsResumable=7, Likes=8, Dislikes=9, IsFavoriteOrLikes=10
```
（`MediaBrowser.Model/Querying/ItemFilter.cs`）

**`ItemFields` 常用值**（`MediaBrowser.Model/Querying/ItemFields.cs`）：
`AirTime`、`CanDelete`、`CanDownload`、`ChannelInfo`、`Chapters`、`Trickplay`、`ChildCount`、`CumulativeRunTimeTicks`、`CustomRating`、`DateCreated`、`DateLastMediaAdded`、`DisplayPreferencesId`、`Etag`、`ExternalUrls`、`Genres`、`ItemCounts`、`MediaSourceCount`、`MediaSources`、`OriginalTitle`、`Overview`、`ParentId`、`Path`、`People`、`PlayAccess`、`ProductionLocations`、`ProviderIds`、`PrimaryImageAspectRatio`、`RecursiveItemCount`、`Settings`、`SeriesStudio`、`SortName`、`SpecialEpisodeNumbers`、`Studios`、`Taglines`、`Tags`、`RemoteTrailers`、`MediaStreams`、`SeasonUserData`、`DateLastRefreshed`、`DateLastSaved`、`RefreshState`、`ChannelImage`、`EnableMediaSourceDisplay`、`Width`、`Height`、`ExtraIds`

> **性能要点**：`fields` 是**按需填充**的。不传 `fields=Overview,People` 就拿不到这些字段（省带宽）。但 `fields=Chapters` 与 `fields=MediaStreams` 触发额外 DB 查询（`DtoService.cs:1462-1465`），列表页不要带上。

#### 2.9.4 播放相关的关键 URL 与参数

**`GET /Videos/{itemId}/stream`**（`VideosController.cs:314`，类级 `[Route]` 缺省 → 前缀 `Videos`）：**51 个参数**，关键的有：

| 参数 | 说明 |
|---|---|
| `static` | 强制静态（不转码） |
| `params` | **旧式位置参数**（逗号分隔，见下） |
| `mediaSourceId`, `deviceId`, `playSessionId` | |
| `container`, `videoCodec`, `audioCodec`, `subtitleCodec` | 输出编码 |
| `segmentContainer`, `segmentLength`, `minSegments` | HLS 分段 |
| `startTimeTicks` | 起播位置 |
| `videoBitRate`, `audioBitRate`, `audioChannels`, `maxAudioChannels`, `audioSampleRate`, `maxAudioBitDepth` | 码率/声道 |
| `width`/`height`/`maxWidth`/`maxHeight` | 尺寸 |
| `framerate`/`maxFramerate` | 帧率 |
| `profile`/`level`/`maxRefFrames`/`maxVideoBitDepth`/`requireAvc`/`requireNonAnamorphic` | 编码约束 |
| `deInterlace`, `enableMpegtsM2TsMode`, `copyTimestamps`, `enableAutoStreamCopy`, `allowVideoStreamCopy`, `allowAudioStreamCopy` | 行为开关 |
| `subtitleStreamIndex`, **`subtitleMethod`**（`Encode`/`Embed`/`External`/`Hls`/`Drop`） | **默认 `Encode`（烧录）** |
| `transcodingMaxAudioChannels`, `cpuCoreLimit`, `transcodeReasons`, `context`, `streamOptions`, `enableAudioVbrEncoding=true` | |
| `tag`, `liveStreamId` | 缓存校验/直播 |

**`params` 的位置参数表**（`Jellyfin.Api/Helpers/StreamingHelpers.cs:403-591` 的 `switch (i)`，索引从 0 起）：

| # | 含义 | # | 含义 |
|---|---|---|---|
| 0 | `DeviceProfileId`（**忽略**） | 17 | `MaxVideoBitDepth` |
| 1 | `DeviceId` | 18 | `Profile` |
| 2 | `MediaSourceId` | 19 | （cabac，已废弃，忽略） |
| 3 | `Static`（"true"） | 20 | `PlaySessionId` |
| 4 | `VideoCodec` | 21 | （api_key，忽略） |
| 5 | `AudioCodec` | 22 | `LiveStreamId` |
| 6 | `AudioStreamIndex` | 23 | （ItemId 重复，忽略） |
| 7 | `SubtitleStreamIndex` | 24 | `CopyTimestamps`（"true"） |
| 8 | `VideoBitRate` | 25 | `SubtitleMethod`（枚举名） |
| 9 | `AudioBitRate` | 26 | `TranscodingMaxAudioChannels` |
| 10 | `MaxAudioChannels` | 27 | `EnableSubtitlesInManifest`（"true"） |
| 11 | `MaxFramerate` | 28 | `Tag` |
| 12 | `MaxWidth` | 29 | `RequireAvc`（"true"） |
| 13 | `MaxHeight` | 30 | `SubtitleCodec` |
| 14 | `StartTimeTicks` | 31 | `RequireNonAnamorphic`（"true"） |
| 15 | `Level` | 32 | `DeInterlace`（"true"） |
| 16 | `MaxRefFrames` | 33 | `TranscodeReasons` |

> `IsValidCodecName(val)` 用 `EncodingHelper.ContainerValidationRegex()` 校验；`Level` 用 `LevelValidationRegex()` 校验，不合法就**静默忽略**。

**`container` 的归一化**（`StreamingHelpers.GetContainerFileExtension`）：`mpegts` → `ts`，`matroska` → `mkv`，其它原样。

**HLS 路由**（`DynamicHlsController`，类级 `[Route("")]`）：
```
GET|HEAD /Videos/{itemId}/master.m3u8     # 主清单
GET      /Videos/{itemId}/main.m3u8       # 分片清单
GET      /Videos/{itemId}/live.m3u8       # 直播
GET      /Videos/{itemId}/hls1/{playlistId}/{segmentId}.{container}
GET|HEAD /Audio/{itemId}/master.m3u8
GET      /Audio/{itemId}/main.m3u8
GET      /Audio/{itemId}/hls1/{playlistId}/{segmentId}.{container}
```

**字幕 URL 约定**（`StreamInfo.cs:1230-1285`）：
```
{base}/Videos/{itemId}/{mediaSourceId}/Subtitles/{index}/{startPositionTicks}/Stream.{format}
```
外部 http(s) 直链时 `IsExternalUrl=true`（不给 ApiKey）；否则追加 `?ApiKey=<token>`。

**Trickplay**：
```
GET /Videos/{itemId}/Trickplay/{width}/tiles.m3u8?mediaSourceId=...
GET /Videos/{itemId}/Trickplay/{width}/{index}.jpg?mediaSourceId=...
```

**媒体片段**：
```
GET /MediaSegments/{itemId}?includeSegmentTypes=Intro,Outro
→ 200 { "Items": [ { "Id":..., "ItemId":..., "Type":"Intro", "StartTicks":..., "EndTicks":... } ], "TotalRecordCount": N }
→ 404 条目不存在
```

#### 2.9.5 一个完整的最小播放流程（伪代码）

```python
# 1) 认证
POST /Users/AuthenticateByName
     {"Username": "...", "Pw": "..."}
     headers: Authorization: MediaBrowser Client="myapp", Device="PC", DeviceId="<uuid>", Version="1.0"
→ { "AccessToken": "...", "User": { "Id": "..." }, "ServerId": "..." }

# 2) 列表
GET /Items?parentId=<libId>&recursive=true&includeItemTypes=Movie
        &sortBy=SortName&sortOrder=Ascending&fields=Overview,ProviderIds,Genres
        &enableImages=true&imageTypeLimit=1&enableImageTypes=Primary,Backdrop
        &startIndex=0&limit=50&enableTotalRecordCount=false
     headers: Authorization: MediaBearer <token>
→ { "Items": [...], "TotalRecordCount": N }

# 3) 播放握手
POST /Items/{id}/PlaybackInfo
     {"userId":"...", "maxStreamingBitrate": 8000000, "startTimeTicks": <resume>,
      "deviceProfile": {...我支持什么...}}
→ { "MediaSources": [ { "Id":"...", "SupportsDirectPlay":true, ...,
                        "TranscodingUrl":"/Videos/<id>/master.m3u8?...", ... } ],
    "PlaySessionId": "..." }
   # 若 MediaSources[0].TranscodingUrl 为空 -> 直接 GET /Videos/{id}/stream?static=true&mediaSourceId=...
   # 否则按 TranscodingUrl 播放（或自己拼 /Videos/{id}/stream?... 参数）

# 4) 开始上报
POST /Sessions/Playing   {"ItemId":"...", "PlaySessionId":"...", "MediaSourceId":"...",
                          "PositionTicks":<resume>, "PlayMethod":2, "CanSeek":true}

# 5) 每 10 秒进度 + 每 30 秒 ping
POST /Sessions/Playing/Progress  {"ItemId":"...", "PlaySessionId":"...", "PositionTicks":<now>,
                                  "IsPaused":false, "PlayMethod":2, "CanSeek":true}
POST /Sessions/Playing/Ping?playSessionId=...

# 6) 停止
POST /Sessions/Playing/Stopped   {"ItemId":"...", "PlaySessionId":"...",
                                  "PositionTicks":<now>, "Failed":false}
```

---

## 三、落地建议：自己写"刮削 + 播放"的 Python 桌面应用

### 3.1 架构分层（建议）

```
┌──────────────────────────────────────────────────────────────┐
│ UI 层 (PySide6)                                              │
│  媒体库网格 / 详情页 / 播放器窗口 / 设置页                     │
└───────────────┬──────────────────────────────────────────────┘
                │ 只调用服务层，不直接碰 DB / 网络
┌───────────────▼──────────────────────────────────────────────┐
│ 服务层 (services/)                                            │
│  library_service  扫描编排、增量、进度事件                     │
│  playback_service 能力协商 + 续播 + 进度上报（对应 §1.1~1.5）   │
│  subtitle_service 字幕发现/转换/延迟偏移                       │
└──────┬──────────────────────┬───────────────────┬────────────┘
       │                      │                   │
┌──────▼──────┐   ┌───────────▼────────┐  ┌───────▼──────────┐
│ 解析层      │   │ 元数据源层          │  │ 播放引擎层        │
│ naming/     │   │ providers/          │  │ player/          │
│  纯函数     │   │  tmdb.py            │  │  probe.py(ffprobe)│
│  可单测     │   │  tvdb.py            │  │  decide.py(§1.8.1)│
│             │   │  omdb.py            │  │  ffmpeg_cmd.py    │
│             │   │  images.py          │  │  (mpv/QtMultimedia│
│             │   │                     │  │   或 自带 ffplay) │
└──────┬──────┘   └───────────┬────────┘  └───────┬──────────┘
       │                      │                   │
┌──────▼──────────────────────▼───────────────────▼──────────┐
│ 存储层 (db/) + 缓存层 (cache/)                                │
│  SQLite (WAL)  |  图片缓存  |  字幕缓存  |  预览图(trickplay)  │
└─────────────────────────────────────────────────────────────┘
```

**核心设计原则（照搬 Jellyfin 的分层）**：
1. **命名解析是纯函数**（输入路径 → 输出结构化结果，无 IO、无网络）→ 可写单元测试，这是 Jellyfin `Emby.Naming` 最值钱的地方。
2. **播放决策是纯函数**（输入"文件探测结果 + 客户端能力" → 输出 `(PlayMethod, Reasons, 参数)`）→ 同样可单测。
3. **元数据源按接口抽象**（`Provider.search()` / `Provider.fetch(id)` / `Provider.images(id)`）→ 加 TVDB 不用改业务代码。
4. **DB 与网络解耦**：扫描阶段只做"入库 + 标记待刮削"，刮削在独立队列里跑（照搬 `QueueRefresh`）。

### 3.2 目录扫描与命名解析（伪代码）

```python
# naming/options.py —— 照搬 Emby.Naming/Common/NamingOptions.cs 的常量
VIDEO_EXTS = {".001",".3g2",".3gp",".amv",".asf",".asx",".avi",".bin",".bivx",".divx",".dv",
    ".dvr-ms",".f4v",".fli",".flv",".ifo",".img",".iso",".m2t",".m2ts",".m2v",".m4v",".mkv",
    ".mk3d",".mov",".mp4",".mpe",".mpeg",".mpg",".mts",".mxf",".nrg",".nsv",".nuv",".ogm",
    ".ogv",".pva",".qt",".rec",".rm",".rmvb",".strm",".svq3",".tp",".ts",".ty",".viv",".vob",
    ".vp3",".webm",".wmv",".wtv",".xvid"}
SUBTITLE_EXTS = {".ass",".mks",".sami",".smi",".srt",".ssa",".sub",".sup",".vtt"}
LYRIC_EXTS = {".lrc",".elrc",".txt"}
STUB_EXTS = {".disc"}

EXTRA_DIRS = {          # 目录名 -> ExtraType（照搬 NamingOptions.cs:497-700）
    "trailers":"Trailer", "backdrops":"ThemeVideo", "theme-music":"ThemeSong",
    "behind the scenes":"BehindTheScenes", "deleted scenes":"DeletedScene",
    "interviews":"Interview", "scenes":"Scene", "samples":"Sample", "shorts":"Short",
    "featurettes":"Featurette", "extras":"Unknown", "extra":"Unknown", "other":"Unknown",
    "clips":"Clip",
}
EXTRA_FILENAMES = {"trailer":"Trailer", "sample":"Sample", "theme":"ThemeSong"}
EXTRA_SUFFIXES = [      # 顺序即优先级；匹配前先去掉文件名末尾数字（识别 -trailer2）
    ("-trailer","Trailer"), (".trailer","Trailer"), ("_trailer","Trailer"), ("- trailer","Trailer"),
    ("-sample","Sample"), (".sample","Sample"), ("_sample","Sample"), ("- sample","Sample"),
    ("-scene","Scene"), ("-clip","Clip"), ("-interview","Interview"),
    ("-behindthescenes","BehindTheScenes"), ("-deleted","DeletedScene"),
    ("-deletedscene","DeletedScene"), ("-featurette","Featurette"), ("-short","Short"),
    ("-extra","Unknown"),
]

# naming/episode.py —— 照搬 EpisodeExpressions（顺序即优先级，见 §2.2.2 全表）
EPISODE_PATTERNS = [
    # (regex, is_named, is_optimistic, supports_absolute)
    (r'.*[\\/](?P<seriesname>(?![Ss]\d+[\]\[ ._-]*[Ee]\d+)[^\\/])*?[Ss](?P<seasonnumber>\d+)[\]\[ ._-]*[Ee](?P<epnumber>\d+)', True, False, None),
    (r'[._ -][Ee][Pp]_?(?P<epnumber>\d+)', False, False, None),
    (r'[\\/. _\[\(-](?P<seasonnumber>\d+)x(?P<epnumber>\d+(?:[a-i]|\.[1-9])?)', True, False, True),   # 1x01
    (r'[Ss]eason[\._ ](?P<seasonnumber>\d+)[\\/](?P<epnumber>\d{1,3})', True, True, None),              # Season 01/01
    (r'(?P<epnumber>\d{1,3})(?:-(?P<endingepnumber>\d{2,3}))?\s?-\s?[^\\/]*$', True, True, None),
    # ... 其余 22 条见 §2.2.2
]
DATE_PATTERNS = [   # IsByDate=True
    (r'(?P<year>\d{4})[._ -](?P<month>\d{2})[._ -](?<day>\d{2})', ["yyyy-MM-dd","yyyy.MM.dd","yyyy MM dd"]),
    (r'(?P<day>\d{2})[._ -](?P<month>\d{2})[._ -](?P<year>\d{4})', ["dd-MM-yyyy","dd.MM.yyyy","dd MM yyyy"]),
]

def parse_episode(path: str, *, is_named=None, is_optimistic=None, supports_absolute=None):
    """返回 EpisodeParseResult 或 None。照搬 EpisodePathParser.Parse 的过滤 + 首命中即停。"""
    if os.path.isdir(path):
        path += ".mp4"                      # 照抄：部分正则要求扩展名
    for rx, named, optimistic, absolute in EPISODE_PATTERNS:
        if is_named is not None and named != is_named: continue
        if is_optimistic is not None and optimistic != is_optimistic: continue
        if supports_absolute is not None and absolute != supports_absolute: continue
        m = re.search(rx, path)
        if not m: continue
        gd = m.groupdict()
        season = int(gd["seasonnumber"]) if gd.get("seasonnumber") else None
        ep     = int(gd["epnumber"])     if gd.get("epnumber")     else None
        if season is None or ep is None: continue
        end = None
        e = gd.get("endingepnumber")
        if e:
            nxt = m.end("endingepnumber")
            # ★ 照抄两条防误判：结束集号后不能紧跟数字/p/i；且必须 >= 起始集号
            if nxt >= len(path) or path[nxt] not in "0123456789iIpP":
                if int(e) >= ep: end = int(e)
        return EpisodeParseResult(season=season, episode=ep, ending=end,
                                  series_name=clean_series_name(gd.get("seriesname")))
    return None

# naming/season.py —— 照搬 SeasonPathParser 的三级顺序
SEASON_KEYWORDS = ["시즌","シーズン","сезон","season","sæson","saison","staffel","series",
    "stagione","säsong","seizoen","seasong","sezon","sezona","sezóna","sezonul",
    "série","séria","serie","seria","temporada","kausi"]

def parse_season_number(folder_name, parent_name=None, *, special_aliases=True, numeric_folders=True):
    # 1) S01 前缀
    if m := re.match(r'[sS](\d{1,4})(?!\d|[eE]\d)(?=\.|_|-|\[|\]|\s|$)', folder_name):
        return int(m.group(1))
    # 2) 去分隔符 + 去掉父目录名（避免剧名干扰）
    f = re.sub(r'[ ._\-\[\]]', '', folder_name)
    if parent_name:
        f = re.sub(re.escape(re.sub(r'[ ._\-\[\]]', '', parent_name)), '', f, flags=re.I)
    # 3) Specials / Extras -> 0
    if special_aliases and f.lower() in ("specials", "extras"):
        return 0
    # 4) 纯数字目录
    if numeric_folders and f.isdigit():
        return int(f)
    # 5) 关键词：数字在前（1st Season）或关键词在前（Season 1）
    for rx in (rf'^\s*(?P<n>\d+)(?:st|nd|rd|th|\.)*(?!\s*[Ee]\d+)\s*(?:{"|".join(SEASON_KEYWORDS)})',
               rf'^\s*(?:{"|".join(SEASON_KEYWORDS)})\s*(?P<n>\d+?)(?=\d{{3,4}}p|[^\d]|$)(?!\s*[Ee]\d)'):
        if m := re.match(rx, f, re.I):
            return int(m.group("n"))
    return None

# naming/movie.py —— 照搬 VideoResolver + CleanDateTime + CleanString
CLEAN_DATETIME = [
    r'(.+[^_\,\.\(\)\[\]\-])[_\.\(\)\[\]\-](19\d{2}|20\d{2})(?!\d+|\W\d{2}\W\d{2})([ _\,\.\(\)\[\]\-][^0-9]|)',
    r'(.+[^_\,\.\(\)\[\]\-])[ _\.\(\)\[\]\-]+(19\d{2}|20\d{2})(?!\d+|\W\d{2}\W\d{2})([ _\,\.\(\)\[\]\-][^0-9]|)',
]
CLEAN_STRINGS = [
    r'^\s*(?P<cleaned>.+?)[ _\,\.\(\)\[\]\-](3d|sbs|tab|hsbs|htab|mvc|HDR|UHD|UltraHD|4k|ac3|dts|'
    r'dvd|dvdrip|dvdscr|screener|hdtv|hdrip|internal|limited|multi|subs|ntsc|ogg|ogm|pal|pdtv|'
    r'proper|repack|rerip|retail|cd[1-9]|r5|bd|svcd|unrated|ws|web-dl|telesync|ts|telecine|tc|'
    r'brrip|bdrip|480p|480i|576p|576i|720p|720i|1080p|1080i|2160p|bluray|blu-ray|x264|x265|'
    r'h264|h265|xvid|AAC|DTS)(?=[ _\,\.\(\)\[\]\-]|$)',
    r'^\s*(?P<cleaned>.+?)((\s*\[[^\]]+\]\s*)+)(\.[^\s]+)?$',
    r'^\s*(?P<cleaned>.+?)\WE\d+(-|~)E?\d+(\W|$)',
    r'^\s*\[[^\]]+\](?!\.\w+$)\s*(?P<cleaned>.+)',
    r'^\s*(?P<cleaned>.+?)\s+-\s+\d+\s*$',
    r'^\s*(?P<cleaned>.+?)(([-._ ](trailer|sample))|-(scene|clip|behindthescenes|deleted|'
    r'deletedscene|featurette|short|interview|other|extra))$',
]

# 多版本判定 —— 照搬 VideoListResolver.IsEligibleForMultiVersion（比官方文档更精确）
def is_multi_version(folder_name: str, filename_no_ext: str) -> bool:
    if not filename_no_ext.lower().startswith(folder_name.lower()):
        return False                                   # ★ 必须逐字符前缀匹配
    rest = filename_no_ext[len(folder_name):].strip()
    cleaned = try_clean_string(rest)                   # 复用 CLEAN_STRINGS
    if cleaned is not None:
        rest = cleaned.strip()
    return (rest == "" or rest[0] in "-_." or re.match(r'^\[([^]]*)\]', rest) is not None)

def scan_library(root: str, kind: str) -> list[MediaItem]:
    """kind: 'movies' | 'shows' | 'music'"""
    items = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 1) extras 目录优先识别（且不递归进 extras 当媒体）
        extra_type = EXTRA_DIRS.get(os.path.basename(dirpath).lower())
        # 2) 视频文件
        vids = [f for f in filenames if os.path.splitext(f)[1].lower() in VIDEO_EXTS]
        for f in vids:
            full = os.path.join(dirpath, f)
            stem = os.path.splitext(f)[0]
            # extras 后缀
            if (et := match_extra_suffix(stem)) or extra_type:
                items.append(Extra(path=full, extra_type=et or extra_type)); continue
            if kind == "movies":
                name, year = clean_movie_name(stem)
                items.append(MovieLike(path=full, name=name, year=year,
                                       provider_ids=parse_provider_ids(stem)))
            elif kind == "shows":
                if (ep := parse_episode(full, is_named=True)) or \
                   (ep := parse_episode(full, is_named=True, is_optimistic=True)):
                    season_no = parse_season_number(os.path.basename(dirpath),
                                                    os.path.basename(os.path.dirname(dirpath)))
                    ep.season = ep.season if ep.season is not None else season_no
                    items.append(EpisodeLike(path=full, **vars(ep)))
        # 3) 多版本归组（同一目录内、前缀匹配的多个视频）
        # 4) 多部分归组（FileStackRule 正则：cd|dvd|part|pt|dis[ck] + 数字/字母）
        # 5) 旁挂文件（字幕/音轨/NFO/图片）关联
    return group_versions(group_stacks(items))
```

**关键实现提醒**：
1. **正则顺序即优先级**，不要重排（尤其日期式在 `SxxExx` 之前还是之后，会影响同名文件的判定）。
2. **`is_named=True` 先试、`is_optimistic=True` 再试**（照搬 `SeriesResolver`/`SeasonResolver` 的两轮调用）。
3. **`CleanDateTime` 的 `(?![0-9]+|\W[0-9]{2}\W[0-9]{2})` 前瞻必须保留**，否则 `1920x1080` 会被当作年份、`2020-01-01` 会被误切。
4. **多版本前缀必须"逐字符"匹配父目录名（含年份与 provider id）**，这是官方明确的硬要求。

### 3.3 TMDB / TVDB 查询与语言回退

```python
# providers/tmdb.py
import re, requests

# 照搬 TmdbUtils.NormalizeLanguage
def normalize_language(language: str | None, country_code: str | None = None) -> str | None:
    if not language: return language
    if language.lower() == "es-419" and country_code:
        language = "es-AR" if country_code.upper() == "AR" else "es-MX"
    parts = language.split("-")
    if len(parts) == 2:
        if parts[1].upper() == "CH":       # TMDB 不支持瑞士，去掉国家码
            return parts[0]
        language = parts[0] + "-" + parts[1].upper()    # ★ 子标签必须大写
    return language

# 照搬 TmdbUtils.GetImageLanguagesParam：<首选>,null,en
def image_languages(preferred: str, country_code: str | None = None) -> str:
    langs = []
    if preferred:
        preferred = normalize_language(preferred, country_code)
        langs.append(preferred)
    langs.append("null")                                  # TMDB 的"无语言"图片（干净无字 logo/海报）
    if preferred.lower() != "en":
        langs.append("en")                                # 英文兜底
    return ",".join(langs)

# 照搬 TmdbUtils.CleanName / NormalizeTitle（保留 · 因为中文译名常用间隔号）
_NON_SEARCH   = re.compile(r'[^\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af·]+', re.U)
_NON_COMPARABLE = re.compile(r'[^\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+', re.U)
def clean_name(name):      return _NON_SEARCH.sub(" ", name).strip()
def normalize_title(t):    return _NON_COMPARABLE.sub(" ", t or "").strip().lower() if t else ""

# 照搬 TmdbUtils.FindBestMatch 的打分
TITLE_EXACT, TITLE_PREFIX, YEAR_EXACT, YEAR_ADJACENT = 8, 4, 2, 1
def best_match(results, name, year):
    best, best_score = None, -1
    n = normalize_title(name)
    for r in results:
        score = 0
        for cand in (r.get("title"), r.get("original_title")):
            c = normalize_title(cand)
            if not c: continue
            if c == n:                 score = max(score, TITLE_EXACT)
            elif c.startswith(n):      score = max(score, TITLE_PREFIX)
        ry = int((r.get("release_date") or "0000")[:4] or 0)
        if year and ry:
            if ry == year:             score += YEAR_EXACT
            elif abs(ry - year) == 1:  score += YEAR_ADJACENT
        if score > best_score:
            best, best_score = r, score
    return best

class TmdbProvider:
    BASE = "https://api.themoviedb.org/3"
    IMG  = "https://image.tmdb.org/t/p"          # 尺寸: w92 w154 w185 w342 w500 w780 original

    def __init__(self, api_key: str, language="zh-CN", country="CN"):
        if not api_key: raise ValueError("必须自备 TMDB API Key（不要把 key 写进代码/仓库）")
        self.key, self.language, self.country = api_key, language, country

    def _get(self, path, **params):
        params.update({"api_key": self.key,
                       "language": normalize_language(self.language, self.country)})
        r = requests.get(f"{self.BASE}{path}", params=params, timeout=15)
        if r.status_code == 429:
            raise RateLimited(float(r.headers.get("Retry-After", 1)))
        r.raise_for_status()
        return r.json()

    def search_movie(self, name, year=None):
        d = self._get("/search/movie", query=clean_name(name),
                      **({"year": year} if year else {}))
        return best_match(d.get("results", []), name, year)

    def movie(self, tmdb_id):
        # include_image_language 决定返回哪些语言的图片
        return self._get(f"/movie/{tmdb_id}",
                         append_to_response="credits,release_dates,images,external_ids,videos",
                         include_image_language=image_languages(self.language, self.country))

    def tv(self, tmdb_id):
        return self._get(f"/tv/{tmdb_id}",
                         append_to_response="credits,content_ratings,images,external_ids,aggregate_credentials")
    def season(self, tmdb_id, season_number):
        return self._get(f"/tv/{tmdb_id}/season/{season_number}",
                         append_to_response="credits,images",
                         include_image_language=image_languages(self.language, self.country))
    def episode(self, tmdb_id, season_number, episode_number):
        return self._get(f"/tv/{tmdb_id}/season/{season_number}/episode/{episode_number}",
                         append_to_response="credits,images",
                         include_image_language=image_languages(self.language, self.country))
```

> **★ 重要说明（诚实标注）**：上面的**端点路径与 `append_to_response` 取值是我按 TMDB 官方 API 的通用用法写的**。Jellyfin 内部用的是 **`TMDbLib` 这个 NuGet 库**（`TmdbClientManager.cs` 调用 `_tmDbClient.GetMovieAsync(...)` 等），**它把 URL 拼接隐藏在了库里**，所以我在源码里**没有**看到形如 `/3/movie/{id}` 的字面量。因此：
> - **"Jellyfin 用什么语言参数、什么图片语言链、什么标题打分"** → 这些**是**源码事实（已给行号）。
> - **"TMDB 端点的确切路径与 `append_to_response` 组合"** → 这是**基于 TMDB 公开 API 的通用写法**，**不是**从 Jellyfin 源码读出来的。落地前请以 <https://developer.themoviedb.org/docs> 为准。

**语言回退的实操建议**（三层，照搬 Jellyfin 的思想）：
```python
def fetch_with_fallback(provider, tmdb_id):
    # 1) 首选语言
    data = provider.movie(tmdb_id)                       # language=zh-CN
    # 2) 字段级回退：标题/简介为空则再请求 en
    if not data.get("overview"):
        en = TmdbProvider(provider.key, language="en", country="US").movie(tmdb_id)
        data["overview"] = en.get("overview") or ""
    # 3) 图片语言已是三段式（<lang>,null,en），无需额外处理
    return data
```
> 官方对"会回退"的表述见 §2.5.4（setup-wizard 页逐字："Metadata from other language / regions may be fetched if metadata is not available with your preferred settings."）。

### 3.4 SQLite 表结构建议

```sql
PRAGMA journal_mode = WAL;          -- 读写并发（照搬 Jellyfin 的 SQLite 默认 NORMAL + WAL 思路）
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;

-- ============ 媒体条目（单表 + type 区分，照搬 Jellyfin 的 BaseItems 思路但收敛字段）============
CREATE TABLE items (
    id                TEXT PRIMARY KEY,              -- uuid4().hex
    kind              TEXT NOT NULL,                 -- movie|series|season|episode|extra|artist|album|track
    name              TEXT NOT NULL,
    original_title    TEXT,
    sort_name         TEXT,
    overview          TEXT,
    tagline           TEXT,
    production_year   INTEGER,
    premiere_date     TEXT,                          -- ISO8601
    end_date          TEXT,
    runtime_ticks     INTEGER,                       -- 照搬 Jellyfin：1 tick = 100ns
    official_rating   TEXT,                          -- 'PG-13' / 'TV-14'（字符串！）
    custom_rating     TEXT,
    community_rating  REAL,                          -- 约定 0-10（与 TMDB vote_average 一致）
    critic_rating     REAL,
    parent_id         TEXT REFERENCES items(id) ON DELETE CASCADE,
    series_id         TEXT REFERENCES items(id) ON DELETE SET NULL,   -- 照搬 Season/Episode.SeriesId
    season_id         TEXT REFERENCES items(id) ON DELETE SET NULL,   -- 照搬 Episode.SeasonId
    index_number      INTEGER,                       -- 集号/轨号
    parent_index_number INTEGER,                     -- 季号/碟号
    index_number_end  INTEGER,                       -- 区间集 S01E01-E02
    airs_before_season  INTEGER,                     -- 特别篇排期（照搬 Episode 字段）
    airs_after_season   INTEGER,
    airs_before_episode INTEGER,
    path              TEXT,                          -- 目录（文件夹类）或文件路径
    file_path         TEXT,                          -- 视频文件路径（可空）
    container         TEXT,                          -- mkv/mp4
    primary_version_id TEXT,                         -- 多版本归组（照搬 Video.PrimaryVersionId）
    is_virtual        INTEGER NOT NULL DEFAULT 0,
    date_created      TEXT,
    date_modified     TEXT,
    date_last_refreshed TEXT,
    preferred_metadata_language TEXT,                -- 条目级覆盖（照搬 BaseItem）
    preferred_metadata_country  TEXT,
    season_zero_display_name TEXT DEFAULT 'Specials',
    metadata_json     TEXT                           -- 只放"极少查询"的杂项，别学 Jellyfin 什么都塞
);
CREATE INDEX idx_items_kind        ON items(kind);
CREATE INDEX idx_items_parent      ON items(parent_id);
CREATE INDEX idx_items_series      ON items(series_id, parent_index_number, index_number);
CREATE INDEX idx_items_year        ON items(production_year);
CREATE INDEX idx_items_sort        ON items(sort_name);
CREATE INDEX idx_items_path        ON items(file_path);        -- 扫描去重靠它
CREATE UNIQUE INDEX uq_items_file  ON items(file_path) WHERE file_path IS NOT NULL;

-- ============ 外部 ID（照搬 ProviderIds 独立表的做法）============
CREATE TABLE item_providers (
    item_id   TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    provider  TEXT NOT NULL,          -- Tmdb|Imdb|Tvdb|MusicBrainzAlbum|...
    value     TEXT NOT NULL,
    PRIMARY KEY (item_id, provider)
);
CREATE INDEX idx_providers_lookup ON item_providers(provider, value);   -- 反查"这部 TMDB 片是否已入库"

-- ============ 人物（照搬 Peoples + PeopleBaseItemMap 两张表）============
CREATE TABLE people (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    person_type TEXT,                 -- 'Actor'|'Director'|... 照搬 Jellyfin 用字符串
    tmdb_id     INTEGER,
    image_path  TEXT,
    UNIQUE (name, person_type)        -- ★ 照搬 Jellyfin 的注释：one row per (Name, PersonType)
);
CREATE TABLE credits (
    item_id     TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    person_id   TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    role        TEXT,                 -- 角色名（演员演的角色 / Writer 的具体职务）
    list_order  INTEGER,              -- ★ 显示顺序（照搬 PeopleBaseItemMap.ListOrder）
    sort_order  INTEGER,
    PRIMARY KEY (item_id, person_id, role)
);
CREATE INDEX idx_credits_item ON credits(item_id, list_order);

-- ============ 标签值（照搬 ItemValues + ItemValueMap，一个表搞定 genres/studios/tags）============
CREATE TABLE genres   (item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                       name TEXT NOT NULL, PRIMARY KEY (item_id, name));
CREATE TABLE studios  (item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                       name TEXT NOT NULL, PRIMARY KEY (item_id, name));
CREATE TABLE tags     (item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                       name TEXT NOT NULL, PRIMARY KEY (item_id, name));
CREATE INDEX idx_genres_name ON genres(name);
-- 建议再加一张去重字典表，便于 UI 的"类型"列表页：
CREATE TABLE genre_dict (name TEXT PRIMARY KEY, item_count INTEGER DEFAULT 0);

-- ============ 媒体流（照搬 MediaStreamInfos）============
CREATE TABLE media_streams (
    item_id      TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    stream_index INTEGER NOT NULL,
    stream_type  TEXT NOT NULL,        -- Video|Audio|Subtitle|EmbeddedImage|Data|Lyric
    codec        TEXT,
    language     TEXT,
    title        TEXT,
    is_default   INTEGER DEFAULT 0,
    is_forced    INTEGER DEFAULT 0,
    is_external  INTEGER DEFAULT 0,
    is_hearing_impaired INTEGER DEFAULT 0,
    width        INTEGER, height INTEGER,
    bit_rate     INTEGER, bit_depth INTEGER, ref_frames INTEGER,
    channels     INTEGER, channel_layout TEXT, sample_rate INTEGER,
    profile      TEXT, level REAL, pixel_format TEXT,
    is_interlaced INTEGER, is_avc INTEGER, is_anamorphic INTEGER,
    average_frame_rate REAL, real_frame_rate REAL,
    color_space TEXT, color_transfer TEXT, color_primaries TEXT,
    dv_profile INTEGER, dv_level INTEGER, hdr10_plus INTEGER,
    rotation     INTEGER,
    external_path TEXT,                -- 外挂字幕/音轨文件路径（Path）
    PRIMARY KEY (item_id, stream_index)
);

-- ============ 多文件（多版本 / 多部分）============
CREATE TABLE item_files (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id      TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    size_bytes   INTEGER,
    mtime        REAL,
    version_label TEXT,               -- '1080p' / 'Directors Cut'（照搬多版本后缀）
    is_primary   INTEGER DEFAULT 1,   -- 是否默认版本
    part_number  INTEGER,             -- 多部分的序号（cd1/cd2）
    UNIQUE (item_id, path)
);
CREATE INDEX idx_item_files_path ON item_files(path);

-- ============ 播放进度（照搬 UserItemData，按 user+item 存，与设备无关）============
CREATE TABLE user_data (
    user_id       TEXT NOT NULL DEFAULT 'default',
    item_id       TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    played        INTEGER NOT NULL DEFAULT 0,
    play_count    INTEGER NOT NULL DEFAULT 0,
    position_ticks INTEGER NOT NULL DEFAULT 0,     -- ★ 续播点
    last_played_date TEXT,
    is_favorite   INTEGER NOT NULL DEFAULT 0,
    rating        REAL,                            -- 点赞=1/点踩=-1 之类
    audio_stream_index    INTEGER,                 -- 记住用户选的音轨
    subtitle_stream_index INTEGER,                 -- 记住用户选的字幕轨
    PRIMARY KEY (user_id, item_id)
);
CREATE INDEX idx_userdata_resume ON user_data(user_id, position_ticks) WHERE position_ticks > 0;

-- ============ 图片（一张表管所有类型）============
CREATE TABLE images (
    item_id    TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    image_type TEXT NOT NULL,          -- Primary|Backdrop|Logo|Thumb|Banner|Art|Disc|Chapter...
    idx        INTEGER NOT NULL DEFAULT 0,   -- 多张 Backdrop 的序号
    language   TEXT,
    width      INTEGER, height INTEGER,
    source     TEXT,                   -- local|tmdb|tvdb|embedded
    local_path TEXT,                   -- 已下载的本地路径（相对缓存根）
    remote_url TEXT,                   -- 未下载时的远程 URL
    vote_average REAL, vote_count INTEGER,
    is_downloaded INTEGER DEFAULT 0,
    PRIMARY KEY (item_id, image_type, idx)
);
CREATE INDEX idx_images_type ON images(image_type);

-- ============ 预览图（trickplay，照搬 TrickplayInfo）============
CREATE TABLE trickplay (
    item_id      TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    width        INTEGER NOT NULL,     -- 单张缩略图宽（默认 320）
    height       INTEGER NOT NULL,
    tile_width   INTEGER NOT NULL,     -- 每行几张（默认 10）
    tile_height  INTEGER NOT NULL,     -- 每列几张（默认 10）
    thumbnail_count INTEGER NOT NULL,
    interval_ms  INTEGER NOT NULL,     -- 默认 10000
    bandwidth    INTEGER,
    dir_path     TEXT NOT NULL,        -- <cache>/trickplay/<id前2位>/<id>/<width> - <tw>x<th>/
    PRIMARY KEY (item_id, width)
);

-- ============ 媒体片段（片头/片尾，照搬 MediaSegment）============
CREATE TABLE media_segments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id    TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    segment_type TEXT NOT NULL,        -- Unknown|Commercial|Preview|Recap|Outro|Intro
    start_ticks INTEGER NOT NULL,
    end_ticks   INTEGER NOT NULL,
    provider_id TEXT NOT NULL
);
CREATE INDEX idx_segments_item ON media_segments(item_id, start_ticks);

-- ============ 章节（照搬 Chapters）============
CREATE TABLE chapters (
    item_id      TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    chapter_index INTEGER NOT NULL,
    start_position_ticks INTEGER NOT NULL,
    name         TEXT,                 -- ★ "片头"关键词跳过就靠它
    image_path   TEXT,
    image_date_modified TEXT,
    PRIMARY KEY (item_id, chapter_index)
);

-- ============ 扫描/刮削状态（Jellyfin 把这部分放在内存队列 + DateLastRefreshed）============
CREATE TABLE scrape_queue (
    item_id     TEXT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    state       TEXT NOT NULL DEFAULT 'pending',   -- pending|running|done|failed|skipped
    priority    INTEGER NOT NULL DEFAULT 0,        -- 照搬 RefreshPriority
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    next_retry_at TEXT,
    updated_at  TEXT
);
CREATE INDEX idx_queue_state ON scrape_queue(state, priority DESC, updated_at);
```

**要点说明**：
1. **`ticks` 用 `INTEGER`**（1 tick = 100 ns，1 秒 = 10,000,000）——与 Jellyfin 一致，便于将来做协议互通；不想用也可以改成毫秒，但要**全项目统一**。
2. **`credits` 必须有 `list_order`**——否则演员表顺序会乱（Jellyfin 有 `ListOrder` 与 `SortOrder` 两个）。
3. **`item_providers(provider, value)` 要建索引**——用于"这条 TMDB id 是否已存在"的反查，避免重复入库。
4. **`user_data.played / position_ticks` 与设备无关**（照搬 Jellyfin），多设备同步靠"后写覆盖 + 读时最新"。
5. **`images` 用 `(item_id, image_type, idx)` 复合主键**，天然支持"多张 backdrop + 语言"。
6. **建议开 `PRAGMA user_version` 做迁移版本号**（Jellyfin 用 EF Core migrations；自研用简单的手写迁移更轻）。

### 3.5 图片下载与缓存

```python
# images/cache.py —— 照搬 Jellyfin 的"两级散列 + 完整 id"布局与取图优先级
class ImageCache:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def path_for(self, item_id: str, image_type: str, idx: int = 0, ext: str = ".jpg") -> Path:
        # 照搬 <root>/library/<id前2位>/<id>/ 的思路
        d = self.root / "library" / item_id[:2] / item_id
        d.mkdir(parents=True, exist_ok=True)
        suffix = "" if idx == 0 else f"-{idx}"
        return d / f"{image_type.lower()}{suffix}{ext}"

# 图片优先级（照搬 §2.4.2 + §2.6.1）：
IMAGE_PRIORITY = [
    "nfo_art",        # NFO 里的 <art>/<thumb> 路径或 URL —— 官方：优先级最高
    "local_file",     # 媒体同目录的 poster.jpg / fanart.jpg / logo.png ...
    "embedded",       # MKV 内嵌封面
    "remote",         # TMDB / TVDB / fanart.tv
]

# 本地文件名的候选（照搬官方文件名表 §2.1.6）
LOCAL_PRIMARY_NAMES  = ["poster", "folder", "cover", "default", "movie", "show", "jacket"]
LOCAL_BACKDROP_NAMES = ["backdrop", "fanart", "background", "art"]
LOCAL_LOGO_NAMES     = ["logo", "clearlogo"]
LOCAL_THUMB_NAMES    = ["thumb", "landscape"]
LOCAL_BANNER_NAMES   = ["banner"]
IMAGE_EXTS           = [".jpg", ".jpeg", ".png", ".webp"]     # 官方：只支持 JPEG/PNG 上传，但显示支持 webp

# 最小宽度门槛（照搬 TypeOptions 默认值）
MIN_BACKDROP_WIDTH = 1280
LIMITS = {"Primary": 1, "Backdrop": 1, "Logo": 1, "Thumb": 1, "Banner": 0, "Art": 0, "Disc": 0}

# TMDB 图片尺寸（按用途选，别一律 original）
TMDB_SIZE = {
    "Primary":  "w500",      # 海报：卡片用 w342，详情页用 w500
    "Backdrop": "w1280",     # 背景
    "Logo":     "w300",
    "Thumb":    "w300",
    "Profile":  "w185",      # 人物头像
    "Still":    "w300",      # 剧照
}

def pick_best(candidates: list[dict], *, min_width=0, limit=1) -> list[dict]:
    """按 (语言匹配 -> 分辨率 -> vote_average) 排序后取前 limit 张。"""
    ok = [c for c in candidates if (c.get("width") or 0) >= min_width]
    ok.sort(key=lambda c: (
        0 if c.get("iso_639_1") == PREFERRED_LANG_2 else (1 if c.get("iso_639_1") is None else 2),
        -(c.get("width") or 0) * (c.get("height") or 0),
        -(c.get("vote_average") or 0),
    ))
    return ok[:limit]
```

> **照搬 Jellyfin 的三条图片规则**：① **本地图 > 在线图**；② **NFO 里的图片路径 > 一切**；③ **每种类型只存前 N 张（默认 1 张），backdrop 最小宽度 1280**。这三条能同时解决"图片太多占空间"和"图太糊"两个矛盾问题。

### 3.6 界面展示（PySide6 要点）

| 关注点 | 建议 |
|---|---|
| **列表虚拟化** | 媒体库动辄上万条，**不要**给每条建一个 QWidget。用 `QListView` + `QAbstractListModel` + `setUniformItemSizes(True)` + 自定义 `QStyledItemDelegate` 画网格卡片；或 `QTableView` + `QAbstractTableModel` |
| **图片异步加载** | 单独 `QThreadPool` + `QRunnable` 加载缩略图，加载完成用信号回主线程 `setPixmap`；**绝不在主线程做网络/磁盘 IO** |
| **图片内存** | 缓存解码后的 `QPixmap` 要限总量（LRU，如 200 张或 200 MB），否则滚动久了内存爆 |
| **QNetworkAccessManager vs requests** | 图片下载用 `requests` + 线程池（简单），但**必须在主线程外**；若要跟随网络状态，可用 `QNetworkAccessManager`（信号在 Qt 事件循环里，天然主线程） |
| **扫描不阻塞 UI** | 扫描/刮削跑在 `QThread` 或 `concurrent.futures.ThreadPoolExecutor`，通过信号上报进度（照搬 Jellyfin 的 `IProgress<double>` + 任务模型） |
| **播放器窗口** | 若用 `libmpv`（推荐：`python-mpv`）→ 用 `mpv` 的 `wid` 嵌入到 `QWidget.winId()`；若用 `QMediaPlayer` → 能力有限，字幕/音轨切换体验差 |
| **字幕渲染** | 若要 ASS 特效 → 依赖 libass（mpv 自带）；若用 Qt 自绘 → 只能渲染纯文本字幕（丢掉 ASS 样式）。**建议直接用 libmpv**，省掉整个字幕渲染栈 |
| **详情页** | 用 `QScrollArea`，背景图做模糊 + 主海报叠在上层（照搬 Jellyfin Web 的视觉套路） |

### 3.7 与播放器联动（续播、进度、字幕）

**建议**：**先做"本地单机播放器"，但把数据模型和接口按"将来能接服务端"来设计**。这样将来要接 Jellyfin/Emby 或自建服务端时，客户端层几乎不用改。

```python
# playback/session.py —— 照搬 Jellyfin 的会话 + 进度模型（本地版）
TICKS = 10_000_000

class PlaybackSession:
    def __init__(self, db, player, item_id, *, min_resume_pct=5, max_resume_pct=90,
                 min_resume_duration_s=300):
        self.db, self.player, self.item_id = db, player, item_id
        self.min_resume_pct, self.max_resume_pct = min_resume_pct, max_resume_pct
        self.min_resume_duration_s = min_resume_duration_s
        self.play_session_id = uuid4().hex
        self._timer = QTimer(); self._timer.setInterval(10_000)     # 10 秒上报一次
        self._timer.timeout.connect(self.report_progress)

    def start(self):
        ud = self.db.get_user_data(self.item_id)
        resume = ud["position_ticks"] if ud else 0
        self.player.load(self.item_id, start_ticks=resume)
        self.report_start(resume)
        self._timer.start()

    def report_start(self, pos):
        # 对应 POST /Sessions/Playing
        self.db.log_session_event(self.play_session_id, "start", pos,
                                  play_method=self.player.play_method)

    def report_progress(self):
        # 对应 POST /Sessions/Playing/Progress
        pos = self.player.position_ticks
        self.db.upsert_user_data(self.item_id, position_ticks=pos, last_played_date=now_iso())
        self.db.log_session_event(self.play_session_id, "progress", pos,
                                  is_paused=self.player.is_paused)

    def stop(self, *, failed=False):
        # 对应 POST /Sessions/Playing/Stopped
        self._timer.stop()
        pos = self.player.position_ticks
        if not failed:
            self.apply_play_state(pos)          # ★ 照搬 Jellyfin UserDataManager.UpdatePlayState
        self.db.log_session_event(self.play_session_id, "stopped", pos, failed=failed)

    def apply_play_state(self, position_ticks):
        """★ 照搬 §1.5.3 的三段式阈值：5% / 90% / 300 秒"""
        runtime = self.db.get_runtime_ticks(self.item_id) or 0
        played, final_pos = False, position_ticks
        if runtime > 0 and position_ticks > 0:
            pct = position_ticks / runtime * 100
            if pct < self.min_resume_pct:
                final_pos = 0                                   # 开头 -> 不记续播
            elif pct > self.max_resume_pct or position_ticks >= runtime - TICKS:
                final_pos, played = 0, True                     # 接近结尾 -> 算看完
            elif runtime / TICKS < self.min_resume_duration_s:
                final_pos, played = 0, True                     # ★ 短片直接算看完
        elif runtime == 0:
            final_pos, played = 0, True                         # 不知道时长 -> 假设看完
        self.db.upsert_user_data(self.item_id, position_ticks=final_pos,
                                 played=played, inc_play_count=True)
```

**字幕处理建议**（综合 §1.4）：
```python
# subtitles/manager.py
class SubtitleManager:
    def __init__(self):
        self.offset_ms = 0            # ★ 用户可调延迟：Jellyfin 服务端没有，必须自己做

    def discover(self, video_path: str) -> list[SubtitleTrack]:
        """发现外挂 + 内嵌字幕。外挂命名照搬 §2.2.7 的 flag 语法。"""
        stem = os.path.splitext(video_path)[0]
        out = []
        for p in glob.glob(glob.escape(stem) + ".*"):
            ext = os.path.splitext(p)[1].lower()
            if ext not in SUBTITLE_EXTS: continue
            flags = self._parse_sidecar_flags(p[len(stem):], ext)   # title/lang/flags
            out.append(SubtitleTrack(path=p, external=True, **flags))
        out += self._probe_embedded(video_path)                     # ffprobe -> MediaStream
        return out

    def apply_offset(self, cues: list[Cue]) -> list[Cue]:
        """★ 纯内存偏移，绝不改原文件（Jellyfin 的做法）"""
        if not self.offset_ms: return cues
        d = self.offset_ms / 1000
        return [Cue(max(0, c.start + d), max(0, c.end + d), c.text) for c in cues]

    def to_vtt(self, path: str, *, start_ticks=0, copy_timestamps=False) -> str:
        """照搬 §1.4.3 的 FilterEvents 语义：
           丢弃 start 之前已结束的 cue；!copy_timestamps 时整体减去 start_position。"""
```

**进度预览（trickplay）建议**（照搬 §1.6）：
```python
def make_trickplay(src, out_dir, *, interval=10, width=320, tile_w=10, tile_h=10, qscale=4):
    """两阶段：抽帧 -> 拼瓦片"""
    tmp = Path(tempfile.mkdtemp())
    # 阶段 1：等间隔抽帧（-vf fps=1/interval,scale=W:-1；%08d 保证字典序=时间序）
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", src,
                    "-an", "-sn", "-vf", f"fps=1/{interval},scale={width}:-1",
                    "-qscale:v", str(qscale), "-f", "image2", str(tmp / "%08d.jpg")], check=True)
    # 阶段 2：拼 10x10 雪碧图（tile 滤镜）
    (out_dir / f"{width} - {tile_w}x{tile_h}").mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(tmp / "%08d.jpg"),
                    "-vf", f"tile={tile_w}x{tile_h}", "-qscale:v", str(qscale),
                    str(out_dir / f"{width} - {tile_w}x{tile_h}" / "%d.jpg")], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
```
> 客户端拿到 `interval` 与 `tile_w/tile_h` 后，`第N秒 → 瓦片索引 = N // (interval*tile_w*tile_h)`、`格内位置 = (N // interval) % (tile_w*tile_h)`，一次请求 + 一次 `background-position` 即可。

### 3.8 最容易踩的坑（按"踩了很痛"排序）

| # | 坑 | 具体表现 | 对策（有 Jellyfin 依据的标 ★） |
|---|---|---|---|
| 1 | **API Key 泄露** | 把 TMDB key 硬编码/提交到 Git/打进客户端 | ★ Jellyfin 自己**内置了一个公开 key**（`TmdbUtils.ApiKey`）作为兜底，但那是"人人可用=随时可能被限流"的共享 key。**自研必须让用户自备 key**，存 `keyring`/系统凭据管理器或 `~/.config/<app>/config.toml`（**加进 `.gitignore`**）；提交前用 `git-secrets`/`gitleaks` 扫一遍 |
| 2 | **限流（429）** | TMDB 突发限流；批量刮削几千条时被封 | ① 批量刮削**串行 + 间隔**（如 0.05–0.2 s/请求）；② 做**内存/DB 缓存**（★ 照搬 Jellyfin 的 `movie-{id}-{lang}` 缓存键）；③ 429 时读 `Retry-After` 指数退避；④ **绝不全库并发**，用队列（★ 照搬 `QueueRefresh` + `RefreshPriority`） |
| 3 | **语言回退没做全** | 中文标题有了、简介是空的；或海报是英文的 | ★ 三层回退：**元数据语言** → 字段为空时再请求 `en`；**图片语言** 用 `<lang>,null,en` 三段式（★ 照搬 `GetImageLanguagesParam`）；注意 ★ **`xx-YY` 的子标签必须大写**，否则 TMDB 不认 |
| 4 | **季集对不上** | S01E01 被识别成 E1 或没识别；Specials 跑到 S01 | ★ 照搬 §2.2.2 的**正则顺序**与两条防误判（结束集号后不能跟数字/`p`/`i`；必须 ≥ 起始集号）；★ `Season 01` 目录名严格要求（官方明确不许 `S01`/`SE01`）；★ Specials 要处理 `airs_before/after_*` 且 UI 要单独开一个"在所属季内显示"开关 |
| 5 | **同一部影片多个版本被当成两部** | `Movie (2021) - 1080p.mkv` 和 `Movie (2021) - 2160p.mkv` 变成两条 | ★ **文件名前缀必须与父目录名逐字符相同**（官方硬要求）；★ 判定用 `IsEligibleForMultiVersion` 的逻辑（去掉前缀后为空 / 以 `-_ .` 开头 / 以 `[` 开头）；★ 排序要区分"分辨率名（按分辨率降序）"与"具名版本（按字母序）" |
| 6 | **图片尺寸过多** | 一次拉 `original` 海报 5 MB，列表页卡死 | ★ 照搬 `TypeOptions` 默认值：**Primary 1 张、Backdrop 1 张且 MinWidth 1280、Logo 1 张**；列表用 `w342`、详情用 `w500`、背景用 `w1280`，**只有"查看原图"才用 original**；★ 图片缓存用 LRU 限总量 |
| 7 | **年份误提取** | `Movie.1920x1080.mkv` → 年份 1920 | ★ 保留 `CleanDateTime` 的前瞻 `(?![0-9]+|\W[0-9]{2}\W[0-9]{2})`；年份限定 `19xx`/`20xx` |
| 8 | **保留字符炸文件名** | 从 TMDB 拿到的标题带 `:`（如 `Mission: Impossible`），回写文件名/建目录失败 | ★ 官方明确保留字符：`< > : " / \ \| ? *`。回写前统一替换（如 `:` → ` -`）；**Windows 上还要额外处理结尾的 `.` 和空格、保留名 `CON`/`PRN`/`AUX`/`NUL`/`COM1..9`/`LPT1..9`** |
| 9 | **进度把"点开就关"记成已看** | 用户点开看一眼退出，影片进了"已看" | ★ 三段式阈值：`< 5%` 不记、`> 90%` 或 `剩余 < 1s` 算看完、**时长 < 300 s 直接算看完**；★ 失败退出要**显式标记 `failed=True` 且不记账**（照搬 `OnPlaybackStopped` 的 `if (playbackFailed) return false;`） |
| 10 | **ffmpeg 进程泄漏** | 反复 seek 后一堆 ffmpeg 僵尸进程 | ★ **停止播放时立刻杀转码进程**（照搬 `KillTranscodingJobs`）；★ 播放中定期 `Ping` 续命 + 无心跳超时自杀；★ `-nostdin` 且**关闭 stdin**（照搬 `RunSubtitleExtractionProcess`：不这样做，服务化运行时 ffmpeg 会阻塞在继承的 stdin 上）；★ 必须**异步 drain stderr**，否则管道缓冲写满会死锁 |
| 11 | **seek 后花屏/字幕不同步** | 跳到 30 分钟后画面卡住、字幕错位 | ★ remux 场景 seek 目标 **+0.5 s** 命中关键帧；★ seek 上限**夹到 `时长 - 5 s`**；★ 保留 `-copyts -avoid_negative_ts disabled`；★ HLS 必须 `-force_key_frames "expr:gte(t,n_forced*N)"` + GOP 限制 |
| 12 | **中文外挂字幕乱码** | GBK/Big5 字幕渲染成乱码 | ★ **探测编码**（`charset-normalizer` / `chardet`）后转 UTF-8；★ UTF-8/ASCII **短路**不做转换；★ 烧录时把编码作为 `:charenc=` 传给 `subtitles=` 滤镜；★ 注意 `.smi/.sami` + UTF-16 时**不能**指定 `-sub_charenc`（ffmpeg 会报错，Jellyfin 专门处理了这条） |
| 13 | **libass 找不到字体** | ASS 特效字幕显示成方块/默认宋体 | ★ 把 MKV 内嵌字体 **dump 到磁盘目录**，再用 `subtitles=...:fontsdir='<dir>'`（★ Jellyfin 从不 `-attach` 把字体 mux 进容器）；★ 抽取出的 ASS 把 `,Arial,` 换成 `,Arial Unicode MS,`（★ 照搬 `SetAssFont`） |
| 14 | **扫库把花絮当正片** | `trailers/xxx.mp4` 出现在电影列表里 | ★ 先识别 extras（目录名 + 特定文件名 + 后缀三类规则，★ 照搬 `ExtraRuleResolver` 的四种 `ExtraRuleType`）；★ **extras 必须在多版本/多部分归组之前剔除**（★ Jellyfin 单测 `TestStackedWithTrailer` 就是为了这个）；★ 后缀匹配前先 `TrimEnd(digits)` 以识别 `-trailer2` |
| 15 | **N+1 查询拖慢列表** | 100 部影片的演员表 = 100 次查询 | ★ **批量查**（★ 照搬 `GetPeopleByItems`）；★ `fields` 按需请求，列表页**不要**带 `People`/`MediaStreams`/`Chapters`；★ 关掉 `enableTotalRecordCount` 省一次 COUNT |
| 16 | **本地 NFO 被在线数据覆盖** | 手改的 `movie.nfo` 被刮削冲掉 | ★ **本地优先是硬规则**（官方："Local metadata will always be fetched and has priority over remote metadata providers"）；★ 再给用户"锁定字段"能力（★ 照搬 `LockedFields` / `MetadataField` 枚举：`Cast/Genres/ProductionLocations/Studios/Tags/Name/Overview/Runtime/OfficialRating`） |
| 17 | **刷新是全量重取，把手工修正冲掉** | 用户改过的简介被覆盖 | ★ 区分 `MetadataRefreshMode`：默认 `Default`（**只补缺失**），只有用户明确点"完全刷新"才用 `FullRefresh`；★ 网络设置变更时明确提示"只影响新内容，已有内容需手动刷新"（★ 官方 UI 文案） |
| 18 | **多集文件（S01E01-E02）** | 要么只入库一集，要么入库两集但文件重复计数 | ★ 官方明确：**显示为单个条目**、携带多集元数据；★ 存 `index_number_end`；★ 建议 UI 上允许"拆分为独立集"（官方也建议用 MKVToolNix 拆分） |
| 19 | **`Season 00` 的 specials 编号各家不同** | 换一个数据源后 specials 全错位 | ★ 官方逐字："Episode numbering for specials may vary from metadata provider to metadata provider."；★ 若提供方没有该 special，**用描述性名称而非 `S00Exy`**（官方建议） |
| 20 | **`.iso` / `VIDEO_TS` / `BDMV` 的预期管理** | 用户以为能直连播，结果每次都转码 | ★ 官方逐字：`VIDEO_TS`/`BDMV` 受支持但**不支持多版本/多部分/外挂字幕音轨**；★ `VIDEO_TS`/`BDMV` **永远不能 DirectPlay**（★ 源码 `StreamBuilder.cs:718-722`）；★ `.iso` "should work, but are not supported" |
| 21 | **音乐元数据以为靠文件名** | 自己按文件名刮削，结果全错 | ★ 官方逐字："Filenames generally do not matter since the info will be scraped from the embedded metadata of the tracks."；★ 必须读内嵌标签（`mutagen`）；★ 格式陷阱：纯音频 `.mp4`→改 `.m4a`、`.mkv`/`.webm`→改 `.mka`、`.weba` 不支持；★ ID3v1 字段 30 字节上限，用 ID3v2.4 |
| 22 | **HDR 内容转码后颜色发灰/过曝** | tonemap 没做或做错 | ★ 官方逐字："Jellyfin currently doesn't support HDR to HDR tone-mapping, or passing through HDR metadata."；★ 要么直连（不转码），要么老老实实 tonemap 到 SDR；★ 软件 tonemap 极慢，优先 `libplacebo`/硬件 |
| 23 | **码率参数按编码器乱写** | 用 `-b:v` 给 libx264 → 效果不对；给 VideoToolbox 加 `maxrate` → 编码器挂起 | ★ 照搬 §1.3.4(b) 的分支表：libx264/libx265 用 `-maxrate`（**不加 `-b:v`**）、QSV 用 `-b:v + -maxrate bitrate+1 + -rc_init_occupancy + -bufsize` 且 `-mbbrc 1`、VAAPI 用 `-rc_mode VBR/CBR`、VideoToolbox **只给 `-b:v` + `-qmin -1 -qmax -1`** |
| 24 | **以为服务端会生成多档码率（ABR）** | 做 4K/1080p/720p 三档自适应，发现 master.m3u8 只有一个 variant | ★ **Jellyfin 服务端不生成 ABR 多档**，码率阶梯在客户端（选一档 → 转一次）。要 ABR 得自己起多个 ffmpeg 进程拼多 variant，Jellyfin 这套**不提供参考** |
| 25 | **依赖 Jellyfin 的核心做片头识别** | 以为有内置"跳过片头"，结果什么都没有 | ★ **核心无任何内置 Intro 检测算法**，只有 `IMediaSegmentProvider` 插件接口 + `MediaSegments` 存储 + `GET /MediaSegments/{itemId}`。要跳过就用**章节名关键词**（最简单）或自己写识别 |

---

## 四、补充与更正（第二轮深挖后追加）

本节记录后续更细致核对中发现的新事实，以及**对前文的更正**。凡与前面章节冲突之处，**以本节为准**。

### 4.1 【更正】图片语言三段式回退的适用范围

**前文 §2.3.3(d) 表述不够精确，此处更正。**

`TmdbUtils.GetImageLanguagesParam`（= `<首选语言>,null,en`）**确实存在**，但**只在"元数据抓取"路径上传入 TMDB**；**专门的图片 provider 传的是 `null`**。已逐行核实：

| 调用方 | 行 | 传入的 `language` / `imageLanguages` / `countryCode` |
|---|---|---|
| **元数据**：`TmdbMovieProvider` | `:193` | `info.MetadataLanguage, GetImageLanguagesParam(info.MetadataLanguage, info.MetadataCountryCode), info.MetadataCountryCode` ← **三段式在这里生效** |
| **元数据**：`TmdbSeriesProvider` | `:224` | 同上（`GetImageLanguagesParam(...)`） |
| **元数据**：`TmdbSeriesProvider`（搜索） | `:60` | `searchInfo.MetadataLanguage, searchInfo.MetadataLanguage, searchInfo.MetadataCountryCode` ← ⚠️ 把语言当成了 `imageLanguages` 传，**看起来是缺陷**（正确应为 `GetImageLanguagesParam(...)`） |
| **图片**：`TmdbMovieImageProvider` | `:85` | `.GetMovieAsync(movieTmdbId, null, null, null, ct)` ← **全部 null** |
| **图片**：`TmdbSeriesImageProvider` | `:67` | `.GetSeriesAsync(tmdbId, null, null, null, ct)` ← **全部 null** |
| **图片**：`TmdbSeasonImageProvider` | `:69` | `.GetSeasonAsync(seriesTmdbId, season.IndexNumber.Value, null, null, null, ct)` ← **全部 null** |

源码里还留着对应 TODO（`TmdbMovieImageProvider.cs:83-84`）：
```csharp
// TODO use image languages if All Languages isn't toggled, but there's currently no way to get that value in here
```

**那图片语言在哪里过滤？** 在 `MediaBrowser.Providers/Manager/ProviderManager.cs:355-365`（**已核实**）：
```csharp
if (!includeAllLanguages && hasPreferredLanguage)
{
    // Filter out languages that do not match the preferred languages.
    //
    // TODO: should exception case of "en" (English) eventually be removed?
    result = result.Where(i => string.IsNullOrWhiteSpace(i.Language) ||
                               string.Equals(preferredLanguage, i.Language, StringComparison.OrdinalIgnoreCase) ||
                               string.Equals(i.Language, "en", StringComparison.OrdinalIgnoreCase));
}
return result.OrderByLanguageDescending(preferredLanguage);
```

**修正后的正确理解（三层）**：
1. **元数据请求** → 带上 `include_image_language=<lang>,null,en`，让 TMDB 侧就按这个优先级返回图片；**这是三段式真正生效的地方**。
2. **专门取图片** → 不传语言（拿回全部），由**服务端**过滤：保留"无语言"图 + 首选语言图 + **硬编码的英文图**。
3. **排序** → `OrderByLanguageDescending(preferredLanguage)`。

> **★ 对自研实现的启示（结论比原文更强）**：不要依赖服务端过滤。**最省事且可控的做法是自己在请求时就带 `include_image_language=<lang>,null,en`**（照搬 Jellyfin 的元数据路径），然后在本地按 `(语言匹配 → 分辨率 → vote_average)` 排序取前 N 张。Jellyfin 之所以在服务端再过滤一次，是因为它的图片 provider 架构无法把语言传进 HTTP 层（源码 TODO 已自认）。

### 4.2 【更正】`OfficialRating` 的字符串格式有明确规范

前文 §2.5.3 我说"不给出 endpoint 字段级别的断言"。现在可以给出**确定的格式规范**（这是 Jellyfin 自己的序列化约定，与端点无关）：

```csharp
// MediaBrowser.Providers/Plugins/Tmdb/TmdbUtils.cs:508-515（已逐行核实）
public static string BuildParentalRating(string countryCode, string ratingValue)
{
    // Exclude US because we store US values as TV-14 without the country code.
    var ratingPrefix = string.Equals(countryCode, "US", StringComparison.OrdinalIgnoreCase)
        ? string.Empty
        : countryCode + "-";
    var newRating = ratingPrefix + ratingValue;

    return newRating.Replace("DE-", "FSK-", StringComparison.OrdinalIgnoreCase);
}
```

**规范总结**（可直接照抄到自研实现）：

| 规则 | 说明 |
|---|---|
| 通用格式 | **`[国家码-]分级值`**，如 `GB-15`、`JP-R15`、`FR-12` |
| **US 特例** | **不带国家码前缀**（源码注释逐字："we store US values as TV-14 without the country code"）→ 存 `PG-13`、`R`、`TV-14` |
| **德国特例** | `DE-` **改写为 `FSK-`** → 存 `FSK-18` 而**不是** `DE-FSK-18` |

**US 风格字面量的另外两个独立证据**：
```csharp
// src/Jellyfin.LiveTv/Channels/ChannelManager.cs:523-533
private static string GetOfficialRating(ChannelParentalRating rating)
    => rating switch
    {
        ChannelParentalRating.Adult  => "XXX",
        ChannelParentalRating.UsR    => "R",
        ChannelParentalRating.UsPG13 => "PG-13",
        ChannelParentalRating.UsPG   => "PG",
        _ => null
    };
```
```csharp
// src/Jellyfin.LiveTv/Listings/SchedulesDirect.cs:341-351
// 把 TVMA 规范成 TV-MA，并过滤 N/A / Approved / Not Rated / Passed
details.ContentRating[0].Code.Replace("TV", "TV-").Replace("--", "-")
```

**分级 → 数值的内部换算**（供"家长控制"用，自研若不需要可忽略）：`Emby.Server.Implementations/Localization/Ratings/*.json`（40+ 国家）被加载为 `Dictionary<string, ParentalRatingScore?>`；`us.json` 实测样例：
```json
{ "ratingStrings": ["PG-13"], "ratingScore": { "score": 13, "subScore": 0 } }
{ "ratingStrings": ["NC-17","TV-MA","TV-MA-L",...], "ratingScore": { "score": 17, "subScore": 1 } }
```
`de.json` 的 `["6","FSK 6","FSK-6"]` 直接印证 `DE-`→`FSK-` 改写。查找 API：`LocalizationManager.GetRatingScore(rating, countryCode)`；未分级词表 `_unratedValues = ["n/a","unrated","not rated","nr"]`。
派生字段：`BaseItem.InheritedParentalRatingValue` / `InheritedParentalRatingSubValue`（`int?`，**是 DB 列**）。父级（BoxSet/Playlist）会继承子级里最宽松的分级。

### 4.3 【补充】`CommunityRating` 是 0–10 分制 —— 有确定证据

前文 §2.5.2 我标注为"推断"。现在有**直接的源码范围校验**：

```csharp
// MediaBrowser.XbmcMetadata/Parsers/BaseNfoParser.cs:546-553（已逐行核实）
// For NFO files that have a separate community rating tag instead of using the ratings node with a name, or standard rating tag
case "communityrating":
    var communityRatingText = reader.ReadElementContentAsString().Replace(',', '.');
    if (float.TryParse(communityRatingText, NumberStyles.AllowDecimalPoint, CultureInfo.InvariantCulture, out var communityRatingValue)
    && communityRatingValue >= 0 && communityRatingValue <= 10)
    {
        item.CommunityRating = communityRatingValue;
    }
    break;
```

配套单测存在：`tests/Jellyfin.XbmcMetadata.Tests/Parsers/MovieNfoParserTests.cs`（`7.5` 通过、`15.5` 被忽略、`7,5` 逗号归一），测试数据 `CommunityRating.nfo` / `CommunityRating_OutOfRange.nfo` / `CommunityRating_Comma.nfo`。

TMDB 侧**直接赋值不缩放**：
```csharp
// TmdbMovieProvider.cs:224
movie.CommunityRating = Convert.ToSingle(movieResult.VoteAverage);
// TmdbSeriesProvider.cs:258 / TmdbEpisodeProvider.cs:188 同构
```

**结论**：`CommunityRating` 的值域约定是 **0–10**，与 TMDB `vote_average` 一致。
**两个已知的语义污染点**（自研时注意，不要盲目信任这个字段）：
- NFO 的 `<rating>`（`BaseNfoParser.cs:534-540`）与 `<ratings><rating>`（`:966-985`）**没有**范围校验，可写入 >10 的值。
- `Emby.Photos/PhotoProvider.cs:98`：`item.CommunityRating = image.ImageTag.Rating;` —— EXIF Rating 通常是 **0–5 星**，写入侧**无缩放无校验**。

### 4.4 【补充】`CriticRating` 的来源与量纲（**与 CommunityRating 不同量纲！**）

**唯一外部来源是 OMDb 的 Rotten Tomatoes 分数**；本仓库树内**没有**独立的 Rotten Tomatoes / Metacritic provider。

```csharp
// MediaBrowser.Providers/Plugins/Omdb/OmdbProvider.cs:598-614（已逐行核实）
public float? GetRottenTomatoScore()
{
    if (Ratings is not null)
    {
        var rating = Ratings.FirstOrDefault(i => string.Equals(i.Source, "Rotten Tomatoes", StringComparison.OrdinalIgnoreCase));
        if (rating?.Value is not null)
        {
            var value = rating.Value.TrimEnd('%');
            if (float.TryParse(value, CultureInfo.InvariantCulture, out var score))
            {
                return score;
            }
        }
    }
    return null;
}
```

> **关键结论**：`CriticRating` 存的是**去掉 `%` 后的 0–100 原值，不做归一化**。
> ⇒ **`CommunityRating` 是 0–10，`CriticRating` 是 0–100 —— 两者量纲不同！** UI 展示时**必须**分别处理（`CommunityRating` 直接显示，`CriticRating` 需除以 10 或按百分比显示）。这是极易踩的坑。
> 另：`OmdbProvider` 声明了 `public string Metascore { get; set; }`，但**全仓库无任何代码读取它**（写了个字段没用）。

### 4.5 【补充】`MetadataField` 里**只有 `OfficialRating` 能被锁定**

```csharp
// MediaBrowser.Model/Entities/MetadataField.cs:6-52
Cast, Genres, ProductionLocations, Studios, Tags, Name, Overview, Runtime, OfficialRating
```

⇒ **`CommunityRating` / `CriticRating` / `CustomRating` 无法通过 `LockedFields` 保护**，全量刷新时会被覆盖。
`ItemFields` 枚举里**只有 `CustomRating`**，**没有** `OfficialRating` / `CommunityRating` / `CriticRating`（即 `fields` 查询参数控制不了这三个字段的返回）。

**受 `LockedFields` 保护的合并字段**（`MetadataService.MergeBaseItemData`）：`Name`、`Genres`、`OfficialRating`、`Overview`、`Runtime`、`Studios`、`Tags`、`ProductionLocations`、`Cast`。
其余字段（`OriginalTitle`、`HomePageUrl`、`OriginalLanguage`、`CommunityRating`、`EndDate`、`IndexNumber`、`CustomRating`、`Tagline`、`ParentIndexNumber`、`PremiereDate`、`ProductionYear`）**只受 `replaceData || 目标为空` 保护**。

### 4.6 【补充】`ProviderIds` 有格式白名单（**防止"脏 id 毒化刷新"**）

`MediaBrowser.Model/Entities/ProviderIdsExtensions.cs`：
```csharp
private static readonly Dictionary<string, Func<string, bool>> _providerIdValidators =
    new(StringComparer.OrdinalIgnoreCase)
    {
        [MetadataProvider.Imdb.ToString()]             = value => ImdbIdRegex().IsMatch(value),
        [MetadataProvider.Tmdb.ToString()]             = IsPositiveNumber,
        [MetadataProvider.TmdbCollection.ToString()]   = IsPositiveNumber,
        [MetadataProvider.AudioDbArtist.ToString()]    = IsPositiveNumber,
        [MetadataProvider.AudioDbAlbum.ToString()]     = IsPositiveNumber,
        [MetadataProvider.MusicBrainzAlbum.ToString()]          = IsGuid,
        [MetadataProvider.MusicBrainzAlbumArtist.ToString()]    = IsGuid,
        [MetadataProvider.MusicBrainzArtist.ToString()]         = IsGuid,
        [MetadataProvider.MusicBrainzReleaseGroup.ToString()]   = IsGuid,
        [MetadataProvider.MusicBrainzRecording.ToString()]      = IsGuid,
        [MetadataProvider.MusicBrainzTrack.ToString()]          = IsGuid
    };

[GeneratedRegex(@"^(tt|nm|co|ev|ch|ni)?[0-9]+$", RegexOptions.IgnoreCase)]
private static partial Regex ImdbIdRegex();
private static bool IsPositiveNumber(string value) => int.TryParse(value, NumberStyles.None, ...) && id > 0;
private static bool IsGuid(string value) => Guid.TryParse(value, ...);
```
设计动机（源码注释逐字）：
> "Providers regularly hand out an id belonging to a different service, e.g. an IMDb person id in the TMDb field. Such an id is not just useless, it also makes the owning provider fail for the item."

`TrySetProviderId` 额外拒绝**名字含 `=`** 的值（注释："When name contains a '=' it can't be deserialized from the database"）。
`MetadataService` 的合并逻辑里，**非法 id 永远进不来，且已有的非法 id 会被主动清除**（注释："A bad id no provider offered a replacement for still has to go, otherwise the item keeps failing the same way on every refresh."）。

> **★ 对自研实现的启示（很值钱）**：自研刮削器**必须**做 provider id 的类型校验。真实场景里 TMDB 会返回 `imdb_id`，TVDb 会返回 `imdb_id`，很多数据源会把**人名 id**（`nm0000123`）塞进影片的 imdb 字段。不做校验的话，下次刷新会一直拿错 id 去查、一直失败，而且因为"id 已存在就不覆盖"，**会永久卡死**。Jellyfin 专门为此写了白名单 + "清污"逻辑，值得照抄。

### 4.7 【补充】`MetadataRefreshMode` 的精确生效条件

`runAllProviders` 的判定（`MediaBrowser.Providers/Manager/MetadataService.cs:652-658`）：
```csharp
var metadataRefreshMode = options.MetadataRefreshMode;
// Run all if either of these flags are true
var runAllProviders = options.ReplaceAllMetadata ||
    metadataRefreshMode == MetadataRefreshMode.FullRefresh ||
    (isFirstRefresh && metadataRefreshMode >= MetadataRefreshMode.Default) ||
    (requiresRefresh && metadataRefreshMode >= MetadataRefreshMode.Default);
```
其中 `isFirstRefresh = item.DateLastRefreshed == DateTime.MinValue`；`requiresRefresh = libraryOptions.AutomaticRefreshIntervalDays > 0 && (now - DateLastRefreshed).TotalDays >= interval`。

**⚠️ 注意枚举顺序的语义**：数值是 `None=0 < ValidationOnly=1 < Default=2 < FullRefresh=3`，所以 **`ValidationOnly` 比 `Default` 更"轻"**。`ValidationOnly` 时**远程 provider 一律不跑**（`GetProviders` 里显式 `return false`），只做本地扫描/校验。

**刷新戳落库的细节**（`:216-231`）：`attemptedFetch = MetadataRefreshMode > ValidationOnly || ImageRefreshMode > ValidationOnly`；两者都成功且 `attemptedFetch` 才更新 `DateLastRefreshed`。注释解释了必要性：
> 全量刷新时若所有 provider 都空手而归，不落戳的话下次还会重复同样的无效查询。

### 4.8 【补充】两个 Refresh 接口的真实行为差异（**很容易误判**）

| 接口 | 真实行为 |
|---|---|
| `POST /Items/{itemId}/Refresh` | **异步入队**：`ProviderManager.QueueRefresh(item.Id, refreshOptions, RefreshPriority.High)` → 立即 204。`IsAutomated = false`；`ForceSave` 由 `FullRefresh/replaceAllImages/replaceAllMetadata` **推导**（客户端无法直传）；**没有 `isScheduledTask` 参数** |
| `POST /Library/Refresh` | **它并不真正扫描**！`LibraryController.RefreshLibrary()`（`:340-352`）调的是 `ILibraryManager.ValidateMediaLibrary(...)`，而该实现是：<br>`Emby.Server.Implementations/Library/LibraryManager.cs:1404-1410`<br>`// Just run the scheduled task so that the user can see it`<br>`_taskManager.CancelIfRunningAndQueue<RefreshMediaLibraryTask>();`<br>即**只是把一个定时任务排队**，让用户在 Dashboard 看到进度。真正干活的是 `ValidateMediaLibraryInternal`（`:1418-1434`）与 `QueueLibraryScan()`（`:3278-3281`） |

**不存在** `POST /Items/{itemId}/Metadata/Refresh`（全仓库零命中）；**不存在** `replaceThumbnailImages` 参数；`imageRefreshMode` 的类型就是 `MetadataRefreshMode`（**没有独立的 `ImageRefreshMode` 枚举**）。

**图片 API 的实际位置**（不在 `ItemImageController`，该文件不存在）：
- `ImageController.cs`：`DELETE Items/{itemId}/Images/{imageType}[/{imageIndex}]`、`POST Items/{itemId}/Images/{imageType}[/{imageIndex}]`、`POST .../{imageIndex}/Index`、`GET Items/{itemId}/Images`、`GET .../{imageType}[/{imageIndex}[/{tag}/{format}/{maxWidth}/{maxHeight}/{percentPlayed}/{unplayedCount}]]` —— **写操作全部 `RequiresElevation`**
- `RemoteImageController.cs`：`GET Items/{itemId}/RemoteImages`（参数 `type`/`startIndex`/`limit`/`providerName`/`includeAllLanguages`）、`GET Items/{itemId}/RemoteImages/Providers`、`POST Items/{itemId}/RemoteImages/Download`（`RequiresElevation`）

### 4.9 【补充】`Identify` API 的精确契约

`Jellyfin.Api/Controllers/ItemLookupController.cs`（类级 `[Route("")]` + `[Authorize]`）：

| 方法 | 路径 | 授权 |
|---|---|---|
| GET | `Items/{itemId}/ExternalIdInfos` | RequiresElevation |
| POST | `Items/RemoteSearch/Movie` | 用户 |
| POST | `Items/RemoteSearch/Trailer` | 用户 |
| POST | `Items/RemoteSearch/MusicVideo` | 用户 |
| POST | `Items/RemoteSearch/Series` | 用户 |
| POST | `Items/RemoteSearch/BoxSet` | 用户 |
| POST | `Items/RemoteSearch/MusicArtist` | 用户 |
| POST | `Items/RemoteSearch/MusicAlbum` | 用户 |
| POST | `Items/RemoteSearch/Person` | RequiresElevation |
| POST | `Items/RemoteSearch/Book` | 用户 |
| POST | **`Items/RemoteSearch/Apply/{itemId}`** | RequiresElevation |

**注意**：搜索是**按媒体类型分路径**的（`/RemoteSearch/Movie`、`/RemoteSearch/Series`…），**不是**统一的 `/RemoteSearch/{providerName}`。请求体是 `RemoteSearchQuery<T>`：`SearchInfo`（`ItemLookupInfo`）、`ItemId`（Guid）、`SearchProviderName`（string）、`IncludeDisabledProviders`（bool）。

`ApplySearchCriteria`（`:254-281`）**是同步的**（不是 QueueRefresh），204 在刷新完成后才返回：
```csharp
item.SetProviderIds(searchResult.ProviderIds);        // ★ 先显式写 id（因为刷新过程不会擦除 id）
await _providerManager.RefreshFullItem(item, new MetadataRefreshOptions(new DirectoryService(_fileSystem))
{
    MetadataRefreshMode = MetadataRefreshMode.FullRefresh,
    ImageRefreshMode   = MetadataRefreshMode.FullRefresh,
    ReplaceAllMetadata = true,
    ReplaceAllImages   = replaceAllImages,             // query 参数，默认 true
    SearchResult       = searchResult,
    RemoveOldMetadata  = true
}, CancellationToken.None);
return NoContent();
```

`IExternalId` **只有 4 个成员**：`ProviderName`、`Key`、`ExternalIdMediaType? Type`、`bool Supports(IHasProviderIds)` —— **没有 `UrlFormatString`**（URL 生成由独立的 `IExternalUrlProvider` 负责，TMDB 的实现是 `TmdbExternalUrlProvider.cs`，基址 `https://www.themoviedb.org/`）。
⚠️ `MetadataProvider.Tvdb` / `TvRage` / `TvMaze` / `Tvcom` **没有任何 `IExternalId` 实现**，所以客户端元数据编辑器里看不到这些字段（尽管解析器会写入 Tvdb/TvMaze 的 id）。

### 4.10 【补充】字幕**写入侧**的命名规则（与读取侧不对称）

`MediaBrowser.Providers/Subtitles/SubtitleManager.cs:210-240`（已逐行核实）：
```csharp
var language = response.Language.ToLowerInvariant();                    // :211
if (language.AsSpan().IndexOfAny(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) >= 0)
    throw new ArgumentException("Language contains invalid characters.");  // :212-215 防目录穿越

var saveFileName = Path.GetFileNameWithoutExtension(video.Path) + "." + language;   // :217
if (response.IsForced)          saveFileName += ".forced";              // :219-222
if (response.IsHearingImpaired) saveFileName += ".sdh";                 // :224-227
// saveInMediaFolder -> ContainingFolderPath，否则 GetInternalMetadataPath()
// :244-309 TrySaveToFiles: 扩展名必须在 SubtitleFileExtensions 白名单内；路径必须位于上述两个根之下；
//          已存在则追加 ".{counter}.{ext}"
```

> **读写不对称（代码事实）**：读取侧识别 `.default`（→ `IsDefault`）、`.forced`/`.foreign`、`.cc`/`.hi`/`.sdh`；**写入侧只产出 `.forced` 与 `.sdh`，从不写 `.default`**。
> 实际产物形如：`Movie.zh.srt`、`Movie.zh.forced.srt`、`Movie.zh.forced.sdh.srt`、`Movie.zh.0.srt`（冲突计数）。
> `SaveSubtitlesWithMedia`（`LibraryOptions`，ctor 默认 `true`）决定落媒体目录还是内部元数据目录。

### 4.11 【补充】本地图片扫描的**文件名候选清单与扩展名优先级**

权威实现：`MediaBrowser.LocalMetadata/Images/LocalImageProvider.cs`（`Order => 0`，`Name => "Local Images"`）。

**候选名清单（逐字）**：
```csharp
private static readonly string[] _commonImageFileNames = { "poster", "folder", "cover", "default" };
private static readonly string[] _musicImageFileNames  = { "folder", "poster", "cover", "jacket", "default", "albumart" };
private static readonly string[] _personImageFileNames = { "folder", "poster" };
private static readonly string[] _seriesImageFileNames = { "poster", "folder", "cover", "default", "show" };
private static readonly string[] _videoImageFileNames  = { "poster", "folder", "cover", "default", "movie" };
```
（与官方文档的文件名表一致，但**多了 `albumart`**。）

**各类别的读取文件名**：
| 类别 | 文件名（按尝试顺序） |
|---|---|
| Primary | `{视频文件名}` → 类型表（见上）→ 最后（非混合目录）裸名 |
| **Logo** | `logo` → **回退 `clearlogo`** |
| **Art** | **`clearart`** |
| Disc | 音乐专辑：`cdart` → `disc`；视频/合集：`disc` → `cdart` → **`discart`** |
| Banner | `banner` |
| Thumb | `landscape` → 回退 `thumb` |
| Backdrop | `{名}-fanart` → `fanart`/`fanart-1..20` → `background`/`background-1..20` → `art`/`art-1..20` → `extrafanart/` 目录内全部 → **最后** `backdrop`/`backdrop1..20` |
| Season（从**剧集目录**读） | `{seasonName 去空格小写 \| season{00} \| season-specials}-poster/-fanart/-banner/-landscape` |
| Episode | `{视频文件名}.ext`、`{视频文件名}-thumb.ext`（同时扫视频目录与 `metadata/` 子目录） |

**扩展名白名单与优先级**（`BaseItem.SupportedImageExtensions`）：
```csharp
public static readonly string[] SupportedImageExtensions = [".png", ".jpg", ".jpeg", ".webp", ".tbn", ".gif", ".svg"];
```
```csharp
// LocalImageProvider.cs:126-127 —— 按白名单顺序排序
.OrderBy(i => Array.IndexOf(BaseItem.SupportedImageExtensions, i.Extension ?? string.Empty));
```
> **⚠️ 一个容易被忽略的细节**：因为是按白名单顺序排序，**同名前缀下 `.png` 会排在 `.jpg` 之前** —— 即同时存在 `poster.png` 与 `poster.jpg` 时，**`poster.png` 胜出**。自研实现若只按"字典序"或"文件系统顺序"取图，行为会与 Jellyfin 不一致。
> 编号扫描只到 **20**，且**连续 3 个缺失即停止**（不是扫满 20 个）。

**⚠️ 一个语义陷阱**：`ImageType.Art` 与文件名 `art.*` **不对应**。
- **写** `ImageType.Art` 用文件名 **`clearart`**；
- **读** 时 `art.jpg`/`art-1.jpg` 被 `PopulateBackdrops` 判为 **Backdrop**，不是 `Art`。
- `ImageType.Art` 的本地来源**只有 `clearart`**；`discart` 也只读不写（写的是 `disc`/`cdart`）；`ImageType.Screenshot` 在本版本**无任何写入/扫描点**。

**每个 ImageType 的磁盘文件名（汇总）**：

| ImageType | 本地（影片目录） | 本地（mixed folder） | Season（写在剧集目录） | Episode | 内部元数据目录 |
|---|---|---|---|---|---|
| `Primary` | `poster.ext`（音乐/人物/相册/Person/Legacy 约定 ⇒ `folder.ext`） | `{videoname}-poster.ext` | `season01-poster.ext` / `season-specials-poster.ext` | `{videoname}-thumb.ext` | `poster.ext` |
| `Backdrop` | `backdrop.ext`, `backdrop1.ext`, `backdrop2.ext`… | `{videoname}-backdrop.ext` | `season01-fanart.ext`（仅 index 0） | — | `backdrop.ext` |
| `Art` | `clearart.ext` | `{videoname}-clearart.ext` | — | — | `clearart.ext` |
| `Thumb` | `landscape.ext` | `{videoname}-landscape.ext` | `season01-landscape.ext` | — | `landscape.ext` |
| `Banner` | `banner.ext` | `{videoname}-banner.ext` | `season01-banner.ext` | — | `banner.ext` |
| `Logo` | `logo.ext` | `{videoname}-logo.ext` | `season01-logo.ext` | — | `logo.ext` |
| `Disc` | 音乐专辑 `cdart.ext`，否则 `disc.ext` | 同 | — | — | 同 |
| `BoxRear` | `back.ext` | `{videoname}-back.ext` | — | — | `back.ext` |
| `Box`/`Menu`/`Chapter`/`Profile` | `box.ext`/`menu.ext`/`chapter.ext`/`profile.ext`（枚举名小写兜底） | 同前缀 | — | — | 同 |

**扩展名由源 mime 决定**（`MimeTypes.ToExtension(mimeType)`），`.jpeg` 归一为 `.jpg`（且**该归一化只在 `GetStandardSavePath` 生效，`GetCompatibleSavePaths` 没有**）。
> **诚实标注**：仓库内可证实的图像 mime 映射只有 `image/jpeg→.jpg`、`image/jpg→.jpg`、`image/tiff→.tiff`、`image/x-png→.png`、`image/x-icon→.ico`（`MediaBrowser.Model/Net/MimeTypes.cs:106-148`）以及 `.tbn→image/jpeg`（`:79`）。**`image/png`/`image/webp` 的映射落在外包 NuGet 包 `MimeTypes` 2.5.2 里，该包源码不在仓库内**，因此"某个 png 源图最终一定存成 `poster.png`"这一步的完整实现表**无法在本仓库内证实**。同时**不存在**任何 "ImageType→扩展名" 的硬编码映射表（"poster 一定 jpg、logo 一定 png"是错的）。

**本地保存的五个前置条件**（`ImageSaver.cs:89-134`）：`SupportsLocalMetadata` && `IsSaveLocalMetadataEnabled()` && 非 `ExtraType` && (是 `AudioBook` 或 **不是** `Audio`) && （Episode 则只能是 `Primary`）&& `IsFileProtocol`（**虚拟 Season 例外**：若其剧集允许本地保存，则仍存本地）。

### 4.12 【补充】`ImageResolution` 枚举与 ffmpeg 映射

`MediaBrowser.Model/**Drawing**/ImageResolution.cs`（**注意不是 `Entities/`**）：
```csharp
public enum ImageResolution
{
    MatchSource = 0, P144 = 1, P240 = 2, P360 = 3, P480 = 4, P720 = 5, P1080 = 6, P1440 = 7, P2160 = 8
}
```
唯一活跃使用点：`ServerConfiguration.ChapterImageResolution`（默认 `MatchSource`）→ `MediaEncoder.GetImageResolutionParameter()`（`MediaBrowser.MediaEncoding/Encoder/MediaEncoder.cs:651-672`）：`P144 => "256x144"` … `P2160 => "3840x2160"`，`MatchSource => string.Empty`（不加参数），非空时拼 `" -s " + param`。
⇒ **章节图/预览图的尺寸是靠 ffmpeg 的 `-s WxH` 硬缩放实现的**。

### 4.13 【补充】图片相关的其它实现细节

| 事项 | 事实 |
|---|---|
| **内嵌图 provider 的显示名** | `Name => "Embedded Image Extractor"`（`MediaBrowser.Providers/MediaInfo/EmbeddedImageProvider.cs:69`），`Order => 99`（`:73`）。**这正是官方文档里唯一出现的图片源开关名**，二者对上了 |
| 内嵌图候选标签 | Primary: `poster`/`folder`/`cover`/`default`/`movie`/`show`；Backdrop: `backdrop`/`background`/`art`；Logo: `logo`（`:29-49`） |
| 内嵌图格式推断 | 由 codec 推（`:178-186`）；由附件 mime/扩展名推（`:207-214`，`.bmp/.gif/.png/.webp` 之外一律 `Jpg`） |
| 每类型数量上限的用法 | `ItemImageProvider.RefreshImages`：`var backdropLimit = typeOptions.GetLimit(ImageType.Backdrop) + oldBackdropImages.Length;`（`:160`，注释 "track library limits, adding buffer to allow lazy replacing of current images"） |
| `ImageOption`（**单数**） | `MediaBrowser.Model/Configuration/ImageOption.cs`：`ImageType Type`、`int Limit`（构造默认 **1**）、`int MinWidth`。**不存在 `ImageOptions` 类型** |
| `ImageInfo` | 实际路径 `MediaBrowser.Model/**Dto**/ImageInfo.cs`：`ImageType`/`ImageIndex`/`ImageTag`/`Path`/`BlurHash`/`Height`/`Width`/`Size` |
| `IImageEncoder` | 路径是 `MediaBrowser.Controller/**Drawing**/IImageEncoder.cs`（不是 `MediaEncoding/`）。实现：`src/Jellyfin.Drawing.Skia/SkiaEncoder.cs`（`Name => "Skia"`，`SupportedOutputFormats = {Webp, Jpg, Png, Svg}`）与 `src/Jellyfin.Drawing/NullImageEncoder.cs`（`SupportsImageEncoding => false`，其余抛 `NotImplementedException`）。**接口里没有 `ResizeImage`**（只有 `SkiaEncoder` 的 `internal static ResizeImage`，下采样时做 3×3 锐化） |
| `ImageSaver` 与 `IImageEncoder` 的关系 | **`ImageSaver` 不使用 `IImageEncoder`** —— 它只按 mime 推扩展名后直接落盘。`IImageEncoder` 用于 HTTP 图片缩放/转码与 trickplay 生成 |
| NFO 内图片路径开关 | `XbmcMetadataOptions.SaveImagePathsInNfo`（默认 **true**）；它**同时改变 saver 的 `MinimumUpdateType`**（`SaveImagePathsInNfo ? ImageUpdate : MetadataDownload`），因此也改变 `IsEnabledFor` 的门槛 |
| NFO `<thumb aspect>` → ImageType | `banner`→Banner、`clearlogo`→Logo、`discart`→Disc、`landscape`→Thumb、`clearart`→Art、`fanart`→Backdrop、其它（含 `poster`）→Primary。**aspect 含 `.` 直接跳过**（无法为季/集/合集设图） |
| NFO 图片写入位置 | 每种类型**只写第一张**（官方文档："only the first `thumb` tag for each artwork type is used"） |
| 图片下载并发 | `SaveImage(item, url, ...)` 用 `AsyncKeyedLocker<string>`（`PoolSize = 20`）**按 URL 串行**，并用 `IMemoryCache` 缓存 10 秒 |
| `GetAvailableRemoteImages` | 这里**是并行的**（`Task.WhenAll(providers.Select(...))`），与 refresh 流程的**严格串行**不同 |

### 4.14 【补充】三个"发现/解析"层面的联动（自研时容易漏）

1. **本地 NFO 文件名会反向影响 item 的解析类型**（不只是元数据来源）：
   - `SeriesResolver.cs:84`：以 **`tvshow.nfo`** 作为"这是剧集目录"的标志
   - `MusicArtistResolver.cs:77`：以 **`artist.nfo`** 作为艺术家标志
   - `MovieResolver.cs:258-259`：遇到 **`tvshow.nfo` / `season.nfo`** 直接返回 `null`，避免把剧集目录误判为电影
   > ⇒ 自研扫描器必须在"判定目录类型"阶段就检查这些 NFO 文件名，而不是只把它们当元数据源。
2. **trickplay 目录会被库扫描忽略**（防止把瓦片图当媒体）：
   `Emby.Server.Implementations/Library/IgnorePatterns.cs:72-74`：`"**/*.trickplay"`、`"**/*.trickplay/**"`；同段 `:48-50` 还有 `"**/metadata/**"`、`"**/metadata"`。
   ⇒ **自研扫描器也要把 `.trickplay` 与 `metadata` 目录加入忽略列表**，否则"保存到媒体目录旁边"的 trickplay 图会被当成图片媒体入库。
3. **`MediaBrowser.LocalMetadata` 已不再提供 `movie.xml`/`series.xml`/`episode.xml`**：`Savers/` 目录只剩 3 个文件，`Providers/` 只剩 2 个，即**只有 BoxSet 的 `collection.xml` 与 Playlist 的 `playlist.xml`**（`grep "movie\.xml"` 在两个项目下零命中）。`XmlProviderUtils.Name => "Emby Xml"` 与 `BaseXmlProvider.Order => 1`（注释 `// After Nfo`）仍在，但已无 Movie/Series/Episode 的使用者。
   ⇒ 自研若要兼容"Emby 老 XML"，需自行实现；**不要**假设 Jellyfin 还会写这些文件。

### 4.15 【补充】TMDB 端点与 `append_to_response` 的真实来源（**证据边界**）

前文 §3.3 我标注过这一点，这里给出更精确的说明：

- Jellyfin 通过 NuGet 包 **`TMDbLib` 3.0.0** 访问 TMDB（`Directory.Packages.props`、`MediaBrowser.Providers.csproj` 引用）。**该包源码不在本仓库树内**，因此 `TmdbClientManager.cs` 里**看不到任何形如 `/3/movie/{id}` 的 URL 字面量**。
- Jellyfin 侧能确定的是**语义化的 `extraMethods`**（即 `append_to_response` 的位标志）：
  | Jellyfin 方法 | `extraMethods` |
  |---|---|
  | `GetMovieAsync`（`:67-96`） | `Credits \| Releases \| Images \| Videos`（`:77`）+ `Keywords`（若 `ExcludeTagsMovies` 为假） |
  | `GetCollectionAsync`（`:107`） | `Images` |
  | `GetSeriesAsync`（`:141`） | `Credits \| CreditsAggregate \| Images \| ExternalIds \| Videos \| ContentRatings \| EpisodeGroups`（`:151`）+ `Keywords` |
  | `GetSeasonAsync`（`:238`） | `Credits \| Images \| ExternalIds \| Videos`（`:253`） |
  | `GetEpisodeAsync`（`:276`） | `Credits \| Images \| ExternalIds \| Videos`（`:305`） |
  | `GetPersonAsync`（`:324`） | `Images \| ExternalIds` |
- 查询参数名（`api_key`、`language`、`include_image_language`、`append_to_response`、`include_adult`、`page`、`year`、`first_air_date_year`、`region`、`query`、`external_source`…）同样来自该包。
- **图片 host 不是字面量**：来自 `GET configuration` 响应的 `images.secure_base_url`（经 `_tmDbClient.GetImageUrl(size, path, true)`，第三参数 `true` = 用 https）。`TmdbController` 的 `GET ClientConfiguration` 端点把这个尺寸表暴露给前端。
- **尺寸有效性校验**：`TmdbClientManager.EnsureClientConfigAsync`（`:702-755`）在首次调用前 `GetConfigAsync()`；若配置的 size 不在 TMDB 返回的 `PosterSizes/BackdropSizes/LogoSizes/ProfileSizes/StillSizes` 中，就**改写成该列表的最后一项**（通常最大）。`GetUrl` 的兜底：`size` 为空 ⇒ 用 `"original"`。
- **缩略图尺寸表**（硬编码，`TmdbClientManager.cs:32-38`）：`Primary → w500`、`Backdrop → w780`、`Thumb → w780`、`Logo → w500`。

**⚠️ 一个值得照抄的转换技巧**（`TmdbClientManager.ConvertToRemoteImageInfo`，`:668-700`）：
```csharp
// Return Backdrops with a language specified (it has text) as Thumb.
if (imageType == ImageType.Backdrop && !string.IsNullOrEmpty(language))
{
    imageType = ImageType.Thumb;
}
```
⇒ **带语言的 backdrop 通常是"有字的艺术图"，Jellyfin 把它归为 Thumb（横版缩略图），而无语言的纯背景图才是 Backdrop。** 这个区分对 UI 观感影响很大，值得照搬。

另有一条：`scaleImage = !IsOriginalImageSize(size)`，若做了缩放就**不存原始宽高**（因为宽高描述的是原图）。

### 4.16 【补充】TMDB 插件的两个"名字"必须区分

| 概念 | 值 | 用途 |
|---|---|---|
| `TmdbUtils.ProviderName` | **`"TheMovieDb"`** | provider **显示名**，用于库设置里的 `MetadataFetchers` 顺序表、`SearchProviderName` 匹配 |
| `MetadataProvider.Tmdb.ToString()` | **`"Tmdb"`** | **`ProviderIds` 字典的键**（即 `ProviderIds["Tmdb"]`） |

所有 6 个 `*ExternalId.Key` 用的是后者（`"Tmdb"` / `TmdbCollection`）。
⚠️ 且 `TmdbSeasonProvider` **没有实现 `IHasOrder`**（其它 TMDB provider 有），所以它的排序权重是默认值 **50** 而非 1 —— 与其他 TMDB provider 不一致。
`ProviderManager` 的默认排序权重：`IHasOrder.Order`，**无该接口则为 50**（注释："after items that want to be first (~0) but before items that want to be last (~100)"）。
已实测的权重：TMDB 的图片 provider 多为 0–2；**所有 `*NfoProvider` 都是默认 50**；`EmbeddedImageProvider` 99；`InternalMetadataFolderImageProvider` **1000**（注释 "Make sure this is last"）。

### 4.17 【补充】`TypeOptions` / `MetadataOptions` 的真实归属

`MetadataOptions`（`MediaBrowser.Model/Configuration/MetadataOptions.cs`）承载**按条目类型**的 fetcher 开关，但**它不是 `LibraryOptions` 的直接属性**，而是通过 `TypeOptions` 关联：

```csharp
// MediaBrowser.Model/Configuration/TypeOptions.cs
public class TypeOptions
{
    public string Type { get; set; }
    public MetadataOptions MetadataOptions { get; set; }     // ★ 按类型的元数据配置
    public ImageOption[] ImageOptions { get; set; }
}
// 访问器：GetMetadataOptions(type) / GetImageOptions(type) / GetLimit(type, imageType) / GetMinWidth(...) / IsEnabled(...)
```
而 `LibraryOptions.TypeOptions` 是 `TypeOptions[]`（ctor 初始化为空数组），图片默认值来自 `TypeOptions.DefaultImageOptions`（静态默认，见 §2.4.3）。
`LibraryOptions` 自己只有**库级**的 `MetadataSavers` / `DisabledLocalMetadataReaders` / `LocalMetadataReaderOrder`（这三者是"库级默认"，会被类型级覆盖）。

> **自研的简化建议**：不必照抄这套三层（库级默认 → 类型级 → 全局默认）。直接用"**库级 + 类型级两层**，类型级缺省则回退库级"就够了。

### 4.18 【补充】`DirectoryService` 的缓存参数（性能相关）

`MediaBrowser.Controller/Providers/DirectoryService.cs`（225 行）：`MaxCachedRecords = 100_000`、空闲 **5 分钟**过期、访问间隔 1s；`GetFileSystemEntries` 用 `ConcurrentDictionary` 并**捕获 `DirectoryNotFoundException` 返回空数组**；`GetFilePaths` 结果 `OrderBy(x => x)`（**有序**，这是扫描顺序稳定的原因）；提供 `Invalidate` / `Move` / `DropCacheIfIdleOrFull`。
⇒ 自研扫描器若做目录缓存，**务必保证"枚举结果有序"**，否则多版本归组、多部分排序会不稳定（同一批文件两次扫描可能得到不同顺序）。

---

## 附录 A：核心引用文件清单（按主题）

**播放（Playback）**

| 主题 | 文件 |
|---|---|
| 直连判定 | `MediaBrowser.Model/Dlna/StreamBuilder.cs`（2493 行）、`MediaOptions.cs`、`DeviceProfile.cs`、`DirectPlayProfile.cs`、`TranscodingProfile.cs`、`CodecProfile.cs`、`ContainerProfile.cs`、`SubtitleProfile.cs`、`ProfileCondition.cs` |
| 枚举 | `MediaBrowser.Model/Session/PlayMethod.cs`、`TranscodeReason.cs`、`MediaBrowser.Model/Dlna/SubtitleDeliveryMethod.cs`、`DlnaProfileType.cs`、`ProfileConditionValue.cs`、`MediaBrowser.Model/Entities/HardwareAccelerationType.cs`、`EncoderPreset.cs`、`MediaStreamType.cs` |
| 请求/应答 | `Jellyfin.Api/Models/MediaInfoDtos/PlaybackInfoDto.cs`、`Jellyfin.Api/Controllers/MediaInfoController.cs` |
| FFmpeg 拼接 | `Jellyfin.Api/Controllers/DynamicHlsController.cs`（2087 行）、`MediaBrowser.Controller/MediaEncoding/EncodingHelper.cs`（**8042 行**）、`MediaBrowser.MediaEncoding/Encoder/MediaEncoder.cs`、`EncodingUtils.cs`、`EncoderValidator.cs` |
| 转码任务 | `MediaBrowser.MediaEncoding/Transcoding/TranscodeManager.cs`、`MediaBrowser.Controller/MediaEncoding/TranscodingJob.cs`、`TranscodingThrottler.cs`、`TranscodingSegmentCleaner.cs`、`EncodingJobInfo.cs` |
| 探测 | `MediaBrowser.MediaEncoding/Probing/*`（`ProbeResultNormalizer.cs` 等） |
| 字幕 | `MediaBrowser.MediaEncoding/Subtitles/SubtitleEncoder.cs`（1058 行）、`SubtitleEditParser.cs`、`JsonWriter.cs`、`SubtitleFormatExtensions.cs`、`MediaBrowser.MediaEncoding/Attachments/AttachmentExtractor.cs`（507 行） |
| 进度/会话 | `Jellyfin.Api/Controllers/PlaystateController.cs`、`SessionController.cs`、`Emby.Server.Implementations/Session/SessionManager.cs`、`Emby.Server.Implementations/Library/UserDataManager.cs`、`MediaBrowser.Model/Session/PlaybackProgressInfo.cs`、`PlaybackStopInfo.cs` |
| Trickplay | `MediaBrowser.Model/Configuration/TrickplayOptions.cs`、`TrickplayScanBehavior.cs`、`MediaBrowser.Model/Dto/TrickplayInfoDto.cs`、`Jellyfin.Server.Implementations/Trickplay/TrickplayManager.cs`、`MediaBrowser.Providers/Trickplay/TrickplayProvider.cs`、`MediaBrowser.Providers/Trickplay/TrickplayImagesTask.cs`、`Jellyfin.Api/Controllers/TrickplayController.cs` |
| 媒体片段 | `src/Jellyfin.Database/.../Enums/MediaSegmentType.cs`、`.../Entities/MediaSegment.cs`、`MediaBrowser.Model/MediaSegments/MediaSegmentDto.cs`、`MediaBrowser.Controller/MediaSegments/IMediaSegmentProvider.cs`、`Jellyfin.Server.Implementations/MediaSegments/MediaSegmentManager.cs`、`Emby.Server.Implementations/ScheduledTasks/Tasks/MediaSegmentExtractionTask.cs`、`Jellyfin.Api/Controllers/MediaSegmentsController.cs` |
| 路径 | `Emby.Server.Implementations/Library/PathManager.cs`、`MediaBrowser.Controller/IO/IPathManager.cs` |

**刮削（Metadata）**

| 主题 | 文件 |
|---|---|
| 命名解析 | `Emby.Naming/Common/NamingOptions.cs`（924 行）、`EpisodeExpression.cs`、`Emby.Naming/Video/VideoResolver.cs`、`VideoListResolver.cs`、`StackResolver.cs`、`FileStackRule.cs`、`ExtraRuleResolver.cs`、`ExtraRule.cs`、`CleanDateTimeParser.cs`、`CleanStringParser.cs`、`Format3DParser.cs`、`StubResolver.cs`、`Emby.Naming/TV/EpisodeResolver.cs`、`EpisodePathParser.cs`、`SeasonPathParser.cs`、`SeriesResolver.cs`、`TvParserHelpers.cs`、`Emby.Naming/ExternalFiles/ExternalPathParser.cs`、`Emby.Naming/Audio/AlbumParser.cs`、`Emby.Naming/AudioBook/*`、`Emby.Naming/Book/*` |
| provider 架构 | `MediaBrowser.Providers/Manager/ProviderManager.cs`、`MetadataService.cs`、`ImageSaver.cs`、`ItemImageProvider.cs`、`MetadataLanguageUtils.cs`、`MediaBrowser.Controller/Providers/*`（`MetadataRefreshOptions.cs`、`ImageRefreshOptions.cs`、`MetadataRefreshMode.cs`、`RemoteSearchQuery.cs`、`IExternalId.cs`、`IExternalUrlProvider.cs`、`DirectoryService.cs`、`RefreshPriority.cs`、`MetadataResult.cs`、`LocalImageInfo.cs`） |
| TMDB（内置） | `MediaBrowser.Providers/Plugins/Tmdb/**`（32 文件 3639 行）：`TmdbClientManager.cs`（866 行）、`TmdbUtils.cs`（517 行）、`TmdbExternalUrlProvider.cs`、`Configuration/PluginConfiguration.cs`、`Api/TmdbController.cs`、`Movies/Tmdb{Movie,MovieImage,MovieExternalId,MovieSimilar}*.cs`、`TV/Tmdb{Series,Season,Episode,MissingEpisode,UpcomingEpisodes,SeriesImage,SeasonImage,EpisodeImage,SeriesSimilar}*.cs`、`BoxSets/*`、`People/*` |
| 其它元数据源 | `MediaBrowser.Providers/Plugins/Omdb/*`、`Plugins/MusicBrainz/*`、`Plugins/AudioDb/*`、`Plugins/StudioImages/*`、`Plugins/ListenBrainz/*`、`Books/{GoogleBooks,ComicVine,ComicBookInfo,ComicInfo,OpenPackagingFormat,Isbn}/*` |
| 本地元数据 | `MediaBrowser.XbmcMetadata/**`（31 文件 4277 行）：`Parsers/BaseNfoParser.cs`（1029 行）、`Savers/BaseNfoSaver.cs`（1063 行）、`Savers/{Movie,Series,Season,Episode,Album,Artist}NfoSaver.cs`、`Providers/*NfoProvider.cs`、`NfoUserDataSaver.cs`；`MediaBrowser.LocalMetadata/**`（16 文件 2714 行）：`Parsers/{BaseItemXmlParser,BoxSetXmlParser,PlaylistXmlParser}.cs`、`Savers/{BaseXmlSaver,BoxSetXmlSaver,PlaylistXmlSaver}.cs`、`Images/LocalImageProvider.cs`、`Images/EpisodeLocalImageProvider.cs` |
| 图片处理 | `MediaBrowser.Providers/MediaInfo/EmbeddedImageProvider.cs`、`MediaBrowser.LocalMetadata/Images/InternalMetadataFolderImageProvider.cs`、`src/Jellyfin.Drawing.Skia/SkiaEncoder.cs`、`src/Jellyfin.Drawing/NullImageEncoder.cs`、`MediaBrowser.Model/Net/MimeTypes.cs`、`MediaBrowser.Model/Drawing/ImageResolution.cs` |
| 配置模型 | `MediaBrowser.Model/Configuration/{ServerConfiguration,LibraryOptions,MetadataOptions,TypeOptions,ImageOption,EncodingOptions,TrickplayOptions,XbmcMetadataOptions,ImageSavingConvention}.cs` |
| 数据模型 | `MediaBrowser.Controller/Entities/BaseItem.cs`（3090 行）、`Folder.cs`、`Video.cs`、`Person.cs`、`PersonInfo.cs`、`Movies/{Movie,BoxSet}.cs`、`TV/{Series,Season,Episode}.cs`、`Audio/{Audio,MusicAlbum,MusicArtist}.cs`、`Photo.cs`、`Trailer.cs`、`MusicVideo.cs`、`MediaBrowser.Model/Entities/{MediaStream,ChapterInfo,MetadataField,MetadataProvider,ImageType,ProviderIdsExtensions,PersonType,VideoType,SeriesStatus,TrailerType}.cs`、`MediaBrowser.Model/Dto/{MediaSourceInfo,BaseItemDto,BaseItemPerson}.cs`、`Jellyfin.Data/Enums/PersonKind.cs`、`src/Jellyfin.Database/.../Entities/{BaseItemEntity,BaseItemProvider,MediaStreamInfo,Chapter,MediaSegment,TrickplayInfo}.cs`、`Jellyfin.Server.Implementations/Item/{BaseItemMapper,PeopleRepository,ChapterRepository,MediaStreamRepository}.cs` |
| API | `Jellyfin.Api/Controllers/{Items,UserLibrary,Library,ItemRefresh,ItemLookup,ItemUpdate,Image,RemoteImage,Subtitle,VideoAttachments,UniversalAudio,MediaInfo,Playstate,Session,Trickplay,MediaSegments}Controller.cs`、`Jellyfin.Api/BaseJellyfinApiController.cs`、`Jellyfin.Api/Helpers/{MediaInfoHelper,StreamingHelpers,DynamicHlsHelper,HlsHelpers,HlsCodecStringHelpers}*.cs` |

---

## 附录 B：**没查到 / 不确定** 清单（如实，不猜）

以下条目是本次研究中**未能确认**或**明确不存在**的内容。凡"不存在"的，均已用 grep 在整棵源码树上验证过。

### B.1 明确**不存在**（已在源码中验证为不存在，前文相关猜测据此更正）

| # | 需求/常见假设 | 核实结果 |
|---|---|---|
| 1 | 配置项名 `metadata_language` / `metadata_country_code` | **不存在**。官方文档全站也从未出现这两个字面量（文档仓库全文 grep 0 命中）。实际字段名是 `PreferredMetadataLanguage` / `MetadataCountryCode` |
| 2 | `LibraryOptions.MetadataDownloadLanguages` | **不存在**（grep 零命中） |
| 3 | `MetadataRefreshOptions.EnableRemoteRefresh` / `EnableLocalRefresh` / `EnableThumbnailImageExtraction` / `EnableTrickplayImageExtraction` | **均不存在**于该类（已通读该类全文）。`EnableTrickplayImageExtraction` 是 `LibraryOptions` 的字段，不是刷新选项 |
| 4 | 独立的 `ImageRefreshMode` 枚举 | **不存在**。`ImageRefreshOptions.ImageRefreshMode` 的类型就是 `MetadataRefreshMode` |
| 5 | "Prefer local metadata" 开关 | **不存在**（`grep -rni "prefer local"` 仅命中期权无关代码，`grep "PreferLocalMetadata"` 零命中）。本地 NFO 始终被读取且始终优先，无法通过开关关闭（官方文档逐字确认） |
| 6 | `SaveLocalThumbnailSets` | **不存在**（grep 零命中） |
| 7 | Jellyfin 内置 Intro/Outro 检测算法 | **不存在**。核心只有 `IMediaSegmentProvider` 插件接口 + `MediaSegments` 表 + `GET /MediaSegments/{itemId}`。全树 `grep "IMediaSegmentProvider"` 只命中接口本身与 `MediaSegmentManager`，**无任何内置实现**；`grep '"Intro"'` 零命中 |
| 8 | 服务端字幕偏移/延迟（`SubtitleOffset` / `itsoffset` / `subdelay`） | **不存在**。全树（含非 `.cs`）grep 零命中。字幕延迟 **100% 是客户端行为** |
| 9 | `POST /Items/{itemId}/Metadata/Refresh` | **不存在**（grep 零命中）。正确的路径是 `POST /Items/{itemId}/Refresh` |
| 10 | `POST /Items/{itemId}/Image` / `ItemImageController` | **不存在**。图片 API 在 `ImageController.cs` + `RemoteImageController.cs`，路径形如 `Items/{itemId}/Images/{imageType}` |
| 11 | `POST /Library/Refresh` 的 `isScheduledTask` 参数 | **不存在**（已核实方法签名为无参） |
| 12 | `replaceThumbnailImages` 参数 | **不存在**于 `POST /Items/{itemId}/Refresh` |
| 13 | 服务端码率阶梯表（ABR 多档配置） | **不存在**。官方文档明确 "Transcoding of local sources is always requested by the client."；服务端只有 `DeviceProfile.MaxStreamingBitrate`（默认 8 Mbps）单一上限。HLS 主清单**不生成多 variant** |
| 14 | `TmdbClient.cs` / `Configuration/TmdbOptions.cs` / `TmdbExternalId.cs` | **均不存在**。实际是 `TmdbClientManager.cs` / `Configuration/PluginConfiguration.cs` / 6 个按类型的 `*ExternalId.cs` |
| 15 | `MediaBrowser.Model/Entities/MediaSourceInfo.cs` | **不存在**。实际路径是 `MediaBrowser.Model/**Dto**/MediaSourceInfo.cs` |
| 16 | `MediaBrowser.Model/Entities/PersonInfo.cs` / `PersonKind.cs` | **均不存在**。实际是 `MediaBrowser.Controller/Entities/PersonInfo.cs` 与 `Jellyfin.Data/Enums/PersonKind.cs` |
| 17 | `BaseItem.People` / `MediaSources` / `MediaStreams` / `Chapters` 实体属性 | **均不是**实体属性（本版本）。People 走 `IPeopleRepository`；其余走 `GetMediaSources()` / `GetMediaStreams()` / `IChapterManager` 运行期访问器 |
| 18 | `BaseItem.LockData` / `FileName` | **均不存在**。实体只有 `IsLocked`（`LockData` 仅存在于 DTO）；只有 `FileNameWithoutExtension`（计算属性） |
| 19 | `Episode.IsMissing` | **不存在**于实体。实体上是 `IsMissingEpisode`（计算属性）；`IsMissing` 只在查询对象 `InternalItemsQuery` 上（对应 `GET /Items?isMissing=`） |
| 20 | `MediaBrowser.LocalMetadata` 提供 `movie.xml` / `series.xml` / `episode.xml` | **不存在**（`grep "movie\.xml"` 在两个本地元数据项目下零命中）。`Savers/` 只剩 3 个文件，即只有 **BoxSet 的 `collection.xml`** 与 **Playlist 的 `playlist.xml`** |
| 21 | `MediaBrowser.Providers/Manager/ImageProvider.cs`、`RefreshQueue.cs`、`RefreshItem.cs`（独立文件） | **均不存在**。分别是 `ImageSaver.cs` + `ItemImageProvider.cs`；刷新队列是 `ProviderManager` 内的一个 `PriorityQueue` 字段（`ProviderManager.cs:67`） |
| 22 | `Emby.Naming/Subtitle/SubtitleParser.cs` / `SubtitleSuffix` | **不存在**。`Emby.Naming` 下只有 `Audio/ AudioBook/ Book/ Common/ ExternalFiles/ Properties/ TV/ Video` |
| 23 | `AllowSubtitleConversion` 配置项 | **不存在**（grep 零命中）。等价能力由 `MediaStream.SupportsSubtitleConversionTo` + `StreamBuilder.GetExternalSubtitleProfile(allowConversion)` 实现 |
| 24 | "ImageType → 扩展名" 的硬编码映射表 | **不存在**。扩展名完全由源 mime 决定（`MimeTypes.ToExtension`），`.jpeg` 归一为 `.jpg` |
| 25 | `ImageOptions`（复数）类型 / `MediaBrowser.Controller/Entities/ImageType.cs` | **均不存在**。只有单数 `ImageOption`；`ImageType` 只在 `MediaBrowser.Model/Entities/ImageType.cs` |
| 26 | `IImageEncoder.ResizeImage` | **不存在**于接口（只有 SkiaEncoder 的 `internal static ResizeImage`） |
| 27 | `IExternalId.UrlFormatString` | **不存在**（grep 零命中）。URL 由独立的 `IExternalUrlProvider` 负责 |
| 28 | `TmdbUtils.NormalizeLanguage` 对 `zh-CN` / `zh-TW` / `zh-HK` / `pt-BR` / `en-US` 的特殊处理 | **不存在**。归一化特例**只有 3 个**：`es-419`→`es-AR`/`es-MX`；`*-CH`→去国家码；两段式第二段强制大写 |
| 29 | `MetadataProvider.Tvdb`/`TvRage`/`TvMaze`/`Tvcom` 的 `IExternalId` 实现 | **不存在**（所以客户端元数据编辑器看不到这些字段，尽管解析器会写入 Tvdb/TvMaze 的 id） |
| 30 | 官方文档页 `/docs/general/server/media/mixed-content` | **404**。现役替代页是 `/docs/general/server/media/mixed-movies-and-shows`（内容极短） |
| 31 | 官方文档页 `/docs/general/server/metadata/movies`、`/metadata/tv-shows`、`/metadata/images`、`/post-install/transcoding/trickplay`、`/server/metadata/trickplay` | **全部 404**（各尝试 2 次并复核状态码）。影片/剧集元数据的实际页面在 `/docs/general/server/**media**/movies` 与 `.../shows`；**官方文档站没有任何 trickplay 设置页，也没有图片专页** |
| 32 | `/docs/general/administration/configuration` 里的 metadata language 配置 | **不含**。该页只管启动前静态配置（路径/日志/主配置项/数据库/字体）。metadata language 的唯一文档出处是 `/docs/general/post-install/setup-wizard` |

### B.2 **未能确认**（存在性/行为未验证，明确标注为不确定）

| # | 事项 | 不确定的具体点 |
|---|---|---|
| 1 | **TMDB 的确切 HTTP 端点字符串与 `append_to_response` token** | 由 NuGet 包 **TMDbLib 3.0.0** 构造，**该包源码不在本仓库树内**。Jellyfin 侧只能确认**语义化的 `extraMethods`**（见 §4.15）与查询参数名。**因此"`/3/movie/{id}?append_to_response=credits,releases,images,videos`"这类完整 URL 字面量我无法从本仓库证实**。§3.3 的 Python 示例中的端点路径是**按 TMDB 公开 API 的通用写法**给出的，落地前请以 <https://developer.themoviedb.org/docs> 为准 |
| 2 | **`image/png` / `image/webp` 的 mime→扩展名映射** | 落在外包 NuGet 包 `MimeTypes` 2.5.2 内，**该包源码不在仓库内**。仓库内只能证实 `image/jpeg→.jpg`、`image/jpg→.jpg`、`image/tiff→.tiff`、`image/x-png→.png`、`image/x-icon→.ico`、`.tbn→image/jpeg`。**因此"某个 png 源图最终一定存成 `poster.png`"这一整条链无法完整证实** |
| 3 | **针对 TMDB 的显式速率限制器（rate limiter）** | 我**没有**在 `TmdbClientManager` 或 `ProviderManager` 中找到令牌桶/QPS 节流实现。**唯一的"限流"是内存缓存（1 小时）+ 单消费者刷新队列**。TMDB 返回 429 时的处理依赖 TMDbLib 内部行为，**未验证**。（`TmdbClientManager` 里也有 `CacheDurationInHours = 1`、`CacheSizeLimit = 100_000` 的 `MemoryCache`） |
| 4 | **provider 级别的失败重试逻辑** | **未找到**显式重试。已知的行为是：远程 provider 异常 ⇒ `Failures++` + 记日志 + **继续下一个**（不做重试）；下次刷新时靠 `AutomaticRefreshIntervalDays` / 手动刷新再试 |
| 5 | **刷新队列与库扫描之间的互斥/去重** | 刷新队列是**全局单消费者**（`_isProcessingRefreshQueue` 布尔 + 单次 `Task.Run`），但库扫描走的是**另一套** `LimitedConcurrencyLibraryScheduler`（并发度 = `LibraryScanFanoutConcurrency` 或 `ProcessorCount - 3`）。**两套机制是否会同时刷新同一个 item、是否有互斥**，我在树内**没有找到显式去重代码**（`_refreshQueue` 内部不去重，每次 `Enqueue` 都新增一项）。这是一个**未确认的潜在并发风险点** |
| 6 | **`MovieNfoSaver.GetLocalSavePath` 的兜底分支** | `GetMovieSavePaths` 无条件 `yield`，所以 `FirstOrDefault()` 永远非 null，`?? Path.ChangeExtension(...)` **实际不可达**。这意味着**写路径是"第一个候选"而不是"第一个存在的候选"**（与读路径 `FirstOrDefault(i => i is not null)` 的语义不同）。无法确认这是 bug 还是有意为之 |
| 7 | **`photo` 的 `CommunityRating` 语义冲突** | `Emby.Photos/PhotoProvider.cs:98` 把 EXIF 的 0–5 星 Rating 直接写进约定为 0–10 的 `CommunityRating`，**写入侧无缩放无校验**。我未找到任何针对 photo 的缩放代码，但**不能断言这是 bug**（也可能是刻意的"原样存"） |
| 8 | **`CommunityRating` 的官方值域文档** | 强证据是 `BaseNfoParser.cs:550` 的 `>= 0 && <= 10` 范围校验 + 对应单测，但该约束**只作用于 `<communityrating>` 元素**，**不作用于** `<rating>`（`:534-540`）与 `<ratings><rating>`（`:966-985`）。且 `BaseItem`/`BaseItemDto` 的 XML doc 只写 "Gets or sets the community rating."，**没有**值域说明 |
| 9 | **`MetadataProvider.Custom = 0` 的写入方** | 枚举注释说它"for users and/or plugins to override the default merging behaviour"，但树内**唯一读取点**是 `Series.GetUserDataKeys()`，**没有任何写入点**。用途未确认 |
| 10 | **`TmdbSeriesProvider.cs:60` 把 `MetadataLanguage` 当 `imageLanguages` 传** | 看起来是缺陷（正确应为 `GetImageLanguagesParam(...)`，其它 provider 都是这么传的），但**无法确认是否有意** |
| 11 | **`TmdbSeasonProvider` 未实现 `IHasOrder`** | 导致它的排序权重是默认 50 而非其它 TMDB provider 的 1–2。**无法确认是否有意** |
| 12 | **`MetadataService` 里本地 provider 失败不增加 `Failures`** | 远程 provider 异常会 `Failures++`，但**本地 provider 异常只设 `ErrorMessage` 不增 `Failures`**。这是有意的（本地失败不应阻止覆盖）还是疏漏，未确认 |
| 13 | **TMDB 图片尺寸配置项的默认值** | `PluginConfiguration` 的 `PosterSize`/`BackdropSize`/`LogoSize`/`ProfileSize`/`StillSize` **默认都是 `null`**（未初始化），此时 `GetUrl` 用 `"original"`。但前端 `config.html` 会用真实尺寸列表填充下拉框，**默认在 UI 上显示为哪一项**，我未验证 |
| 14 | **`ImageSaver.cs:194` 的可疑比较** | `var directory = Path.GetDirectoryName(currentPath); if (item is Episode && directory.Equals("metadata", StringComparison.Ordinal))` —— 把**完整目录路径**与字面量 `"metadata"` 比较，**几乎恒为 false**。真正生效的 `metadata` 目录清理在 `ItemImageProvider.PruneImages`。无法确认是否 bug |
| 15 | **`SubtitleProfile.DidlMode` 属性** | 只声明并带 `[XmlAttribute("didlMode")]`，**服务端代码中未发现任何消费方**（疑似历史字段） |
| 16 | **`SubtitleEncoder.cs:222` 的 `IsExternal = MediaStream.IsVobSubFormat(outputFormat)`** | 对分支内所有可能的 `outputFormat` 值（`ass`/`ssa`/`pgssub`/`mks`/`srt`）**恒为 false**，疑为死逻辑。对应测试未覆盖该分支 |
| 17 | **`EncodingJobInfo.InternalSubtitleStreamOffset`** | 被赋值（`EncodingHelper.cs:7527`）但**全树无读取点**，疑为历史残留 |
| 18 | **`EncodingHelper.cs:7780` 的 `hasCopyTs`** | 通过检查滤镜串里是否含 `"copyts"` 判断，但本版本**没有任何滤镜串会输出 `copyts`**，该判断恒为 false |
| 19 | **`SubtitleEncoder.cs:843` 的 `outputPath` 未转义** | 对比同文件其它三处（`:457`、`:635`、`:693`）都调用了 `EscapeProcessArgument()`，此处未调用。因该路径来自受控的 GUID/索引拼接，**实际不可利用**，但属不一致 |
| 20 | **字幕响应的 MIME 类型** | 仓库内 `MimeTypes` 只有 `.ass`/`.ssa → text/x-ssa`，**没有** `.srt`/`.vtt`/`.ttml`/`.json` 条目，故 `Stream.srt` 的实际响应头应是默认的 `application/octet-stream`（与 `[ProducesFile("text/*")]` 的元数据声明不符）。**我未实际发起 HTTP 请求验证响应头** |
| 21 | **路由是否存在"双前缀别名"** | `BaseJellyfinApiController` 声明了 `[Route("[controller]")]`，而 23 个控制器又声明 `[Route("")]`。ASP.NET Core 的 `RouteAttribute` 默认 `Inherited = true`，**理论上可能存在 `/Items/Items/...`、`/UserLibrary/Items/...` 这类别名**。我**没有运行 Jellyfin 实测路由表**（本机未监听 8096），报告中一律按"空前缀"解释。若实际存在别名，本报告未覆盖 |
| 22 | **转码相关的部分细节** | `GetMaxAudioBitrateForTotalBitrate` 的完整阈值表已给出，但 `IsBitrateLimitExceeded` 的精确比较（`>` 还是 `>=`）我未逐行确认；`GetNumberOfThreads` 的具体算法未展开 |
| 23 | **`HlsCodecStringHelpers.cs`（370 行）生成的 `CODECS=` 字符串的完整规则** | 只确认了它的存在与职责，未逐行展开 |
| 24 | **`GetVideoQualityParam` 的完整体（约 770 行）** | 我只读了头部与 `GetVideoBitrateParam`、`GetEncoderParam`、关键帧部分；`GetVideoQualityParam` 内部针对各家编码器的 profile/level/`-x264-params` 等细节**未全部展开** |
| 25 | **`MediaBrowser.Model/Dlna/StreamBuilder.cs` 中 `GetCompatibilityXxx` 系列函数的完整条件判定** | `GetCompatibilityContainer` / `GetCompatibilityVideoCodec` / `GetCompatibilityAudioCodecDirect` 的**完整实现未逐行读完**（它们是把 `CodecProfile`/`ContainerProfile` 的 `Conditions` 逐条过一遍）；`ConditionProcessor.cs`（389 行）的条件比较语义也未逐条展开 |
| 26 | **`MediaBrowser.Model/Entities/MediaStream.cs` 的 `Score` 由谁赋值** | 该字段不落库，但**赋值点未定位**（可能是 `ProbeResultNormalizer` 或 `MediaSourceManager`）。影响"默认字幕轨选择"的排序权重来源 |
| 27 | **`ItemFields` 枚举的尾部成员** | 该文件 253 行，我只逐行读到约 `:242`（`ExtraIds`），最后若干成员未列行号 |
| 28 | **`ItemValueType` 缺少数值 5 的历史原因** | 实测只有 `Artist=0`、`AlbumArtist=1`、`Genre=2`、`Studios=3`、`Tags=4`、`InheritedTags=6`，**值 5 缺失**。未找到 5 的历史定义或移除证据 |
| 29 | **Jellyfin 的 Web 端（jellyfin-web）如何实现字幕延迟、ABR 码率阶梯、trickplay 消费** | **前端不在本仓库**（顶层只有服务端项目）。本报告只能确认"服务端不提供这些能力"，**无法给出客户端的文件行号级证据** |
| 30 | **`LibraryController.RefreshLibrary()` 之后任务实际何时执行/多久完成** | 只知道它 `CancelIfRunningAndQueue<RefreshMediaLibraryTask>()`，**任务实际调度时机与耗时未验证** |
| 31 | **TVDB / fanart.tv / AniDB / AniList / Kitsu 插件的字段映射** | 这些实现在**外部插件仓库**（`jellyfin/jellyfin-plugin-tvdb` 等），**本仓库不含其代码**，因此本报告**不给出**它们的字段映射细节（避免臆测）。所需接口契约是 `IRemoteMetadataProvider` 系列 |
| 32 | **`LibraryOptions.AutomaticRefreshIntervalDays` 的默认值** | 该属性是 `int`（非可空），但我**未确认其默认值是 0 还是其它**（0 表示不自动刷新） |
| 33 | **`SubtitleEdit` 支持的扩展名全集合** | `SubtitleEditParser` 完全依赖**运行时反射** `libse` 4.0.12 程序集中的 `SubtitleFormat` 子类；该 NuGet 包在本机未 restore，**无法枚举全部扩展名** |
| 34 | **`TranscodingJob` 的空闲超时/自动清理阈值** | 我只确认了"有 `StopKillTimer` / `IncrementActiveRequestCount` 机制"与"`POST /Sessions/Playing/Stopped` 会立即杀任务"，但**具体的空闲超时秒数未定位**（`DeleteTranscodeFileTask` 是 24 小时周期，不是空闲超时） |

### B.3 关于引用基线的说明

- 本报告所有源码引用的**行号**对应我下载并解包的 `master` 分支快照，tree SHA 标注为 `fd75964da853765d63101b4889f23a4161e2758b`（该 SHA 来自 GitHub API 的 `git/trees/master` 响应）。
- **注意**：解包的目录**不含 `.git`**，因此**无法在本地用 git 校验该 commit SHA**；SHA 采信自 API 响应。
- 该快照的 `SharedVersion.cs` 显示 `AssemblyVersion("13.0.0")`，且包含 2026 年日期的迁移文件（如 `20260910120000_MigrateRatingLevels.cs`）——说明这是**较新的开发分支快照**，与网上常见的 10.8/10.9/10.10 资料可能存在差异（本报告已多次标注这类差异，例如 `EnableDirectStream` 被注释为 broken、`MediaBrowser.LocalMetadata` 不再写 `movie.xml`）。
- **降级方案**：全部结论均可通过 `https://github.com/jellyfin/jellyfin/blob/master/<路径>#L<行号>` 复核；若上游已推进导致行号漂移，请按**文件 + 符号名**（如 `StreamBuilder.GetVideoDirectPlayProfile`）定位。

---

*报告结束。*
