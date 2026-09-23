# Jellyfin 事实核查（播放 + 刮削）— 每条含可点击出处

> 编写约定：**凡未能找到一手可引用来源的，一律写「未能确认」**，不做推断性断言。
> 所有源码引用均来自本地解包的官方 tarball（`codeload.github.com`，不耗 API 额度），行号与 tag/分支真实对应。
> 本报告不覆盖「目录/命名/图片/字幕」官方文档规范（父 agent 已逐字核对）。

---

## 0. 版本基准（**必须先读**，否则结论会错）

Jellyfin 在 **12.0 做了一次破坏性认证变更**，同一句话在 10.11 和 12.x 上结论相反。全部结论按版本标注。

| 版本 | 发布时间 | 说明 | 出处 |
|---|---|---|---|
| **12.1** | 2026-09-15 | **当前 stable**；`x-jellyfin-version = 12.1.0` | [GitHub Releases API](https://api.github.com/repos/jellyfin/jellyfin/releases/tags/v12.1)；[OpenAPI stable](https://api.jellyfin.org/openapi/jellyfin-openapi-stable.json) |
| 12.0 | 2026-09-08 | 破坏性变更版本（认证、路由前缀、HLS 隐藏） | [v12.0 release notes](https://github.com/jellyfin/jellyfin/releases/tag/v12.0) |
| 10.11.11 | 2026-06-06 | 最后一代 10.x（旧行为基线） | [Releases API](https://api.github.com/repos/jellyfin/jellyfin/releases?per_page=30) |
| master / unstable | — | `SharedVersion.cs` = **13.0.0**；[OpenAPI unstable](https://api.jellyfin.org/openapi/jellyfin-openapi-unstable.json) `x-jellyfin-version = 13.0.0` | `SharedVersion.cs`：<https://github.com/jellyfin/jellyfin/blob/master/SharedVersion.cs> |

本地解包目录：`/tmp/jf121/jellyfin-12.1`（stable）、`/tmp/jf1011/jellyfin-10.11.11`、`/tmp/jf/jellyfin-master`。

---

## 1. Fork 关系：确认

**确认：Jellyfin 派生自 Emby 3.5.2，起于 2018 年 12 月初，原因是 Emby 转向闭源。**

- 官方 README 原文：
  > "Jellyfin is descended from Emby's 3.5.2 release and ported to the .NET platform to enable full cross-platform support."
  — <https://github.com/jellyfin/jellyfin/blob/master/README.md>（亦见 <https://jellyfin.org/docs/general/about>）

- 官方 About 页「Why did you fork?」原文：
  > "The Jellyfin project was started in **early December 2018** primarily as a result of **Emby's decision to take their next release (4.x) closed-source**."
  — <https://jellyfin.org/docs/general/about>

  同页还列出其他动因：a paywall for gratis users in an early 3.x release、the hiding/removal of client app and server code over several years、a lack of openness to community contributions、previously-reported GPL violations。

- 官方 FAQ 亦把 fork 理由指回 About 页：<https://jellyfin.org/docs/general/faq>

### ⚠️ 关于「Emby 3.6 起闭源」这一说法

| 说法 | 结论 | 依据 |
|---|---|---|
| fork 基座是 Emby **3.5.2** | **已确认** | README / About 页（上面两条） |
| 闭源动作发生在 **4.x** | **官方 Jellyfin 措辞** | About 页原文 "next release (4.x) closed-source" |
| 闭源自 **3.6** 起 | **第三方来源支持，官方一手来源未能确认** | NixOS nixpkgs issue #51832（2018-12-10）："the Emby server has changed license from GPL-2 to 'proprietary core with open-source components' … **the sources for the 3.6 version and onward won't be distributed**"，并引用 <https://github.com/MediaBrowser/Emby/issues/3479#issuecomment-444985456> — <https://github.com/NixOS/nixpkgs/issues/51832> |

> 该 Emby issue 页面正文用 `web_fetch` 只取到 GitHub 导航框架（JS 渲染），评论 API 亦返回空 —— 因此「3.6 是第一个闭源版本」**未能由一手来源确认**。

**给中文报告的写法建议**：写「Jellyfin 官方表述为『Emby 决定将 4.x 闭源』；第三方（NixOS 打包者）记录为『3.6 起源码不再分发』。两个版本号并存，Jellyfin 侧的确定事实是 fork 基座 = Emby 3.5.2、时间 = 2018 年 12 月初。」

---

## 2. 认证（Authentication）

### 2.1 现在的正确请求头（Jellyfin 12.x）

```
Authorization: MediaBrowser Client="MyApp", Device="MyDevice", DeviceId="unique-device-id", Version="1.0.0", Token="<access token>"
```

- **scheme 必须是 `MediaBrowser`（大小写不敏感）**，这是唯一的硬编码合法 scheme 名：
  `Jellyfin.Server.Implementations/Security/AuthorizationContext.cs` **L258**
  ```csharp
  var validName = name.Equals("MediaBrowser", StringComparison.OrdinalIgnoreCase);
  ```
  <https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Server.Implementations/Security/AuthorizationContext.cs#L258>

- **读取的键**：`DeviceId`、`Device`、`Client`、`Version`、`Token` 共 5 个（**没有 `UserId`**）：
  `AuthorizationContext.cs` **L84-L91**
  <https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Server.Implementations/Security/AuthorizationContext.cs#L84-L91>

- **主头名是 `Authorization`**（标准头），legacy 头名是回退项：
  `AuthorizationContext.cs` **L229-L239**
  <https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Server.Implementations/Security/AuthorizationContext.cs#L229-L239>

- **数值解析是手写 parser**，支持 `Key=Value` 逗号分隔、值可带引号、值会做 `WebUtility.UrlDecode`：
  `AuthorizationContext.cs` **L276-L317**（`GetParts`）
  <https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Server.Implementations/Security/AuthorizationContext.cs#L276-L317>

- **OpenAPI 权威声明**：securityScheme 只有一个，类型 `apiKey`、`in: header`、`name: Authorization`，描述 "API key header parameter"：
  ```json
  "securitySchemes": {"CustomAuthentication": {"type": "apiKey", "description": "API key header parameter", "name": "Authorization", "in": "header"}}
  ```
  — <https://api.jellyfin.org/openapi/jellyfin-openapi-stable.json>（`components.securitySchemes`；spec version 12.1.0）
  受保护端点示例：`POST /Sessions/Playing` 的 `security = [{"CustomAuthentication": ["DefaultAuthorization"]}]`（同 spec）。
  认证方案绑定代码：`Jellyfin.Api/Auth/CustomAuthenticationHandler.cs`（`HandleAuthenticateAsync` → `_authService.Authenticate(Request)`）
  <https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Api/Auth/CustomAuthenticationHandler.cs>

- **官方 jellyfin.org/docs 上「没有」认证页**：`https://jellyfin.org/docs/general/server/authentication` 返回 **HTTP 404**；我抓取了 <https://jellyfin.org/sitemap.xml>（213 条 URL，其中 108 条 `/docs/`），**不存在任何认证/授权页面**。→ 「Jellyfin 官网文档记载该请求头格式」这一条 **未能确认（该页面不存在）**；权威出处只能用 OpenAPI + 源码 + release notes。

### 2.2 向后兼容：`X-Emby-Authorization` / `X-Emby-Token` 还收不收？

**结论（按版本）：12.0 起默认关闭；10.11.x 默认开启。**

全部 legacy 入口都被同一个开关 `ServerConfiguration.EnableLegacyAuthorization` 包住：

| 入口 | 位置（v12.1） | 是否需要 `EnableLegacyAuthorization` |
|---|---|---|
| `Authorization` 头（scheme `MediaBrowser`） | L231 / L258 | **否（永远可用）** |
| `X-Emby-Authorization` 头 | **L233-L236** | **是** |
| scheme `Emby`（即 `Authorization: Emby ...`） | **L259** | **是** |
| `X-Emby-Token` 头 | **L93-L96** | **是** |
| `X-MediaBrowser-Token` 头 | **L98-L101** | **是** |
| `?ApiKey=` 查询参数 | **L103-L106** | **否（永远可用，未加开关）** |
| `?api_key=` 查询参数 | **L108-L111** | **是** |

引用（v12.1 同一文件）：
<https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Server.Implementations/Security/AuthorizationContext.cs#L93-L111>
<https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Server.Implementations/Security/AuthorizationContext.cs#L233-L236>
<https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Server.Implementations/Security/AuthorizationContext.cs#L258-L259>

**默认值随版本翻转（关键差异）：**

| 版本 | `ServerConfiguration.EnableLegacyAuthorization` | 迁移例程 |
|---|---|---|
| v10.11.11 | `= true`（默认开） | 无 |
| v12.1 | 无初始化器 → **`false`（默认关）** | 有：`20260531160000_DisableLegacyAuthorization.cs`，**强制**写 `false` 并保存 |
| master 13.0.0 | 同 v12.1 | 同 v12.1 |

- v10.11.11：<https://github.com/jellyfin/jellyfin/blob/v10.11.11/MediaBrowser.Model/Configuration/ServerConfiguration.cs#L290>（`public bool EnableLegacyAuthorization { get; set; } = true;`）
- v12.1：<https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Configuration/ServerConfiguration.cs#L290>（`public bool EnableLegacyAuthorization { get; set; }`）
- 迁移例程 v12.1：<https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Server/Migrations/Routines/20260531160000_DisableLegacyAuthorization.cs>
  （`[JellyfinMigration("2026-05-31T16:00:00", nameof(DisableLegacyAuthorization), Stage = CoreInitialisation)]`；`PerformAsync` 内 `Configuration.EnableLegacyAuthorization = false;`）

**官方 release notes 明文（最强引用）：**
> "**Legacy authorization is now disabled by default, and a migration disables it on existing installs as well**"
> "**Legacy route prefixes removed (`/emby/*` and `/mediabrowser/*`).** Old third-party clients that rely on them will stop working"
> "The API no longer allows the use of deprecated authorization mechanisms by default. Clients and tooling need to migrate if they haven't done so already. See https://github.com/jellyfin/jellyfin/pull/15559 for details."
— <https://github.com/jellyfin/jellyfin/releases/tag/v12.0>（章节 *Breaking and behavior changes* / *Developers → API Changes*）

**引入该开关的 PR 原文：**
> "This is part 2 of 3 for the removal of legacy authorization method. Previously we added an configuration options that is disabled by default in #13306 which **released with 10.11**. This PR will change the `EnableLegacyAuthorization` option to **false for all installs**… We'll remove this configuration option (and the authorization methods) in a future release"
— PR **#15559** "Disable legacy authorization methods by default"（merged 2025-11-27）
<https://github.com/jellyfin/jellyfin/pull/15559>

> ⚠️ 注意 PR 正文说该开关「disabled by default in #13306 which released with 10.11」，但我在 **v10.11.11 的源码里读到的是 `= true`**（上表）。两者口径不一致，我以**读到的源码为准**；PR 措辞可能指「配置项存在但用户可关」。若要写进报告，建议直接写「v10.11.11 默认 true、v12.x 默认 false + 迁移强制 false」，避免引用这句含糊的 PR 描述。

### 2.3 `POST /Users/AuthenticateByName`

- 路径与操作：`POST /Users/AuthenticateByName`，`operationId: AuthenticateUserByName`，tag `Authentication`，**`security = None`（匿名可调）**
- 请求体 schema 名 **`AuthenticateUserByName`**：`{ "Username": string, "Pw": string }`（"Pw" 注释为 "Gets or sets the plain text password."）
- 响应 200 → schema **`AuthenticationResult`**：`User`(UserDto), `SessionInfo`(SessionInfoDto), `AccessToken`(string), `ServerId`(string)
— 全部来自 <https://api.jellyfin.org/openapi/jellyfin-openapi-stable.json>

### 2.4 API Key 头 `Authorization: MediaBrowser Token="..."`

- `Token` 值会先被拿去查设备表（`_deviceManager.GetDevices(new DeviceQuery { AccessToken = token })`，L132-L133）；
- 查不到设备时，再查 **API Keys 表**：`await dbContext.ApiKeys.FirstOrDefaultAsync(apiKey => apiKey.AccessToken == token)` → `authInfo.IsApiKey = true`（**L195-L217**）
— <https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Server.Implementations/Security/AuthorizationContext.cs#L195-L217>

> 结论：**`Authorization: MediaBrowser Token="<api key>"` 在源码上可行**（scheme 校验只认 `MediaBrowser`，`Token` 缺省时回落到 ApiKeys 表）。
> 但**官方文档未记载该最小写法** → 该「写法本身」标注为「**源码可推导 / 官方文档未见明文**」，不是文档级事实。
> 「API key 认证」整体行为有 OpenAPI 注释与源码支撑；`X-Emby-Token` 传 API key 的旧法在 12.0 起默认失效。

### 2.5 Emby 侧对照（一手：Emby 官方开发者文档，© EMBY LLC，2022）

- API 基址：`http[s]://hostname:port/**emby**/{apipath}` — <https://dev.emby.media/doc/restapi/index.html>
- **用户认证头（scheme 是 `Emby`，且带 `UserId`）**：
  ```
  Authorization=Emby UserId="e8837bc1-…", Client="Android", Device="Samsung Galaxy SIII", DeviceId="xxx", Version="1.0.0.0"
  ```
  — <https://dev.emby.media/doc/restapi/User-Authentication.html>
- **token 头 = `X-Emby-Token`**（"The return result object will have an **AccessToken** property. This should be included in all subsequent Http requests using the header **X-Emby-Token**."）— 同上
- 登录接口也是 `POST /Users/AuthenticateByName`，密码放 body 的 `pw`；登出 `POST /Sessions/Logout`；服务器信息 `GET /System/Info` — 同上
- **API Key**：请求头 `X-Emby-Token`，或查询参数 `api_key`（例：`http://localhost:8096/emby/System/Info?api_key=…`）— <https://dev.emby.media/doc/restapi/API-Key-Authentication.html>
- Emby 官方文档**未提及** `X-Emby-Authorization` 这个头名 → 「Emby 当前是否仍接受 `X-Emby-Authorization`」= **未能确认**。

> 📌 **对本报告最重要的对照**：Emby 用 `Authorization: Emby …, UserId="…"`；Jellyfin 用 `Authorization: MediaBrowser … , Token="…"`（无 UserId，token 内嵌在头里）。Jellyfin 仅在 legacy 开关打开时才认 `Emby` scheme。

---

## 3. 播放（Playback）

### 3.1 `POST /Items/{itemId}/PlaybackInfo`

**OpenAPI 事实（v12.1 spec）：**
- `GET  /Items/{itemId}/PlaybackInfo` → `operationId: GetPlaybackInfo`，tag `MediaInfo`，query 参数仅 `userId`
- `POST /Items/{itemId}/PlaybackInfo` → `operationId: GetPostedPlaybackInfo`，tag `MediaInfo`
- 响应 200 → **`PlaybackInfoResponse`**（三种 content-type：`application/json`、`application/json; profile="CamelCase"`、`application/json; profile="PascalCase"`）；404 → `ProblemDetails`
— <https://api.jellyfin.org/openapi/jellyfin-openapi-stable.json>

**`POST` 的 Query 参数（全部 `deprecated: true`，共 15 个）**：
`userId, maxStreamingBitrate, startTimeTicks, audioStreamIndex, subtitleStreamIndex, maxAudioChannels, mediaSourceId, liveStreamId, autoOpenLiveStream, enableDirectPlay, enableDirectStream, enableTranscoding, allowVideoStreamCopy, allowAudioStreamCopy`
源码里这 15 个都打了 `[FromQuery, ParameterObsolete]`，XML 注释原文：
> "For backwards compatibility parameters can be sent via Query or Body, with Query having higher precedence. **Query parameters are obsolete.**"
— `Jellyfin.Api/Controllers/MediaInfoController.cs` **L90-L135**
<https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Api/Controllers/MediaInfoController.cs#L90-L135>

**请求体 schema = `PlaybackInfoDto`（16 个字段，OpenAPI 与源码一致）**：

| 字段 | 类型 |
|---|---|
| `UserId` | string(Guid) |
| `MaxStreamingBitrate` | integer |
| `StartTimeTicks` | integer |
| `AudioStreamIndex` | integer |
| `SubtitleStreamIndex` | integer |
| `MaxAudioChannels` | integer |
| `MediaSourceId` | string |
| `LiveStreamId` | string |
| **`DeviceProfile`** | `DeviceProfile`（**内联对象**） |
| `EnableDirectPlay` / `EnableDirectStream` / `EnableTranscoding` | boolean |
| `AllowVideoStreamCopy` / `AllowAudioStreamCopy` | boolean |
| `AutoOpenLiveStream` | boolean |
| `AlwaysBurnInSubtitleWhenTranscoding` | boolean |
| — | — |

源码：`Jellyfin.Api/Models/MediaInfoDtos/PlaybackInfoDto.cs`（共 90 行）
<https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Api/Models/MediaInfoDtos/PlaybackInfoDto.cs>

**`PlaybackInfoResponse`（3 个字段，与 Emby 同名）**：
`MediaSources` (`IReadOnlyList<MediaSourceInfo>`), `PlaySessionId` (string), `ErrorCode` (`PlaybackErrorCode?`)
— 源码 `MediaBrowser.Model/MediaInfo/PlaybackInfoResponse.cs` **L25 / L31 / L37**
<https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/MediaInfo/PlaybackInfoResponse.cs#L25-L37>
  ↳ **与 Emby 完全同名**（Emby: `MediaSources`, `PlaySessionId`, `ErrorCode`，见 <https://dev.emby.media/reference/RestAPI/MediaInfoService/postItemsByIdPlaybackinfo.html>）。**没有改名。**

#### 🚩 `DeviceProfileId`：Jellyfin 在 PlaybackInfo 里**没有**这个字段

- `PlaybackInfoDto` 中**不存在** `DeviceProfileId`（OpenAPI + 源码双重确认）。
- 全仓库 `DeviceProfileId` 仅出现在：`MediaBrowser.Model/Dlna/StreamInfo.cs`（**L196 / L913-L916**，作为输出/URL 参数）与 `StreamBuilder.cs`（**L70 / L253**，写入 `streamInfo.DeviceProfileId = options.Profile.Id?.ToString("N")`）。
- 唯一作为**入参**出现的地方是 `/Videos/{itemId}/stream` 的 query 参数 **`deviceProfileId`（`deprecated: true`）**。
— OpenAPI <https://api.jellyfin.org/openapi/jellyfin-openapi-stable.json>；源码 <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Dlna/StreamInfo.cs#L196>

#### `DeviceProfile` 结构差异（Emby vs Jellyfin）

| | Jellyfin 12.1（OpenAPI）**11 个** | Emby（dev.emby.media）**13 个** |
|---|---|---|
| 共有 | `Name`, `Id`, `MaxStreamingBitrate`, `MusicStreamingTranscodingBitrate`, `MaxStaticMusicBitrate`, `DirectPlayProfiles`, `TranscodingProfiles`, `ContainerProfiles`, `CodecProfiles`, `SubtitleProfiles` | 同左 |
| Jellyfin 独有 | **`MaxStaticBitrate`** | — |
| Emby 独有 | — | `SupportedMediaTypes`, `DeclaredFeatures`, **`ResponseProfiles`** |

Jellyfin 源：<https://api.jellyfin.org/openapi/jellyfin-openapi-stable.json>（`components.schemas.DeviceProfile`）
Emby 源：<https://dev.emby.media/reference/RestAPI/MediaInfoService/postItemsByIdPlaybackinfo.html>
> ⚠️ Emby 侧文档页脚为 "Copyright 2022 © EMBY LLC"，**可能滞后于当前 Emby 版本**；Emby 现状的准确性需自行复核。

#### 请求体差异（Emby `PlaybackInfoRequest` vs Jellyfin `PlaybackInfoDto`）

| Emby 有、Jellyfin **没有** | Jellyfin 有、Emby **没有** |
|---|---|
| `Id`, `AllowInterlacedVideoStreamCopy`, `IsPlayback`, `CurrentPlaySessionId` | `AlwaysBurnInSubtitleWhenTranscoding` |
| （Emby 的 `DeviceProfile` 是**数组** `DeviceProfile[]`） | （Jellyfin 的 `DeviceProfile` 是**单个对象**） |

Emby 源：<https://dev.emby.media/reference/RestAPI/MediaInfoService/postItemsByIdPlaybackinfo.html>
Jellyfin 源：<https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Api/Models/MediaInfoDtos/PlaybackInfoDto.cs>

#### `MediaSourceInfo` 字段差异（45 个属性）

- **同名保留**（分叉后未改名）：`TranscodingUrl`, `TranscodingSubProtocol`, `TranscodingContainer`, `Protocol`, `Id`, `Path`, `Container`, `Size`, `Name`, `IsRemote`, `RunTimeTicks`, `SupportsTranscoding`, `SupportsDirectStream`, `SupportsDirectPlay`, `IsInfiniteStream`, `RequiresOpening`, `OpenToken`, `RequiresClosing`, `LiveStreamId`, `BufferMs`, `RequiresLooping`, `SupportsProbing`, `MediaStreams`, `Formats`, `Bitrate`, `Timestamp`, `RequiredHttpHeaders`, `AnalyzeDurationMs`, `DefaultAudioStreamIndex`, `DefaultSubtitleStreamIndex` …
- **Jellyfin 独有（Emby 文档中无）**：`ETag`, `IgnoreDts`, `IgnoreIndex`, `GenPtsInput`, `UseMostCompatibleTranscodingProfile`, `VideoType`, `IsoType`, `MediaAttachments`, `FallbackMaxStreamingBitrate`, `HasSegments`
- **Emby 有、Jellyfin OpenAPI 中无**：`Chapters`, `ProbePath`, `ProbeProtocol`, **`DirectStreamUrl`**, `AddApiKeyToDirectStreamUrl`, `SortName`, `HasMixedProtocols`, `ContainerStartTimeTicks`, `TrancodeLiveStartIndex`, `WallClockStart`, `ItemId`, `ServerId`, `MimeType`, `TranscodingMimeType`, `ReadAtNativeFramerate`(注：Jellyfin 有此字段，Emby 亦列)
- **两个 `[JsonIgnore]` 的内部字段（序列化时根本不出现在 JSON 里，别写进 API 文档）**：
  `TranscodeReasons`（`TranscodeReason` 枚举）与 `DefaultAudioIndexSource`
  — `MediaBrowser.Model/Dto/MediaSourceInfo.cs` **L119-L120**（master）
  <https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/Dto/MediaSourceInfo.cs#L117-L120>
  ↳ 这也解释了为什么 `TranscodeReasons` 在 OpenAPI 的 `MediaSourceInfo` 里查不到（但 `TranscodeReason` **枚举本身**在 spec 里存在，被 `TranscodingInfo` 引用，含 28 个取值：`ContainerNotSupported`, `VideoCodecNotSupported`, … `VideoRotationNotSupported`）。

Jellyfin 源：<https://api.jellyfin.org/openapi/jellyfin-openapi-stable.json>（`components.schemas.MediaSourceInfo`）
Emby 源：<https://dev.emby.media/reference/RestAPI/MediaInfoService/postItemsByIdPlaybackinfo.html>

### 3.2 直接播放 vs 转码的判定逻辑位置（源码）

| 环节 | 文件:行 |
|---|---|
| HTTP 入口（POST） | `Jellyfin.Api/Controllers/MediaInfoController.cs` **L116-L188** |
| DeviceProfile 来源优先级：body → 设备能力表 | 同上 **L137-L147**：`playbackInfoDto?.DeviceProfile`，为空则 `_deviceManager.GetCapabilities(User.GetDeviceId())?.DeviceProfile` |
| 组装 PlaybackInfo + 注入设备特定数据 | `Jellyfin.Api/Helpers/MediaInfoHelper.cs` **L93**（`GetPlaybackInfo`）、**L182**（`SetDeviceSpecificData`） |
| 构造判定器 | `MediaInfoHelper.cs` **L203** `var streamBuilder = new StreamBuilder(_mediaEncoder, _logger);` |
| **判定发生处** | `MediaInfoHelper.cs` **L270** `streamBuilder.GetOptimalVideoStream(options)` |
| 转码 URL 落到 `MediaSourceInfo.TranscodingUrl` | `MediaInfoHelper.cs` **L312 / L325** `mediaSource.TranscodingUrl = streamInfo.ToUrl(null, claimsPrincipal.GetToken(), …)` |
| 核心算法 | `MediaBrowser.Model/Dlna/StreamBuilder.cs` **L232**（`GetOptimalVideoStream`）、**L259**（`GetOptimalStream`）、**L262**（`SortMediaSources`） |
| `PlayMethod` 赋值点 | `StreamBuilder.cs` **L92**（DirectPlay）、**L99**（DirectStream）、**L475**、**L494**、**L816**（`playlistItem.PlayMethod = PlayMethod.Transcode;`） |
| 判定原因枚举 | `StreamBuilder.cs` **L307** `GetTranscodeReasonForFailedCondition` |

链接：
- <https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Api/Helpers/MediaInfoHelper.cs#L93> / [#L182](https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Api/Helpers/MediaInfoHelper.cs#L182) / [#L203](https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Api/Helpers/MediaInfoHelper.cs#L203) / [#L270](https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Api/Helpers/MediaInfoHelper.cs#L270) / [#L312](https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Api/Helpers/MediaInfoHelper.cs#L312)
- <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Dlna/StreamBuilder.cs#L232> / [#L262](https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Dlna/StreamBuilder.cs#L262) / [#L816](https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Dlna/StreamBuilder.cs#L816)

> ❗ 任务书里提到的 `Jellyfin.Api/Controllers/MediaInfoController.cs` 判定位置**不准确**：Controller 只做参数搬运与 DeviceProfile 选择，**真正的 direct play / transcode 判定在 `MediaBrowser.Model/Dlna/StreamBuilder.cs`**，由 `Jellyfin.Api/Helpers/MediaInfoHelper.cs` 调用。另外任务书提到的 `Jellyfin.Providers` 目录**不存在**（实际是 `MediaBrowser.Providers`）。

### 3.3 转码 / HLS 端点（**不在 OpenAPI 里！**）

**关键结论：这些端点全部被排除在 OpenAPI spec 之外。**
两个控制器类都带 `[ApiExplorerSettings(IgnoreApi = true)]`：
- `Jellyfin.Api/Controllers/DynamicHlsController.cs` **L40**
- `Jellyfin.Api/Controllers/HlsSegmentController.cs` **L23**

官方 release notes 亦确认：
> "**The HLS controllers are hidden from the specification**"
> "If an endpoint isn't listed in the OpenAPI specification it should not be used by clients. There are certain endpoints that are still exposed for legacy reasons despite being excluded from the OpenAPI spec. **These can be removed in any major release without warning**"
— <https://github.com/jellyfin/jellyfin/releases/tag/v12.0>（*Developers → API Changes*）

**确切路径（源码行号，v12.1）：**

| 方法 | 路径 | 位置 |
|---|---|---|
| `GET`/`HEAD` | `/Videos/{itemId}/master.m3u8` | DynamicHlsController **L404-L405** |
| `GET` | `/Videos/{itemId}/main.m3u8` | DynamicHlsController **L745** |
| `GET` | `/Videos/{itemId}/hls1/{playlistId}/{segmentId}.{container}` | DynamicHlsController **L1086** |
| `GET` | `/Videos/{itemId}/live.m3u8` | DynamicHlsController **L164** |
| `GET`/`HEAD` | `/Audio/{itemId}/master.m3u8`、`/Audio/{itemId}/main.m3u8`、`/Audio/{itemId}/hls1/{playlistId}/{segmentId}.{container}` | **L577 / L914 / L1268** |
| `GET` | `/Videos/{itemId}/hls/{playlistId}/stream.m3u8`（legacy） | HlsSegmentController **L79** |
| `GET` | `/Videos/{itemId}/hls/{playlistId}/{segmentId}.{segmentContainer}`（legacy） | HlsSegmentController **L126** |
| `GET` | `/Audio/{itemId}/hls/{segmentId}/stream.mp3` / `.aac`（legacy） | HlsSegmentController **L55-L56** |
| **`DELETE`** | **`/Videos/ActiveEncodings`** | HlsSegmentController **L103** |

- `DELETE /Videos/ActiveEncodings` 的参数：**`deviceId`（必填）、`playSessionId`（必填），均在 query**，返回 **204**；实现调用 `_transcodeManager.KillTranscodingJobs(deviceId, playSessionId, _ => true)` — HlsSegmentController **L103-L112**
- `/Videos/{itemId}/master.m3u8` 的 query 参数（源码，共 50+）：`static`, `params`, `tag`, `deviceProfileId`(`[ParameterObsolete]`), `playSessionId`, `segmentContainer`, `segmentLength`, `minSegments`, **`mediaSourceId`（`[FromQuery, Required]`，必填）**, `deviceId`, `audioCodec`, `enableAutoStreamCopy`, `allowVideoStreamCopy`, `allowAudioStreamCopy`, `audioSampleRate`, `maxAudioBitDepth`, `audioBitRate`, `audioChannels`, `maxAudioChannels`, `profile`, `level`, `framerate`, `maxFramerate`, `copyTimestamps`, `startTimeTicks`, `width`, `height`, `maxWidth`, `maxHeight`, `videoBitRate`, `subtitleStreamIndex`, `subtitleMethod`, `maxRefFrames`, `maxVideoBitDepth`, `requireAvc`, `deInterlace`, `requireNonAnamorphic`, `transcodingMaxAudioChannels`, `cpuCoreLimit`, `liveStreamId`, `enableMpegtsM2TsMode`, `videoCodec`, `subtitleCodec`, `transcodeReasons`, `audioStreamIndex`, `videoStreamIndex`, `context`, `streamOptions`, `enableAdaptiveBitrateStreaming`(默认 `false`), `enableTrickplay`(默认 `true`), `enableAudioVbrEncoding`(默认 `true`), `alwaysBurnInSubtitleWhenTranscoding`(默认 `false`)
  — DynamicHlsController **L404-L470**
- `GET|HEAD /Videos/{itemId}/stream` 与 `/Videos/{itemId}/stream.{container}`：**在** OpenAPI 里，共 **51 个 query 参数**（含 `deviceProfileId` deprecated、`streamOptions`、`transcodeReasons`、`enableAudioVbrEncoding` …）；**`security = None`**（OpenAPI 与源码一致：`VideosController` 类与 `GetVideoStream` 上都没有 `[Authorize]`）。
  — <https://api.jellyfin.org/openapi/jellyfin-openapi-stable.json>；源码 <https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Api/Controllers/VideosController.cs#L314-L318>
- **流 URL 的鉴权方式**：Jellyfin 自己在 URL 里拼 `?ApiKey=<token>` —— `MediaBrowser.Model/Dlna/StreamInfo.cs` **L1276-L1280**（注释原文 "Use \"?ApiKey=\" as seen in HEAD and other parts of the code"），另一处 **L1034** `sb.Append("&ApiKey=")`。
  <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Dlna/StreamInfo.cs#L1276-L1280>
  ↳ 这解释了什么 `?ApiKey=` 在 `AuthorizationContext` 里**唯独没有被 legacy 开关包住**（见 §2.2）：12.0 之后它仍是流式端点唯一可用的鉴权手段。

### 3.4 播放进度上报

| 方法 | 路径 | 请求体 schema | operationId |
|---|---|---|---|
| `POST` | `/Sessions/Playing` | **`PlaybackStartInfo`** | `ReportPlaybackStart` |
| `POST` | `/Sessions/Playing/Progress` | **`PlaybackProgressInfo`** | `ReportPlaybackProgress` |
| `POST` | `/Sessions/Playing/Stopped` | **`PlaybackStopInfo`** | `ReportPlaybackStopped` |
| `POST` | `/Sessions/Playing/Ping`（query `playSessionId` **必填**） | 无 body | `PingPlaybackSession` |

全部在 OpenAPI（tag `Session`，`security = [{"CustomAuthentication": ["DefaultAuthorization"]}]`）— <https://api.jellyfin.org/openapi/jellyfin-openapi-stable.json>
源码：`Jellyfin.Api/Controllers/PlaystateController.cs` **L201-L249**（类 `[Route("")]`、`[Authorize]`、`[Tags("Session")]`，见 L25-L27）
<https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Api/Controllers/PlaystateController.cs#L201-L249>

**模型定义（源码 + 行号）：**

- **`PlaybackStartInfo` 是 `PlaybackProgressInfo` 的空子类**（Jellyfin 的一个怪点，字段完全等同）：
  ```csharp
  public class PlaybackStartInfo : PlaybackProgressInfo { }
  ```
  `MediaBrowser.Model/Session/PlaybackStartInfo.cs` **L6**（全文件仅 9 行）
  <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Session/PlaybackStartInfo.cs>
  ↳ OpenAPI 里两者也确实是同一组 21 个属性。

- **`PlaybackProgressInfo`（21 字段）** — `MediaBrowser.Model/Session/PlaybackProgressInfo.cs`（120 行）：
  `CanSeek`(bool), `Item`(BaseItemDto), `ItemId`(Guid), **`SessionId`**, `MediaSourceId`, `AudioStreamIndex`(int?), `SubtitleStreamIndex`(int?), `IsPaused`, `IsMuted`, `PositionTicks`(long?), **`PlaybackStartTimeTicks`**(long?), `VolumeLevel`(int?), **`Brightness`**(int?), **`AspectRatio`**, `PlayMethod`(enum), `LiveStreamId`, `PlaySessionId`, **`RepeatMode`**, **`PlaybackOrder`**, **`NowPlayingQueue`**(QueueItem[]), **`PlaylistItemId`**
  <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Session/PlaybackProgressInfo.cs>

- **`PlaybackStopInfo`（11 字段）** — `MediaBrowser.Model/Session/PlaybackStopInfo.cs`（68 行）：
  `Item`, `ItemId`, `SessionId`, `MediaSourceId`, `PositionTicks`, `LiveStreamId`, `PlaySessionId`, `Failed`(bool), `NextMediaType`, `PlaylistItemId`, `NowPlayingQueue`
  <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Session/PlaybackStopInfo.cs>

- **`PlayMethod` 枚举**（与 Emby 字符串取值一致）：`Transcode = 0`, `DirectStream = 1`, `DirectPlay = 2`
  `MediaBrowser.Model/Session/PlayMethod.cs`
  <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Session/PlayMethod.cs>

#### 与 Emby「Playback Check-ins」官方文档的字段差异

Emby 官方文档（<https://dev.emby.media/doc/restapi/Playback-Check-ins.html>）声明的 body 字段：
`QueueableMediaTypes`, `CanSeek`, `ItemId`/`Item`, `MediaSourceId`, `AudioStreamIndex`, `SubtitleStreamIndex`, `IsPaused`, `IsMuted`, `PositionTicks`, `VolumeLevel`, `PlayMethod`, `PlaySessionId`, `LiveStreamId`, `PlaylistIndex`, `PlaylistLength`, `SubtitleOffset`, `PlaybackRate`；Progress 额外有 **`EventName`**（`TimeUpdate`/`Pause`/`Unpause`/`VolumeChange`/`RepeatModeChange`/`AudioTrackChange`/`SubtitleTrackChange`/`PlaylistItemMove`/`PlaylistItemRemove`/`PlaylistItemAdd`/`QualityChange`/`SubtitleOffsetChange`/`PlaybackRateChange`）。

| 差异方向 | 字段 |
|---|---|
| Emby 文档有、**Jellyfin 三个模型里都没有** | `QueueableMediaTypes`, **`EventName`**, `PlaylistIndex`, `PlaylistLength`, `SubtitleOffset`, `PlaybackRate` |
| Jellyfin 有、Emby 文档**未列** | `SessionId`, `PlaybackStartTimeTicks`, `Brightness`, `AspectRatio`, `RepeatMode`, `PlaybackOrder`, `NowPlayingQueue`, `PlaylistItemId` |

> `EventName` 在 Jellyfin 中**明确不存在**：我在 v10.11.11 / v12.1 / master 三个版本的 `PlaybackProgressInfo.cs` 上都 grep 不到该属性。
> ⚠️ Emby 侧依据是 2022 年版官方文档，**Emby 现状未能确认**。
> `POST /Sessions/Playing/Ping` 在 Emby 官方文档（2022）中**未出现**；「Emby 是否有该端点」= **未能确认**。

#### JSON 大小写

- **默认输出 = PascalCase**：`Jellyfin.Server/Extensions/ApiServiceCollectionExtensions.cs` 的 `AddJsonOptions` 使用 `JsonDefaults.PascalCaseOptions`，`PropertyNamingPolicy = null`
  <https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Server/Extensions/ApiServiceCollectionExtensions.cs>
  <https://github.com/jellyfin/jellyfin/blob/v12.1/src/Jellyfin.Extensions/Json/JsonDefaults.cs>
  （注：12.x 起部分项目移至 `src/` 前缀，路径为 `src/Jellyfin.Extensions/Json/JsonDefaults.cs`）
- 客户端可通过 `Accept: application/json; profile="CamelCase"` 切换为 camelCase（OpenAPI 中每个 200 响应都并列声明这三种 content-type）。
- 反序列化容忍数字以字符串形式给出（`NumberHandling = AllowReadingFromString`）。

---

## 4. 元数据刮削（Metadata scraping）

### 4.1 官方文档口径 vs 源码口径（**两者不一致，需并列陈述**）

**官方文档（jellyfin.org/docs/general/server/metadata/）原文：**
> "By default, Jellyfin ships with the following providers:
> - The Movie Database (TMDb)
> - The Open Movie Database API (OMDb API)
> - Local .nfo files
> There are more official providers available in our **Plugin Catalog**, like **TheTVDB**, **fanart.tv** or **AniDB**."
— <https://jellyfin.org/docs/general/server/metadata/>

**源码口径（v12.1 `MediaBrowser.Providers/Plugins/` 实际目录）：**
`AudioDb`、`ListenBrainz`、`MusicBrainz`、`Omdb`、`StudioImages`、`Tmdb`
（`ListenBrainz` 为 12.0 新增：release notes "**ListenBrainz is now bundled with the server** and provides similar artist data"）
— <https://github.com/jellyfin/jellyfin/tree/v12.1/MediaBrowser.Providers/Plugins>

> 结论：**官方文档的「默认内置」清单不完整**（漏了 MusicBrainz / AudioDb / StudioImages / ListenBrainz）。写报告时建议同时给出两套口径并注明差异。

### 4.2 逐项核查

| Provider | 是否内置（编译进服务端） | 证据 |
|---|---|---|
| **TheMovieDb (TMDb)** | ✅ 内置（作为 bundled plugin，`Plugin.cs` 的 `Name => "TMDb"`；Provider 名 `TmdbUtils.ProviderName`） | `MediaBrowser.Providers/Plugins/Tmdb/`，含 Movies/TV/People/BoxSets 子目录；<https://github.com/jellyfin/jellyfin/tree/v12.1/MediaBrowser.Providers/Plugins/Tmdb> |
| **OMDb** | ✅ 内置（`Plugin.cs` `Name => "OMDb"`；Provider 名 `"The Open Movie Database"`） | <https://github.com/jellyfin/jellyfin/tree/v12.1/MediaBrowser.Providers/Plugins/Omdb> |
| **MusicBrainz** | ✅ 内置（`Plugin.cs` `Name => "MusicBrainz"`） | <https://github.com/jellyfin/jellyfin/tree/v12.1/MediaBrowser.Providers/Plugins/MusicBrainz> |
| **AudioDb (TheAudioDB)** | ✅ 内置（`Plugin.cs` `Name => "AudioDB"`；Provider 名 `"TheAudioDB"`） | <https://github.com/jellyfin/jellyfin/tree/v12.1/MediaBrowser.Providers/Plugins/AudioDb> |
| **StudioImages** | ✅ 内置（`Plugin.cs` `Name => "Studio Images"`；Provider 名 `"Artwork Repository"`） | <https://github.com/jellyfin/jellyfin/tree/v12.1/MediaBrowser.Providers/Plugins/StudioImages> |
| **ListenBrainz** | ✅ 内置（12.0 起；`ListenBrainzPlugin.cs` `Name => "ListenBrainz Similarity Provider"`） | 同上目录 + <https://github.com/jellyfin/jellyfin/releases/tag/v12.0> |
| **Local metadata / NFO** | ✅ 内置，两个独立项目：`MediaBrowser.XbmcMetadata`（NFO 读写）、`MediaBrowser.LocalMetadata`（本地 XML：`BoxSetXmlProvider.cs`、`PlaylistXmlProvider.cs`） | <https://github.com/jellyfin/jellyfin/tree/v12.1/MediaBrowser.XbmcMetadata/Providers>（11 个 `*NfoProvider.cs`）、<https://github.com/jellyfin/jellyfin/tree/v12.1/MediaBrowser.LocalMetadata/Providers> |
| **BoxSet** | ✅ 内置（本地元数据）：`MediaBrowser.Providers/BoxSets/BoxSetMetadataService.cs` | <https://github.com/jellyfin/jellyfin/tree/v12.1/MediaBrowser.Providers/BoxSets> |
| **Zap2It** | ⚠️ 内置但**只是 ExternalUrl / ExternalId provider，不是刮削器**：`MediaBrowser.Providers/TV/Zap2ItExternalUrlProvider.cs`（`Name => "Zap2It"`）、`Zap2ItExternalId.cs` | <https://github.com/jellyfin/jellyfin/tree/v12.1/MediaBrowser.Providers/TV> |
| **TheTVDB** | ❌ **不在服务端源码里**（v10.11.11 / v12.1 / master 三个版本都没有 `Plugins/Tvdb` 目录，也没有任何 `*Tvdb*Provider*.cs`）。它是**官方插件**。 | 见下方 4.3 |
| **FanArt (fanart.tv)** | ❌ 不内置 → **官方插件** | 见 4.3 |
| **TVmaze** | ❌ 不内置 → **官方插件** | 见 4.3 |
| **AniDB / AniList / Kitsu / AniSearch** | ❌ 不内置 → 官方插件 | 见 4.3 |
| **Books：ComicBookInfo / ComicInfo / ComicVine / GoogleBooks / Isbn / OpenPackagingFormat** | ✅ 内置（`MediaBrowser.Providers/Books/`） | <https://github.com/jellyfin/jellyfin/tree/v12.1/MediaBrowser.Providers/Books> |

### 4.3 TheTVDB 与「拆成插件」问题：**已确认**

- **TheTVDB 仍是官方插件，但已不在服务端内置**：
  官方插件清单 `manifest.json`（36 条）中确有 **`TheTVDB`**，`guid = a677c0da-fac5-4cde-941a-7134223f14c8`，`category = MoviesAndShows`，`versions = 24`
  — <https://repo.jellyfin.org/files/plugin/manifest.json>（本地抓取于本次会话，HTTP 200，199,871 字节）
- 文档也把它归到 Plugin Catalog 而非内置：见上面 §4.1 引文。
- 源码中 `Tvdb` **只作为 provider ID 存在**（`MetadataProvider.Tvdb`），例如 NFO 读写与 TMDb 用它做外部 ID 查询：
  `MediaBrowser.XbmcMetadata/Parsers/BaseNfoParser.cs` **L251-L253**、`MediaBrowser.Providers/Plugins/Tmdb/TV/TmdbSeriesProvider.cs` **L92-L108**
  <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.XbmcMetadata/Parsers/BaseNfoParser.cs#L251-L253>
- 12.0 release notes 另加：**"TVDB provider IDs are supported for movies"** — <https://github.com/jellyfin/jellyfin/releases/tag/v12.0>
- **TheTVDB 何时被移出服务端**：**未能确认**（我未找到可引用的移除 PR / release note；只确认了 10.11.11 时它已不在源码树内）。

**TMDb 是否被拆成插件？→ 没有。** TMDb 至今仍是服务端内置的 bundled plugin（`MediaBrowser.Providers/Plugins/Tmdb/Plugin.cs`），官方插件清单里**没有** TMDb 条目。**未发现任何 TMDb 拆分的证据**。

**12.0 真正发生的 provider 变化**（官方 release notes）：
> "ListenBrainz is now bundled with the server…"
> "TVDB provider IDs are supported for movies"
> "AudioDb artist search"
> "MusicBrainz lookups are more resilient"
> "**The Bookshelf plugin has been deprecated** and its features have been merged into server or extracted into the **ComicVine** and **GoogleBooks** providers."
> "**Bookshelf has been split into separate GoogleBooks and ComicVine providers**"
> "**A new OpenLibrary plugin has been created** for metadata and images"
— <https://github.com/jellyfin/jellyfin/releases/tag/v12.0>

### 4.4 评分字段名

**Jellyfin 与 Emby 的这三个字段「声明完全相同」**（同一个祖先，连 XML 注释都一样）：

| 字段 | Jellyfin 12.1 声明 | Emby 声明 |
|---|---|---|
| `CriticRating` | `public float? CriticRating { get; set; }` | `public float? CriticRating { get; set; }` |
| `OfficialRating` | `public string OfficialRating { get; set; }` | `public string OfficialRating { get; set; }` |
| `CommunityRating` | `public float? CommunityRating { get; set; }` | `public float? CommunityRating { get; set; }` |

- Jellyfin 源：`MediaBrowser.Model/Dto/BaseItemDto.cs` **L127 / L143 / L181**
  <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Dto/BaseItemDto.cs#L127>
  <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Dto/BaseItemDto.cs#L143>
  <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Dto/BaseItemDto.cs#L181>
- Emby 源：<https://dev.emby.media/reference/pluginapi/MediaBrowser.Model.Dto.BaseItemDto.html>（页面内三处 `Declaration` 原文与上表一致）

> 结论：**评分字段名 Jellyfin 与 Emby 无差异**（均为 `CommunityRating` / `CriticRating` / `OfficialRating`）。
> ⚠️ Emby 侧页面为 2022 年版参考文档，**Emby 当前版本的字段集合未能全面确认**（仅这三个字段已核对）。

---

## 5. 插件生态

### 5.1 Jellyfin：公开 JSON manifest，任何人可自建仓库

- **官方仓库 URL（官方文档明确写出）**：
  - stable：`https://repo.jellyfin.org/files/plugin/manifest.json`
  - unstable：`https://repo.jellyfin.org/files/plugin-unstable/manifest.json`
  — <https://jellyfin.org/docs/general/server/plugins/>
- **manifest 实测**：顶层是一个 **JSON 数组**，本次抓取共 **36 个插件**。字段示例（第一条）含 `guid` / `name` / `category` / `versions[]`。
  实测包含的插件名：Bookshelf, Fanart, IMVDb, Kodi Sync Queue, LDAP Authentication, NextPVR, Open Subtitles, Playback Reporting, Reports, TMDb Box Sets, Trakt, TVHeadend, Cover Art Archive, **TheTVDB**, AniDB, AniList, AniSearch, Kitsu, **TVmaze**, Webhook, OPDS, Session Cleaner, VGMdb, Simkl, Subtitle Extract, Discogs, Artwork, Local Intros, DLNA, Transcode Killer, Chapter Segments Provider, LrcLib Lyrics, Google Books, Comic Vine, OpenLibrary, Folio
  — <https://repo.jellyfin.org/files/plugin/manifest.json>
- **manifest schema（C# 模型，权威）**：`MediaBrowser.Common/Plugins/PluginManifest.cs`
  `category`, `changelog`, `description`, **`guid`**, `name`, `overview`, `owner`, **`targetAbi`**, `timestamp`, `version`, `status`(`PluginStatus`), `autoUpdate`(默认 `true`), `imagePath`, `assemblies`(string[])
  <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Common/Plugins/PluginManifest.cs>
- **每版本条目 schema**：`MediaBrowser.Model/Updates/VersionInfo.cs`
  `version`, `changelog`, `targetAbi`, **`sourceUrl`**, **`checksum`**, `timestamp`, `repositoryName`, `repositoryUrl`
  <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Updates/VersionInfo.cs>
- **第三方可自建仓库**：官方文档直接列出多个第三方 manifest URL（如 `https://raw.githubusercontent.com/…/manifest.json`），并说明「Manage Repositories」可改 URL。
  — <https://jellyfin.org/docs/general/server/plugins/>
- 插件模板仓库：<https://github.com/jellyfin/jellyfin-plugin-template>（同页引用）
- **ABI 绑定**：12.0 "The server now targets **.NET 10**. Plugins have to be retargeted and rebuilt"；manifest 里的 `targetAbi` 即该校验字段 — <https://github.com/jellyfin/jellyfin/releases/tag/v12.0>

### 5.2 Emby：中心化、人工审核、支持付费插件

- 插件目录**由 Emby 团队集中管理**，开发者需要**向 Emby 团队申请 developer id**，然后登录后台 `https://plugins.emby.tv/admin/packages.html` 上传：
  > "Then, request a **developer id from the Emby team** by sending a PM to ebr or posting in the dev forum that you are ready to publish. Once you have your credentials you'll need to go to: https://plugins.emby.tv/admin/packages.html"
  — <https://dev.emby.media/doc/plugins/dev/Getting-your-plug-in-in-the-catalog.html>
- 目录条目字段（与 Jellyfin manifest 的公开字段**不一一对应**）：`Name`, `GUID`, `Short Description`, `Overview`, `Website`, `Thumb Image`, `Preview Image`, `Target System`, `Package Type`(Userinstalled), `Category`, `Tile Color`, `Target File Name`, **`Premium Plug-in`**, **`Feature ID`**, **`Price`**, `Registration Information`；版本条目：`Version`, **`Version Class`(Dev/Beta/release)**, `Description`, **`Required Version`**
  — 同上
- **付费插件是官方支持的一等公民**：
  > "**Premium Plug-in** - Check this if users will be required to register this plug-in (pay for it). All premium plug-ins will automatically have a **14-day free trial** after first install."
  — 同上
  对应接口：`IRequiresRegistration`（"If your plugin will be a premium plugin, see IRequiresRegistration"）— <https://dev.emby.media/doc/plugins/dev/>
- **开发政策（Development Policy）对插件内容有强约束**：
  > "**No plug-in shall directly violate or otherwise circumvent or cause the Emby product as a whole to violate or circumvent any laws as governed by the United States of America.**" 包含：违反数据源 ToS、**未经许可的 web scraping**、违反库许可证；"Any plug-in that violates this rule will be **removed from the plug-in catalog without notice**."，并点名禁止 scraping IMDb.com、禁止直接下载 YouTube 视频。
  — <https://dev.emby.media/doc/plugins/dev/Development-Policy.html>
- 插件入口点接口：`IServerEntryPoint`；可用接口 `IFileSystem`, `IHttpClient`, `INetworkManager`, `IProcessFactory`, `IZipClient` — <https://dev.emby.media/doc/plugins/dev/>
- **Emby 是否有公开的机器可读 manifest JSON 地址**：**未能确认**（dev.emby.media 未记载；未找到官方发布的 URL）。

---

## 6. 许可证与 IP

### 6.1 Jellyfin = GPLv2

仓库根 `LICENSE` 首行原文：
```
GNU GENERAL PUBLIC LICENSE
                       Version 2, June 1991
```
— <https://github.com/jellyfin/jellyfin/blob/v12.1/LICENSE>
README 亦挂 "GPL 2.0 License" badge — <https://github.com/jellyfin/jellyfin/blob/master/README.md>

### 6.2 Emby = 专有（闭源）

**Emby Terms of Service（Updated August 16, 2026，© 2026 Emby LLC）关键条款原文：**
- §3(b) 授权范围：「Emby grants you a **personal, non-commercial**, worldwide, royalty-free, **revocable, non-transferable, non-sublicensable, and non-exclusive** license to use the software」
- §3(d) 禁止事项：「you may not … (1) **copy, modify, distribute, sell, or lease** any part of the Software; (2) **reverse engineer, disassemble, decompile**, or otherwise attempt to discover the source code or structure …; (4) **develop any improvement, modification, or derivative works of the Software, or include any portion thereof in any other product**, software, work, equipment, or item …」
- §3(g) 反向工程例外：仅限「as permitted by applicable law」或「for the purpose of debugging modifications made by you to certain third party files in source code format that are licensed under the **LGPL** or under the **GPL2**」（且须先有善意尝试失败）
- §3(h) 所有权：「**Emby shall own all title, ownership rights, and intellectual property rights** in and to the Software, and any copies or derivative works thereof」
- §4(c) Access Services：「solely for **non-commercial, private and personal use**」
- 页脚：「Downloading any Emby software constitutes acceptance of these terms.」
— <https://emby.media/terms.html>

**Emby 闭源的时间/版本**：见 §1 的版本分歧说明（第三方记录为 3.6 起；Jellyfin 官方措辞为 4.x；一手 Emby issue 未能打开）→ 精确版本号**未能确认**，但「Emby 现为专有软件」**已确认**（ToS §3 明文 + Emby 自身不再分发服务端源码）。

### 6.3 对第三方客户端/App 开发者意味着什么（只列可核实项）

| 问题 | 可核实的答案 | 出处 |
|---|---|---|
| Jellyfin 服务端许可证 | GPLv2 | <https://github.com/jellyfin/jellyfin/blob/v12.1/LICENSE> |
| Jellyfin 是否明文规定「调用其 HTTP API 的第三方客户端需遵守何种许可证」 | **未能确认**——我在 jellyfin.org/docs 全站 108 个 `/docs/` 页面（sitemap 全文）与 README/FAQ/About 中**未找到**任何针对第三方客户端的许可证义务条款 | <https://jellyfin.org/sitemap.xml> |
| Jellyfin 是否有商标/品牌使用政策 | 有独立页面：`/docs/general/contributing/branding`（本报告按分工未逐字核对内容） | <https://jellyfin.org/docs/general/contributing/branding> |
| Emby API 是否有官方文档 | **有**：`https://dev.emby.media`（REST API 文档 + 完整 Reference + 静态 Swagger `http://swagger.emby.media/?staticview=true`；交互版只能从运行中的 Emby Server 仪表盘启动） | <https://dev.emby.media/doc/restapi/index.html> |
| Emby API 的使用条款 | dev.emby.media 每页页脚只指向通用 ToS，**没有独立的 API 许可条款**；适用条款即上述 ToS（§3(d) 明确禁止修改/派生/嵌入其它产品，§4(c) 限非商业个人使用）→「API 本身是否有单独条款」**未能确认** | <https://dev.emby.media/doc/restapi/index.html>、<https://emby.media/terms.html> |
| Emby 是否要求第三方客户端申请授权 | Emby 官方论坛存在「Permission request — Integrating Emby into JellyWatch (Android application)」「Third-party client using Emby REST API — API usage & "Emby" branding in store listing」等主题，说明实践中需要申请；**但我未打开/核实这些主题的结论** → 结论**未能确认** | <https://emby.media/community/topic/142945-permission-request-%E2%80%94-integrating-emby-into-jellywatch-android-application/> |

---

## 7. Emby vs Jellyfin 对照表

> 「未能确认」= 我未找到可引用的一手来源。Emby 侧主要依据 dev.emby.media（页面版权年 2022），**Emby 现状可能已变化**。

| # | 维度 | Jellyfin（12.1 stable） | Emby | 结论 |
|---|---|---|---|---|
| 1 | **认证头格式** | `Authorization: MediaBrowser Client="…", Device="…", DeviceId="…", Version="…", Token="…"`（scheme **必须** `MediaBrowser`，无 `UserId`） | `Authorization=Emby UserId="…", Client="…", Device="…", DeviceId="…", Version="…"`（scheme **`Emby`**，**含 `UserId`**） | **不同**（scheme 名不同；Jellyfin 无 UserId、Emby 无 Token 键） |
| 2 | **token 头** | 无独立 token 头（token 放在 `Authorization` 头的 `Token=` 键里）；`X-Emby-Token` / `X-MediaBrowser-Token` **12.0 起默认关闭**；`?ApiKey=` **仍可用** | **`X-Emby-Token`**（官方文档主推）；API Key 亦可用 `?api_key=` | **不同**（Jellyfin 12.x 已弃用 `X-Emby-Token`） |
| 3 | **`X-Emby-Authorization` 兼容** | **仅当 `EnableLegacyAuthorization=true`**（12.x 默认 `false` + 迁移强制 `false`；10.11.11 默认 `true`） | Emby 官方文档**未提及**此头名 → **未能确认** | Jellyfin 侧：**12.0 起默认不收** |
| 4 | **API 基址前缀** | `/emby/*` 与 `/mediabrowser/*` **已于 12.0 移除**；根路径即 API | **`/emby/{apipath}`** | **不同（12.0 起分道扬镳）** |
| 5 | **PlaybackInfo 路径** | `GET` + `POST /Items/{itemId}/PlaybackInfo` | `POST /Items/{Id}/PlaybackInfo` | **路径形状相同**，路由参数名不同（`itemId` vs `Id`），且 Jellyfin 多一个 `GET` |
| 6 | **PlaybackInfo 请求体** | `PlaybackInfoDto`（16 字段；**无** `Id`/`IsPlayback`/`CurrentPlaySessionId`/`AllowInterlacedVideoStreamCopy`；`DeviceProfile` 为**单个对象**；多 `AlwaysBurnInSubtitleWhenTranscoding`） | `PlaybackInfoRequest`（含 `Id`、`IsPlayback`、`CurrentPlaySessionId`、`AllowInterlacedVideoStreamCopy`；`DeviceProfile` 为**数组**） | **字段有增删差异** |
| 7 | **`DeviceProfileId`** | PlaybackInfo 请求体里**没有**；仅作为 `/Videos/{itemId}/stream` 的 **deprecated** query 参数存在 | Emby 的 `PlaybackInfoRequest` 里也**没有** `DeviceProfileId`（只有 `DeviceProfile`） | **一致（都没有）**；"Jellyfin 有 DeviceProfileId 参数字段"的说法**不成立** |
| 8 | **PlaybackInfo 响应** | `MediaSources` / `PlaySessionId` / `ErrorCode` | `MediaSources` / `PlaySessionId` / `ErrorCode` | **完全同名，未改名** |
| 9 | **播放上报路径** | `POST /Sessions/Playing`、`/Sessions/Playing/Progress`、`/Sessions/Playing/Stopped`（+ Jellyfin 独有 `POST /Sessions/Playing/Ping?playSessionId=`） | `POST /Sessions/Playing`、`/Sessions/Playing/Progress`、`/Sessions/Playing/Stopped`（官方文档未列 Ping） | **路径完全同名**；Jellyfin 多一个 Ping（Emby 是否有 → 未能确认） |
| 10 | **播放上报字段** | `PlaybackProgressInfo` 21 字段 / `PlaybackStopInfo` 11 字段；`PlaybackStartInfo` 为**空子类**；**无 `EventName`**；有 `SessionId`/`PlaybackStartTimeTicks`/`Brightness`/`AspectRatio`/`RepeatMode`/`PlaybackOrder`/`NowPlayingQueue`/`PlaylistItemId` | 官方文档字段：`QueueableMediaTypes`/`EventName`/`PlaylistIndex`/`PlaylistLength`/`SubtitleOffset`/`PlaybackRate` 等 | **有增删差异**；`PlayMethod` 取值集合一致（`Transcode`/`DirectStream`/`DirectPlay`） |
| 11 | **MediaSource 字段名** | `TranscodingUrl`、`TranscodingSubProtocol`、`TranscodingContainer`、`MediaStreams`、`Bitrate`、`Formats`、`PlaySessionId`(在 Response 上) 等**与 Emby 同名**；多 `ETag`/`IgnoreDts`/`IgnoreIndex`/`GenPtsInput`/`VideoType`/`IsoType`/`MediaAttachments`/`FallbackMaxStreamingBitrate`/`HasSegments`/`UseMostCompatibleTranscodingProfile`；**无** `DirectStreamUrl`/`AddApiKeyToDirectStreamUrl`/`Chapters`/`ProbePath`/`SortName`/`ServerId`/`MimeType`/`TranscodingMimeType`；`TranscodeReasons` 是 `[JsonIgnore]` 内部字段 | 见左（Emby 参考文档） | **同名为主 + 双向增删**；`TranscodingUrl` **未改名** |
| 12 | **转码 URL 形态** | `GET /Videos/{itemId}/master.m3u8`（`mediaSourceId` **必填**）、`/Videos/{itemId}/main.m3u8`、`/Videos/{itemId}/hls1/{playlistId}/{segmentId}.{container}`、`/Audio/{itemId}/…`、legacy `/Videos/{itemId}/hls/{playlistId}/stream.m3u8`、`DELETE /Videos/ActiveEncodings?deviceId=&playSessionId=`（204）；**全部 `[ApiExplorerSettings(IgnoreApi = true)]`，不在 OpenAPI 内** | 官方 REST 文档未给出这些具体 HLS 路径（Emby 的 API Browser 需从运行中的服务器启动）→ **未能确认** | Jellyfin 侧**已确认，但官方明确「不在 spec 内的端点客户端不应使用，可能在任意大版本无预警移除」** |
| 13 | **Providers（内置）** | 文档口径：**TMDb、OMDb、Local .nfo**；源码口径另含 **MusicBrainz、AudioDb、StudioImages、ListenBrainz**、Books 系列（ComicBookInfo/ComicInfo/ComicVine/GoogleBooks/Isbn/OPF）；`Zap2It` 仅为 ExternalUrl/Id provider | Emby 官方文档未提供等价的内置 provider 清单 → **未能确认** | Jellyfin 侧**已确认（文档与源码两套口径）** |
| 14 | **TheTVDB** | **不内置**，为官方插件（guid `a677c0da-fac5-4cde-941a-7134223f14c8`） | 未能确认 | — |
| 15 | **FanArt / TVmaze / AniDB / AniList / Kitsu / AniSearch** | **不内置**，均为官方插件 | 未能确认 | — |
| 16 | **NFO 支持** | ✅ 内置，独立项目 `MediaBrowser.XbmcMetadata`（`Providers/` 下 11 个 `*NfoProvider.cs` + `Savers/` + `Parsers/`）+ `MediaBrowser.LocalMetadata`（XML） | 未能确认（Emby 是否内置 NFO → 未找到官方文档说明） | Jellyfin 侧**已确认** |
| 17 | **评分字段** | `CommunityRating`(float?)、`CriticRating`(float?)、`OfficialRating`(string) | **完全相同**的声明 `public float? CommunityRating` / `public float? CriticRating` / `public string OfficialRating` | **无差异** |
| 18 | **插件体系** | 公开 JSON manifest（stable `https://repo.jellyfin.org/files/plugin/manifest.json`，36 条）；schema = `PluginManifest.cs` + `VersionInfo.cs`（`guid`/`name`/`category`/`targetAbi`/`sourceUrl`/`checksum`/`autoUpdate`/`assemblies`…）；**任何人可自建仓库**并填 URL；模板仓库公开 | 中心化、需向 Emby 团队申请 developer id，后台 `plugins.emby.tv/admin/packages.html` 上传；**支持 Premium 付费插件**（14 天试用 / `IRequiresRegistration`）；Development Policy 对 scraping/ToS/许可证有强约束并可无预警下架；**公开 manifest URL 未能确认** | **机制差异大** |
| 19 | **许可证** | **GPLv2**（仓库 LICENSE 明文） | **专有**：ToS §3(b) 个人非商业、可撤销、不可转让/再许可；§3(d) 禁复制/修改/分发/反向工程/派生作品/嵌入其它产品；§3(h) Emby 拥有全部 IP | **根本不同** |
| 20 | **闭源时间点** | fork 基座 = Emby **3.5.2**，2018 年 12 月初 | Jellyfin 官方称「4.x 闭源」；第三方（NixOS #51832，2018-12-10）称「3.6 起源码不再分发」 | **版本号存分歧 → 3.6 说未能由一手来源确认** |

---

## 8. 我打不开 / 取不到正文的来源清单

| URL | 情况 |
|---|---|
| `https://jellyfin.org/docs/general/server/authentication` | **HTTP 404**（该页面不存在；jellyfin.org sitemap 全站 213 条 URL 中无任何认证页） |
| `https://dev.emby.media/reference/RestAPI/SessionsService/postSessionsPlaying.html` | **HTTP 404**（Emby reference 站点无此文件名） |
| `https://github.com/MediaBrowser/Emby/issues/3479`（含 `#issuecomment-444985456`） | 页面只返回 GitHub 导航框架（JS 渲染），**issue 正文取不到** → 「Emby 3.6 起闭源」因此无法由一手来源确认 |
| `https://api.github.com/repos/MediaBrowser/Emby/issues/comments/444985456` | 返回**空对象**（无法确认该评论是否仍存在/可访问） |
| `https://en.wikipedia.org/wiki/Emby` | `web_fetch` 报错：`URL hostname "en.wikipedia.org" resolves to a non-public IP address`（环境网络限制） |
| `https://raw.githubusercontent.com/...` | 按环境说明**不可用**，本报告未将其作为任何结论的来源 |
| `https://mintlify.wiki/jellyfin/jellyfin/api/authentication/overview` | **可打开**，但属第三方 Mintlify 镜像（非 jellyfin.org / 非官方仓库），**未作为权威来源引用**；其内容（如 "Method 3: Legacy Headers (if enabled)"）与本报告从源码得出的结论方向一致，但请勿引为官方出处 |

---

## 9. 三条最值得写进中文报告的「反直觉」发现

1. **Jellyfin 12.0 是一次认证断代**：`X-Emby-Authorization`、`X-Emby-Token`、`Emby` scheme、`/emby/*` 路由前缀**全部默认失效**，且对既有安装**强制迁移关闭**。任何基于 Emby 时代写法的第三方客户端在 Jellyfin 12.x 上会直接 401。
   <https://github.com/jellyfin/jellyfin/releases/tag/v12.0> + <https://github.com/jellyfin/jellyfin/pull/15559>

2. **`?ApiKey=` 查询参数是唯一没被关掉的 legacy 通道**，因为 Jellyfin 自己的流式 URL 就靠它（`StreamInfo.cs` 里手写 `"?ApiKey=" + accessToken`）。这是「HLS/stream 端点没有 `[Authorize]`」与「legacy 认证被关」之间唯一的接缝。
   <https://github.com/jellyfin/jellyfin/blob/v12.1/Jellyfin.Server.Implementations/Security/AuthorizationContext.cs#L103-L106> + <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/Dlna/StreamInfo.cs#L1276-L1280>

3. **`PlaybackInfoResponse` 与 `MediaSourceInfo.TranscodingUrl` 都没改名**——Jellyfin 在播放主链路上保持了 Emby 的字段名（`MediaSources` / `PlaySessionId` / `ErrorCode` / `TranscodingUrl` / `TranscodingSubProtocol` / `TranscodingContainer`），差异集中在**请求体**（`DeviceProfile` 对象 vs 数组、去掉 `CurrentPlaySessionId`/`IsPlayback`）、**上报模型**（去掉 `EventName`/`QueueableMediaTypes`，加上 `RepeatMode`/`PlaybackOrder`/`NowPlayingQueue`）与**判定实现**（`MediaBrowser.Model/Dlna/StreamBuilder.cs`）上。
   <https://github.com/jellyfin/jellyfin/blob/v12.1/MediaBrowser.Model/MediaInfo/PlaybackInfoResponse.cs#L25-L37>
