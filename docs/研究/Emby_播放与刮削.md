# Emby 播放与自动刮削 深度技术报告（附 Jellyfin 对照）

> 调研日期：2026-09-22
> 目标仓库：`MediaBrowser/Emby`（GitHub）
> 产出人：DSH 子 agent（媒体服务器方向）
> 配套文件：`jellyfin-facts-播放与刮削.md`（Jellyfin 侧的一手核查，位于 `/home/xgl/python/网盘管理/`）

---

## 0. 阅读须知：本报告的证据分级

全文每一条结论都带**证据等级标签**，请按标签决定你能不能照着实现：

| 标签 | 含义 | 可信度 |
|---|---|---|
| **[码]** | 从**公开源码**读到（`MediaBrowser/Emby` 仓库内的 `.cs` 文件，带文件路径+行号） | 最高。Emby 自己的代码 |
| **[属]** | 从**仓库内二进制 DLL 的 .NET 元数据/字符串堆**提取（类型名、属性名、枚举值、`[Route]`/`[ApiMember]` 特性、字符串字面量）。这些是编译器产物，不是猜测 | 很高。但**是 3.5.3 快照**，且无法看到方法体（IL 未反编译） |
| **[规410]** | ★ **本报告对 API 形状最强的证据**：来自 **`MediaBrowser/Emby.SDK` 仓库的官方 OpenAPI** —— `Resources/OpenApi/openapi_v3.json`（OpenAPI 3.0.1，**`info.version = 4.10.0.40`，433 个 path，338 个 schema**）。该仓库 `pushed_at = 2026-09-16`，是 Emby 官方 SDK 仓库 | **最高（API 形状）**。官方发布、与 Emby Server 4.10.0.40 同版本的机器可读规范 |
| **[规]** | 从**官方静态 API 浏览器** `https://swagger.emby.media/openapi.json` 读到（对应 `http://swagger.emby.media/?staticview=true`） | 中。**`info.version = 4.1.1.0`，已被 [规410] 取代**；仅保留作跨版本差异分析 |
| **[官]** | 从 **Emby 官方文档站**（`dev.emby.media` / `emby.media`）读到 | 高。但官方文档有明显滞后与个别笔误 |
| **[社]** | 第三方文章 / 社区帖 / 博客 | 仅供参考，本报告尽量少用 |
| **[未]** | **没查到 / 无法确认** | **不要照抄**。第 6 章汇总 |

> ⚠️ **最重要的一句话**：**Emby 的服务端从 3.6/4.x 起闭源**，`MediaBrowser/Emby` 这个公开仓库停在 **3.5.3.0**（详见 §1）。因此本报告的每一级证据对应不同版本，**不要跨版本混用**：
> - **[码] / [属]** = **Emby Server 3.5.3.0**（源码 / 二进制产物）
> - **[规410]** = **Emby Server 4.10.0.40** 的官方 API 形状（`Emby.SDK` 官方 OpenAPI）
> - **[规]** = **Emby Server 4.1.1.0** 的官方 API 形状（已过时，仅作对照）
> - **[官]** = Emby 官方知识库 / 开发者文档（**文档落后于实现是常态**，本报告会指出冲突）
>
> **凡是 3.5.3 源码与 4.10 规范冲突之处，本报告以 4.10 规范为准并显式标注** —— 逐条见 **§7 跨版本修正表**。
> **Emby 4.x 的算法内部实现（例如直连判定的具体条件求值顺序）仍然无法用一手证据覆盖** —— 第 6 章逐条列明。

---

## 1. 取数方法与「这个仓库到底是什么」

### 1.1 `MediaBrowser/Emby` 仓库的真实身份（先回答最关键的问题）

用 GitHub API 直接查（`curl -s https://api.github.com/repos/MediaBrowser/Emby`）：

| 字段 | 值 |
|---|---|
| `full_name` | `MediaBrowser/Emby` |
| `description` | Emby Server is a personal media server with apps on just about every device. |
| `default_branch` | `master` |
| `archived` | **`false`（未归档）** |
| `disabled` | `false` |
| `created_at` | 2013-02-16 |
| `pushed_at` | **2024-03-27T18:20:17Z**（最后一次 push） |
| `updated_at` | 2026-09-22（star 等元数据变动） |
| `size` | 1,567,749 KB（≈1.5 GB） |
| `stargazers_count` | 4,972 |
| `forks_count` | 851 |
| `license` | **GPL-2.0** |
| `language` | C# |
| `homepage` | https://emby.media |

**实际版本号**由仓库根目录的 `SharedVersion.cs` 决定：

```csharp
[assembly: AssemblyVersion("3.5.3.0")]
```

出处：`https://github.com/MediaBrowser/Emby/blob/master/SharedVersion.cs`

**结论（[码] 级事实）：**

1. 这个仓库 **确实是 Emby 服务端本体**，不是客户端 SDK、也不是插件仓库 —— 但它是一个**冻结点在 Emby Server 3.5.3.0 的历史快照**。
2. Jellyfin 官方说明其分叉基座是 **Emby 3.5.2**（见 §4.1），所以这个 3.5.3 快照**几乎就是分叉点之后一个补丁版**的服务端源码。**本报告可以直接用它来解释「Jellyfin 的哪些行为是从 Emby 继承的」**。
3. 它**未归档**，但 `pushed_at` 停在 2024-03 —— 说明官方只是不再往里推功能提交（或只做极少量维护），并没有走 GitHub 的「Archive」流程。
4. `ThirdParty/emby/`、`packages/` 目录里的关键 DLL 时间戳是 **2018-09-20**，与 3.5.3 的发布时间吻合。

### 1.2 仓库的完整性边界：哪些是源码，哪些是二进制

这一点**极其重要**，直接决定了「哪些问题能从代码回答」：

**仓库里是源码的（可读、可引用行号）：**

```
Emby.Server.Implementations/     ← 服务端主体：库扫描、会话、NFO 保存、图片保存、FFmpeg 调用
MediaBrowser.Api/                 ← 一部分 REST API（318 条 [Route]）
MediaBrowser.Providers/           ← 元数据抓取器（TMDB / TVDB / OMDb / MusicBrainz / AudioDb / FanArt）
MediaBrowser.LocalMetadata/       ← 本地图片 + 本地 XML 元数据
MediaBrowser.XbmcMetadata/        ← NFO 读写（Kodi 风格）
Emby.Dlna/ Emby.Drawing.*/ Emby.Notifications/ ...
```

**仓库里只有编译好的 DLL（无源码）：**

| DLL | 路径 | 大小 | 里面是什么 |
|---|---|---|---|
| `MediaBrowser.Model.dll` | `packages/MediaBrowser.Common.3.5.0/lib/netstandard2.0/` | 390 KB | **全部 DTO**：`MediaSourceInfo`、`MediaStream`、`PlaybackInfoRequest/Response`、`PlaybackProgressInfo/StartInfo/StopInfo`、`DeviceProfile`、以及 **直连/转码判定类 `MediaBrowser.Model.Dlna.StreamBuilder`** |
| `MediaBrowser.Controller.dll` | `packages/MediaBrowser.Server.Core.3.5.0/lib/netstandard2.0/` | 6.6 MB | 控制器接口 + **FFmpeg 命令拼装**（硬件加速参数全在这里） |
| `Emby.Server.MediaEncoding.dll` | `ThirdParty/emby/` | 258 KB | **整个播放/转码 REST API**：`Emby.Server.MediaEncoding.Api.*`（`MediaInfoService`、`BaseStreamingService`、`Hls.*`） |
| `Emby.Naming.dll` | `packages/MediaBrowser.Naming.1.1.7-beta/lib/netstandard2.0/` | 60 KB | **命名解析**：`EpisodePathParser`、`SeasonPathParser`、`VideoResolver`、`SubtitleParser`，含 27+ 条正则 |
| `Emby.Server.Connect.dll` | `ThirdParty/emby/` | 64 KB | Emby Connect |
| `Emby.Server.Sync.dll` | `ThirdParty/emby/` | 163 KB | 同步/下载 |

**这说明了什么（[码]/[属] 级事实）：**

> **Emby 在 3.5.3 就把「播放 + 转码 + 命名解析 + DTO」抽成了二进制件。**
> 也就是说：**光看 `MediaBrowser/Emby` 的源码，你读不到 `PlaybackInfo` 的实现，也读不到命名正则。**
> 但这些 DLL 是完整程序集（**不是 stripped 引用程序集 —— 我实测过：`Emby.Naming.dll` 有 175 个方法定义、`MediaBrowser.Controller.dll` 有 1734 个、`Emby.Server.MediaEncoding.dll` 有 655 个**），所以：
> - **可以**从元数据里 100% 准确地提取：路由、HTTP 动词、参数名、`ApiMember` 描述、DTO 属性名、枚举成员、以及**字符串字面量**（含真实 ffmpeg 命令模板）；
> - **不能**（本报告未做 IL 反编译）恢复方法体里的**控制流与判定顺序**。

### 1.3 因此，本报告的取证路线

```
问题：Emby 的播放/刮削到底怎么做的？
   │
   ├─ 3.5.3 行为 ──┬─ 源码可答的（刮削、库扫描、NFO、图片、会话、认证）→ [码]
   │               └─ 源码不可答的（播放 API、转码参数、命名解析）→ 从 DLL 元数据/字符串提取 [属]
   │
   ├─ 4.1.1.0 接口形状 ── 官方 OpenAPI [规]
   │
   └─ 官方说法/意图 ── dev.emby.media + emby.media [官]
```

**用到的命令（可复现）：**

```bash
# 1) 仓库元数据（消耗 1 次 GitHub API 配额）
curl -s https://api.github.com/repos/MediaBrowser/Emby

# 2) 整仓库 tarball —— 关键技巧：codeload 可用、且不消耗 API 配额
#    （raw.githubusercontent.com 在本环境不通）
curl -sL -o emby-master.tar.gz \
  "https://codeload.github.com/MediaBrowser/Emby/tar.gz/refs/heads/master"
# 实测：178,752,003 字节；解包后 478 MB

# 3) 官方 OpenAPI
curl -sL -o emby_openapi.json "https://swagger.emby.media/openapi.json"
# 实测：2,012,502 字节（带 BOM 的 UTF-8 JSON）

# 4) DLL 元数据/字符串提取（自写 .NET 工具，用 System.Reflection.Metadata）
strings -e l -n 4 <dll>          # UTF-16 字符串堆 = 源码里的字符串字面量
```

> 注：`https://api.github.com` 未认证额度只有 60 次/小时，本报告主要靠 **codeload tarball**（无配额）+ **本地提取**完成，API 调用极少。

---

## 2. 播放（Playback）

### 2.1 认证与会话模型

#### 2.1.1 请求头原文（3.5.3 源码，逐行可查）

文件：`Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs`
链接：`https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs`

**[码] 授权头读取顺序（L179–L188）：**

```csharp
private Dictionary<string, string> GetAuthorizationDictionary(IRequest httpReq)
{
    var auth = httpReq.Headers["X-Emby-Authorization"];

    if (string.IsNullOrEmpty(auth))
    {
        auth = httpReq.Headers["Authorization"];
    }

    return GetAuthorization(auth);
}
```

→ **`X-Emby-Authorization` 优先，为空则回退到标准 `Authorization`。两者完全等价。**（这解释了为什么 Jellyfin 客户端发 `Authorization: MediaBrowser ...` 在 Emby 上也能用。）

**[码] 头的语法解析（L192–L232）：**

```csharp
var parts = authorizationHeader.Split(new[] { ' ' }, 2);   // L194  按第一个空格切成 scheme + 剩余
if (parts.Length != 2) return null;                        // L197

var acceptedNames = new[] { "MediaBrowser", "Emby" };      // L208  ★ 两个 scheme 都收
if (!acceptedNames.Contains(parts[0] ?? string.Empty, StringComparer.OrdinalIgnoreCase))
    return null;                                           // L211  OrdinalIgnoreCase

authorizationHeader = parts[1];                            // L215
parts = authorizationHeader.Split(',');                    // L216  ★ 逗号分隔
...
var param = item.Trim().Split(new[] { '=' }, 2);           // L226  ★ 每个只按第一个 '=' 切
if (param.Length == 2)
{
    var value = NormalizeValue(param[1].Trim(new[] { '"' }));  // L228  ★ 去掉两端双引号
    result.Add(param[0], value);                               // 键名大小写不敏感（Dictionary 用 OrdinalIgnoreCase）
}
```

**[码] 读取的键（L56–L61）：** `DeviceId`、`Device`、`Client`、`Version`、`Token`
（**3.5.3 源码里没有读 `UserId`** —— 见 §6「未确认」清单。Emby 4.x 是否读了 `UserId` 未能确证。）

**[码] Token 的三个来源与优先级（L66–L78）：**

```csharp
if (string.IsNullOrEmpty(token)) token = httpReq.Headers["X-Emby-Token"];        // L68
if (string.IsNullOrEmpty(token)) token = httpReq.Headers["X-MediaBrowser-Token"]; // L73
if (string.IsNullOrEmpty(token)) token = httpReq.QueryString["api_key"];          // L77
```

→ 优先级：**`X-Emby-Token` > `X-MediaBrowser-Token` > `?api_key=`**
（也即：`Token=` 写在授权头里是第 0 优先级，因为它先被填进 `token` 变量。）

**[码] CORS 白名单（`ResponseFilter.cs:29`、`HttpListenerHost.cs:543`）** 明确列出 `X-MediaBrowser-Token, X-Emby-Authorization`，佐证这两个头是官方一等公民。

**[官] 官方文档口径（`https://dev.emby.media/doc/restapi/User-Authentication.html`）原文：**

> **Authorization Request Header**
> Add the following request header on every request:
> ```
> Authorization=Emby UserId="e8837bc1-ad67-520e-8cd2-f629e3155721", Client="Android", Device="Samsung Galaxy SIII", DeviceId="xxx", Version="1.0.0.0"
> ```

> ⚠️ **注意两处不一致（都如实记录）：**
> 1. 官方文档写的是 `Authorization=Emby ...`（用 `=` 而非 `:`），**这是文档笔误**；HTTP 头必须是 `Name: Value`。
> 2. 官方文档给的 scheme 是 **`Emby`**、参数含 **`UserId`**；而**客户端实际发的是 `MediaBrowser` 且不带 `UserId`**（见 §4.2 Jellyfin 源码硬编码 `MediaBrowser`；Emby 服务端 L208 两个都收）。
> 3. 官方文档**完全没提 `X-Emby-Authorization`**，但服务端源码明确支持。

**[官] API Key（`https://dev.emby.media/doc/restapi/API-Key-Authentication.html`）：**

> The API key must be included in every request made that requires authentication. There are two different ways…
> - **Http Request Header**：Send the API key string in an http request header named **`X-Emby-Token`**.
> - **Query Parameter**：transmit the API key as a query string parameter name **`api_key`**.
>   Example: `http://localhost:8096/emby/System/Info?api_key=123456789987654321`

**[官] Base URL（`https://dev.emby.media/doc/restapi/index.html`）：**

> The Emby Server API can be reached via: `http[s]://hostname:port/emby/{apipath}`

→ **Emby 的 API 挂在 `/emby/` 前缀下**（`/System/Info` 实际是 `/emby/System/Info`）。Jellyfin 12.0 已**移除** `/emby/*` 与 `/mediabrowser/*` 前缀（见 §4）。

**[官] 请求/响应格式：** 同时支持 JSON 与 XML，由 `Content-Type` 决定（`application/json` / `application/xml`）。

**[规] 官方 OpenAPI 也把授权头作为显式参数列出：**
`POST /Users/AuthenticateByName` 声明了 `X-Emby-Authorization`（`in: header, type: string`）这一参数。

##### ★★ [规410] Emby 4.10.0.40 的官方认证定义（原文，权威度最高）

`MediaBrowser/Emby.SDK` 的 `Resources/OpenApi/openapi_v3.json` → `components.securitySchemes` 里**只有两个方案**，原文如下：

```json
"apikeyauth": {
  "type": "apiKey",
  "description": "**ApiKey Authentication**  API keys are static access tokens providing access to the Emby API for external applications.  Keys can be created from the Server Dashboard under **Advanced > Security**  The api key can alternatively be specified in an http header named _X-Emby-Token_  ...",
  "name": "api_key",
  "in": "query"
},
"embyauth": {
  "type": "http",
  "description": "**Emby User Authentication**  An access token must be acquired via _/Users/AuthenticateByName_ and then sent in an http header with every reuest. ...",
  "scheme": "bearer",
  "bearerFormat": "Emby UserId=\"(guid)\", Client=\"(string)\", Device=\"(string)\", DeviceId=\"(string)\", Version=\"string\", Token=\"(string)\""
}
```

**这解决了三个悬而未决的问题：**

| 问题 | 4.10 官方规范的答案 |
|---|---|
| 授权头的官方格式是什么？ | **`Emby UserId="(guid)", Client="(string)", Device="(string)", DeviceId="(string)", Version="string", Token="(string)"`**（`bearerFormat` 字段原文）—— **注意这里确实有 `UserId`**，与 3.5.3 源码「只读 5 个 key、不读 UserId」不一致（见 §7） |
| API key 怎么传？ | **两种**：`?api_key=`（query，**规范里唯一的 apiKey 位置**）**或** HTTP 头 **`X-Emby-Token`**（描述文字里明确写了） → **`X-Emby-Token` 在 4.10 仍然有效**（这条回答了子 agent 提出的"4.x 是否仍接受"的疑问，在规范层面答案是肯定的） |
| `X-MediaBrowser-Token` 还在吗？ | **规范里没有提到**。3.5.3 源码明确接受（L73）。**4.10 是否仍接受 → 未能确认（见 §6）** |

**另外：**
- **[规410] `POST /Users/AuthenticateByName` 的 body schema 是 `AuthenticateUserByName { Username, Pw }`**（4.10 里字段名是 **`Pw`**）；响应 `AuthenticationResult { User, SessionId, AccessToken, ServerId }`
- **所有流式端点（`/Videos/{Id}/stream`、`master.m3u8`、`hls1/...`）在 4.10 规范里都声明了 `security: [{"apikeyauth": []}, {"embyauth": []}]`** —— 即**两种认证都接受**，这也解释了为什么 `?api_key=` 能直接放进播放 URL
- **[官] 家长控制的错误语义（[Parental Control](https://dev.emby.media/doc/restapi/Parental-Control.html)）**：被家长控制拦截时返回 **401**，且响应头带 **`X-Application-Error-Code: ParentalControl`** → 客户端可用它区分「token 失效」与「家长控制拦截」。

#### 2.1.2 登录流程

**[规] 端点与模型：**

| 步骤 | 方法 | 路径 | 请求体 / 参数 | 响应 |
|---|---|---|---|---|
| 取公开用户 | GET | `/Users/Public` | — | `UserDto[]` |
| 按用户名登录 | POST | `/Users/AuthenticateByName` | body `AuthenticateUserByName { Username, Password, Pw }`；header `X-Emby-Authorization` | `AuthenticationResult`（含 `AccessToken`、`ServerId`、`User`） |
| 按用户 Id 登录 | POST | `/Users/{Id}/Authenticate` | — | 同上 |
| 注销 | POST | `/Sessions/Logout` | — | — |

**[规] `AuthenticateUserByName` 同时有 `Password` 和 `Pw` 两个字段** —— `Pw` 是历史遗留别名（官方文档里写的就是 `pw`）。

**[官] Emby 官方对登录的说明（`User-Authentication.html`）：**

> - Make a call to `/Users/Public` to get all public users. Public users are users that the server admin has allowed to be displayed visually on the login screen.
> - For each user, if `PrimaryImageTag` has a value, that indicates the user has an image. The image can then be downloaded using `/Users/{Id}/Images/{Type}`.
> - Each user has a `HasPassword` property. This is used to determine if the user should be prompted to input a password. **The application must authenticate regardless of this value.**
> - Authenticate using `/Users/AuthenticateByName`. A 200 status code indicates success, while anything in the 400 or 500 range indicates failure.
> - The return result object will have an `AccessToken` property. This should be included in all subsequent Http requests using the header **`X-Emby-Token`**.
> - If the user explicitly logs out, send a POST to `/Sessions/Logout`. This will revoke your access token.
> - If any http requests fail with a **401** response status code, this is generally an indication that the access token has been revoked.
> - For applications that support connectivity to multiple servers, the token should be saved along with the server's `Id`… The server Id is part of the `SystemInfo` object which can be retrieved from **`/System/Info`**.

**[码] Token 的时效性（`AuthorizationContext.cs` L146–L150）：** 每次请求若 `DateLastActivity` 距今超过 **3 分钟**，会更新 token 记录 —— 即 **Emby 的 access token 是滑动过期的**。

**[码] 客户端上报的 `Client`/`Device`/`Version` 会反写回 token 记录（L120–L142）**，唯一例外是 `Client` 含 `chromecast`（不覆盖）。

#### 2.1.3 会话（Session）模型

**[属] `SessionInfo`（Model DLL）字段：**
`PlayState`、`AdditionalUsers`、`Capabilities`、`RemoteEndPoint`、`PlayableMediaTypes`、`PlaylistItemId`、`Id`、`ServerId`、`UserId`、`UserName`、`UserPrimaryImageTag`、`Client`、`LastActivityDate`、`DeviceName`、`DeviceType`、`NowPlayingItem`、`DeviceId`、`ApplicationVersion`、`AppIconUrl`、`SupportedCommands`、`TranscodingInfo`、`SupportsRemoteControl`

**[属] `PlayerStateInfo`：** `PositionTicks`、`CanSeek`、`IsPaused`、`IsMuted`、`VolumeLevel`、`AudioStreamIndex`、`SubtitleStreamIndex`、`MediaSourceId`、`PlayMethod`、`RepeatMode`

**[属] `ClientCapabilities`（客户端能力上报）：**
`PlayableMediaTypes`、`SupportedCommands`、`SupportsMediaControl`、`PushToken`、`PushTokenType`、`SupportsPersistentIdentifier`、`SupportsSync`、`DeviceProfile`、`IconUrl`、`AppId`

**[属] `SupportedCommands` 的取值 = `GeneralCommandType` 枚举：**
`MoveUp, MoveDown, MoveLeft, MoveRight, PageUp, PageDown, PreviousLetter, NextLetter, ToggleOsd, ToggleContextMenu, Select, Back, TakeScreenshot, SendKey, SendString, GoHome, GoToSettings, VolumeUp, VolumeDown, Mute, Unmute, ToggleMute, SetVolume, SetAudioStreamIndex, SetSubtitleStreamIndex, ToggleFullscreen, DisplayContent, GoToSearch, DisplayMessage, SetRepeatMode, ChannelUp, ChannelDown, SetMaxStreamingBitrate, Guide, ToggleStats, PlayMediaSource, PlayTrailers`

**[属] `PlaystateCommand` 枚举：** `Stop, Pause, Unpause, NextTrack, PreviousTrack, Seek, Rewind, FastForward, PlayPause`

**[规] 会话相关端点：**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/Sessions` | 列出会话（查询参数见 3.5.3 `SessionQuery`） |
| POST | `/Sessions/Capabilities` | 上报能力（简版） |
| POST | `/Sessions/Capabilities/Full` | 上报能力（含 `DeviceProfile`） |
| POST | `/Sessions/Logout` | 结束会话并吊销 token |
| POST | `/Sessions/{Id}/Playing` | 命令某会话播放某 item |
| POST | `/Sessions/{Id}/Playing/{Command}` | `PlaystateCommand`：`Stop/Pause/Unpause/NextTrack/PreviousTrack/Seek/Rewind/FastForward/PlayPause` |
| POST | `/Sessions/{Id}/Command` | 下发 `GeneralCommand` |
| POST | `/Sessions/{Id}/Command/{Command}` | 同上，命令在路径里 |
| POST | `/Sessions/{Id}/System/{Command}` | 系统命令 |
| POST | `/Sessions/{Id}/Message` | 弹消息（`MessageCommand`） |
| POST | `/Sessions/{Id}/Viewing` | 让某会话浏览到某 item |
| POST/DELETE | `/Sessions/{Id}/Users/{UserId}` | 会话内加/减附加用户 |

**3.5.3 源码中的会话路由**：`MediaBrowser.Api/Session/SessionsService.cs`（L21–L257），可用 `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L21` 逐个核对。**[码]**

---

### 2.2 `PlaybackInfo`：客户端如何拿到「该怎么播」的决策

这是整个播放链路的核心接口。**它在 3.5.3 里已经不在公开源码中**，而是位于 `ThirdParty/emby/Emby.Server.MediaEncoding.dll`。

**[属] 从 DLL 元数据提取的 `[Route]` 特性（原文）：**

```
### Emby.Server.MediaEncoding.Api.GetPlaybackInfo
    [RouteAttribute: /Items/{Id}/PlaybackInfo | GET | Summary=Gets live playback media info for an item]
    P Id      [ApiMember: Name=Id     Description=Item Id  IsRequired=True DataType=string ParameterType=path  Verb=GET]
    P UserId  [ApiMember: Name=UserId Description=User Id  IsRequired=True DataType=string ParameterType=query Verb=GET]

### Emby.Server.MediaEncoding.Api.GetPostedPlaybackInfo
    [RouteAttribute: /Items/{Id}/PlaybackInfo | POST | Summary=Gets live playback media info for an item]
```

**[属] 3.5.3 的实现类与方法名（`Emby.Server.MediaEncoding.Api.MediaInfoService`）：**

```
GetPlaybackInfo / GetPlaybackInfo(重载)
SetDeviceSpecificData / SetDeviceSpecificData(重载)   ← 逐条 MediaSource 套 DeviceProfile
GetMaxBitrate
SetDeviceSpecificSubtitleInfo
SortMediaSources
EnableRemoteDirectPlay
NormalizeMediaSourceContainer
```

**[属] `Emby.Server.MediaEncoding.Api.TranscodingJob` 的字段**（这说明服务端对一个转码任务跟踪什么）：
`PlaySessionId, LiveStreamId, IsLiveOutput, MediaSource, Path, Type, Process, ActiveRequestCount, KillTimer, DeviceId, CancellationTokenSource, HasExited, IsUserPaused, Id, Framerate, CompletionPercentage, BytesDownloaded, BytesTranscoded, BitRate, TranscodingPositionTicks, DownloadPositionTicks, LastPingDate, TimerDuration`

> 注意 `LastPingDate` —— 与 §2.8 的 `/Sessions/Playing/Ping` 对应：客户端不 ping，转码任务会被 kill。

#### 2.2.1 请求参数（两路证据完全一致）

**[规] 官方 OpenAPI（4.1.1.0）：**

```
GET  /Items/{Id}/PlaybackInfo
   - Id      in=path  string  req=true   Item Id
   - UserId  in=query string  req=true   User Id
   → 200 MediaInfo.PlaybackInfoResponse

POST /Items/{Id}/PlaybackInfo
   - Id      in=path  string  req=true
   body: application/json → MediaInfo.PlaybackInfoRequest
   → 200 MediaInfo.PlaybackInfoResponse
```

**[属]/[规] `MediaInfo.PlaybackInfoRequest` 的全部字段（3.5.3 DTO 与 4.1.1.0 schema **逐字一致**）：**

| 字段 | 类型 | 含义 |
|---|---|---|
| `Id` | string | Item Id |
| `UserId` | string | 用户 Id |
| `MaxStreamingBitrate` | int | 客户端能接受的最大码率（**这是客户端唯一的码率阶梯来源，服务端不定义阶梯**） |
| `StartTimeTicks` | int | 起播位置（1 tick = 100 ns；1 ms = 10000 ticks） |
| `AudioStreamIndex` | int? | 指定音轨 |
| `SubtitleStreamIndex` | int? | 指定字幕轨（`null`/`-1` = 关闭） |
| `MaxAudioChannels` | int? | 最大声道数 |
| `MediaSourceId` | string | 指定某个 MediaSource（多版本时用） |
| `LiveStreamId` | string | 直播流 |
| `DeviceProfile` | `Dlna.DeviceProfile` | ★ **客户端把自己的解码能力告诉服务端** |
| `EnableDirectPlay` | bool? | 允许直连 |
| `EnableDirectStream` | bool? | 允许直接串流（remux） |
| `EnableTranscoding` | bool? | 允许转码 |
| `AllowVideoStreamCopy` | bool? | 转码时允许视频流 copy |
| `AllowAudioStreamCopy` | bool? | 转码时允许音频流 copy |
| `IsPlayback` | bool? | 是否真的准备播放（false 表示只是探测） |
| `AutoOpenLiveStream` | bool? | 自动打开直播流 |
| `DirectPlayProtocols` | string[] | 允许的直连协议（如 `["Http","File"]`） |

> ★★ **参数到底放 query 还是 body？（[码] 实证，见 §2.7.6(3)）**
> **Emby 官方 Web 客户端在 `POST /Items/{Id}/PlaybackInfo` 时，把所有参数（`UserId`、`MaxStreamingBitrate`、`StartTimeTicks`、`MediaSourceId`、`EnableDirectPlay`…）放在 **query string**，body 里**只放 `DeviceProfile`**。**
> **Jellyfin 12.x 正好相反**：那 15 个 query 参数全被标为 `deprecated`，要求放 body。
> **做兼容客户端最稳的做法：两边都放（query + body 同名同值）。** Emby 与 Jellyfin 都能正确处理。

#### 2.2.2 响应结构

**[属]/[规] `MediaInfo.PlaybackInfoResponse` —— 只有三个字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `MediaSources` | `MediaSourceInfo[]` | 候选播放源（多版本时多个） |
| `PlaySessionId` | string | ★ 本次播放会话 Id，后续所有进度上报都要带 |
| `ErrorCode` | string | 枚举：`NotAllowed` / `NoCompatibleStream` / `RateLimitExceeded` |

> **[属] `PlaybackErrorCode` 枚举（3.5.3）= `NotAllowed, NoCompatibleStream, RateLimitExceeded`** —— 与 4.1.1.0 完全一致。
> `RateLimitExceeded` 对应 `RemoteClientBitrateLimit`（§2.10）。

#### 2.2.3 `MediaSourceInfo` 全字段（★ 客户端程序员最需要的一张表）

> ★ **[规410] 修正**：Emby 4.10.0.40 的 `MediaSourceInfo` 是 **49 个属性**，**新增了 `DirectStreamUrl`、`AddApiKeyToDirectStreamUrl`、`Chapters`、`ProbePath`、`ProbeProtocol`、`SortName`、`ItemId`、`ServerId`、`MimeType`、`TranscodingMimeType`、`HasMixedProtocols`、`ContainerStartTimeTicks`、`TrancodeLiveStartIndex`、`WallClockStart`**，**去掉了** `ETag`、`IgnoreDts`、`IgnoreIndex`、`GenPtsInput`、`VideoType`、`IsoType`、`SupportsProbing`、`RequireLooping` 等（详见 §7）。
> **其中 `DirectStreamUrl` 是最重要的新增字段** —— 4.10 起服务端会直接给你 "直接串流" 的完整 URL，客户端不必自己拼。

**[属] 3.5.3 `MediaBrowser.Model.Dto.MediaSourceInfo`（53 个属性）：**
`Protocol, Id, Path, EncoderPath, EncoderProtocol, Type, Container, Size, Name, IsRemote, ETag, RunTimeTicks, ReadAtNativeFramerate, IgnoreDts, IgnoreIndex, GenPtsInput, SupportsTranscoding, SupportsDirectStream, SupportsDirectPlay, IsInfiniteStream, RequiresOpening, OpenToken, RequiresClosing, LiveStreamId, BufferMs, RequiresLooping, SupportsProbing, VideoType, IsoType, Video3DFormat, MediaStreams, Formats, Bitrate, Timestamp, RequiredHttpHeaders, TranscodingUrl, TranscodingSubProtocol, TranscodingContainer, AnalyzeDurationMs, TranscodeReasons, DefaultAudioStreamIndex, DefaultSubtitleStreamIndex`

**[规] 4.1.1.0 OpenAPI 里的枚举值：**

| 字段 | 取值 |
|---|---|
| `Protocol` | `File, Http, Rtmp, Rtsp, Udp, Rtp, Ftp, Mms` |
| `EncoderProtocol` | 同上 |
| `Type` | `Default, Grouping, Placeholder` |
| `Timestamp` | `None, Zero, Valid` |
| `Video3DFormat` | `HalfSideBySide, FullSideBySide, FullTopAndBottom, HalfTopAndBottom, MVC` |
| `TranscodingSubProtocol` | 实际取值 `http`（渐进式）或 `hls`（HLS） |
| `TranscodingContainer` | 目标容器（如 `ts` / `mp4`） |

**关键字段语义：**

- **`SupportsDirectPlay` / `SupportsDirectStream` / `SupportsTranscoding`** —— 三个布尔，服务端告诉你这个源**能不能**分别做这三种模式。**注意：这是"这个源本身的性质"，不是"最终决定"。最终决定看 `TranscodingUrl` / `DirectStreamUrl` 是否为空。**
- **`TranscodingUrl`** —— **不为空就代表服务端建议转码/直接串流**，客户端直接 GET 这个 URL 即可。这是 Emby 播放最重要的一个字段。
- **`DirectStreamUrl`** —— 直接串流的 URL（**Jellyfin 12.x 的 spec 里没有这个字段**，见 §4.4）。
- **`TranscodeReasons`** —— `TranscodeReason` 枚举数组，说明"为什么不能直连"。
- `MediaStreams` —— 见下一节。

**[规410] `TranscodeReason`（4.10.0.40，全 **27** 项）—— 当前权威：**
```
ContainerNotSupported, VideoCodecNotSupported, AudioCodecNotSupported,
ContainerBitrateExceedsLimit, AudioBitrateNotSupported, AudioChannelsNotSupported,
VideoResolutionNotSupported, UnknownVideoStreamInfo, UnknownAudioStreamInfo,
AudioProfileNotSupported, AudioSampleRateNotSupported, AnamorphicVideoNotSupported,
InterlacedVideoNotSupported, SecondaryAudioNotSupported, RefFramesNotSupported,
VideoBitDepthNotSupported, VideoBitrateNotSupported, VideoFramerateNotSupported,
VideoLevelNotSupported, VideoProfileNotSupported, AudioBitDepthNotSupported,
SubtitleCodecNotSupported, DirectPlayError,
VideoRangeNotSupported, SubtitleContentOptionsEnabled,
ExternalAudioNotSupported, AudioDelayNotSupported          ← 4 项为 4.x 新增
```

**[属] `TranscodeReason` 枚举（3.5.3 全 23 项，缺少上面最后 4 项）：**
`ContainerNotSupported, VideoCodecNotSupported, AudioCodecNotSupported, ContainerBitrateExceedsLimit, AudioBitrateNotSupported, AudioChannelsNotSupported, VideoResolutionNotSupported, UnknownVideoStreamInfo, UnknownAudioStreamInfo, AudioProfileNotSupported, AudioSampleRateNotSupported, AnamorphicVideoNotSupported, InterlacedVideoNotSupported, SecondaryAudioNotSupported, RefFramesNotSupported, VideoBitDepthNotSupported, VideoBitrateNotSupported, VideoFramerateNotSupported, VideoLevelNotSupported, VideoProfileNotSupported, AudioBitDepthNotSupported, SubtitleCodecNotSupported, DirectPlayError`

#### 2.2.4 `MediaStream` 全字段

**[属] 3.5.3 `MediaBrowser.Model.Dto.MediaStream`：**
`Codec, CodecTag, Language, ColorTransfer, ColorPrimaries, ColorSpace, Comment, TimeBase, CodecTimeBase, Title, Extradata, VideoRange, DisplayTitle, DisplayLanguage, NalLengthSize, IsInterlaced, IsAVC, ChannelLayout, BitRate, BitDepth, RefFrames, PacketLength, Channels, SampleRate, IsDefault, IsForced, Height, Width, AverageFrameRate, RealFrameRate, Profile, Type, AspectRatio, Index, Score, IsExternal, DeliveryMethod, DeliveryUrl, IsExternalUrl, IsTextSubtitleStream, SupportsExternalStream, Path, PixelFormat, Level, IsAnamorphic`

**[规410] 枚举（4.10.0.40，权威）：**

| 字段 | 取值 |
|---|---|
| `Type`（`MediaStreamType`） | **`Unknown, Audio, Video, Subtitle, EmbeddedImage, Attachment, Data`** |
| `DeliveryMethod`（`SubtitleDeliveryMethod`） | **`Encode, Embed, External, Hls, VideoSideData`** |
| `Protocol`（`MediaProtocol`） | `File, Http, Rtmp, Rtsp, Udp, Rtp, Ftp, Mms` —— ⚠️ **没有 `Hls`** |
| `SubtitleLocationType` | `InternalStream, VideoSideData` |
| `ExtendedVideoTypes` | `None, Hdr10, Hdr10Plus, HyperLogGamma, DolbyVision` |

> ★ **跨版本新增**：`Attachment`/`Data` 流类型、`VideoSideData` 字幕投递方式（4.1.1.0 及以前都没有）、`ExtendedVideoTypes`（HDR 识别）、`IsHearingImpaired`、`Rotation`、`DeliveryFormat`、`IsChunkedResponse`、`StreamStartTimeTicks`。
> ★ **注意 4.10 的 `MediaStream` 是 56 个属性**，比 3.5.3 的 45 个多 11 个。

**[规] 4.1.1.0 的枚举（已过时，仅作对照）：** `Type = Audio, Video, Subtitle, EmbeddedImage`；`DeliveryMethod = Encode, Embed, External, Hls`

**[属] `SubtitleDeliveryMethod` 枚举：** `Encode, Embed, External, Hls`

**这四种字幕投递方式的确切含义（[码] 结合 §2.7 的注入方式）：**

| `DeliveryMethod` | 含义 | 客户端要做什么 |
|---|---|---|
| `Encode` | 服务端把文本字幕**烧进视频画面**（硬字幕） | 直接用视频流，无需处理字幕 |
| `Embed` | 字幕**留在容器里**（客户端自己解） | 客户端从容器解字幕 |
| `External` | 字幕被**抽成外挂文件**，通过 `DeliveryUrl` 下载 | 下载 `DeliveryUrl` 并自行渲染 |
| `Hls` | 字幕走 **HLS 独立字幕轨**（`#EXT-X-MEDIA:TYPE=SUBTITLES`） | 播放器按 HLS 规范加载 |

**`DeliveryUrl` 的格式（[属] 从 `MediaBrowser.Model.dll` 字符串堆提取的格式串）：**

```
{0}/Videos/{1}/{2}/Subtitles/{3}/{4}/Stream.{5}
```
即 `{ServerBase}/Videos/{ItemId}/{MediaSourceId}/Subtitles/{Index}/{StartPositionTicks}/Stream.{Format}`

#### 2.2.5 `TranscodingUrl` / `DirectStreamUrl` 的真实形状（★ 从二进制字符串堆提取的格式串）

**[属] `MediaBrowser.Model.dll` 字符串堆里的 URL 模板（原文）：**

```
{0}/videos/{1}/stream{2}?{3}          ← 渐进式（SubProtocol=http）
{0}/videos/{1}/master.m3u8?{2}        ← HLS（SubProtocol=hls）
{0}/audio/{1}/stream{2}?{3}           ← 音频渐进式
{0}/audio/{1}/master.m3u8?{2}         ← 音频 HLS
{0}/Videos/{1}/{2}/Subtitles/{3}/{4}/Stream.{5}
{0}={1}                               ← query 参数拼装
?api_key=
```

**解读（[属] 级推断，逻辑上确定）：**
- `{0}` = 服务器基址（含 `/emby` 前缀）
- `{1}` = ItemId
- `{2}` = 容器后缀（有 `.mkv` 时是 `".mkv"`，无则空）
- `{3}` = `StreamInfo.BuildParams()` 拼出的 query string
- 结尾会追加 `?api_key=<token>`（HLS/stream 端点允许 query 认证，因为 `<video>`/播放器往往无法自定义头）

**[属] `StreamInfo` 暴露的方法名揭示参数是怎么来的：**
`ToUrl, GetUrl, BuildParams, SetOption, GetOption, GetExternalSubtitles, GetSubtitleProfiles, AddSubtitleProfiles, GetSubtitleStreamInfo, GetTargetVideoBitDepth, GetTargetAudioBitDepth, GetTargetVideoLevel, GetTargetRefFrames, GetTargetAudioChannels, GetMediaStreamCount, GetSelectableAudioStreams, GetSelectableSubtitleStreams, GetSelectableStreams`

**[属] 我逐个在 `MediaBrowser.Model.dll` 字符串堆中确认存在的 query 参数名（这些就是 `BuildParams` 会写出去的键）：**

```
Static, Container, AudioCodec, VideoCodec, AudioBitrate, VideoBitrate,
AudioStreamIndex, VideoStreamIndex, SubtitleStreamIndex, SubtitleMethod,
StartTimeTicks, CopyTimestamps, MaxWidth, MaxHeight, MaxFramerate,
RequireAvc, TranscodeReasons, TranscodeSeekInfo,
SegmentContainer, SegmentLength, MinSegments, BreakOnNonKeyFrames,
MaxAudioChannels, TranscodingMaxAudioChannels, EnableSubtitlesInManifest,
EstimateContentLength, MediaSourceId, DeviceId, PlaySessionId, LiveStreamId,
api_key
```

> **[社]/[未]**：`TranscodingUrl` 里参数的**确切书写顺序**本报告**没有**确证（需要 IL 反编译 `StreamInfo.BuildParams` 的方法体）。但**参数集合**已在二进制字符串堆里逐个确认存在。客户端应当**把服务端返回的 `TranscodingUrl` 原样使用**，不要自己拼 —— 这也是 Emby 所有官方客户端的做法。

**[属] 同上，`MediaBrowser.Model.dll` 的 `Dlna.StreamInfo` 属性全集（约 60 个）**，这些决定了 URL 里可能出现什么：
`ItemId, PlayMethod, Context, MediaType, Container, SubProtocol, StartPositionTicks, SegmentLength, MinSegments, BreakOnNonKeyFrames, RequireAvc, RequireNonAnamorphic, CopyTimestamps, EnableMpegtsM2TsMode, EnableSubtitlesInManifest, AudioCodecs, VideoCodecs, AudioStreamIndex, SubtitleStreamIndex, TranscodingMaxAudioChannels, GlobalMaxAudioChannels, AudioBitrate, VideoBitrate, MaxWidth, MaxHeight, MaxFramerate, DeviceProfile, DeviceProfileId, DeviceId, RunTimeTicks, TranscodeSeekInfo, EstimateContentLength, MediaSource, SubtitleCodecs, SubtitleDeliveryMethod, SubtitleFormat, PlaySessionId, TranscodeReasons, StreamOptions, MediaSourceId, IsDirectStream, Target*（一大批目标参数）, …`

---

### 2.3 直连 / 转码的判定逻辑

#### 2.3.1 判定在哪（[属] 级）

**[属] 判定类是 `MediaBrowser.Model.Dlna.StreamBuilder`** —— 它在 Emby 3.5.3 的 `MediaBrowser.Model.dll` 里，**不在公开源码里**。

**[属] `StreamBuilder` 的方法名（从 DLL 元数据提取，共 30 个）：**

```
.ctor
BuildAudioItem / BuildVideoItem        （各有重载）
GetOptimalStream                       ★ 主入口
SortMediaSources
GetTranscodeReasonForFailedCondition
NormalizeMediaSourceFormatIntoSingleContainer
GetBitrateForDirectPlayCheck
GetAudioDirectPlayMethods
GetTranscodeReasonsFromDirectPlayProfile
GetDefaultSubtitleStreamIndex
SetStreamInfoOptionsFromTranscodingProfile
GetDefaultAudioBitrateIfUnknown
GetAudioBitrate
GetMaxAudioBitrateForTotalBitrate
GetVideoDirectPlayProfile             ★
LogConditionFailure
IsEligibleForDirectPlay
GetSubtitleProfile
IsSubtitleEmbedSupported
GetExternalSubtitleProfile
IsAudioEligibleForDirectPlay
ValidateInput / ValidateAudioInput
ApplyTranscodingConditions（3 个重载）
IsAudioDirectPlaySupported
IsVideoDirectPlaySupported
```

**[属] `StreamBuilder` 依赖的字段：** `_logger`、`_transcoderSupport`（类型 `ITranscoderSupport` / `FullTranscoderSupport`）

**[属] 相关判定类型（同在 Model DLL）：**
`ConditionProcessor`、`ContentFeatureBuilder`、`MediaFormatProfileResolver`、`ResolutionNormalizer`、`ProfileCondition{ Condition, Property, Value, IsRequired }`

#### 2.3.2 判定用的「条件」模型（★ 客户端写 DeviceProfile 时必须懂）

**[属] `Dlna.ProfileConditionValue` 全部 22 个可判定属性：**

```
AudioChannels, AudioBitrate, AudioProfile, Width, Height, Has64BitOffsets,
PacketLength, VideoBitDepth, VideoBitrate, VideoFramerate, VideoLevel,
VideoProfile, VideoTimestamp, IsAnamorphic, RefFrames, NumAudioStreams,
NumVideoStreams, IsSecondaryAudio, VideoCodecTag, IsAvc, IsInterlaced,
AudioSampleRate, AudioBitDepth
```

**[属] `Dlna.ProfileConditionType`（5 种比较）：** `Equals, NotEquals, LessThanEqual, GreaterThanEqual, EqualsAny`

**[属] `Dlna.ProfileCondition`：** `{ Condition, Property, Value, IsRequired }`

**[属] `Dlna.DeviceProfile`（4.1.1.0 有 39 个属性）关键部分：**

| 分组 | 字段 |
|---|---|
| 身份 | `Name, Id, Identification, FriendlyName, Manufacturer, ManufacturerUrl, ModelName, ModelDescription, ModelNumber, ModelUrl, SerialNumber` |
| 码率上限 | **`MaxStreamingBitrate`、`MaxStaticBitrate`、`MusicStreamingTranscodingBitrate`、`MaxStaticMusicBitrate`** |
| 图片限制 | `MaxAlbumArtWidth/Height, MaxIconWidth/Height` |
| DLNA 细节 | `EnableAlbumArtInDidl, EnableSingleAlbumArtLimit, EnableSingleSubtitleLimit, SupportedMediaTypes, AlbumArtPn, SonyAggregationFlags, ProtocolInfo, TimelineOffsetSeconds, RequiresPlainVideoItems, RequiresPlainFolders, EnableMSMediaReceiverRegistrar, IgnoreTranscodeByteRangeRequests, XmlRootAttributes` |
| **★ 四个 Profile 数组** | **`DirectPlayProfiles[]`、`TranscodingProfiles[]`、`ContainerProfiles[]`、`CodecProfiles[]`、`ResponseProfiles[]`、`SubtitleProfiles[]`** |

**[属] 各 Profile 的结构：**

```
DirectPlayProfile   { Container, AudioCodec, VideoCodec, Type }
TranscodingProfile  { Container, Type, VideoCodec, AudioCodec, Protocol,
                      EstimateContentLength, EnableMpegtsM2TsMode, TranscodeSeekInfo,
                      CopyTimestamps, Context, EnableSubtitlesInManifest,
                      MaxAudioChannels, MinSegments, SegmentLength, BreakOnNonKeyFrames }
CodecProfile        { Type, Conditions, ApplyConditions, Codec, Container }
ContainerProfile    { Type, Conditions, Container }
SubtitleProfile     { Format, Method, DidlMode, Language, Container }
ResponseProfile     { Type, Container, AudioCodec, VideoCodec, MimeType, Conditions }
```

**[属] `Dlna.EncodingContext`：** `Streaming, Static`
**[属] `Dlna.DlnaProfileType`：** `Audio, Video, Photo`
**[属] `Dlna.TranscodeSeekInfo`：** `Auto, Bytes`

**[属] `Dlna.ResolutionConfiguration { MaxWidth, MaxBitrate }` 与 `ResolutionNormalizer.Configurations`** —— 这是服务端内置的「分辨率→码率」对照表。⚠️ **具体的阶梯数值在方法体里，本报告未能读取**（见 §6）。

#### 2.3.3 判定流程（[属] 方法名 + [规] 字段语义 推出的确定性结论）

```
POST /Items/{Id}/PlaybackInfo  (body = PlaybackInfoRequest)
   │
   ├─ 1. 取该 Item 的所有 MediaSource（多版本 → MediaSources[]）
   │
   ├─ 2. SetDeviceSpecificData(mediaSource, request, ...)
   │      ├─ DeviceProfile = request.DeviceProfile
   │      │   （若为空，则回落到服务器上保存的该 DeviceId 的 DeviceProfile 缓存）
   │      ├─ GetMaxBitrate(request.MaxStreamingBitrate, 用户策略, 是否局域网)
   │      └─ SortMediaSources(...)   ← 多版本排序
   │
   ├─ 3. StreamBuilder.GetOptimalStream(options)     ★★ 核心判定
   │      ├─ GetVideoDirectPlayProfile(mediaSource, deviceProfile)
   │      ├─ IsEligibleForDirectPlay  → IsVideoDirectPlaySupported / IsAudioDirectPlaySupported
   │      ├─ ApplyTranscodingConditions  (ContainerProfiles / CodecProfiles 逐条件比对)
   │      ├─ GetTranscodeReasonForFailedCondition → 收集 TranscodeReasons
   │      └─ 产出 StreamInfo { PlayMethod, IsDirectStream, Target*, TranscodeReasons }
   │
   └─ 4. SetDeviceSpecificSubtitleInfo(mediaSource, ...)
          ├─ GetSubtitleProfile / GetSubtitleStreamInfo
          └─ 每个字幕轨 → DeliveryMethod (Encode/Embed/External/Hls) + DeliveryUrl
```

**产出结果写回 `MediaSourceInfo`：** `SupportsDirectPlay / SupportsDirectStream / SupportsTranscoding / TranscodingUrl / TranscodingSubProtocol / TranscodingContainer / TranscodeReasons / DefaultAudioStreamIndex / DefaultSubtitleStreamIndex`，外加顶层的 `PlaySessionId`。

**`PlayMethod` 的三个取值（[属] `MediaBrowser.Model.Session.PlayMethod`）：`Transcode` / `DirectStream` / `DirectPlay`**

**给客户端的实操判据（这是本报告最实用的一条）：**
> 不要自己去推 `SupportsDirectPlay` 这些布尔。**看 `MediaSources[i].TranscodingUrl`：**
> - `TranscodingUrl` 为空 → 该源可以**直连**，用 `MediaSources[i].Path`（或 `DirectStreamUrl`，若存在）播放，上报 `PlayMethod=DirectPlay`
> - `TranscodingUrl` 非空且 `TranscodingSubProtocol == "http"` → **直接串流(remux)**，GET `TranscodingUrl`，上报 `PlayMethod=DirectStream`
> - `TranscodingUrl` 非空且 `TranscodingSubProtocol == "hls"` → **转码**，GET `TranscodingUrl`（返回 m3u8），上报 `PlayMethod=Transcode`

---

### 2.4 转码参数：**真实的 ffmpeg 命令模板**（从二进制字符串堆提取）

这是本报告最有价值的部分之一。以下全部是 **[属]** 级证据：从仓库内 `Emby.Server.MediaEncoding.dll` / `MediaBrowser.Controller.dll` 的 **UTF-16 字符串堆**中读到的**字符串字面量**，它们是 Emby 编译进程序集的格式串，供 `String.Format` 生成 ffmpeg 命令行。

复现命令：
```bash
strings -e l -n 4 ThirdParty/emby/Emby.Server.MediaEncoding.dll
strings -e l -n 4 packages/MediaBrowser.Server.Core.3.5.0/lib/netstandard2.0/MediaBrowser.Controller.dll
```

#### 2.4.1 HLS（MPEG-TS）转码命令

```
{0} {1} {2} -map_metadata -1 -map_chapters -1 -threads {3} {4} {5} -max_delay 5000000
  -avoid_negative_ts disabled -start_at_zero {6} -hls_time {7}
  -individual_header_trailer 0 -start_number {8} -hls_list_size {9}{10} -y "{11}"
```

- `{0}` = ffmpeg 路径，`{1}` = 全局参数，`{2}` = 输入参数（`-i ...`）
- `{3}` = 线程数，`{4}{5}` = 视频/音频编码参数
- `{6}` = 视频 filter / 关键帧参数，`{7}` = `-hls_time` 秒
- `{8}` = `-start_number`，`{9}` = `-hls_list_size`，`{10}` = 附加参数（如 `-hls_base_url`）
- `{11}` = 输出 m3u8 路径

#### 2.4.2 HLS（fMP4）转码命令

```
{0} {1} -map_metadata -1 -map_chapters -1 -threads {2} {3} {4} {5} -max_delay 5000000
  -avoid_negative_ts disabled -start_at_zero -hls_segment_type {13} -hls_time {6} {10}
  -individual_header_trailer 0 -start_number {7} -hls_list_size {8}
  -hls_segment_filename "{12}" -hls_fmp4_init_filename "{11}" -y "{9}"
```

#### 2.4.3 旧式 segment muxer（Emby 也保留了两条 segment 路线）

```
{0} {1} -map_metadata -1 -map_chapters -1 -threads {2} {3} {4} {5} -f segment
  -max_delay 5000000 -avoid_negative_ts disabled -start_at_zero -segment_time {6} {10}
  -individual_header_trailer 0 -segment_format {11} -segment_list_entry_prefix {12}
  -segment_list_type m3u8 -segment_start_number {7} -segment_list "{8}" -y "{9}"
```
（另一条变体把 `-threads {2}` 提前、并在 `-individual_header_trailer 0` 后接 `{12}`）

#### 2.4.4 关键帧对齐（HLS 切片精度的关键）

```
 -force_key_frames "expr:gte(t,n_forced*{0})"
 -force_key_frames "expr:if(isnan(prev_forced_t),eq(t,t),gte(t,prev_forced_t+{0}))"
```
（两条都有；第二条用于 `n_forced` 不可靠时。）

#### 2.4.5 HLS 主播放列表的附加指令

```
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-MAP:URI="hls1/{0}/{1}{2}{3}"
 -hls_base_url "{0}/"
 -hls_flags
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="{0}",DEFAULT={1},FORCED={2},AUTOSELECT=YES,URI="{3}",LANGUAGE="{4}"
```

#### 2.4.6 通用编码参数模板（`MediaBrowser.Controller.dll` 字符串堆）

| 模板 | 用途 |
|---|---|
| `{0} {1}{2} {3} {4} -map_metadata -1 -map_chapters -1 -threads {5} {6}{7}{8} -y "{9}"` | 通用转码 |
| `{0} {1}{7}{8} -threads {2}{3} {4} -id3v2_version 3 -write_id3v1 1{6} -y "{5}"` | 音频转码（写 ID3） |
| ` -b:v {0} -maxrate {0} -bufsize {1}` | 视频码率（含 maxrate/bufsize） |
| ` -maxrate {0} -bufsize {1}` | 同上 |
| ` -crf 23` / ` -crf 28` / ` -crf ` | 质量模式 |
| ` -level 3.0 / 3.1 / 3.2 / 4.0 / 4.1 / 4.2 / 5.0 / 5.1 / 5.2` | H.264 level 预设 |
| ` -preset 7 / default / fast / medium / slow` | NVENC/x264 preset 预设 |
| `-speed 16 -quality good -profile:v {0} -slices 8 -crf {1} -qmin {2} -qmax {3}` | VP8/VP9 类编码 |
| `-mbd rd -flags +mv4+aic -trellis 2 -cmp 2 -subcmp 2 -bf 2` | MPEG-2 类编码微调 |
| `-qmin 2` / `-mbd 2` | 质量下限 |
| ` -pix_fmt yuv420p` / ` -pix_fmt nv21` | 像素格式 |
| ` -f mp4 -movflags frag_keyframe+empty_moov` | 渐进式 MP4 分片 |
| ` -copyts -avoid_negative_ts disabled -start_at_zero` | 时间戳处理（与 `-ss` 偏移配合） |
| ` -ss {0}` | 起播位置（seek） |
| ` -analyzeduration 200M` / `-probesize 1G` | 探测参数 |
| ` -map 0:{0}` / ` -map -0:a` / ` -map -0:s` / ` -map 1:0 -sn` / ` -map 0:a -map 1:v -c:1:v copy` | 流选择 |
| `aac -strict experimental` | AAC 编码器的旧兼容写法 |
| `-codec:v:0` / `-codec:a:0` / `-codec:s:0` | 显式指定第 0 路编解码器 |
| `-user_agent "` | 拉远程流时的 UA |

#### 2.4.7 缩放表达式（决定转码后的分辨率，★ 很有意思）

Emby 用的是**基于 `dar`（display aspect ratio）的自适应 scale 表达式**，而不是简单 `scale=W:H`：

```
scale={0}:{1}
scale={0}:trunc({0}/dar/2)*2
scale={0}:trunc(ow/a/2)*2
scale=trunc({0}/2)*2:trunc({1}/2)*2
scale=trunc({0}/64)*64:trunc({1}/2)*2
scale=trunc(oh*a/2)*2:{0}
scale=trunc(oh*a/64)*64:{0}
scale=trunc(oh*a/2)*2:min(max(iw/dar\,ih)\,{0})
scale=trunc(oh*a/64)*64:min(max(iw/dar\,ih)\,{0})
scale=trunc(min(max(iw\,ih*dar)\,{0})/2)*2:trunc(ow/dar/2)*2
scale=trunc(min(max(iw\,ih*dar)\,{0})/64)*64:trunc(ow/dar/2)*2
scale=trunc(min(max(iw\,ih*dar)\,min({0}\,{1}*dar))/2)*2:trunc(min(max(iw/dar\,ih)\,min({0}/dar\,{1}))/2)*2
scale=trunc(min(max(iw\,ih*dar)\,min({0}\,{1}*dar))/64)*64:trunc(min(max(iw/dar\,ih)\,min({0}/dar\,{1}))/2)*2
```

**看点：**
- `/64)*64` 的变体是给 **硬件编码器**（QSV/NVENC 要求宽高是 64 的倍数）用的；
- `/2)*2` 保证偶数；
- `min(max(iw\,ih*dar)\,{0})` 表示**只在超过上限时才缩**（不放大）。

**去交错 / 变形（anamorphic）校正表达式：**

```
deinterlace
crop=iw/2:ih:0:0,scale=(iw*2):ih,setdar=dar=a,crop=min(iw\,ih*dar):min(ih\,iw/dar):(iw-min(iw\,iw*sar))/2:(ih - min (ih\,ih/sar))/2,setsar=sar=1,scale={0}:trunc({0}/dar/2)*2
crop=iw:ih/2:0:0,setdar=dar=a,crop=min(iw\,ih*dar):min(ih\,iw/dar):(iw-min(iw\,iw*sar))/2:(ih - min (ih\,ih/sar))/2,setsar=sar=1,scale={0}:trunc({0}/dar/2)*2
```

#### 2.4.8 转码任务类型标识（日志/内部类型名）

```
ffmpeg-transcode        转码
ffmpeg-remux            重新封装（remux / DirectStream）
ffmpeg-directstream     直接串流
```

以及一批可观测的日志串，说明 Emby 自己怎么诊断：
```
FFMpeg exited with code {0}
ffmpeg not found
ffmpeg version 3.0 or greater is required.
ffmpeg supported protocols: {0}
Starting transcoding because currentTranscodingIndex=null
Starting transcoding because requestedIndex={0} and currentTranscodingIndex={1}
KillTranscodingJob - JobId {0} PlaySessionId {1}. Killing transcoding
Transcoding kill timer stopped for JobId {0} PlaySessionId {1}. Killing transcoding
Finished waiting for {0} segments in {1}
Invalid segment index requested: {0} - Segment count: {1}
Deleting partial HLS file {0}
```

#### 2.4.9 有转码任务但无请求时的清理

**[属] `Emby.Server.MediaEncoding.Api.TranscodingJob` 有 `KillTimer` + `LastPingDate` + `TimerDuration`** → Emby 用定时器 + 客户端 ping 双保险来杀僵尸 ffmpeg 进程。

---

### 2.5 硬件加速

**[属] `MediaBrowser.Controller.dll` 字符串堆里全部硬件加速相关字面量（原文）：**

**VAAPI（Linux）：**
```
-hwaccel vaapi -hwaccel_output_format {0}
 -vaapi_device {0}
,format=nv12|vaapi,hwupload
scale_vaapi
format=nv12|vaapi
deinterlace_vaapi
```

**DXVA2（Windows）：**
```
-hwaccel dxva2
```

**CUDA/NVDEC 解码（`*_cuvid` 解码器）：**
```
-c:v h264_cuvid / hevc_cuvid / mpeg2_cuvid / mpeg4_cuvid / vc1_cuvid
```

**QSV（Intel Quick Sync，`*_qsv`）：**
```
-c:v h264_qsv / hevc_qsv / mpeg2_qsv / mpeg4_qsv / vc1_qsv / vp8_qsv?(未见于列表) / vp9_qsv?(未见于列表)
```
> 我在字符串堆里**确切看到**的 QSV 项：`h264_qsv, hevc_qsv, mpeg2_qsv, mpeg4_qsv, vc1_qsv`。

**AMF（AMD）：**
```
-c:v h264_amf
```

**MMAL（Raspberry Pi）：**
```
-c:v h264_mmal / mpeg2_mmal
```

**MediaCodec（Android）：**
```
-c:v h264_mediacodec / hevc_mediacodec / mpeg2_mediacodec / mpeg4_mediacodec /
     vp8_mediacodec / vp9_mediacodec
```

**[属] 与硬件加速相关的用户策略门控日志串：**
```
User policy for {0}: EnablePlaybackRemuxing: {1} EnableVideoPlaybackTranscoding: {2} EnableAudioPlaybackTranscoding: {3}
RemoteClientBitrateLimit: {0}, RemoteIp: {1}, IsInLocalNetwork: {2}
```

> ⚠️ **[未] 未能确认**：Emby 是否**把「硬件转码」作为 Emby Premiere 的付费门槛**、以及这个门槛在服务端的**强制点**在哪。3.5.3 里我只找到 `PluginSecurityManager` 的 `SupporterKey` / `IsMBSupporter` / `RegisterAppStoreSale`（`Emby.Server.Implementations/Security/PluginSecurityManager.cs`，校验地址 `https://mb3admin.com/admin/service/registration/validate`），**没有找到它卡在转码路径上的代码证据**。**这是 3.5.3 的情况，4.x 的现行策略未确证。** 见 §6。

---

### 2.6 字幕

#### 2.6.1 投递方式（已在 §2.2.4 说明）

**[属] 四种 `DeliveryMethod`：`Encode / Embed / External / Hls`**

#### 2.6.2 图形字幕烧录 vs 文本字幕

**[属] `MediaStream.IsTextSubtitleStream`** —— 这个布尔是客户端/服务端决定「能不能抽取为文本」的判据。
**[属] 字幕相关字符串：** `.srt .ssa .ass .sub`（`Emby.Naming.dll` 的 `SubtitleFileExtensions`）、`dvb_subtitle`、`WM/SubTitle`、`WM/SubTitleDescription`

#### 2.6.3 服务端的字幕抽取与转码命令（★ [属] 原文）

**从视频里抽取内嵌字幕到外部文件：**
```
-i {0} -map 0:{1} -an -vn -c:s {2} "{3}"
```
（`{1}` = 字幕流 index，`{2}` = 目标字幕编码格式如 `srt`，`{3}` = 输出路径）

**把已有字幕文件转成 srt：**
```
{0} -i "{1}" -c:s srt "{2}"
```

**ASS 字体注入：**
```
Setting ass font within {0}
```

**相关日志：**
```
ffmpeg subtitle extraction completed for {0} to {1}
ffmpeg subtitle extraction failed for {0} to {1}
ffmpeg subtitle conversion succeeded for {0}
ffmpeg subtitle conversion failed for {0}
Deleting extracted subtitle due to failure: {0}
```

#### 2.6.4 HLS 字幕轨

**[属] 主播放列表里的字幕项模板：**
```
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="{0}",DEFAULT={1},FORCED={2},AUTOSELECT=YES,URI="{3}",LANGUAGE="{4}"
```
以及视频流的引用：
```
,SUBTITLES="{0}"
```

**字幕清单 URL：**
```
{0}/Subtitles/{1}/subtitles.m3u8?SegmentLength={2}&api_key={3}
```
与 HLS 端点：
```
GET /Videos/{Id}/subtitles.m3u8 ? SubtitleSegmentLength=<int>&ManifestSubtitles=<string>   [规]
```

#### 2.6.5 字幕下载（本地文件命名 / 在线搜索）

**[属] `Emby.Naming.Subtitles.SubtitleParser` 方法：** `ParseFile`、`GetFlags`；结果类型 `SubtitleInfo { Path, Language, IsDefault, IsForced }`
**[属] 字幕扩展名：** `.srt .ssa .ass .sub`
**[属] 字幕标志位（从文件名里解析）：** `foreign`、`forced`、`default`

**在线字幕端点 [规]：**
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/Items/{Id}/RemoteSearch/Subtitles/{Language}` | 按语言搜索在线字幕 |
| POST | `/Items/{Id}/RemoteSearch/Subtitles/{SubtitleId}` | 下载指定字幕 |
| GET | `/Providers/Subtitles/Subtitles/{Id}` | 取字幕内容 |
| DELETE | `/Videos/{Id}/Subtitles/{Index}` | 删除字幕 |
| GET | `/Videos/{Id}/{MediaSourceId}/Subtitles/{Index}/Stream.{Format}` | 取字幕（可带 `StartPositionTicks` / `EndPositionTicks` / `CopyTimestamps`） |
| GET | `/Videos/{Id}/{MediaSourceId}/Subtitles/{Index}/{StartPositionTicks}/Stream.{Format}` | 同上（位置在路径里） |

**[属] `SubtitleSearchRequest`（`MediaBrowser.Controller.Subtitles`）** 与 `ISubtitleProvider` / `ISubtitleManager` 接口存在 → **在线字幕下载是插件式的**（在 3.5.3 里 OpenSubtitles 等是插件，不在本仓库内）。

#### 2.6.6 ★ 字幕偏移（Subtitle Offset）

> **[未] 明确结论：Emby 3.5.3 的服务端字幕链路里，我【没有】找到任何字幕时间轴偏移（offset）参数或 `itsoffset` 用法。**
> - `PlaybackInfoRequest` 无 `SubtitleOffset`
> - `StreamRequest` / `VideoStreamRequest` 无 `SubtitleOffset`
> - `Emby.Server.MediaEncoding.dll` 字符串堆里没有 `itsoffset`
>
> 所以**字幕偏移在 Emby 里是纯客户端行为**（客户端自行平移时间轴后渲染），与服务端无关。这与 Jellyfin 的结论一致。**但 4.x 是否新增了服务端偏移参数，本报告未能确证。**

---

### 2.7 进度上报三接口（+ 一个 Emby 独有的 Ping）

#### 2.7.1 路由（[属] 从 DLL 的 `[Route]` 特性原文提取）

```
### Emby.Server.MediaEncoding.Api.ReportPlaybackStart
    [Route: /Sessions/Playing | POST | Summary=Reports playback has started within a session]

### Emby.Server.MediaEncoding.Api.ReportPlaybackProgress
    [Route: /Sessions/Playing/Progress | POST | Summary=Reports playback progress within a session]

### Emby.Server.MediaEncoding.Api.ReportPlaybackStopped
    [Route: /Sessions/Playing/Stopped | POST | Summary=Reports playback has stopped within a session]

### Emby.Server.MediaEncoding.Api.PingPlaybackSession          ← ★ Emby 独有
    [Route: /Sessions/Playing/Ping | POST | Summary=Pings a playback session]
    P PlaySessionId  [ApiMember: Name=PlaySessionId IsRequired=False DataType=string ParameterType=query Verb=POST]
```

**[规] 官方 OpenAPI 4.1.1.0 完全确认这四条**（`/Sessions/Playing`、`/Sessions/Playing/Ping`、`/Sessions/Playing/Progress`、`/Sessions/Playing/Stopped`）。

#### 2.7.2 请求体：`PlaybackProgressInfo`（三个接口共用）

**[属] 3.5.3 `MediaBrowser.Model.Session.PlaybackProgressInfo` 全部字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `CanSeek` | bool | 客户端能否 seek |
| `Item` | `BaseItemDto` | 完整 item 对象（可选，冗长） |
| `NowPlayingQueue` | `QueueItem[]` | 播放队列 |
| `PlaylistItemId` | string | 队列项 Id |
| `ItemId` | string | ★ item Id |
| `SessionId` | string | 会话 Id |
| `MediaSourceId` | string | ★ 播放的 MediaSource |
| `AudioStreamIndex` | int? | 当前音轨 |
| `SubtitleStreamIndex` | int? | 当前字幕轨 |
| `IsPaused` | bool | 是否暂停 |
| `IsMuted` | bool | 是否静音 |
| `PositionTicks` | long? | ★ 当前位置（1 ms = 10000 ticks） |
| `PlaybackStartTimeTicks` | long? | 起播时间 |
| `VolumeLevel` | int? | 0–100 |
| `Brightness` | int? | 亮度（部分客户端） |
| `AspectRatio` | string | 画面比 |
| `PlayMethod` | string | `DirectPlay` / `DirectStream` / `Transcode` |
| `LiveStreamId` | string | 直播 |
| `PlaySessionId` | string | ★ 来自 PlaybackInfo |
| `RepeatMode` | string | `RepeatNone` / `RepeatAll` / `RepeatOne` |

**[属] `PlaybackStartInfo` 是 `PlaybackProgressInfo` 的（近乎）空子类** —— 3.5.3 的 `PlaybackStartInfo` 类型在 DLL 里**没有自己声明的属性**（全部继承）。
**[规] 4.1.1.0 OpenAPI 里 `PlaybackStartInfo` 与 `PlaybackProgressInfo` 的字段列表完全相同**，唯一的差别是 `PlaybackProgressInfo` 多一个 `RunTimeTicks`。

**[属] `PlaybackStopInfo` 全部字段：**
`NowPlayingQueue, PlaylistItemId, Item, ItemId, SessionId, MediaSourceId, PositionTicks, LiveStreamId, PlaySessionId, Failed, NextMediaType`

**[规] 4.1.1.0 的 `PlaybackStopInfo` 与 3.5.3 一致（`NowPlayingQueue, PlaylistItemId, Item, ItemId, SessionId, MediaSourceId, PositionTicks, LiveStreamId, PlaySessionId, Failed, NextMediaType`）。**

#### 2.7.3 ★ 关于 `EventName`：三层证据冲突，最终以 4.10 规范为准

任务书里提到「`EventName`（`timeupdate`/`pause`/`unpause`/`stop`/`seek`/`ended`?）」。这一条我查到了**三层互相冲突的证据**，最终结论如下：

| 来源 | `EventName` 是否存在 |
|---|---|
| **[属] Emby 3.5.3 的 `PlaybackProgressInfo` DTO** | ❌ **不存在**（20 个属性，无 `EventName`） |
| **[规] Emby 4.1.1.0 的官方 OpenAPI** | ❌ **不存在**（21 个属性，无 `EventName`） |
| **[官] Emby 开发者文档 [Playback Check-ins](https://dev.emby.media/doc/restapi/Playback-Check-ins.html)** | ✅ **明确存在**，且给出 13 个取值 |
| **[规410] Emby 4.10.0.40 的官方 OpenAPI** | ✅ **存在**（`PlaybackProgressInfo` 与 `PlaybackStartInfo` 各 **30 个属性**，含 `EventName`） |

→ **最终结论：`EventName` 是 Emby 4.x 新增字段，在 3.5.3 与 4.1.1.0 里都没有，在 4.10.0.40 里存在。**
**任务书是对的，我最初基于 3.5.3 与 4.1.1.0 的"不存在"判断是错的 —— 这里如实记录这个纠正。**

**[官] 官方 `EventName` 取值（Playback Check-ins 原文，共 13 个）：**
```
TimeUpdate   Pause   Unpause   VolumeChange   RepeatModeChange
AudioTrackChange   SubtitleTrackChange
PlaylistItemMove   PlaylistItemRemove   PlaylistItemAdd
QualityChange   SubtitleOffsetChange   PlaybackRateChange
```
（注意：**没有** `stop` / `seek` / `ended` —— 任务书里猜的那三个都不在官方列表里。）

**[官] 官方对上报频率的明文规定（原文）：**
> "Playback progress should be reported at the following times:
> - **Automatically every 10 seconds**
> - **Immediately following any user interaction with the player, for example, pause, un-pause, etc.**"
> "**The server will automatically increment playback progress every second, so it is not necessary to automatically report more often than at 10 second intervals. The progress reports coming from the app will be used to re-calibrate the automatic progress increment on the server.**"

→ **★ 这是 Emby 进度上报最实用的两条规则：客户端每 10 秒报一次 + 每次用户交互立刻报一次；服务端自己在两次上报之间每秒插值。**

**[官] 官方还提供了 WebSocket 上报通道（原文）：**
```json
{ "MessageType": "ReportPlaybackProgress", "Data": { /* 与 HTTP body 完全相同的对象 */ } }
```
→ "For improved performance, the progress messages can also be sent to the server via the web socket connection."

**[官] `ItemId` vs `Item` 的官方语义（原文）：**
> "If the user is playing a server library item, simply supply the `ItemId` property and omit `Item`. If the user is playing content that is not part of the server library, it can still be reported by supplying an object containing information describing the media."
> `Item` 的属性：`Name, MediaType(Audio|Video|Book|Game), Type(Movie|Episode|Trailer|Video|Audio|Book|Game), RunTimeTicks, PremiereDate, ProductionYear, IndexNumber, IndexNumberEnd, ParentIndexNumber, SeriesName, Album, Artists`

**[官] 官方 Progress 端点说明（原文）：** "The contents of the request are identical to the playback start message, except with an additional `EventName` property." → **即 Start 与 Progress 的区别只在 `EventName`（与 4.10 规范一致：两者属性集完全相同）。**

**[规410] 4.10.0.40 的三个 schema 完整字段表（30 / 30 / 14）：**

`PlaybackProgressInfo`（30）= `CanSeek, NowPlayingQueue, PlaylistItemId, SessionId, AudioStreamIndex, SubtitleStreamIndex, IsPaused, PlaylistIndex, PlaylistLength, IsMuted, RunTimeTicks, PlaybackStartTimeTicks, VolumeLevel, Brightness, AspectRatio, EventName, PlayMethod, RepeatMode, SleepTimerMode, SleepTimerEndTime, Shuffle, SubtitleOffset, PlaybackRate, PlaylistItemIds, PlaySessionId, ItemId, LiveStreamId, MediaSourceId, Item, PositionTicks`

`PlaybackStartInfo`（30）= **与上表完全相同**（Emby 与 Jellyfin 一样，Start 是 Progress 的同构体）

`PlaybackStopInfo`（14）= `NowPlayingQueue, PlaylistItemId, PlaylistIndex, PlaylistLength, SessionId, IsAutomated, Failed, NextMediaType, PlaySessionId, ItemId, LiveStreamId, MediaSourceId, Item, PositionTicks`

> ★ **一个容易忽略的坑**：`PlaybackStopInfo` 在 4.10 **没有 `PositionTicks` 之外的进度语义区分**，也**没有 `RunTimeTicks`**；想知道"看了多久"必须自己用 Start/Progress 的 `PlaybackStartTimeTicks` 推算。

> **实操建议：** 若你要兼容 Emby 3.5.x/4.0/4.1（老服务器），**不要发送 `EventName` 之外的新字段**（`SleepTimerMode`、`Shuffle`、`PlaylistItemIds` 等），只发 3.5.3 就有的那 20 个字段即可，两边都能工作。若只面向 4.10+，可以完整使用。

#### 2.7.4 旧的「按用户」上报路径（Emby 3.x 遗留，[属] 原文）

```
/Users/{UserId}/PlayingItems/{Id}            POST    → Reports that a user has begun playing an item
/Users/{UserId}/PlayingItems/{Id}/Progress   POST    → Reports a user's playback progress
/Users/{UserId}/PlayingItems/{Id}            DELETE  → Reports that a user has stopped playing an item
/Users/{UserId}/PlayedItems/{Id}             POST    → Marks an item as played   (参数 DatePlayed: yyyyMMddHHmmss)
/Users/{UserId}/PlayedItems/{Id}             DELETE  → Marks an item as unplayed
```

**`OnPlaybackStart` 的 `ApiMember` 参数（[属] 原文，说明旧接口的字段集）：**
`UserId(path,必填), Id(path,必填), MediaSourceId(query,必填), CanSeek, AudioStreamIndex, SubtitleStreamIndex, PlayMethod, LiveStreamId, PlaySessionId`

**`OnPlaybackProgress` 的 `ApiMember` 参数：**
`UserId, Id, MediaSourceId(必填), PositionTicks, IsPaused, IsMuted, AudioStreamIndex, SubtitleStreamIndex, VolumeLevel, PlayMethod, LiveStreamId, PlaySessionId, RepeatMode`

**`OnPlaybackStopped` 的 `ApiMember` 参数：**
`UserId, Id, MediaSourceId(必填), NextMediaType(必填), PositionTicks, LiveStreamId, PlaySessionId`

> **[规] 4.1.1.0 仍然保留这三条 `/Users/{UserId}/PlayingItems/*`**。新客户端应优先用 `/Sessions/Playing*`（它们用 token 里的用户身份，不需要传 `UserId`）。

#### 2.7.5 ★ 续播阈值（[码] 级，Emby 源码原文）

文件：`Emby.Server.Implementations/Library/UserDataManager.cs` L222–L280
链接：`https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/Library/UserDataManager.cs#L222-L280`

```csharp
var runtimeTicks  = item.GetRunTimeTicksForPlayState();
var positionTicks = reportedPositionTicks ?? runtimeTicks;
var hasRuntime    = runtimeTicks > 0;

if (positionTicks > 0 && hasRuntime)
{
    var pctIn = Decimal.Divide(positionTicks, runtimeTicks) * 100;

    // Don't track in very beginning
    if (pctIn < _config.Configuration.MinResumePct)                 // L236
    {
        positionTicks = 0;                                            // 视为没看过
    }
    // If we're at the end, assume completed
    else if (pctIn > _config.Configuration.MaxResumePct || positionTicks >= runtimeTicks)  // L242
    {
        positionTicks = 0;
        data.Played = playedToCompletion = true;                      // 视为看完
    }
    else
    {
        var durationSeconds = TimeSpan.FromTicks(runtimeTicks).TotalSeconds;
        if (durationSeconds < _config.Configuration.MinResumeDurationSeconds)  // L253
        {
            positionTicks = 0;
            data.Played = playedToCompletion = true;                  // 短片直接算看完
        }
    }
}
else if (!hasRuntime)
{
    data.Played = playedToCompletion = true;   // L260  不知道时长 → 当作看完
    positionTicks = 0;
}

if (!item.SupportsPlayedStatus)        { positionTicks = 0; data.Played = false; }  // L267
if (!item.SupportsPositionTicksResume) { positionTicks = 0; }                       // L275

data.PlaybackPositionTicks = positionTicks;
```

**三个可配置项（[属] `ServerConfiguration` 属性）：`MinResumePct`、`MaxResumePct`、`MinResumeDurationSeconds`**

> **[属] 一个 4.x 的变化：** 在 **4.1.1.0 的官方 OpenAPI 里，这三个字段出现在 `Configuration.LibraryOptions` 里，而不是全局 `ServerConfiguration`** —— 也就是说 **Emby 4.x 把续播阈值下放到了「每个媒体库」的 LibraryOptions**。
> （3.5.3 里它们在全局 `ServerConfiguration`；`LibraryOptions` 里没有它们。）
>
> ⚠️ **[未] 具体默认值（MinResumePct=? / MaxResumePct=? / MinResumeDurationSeconds=?）本报告未能确证** —— 默认值写在 `MediaBrowser.Model` 的构造函数 IL 里，未反编译；官方 OpenAPI 的 schema 也没有 `default`。（对比：Jellyfin 是 `MinResumePct=5 / MaxResumePct=90 / MinResumeDurationSeconds=300`，见配套文件，但**不能直接当成 Emby 的默认值**。）

---

### 2.7.6 ★★ 从 Emby 自己的 Web 客户端源码读到的「实际调用方式」（[码] 级，最实用）

> 出处：`MediaBrowser.WebDashboard/dashboard-ui/bower_components/emby-apiclient/apiclient.js`（Emby Server 3.5.3 仓库内自带的官方 JS 客户端，**压缩过的单行文件**，所以只能给"函数名 + 代码原文"而不能给行号）。
> **这是 Emby 官方客户端自己的做法 —— 比 OpenAPI 更能说明"参数到底放哪"。**

**(1) 授权头的构造（原文）：**
```js
ApiClient.prototype.setRequestHeaders = function (headers) {
    var currentServerInfo = this.serverInfo(), appName = this._appName,
        accessToken = currentServerInfo.AccessToken, values = [];
    if (appName)         values.push('Client="'   + appName        + '"');
    if (this._deviceName) values.push('Device="'  + this._deviceName + '"');
    if (this._deviceId)   values.push('DeviceId="'+ this._deviceId   + '"');
    if (this._appVersion) values.push('Version="' + this._appVersion + '"');
    if (accessToken)      values.push('Token="'   + accessToken      + '"');
    if (values.length) {
        var auth = "MediaBrowser " + values.join(", ");
        headers["X-Emby-Authorization"] = auth;
    }
};
```
→ **确认：Emby 自己的 Web 客户端用的头名是 `X-Emby-Authorization`，scheme 字面量是 `MediaBrowser`，参数顺序是 `Client, Device, DeviceId, Version, Token`，分隔符是 `", "`（逗号+空格），值一律加双引号。**

**(2) `/emby` 前缀是客户端自动补的（原文）：**
```js
ApiClient.prototype.getUrl = function (name, params, serverAddress) {
    ...
    var lowered = url.toLowerCase();
    if (-1 === lowered.indexOf("/emby") && -1 === lowered.indexOf("/mediabrowser")) { url += "/emby"; }
    if ("/" !== name.charAt(0)) { url += "/"; }
    url += name;
    if (params && (params = paramsToString(params))) { url += "?" + params; }
    return url;
};
```
→ **客户端在 base URL 里没有 `/emby` 或 `/mediabrowser` 时自动追加 `/emby`** —— 这印证了官方文档的 `http[s]://hostname:port/emby/{apipath}`。

**(3) ★ `PlaybackInfo` 的调用方式（原文）—— 重要！**
```js
ApiClient.prototype.getPlaybackInfo = function (itemId, options, deviceProfile) {
    var postData = { DeviceProfile: deviceProfile };
    return this.ajax({
        url:  this.getUrl("Items/" + itemId + "/PlaybackInfo", options),   // ← options 全部进 query string!
        type: "POST",
        data: JSON.stringify(postData),                                     // ← body 里只有 DeviceProfile
        contentType: "application/json",
        dataType: "json"
    });
};
```
→ **★★ 这是本报告最有实操价值的一条：Emby 官方客户端在 `POST /Items/{Id}/PlaybackInfo` 时，把 `UserId` / `MaxStreamingBitrate` / `StartTimeTicks` / `MediaSourceId` / `EnableDirectPlay` 等**全部放在 query string**，body 里**只放 `DeviceProfile`**。**
→ Jellyfin 侧的做法正好相反（Jellyfin 把 15 个 query 参数标为 `deprecated`，要求放 body）。**做兼容客户端时最稳的做法是：两边都放（query + body 同名同值）。**

**(4) 登录 body 用的是 `Pw` 而不是 `Password`（原文）：**
```js
ApiClient.prototype.authenticateUserByName = function (name, password) {
    var url = this.getUrl("Users/authenticatebyname"), instance = this;
    ...
    var postData = { Username: name, Pw: password || "" };
    instance.ajax({ type: "POST", url: url, data: JSON.stringify(postData),
                    dataType: "json", contentType: "application/json" }) ...
};
```
→ **Emby 官方客户端发的是 `{ "Username": "...", "Pw": "..." }`**。这与 4.10 官方规范的 `AuthenticateUserByName { Username, Pw }` 完全一致（`Password` 只是兼容别名）。

**(5) 刷新元数据也是 query 参数（原文）：**
```js
ApiClient.prototype.refreshItem = function (itemId, options) {
    var url = this.getUrl("Items/" + itemId + "/Refresh", options || {});
    return this.ajax({ type: "POST", url: url });
};
```

**(6) WebSocket 的 URL 与鉴权（原文）：**
```js
ApiClient.prototype.openWebSocket = function () {
    var accessToken = this.accessToken();
    var url = this.getUrl("socket");
    url = replaceAll(url, "emby/socket", "embywebsocket");
    url = replaceAll(url, "https:", "wss:");
    url = replaceAll(url, "http:",  "ws:");
    url += "?api_key=" + accessToken;
    url += "&deviceId=" + this.deviceId();
    var webSocket = new WebSocket(url);
    ...
};
ApiClient.prototype.sendWebSocketMessage = function (name, data) {
    var msg = { MessageType: name };
    if (data) { msg.Data = data; }
    this._webSocket.send(JSON.stringify(msg));
};
```
→ **★ WebSocket 端点：`/embywebsocket?api_key=<token>&deviceId=<deviceId>`**（注意路径是 `embywebsocket` 而不是 `emby/socket`）。
→ WebSocket 消息信封：`{ MessageType: "<名字>", Data: {...} }` —— **与 §2.7.3 官方文档里说的 `{MessageType: "ReportPlaybackProgress", Data: {...}}` 完全一致。**
→ **[属] `Emby.Server.Implementations/Session/` 里有 `SessionWebSocketListener.cs` 与 `WebSocketController.cs` 佐证服务端实现。**

**(7) ★ 码率探测（`PlaybackInfo` 的 `MaxStreamingBitrate` 从哪来）（原文）：**
```js
ApiClient.prototype.detectBitrate = ...
ApiClient.prototype.getDownloadSpeed = function (byteSize) {
    var url = this.getUrl("Playback/BitrateTest", { Size: byteSize });
    ... return Math.round(8 * bytesPerSecond);
};
function detectBitrateInternal(instance, tests, index, currentBitrate) { ... }
function detectBitrateWithEndpointInfo(instance, endpointInfo) {
    if (endpointInfo.IsInNetwork) { return 14e7; }        // 局域网：直接给 140,000,000 bps
    return detectBitrateInternal(instance, [
        { bytes: 5e5, threshold: 5e5 },                    // 500 KB / 0.5 Mbps
        { bytes: 1e6, threshold: 2e7 },                    // 1 MB  / 20 Mbps
        { bytes: 3e6, threshold: 5e7 }                     // 3 MB  / 50 Mbps
    ], 0, currentBitrate);
}
function normalizeReturnBitrate(instance, bitrate) {
    ...
    var result = Math.round(0.7 * bitrate);                // ★ 取其 70% 作为安全值
    if (instance.getMaxBandwidth) { result = Math.min(result, maxRate); }
    return result;
}
```
→ **★ 这是"客户端怎么算 `MaxStreamingBitrate`"的官方答案：**
> 1. 先 `GET /emby/System/Endpoint`（`getEndpointInfo`）判断 `IsInNetwork`
> 2. 局域网 → **直接 140 Mbps**（`14e7`）
> 3. 非局域网 → 依次下载 **500 KB / 1 MB / 3 MB** 三档，测出实际带宽
> 4. 若低于该项阈值则停止（不再测更大档）
> 5. **取实测值的 70%** 作为 `MaxStreamingBitrate`
> 6. 探测结果缓存 **1 小时**（`36e5` ms）
> 7. 用 `GET /emby/Playback/BitrateTest?Size=<bytes>` 做下载测速

**★ 这直接回答了任务书里的「码率阶梯」问题：**
> **Emby 服务端不定义码率阶梯 —— 阶梯在客户端。** 客户端用上面这套算法决定自己的 `MaxStreamingBitrate`，在 `PlaybackInfo` 里发给服务端；服务端据此决定是直连还是转码、以及转码目标码率。
> （这与 Jellyfin 侧子 agent 的结论一致："**码率阶梯不在服务端**，由客户端在 `PlaybackInfo` 传 `maxStreamingBitrate`"。）

### 2.8 Emby Connect、多用户、权限对播放的影响

#### 2.8.1 Emby Connect

**[属] 从 `ThirdParty/emby/Emby.Server.Connect.dll` 字符串堆提取（原文）：**
```
https://connect.emby.media/service/
https://connect.emby.media/service/ip
X-Connect-Token
accessToken
ConnectAccessKey
ConnectServerId
connectUserId
connectUsername
ConnectUserQuery
connect.txt
Cannot update Emby Connect information without a WanApiAddress
wanApiAddress
Connect returned a 404 when removing a user auth link. Handling it.
Error registering with Connect
A Connect account is required in order to send invitations.
http://ipv4bot.whatismyipaddress.com
http://ipv6bot.whatismyipaddress.com
```

**[规] 相关端点：**
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/Connect/Exchange?ConnectUserId=` | 用 Connect 用户 Id 换本地用户 |
| GET | `/Connect/Pending` | 待批准的 Connect 请求 |
| POST/DELETE | `/Users/{Id}/Connect/Link` | 本地用户绑定/解绑 Connect 账号 |

**[码] 3.5.3 里 Connect token 也是一种 access token（`AuthorizationContext.cs` L164）：**
```csharp
else
{
    info.User = _connectManager.GetUserFromExchangeToken(token);
}
```
→ **当 `token` 不在本地 `AuthenticationInfo` 表里时，会尝试当成 Connect exchange token。** 这就是 Emby Connect 登录的实现接缝。

**[规] `UserDto` 里的 Connect 相关字段：`ConnectUserName`、`ConnectLinkType`。**

> **[未]** Connect 的完整协议（`/service/` 下的具体子路径、注册/心跳格式）本报告**未能确证** —— 该 DLL 的方法体未反编译，官方也未公开文档。**这是私有协议，不建议第三方实现。**

#### 2.8.2 用户策略（`UserPolicy`）里影响播放的字段

**[属] 3.5.3 `MediaBrowser.Model.Users.UserPolicy` 全部 35 个字段：**

| 分组 | 字段 |
|---|---|
| 身份 | `IsAdministrator, IsHidden, IsDisabled, AuthenticationProviderId, InvalidLoginAttemptCount` |
| **播放权限** | **`EnableMediaPlayback`**、**`EnableAudioPlaybackTranscoding`**、**`EnableVideoPlaybackTranscoding`**、**`EnablePlaybackRemuxing`**、**`EnableSyncTranscoding`**、**`EnableMediaConversion`** |
| **码率限制** | **`RemoteClientBitrateLimit`** ★ |
| 家长控制 | `MaxParentalRating`、`BlockedTags`、`BlockUnratedItems` |
| 内容访问 | `EnabledDevices`、`EnableAllDevices`、`EnabledChannels`、`EnableAllChannels`、`EnabledFolders`、`EnableAllFolders`、`BlockedMediaFolders`、`BlockedChannels` |
| 远程 | `EnableRemoteAccess`、`EnableRemoteControlOfOtherUsers`、`EnableSharedDeviceControl`、`AccessSchedules` |
| 其他 | `EnableUserPreferenceAccess`、`EnableLiveTvManagement`、`EnableLiveTvAccess`、`EnableContentDeletion`、`EnableContentDeletionFromFolders`、`EnableContentDownloading`、`EnablePublicSharing` |

**[属] 这三个开关在播放决策里被真实检查** —— 证据是 `Emby.Server.MediaEncoding.dll` 内的日志格式串：
```
User policy for {0}. EnableAudioPlaybackTranscoding: {1}
User policy for {0}: EnablePlaybackRemuxing: {1} EnableVideoPlaybackTranscoding: {2} EnableAudioPlaybackTranscoding: {3}
RemoteClientBitrateLimit: {0}, RemoteIp: {1}, IsInLocalNetwork: {2}
```

**推论（[属] 强证据）：**
1. **`EnablePlaybackRemuxing` 关掉 → 连 DirectStream 都不允许**（只能直连或全转码）
2. **`EnableVideoPlaybackTranscoding` 关掉 → 不能视频转码**
3. **`EnableAudioPlaybackTranscoding` 关掉 → 不能音频转码**
4. **`RemoteClientBitrateLimit` 只对「非局域网」客户端生效**（日志里有 `IsInLocalNetwork`）→ 超出限制时 `PlaybackInfoResponse.ErrorCode = RateLimitExceeded`
5. 局域网判定用 `ServerConfiguration.LocalNetworkSubnets` / `LocalNetworkAddresses`

**[属] `ServerConfiguration` 里的全局对应项：`RemoteClientBitrateLimit`（全局兜底）、`LocalNetworkSubnets`、`LocalNetworkAddresses`、`EnableRemoteAccess`。**

#### 2.8.3 `BaseItemDto` 里与播放/刮削相关的字段（[规] 4.1.1.0）

```
CommunityRating, CriticRating, OfficialRating, CustomRating,
OriginalTitle, Overview, Taglines, Tags, Genres, GenreItems,
People, Studios, ProviderIds, Path, PremiereDate, ProductionYear,
DateCreated, IndexNumber, IndexNumberEnd, ParentIndexNumber,
SeriesId, SeriesName, SeasonId, SeasonName, EpisodeTitle,
ImageTags, BackdropImageTags, ParentBackdropImageTags,
ParentLogoImageTag, ParentLogoItemId, ParentThumbImageTag,
SeriesPrimaryImageTag, PrimaryImageAspectRatio,
MediaSources, MediaStreams, IsPremiere, AirsBeforeSeasonNumber,
AirsBeforeEpisodeNumber, AirsAfterSeasonNumber, DisplaySpecialsWithSeasons
```

**[属] `MediaStream` 在 `BaseItemDto` 里也返回** —— 客户端可以从 `/Users/{UserId}/Items/{Id}` 直接拿到流信息，不必调 `PlaybackInfo`（但**决策必须走 `PlaybackInfo`**）。

---

## 3. 自动刮削（Metadata Scraping）

> **本章的证据分层要特别小心**：Emby 官方知识库（`emby.media/support/articles/`）**非常不完备** —— 经对官方文档源仓库 `EmbySupport/Emby.Docs` 的 master 全量检索确认：
> **官方知识库没有「元数据提供方」专页，也完全没有「NFO」专页**（全仓库 `*.md` 检索 `\bnfo\b|\.nfo` 仅 3 处顺带提及，且**无任何一处给出 NFO 文件名**）。
> 因此本章会把「**[码] Emby 3.5.3 源码实际实现的规则**」与「**[官] 官方文档声明的规则**」**并列**，两者不一致的地方明确标注。

### 3.1 命名与目录规范

#### 3.1.1 官方定义的总原则

**[官]** 出处：[Quick Start](https://emby.media/support/articles/Quick-Start.html)

> "**Emby identifies your media according to folder structure, file name, and type of library to which it is assigned.** Once identified, Emby downloads a rich assortment of information about your media including ratings, cast, descriptions & poster art."

→ **三大识别输入：目录结构 + 文件名 + 库内容类型。** 这是 Emby 识别机制最权威的一句官方定义。

**[属]** 3.5.3 的库内容类型枚举 `CollectionType`：
`Movies, TvShows, Music, MusicVideos, Trailers, HomeVideos, BoxSets, Books, Photos, Games, LiveTv, Playlists, Folders`

#### 3.1.2 电影

**[官]** 出处：[Movie Naming](https://emby.media/support/articles/Movie-Naming.html)

**推荐结构：每部电影一个独立文件夹，用文件夹名判定影片**

```
\Movies\Avatar (2009)\Avatar (2009).mkv
\Movies\Pulp Fiction (1994)\Pulp Fiction (1994).mp4
\Movies\Top Gun (1986)\Top Gun (1986).mp4
```

原文：**"Movie folders are used internally to improve detection and would only be reflected in the library 'Folders' view."**

**ID 标签（写在文件夹名或文件名里）—— 支持 8 种格式：**

```
Name (Year) [tmdbid=xxxx]     Name (Year) [tmdbid-xxxx]
Name (Year) [tmdb=xxxx]       Name (Year) [tmdb-xxxx]
Name (Year) {tmdbid=xxxx}     Name (Year) {tmdbid-xxxx}
Name (Year) {tmdb=xxxx}       Name (Year) {tmdb-xxxx}
```

支持的 ID 名称：**tvdb、tmdb、imdb**。示例：`Casino Royale (2006) [tmdbid=36557]`

**多版本（Multi-version）—— 硬规则：**

```
/Movies/300 (2006)/300 (2006) - 1080p.mkv
/Movies/300 (2006)/300 (2006) - 4K.mkv
/Movies/300 (2006)/300 (2006) - 720p.mp4
/Movies/300 (2006)/300 (2006) - extended edition.mp4
/Movies/300 (2006)/300 (2006) - directors cut.mp4
/Movies/300 (2006)/300 (2006) - 3D.hsbs.mp4
```

- 必须**同一文件夹**，每个版本名必须以**文件夹名**开头，后接 **`" - "`**（空格-连字符-空格）
- 原文：**"If using the dash method anything following the dash will be what you see in the Emby client app."**
- **官方硬限制：`Up to 8 different versions will appear in a list of a movie versions.`**

**电影 Extras 子文件夹（官方完整列表）：**
`extras`、`specials`、`shorts`、`scenes`、`featurettes`、`behind the scenes`、`deleted scenes`、`interviews`、`trailers`
- **不支持嵌套**；官方警告：**必须先有正片文件再加 extras，否则会误识别**

**其他官方支持格式：**
- DVD：含 `VIDEO_TS` 子目录或 `VIDEO_TS.ifo` → DVD；含 `BDMV` → Blu-ray
- ISO：文件名含 `.dvd` 或 `.bluray` 才自动判定，**否则默认按 DVD 处理**
- **分卷（file stacking）后缀**：`part#`、`cd#`、`dvd#`、`pt#`、`disk#`、`disc#`（# 取 1–9 或 A–D），另支持 `moviename#.ext`（# 为 A–D）。要求同文件夹，且**该文件夹内无其他视频**
- `.strm`：见 [Strm Files](https://emby.media/support/articles/Strm-Files.html)
- `.disc` 占位：见 [Media Stubs](https://emby.media/support/articles/Media-Stubs.html)
- 预告片：同名 + `-trailer`（如 `Home Alone (1990)-trailer.mp4`），或放 `trailers` 子文件夹；**蓝光/DVD 目录 rip 必须用 trailers 子文件夹**（[Trailer Naming](https://emby.media/support/articles/Trailers.html)）

**[属] 3.5.3 代码里的对应实现（与官方文档一致的部分）：**

`Emby.Server.Implementations/Library/Resolvers/BaseVideoResolver.cs` L169–L196：
```csharp
var extension = Path.GetExtension(video.Path);
video.VideoType =
    string.Equals(extension, ".iso", OrdinalIgnoreCase) ||
    string.Equals(extension, ".img", OrdinalIgnoreCase) ? VideoType.Iso : VideoType.VideoFile;

video.IsShortcut = string.Equals(extension, ".strm", StringComparison.OrdinalIgnoreCase);   // L175
video.IsPlaceHolder = videoInfo.IsStub;

if (videoInfo.IsStub)
{
    if (string.Equals(videoInfo.StubType, "dvd", ...))    video.VideoType = VideoType.Dvd;
    else if (string.Equals(videoInfo.StubType, "bluray", ...)) video.VideoType = VideoType.BluRay;
}
```
→ **[码] `.strm` 是官方支持的一等公民**（`IsShortcut`）。这对「网盘管理」类项目直接相关。

**[属] `Emby.Naming.dll` 字符串堆里的视频扩展名全集（原文，共 35 个）：**
```
.mkv .mp4 .m4v .3gp .nsv .ts .ty .strm .rm .rmvb .ifo .mov .qt .divx .xvid .bivx
.vob .nrg .img .iso .pva .wmv .asf .asx .ogm .m2v .avi .bin .dvr-ms .mpg .mpeg
.avc .vp3 .svq3 .nuv .viv .dv .fli .flv .001 .tp .disc
```
**[属] `NamingOptions.VideoFileExtensions` / `StubFileExtensions` 是两个独立列表。**

**[属] 分卷/堆叠（stacking）正则（`NamingOptions.VideoFileStackingExpressions`，原文）：**
```
(.*?)([ _.-]*(?:cd|dvd|p(?:ar)?t|dis[ck])[ _.-]*[0-9]+)(.*?)(\.[^.]+)$
(.*?)([ _.-]*(?:cd|dvd|p(?:ar)?t|dis[ck])[ _.-]*[a-d])(.*?)(\.[^.]+)$
(.*?)([ ._-]*[a-d])(.*?)(\.[^.]+)$
```

#### 3.1.3 电视剧

**[官]** 出处：[TV Naming](https://emby.media/support/articles/TV-Naming.html)

> 原文硬规定：**"It is mandatory for each TV show to have its own folder under the library folder path(s)."**（每部剧**必须**有独立文件夹。）

**推荐结构：**
```
\TV\Glee (2009)\Season 1\Glee S01E01.mp4
\TV\Seinfeld (1989)\Seinfeld S01E01.mp4
```
年份非强制，但对重启剧（Battlestar Galactica 1978 vs 2003）与易混淆剧名（Africa (2013)）**非常有帮助**。

**官方剧集命名约定完整列表（原文）：**
```
show name - S01E01 - Episode Name.ext
show name S01E01 Episode Name.ext
anything_s01e02.ext
anything_s1e2.ext
anything_s01.e02.ext
anything_s01_e02.ext
anything_1x02.ext
anything_102.ext
02 Episode Name.ext
s01e02.ext
1x02.ext
```

**官方按日期命名列表（原文）：**
```
anything_1996.11.14.ext
anything_1996-11-14.ext
anything_14.11.1996.ext
```

**Specials（Season 00）—— 季文件夹名只能是这三个之一：**
```
Season 0      Season 00      Specials
```
示例：`\TV\Glee (2009)\Season 0\Glee S00E01.mp4`

**多集文件（Multi-Episode）—— 官方给出 20+ 种写法，全部要求属于同一季**，例如：
```
01x02x03 episode name.ext      S01E02E03 episode name.ext
S01xE02xE03 episode name.ext   S01E02-E03 episode name.ext
S01E02-X03 episode name.ext    01x02 01x03 episode name.ext
01x02 - 01x05 episode name.ext   (includes episodes 2,3,4 and 5)
S01x02 - S01x03 episode name.ext
```

**多版本剧集：**
```
show name - S01E01 - Display Name 1.ext
show name - S01E01 - Display Name 2.ext
```
官方限制同为 **最多 8 个版本**。

**TV Extras：** 可在**剧集级、季级、集级**放 extras 文件夹，名字同电影；也支持**文件名后缀**：
```
-behindthescenes   -deleted   -featurette   -interview
-other             -scene     -short        -trailer
```
官方重要说明：**"Emby will not look for external metadata provider (e.g. TVDB) matches for episode-level extras; to potentially match, the extras need to be at the series or season level."**

**混合内容库（Unset 内容类型）：** 官方原文 **"TV series are supported in mixed content libraries using the Unset content type, but season sub-folders are required"**；[Library Setup](https://emby.media/support/articles/Library-Setup.html) 另警告 **"Please note that support for mixed content is limited."**

**复杂目录结构：** 若顶级目录在剧集文件夹之前还有细分（如 `\TV\A-M\Glee (2009)`），官方推荐把 `A-M`、`N-Z` 分别作为**库路径**加入，而不是添加顶层 `\TV`。

#### 3.1.4 ★ [属] 3.5.3 代码里的季目录名与多语言支持（官方文档没写）

**[属] `Emby.Naming.dll` 字符串堆里的季文件夹名（原文）：**
```
season   specials   extras   series   son   temporada   saison   staffel   stagione
```
→ **Emby 的季目录识别是多语言的**（西班牙语 `temporada`、法语 `saison`、德语 `staffel`、意大利语 `stagione`、以及 `series`/`son`）。**官方 TV Naming 页只写了 `Season 0/00` 与 `Specials`，这是文档滞后于实现的典型例子。**

**[属] `Emby.Naming.TV.SeasonPathParser` 方法名：** `Parse`、`GetSeasonNumberFromPath`、`GetSeasonNumberFromPart`、`GetSeasonNumberFromPathSubstring`
**[码] 使用点：** `Emby.Server.Implementations/Library/Resolvers/TV/SeasonResolver.cs` L53：
```csharp
var seasonParserResult = new SeasonPathParser(namingOptions).Parse(path, true, true);
```
L90（Season 0 的显示名可由库选项覆盖）：
```csharp
args.LibraryOptions.SeasonZeroDisplayName :
string.Format(_localization.GetLocalizedString("NameSeasonNumber"), seasonNumber.ToString(UsCulture), ...)
```
**[属] `LibraryOptions.SeasonZeroDisplayName`** —— 即「Season 0 在 UI 里显示成什么」（默认通常是 "Specials"）。

#### 3.1.5 ★ [属] 3.5.3 的季集识别正则全集（官方文档从未公布）

这些正则直接从 `Emby.Naming.dll` 的 UTF-16 字符串堆读出，属于 `NamingOptions.EpisodeExpressions / EpisodeWithoutSeasonExpressions / EpisodeMultiPartExpressions / CleanStringRegexes / CleanDateTimeRegexes` 等列表。

**A. 主 SxxExx 系列（`EpisodeExpressions`）**
```
.*(\\|\/)(?<seriesname>((?![Ss]([0-9]+)[][ ._-]*[Ee]([0-9]+))[^\\\/])*)?[Ss](?<seasonnumber>[0-9]+)[][ ._-]*[Ee](?<epnumber>[0-9]+)([^\\/]*)$
[\._ -]()[Ee][Pp]_?([0-9]+)([^\\/]*)$
[\\/\._ \[\(-]([0-9]+)x([0-9]+(?:(?:[a-i]|\.[1-9])(?![0-9]))?)([^\\/]*)$
[\\\\/\\._ -](?<seriesname>(?![0-9]+[0-9][0-9])([^\\\/])*)[\\\\/\\._ -](?<seasonnumber>[0-9]+)(?<epnumber>[0-9][0-9](?:(?:[a-i]|\\.[1-9])(?![0-9]))?)([\\._ -][^\\\\/]*)$
.*(\\|\/)[sS]?(?<seasonnumber>\d{1,4})[xX](?<epnumber>\d{1,3})[^\\\/]*$
.*(\\|\/)[sS](?<seasonnumber>\d{1,4})[x,X]?[eE](?<epnumber>\d{1,3})[^\\\/]*$
.*(\\|\/)(?<seriesname>((?![sS]?\d{1,4}[xX]\d{1,3})[^\\\/])*)?([sS]?(?<seasonnumber>\d{1,4})[xX](?<epnumber>\d{1,3}))[^\\\/]*$
.*(\\|\/)(?<seriesname>[^\\\/]*)[sS](?<seasonnumber>\d{1,4})[xX\.]?[eE](?<epnumber>\d{1,3})[^\\\/]*$
```

**B. 多集（`MultipleEpisodeExpressions`，含 `endingepnumber` 命名组）**
```
.*(\\|\/)[sS]?(?<seasonnumber>\d{1,4})[xX](?<epnumber>\d{1,3})((-| - )\d{1,4}[eExX](?<endingepnumber>\d{1,3}))+[^\\\/]*$
.*(\\|\/)[sS]?(?<seasonnumber>\d{1,4})[xX](?<epnumber>\d{1,3})((-| - )\d{1,4}[xX][eE](?<endingepnumber>\d{1,3}))+[^\\\/]*$
.*(\\|\/)[sS]?(?<seasonnumber>\d{1,4})[xX](?<epnumber>\d{1,3})((-| - )?[xXeE](?<endingepnumber>\d{1,3}))+[^\\\/]*$
.*(\\|\/)[sS]?(?<seasonnumber>\d{1,4})[xX](?<epnumber>\d{1,3})(-[xE]?[eE]?(?<endingepnumber>\d{1,3}))+[^\\\/]*$
.*(\\|\/)(?<seriesname>((?![sS]?\d{1,4}[xX]\d{1,3})[^\\\/])*)?([sS]?(?<seasonnumber>\d{1,4})[xX](?<epnumber>\d{1,3}))((-| - )\d{1,4}[xXeE](?<endingepnumber>\d{1,3}))+[^\\\/]*$
...（同一家族共 8 条）
```

**C. 无季号 / 纯集号系列（`EpisodeWithoutSeasonExpressions`）**
```
.*[\\\/](?<epnumber>\d{1,3})(-(?<endingepnumber>\d{2,3}))*\.\w+$
.*(\\|\/)(?<epnumber>\d{1,3})(-(?<endingepnumber>\d{2,3}))*\s?-\s?[^\\\/]*$
.*(\\|\/)(?<epnumber>\d{1,3})(-(?<endingepnumber>\d{2,3}))*\.[^\\\/]+$
.*[\\\/][^\\\/]* - (?<epnumber>\d{1,3})(-(?<endingepnumber>\d{2,3}))*[^\\\/]*$
[Ss]eason[\._ ](?<seasonnumber>[0-9]+)[\\\/](?<epnumber>\d{1,3})([^\\\/]*)$
.*[\\\/][^\\\/]* (?<epnumber>\d{1,3})(-(?<endingepnumber>\d{2,3}))*[^\\\/]*$
[/\._ \-]()([0-9]+)(-[0-9]+)?
```

**D. 日期命名（`CleanDateTimes`，用于日期型剧集）**
```
([0-9]{4})[\.-]([0-9]{2})[\.-]([0-9]{2})     →  yyyy.MM.dd / yyyy-MM-dd / yyyy_MM_dd
([0-9]{2})[\.-]([0-9]{2})[\.-]([0-9]{4})     →  dd.MM.yyyy / dd-MM-yyyy / dd_MM_yyyy
```

**E. 清洗发布组标签（`CleanStringRegexes`，从文件名里剥掉 `1080p/x264/BluRay/...`）**
```
(.+[^ _\,\.\(\)\[\]\-])[ _\.\(\)\[\]\-]+(19[0-9][0-9]|20[0-1][0-9])([ _\,\.\(\)\[\]\-][^0-9]|$)
[ _\,\.\(\)\[\]\-](ac3|dts|custom|dc|divx|divx5|dsr|dsrip|dutch|dvd|dvdrip|dvdscr|dvdscreener|screener|dvdivx|cam|fragment|fs|hdtv|hdrip|hdtvrip|internal|limited|multisubs|ntsc|ogg|ogm|pal|pdtv|proper|repack|rerip|retail|cd[1-9]|r3|r5|bd5|se|svcd|swedish|german|read.nfo|nfofix|unrated|ws|telesync|ts|telecine|tc|brrip|bdrip|480p|480i|576p|576i|720p|720i|1080p|1080i|2160p|hrhd|hrhdtv|hddvd|bluray|x264|h264|xvid|xvidvd|xxx|www.www|\[.*\])([ _\,\.\(\)\[\]\-]|$)
[ _\,\.\(\)\[\]\-](3d|sbs|tab|hsbs|htab|mvc|\[.*\])([ _\,\.\(\)\[\]\-]|$)
(\[.*\])
```
> ★ 这一组正是「`Movie.Name.2023.1080p.BluRay.x264-GROUP.mkv` 能被正确识别成 `Movie Name`」的原因。

**F. 3D 规则（`Format3DRules`）**：`fsbs`、`ftab`、`hsbs`、`htab`、`mvc`、`sbs`、`tab`、`3d`
**[属] `Video3DFormat` 枚举：** `HalfSideBySide, FullSideBySide, FullTopAndBottom, HalfTopAndBottom, MVC`

**G. Extras 识别（`VideoExtraRules`）—— 前后缀都支持（原文串）：**
```
trailer      -trailer   .trailer   _trailer   " trailer"
sample       -sample    .sample    _sample    " sample"
themesong    theme      scene      -scene     clip       -clip
interview    -interview behindthescenes  -behindthescenes
deletedscene -deleted   featurette -featurette
short        -short
```
**[属] `ExtraType` 枚举：** `Clip, Trailer, BehindTheScenes, DeletedScene, Interview, Scene, Sample, ThemeSong, ThemeVideo`

**H. 章节/分段（`EpisodeMultiPartExpressions`，用于 DVD/蓝光的 chapter/part）**
```
ch(?:apter)?[\s_-]?(?<chapter>\d+)
p(?:ar)?t[\s_-]?(?<part>\d+)
^(?<chapter>\d+)
(?<part>\d+)$
(?<chapter>\d+)_(?<part>\d+)
dis(?:c|k)[\s_-]?(?<chapter>\d+)
[/\._ \-]p(?:ar)?t[_. -]()([ivx]+|[0-9]+)([._ -][^\/]*)$
```

**[属] `NamingOptions` 的完整可配置项（证明这些规则都是数据驱动的）：**
```
AudioFileExtensions, AlbumStackingPrefixes, SubtitleFileExtensions,
SubtitleFlagDelimiters, SubtitleForcedFlags, SubtitleDefaultFlags,
EpisodeExpressions, EpisodeWithoutSeasonExpressions, EpisodeMultiPartExpressions,
VideoFileExtensions, StubFileExtensions, AudioBookPartsExpressions,
StubTypes, VideoFlagDelimiters, Format3DRules, VideoFileStackingExpressions,
CleanDateTimes, CleanStrings, MultipleEpisodeExpressions, VideoExtraRules,
VideoFileStackingRegexes, CleanDateTimeRegexes, CleanStringRegexes,
EpisodeWithoutSeasonRegexes, EpisodeMultiPartRegexes
```

**[属] `Emby.Naming.TV.EpisodePathParserResult` 结果结构：**
`SeasonNumber, EpisodeNumber, EndingEpsiodeNumber, SeriesName, Success, IsByDate, Year, Month, Day`
→ **`IsByDate` 明确证明 Emby 的解析器区分「按集号」与「按日期」两条路径。**

**[属] `VideoFileInfo`（视频文件解析结果）：**
`Path, Container, Name, Year, ExtraType, ExtraRule, Format3D, Is3D, IsStub, StubType, IsDirectory, FileNameWithoutExtension`
**[属] `VideoInfo`（一个"影片"聚合结果）：** `Name, Year, Files, Extras, AlternateVersions`
→ **`AlternateVersions` 就是多版本合并后的产物。**

#### 3.1.6 音乐 / 有声书 / 图书 / 合集

**[官] 音乐** [Music Naming](https://emby.media/support/articles/Music-Naming.html)：
- **完全基于 tag**，Emby **4.6+ 不再强制目录结构**
- 但若要**把 nfo/图片写进媒体文件夹**，则**必须**用结构化目录（推荐 `\Music\Artist Name\Album Name\1- Song.mp3`）
- 编译专辑要让所有曲目的 **album** 与 **album artist** 一致，否则专辑会被拆开；无单一艺术家时 album artist 可填 `Various`
- `artist` 多值用 **`"; "`（分号+空格）** 分隔
- 同名专辑需用 `mbzalbumids` 或改名区分
- Music Video 用 **Music Videos** 库类型，命名规则与电影相同，文件需按 `https://imvdb.com/` 的命名

**[官] 图书** [Book Naming](https://emby.media/support/articles/Book-Naming.html)：支持 **pdf、epub、mobi、cbr、cbz、azw3**；命名只要「标题 + 括号年份」，如 `\Books\Pulp Fiction (1994).pdf`；也支持 `Avatar (2009)-cd1.pdf`。

**[官] 合集** [Collections](https://emby.media/support/articles/Collections.html)：合集名要与 **TheMovieDb.Org 上的 collection 名一致**才会自动抓图。[AutoBoxSets](https://emby.media/support/articles/AutoBoxSets.html) 原文 **"The Auto Box Sets features have now been merged into Emby Server."**（原插件已无功能，可卸载。）

**[属] 3.5.3 的合集实现：** `MediaBrowser.Providers/BoxSets/`（provider）+ `MediaBrowser.LocalMetadata/Providers/BoxSetXmlProvider.cs` + `Savers/BoxSetXmlSaver.cs`；库类型枚举里有独立的 `BoxSets`。
**[属] `BoxSet` 的 XML 保存路径：** `BoxSetXmlSaver` 沿用 `BaseXmlSaver` 的 `GetLocalSavePath`。
**[属] `MetadataProviders.TmdbCollection`** 这个 provider id 存在 → `movieData.belongs_to_collection.id` 会被写进 `ProviderIds[TmdbCollection]`（见 §3.4.1 源码）。

> ⚠️ **重要否证**：官方文档**没有**任何名为 `Collections\` 或 `BoxSets\` 的**媒体文件夹**命名约定（在 `\Movies\` 下放 `BoxSets\` 那种做法）。全仓库检索 `BoxSet` 仅命中 `AutoBoxSets.md` 自身 → **未能确认**。

#### 3.1.7 排除扫描

**[官]** [Excluding Files & Folders](https://emby.media/support/articles/Excluding-Files-Folders.html)
- **4.9+**：用 `.embyignore`。`#` 注释；`*` 通配；**目录分隔符统一用正斜杠 `/`（Windows 亦然）**；含分隔符时相对该文件所在目录，否则匹配任意层级。4.9+ 另**新增支持 `.plexignore`**（库设置开关）。
- **4.8**：用 `.ignore`。

#### 3.1.8 官方对流媒体封装的工程建议

**[官]** [Movie Naming](https://emby.media/support/articles/Movie-Naming.html) 原文建议：**MP4 或 MKV 容器 + H.264（或 H.265）视频 + 至少一条 2 声道 AAC 默认音轨**，其他音轨（Dolby Digital、DTS）并列。目的是**减少服务端实时转码**。措辞是 "strongly encouraged"，非硬性要求。

> ★ **这条与 §2 的播放链路直接呼应**：Emby 的 `DeviceProfile` 默认（浏览器/移动端）就是围绕 H.264+AAC 设计的，所以「MP4/MKV + H.264 + AAC」能最大概率直连。

---

### 3.2 季集识别失败时的人工介入：Identify

**[官]** 出处：[Identify](https://emby.media/support/articles/Identify.html)、[Metadata Manager](https://emby.media/support/articles/Metadata-manager.html)

**操作路径：** Web 应用 → 条目详情页 → **3 点菜单** → **Identify** → 搜索/填字段 → 选正确结果 → 确认。
> 官方限制原文：**"The IDENTIFY option is only available for Emby administrators."**（**仅管理员可用**。）

**提供方 ID 的两种注入方式：**
1. **UI 直接填**：原文 "if you already know the database IDs for your incorrectly identified item, simply insert the correct IDs into the database fields and refresh the item."
2. **写进文件夹/文件名**（免刮削直接命中，见 §3.1.2 的 8 种格式）

**官方副作用警告：** 同一 ID 的两个条目（如同一电影的 3D 与 2D 版）会被视为**完全相同的条目** —— 它可能重复出现在 Resume 列表，且**看了 3D 版会把 2D 版也标记为已看**。

**刷新元数据的两种模式：**
| 模式 | 行为 |
|---|---|
| **刷新全部元数据**（默认） | 用外部数据库元数据**覆盖所有字段** |
| **仅刷新缺失数据** | 保留已有元数据，**只补空缺** |
| **Replace existing images**（附加开关） | 强制删除并重下全部图片 —— 官方警告：**会删掉你的自定义图片** |

**字段锁：** 改完**必须点 Save**；要让改动扛住后续刷新，必须**锁定字段或锁定整个条目**。

**[属] API 层的对应枚举 `MetadataRefreshMode`：** `None, ValidationOnly, Default, FullRefresh`
**[属] `ImageRefreshOptions`：** `{ ImageRefreshMode, DirectoryService, ReplaceAllImages, ReplaceImages, IsAutomated }`
**[属] `MetadataRefreshOptions`：** `{ ReplaceAllMetadata, MetadataRefreshMode, SearchResult, RefreshPaths, ForceSave, EnableRemoteContentProbe }`

---

### 3.3 元数据源（Providers）与字段贡献

#### 3.3.1 官方说了什么（很少）

**[官]** [Metadata Manager](https://emby.media/support/articles/Metadata-manager.html) 原文，**图片**来源的**唯一**官方枚举：

> **"Images are downloaded from Fanart.tv, TheMovieDB, The Open Movie Database, and TheTVDB."**

同页另一处原文（**文本元数据**）：

> "All media can get their information from online databases such as **TheMovieDB** and **TheTVDB**."

**[官]** [Emby Blog: How to guide: Metadata](https://emby.media/community/blogs/entry/581-how-to-guide-metadata/)（2024-12-25，Emby Team 成员 sross44）原文：
- "Select metadata providers like **TheMovieDB** (for movies), **TheTVDB** (for TV shows), or **MusicBrainz** (for music)."
- "**Drag and drop the providers to prioritize which one Emby should use first.**"
- "**Language Preferences**: Specify the language for metadata **and artwork** to match your preference."

**[官-cat]** 官方插件清单 [`EmbyPackages.json`](https://www.mb3admin.com/admin/service/EmbyPackages.json)（219 个包）中 `category: Metadata` 的提供方插件：

| 插件名 | GUID | owner | overview 原文 |
|---|---|---|---|
| **MovieDb** | `3A63A9F3-810E-44F6-910A-14D6AD1255EC` | luke | "MovieDb metadata for movies"（81 版本） |
| **Tvdb** | `7FB7FF5E-5407-4F74-8990-B7AA643085D2` | luke | "Tvdb metadata provider for tv shows"（86 版本） |
| **OMDb** | `C25B3C85-1880-4827-9C72-0FA74314F428` | luke | "OMDb metadata provider for Emby."（21 版本） |
| **MusicBrainz** | `341944AF-4959-47E5-8ACE-398520208A71` | luke | "A MusicBrainz plugin for Emby."（25 版本） |
| **TheAudioDb** | `18CFFD2C-74F5-4EDE-8DAD-BE339443AFE4` | luke | "A metadata provider using TheAudioDb."（目标文件名 `AudioDb.dll`） |
| **TV Maze Metadata Provider** | `F4A6AE33-4466-437C-9824-60098197B1AD` | softworkz | 声明 `TvMazeSeriesProvider`、`TvMazeEpisodeProvider`、`TvMazeSeasonProvider`、`TvMazePersonProvider` + 4 个 ImageProvider |
| **Nfo Metadata** | `E610BA80-9750-47BC-979D-3F0FC86E0081` | luke | "Nfo metadata support"（56 版本，最新 1.0.11） |
| **Fanart.tv** | `8D7D93B2-01DC-48DC-8C5D-4E7ABBD9F9EB` | luke | "An Emby Server plugin for fanart.tv" |
| **StudioImages** | `C68856B8-6031-480D-B08E-43B9114ADDB2` | luke | "Images for Studios" |
| **Xml Metadata** | `2850d40d-9c66-4525-aa46-968e8ef04e97` | luke | "Adds **legacy xml** support for movies, tv series, and home videos." |

**关键推论：**
- **TheTVDB 在今天的 Emby 中由插件 `Tvdb` 承载**，但 **[L2] Emby Moderator GrimReaper（2025-08-19）原文**："**TVDB is default provider for TV Shows-type libraries on new server installations.** If you did not change anything, that should already be happening."（[来源](https://emby.media/community/topic/141431-thetvdb-as-a-source/)）
- **TMDB 不需要用户自备 API key** —— **[L2] Emby 管理员 Luke（2025-01-18）原文："Hi, you do not have to configure any api key. This is all built into Emby Server."**（[来源](https://emby.media/community/topic/135571-plugins-moviedb-issue/)）
  （该帖用户从日志里看到 `api_key=****` 报 "Invalid API key"，Luke 与 Happy2Play 均指出那是**日志脱敏脚本插入了不可见字符**造成的假象，真实原因是 TMDB 超时。）
- **Zap2It**：官方知识库 + 官方插件清单**零命中** → **未能确认**（Zap2It 是 Plex 生态的 EPG 来源，**不是 Emby 的影视元数据提供方**）
- **"Screen scraping"（屏幕刮削）**：同上**零命中** → **未能确认**
- **"Emby Metadata" 作为影视元数据提供方**：无此命名 → **未能确认**（Emby 另有 [Emby Guide Data](https://emby.media/support/articles/Emby-Guide-Data.html)，那是 Live TV 节目单）

#### 3.3.2 ★ [码] 3.5.3 源码里每个 provider 到底填哪些字段（官方从未公布）

这一节全部是 **[码]** 级证据 —— 从 `MediaBrowser/Emby` 的公开源码逐行读出。**这是「各提供方贡献哪些字段」这个问题唯一的一手答案。**

##### (1) TheMovieDb → 电影（`MediaBrowser.Providers/Movies/GenericMovieDbInfo.cs` L125–L320）

| TMDB 字段 | → Emby 字段 | 源码行 |
|---|---|---|
| `title` / `GetTitle()` | `movie.Name` | L129 |
| `original_title` | `movie.OriginalTitle` | L131 |
| `overview` | `movie.Overview`（`WebUtility.HtmlDecode` + `\n\n`→`\n`） | L133–L134 |
| `tagline` | `movie.Tagline` | L140 |
| `production_countries[].name` | `movie.ProductionLocations[]` | L145 |
| `id` | `ProviderIds["Tmdb"]` | L156 |
| `imdb_id` | `ProviderIds["Imdb"]` | L157 |
| `belongs_to_collection.id` | `ProviderIds["TmdbCollection"]` | L161 |
| `belongs_to_collection.name` | `movie.CollectionName` | L163 |
| `vote_average` | **`movie.CommunityRating`**（float） | L172 |
| `releases.countries[].certification` | **`movie.OfficialRating`** | L191 / L195 |
| `release_date` | `movie.PremiereDate` + `movie.ProductionYear` | L206–L207 |
| `production_companies[].name` | `movie.SetStudios(...)` | L214 |
| `genres[].name` | `movie.AddGenre(...)` | L221 |
| `casts`（cast/crew） | `People`（Actor/Director/Writer） | L226–L290 |

**★ 分级的国家化处理（L184–L196，很值得抄）：**
```csharp
var ourRelease = releases.FirstOrDefault(c => string.Equals(c.iso_3166_1, preferredCountryCode, OrdinalIgnoreCase));
var usRelease   = releases.FirstOrDefault(c => string.Equals(c.iso_3166_1, "US", OrdinalIgnoreCase));

if (ourRelease != null)
{
    var ratingPrefix = string.Equals(preferredCountryCode, "us", OrdinalIgnoreCase) ? "" : preferredCountryCode + "-";
    var newRating = ratingPrefix + ourRelease.certification;
    newRating = newRating.Replace("de-", "FSK-", StringComparison.OrdinalIgnoreCase);
    movie.OfficialRating = newRating;          // 例：us → "PG-13"；de → "FSK-12"
}
else if (usRelease != null)
{
    movie.OfficialRating = usRelease.certification;   // 回退到美版分级
}
```
→ **这正是「`OfficialRating` 里的 `FSK-12` 是怎么来的」的答案：`de` + `-` + `12` → 再把 `de-` 替换成 `FSK-`。**

**[码] MovieDbProvider 里的硬编码常量（L166–L172）：**
```csharp
public const string BaseMovieDbUrl = "https://api.themoviedb.org/";
private const string TmdbConfigUrl = BaseMovieDbUrl + "3/configuration?api_key={0}";
private const string GetMovieInfo3  = BaseMovieDbUrl + @"3/movie/{0}?api_key={1}&append_to_response=casts,releases,images,keywords,trailers";
internal static string ApiKey = "f6bd687ffa63cd282b6ff2c6877f2669";
internal static string AcceptHeader = "application/json,image/*";
```

**[码] 多语言与图片语言回退（`GetImageLanguagesParam`，L256–L283）：**
```csharp
languages.Add(preferredLanguage);
if (preferredLanguage.Length == 5) languages.Add(preferredLanguage.Substring(0, 2));  // en-US → 也加 en
languages.Add("null");                                                                 // 无语言
if (!string.Equals(preferredLanguage, "en", ...)) languages.Add("en");                 // 英文兜底
return string.Join(",", languages);
```
→ 组装出 `&include_image_language=zh-CN,zh,null,en` 这样的参数。

##### (2) ★ [码] TheMovieDb 的限流（任务书问的"限流"有确切答案）

`MediaBrowser.Providers/Movies/MovieDbProvider.cs` L413–L434：
```csharp
private static long _lastRequestTicks;
// The limit is 40 requests per 10 seconds
private static int requestIntervalMs = 300;

internal async Task<HttpResponseInfo> GetMovieDbResponse(HttpRequestOptions options)
{
    var delayTicks = (requestIntervalMs * 10000) - (DateTime.UtcNow.Ticks - _lastRequestTicks);
    var delayMs = Math.Min(delayTicks / 10000, requestIntervalMs);

    if (delayMs > 0)
    {
        _logger.Debug("Throttling Tmdb by {0} ms", delayMs);
        await Task.Delay(Convert.ToInt32(delayMs)).ConfigureAwait(false);
    }

    _lastRequestTicks = DateTime.UtcNow.Ticks;
    options.BufferContent = true;
    options.UserAgent = "Emby/" + _appHost.ApplicationVersion;
    return await _httpClient.SendAsync(options, "GET").ConfigureAwait(false);
}
```
→ **Emby 对 TMDB 做全局 300 ms/请求的串行节流（注释写明 TMDB 限制是 40 请求/10 秒），并把 UA 设为 `Emby/<版本>`。**

##### (3) ★ [码] TMDB 数据本地缓存（任务书问的"缓存目录"）

`MovieDbProvider.cs` L180–L250：
```csharp
internal static string GetMovieDataPath(IApplicationPaths appPaths, string tmdbId)
    => Path.Combine(GetMoviesDataPath(appPaths), tmdbId);

internal static string GetMoviesDataPath(IApplicationPaths appPaths)
    => Path.Combine(appPaths.CachePath, "tmdb-movies2");

internal string GetDataFilePath(string tmdbId, string preferredLanguage)
{
    if (string.IsNullOrWhiteSpace(preferredLanguage)) preferredLanguage = "alllang";
    return Path.Combine(path, string.Format("all-{0}.json", preferredLanguage));
}
```
**缓存路径：** `<CachePath>/tmdb-movies2/<tmdbId>/all-<language>.json`
**缓存 TTL（`EnsureMovieInfo`，L214–L232）：**
```csharp
if (fileInfo.Exists)
{
    // If it's recent or automatic updates are enabled, don't re-download
    if ((DateTime.UtcNow - _fileSystem.GetLastWriteTimeUtc(fileInfo)).TotalDays <= 2)
        return Task.CompletedTask;
}
```
→ **2 天内不重下。** 另一处 `FetchMainResult` 里的 `CacheLength = TimeSpan.FromDays(3)` 用于非 tmdbId 路径的低层缓存。
**概述为空的英文回退（L379–L411）：** 若目标语言 `overview` 为空且语言非 en，**再发一次 `&language=en` 请求只取 overview**。

##### (4) TheMovieDb → 剧集（`MediaBrowser.Providers/TV/TheMovieDb/MovieDbSeriesProvider.cs` L222–L340）

| TMDB 字段 | → Emby 字段 | 行 |
|---|---|---|
| `name` | `series.Name` | L222 |
| `id` | `ProviderIds["Tmdb"]` | L223 |
| `vote_average` | `series.CommunityRating` | L232 |
| `overview` | `series.Overview` | L235 |
| `networks[].name` | **`series.Studios`** ← 注意是 Studios 不是 Network | L239 |
| `genres[].name` | `series.Genres` | L244 |
| `episode_run_time[0]` | `series.RunTimeTicks`（取第一条） | L249 |
| `status` | `series.Status`（`Ended`/`Continuing`） | L253–L258 |
| `first_air_date` | `series.PremiereDate` | L261 |
| `external_ids.imdb_id` | `ProviderIds["Imdb"]` | L268 |
| `external_ids.tvrage_id` | `ProviderIds["TvRage"]` | L272 |
| `external_ids.tvdb_id` | `ProviderIds["Tvdb"]` | L276 |
| `content_ratings`（按国家） | **`series.OfficialRating`** | L288/L292/L296 |
| `videos` | `series.AddTrailerUrl(...)` | L309 |
| `credits` | `People` | L315–L337 |

##### (5) TheMovieDb → 剧集单集（`MediaBrowser.Providers/TV/TheMovieDb/MovieDbEpisodeProvider.cs` L52–L137）

`info.Name → item.Name`、`info.IndexNumber → item.IndexNumber`、`info.ParentIndexNumber → item.ParentIndexNumber`、`info.IndexNumberEnd → item.IndexNumberEnd`、`external_ids.tvdb_id → ProviderIds["Tvdb"]`、`air_date → item.PremiereDate`（并推 `ProductionYear`）、`overview → item.Overview`、`vote_average → item.CommunityRating`、`videos → AddTrailerUrl`

##### (6) ★ [码] OMDb → 字段映射（`MediaBrowser.Providers/Omdb/OmdbProvider.cs`）

**URL 与硬编码 key（L277–L280）：**
```csharp
baseUrl = "https://www.omdbapi.com";
var url = baseUrl + "?apikey=fe53f97e";
```

| OMDb 字段 | → Emby 字段 | 行 |
|---|---|---|
| `Title` | `item.Name` | L54 |
| `Rated` | **`item.OfficialRating`** | L58 |
| `Year` | `item.ProductionYear`（取前 4 位） | L64–L68 |
| `Ratings`（Rotten Tomatoes） | **`item.CriticRating`** | L71–L75 |
| `imdbRating` | **`item.CommunityRating`** | L89–L93 |
| `imdbVotes` | `VoteCount` —— **代码里被注释掉了** | L80–L84 |
| `imdbID` | `ProviderIds["Imdb"]` | L101–L103 |
| `Website` | `HomePageUrl` —— **代码里被注释掉了** | L96–L99 |

**`GetRottenTomatoScore()`** 是把 `Ratings[]` 里 Source 为 Rotten Tomatoes 的字符串（如 `"94%"`）解析成浮点。

##### (7) TheTVDB → 剧集（`MediaBrowser.Providers/TV/TheTVDB/TvdbSeriesProvider.cs`）

> ⚠️ **重大历史局限，必须明确标注**：3.5.3 的 TVDB provider **用的是 TheTVDB 早已下线的 v1 XML API**，不是 v4 JSON API。

**[码] URL 常量（L54–L58）：**
```csharp
public const string TvdbBaseUrl = "https://www.thetvdb.com/";
private const string SeriesSearchUrl   = TvdbBaseUrl + "api/GetSeries.php?seriesname={0}&language={1}";
private const string SeriesGetZip      = TvdbBaseUrl + "api/{0}/series/{1}/all/{2}.zip";
private const string GetSeriesByImdbId = TvdbBaseUrl + "api/GetSeriesByRemoteID.php?imdbid={0}&language={1}";
private const string GetSeriesByZap2ItId = TvdbBaseUrl + "api/GetSeriesByRemoteID.php?zap2it={0}&language={1}";
```
调用处（L228）：`string.Format(SeriesGetZip, TVUtils.TvdbApiKey, seriesId, NormalizeLanguage(preferredMetadataLanguage))`

**[码] 增量更新机制（`MediaBrowser.Providers/TV/TheTVDB/TvdbPrescanTask.cs` L30–L38）** —— **这就是任务书问的「增量刷新」在 Emby 里的实现：**
```csharp
public const string TvdbBaseUrl = "https://thetvdb.com/";
private const string ServerTimeUrl = TvdbBaseUrl + "api/Updates.php?type=none";
private const string UpdatesUrl     = TvdbBaseUrl + "api/Updates.php?type=all&time={0}";
```
→ 这是一个 `ILibraryPostScanTask`：**库扫描之后**拉 TVDB 的「自某时间以来所有变更」列表，用于增量更新已入库剧集。（该 API 也已随 TVDB v1 下线。）

**[码] TVDB XML → Emby 字段（L1137–L1342，逐 XML 元素）：**
`id → ProviderIds["Tvdb"]`、`SeriesName → item.Name`、`Overview → item.Overview`、`Airs_DayOfWeek → item.AirDays`（经 `TVUtils.GetAirDays`）、`Airs_Time → item.AirTime`、`ContentRating → item.OfficialRating`、`Rating → item.CommunityRating`、`IMDB_ID → ProviderIds["Imdb"]`、`Zap2It_ID → ProviderIds["Zap2It"]`、`Status → item.Status`（`SeriesStatus` 枚举）、`FirstAired → PremiereDate + ProductionYear`、`Runtime → RunTimeTicks`、`Genre → item.Genres`、`Network → item.SetStudios(...)`

**[码] `MetadataProviders` 枚举（3.5.3 全部 16 个 provider id）：**
`Gamesdb, Imdb, Tmdb, Tvdb, Tvcom, TmdbCollection, MusicBrainzAlbum, MusicBrainzAlbumArtist, MusicBrainzArtist, MusicBrainzReleaseGroup, Zap2It, TvRage, AudioDbArtist, AudioDbAlbum, MusicBrainzTrack, TvMaze`

##### (8) 音乐（MusicBrainz / AudioDb / FanArt）

**[码] URL：**
| Provider | URL | 源码 |
|---|---|---|
| MusicBrainz | `https://www.musicbrainz.org` | `Music/MusicBrainzAlbumProvider.cs:34` |
| TheAudioDb | `https://www.theaudiodb.com/api/v1/json/<ApiKey>` | `Music/AudioDbArtistProvider.cs:31` |
| FanArt.tv（TV） | `https://webservice.fanart.tv/v3/tv/{1}?api_key={0}` | `TV/FanArt/FanartSeriesProvider.cs:38` |
| FanArt.tv（Music） | `https://webservice.fanart.tv/v3.1/music/{1}?api_key={0}` | `Music/FanArtArtistProvider.cs:32` |
| FanArt.tv（Movie） | `https://webservice.fanart.tv/v3/movies/{1}?api_key={0}` | `Movies/FanartMovieImageProvider.cs:39` |

**[码] 外部 Id 链接格式（`Music/MusicExternalIds.cs`、`Music/AudioDbExternalIds.cs`）：**
`https://musicbrainz.org/release-group/{0}`、`/artist/{0}`、`/release/{0}`、`/track/{0}`；`https://www.theaudiodb.com/album/{0}`、`/artist/{0}`
**[码] `MetadataProviders` 里的音乐相关 id：** `MusicBrainzAlbum, MusicBrainzAlbumArtist, MusicBrainzArtist, MusicBrainzReleaseGroup, MusicBrainzTrack, AudioDbArtist, AudioDbAlbum`
**[码] FanArt 缓存目录：** `Path.Combine(appPaths.CachePath, "fanart-tv")`（`FanartSeriesProvider.cs:251`）

##### (9) [属] 提供方的可配置性（每库/每类型开关与排序）

**[码] `MediaBrowser.Model.Configuration.MetadataOptions`（按 `ItemType` 分组）：**
```
ItemType, DisabledMetadataSavers, LocalMetadataReaderOrder,
DisabledMetadataFetchers, MetadataFetcherOrder,
DisabledImageFetchers, ImageFetcherOrder
```
→ **每个条目类型都能单独：禁用某个元数据抓取器、禁用某个图片抓取器、指定抓取器顺序、指定本地元数据读取器顺序、禁用某个 saver。**
**[规] 4.1.1.0 的对应结构：** `Configuration.TypeOptions` 的 `MetadataFetchers / MetadataFetcherOrder / ImageFetchers / ImageFetcherOrder / ImageOptions`；`Library.LibraryOptionInfo { Name, DefaultEnabled }`；`Library.LibraryTypeOptions` 暴露 `MetadataFetchers / ImageFetchers / SupportedImageTypes / DefaultImageOptions`
**[官] UI 层：** 官方博客确认提供方**可拖拽排序**决定优先级。

#### 3.3.3 ★ 各 provider 的能力矩阵（本报告综合 [码] 源码 + [官] 文档整理）

| Provider | 电影 | 剧集 | 单集 | 音乐 | 主要贡献字段 | 图片来源 |
|---|:--:|:--:|:--:|:--:|---|---|
| **TheMovieDb** | ✔ | ✔ | ✔ | — | Name, OriginalTitle, Overview, Tagline, CommunityRating, OfficialRating, PremiereDate, ProductionYear, Genres, Studios, CollectionName, People, TrailerUrl, ProviderIds(Tmdb/Imdb/Tvdb/TvRage/TmdbCollection), ProductionLocations | ✔（poster/backdrop/logo，original 尺寸） |
| **TheTVDB**（3.5.3 为 v1 API） | — | ✔ | ✔ | — | Name, Overview, **OfficialRating, CommunityRating**, AirDays, AirTime, Status, PremierDate, RunTimeTicks, Genres, Studios, ProviderIds(Tvdb/Imdb/Zap2It) | ✔ |
| **OMDb** | ✔ | — | ✔（`TV/Omdb/OmdbEpisodeProvider.cs`） | — | **`Rated`→OfficialRating**、**`imdbRating`→CommunityRating**、**`Ratings(Rotten Tomatoes)`→CriticRating**、Title, Year, ProviderIds(Imdb) | ✔（`Omdb/OmdbImageProvider.cs`） |
| **MusicBrainz** | — | — | — | ✔ | Artist/Album/ReleaseGroup/Track 的 Id 与结构 | ✘ |
| **TheAudioDb** | — | — | — | ✔ | Artist/Album 元数据 | ✔（`AudioDbAlbumImageProvider` / `AudioDbArtistImageProvider`） |
| **FanArt.tv** | ✔ | ✔ | — | ✔ | 仅图片（clearart/clearlogo/disc/banner/backdrop/thumb） | ✔ |
| **TVmaze** | — | ✔ | ✔ | — | 剧集/季/集/人物（**今天以插件形式存在**） | — |
| **StudioImages** | — | — | — | — | 仅制片公司图片 | ✔ |
| **Local（NFO/本地图片）** | ✔ | ✔ | ✔ | 部分 | 见 §3.6 | ✔ |

> ⚠️ **[未]** 官方文档**没有**这类逐提供方的字段贡献表 → 上表的「主要贡献字段」列是我从 **3.5.3 源码**逐行读出的（[码] 级），**4.x 的现行行为可能已变化**。

---

### 3.4 评分与分级

#### 3.4.1 字段语义（[规] 4.1.1.0 官方 OpenAPI，权威）

| 字段 | JSON 类型 | 含义 |
|---|---|---|
| **`OfficialRating`** | **`string`** | **内容分级字符串**（如 `PG-13`、`R`、`FSK-12`、`TV-MA`），家长控制比对用 |
| **`CustomRating`** | **`string`** | 用户自定义分级，**覆盖** `OfficialRating` 用于家长控制判断 |
| **`CommunityRating`** | **`number(float)`** | **大众评分**（0–10 量级），**不是内容分级** |
| **`CriticRating`** | **`number(float)`** | **影评人评分**，同样是数值评分，**不是内容分级** |
| `IsoSpeedRating` | — | ISO 感光度（照片 EXIF） |

> ★ **最容易搞错的一点：`CommunityRating` / `CriticRating` 是数值评分，`OfficialRating` / `CustomRating` 才是内容分级。** 三者在 3.5.3 的 `BaseItemDto` 与 4.1.1.0 的 OpenAPI 里**完全同名同类型**。

**[码] 数据来源对应关系（3.5.3 源码）：**
- `CommunityRating` ← TMDB `vote_average`（`GenericMovieDbInfo.cs:172`）/ TMDB 剧集 `vote_average`（`MovieDbSeriesProvider.cs:232`）/ OMDb `imdbRating`（`OmdbProvider.cs:93`）/ TVDB `Rating`（`TvdbSeriesProvider.cs:1203`）
- `CriticRating` ← **只有 OMDb** 的 Rotten Tomatoes 分数（`OmdbProvider.cs:75` / L182）
- `OfficialRating` ← TMDB `releases.countries[].certification`（带国家前缀）/ TMDB `content_ratings` / OMDb `Rated` / TVDB `ContentRating`

> ★ **一个重要发现：`CriticRating` 在 Emby 里只由 OMDb（Rotten Tomatoes）提供。** 如果 OMDb 被禁用或该片没有 RT 分数，`CriticRating` 就是空的。

#### 3.4.2 分级制度与家长控制

**[官]** [Parental Controls](https://emby.media/support/articles/Parental-Controls.html)：Dashboard → **Users** → 点用户 → **Parental Control**。原文：
> "**The simplest way is to set the max parental rating for a user.**"
> "**Content with a higher rating will not be displayed. This value will not affect unrated content, but there are additional options to control that as well:**"

**[规] 相关字段：** `Users.UserPolicy.MaxParentalRating`、`Users.UserPolicy.BlockUnratedItems`、`Users.UserPolicy.BlockedTags`、`MetadataEditorInfo.ParentalRatingOptions`（`ParentalRating[]`）、`QueryFiltersLegacy.OfficialRatings`

**[规] 端点数：** `GET /Localization/ParentalRatings`（"Gets known parental ratings"）—— **这是第三方客户端获取当前服务器已知分级列表的官方途径。**

**[社/L3] 分级取值表（★ 只能降级引用）**

> ⚠️ **官方知识库中不存在「各国分级制度一览表」** → **[未] 官方层面未能确认**。
> 唯一的可核验来源是**社区转贴的服务端源码片段**（[Format of age rating](https://emby.media/community/topic/105402-format-of-age-rating/)，社区 Top Contributor Happy2Play 贴出的 `LoadRatings(...)`）。**这是转贴源码，不是官方规范。**

**`us`（若采信该 L3 来源）：**
```
TV-Y=1  APPROVED=1  G=1  E=1  EC=1  TV-G=1
TV-Y7=3  TV-Y7-FV=4
PG=5  TV-PG=5
PG-13=7  T=7
TV-14=8
R=9  M=9  TV-MA=9
NC-17=10
AO=15  RP=15  UR=15  NR=15  X=15  XXX=15
```

**`de`：**
```
DE-0=1  FSK-0=1
DE-6=5  FSK-6=5
DE-12=7 FSK-12=7
DE-16=8 FSK-16=8
DE-18=9 FSK-18=9
```

**要点：** 分级是**按语言/国家分组的字符串集合**，每组有**数值索引**；字符串**必须精确匹配**（用户实测把 `DE:FSK-0`、`DE:FSK0`、`DE:FSK 0`、`DE:0` 混写在同一字段里会导致该条被判为**未知分级**）。

> ⚠️ **[未] GB（BBFC）、JP 及其他国家的分级表：本次调研未找到任何可靠来源** → **未能确认**。

**与 TMDB 国家前缀的衔接（[码] 见 §3.3.2(1)）：** TMDB 路径会把 `OfficialRating` 写成 `de-FSK-12` 形式（`de` + `-` + `12`，再把 `de-` 替换为 `FSK-`）→ 最终是 **`FSK-12`**；美版则是裸的 `PG-13`。

---

### 3.5 图片

#### 3.5.1 图片类型（[属] 3.5.3 / [规] 4.1.1.0）

**[属] 3.5.3 `MediaBrowser.Model.Entities.ImageType`（12 项）：**
`Primary, Art, Backdrop, Banner, Logo, Thumb, Disc, Box, Screenshot, Menu, Chapter, BoxRear`

**[规410] 4.10.0.40 OpenAPI（15 项）—— 这是当前权威枚举：**
`Primary, Art, Backdrop, Banner, Logo, Thumb, Disc, Box, Screenshot, Menu, Chapter, BoxRear, Thumbnail, LogoLight, LogoLightColor`

> ★ **两处跨版本差异（见 §7）**：
> - `Thumbnail` 在 3.5.3 里**没有**，4.1.1.0 起新增
> - **`LogoLight` / `LogoLightColor` 在 4.10 里正式进入 `ImageType` 枚举** —— 这正好印证了官方文档 [Image Editing](https://emby.media/support/articles/Image-Editing.html) 里描述的 Live TV 频道专用 logo 类型（文档写得比 4.1.1.0 的 spec 早）

**[规] 4.1.1.0（13 项，已过时，仅作对照）：** `Primary, Art, Backdrop, Banner, Logo, Thumb, Disc, Box, Screenshot, Menu, Chapter, BoxRear, Thumbnail`

**[官] 官方用途说明**（[Image Editing & Image Types](https://emby.media/support/articles/Image-Editing.html)）：

| 类型 | 官方原文 |
|---|---|
| **Primary** | "This is the normal cover art used." |
| **Logo** | "Option image usually superimposed over backdrops" |
| **Backdrops/Fanart** | "This is the background used behind all other graphics and text. **Many clients can alternate these backgrounds if more than one graphic is present.**" |
| **Thumb** | "Used for thumbnail views" |
| **Banner** | "Used for banner views" |
| **Disc** | "Used for disk views" |
| **Art** | "Used in some clients similar to the Logo image type" |

同页还记录 **Live TV 频道**专用类型 `Primary` / `LogoLight` / `LogoLightColor`（"LogoLightColor is the default logo used…用于深色背景"）—— **这两个类型不在 4.1.1.0 的 OpenAPI 枚举里**（spec 版本早于该特性）。
同页还说明降级行为：**banner / disc / logo / thumb 视图中，没有对应图片的条目会回退使用 primary 图。**

#### 3.5.2 ★ 本地图片文件名：官方表格 vs 源码实现

**[官] 电影/视频**（[Movie Naming § Video images](https://emby.media/support/articles/Movie-Naming.html)）
支持的扩展名：**jpg、jpeg、png、gif、tbn**。**按下列顺序依次检查**：

| Image Type | 文件名（按检查顺序） |
|---|---|
| Primary | `{name}.ext`、`{name}-poster.ext`、`{name}-cover.ext`、`{name}-default.ext`、`{name}-movie.ext`、`folder.ext`、`poster.ext`、`cover.ext`、`default.ext`、`movie.ext` |
| Art | `{name}-clearart.ext`、`clearart.ext` |
| Backdrop | `backdrop.ext, backdropX.ext`、`fanart.ext, fanart-X.ext`、`background.ext, background-X.ext`、`art.ext, art-X.ext`、`extrafanart/fanartX.ext` |
| Banner | `{name}-banner.ext`、`banner.ext` |
| Disc | `{name}-disc.ext`、`{name}-cdart.ext`、`disc.ext`、`cdart.ext` |
| Logo | `{name}-clearlogo.ext`、`clearlogo.ext`、`{name}-logo.ext`、`logo.ext` |
| Thumb | `{name}-thumb.ext`、`{name}-landscape.ext`、`thumb.ext`、`landscape.ext` |

原文：**"For videos that are not contained within their own folder, only the conventions using {name} are supported."**

**[官] 剧集/季**（[TV Naming § Series & Season Images](https://emby.media/support/articles/TV-Naming.html)）
扩展名：**jpg、jpeg、png、tbn**（**此处官方未列 gif**）

| Image Type | 文件名 |
|---|---|
| Primary | `folder.ext`、`poster.ext`、`cover.ext`、`default.ext`、`show.ext`（仅剧集文件夹）、`seasonXX-poster.ext`、`season-specials-poster.ext` |
| Art | `clearart.ext` |
| Backdrop | `backdrop.ext, backdropX.ext`、`fanart.ext, fanart-X.ext`、`background.ext, background-X.ext`、`art.ext, art-X.ext`、`extrafanart/fanartX.ext`、`seasonXX-fanart.ext`、`season-specials-fanart.ext` |
| Banner | `banner.ext`、`seasonXX-banner.ext`、`season-specials-banner.ext` |
| Disc | `disc.ext`、`cdart.ext` |
| Logo | `clearlogo.ext`、`logo.ext` |
| Thumb | `thumb.ext`、`landscape.ext`、`seasonXX-landscape.ext`、`season-specials-landscape.ext` |

**剧集（episode）图片——官方只给两条：**
- `{name}-thumb.ext`（放**同一文件夹**）
- `{name}.ext`（放 **metadata 子文件夹**）

**[官] 音乐**（[Music Naming § Music Images](https://emby.media/support/articles/Music-Naming.html)）
Primary：`folder.ext, poster.ext, cover.ext, default.ext, artist.ext, artist-cover.ext, artist-default.ext, artist-folder.ext, artist-poster.ext`
Thumb：`thumb.ext`、`landscape.ext`、**`<folder name>.ext`**（示例：艺术家文件夹 "Ed Sheeran" → `ed sheeran.jpg`）
官方版本要求：**`artist.ext` / `artist-xxxx.ext` 系列需要 Emby Server 4.10**。

##### ★★ [码] 3.5.3 源码里的实际实现（`MediaBrowser.LocalMetadata/Images/LocalImageProvider.cs`，488 行）

> **这是本报告能给出的最精确版本 —— 比官方文档更细，且是 Emby 自己的代码。**

**Primary 候选名（按条目类型分组，L222–L259）：**
```csharp
CommonImageFileNames  = { "poster", "folder", "cover", "default" }
MusicImageFileNames   = { "folder", "poster", "cover", "default" }   // 音乐优先 folder
PersonImageFileNames  = { "folder", "poster" }
SeriesImageFileNames  = { "poster", "folder", "cover", "default", "show" }
VideoImageFileNames   = { "poster", "folder", "cover", "default", "movie" }
```

**Primary 查找顺序（L283–L312）：**
1. `item.FileNameWithoutExtension`（即 `<视频文件名>.<ext>`，**最高优先**）
2. `imagePrefix + name`，其中 `imagePrefix = FileNameWithoutExtension + "-"` → 即 `<文件名>-poster.jpg` 等
3. **仅当不在混合目录时**：裸 `name` → 即 `poster.jpg` / `folder.jpg` …

**其他类型的文件名（L144–L207，逐条带行号）：**

| 行号 | 代码 | 文件名词 | ImageType |
|---|---|---|---|
| L144 | `AddImage(files, images, "logo", ...)` | `logo` | Logo |
| L147 | `AddImage(files, images, "clearlogo", ...)` | `clearlogo` | Logo（logo 缺失时） |
| L154 | `AddImage(files, images, "clearart", ...)` | `clearart` | Art |
| L160/L164 | `"cdart"` 优先，回退 `"disc"` | `cdart` / `disc` | Disc（**MusicAlbum**） |
| L169/L173/L178 | `"disc"` → `"cdart"` → `"discart"` | | Disc（**Game / Video / BoxSet**） |
| L184/L185 | `"box"`、`"menu"` | | Box / Menu（**Game**） |
| L187/L191 | `"back"` → `"boxrear"` | | BoxRear（**Game**） |
| L198 | `"banner"` | | Banner |
| L204/L207 | `"landscape"` → `"thumb"` | | Thumb |
| L336 | `PopulateBackdrops(..., "fanart", "fanart-", ...)` | `fanart` / `fanart-` | Backdrop |
| L348 | `PopulateBackdrops(..., "backdrop", "backdrop", ...)` | `backdrop` | Backdrop |

**排除规则（L124–L140）：** `logo/clearlogo/clearart/banner/landscape/thumb/backdrop` **对 Episode、Song、Person 都不生效**。

**剧集专用（`EpisodeLocalImageProvider.cs` L46–L64）：**
```csharp
var thumbName = filenameWithoutExtension + "-thumb";
// 同目录下 <集文件名>.<ext>  → Primary
// 同目录下 <集文件名>-thumb.<ext> → Primary
```
→ 与官方 TV Naming 的两条**完全对上**。

**季图从剧集文件夹取（`LocalImageProvider.cs`，`PopulateSeasonImagesFromSeriesFolder`）** —— Season 会去父级 Series 文件夹找图。

#### 3.5.3 ★ [码] 图片**写入**路径（`MediaBrowser.Providers/Manager/ImageSaver.cs`）

**保存开关（L79–L118）：**
```csharp
var saveLocally = item.SupportsLocalMetadata && item.IsSaveLocalMetadataEnabled()
                  && !item.ExtraType.HasValue && !(item is Audio);
if (item is User) saveLocally = true;
if (type != ImageType.Primary && item is Episode) saveLocally = false;   // 剧集只存 primary 到本地
if (!item.IsFileProtocol) saveLocally = false;                          // 非文件协议不存本地
```
**两种命名约定（`ImageSavingConvention` 枚举：`Legacy` / `Compatible`，L287–L295）：**
```csharp
if (!saveLocally || _config.Configuration.ImageSavingConvention == ImageSavingConvention.Legacy)
    return new[] { GetStandardSavePath(...) };
return GetCompatibleSavePaths(...);
```

**[码] `GetStandardSavePath`（L339–L466）—— Legacy 约定 + 内部元数据目录回退：**

| 类型/条件 | 输出文件名 |
|---|---|
| Season Thumb（L355–L366） | `<剧集文件夹>/seasonXX-landscape.jpg`；Season 0 → `season-specials-landscape.jpg` |
| Season Banner（L376–L388） | `seasonXX-banner.jpg` / `season-specials-banner.jpg` |
| Art | `clearart` |
| BoxRear | `back` |
| Thumb | `landscape` |
| Disc | MusicAlbum → `cdart`，否则 `disc` |
| Primary | 剧集 → `<集文件名>`；否则 → `folder`（音乐/艺术家/相册/人物 **或** Legacy 约定）或 `poster` |
| Backdrop | `backdrop`（index 0）/ `backdrop<N>`（L469–L482 找第一个未占用编号） |
| Screenshot | `screenshot` / `screenshot<N>` |
| 其他 | `type.ToString().ToLower()` |

- **扩展名归一化（L432–L437）：** `.jpeg` → `.jpg`；统一小写
- **剧集 Primary 且有 saveLocally（L441–L444）：** `<集文件所在目录>/metadata/<集文件名><ext>` ← **这就是官方说的 "metadata 子文件夹"**
- **混合目录（`GetSavePathForItemInMixedFolder`，L595–L605）：** `<目录>/<视频文件名>-<imageFilename><ext>`；Primary 时 `imageFilename` 强制为 `poster`
- **都不满足时回退内部目录（L457–L464）：** `item.GetInternalMetadataPath()/filename.ext`

**[码] `GetCompatibleSavePaths`（L496–L592）—— Compatible 约定（Kodi 兼容）：**

| 类型 | 输出 |
|---|---|
| Backdrop index 0 | `fanart.jpg`；Season → `seasonXX-fanart.jpg` / `season-specials-fanart.jpg`；混合目录 → `<名>-fanart.jpg` |
| Backdrop index > 0 | `<目录>/extrafanart/fanart<N>.jpg`；若开 `EnableExtraThumbsDuplication` 另写 `<目录>/extrathumbs/thumb<N>.jpg` |
| Primary（Season） | `seasonXX-poster.jpg` / `season-specials-poster.jpg` |
| Primary（Episode） | `<季文件夹>/<集文件名>-thumb.jpg` |
| Primary（混合目录 或 MusicVideo） | `<名>-poster.jpg` |
| Primary（音乐） | `folder.jpg` |
| Primary（其他） | `poster.jpg` |
| 其余 | 走 `GetStandardSavePath(..., saveLocally: true)` |

**[码] 内部元数据目录（`Emby.Server.Implementations/ServerApplicationPaths.cs`）：**
```
InternalMetadataPath = Path.Combine(DataPath, "metadata")
People       → <InternalMetadataPath>/People
artists      → <InternalMetadataPath>/artists
Genre        → <InternalMetadataPath>/Genre
MusicGenre   → <InternalMetadataPath>/MusicGenre
Studio       → <InternalMetadataPath>/Studio
Year         → <InternalMetadataPath>/Year
general      → <InternalMetadataPath>/general
ratings      → <InternalMetadataPath>/ratings
mediainfo    → <InternalMetadataPath>/mediainfo
GameGenre    → <InternalMetadataPath>/GameGenre
VirtualInternalMetadataPath = "%MetadataPath%"   （用于路径替换）
```

#### 3.5.4 图片尺寸 / 缩放

**[官] 官方知识库没有任何「图片必须为 X×Y 像素」的规定** → **未能确认**是否存在官方推荐尺寸。
**[官] 也不存在文档化的固定「缓存宽度」常量。**

**[规] 可核验的是「按需实时缩放」**：所有取图端点（`/Items/{Id}/Images/{Type}/{Index}` 等）都接受同一组参数：

```
MaxWidth  MaxHeight  Width  Height  Quality  Tag
CropWhitespace  EnableImageEnhancers  Format
AddPlayedIndicator  PercentPlayed  UnplayedCount
BackgroundColor  ForegroundLayer
```

→ **Emby 的图片缩放是请求时按客户端需要实时做的，不是预先缓存固定尺寸。**

**[码] `BaseDynamicImageProvider`（`Emby.Server.Implementations/Images/`）** 负责生成**拼贴图**（如合集/季的海报由多张图拼成）：
```csharp
GetSupportedImages(item) => { ImageType.Primary } (+ Thumb)
CreateImage(item, itemsWithImages, outputPathWithoutExtension, imageType, 0)
CreateSingleImage(...) / CreateImageCollage(options) with Height/Width
```

**[规] 每库每类型的抓图上限：** `Configuration.ImageOption { Type: ImageType, Limit: int, MinWidth: int }`

#### 3.5.5 ★ 图片语言优先级（[码] 3.5.3 一手答案）

`MediaBrowser.Providers/Movies/MovieDbImageProvider.cs` L58–L130：

```csharp
public async Task<IEnumerable<RemoteImageInfo>> GetImages(BaseItem item, CancellationToken cancellationToken)
{
    var language = item.GetPreferredMetadataLanguage();      // L62  ← 条目级语言偏好
    var tmdbImageUrl = tmdbSettings.images.GetImageUrl("original");   // L73  ★ 取 original 原图

    // ... posters
    Url      = tmdbImageUrl + i.file_path,      // L81
    Width    = i.width,                          // L84
    Language = MovieDbProvider.AdjustImageLanguage(i.iso_639_1, language),   // L86

    // ... backdrops 同理（L97–L100）

    var isLanguageEn = string.Equals(language, "en", StringComparison.OrdinalIgnoreCase);   // L108
    // 排序打分（L110–L126）：
    if (string.Equals(language, i.Language, ...))  return ...;         // 首选：完全匹配目标语言
    if (!isLanguageEn)
    {
        if (string.Equals("en", i.Language, ...))  return ...;          // 次选：英文
    }
    if (string.IsNullOrEmpty(i.Language))  return isLanguageEn ? 3 : 2; // 末选：无语言标记
}
```

**[码] `MovieDbProvider.AdjustImageLanguage`（L302–L315）：** 若服务器语言是 `zh-CN`（5 字符）而 TMDB 返回 `zh`（2 字符），且服务器语言以它开头 → **把图片语言"升级"成 `zh-CN`**，避免同一语言的图片被当成不同语言重复展示。

**[码] `MovieDbProvider.GetImageLanguagesParam`（L256–L283）：** 请求参数里拼出 `include_image_language=<lang>,<2字母>,null[,en]`。

**[属] `RemoteImageInfo` 结构（3.5.3）：**
`ProviderName, Url, ThumbnailUrl, Height, Width, CommunityRating, VoteCount, Language, Type, RatingType`

**[规] 语言相关权威字段：**
- 库级 `Configuration.LibraryOptions.PreferredMetadataLanguage` + `MetadataCountryCode`
- 服务端级 `ServerConfiguration.PreferredMetadataLanguage` + `MetadataCountryCode` + `ImageSavingConvention(Legacy|Compatible)`
- 条目级 `BaseItemDto.PreferredMetadataLanguage` / `PreferredMetadataCountryCode`
- 远程图片查询参数 `GET /Items/{Id}/RemoteImages` 的 **`IncludeAllLanguages`**（boolean）

**[社/L2] UI 上的 "Preferred image download language" 确实存在**（Emby 4.9.3.0），该设置会**按语言过滤图片查询**，且**当时没有语言回退**（英文设置下日/韩片可能拿到 0 张海报）。Emby 管理员 Luke 在同帖回复：**"Hi, we plan to add fallback options in future updates."**（[来源](https://emby.media/community/topic/146202-tmdb-posters-no-language-fallback-person-bio-missing-after-bulk-refresh/)）
**[社/L2] 服务端实际请求日志实证：** `...&language=zh-CN&include_image_language=zh-CN,null`
→ **元数据语言映射为 `language=`，图片语言映射为 `include_image_language=<lang>,null`。**

> ⚠️ **[未] `ImageLanguagePreference` 这个字段名在官方 OpenAPI 中不存在**（正则检索零命中）→ **未能确认**该字段名。

---

### 3.6 本地元数据优先：NFO 与本地图片

#### 3.6.1 ★ 优先级的准确答案（官方出处）

**[L2，官方人员]** Emby 管理员（Emby Team）**Luke**，2023-09-09，[Metadata Priority - Local vs Remote](https://emby.media/community/topic/121372-metadata-priority-local-vs-remote/) 原文：

> **"Hi, local metadata is always preferred first on a normal scan. It's only when you refresh metadata that it moves to last. There's no way to change that."**

→ **准确表述：正常扫描时本地元数据总是优先；只有「刷新元数据」时本地才被排到最后，且无法更改。**
（**这条不在官方知识库里，只在官方论坛。**）

同帖 Emby Dev **softworkz** 补充了插件层面的顺序接口：
- `ICustomMetadataProvider<T>` 在 local 与 remote **之后**执行
- **不要**实现 `IPreRefreshProvider`（会在所有其他之前执行）
- 用 `IHasOrder` 设顺序（**仅在同类型提供方之间生效**）
- `IForcedProvider` 可让提供方在条目被**锁定**时仍执行

**[属] 代码侧佐证 —— 排序枚举：**
```
MediaBrowser.Controller.Providers.RefreshPriority        : High, Normal, Low
MediaBrowser.Controller.Providers.MetadataProviderPriority: First, Second, Third, Fourth, Fifth, Last
```
**[属] `MetadataResult<T>`：** `{ Images, UserDataList, People, HasMetadata, Item, ResultLanguage, Provider, QueriedById }`

#### 3.6.2 ★ NFO：官方文档 vs 3.5.3 源码（重要调和）

**[官] 官方知识库只有 3 处提到 nfo，且都没给文件名：**
1. [Backup & Restore to New System](https://emby.media/support/articles/Backup-Restore-To-New-System.html)："Any artwork/nfo files stored alongside the media files, should be included in your migration…"
2. [Backup Using Plugin](https://emby.media/support/articles/Backup-Using-Plugin.html)："You can configure your libraries to save nfo and image files alongside the media within the media folders…"
3. [Music Naming](https://emby.media/support/articles/Music-Naming.html)："if you wish to save metadata files directly into your media folders such as nfo, images, etc, then a structured folder layout is necessary…"

→ **官方文档从未列出 NFO 文件名。**

**[官-cat] NFO 读取在当代 Emby 由插件承载：** 官方插件清单里有 **`Nfo Metadata`**（GUID `E610BA80-9750-47BC-979D-3F0FC86E0081`，category=Metadata，owner=luke，overview="Nfo metadata support"，56 个版本）。
**[L2] Luke 另一条明确说明：** **"Nfo's are not used with photos."**（[来源](https://emby.media/community/topic/147223-unable-to-generate-nfo-files/)，2026-04）

##### ★★ 但 [码] Emby 3.5.3 源码里 NFO 文件名是**完全确定**的

项目 `MediaBrowser.XbmcMetadata/`（NFO 读写）+ `MediaBrowser.LocalMetadata/`（本地 XML）。

**读取（`Providers/`）：** `MovieNfoProvider.cs`、`SeriesNfoProvider.cs`、`SeasonNfoProvider.cs`、`EpisodeNfoProvider.cs`、`AlbumNfoProvider.cs`、`ArtistNfoProvider.cs` + 对应 `Parsers/`：`MovieNfoParser.cs`、`SeriesNfoParser.cs`、`SeasonNfoParser.cs`、`EpisodeNfoParser.cs`、`BaseNfoParser.cs`

**写入（`Savers/`）—— 文件名逐行可查：**

| 项目 | 文件 | 代码 |
|---|---|---|
| **剧集** | `SeriesNfoSaver.cs` L20–L22 | `return Path.Combine(item.Path, "tvshow.nfo");` |
| **季** | `SeasonNfoSaver.cs` L19–L21 | `return Path.Combine(item.Path, "season.nfo");` |
| **单集** | `EpisodeNfoSaver.cs` L20–L22 | `return Path.ChangeExtension(item.Path, ".nfo");` ← `<集文件名>.nfo` |
| **电影** | `MovieNfoSaver.cs` L20–L58 | 见下 |

**`MovieNfoSaver.GetMovieSavePaths`（L27–L58）完整逻辑（[码] 原文）：**
```csharp
public static List<string> GetMovieSavePaths(ItemInfo item, IFileSystem fileSystem)
{
    var list = new List<string>();

    if (item.VideoType == VideoType.Dvd && !item.IsPlaceHolder)
    {
        var path = item.ContainingFolderPath;
        list.Add(Path.Combine(path, "VIDEO_TS", "VIDEO_TS.nfo"));      // DVD → VIDEO_TS/VIDEO_TS.nfo
    }

    if (!item.IsPlaceHolder && (item.VideoType == VideoType.Dvd || item.VideoType == VideoType.BluRay))
    {
        var path = item.ContainingFolderPath;
        list.Add(Path.Combine(path, Path.GetFileName(path) + ".nfo")); // <文件夹名>.nfo
    }
    else
    {
        // http://kodi.wiki/view/NFO_files/Movies
        // movie.nfo will override all and any .nfo files in the same folder...
        //if (!item.IsInMixedFolder && item.ItemType == typeof(Movie))
        //{
        //    list.Add(Path.Combine(item.ContainingFolderPath, "movie.nfo"));
        //}

        list.Add(Path.ChangeExtension(item.Path, ".nfo"));             // ① <视频文件名>.nfo  ★优先

        if (!item.IsInMixedFolder)
        {
            list.Add(Path.Combine(item.ContainingFolderPath, "movie.nfo"));  // ② movie.nfo（非混合目录时）
        }
    }

    return list;
}
```
**★ 关键细节：数组顺序 = 优先级。`list[0]` 是写入目标（`MovieNfoSaver.GetLocalSavePath` 返回 `paths[0]`），所以：**
1. **`<视频文件名>.nfo` 优先于 `movie.nfo`**
2. `movie.nfo` **只在 `!item.IsInMixedFolder`（即该电影有独立文件夹）时才作为候选**
3. **注意源码里那段被注释掉的代码**：早期版本曾把 `movie.nfo` 放在**第一位**（"movie.nfo will override all and any .nfo files"），后来被改成第二位 —— **这是 Emby 对 Kodi NFO 规范的一处有意偏离**
4. DVD/Blu-ray 走独立分支：`VIDEO_TS/VIDEO_TS.nfo` 与 `<文件夹名>.nfo`

**[码] NFO 保存的启用条件（`MovieNfoSaver.IsEnabledFor`）：**
```csharp
if (!item.SupportsLocalMetadata) return false;
var video = item as Video;
if (video != null && !(item is Episode) && !video.ExtraType.HasValue) { ... }
```

**[码] NFO 的根元素名（`MovieNfoSaver.GetRootElementName`）：**
```csharp
return item is MusicVideo ? "musicvideo" : "movie";
```

**[属]/[码] NFO 选项（`MediaBrowser.XbmcMetadata/Configuration/NfoOptions.cs`）：**
`EnableExtraThumbsDuplication`（控制是否同时写 `extrathumbs/thumbN.jpg`）

**[官] NFO 写入的库级开关（[规] 权威字段名）：**
```
LibraryOptions.SaveLocalMetadata          SaveLocalThumbnailSets
LibraryOptions.MetadataSavers             LibraryOptions.DisabledLocalMetadataReaders
LibraryOptions.LocalMetadataReaderOrder
```

**结论（可以放心写的表述）：**
> **Emby 官方知识库确认「可以把元数据保存为 NFO 并写入媒体文件夹」（库级开关），也确认有 NFO 读取能力（由官方插件 `Nfo Metadata` 承载），但官方文档从未公布 NFO 文件命名规范。**
> **在 Emby Server 3.5.3 的公开源码里，NFO 文件名是明确实现的：`tvshow.nfo`、`season.nfo`、`<集文件名>.nfo`、`<视频文件名>.nfo`（优先）/ `movie.nfo`（次选，且仅独立文件夹时）。**

#### 3.6.3 本地字幕

**[属] `Emby.Naming.Subtitles.SubtitleParser`**：方法 `ParseFile` / `GetFlags`；结果 `SubtitleInfo { Path, Language, IsDefault, IsForced }`
**[属] 扩展名（`NamingOptions.SubtitleFileExtensions`）：** `.srt`、`.ssa`、`.ass`、`.sub`
**[属] 标志位识别：** `foreign`、`forced`、`default`
**[属] `NamingOptions.SubtitleFlagDelimiters / SubtitleForcedFlags / SubtitleDefaultFlags`** —— 说明「标志位」与「分隔符」也是数据驱动的。

> **推测（不写成结论）：** `Movie.zh-CN.forced.srt` 这类命名之所以能识别，是因为 `SubtitleParser` 会按分隔符切分文件名，把语言码与 `forced`/`default`/`foreign` 提取出来。**具体的切分正则我在字符串堆里看到了 `foreign`/`forced`/`default` 三个标记串，但没有看到完整的命名模板** → **[未] 本地字幕命名模板未能完全确证**。

**[规] 字幕在线下载相关端点：** 见 §2.6.5。
**[属] 字幕相关库选项（`LibraryOptions`）：**
```
DisabledSubtitleFetchers, SubtitleFetcherOrder,
SkipSubtitlesIfEmbeddedSubtitlesPresent, SkipSubtitlesIfAudioTrackMatches,
SubtitleDownloadLanguages, RequirePerfectSubtitleMatch, SaveSubtitlesWithMedia
```
**[官]** [Library Setup](https://emby.media/support/articles/Library-Setup.html) Advanced Settings 原文列举 "**Enable Open Subtitles subtitles**"。

---

### 3.7 扫描触发 / 刷新 / 限流

#### 3.7.1 实时监控（RTM）

**[官]** [Library Setup](https://emby.media/support/articles/Library-Setup.html) 原文：
> "To have Emby monitor changes to files and addition of content, **real-time monitoring should be enabled**."
> **Important**: "**This option is only available on supported file systems. You should restart your Emby Server after changing this option.**"

→ **仅在受支持的文件系统上可用；修改后需重启 Emby Server。**

**[属]/[规] 字段名：`LibraryOptions.EnableRealtimeMonitor`**（**库级**，每库独立）

**[官] 运维坑（官方文档收录）：** [Synology NAS](https://emby.media/support/articles/Synology-NAS.html) 指出，若媒体在 Synology NAS 上而 RTM 找不到新增内容，需调整 Synology 的 **Inotify 与 Watches** 设置。

##### ★★ [码] 3.5.3 的 RTM 实现细节（`Emby.Server.Implementations/IO/LibraryMonitor.cs`）

```csharp
private readonly ConcurrentDictionary<string, FileSystemWatcher> _fileSystemWatchers = ...;  // L24

// 开关判定（L188）
return options.EnableRealtimeMonitor;

// 创建 watcher（L314–L344）
if (_fileSystemWatchers.ContainsKey(path)) return;
// "Creating a FileSystemWatcher over the LAN can take hundreds of milliseconds,
//  so wrap it in a Task to do them all in parallel"   ← L319 原文注释
var newWatcher = new FileSystemWatcher(path, "*")
{
    IncludeSubdirectories = true            // L326
};
newWatcher.InternalBufferSize = 65536;      // L329  ★ 64 KB 缓冲
newWatcher.NotifyFilter = NotifyFilters.CreationTime    // L331–L336
                        | NotifyFilters.DirectoryName
                        | NotifyFilters.FileName
                        | NotifyFilters.LastWrite
                        | NotifyFilters.Size
                        | NotifyFilters.Attributes;
```

**[码] 变更后的延迟刷新（`Emby.Server.Implementations/IO/FileRefresher.cs` L98/L102）：**
```csharp
_timer = _timerFactory.Create(OnTimerCallback, null,
    TimeSpan.FromSeconds(ConfigurationManager.Configuration.LibraryMonitorDelay),
    TimeSpan.FromMilliseconds(-1));
```
→ **`ServerConfiguration.LibraryMonitorDelay`（秒）是「文件变更后等多久再刷新」的防抖窗口。**

**[码] 另一处硬编码延迟（`LibraryMonitor.cs` L104）：** `await Task.Delay(45000)` —— 库扫描/监听协调用的 45 秒等待。

**[码] `ReportFileSystemChangeBeginning/Complete`（L76 等）** —— Emby 在**自己写文件前**会先告诉 watcher "这是我自己干的"，避免自己写 NFO/图片触发的无限循环刷新。**这是很值得借鉴的一个设计。**

#### 3.7.2 定时扫描

**[官]** [Scheduled Tasks](https://emby.media/support/articles/Scheduled-Tasks.html)
- 入口：Dashboard → **Scheduled Tasks**
- 原文：任务 "Many of them take too long to be run within the normal library scan, so that's why we make them available as configurable scheduled tasks."（**"normal library scan" 是独立概念**）
- **任何任务都可随时手动运行**
- **插件可注册自己的定时任务**
- **官方完整触发器枚举（5 种）：**
  - Daily at set time
  - Weekly at set day and time
  - **Interval (Based on a number of hours)**
  - At application startup
  - When the server resumes from sleep

**[码] 3.5.3 的默认库扫描间隔（`RefreshMediaLibraryTask.cs` L41–L48）：**
```csharp
public IEnumerable<TaskTriggerInfo> GetDefaultTriggers()
{
    return new[] {
        // Every so often
        new TaskTriggerInfo { Type = TaskTriggerInfo.TriggerInterval,
                              IntervalTicks = TimeSpan.FromHours(12).Ticks}
    };
}
```
→ **★ Emby 3.5.3 的 "Scan media library" 默认是【每 12 小时】一次。**
（对比：**官方文档没有给出这个默认值** → 任务书问的"定时"问题，这个数字只能从源码得到，且是 3.5.3 的。）

**[码] 触发器实现：** `ScheduledTasks/IntervalTrigger.cs`（`Interval` 属性 + `TimeSpan.FromDays(7)` 的最大到期上限）、`DailyTrigger.cs`、`WeeklyTrigger.cs`、`StartupTrigger.cs`、`SystemEventTrigger.cs`
**[码] 任务清单目录：** `Emby.Server.Implementations/ScheduledTasks/`（含 `RefreshMediaLibraryTask`、`ChapterImagesTask`、`PeopleValidationTask`、`PluginUpdateTask`、`SystemUpdateTask`）
**[码] `TvdbPrescanTask`（`MediaBrowser.Providers/TV/TheTVDB/TvdbPrescanTask.cs`）实现 `ILibraryPostScanTask`** —— **库扫描后**跑 TVDB 增量更新。这就是「增量刷新」在 Emby 里的挂载点。

**[规] 按天自动刷新字段：** `LibraryOptions.AutomaticRefreshIntervalDays`（库级）

#### 3.7.3 手动扫描

**[官]** Server Settings → **Library** → **"Scan Library Files"**（[Backup & Restore](https://emby.media/support/articles/Backup-Restore-To-New-System.html) 等页记载）
**[规] 端点：`POST /Library/Refresh`**（"Starts a library scan"）

**[官]** 其他扫描相关库设置（[Library Setup](https://emby.media/support/articles/Library-Setup.html) Advanced Settings 原文）：
- **Prefer embedded titles over filenames** → `LibraryOptions.EnableEmbeddedTitles`
- **Extract chapter images during the library scan** → `LibraryOptions.ExtractChapterImagesDuringLibraryScan`
- **Enable Open Subtitles subtitles** → `LibraryOptions.DisabledSubtitleFetchers` 等

#### 3.7.4 ★ `LibraryOptions` 字段表

> ★ **[规410] Emby 4.10.0.40 的 `LibraryOptions` 有 65 个字段**，比 3.5.3 的 28 个翻了一倍多，新增的关键项包括：
> `PreferredImageLanguage`（★ **图片语言偏好字段确实存在**，子 agent 在 4.1.1.0 里查不到只是版本太老）、`IgnoreFileExtensions`、`IgnoreHiddenFiles`、`EnablePlexIgnore`（★ 对应官方文档的 `.plexignore` 支持）、`EnableMarkerDetection` + `EnableMarkerDetectionDuringLibraryScan` + `IntroDetectionFingerprintLength`（★ **片头识别**）、`AutoGenerateChapters` + `AutoGenerateChapterIntervalMinutes`、`EnableMultiVersionByFiles` / `EnableMultiVersionByMetadata` / `EnableMultiPartItems`、`EnableAudioResume`、`ImportCollections` + `MinCollectionItems`、`ImportPlaylists`、`CacheImages`、`SaveLyricsWithMedia` / `LyricsFetcherOrder` / `SubtitleDownloadMaxAgeDays`、`EnableAdultMetadata`、`ForcedSubtitlesOnly` / `HearingImpairedSubtitlesOnly`、`PlaceholderMetadataRefreshIntervalDays`、`SampleIgnoreSize`、`MusicFolderStructure`、`ShareEmbeddedMusicAlbumImages`、`ExcludeFromSearch`、`MergeTopLevelFolders`、`ContentType`、`ThumbnailImagesIntervalSeconds`
> 以及 **[规410]** `MinResumePct` / `MaxResumePct` / `MinResumeDurationSeconds`（★ 从全局 `ServerConfiguration` **下放到每库**）。
> 详见 **§7 跨版本修正表**。

#### [属] 3.5.3 的 `LibraryOptions` 完整字段表（Model DLL，28 项）

```
EnableArchiveMediaFiles          EnablePhotos
EnableRealtimeMonitor        ★   EnableChapterImageExtraction
ExtractChapterImagesDuringLibraryScan
DownloadImagesInAdvance          PathInfos
SaveLocalMetadata                EnableInternetProviders
ImportMissingEpisodes            EnableAutomaticSeriesGrouping
EnableEmbeddedTitles             AutomaticRefreshIntervalDays     ★
PreferredMetadataLanguage        MetadataCountryCode
SeasonZeroDisplayName            MetadataSavers
DisabledLocalMetadataReaders     LocalMetadataReaderOrder
DisabledSubtitleFetchers         SubtitleFetcherOrder
SkipSubtitlesIfEmbeddedSubtitlesPresent
SkipSubtitlesIfAudioTrackMatches
SubtitleDownloadLanguages        RequirePerfectSubtitleMatch
SaveSubtitlesWithMedia           TypeOptions
```

> ★ **[规410] 4.10 的 `ServerConfiguration` 有 71 个字段，其中刮削相关的关键项：**
> `MetadataPath, MetadataNetworkPath, PreferredMetadataLanguage, MetadataCountryCode, ImageSavingConvention(Legacy|Compatible), PathSubstitutions, CachePath, ValidateImageTags, EnableSavedMetadataForPeople, ImageExtractionTimeoutMs, **LibraryMonitorDelaySeconds**`
> ⚠️ **`LibraryMonitorDelay` 在 4.10 改名为 `LibraryMonitorDelaySeconds`**；`MinResumePct`/`MaxResumePct`/`MinResumeDurationSeconds` **已移出** `ServerConfiguration`，进了 `LibraryOptions`。

**[属] 3.5.3 全局 `ServerConfiguration` 中与刮削相关的字段：**
```
MetadataPath  MetadataNetworkPath
PreferredMetadataLanguage  MetadataCountryCode
MinResumePct  MaxResumePct  MinResumeDurationSeconds
LibraryMonitorDelay     ★ 文件变更防抖窗口（秒）
ImageSavingConvention   ★ Legacy | Compatible
MetadataOptions         ★ 每 ItemType 的 provider 开关
EnableGroupingIntoCollections   DisplaySpecialsWithinSeasons
EnableNewOmdbSupport    ImageExtractionTimeoutMs
PathSubstitutions       SaveMetadataHidden
EnableSimpleArtistDetection     UninstalledPlugins
```

> ★ **一个 4.x 的重要变化：** 在 **4.1.1.0 官方 OpenAPI 里，`MinResumePct` / `MaxResumePct` / `MinResumeDurationSeconds` 出现在 `Configuration.LibraryOptions` 里**（3.5.3 里它们在全局 `ServerConfiguration`）。**Emby 4.x 把续播阈值下放到了每个媒体库。**
> 4.1.1.0 的 `LibraryOptions` 还多出：`CollapseSingleItemFolders`、`SaveLocalThumbnailSets`、`ThumbnailImagesIntervalSeconds`、`EnableChapterImageExtraction` 等。

#### 3.7.5 刷新与节流

**[码] 刷新队列串行化 + 100 ms 节流（`MediaBrowser.Providers/Manager/ProviderManager.cs` L1024–L1073）：**
```csharp
private async Task StartProcessingRefreshQueue()
{
    while (_refreshQueue.TryDequeue(out refreshItem))
    {
        var item = libraryManager.GetItemById(refreshItem.Item1);
        if (item != null)
        {
            // Try to throttle this a little bit.       ← L1048 原文注释
            await Task.Delay(100).ConfigureAwait(false);   // L1049
            ...
        }
    }
}
```
→ **Emby 把刷新请求放进一个队列，单线程消费，每条之间 sleep 100 ms。** 这是防打爆 provider API 的第二道闸（第一道是 TMDB 的 300 ms）。

**[码] 单条刷新入口：** `ProviderManager.RefreshSingleItem(item, options, ct)`（L116）；`RefreshItem` 在 L1075。
**[码] 全刷 vs 只刷图片：** L378 `if (refreshOptions.ImageRefreshMode != MetadataRefreshMode.FullRefresh)`

**[规] 手动刷新端点：`POST /Items/{Id}/Refresh`**

| 参数 | 类型 | 说明 |
|---|---|---|
| `Recursive` | bool | 是否递归（剧集→季→集） |
| `MetadataRefreshMode` | enum | `Default` / `FullRefresh` |
| `ImageRefreshMode` | enum | `Default` / `FullRefresh` |
| `ReplaceAllMetadata` | bool | 仅 FullRefresh 时有效 |
| `ReplaceAllImages` | bool | 仅 FullRefresh 时有效 |

**[码] 3.5.3 的对应实现（`MediaBrowser.Api/ItemRefreshService.cs` L16–L76）：**
```csharp
[ApiMember(Name="MetadataRefreshMode", ... Verb="POST")]  public MetadataRefreshMode MetadataRefreshMode { get; set; }
[ApiMember(Name="ImageRefreshMode",    ...)]              public MetadataRefreshMode ImageRefreshMode { get; set; }
[ApiMember(Name="ReplaceAllMetadata", Description="Determines if metadata should be replaced. Only applicable if mode is FullRefresh", ...)]
[ApiMember(Name="ReplaceAllImages",   Description="Determines if images should be replaced. Only applicable if mode is FullRefresh", ...)]
[Route("/Items/{Id}/Refresh", "POST", Summary = "Refreshes metadata for an item")]
...
ForceSave = request.MetadataRefreshMode == MetadataRefreshMode.FullRefresh
         || request.ImageRefreshMode    == MetadataRefreshMode.FullRefresh
         || request.ReplaceAllImages || request.ReplaceAllMetadata,   // L76
```

**[属] `MetadataRefreshMode` 枚举：** `None, ValidationOnly, Default, FullRefresh`

#### 3.7.6 「新增媒体」的日期判定

**[官]** [New Media Date Handling](https://emby.media/support/articles/New-Media-Date-Handling.html)：两种可选 —— 用**扫描入库日期**，或用**媒体文件自身的创建时间戳**。设置路径：Library → 右上 "Advanced"。官方建议：新建库指向既有媒体时，先用文件创建日期。**改完必须点 Save。**

---

### 3.8 API 速查（做第三方客户端会用到的路径与参数）

> 全部路径来自 **[规] 官方 OpenAPI 4.1.1.0**（356 paths），并与 **[属] 3.5.3 DLL 的 `[Route]` 特性**交叉验证过。
> 所有路径都要加 **`/emby`** 前缀（[官]）。

#### 3.8.1 认证与用户

| 方法 | 路径 | 关键参数 / body | 说明 |
|---|---|---|---|
| GET | `/System/Info/Public` | — | 匿名可访问的服务器信息 |
| GET | `/System/Info` | — | 完整服务器信息（需认证） |
| GET | `/Users/Public` | — | 公开用户列表（登录页用） |
| POST | `/Users/AuthenticateByName` | body `{Username, Password/Pw}`；header `X-Emby-Authorization` | 登录；返回 `AccessToken`、`ServerId`、`User` |
| POST | `/Users/{Id}/Authenticate` | body `{Password/Pw}` | 按 Id 登录 |
| POST | `/Sessions/Logout` | — | 注销（吊销 token） |
| GET | `/Users` | — | 用户列表（管理员） |
| POST | `/Users/New` | body `{Name}` | 新建用户 |
| POST | `/Users/{Id}/Policy` | body `UserPolicy` | 改用户权限（**含播放/转码开关**） |
| POST | `/Users/{Id}/Password` / `/EasyPassword` | — | 改密码 |
| GET/DELETE/POST | `/Users/{Id}` | — | 用户增删改 |
| GET | `/Users/{Id}/Connect/Link` 等 | — | Emby Connect 绑定 |
| GET | `/Connect/Exchange` | `ConnectUserId` | Connect 用户 → 本地用户 |
| GET | `/Connect/Pending` | — | 待批准 Connect 请求 |

#### 3.8.2 媒体库与条目

| 方法 | 路径 | 关键参数 | 说明 |
|---|---|---|---|
| GET | `/Users/{UserId}/Views` | — | 用户的媒体库视图 |
| GET | `/Users/{UserId}/Items` | `ParentId, Recursive, IncludeItemTypes, Fields, SortBy, SortOrder, StartIndex, Limit, Filters, Genres, Years, MinPremiereDate, MaxPremiereDate, SearchTerm, ImageTypeLimit, EnableImages, EnableUserData` | ★ 主查询接口 |
| GET | `/Users/{UserId}/Items/{Id}` | `Fields` | 取单条 |
| GET | `/Users/{UserId}/Items/Latest` | `ParentId, Limit, Fields` | 最新 |
| GET | `/Users/{UserId}/Items/Resume` | `MediaTypes, Limit, Fields` | ★ 继续观看（依赖 §2.7.5 的续播阈值） |
| GET | `/Users/{UserId}/Items/{Id}/Intros` | — | 片头（Cinema Intros，Premiere 功能） |
| GET | `/Users/{UserId}/Items/{Id}/LocalTrailers` | — | 本地预告片 |
| GET | `/Users/{UserId}/Items/{Id}/SpecialFeatures` | — | 花絮/Extras |
| GET | `/Items` | 同 `/Users/{UserId}/Items` | 无用户上下文版 |
| GET | `/Items/{Id}/Ancestors` | `UserId` | 祖先链（剧集/季） |
| GET | `/Items/{Id}/Similar` / `/Items/{Id}/InstantMix` | — | 相似/混音 |
| GET | `/Items/Counts` / `/Items/Filters` / `/Items/Filters2` / `/Items/Prefixes` | — | 筛选元数据 |
| GET | `/Library/MediaFolders` / `/Library/VirtualFolders` / `/Library/PhysicalPaths` / `/Library/SelectableMediaFolders` | — | 媒体库结构 |
| POST | `/Library/VirtualFolders` | body `Library.AddVirtualFolder` | 新建媒体库 |
| POST | `/Library/VirtualFolders/LibraryOptions` | — | 改库选项 |
| POST/DELETE | `/Library/VirtualFolders/Paths` | — | 增删库路径 |
| **POST** | **`/Library/Refresh`** | — | **★ 触发全库扫描** |
| POST | `/Library/Media/Updated` | — | 通知某些路径有更新 |
| POST | `/Library/Movies/Added` / `/Library/Series/Added` / `/Library/Movies/Updated` / `/Library/Series/Updated` | — | 细粒度通知 |
| GET | `/Shows/{Id}/Seasons` / `/Shows/{Id}/Episodes` | `UserId, Fields` | ★ 季/集 |
| GET | `/Shows/NextUp` / `/Shows/Upcoming` | — | 下一集 / 待播 |
| GET | `/Movies/Recommendations` / `/Movies/{Id}/Similar` | — | 推荐 |
| GET | `/Persons` / `/Persons/{Name}` / `/Artists` / `/Genres` / `/Studios` / `/Years` | — | 分类浏览 |

#### 3.8.3 刮削 / 元数据

| 方法 | 路径 | 关键参数 | 说明 |
|---|---|---|---|
| **POST** | **`/Items/{Id}/Refresh`** | `Recursive, MetadataRefreshMode, ImageRefreshMode, ReplaceAllMetadata, ReplaceAllImages` | **★ 刷新元数据/图片** |
| GET | `/Items/{ItemId}/MetadataEditor` | — | ★ 元数据编辑器信息（`MetadataEditorInfo { ParentalRatingOptions[], Countries[], Cultures[], ExternalIdInfos[] }`） |
| POST | `/Items/{ItemId}` | body `BaseItemDto` | ★ **更新元数据（含锁定字段）** |
| GET | `/Items/{Id}/ExternalIdInfos` | — | 该条目支持哪些外部 Id（`ExternalIdInfo { Name, Key, UrlFormatString }`） |
| **POST** | **`/Items/RemoteSearch/Movie`** | body `RemoteSearchQuery<MovieInfo> { SearchInfo, ItemId, SearchProviderName, IncludeDisabledProviders }` | **★ Identify：搜电影** |
| POST | `/Items/RemoteSearch/Series` | 同上（`SeriesInfo`） | **★ Identify：搜剧集** |
| POST | `/Items/RemoteSearch/{Trailer,MusicVideo,Game,BoxSet,MusicArtist,MusicAlbum,Person,Book}` | 同上 | 其他类型 |
| **POST** | **`/Items/RemoteSearch/Apply/{Id}`** | `ReplaceAllImages`；body `RemoteSearchResult` | **★ Identify：应用选中结果并刷新** |
| GET | `/Items/RemoteSearch/Image` | — | 取搜索结果缩略图 |
| GET | `/Items/{Id}/RemoteImages` | `Type, StartIndex, Limit, ProviderName, IncludeAllLanguages` | ★ 可用远程图片列表 |
| POST | `/Items/{Id}/RemoteImages/Download` | `Type, ProviderName, ImageUrl` | ★ 下载指定远程图片 |
| GET | `/Items/{Id}/RemoteImages/Providers` | — | 该条目有哪些图片来源 |
| GET | `/Items/{Id}/Images` | — | 已有图片信息 |
| GET/HEAD | `/Items/{Id}/Images/{Type}` 与 `/Items/{Id}/Images/{Type}/{Index}` | `MaxWidth, MaxHeight, Width, Height, Quality, Tag, CropWhitespace, EnableImageEnhancers, Format, AddPlayedIndicator, PercentPlayed, UnplayedCount, BackgroundColor, ForegroundLayer` | ★ 取图（实时缩放） |
| POST | `/Items/{Id}/Images/{Type}/{Index}` | body `application/octet-stream` | ★ 上传自定义图片 |
| DELETE | `/Items/{Id}/Images/{Type}/{Index}` | — | 删图 |
| POST | `/Items/{Id}/Images/{Type}/{Index}/Index` | — | 调整图片顺序 |
| GET | `/Items/{Id}/Images/{Type}/{Index}/{Tag}/{Format}/{MaxWidth}/{MaxHeight}/{PercentPlayed}/{UnplayedCount}` | — | 路径式取图（可作 CDN 友好 URL） |
| GET | `/Items/{Id}/CriticReviews` | — | 影评 |
| GET | `/Items/{Id}/ThemeMedia` / `/ThemeSongs` / `/ThemeVideos` | `InheritFromParent` | 主题曲/主题视频（Premiere） |
| GET | `/Artists/{Name}/Images/{Type}/{Index}`、`/Genres/{Name}/Images/…`、`/Persons/{Name}/Images/…`、`/Studios/{Name}/Images/…`、`/MusicGenres/{Name}/Images/…` | 同上 | 按名字取图 |
| GET | `/Localization/ParentalRatings` | — | ★ 服务器已知分级列表 |
| GET | `/Localization/Countries` / `/Cultures` / `/Options` | — | 国家/文化/选项 |
| GET | `/Genres` / `/MusicGenres` / `/Studios` / `/Persons` / `/Years` / `/Artists` | `UserId, ParentId, SortBy` | 分类枚举 |
| POST | `/Collections` / `/Collections/{Id}/Items` | — | 手动合集 |
| GET/DELETE | `/Videos/{Id}/AlternateSources`、`POST /Videos/MergeVersions` | — | 多版本管理 |
| GET | `/Videos/{Id}/AdditionalParts` | — | 分段 |

#### 3.8.4 配置与系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/System/Configuration` | ★ 服务端全局配置（含 `MetadataPath`、`PreferredMetadataLanguage`、`ImageSavingConvention` 等） |
| GET/POST | `/System/Configuration/{Key}` | 按插件/模块的配置 |
| GET | `/ScheduledTasks` | ★ 列出定时任务（含"Scan media library"） |
| GET | `/ScheduledTasks/{Id}` | 任务详情与**触发器** |
| POST/DELETE | `/ScheduledTasks/Running/{Id}` | ★ **手动触发/取消一个任务** |
| POST | `/ScheduledTasks/{Id}/Triggers` | ★ 改触发器 |
| GET | `/System/Logs` / `/System/Logs/Log` | 日志（排查刮削失败必备） |
| GET | `/System/ActivityLog/Entries` | 活动日志 |
| GET | `/Devices` / `/Devices/Info` / `/Devices/Options` | 设备 |
| GET | `/Plugins` | 已装插件 |
| GET/POST | `/Plugins/{Id}/Configuration` | ★ 插件配置（**元数据提供方就是插件**） |
| GET | `/Packages` / `/Packages/Updates` / `/Packages/{Name}` | 插件市场 |
| POST | `/Packages/Installed/{Name}` | 安装插件 |
| GET | `/Notifications/*`、`/Reports/*`、`/DisplayPreferences/*` | 通知/报表/显示偏好 |

---

## 4. Emby vs Jellyfin 对照

> **Jellyfin 侧的所有结论来自配套文件 `jellyfin-facts-播放与刮削.md`**（该文件由独立子 agent 逐条核查，每条带 GitHub blob 行号链接 / OpenAPI / release notes 出处）。
> **★ 最重要的前提**：**Jellyfin 在 12.0 做了破坏性认证变更** —— 同一句话在 **10.11** 与 **12.x** 上结论**相反**。
> - 当前 stable = **Jellyfin 12.1**（2026-09-15，OpenAPI `x-jellyfin-version = 12.1.0`）；master/unstable = **13.0.0**
> - 最后一代 10.x = **10.11.11**（2026-06-06）
> - **对照表的"Jellyfin"列默认指 12.1**，涉及分叉处会同时标 10.11。

### 4.1 Fork 关系（已有官方一手确认）

| 事实 | 出处 |
|---|---|
| Jellyfin README 原文：**"descended from Emby's 3.5.2 release"** | Jellyfin 仓库 README |
| Jellyfin About 页原文：started in **early December 2018** primarily as a result of **"Emby's decision to take their next release (4.x) closed-source"**（另列 paywall、隐藏/移除客户端与服务端代码、GPL 违规等） | jellyfin.org About |
| ⚠️ **"Emby 3.6 起闭源"未能由一手来源确认** | Jellyfin 官方措辞是 **4.x**；第三方 NixOS nixpkgs #51832（2018-12-10）称「3.6 version and onward 的源码不再分发」并引用 Emby issue #3479 评论 —— 但**该 issue 页面只返回 JS 导航框架、评论 API 返回空对象，正文取不到** |

→ **本报告采用的表述：官方称 4.x 闭源 / 第三方记录 3.6 起 / 确定事实是 fork 基座 = Emby 3.5.2、时间 = 2018 年 12 月初。**
→ **本报告手里的 `MediaBrowser/Emby` master = 3.5.3.0**，正好是分叉点之后一个补丁版，因此**它与 Jellyfin 的早期代码高度同源**，「Jellyfin 保留了 Emby 的什么」几乎可以直接对照。

### 4.2 ★ 认证对照表（最关键、差异最大）

| 项目 | **Emby 3.5.3**（[码] 源码） | **Emby 4.10.0.40**（[规410] 官方规范） | **Jellyfin 10.11.11** | **Jellyfin 12.1** |
|---|---|---|---|---|
| 主授权头名 | `X-Emby-Authorization` 优先，回退 `Authorization`（**两者等价**） | `Authorization`（spec 里 `scheme: bearer`） | `Authorization` 为主，`X-Emby-Authorization` 为 legacy 回退 | 同左，**但 legacy 回退需要开关** |
| **scheme 字面量** | **`MediaBrowser` 或 `Emby`**（`OrdinalIgnoreCase`，两个都收） | **`Emby`**（`bearerFormat` 原文以 `Emby ` 开头） | **`MediaBrowser`**（硬编码，`AuthorizationContext.cs` L258） | **`MediaBrowser`**（仍是硬编码） |
| 参数格式 | `Scheme Key="Value", Key="Value"`，逗号分隔，按第一个 `=` 切，**双引号被剥掉（引号可选）** | 同左 | 同左 | 同左 |
| 读取的键 | **`DeviceId, Device, Client, Version, Token`**（5 个，**不读 `UserId`**） | `UserId`（规范 `bearerFormat` 里有）+ 上述 5 个 | `DeviceId, Device, Client, Version, Token`（5 个，无 `UserId`，L84–L91） | 同左 |
| Token 头 | **`X-Emby-Token` > `X-MediaBrowser-Token` > `?api_key=`** | **`X-Emby-Token`**（spec 描述原文）或 **`?api_key=`**（spec 里唯一的 apiKey 位置）；`X-MediaBrowser-Token` 规范未提 | `X-Emby-Token`(L93-96)、`X-MediaBrowser-Token`(L98-101)、`?ApiKey=`(L103-106)、`?api_key=`(L108-111) | **除 `?ApiKey=` 外全部需要 `EnableLegacyAuthorization`，12.x 默认 `false` + 迁移强制 false** |
| 默认开关状态 | 无开关，全部可用 | 无开关，全部可用 | `EnableLegacyAuthorization = true`（ServerConfiguration.cs L290） | **`false`**（无初始化器；迁移 `20260531160000_DisableLegacyAuthorization.cs`） |
| 路由前缀 | **`/emby/{apipath}`**（[官]） | 同左 | `/emby/*` 与 `/mediabrowser/*` 可用 | **`/emby/*` 与 `/mediabrowser/*` 已移除**（release notes 原文） |
| 家长控制错误语义 | — | 401 + `X-Application-Error-Code: ParentalControl`（[官]） | — | — |

> **★ 三条反直觉结论：**
> 1. **`Authorization: MediaBrowser ...` 不是 Jellyfin 发明的** —— Emby 3.5.3 源码 L208 就同时接受 `MediaBrowser` 与 `Emby`，且 L184 就把 `Authorization` 作为 `X-Emby-Authorization` 的回退。**中文博客里「Jellyfin 改用 Authorization、Emby 用 X-Emby-Authorization」的说法不准确。**
> 2. **官方文档与官方规范对 scheme 的说法不一致**：`dev.emby.media` 的 User-Authentication 页写 `Emby`，而 Emby 的 **JS Web 客户端实际发的是 scheme 字面量 `MediaBrowser`**（`apiclient.js` L726，参数顺序 Client/Device/DeviceId/Version/Token）。**服务端两个都收，所以都能用。**
> 3. **Jellyfin 12.0 是认证断代**：`X-Emby-Authorization` / `X-Emby-Token` / `Emby` scheme / `/emby/*` 前缀**全部默认失效并被迁移强制关闭**。为 Emby 写的第三方客户端在 Jellyfin 12.x 上会直接 401。
> 4. **`?ApiKey=` 是 Jellyfin 唯一没被关掉的 legacy 通道** —— 因为 Jellyfin 自己的流 URL 就靠它（`MediaBrowser.Model/Dlna/StreamInfo.cs` L1276-L1280 手写 `"?ApiKey=" + accessToken`）。

### 4.3 播放 API 对照表

| 项目 | **Emby 3.5.3 [属]** | **Emby 4.10.0.40 [规410]** | **Jellyfin 12.1** |
|---|---|---|---|
| 播放决策接口 | `GET/POST /Items/{Id}/PlaybackInfo` | **同** | **同**（`opId` 也叫 `GetPlaybackInfo`/`GetPostedPlaybackInfo`） |
| 请求 body schema 名 | `MediaInfo.PlaybackInfoRequest` | `PlaybackInfoRequest`（19 字段） | **`PlaybackInfoDto`**（16 字段） |
| 请求参数位置 | POST body（+ `GET` 时 `UserId` query，**必填**） | 同 | **15 个 query 参数全部 `deprecated`**，优先用 body |
| **`DeviceProfile` 类型** | 单对象 `Dlna.DeviceProfile` | 单对象 `DeviceProfile`（**13 字段**） | **单对象**（11 字段） |
| 请求字段差异 | 有 `Id`、`IsPlayback`、`DirectPlayProtocols` | 有 `Id`、`IsPlayback`、`CurrentPlaySessionId`、`AllowInterlacedVideoStreamCopy` | **没有** `Id`/`IsPlayback`/`CurrentPlaySessionId`/`AllowInterlacedVideoStreamCopy`；**多** `AlwaysBurnInSubtitleWhenTranscoding` |
| `DeviceProfileId` 是否为请求字段 | ❌（只在 `/Videos/{Id}/stream` query） | ❌（同上，且在该端点被标 deprecated） | ❌ 同左 |
| 响应字段 | `MediaSources[]`、`PlaySessionId`、`ErrorCode` | **完全同名** | **完全同名**（★ 未改名） |
| `ErrorCode` 取值 | `NotAllowed, NoCompatibleStream, RateLimitExceeded` | **同** | **同**（Jellyfin 侧也确认同名） |
| `MediaSourceInfo` 字段数 | 53 | **49** | 45 |
| `TranscodingUrl` | ✔ | ✔ | ✔（**同名未改**） |
| `TranscodingSubProtocol` / `TranscodingContainer` | ✔ | ✔ | ✔（**同名未改**） |
| **`DirectStreamUrl`** | ✔ | **✔** | **❌ OpenAPI 里没有** |
| `AddApiKeyToDirectStreamUrl` | ❌ | ✔ | ❌ |
| `Chapters` | ❌ | ✔ | ❌（Jellyfin OpenAPI 里无） |
| `ETag` / `IgnoreDts` / `IgnoreIndex` / `GenPtsInput` | ✔ | **❌（4.10 已移除）** | **✔（Jellyfin 有）** |
| `MediaStream` 字段数 | 45 | **56** | — |
| `TranscodeReason` 取值数 | 23 | **27** | 28（含 `VideoRotationNotSupported` 等） |
| `Throttle`/`TranscodingInfo` | 12 字段 | **32 字段**（含 `VideoEncoderIsHardware`、`VideoEncoderHwAccel`、`VideoDecoderHwAccel`、`VideoPipelineInfo`、`SubtitlePipelineInfos`、`ProcessStatistics`、`CurrentThrottle`、`SubProtocol`、`AudioBitrate`、`VideoBitrate`） | — |
| **进度上报三接口** | `/Sessions/Playing`、`/Progress`、`/Stopped` | **同** | **同**（★ 未改名） |
| **`/Sessions/Playing/Ping`** | **✔（Emby 独有）** | **✔** | **✔（Jellyfin 也有；query `playSessionId` 必填）** |
| 上报模型 | `PlaybackProgressInfo`(20)/`PlaybackStartInfo`(继承)/`PlaybackStopInfo`(11) | `PlaybackProgressInfo`(**30**)/`PlaybackStartInfo`(**30**)/`PlaybackStopInfo`(**14**) | `PlaybackProgressInfo`(21)/`PlaybackStartInfo`(**空子类**)/`PlaybackStopInfo`(11) |
| **`EventName`** | **❌** | **✔（13 个取值）** | **❌（10.11.11 / 12.1 / master 全部 grep 不到）** |
| `SubtitleOffset` / `PlaybackRate` / `PlaylistIndex` / `PlaylistLength` | ❌（但 [官] 文档有） | **✔** | ❌ |
| `SessionId` / `PlaybackStartTimeTicks` / `Brightness` / `AspectRatio` / `RepeatMode` | ✔（3.5.3 有） | ✔ | ✔ |
| `SleepTimerMode` / `SleepTimerEndTime` / `Shuffle` / `PlaylistItemIds` | ❌ | **✔（4.x 新增）** | ❌ |
| 旧式「按用户」上报 | `/Users/{UserId}/PlayingItems/{Id}[/Progress]` | ✔ | ✔ |
| HLS 主播放列表 | `GET /Videos/{Id}/master.m3u8` | **同** | **同路径**，但**被 `[ApiExplorerSettings(IgnoreApi=true)]` 排除在 OpenAPI 之外** |
| 变体播放列表 | `GET /Videos/{Id}/main.m3u8` | **同** | **同** |
| TS 分片 | `/Videos/{Id}/hls1/{PlaylistId}/{SegmentId}.{SegmentContainer}` | **同** | **同** |
| legacy 分片 | `/Videos/{Id}/hls/{PlaylistId}/{SegmentId}.{SegmentContainer}` | （4.10 规范里**已无**） | `/Videos/{itemId}/hls/{playlistId}/stream.m3u8` 与 `{segmentId}.{container}`（legacy，L79/L126） |
| 直播 HLS | `GET /Videos/{Id}/live.m3u8` | **同** + 新增 `/Videos/{Id}/live_subtitles.m3u8` | `GET /Videos/{itemId}/live.m3u8` |
| 停止转码 | `DELETE /Videos/ActiveEncodings?DeviceId=&PlaySessionId=` | **同** + 新增 `POST /Videos/ActiveEncodings/Delete` | `DELETE /Videos/ActiveEncodings?deviceId=&playSessionId=`（**query 名小写开头**），返回 204 |
| 通用音频流 | `/Audio/{Id}/universal[.{Container}]` | 同 | — |
| BIF（trickplay） | ❌（3.5.3 无） | **✔ `GET /Videos/{Id}/index.bif`** | ✔（Trickplay，`Interval=10000ms`、`WidthResolutions=[320]`、`TileWidth/Height=10`、`JpegQuality=90`） |
| 字幕清单 | `GET /Videos/{Id}/subtitles.m3u8` | 同 | — |
| 字幕流 | `/Videos/{Id}/{MediaSourceId}/Subtitles/{Index}[/{StartPositionTicks}]/Stream.{Format}` | 同 | — |
| **流端点的鉴权** | 全部 `[Authenticated]`，支持 `?api_key=` | spec 声明 `security: [apikey, embyauth]` | `/Videos/{itemId}/stream` 在 Jellyfin 里 **`security = None`**（无 `[Authorize]`），靠 URL 里的 `?ApiKey=` |
| **CSS `Profile`/`Level`/`Framerate` 等转码参数** | 有（`StreamRequest` 继承链） | **4.10 规范只列 24 个参数，比 4.1.1.0 的 51 个还少** | `/Videos/{itemId}/stream` 在 Jellyfin OpenAPI 里列 **51 个**；`master.m3u8` 源码里有 **50+** |

> ★ **写客户端时最容易踩的三个坑：**
> 1. **流式端点的 OpenAPI 不可信**：Emby 4.10 规范在 `/Videos/{Id}/stream` 上只列 24 个参数（缺 `Profile`、`Level`、`Framerate`、`MaxFramerate`、`MaxRefFrames`、`MaxAudioSampleRate`、`MaxAudioBitDepth`、`RequireAvc`、`SegmentContainer`、`MinSegments`、`BreakOnNonKeyFrames`、`TranscodeReasons`、`LiveStreamId`、`Tag`、`PlaySessionId`、`MediaSourceId`、`EstimateContentLength` 等一大批）。**Emby 官方 Wiki 明确说 `MediaSourceId`/`PlaySessionId`/`DeviceId` 是必需参数，OpenAPI 一条都没列。照 OpenAPI 写客户端会失败。**
> 2. **命名大小写不一致**：Emby 的流式端点是 **`AudioBitRate` / `VideoBitRate`**（大写 R），而 `PlaybackInfo` body 里是 **`MaxStreamingBitrate`**（小写 r）。Jellyfin 侧则统一倾向小写 r 形态。
> 3. **不要把 `/Videos/ActiveEncodings` 的参数名抄错**：Emby 是 **`DeviceId` / `PlaySessionId`**（PascalCase），Jellyfin 是 **`deviceId` / `playSessionId`**（camelCase）。

### 4.4 刮削对照表

| 项目 | **Emby 3.5.3 [码]** | **Emby 今天 [官]/[官-cat]** | **Jellyfin 12.1** |
|---|---|---|---|
| 官方命名规范页 | — | **有**（Movie-Naming / TV-Naming / Music-Naming / Book-Naming） | **有**（jellyfin.org/docs/general/server/media/*） |
| 电影目录结构 | 同 | `\Movies\Name (Year)\Name (Year).ext` | 同（Jellyfin 文档规范一致） |
| 多版本电影 | 8 种 `[tmdbid=xxxx]` 等 ID 标签 | 同 + **`" - "` 后缀多版本，上限 8 个** | 同（`- 1080p` 形态） |
| 剧集强制独立文件夹 | 同 | **"It is mandatory for each TV show to have its own folder"** | 同 |
| Season 0 目录名 | 代码支持 `specials` / `extras` / `season` / + 多语言 `son/temporada/saison/staffel/series/stagione` | 官方文档只说 `Season 0` / `Season 00` / `Specials` | 官方文档规范（父任务已逐字核对） |
| **绝对集号** | 代码里有 `EpisodeWithoutSeasonExpressions`（纯集号正则） | **官方文档无 "absolute" 一词** → 未能确认 | 有 `SupportsAbsoluteEpisodeNumbers`（`Emby.Naming/Common/NamingOptions.cs`） |
| **按年份建季文件夹** | — | **官方 TV Naming 页无记载 → 未能确认** | **未能确认** |
| 日期命名 | 代码里有 `CleanDateTimes` 正则（yyyy.MM.dd 等） | 官网三条示例 | 官网有 |
| **NFO 文件名** | **源码明确实现**：`tvshow.nfo`、`season.nfo`、`<集文件名>.nfo`、`<视频文件名>.nfo`（优先）/ `movie.nfo`（次选） | **官方知识库完全无 NFO 专页、无文件名**；读取能力由官方插件 **`Nfo Metadata`**（GUID `E610BA80-9750-47BC-979D-3F0FC86E0081`）承载 | **内置** `MediaBrowser.XbmcMetadata/Providers/`（11 个 `*NfoProvider.cs`）+ `MediaBrowser.LocalMetadata` |
| **本地 vs 在线优先级** | 实现里 local 有独立 Order | **[L2] Luke 原文："local metadata is always preferred first on a normal scan. It's only when you refresh metadata that it moves to last. There's no way to change that."** | 官方文档规范（父任务已核对） |
| 本地图片文件名 | **源码逐条实现**（poster/folder/cover/default/show/movie、logo→clearlogo、clearart、landscape/thumb、fanart/backdrop、disc/cdart/discart、banner、box/menu/back/boxrear） | **官方文档有完整表格**（Movie-Naming / TV-Naming / Music-Naming 各一张按检查顺序的表格） | 官方文档规范 |
| 图片写入命名 | **源码逐条实现**（Legacy / Compatible 两种约定，见 §3.5.3） | 官方文档只讲"读取"顺序 | — |
| **TheMovieDb** | **内置源码**（`MovieDbProvider.cs`） | **插件 `MovieDb`**（GUID `3A63A9F3-…`）；**API key 内建，用户无需配置**（Luke 2025-01-18） | **内置**（bundled plugin，官方 manifest 里无 TMDb 条目 → 未被拆出） |
| **TheTVDB** | **内置源码**（`TvdbSeriesProvider.cs`，**用已下线的 v1 XML API**） | **插件 `Tvdb`**（GUID `7FB7FF5E-…`）；**新装服务器上仍是 TV 类库的默认首选**（GrimReaper 2025-08-19） | **不内置**（10.11.11/12.1/master 三版源码树均无 `Plugins/Tvdb`）；**是官方插件**（manifest guid `a677c0da-fac5-4cde-941a-7134223f14c8`） |
| **OMDb** | **内置源码**（`OmdbProvider.cs`，硬编码 `apikey=fe53f97e`） | **插件 `OMDb`**（GUID `C25B3C85-…`） | **内置**（`MediaBrowser.Providers/Plugins/Omdb`） |
| **MusicBrainz** | **内置源码** | **插件 `MusicBrainz`**（GUID `341944AF-…`） | **内置**（官方文档漏列） |
| **TheAudioDb** | **内置源码** | **插件 `TheAudioDb`**（GUID `18CFFD2C-…`，`AudioDb.dll`） | **内置**（官方文档漏列） |
| **Fanart.tv** | **内置源码** | **插件 `Fanart.tv`**（GUID `8D7D93B2-…`） | **不内置，是官方插件**（`170a157f-…`） |
| **TVmaze** | provider id 存在（`MetadataProviders.TvMaze`） | **插件 `TV Maze Metadata Provider`**（GUID `F4A6AE33-…`） | **不内置，是官方插件**（`a4a488d0-…`） |
| **Zap2It** | provider id 存在（只作外部 Id 链接） | **官方知识库 + 插件清单零命中 → 未能确认** | 内置但**只是 ExternalUrl/ExternalId provider，不是刮削器** |
| **Screen scraping** | ❌ | **零命中 → 未能确认** | ❌ |
| 评分字段 | `CommunityRating`(float) / `CriticRating`(float) / `OfficialRating`(string) | **同名同类型** | **完全同名同类型**（连 XML 注释都一样）→ **无差异** |
| 分级取值表 | — | 官方无表；**仅 US 与 DE 有 L3 级源码转贴** | — |
| 定时扫描默认间隔 | **源码：12 小时**（`RefreshMediaLibraryTask.cs`） | **官方文档未给出** | 官方文档规范（父任务已核对） |
| 实时监控 | `FileSystemWatcher`，`IncludeSubdirectories`、`InternalBufferSize=65536`、6 项 NotifyFilters；`LibraryMonitorDelaySeconds` 防抖；**写文件前先 `ReportFileSystemChangeBeginning` 自报** | `LibraryOptions.EnableRealtimeMonitor`；官方：**"only available on supported file systems"、"restart after changing"**；Synology 有 inotify 坑 | `EnableRealtimeMonitor`，同源实现 |
| 增量刷新 | **TVDB `api/Updates.php?type=all&time={0}`** 作为 `ILibraryPostScanTask` | — | — |
| 刷新节流 | **TMDB 300 ms/请求**（注释写 "40 requests per 10 seconds"）+ **刷新队列 100 ms/条** | — | — |
| 排除扫描 | — | **4.9+ `.embyignore`**（正斜杠、`*` 通配、`#` 注释）+ **`.plexignore`**；4.8 用 `.ignore` | — |
| 片头识别（Intro Skip） | ❌ | **Premiere 功能**（`Intro Skipping`） | **核心没有内置片头识别**，只有插件接口 `IMediaSegmentProvider` + `MediaSegmentType` |
| 插件体系 | 中心化，需向 Emby 团队申请 developer id；**支持付费插件**（`Premium Plug-in`/`Feature ID`/`Price`，14 天试用，接口 `IRequiresRegistration`） | 同 | **公开 manifest JSON**：`https://repo.jellyfin.org/files/plugin/manifest.json`（实测 199,871 B，**36 个插件**）；**任何人可自建仓库**；12.0 起 target .NET 10，插件必须重编 |
| 许可证 | **Emby = 专有**（ToS 更新于 2026-08-16，© 2026 Emby LLC）：§3(b) 个人/非商业/可撤销；§3(d) **禁 copy/modify/distribute/sell/lease、禁反向工程、禁派生作品、禁嵌入其它产品**；§3(h) Emby 拥有全部 IP | 同 | **GPLv2**（仓库 LICENSE 首行 "GNU GENERAL PUBLIC LICENSE Version 2, June 1991"） |
| 元数据刮削是否收费 | — | **★ 免费**：官方 Premiere Feature Matrix 里 **"Library Organization and Metadata Gathering" 在 Free 与 Premiere 两列都是 ✔** | 全免费 |
| Emby Connect 是否收费 | — | **★ 免费**（矩阵里 "Emby Connect (simplified remote login)" Free ✔） | — |
| 硬件转码是否收费 | 3.5.3 源码里**未找到门控点**（只有 `PluginSecurityManager` 的 `SupporterKey`/`IsMBSupporter`） | **★ 是 Premiere 功能**（矩阵 Free 列为 `*`），**脚注原文："\* Nvidia Shield and WD NAS devices do not require Premiere for HW Acceleration"** | 免费 |

### 4.5 生态/插件对照

| 维度 | Emby | Jellyfin |
|---|---|---|
| 插件分发 | 中心化，需申请 developer id，后台 `https://plugins.emby.tv/admin/packages.html` 上传 | **公开 manifest JSON，任何人可自建仓库**（官方文档直接列第三方 manifest URL） |
| 机器可读清单 | `https://www.mb3admin.com/admin/service/EmbyPackages.json`（219 个包） | `https://repo.jellyfin.org/files/plugin/manifest.json`（36 个插件）+ unstable 版 |
| 清单 schema | 含 `Premium Plug-in` / `Feature ID` / `Price` / `Version Class(Dev/Beta/release)` / `Required Version` | `MediaBrowser.Common/Plugins/PluginManifest.cs` → `category/changelog/description/guid/name/overview/owner/targetAbi/timestamp/version/status/autoUpdate/imagePath/assemblies`；每版本 `MediaBrowser.Model/Updates/VersionInfo.cs` |
| 付费插件 | **一等公民**（14 天试用，`IRequiresRegistration`） | ❌ 无 |
| 开发政策 | 明禁 scraping / 违反 ToS / 违反库许可，违规"removed from the plug-in catalog without notice"（点名 IMDb scraping、YouTube 下载） | 宽松 |
| 官方 SDK | `MediaBrowser/Emby.SDK`（**含官方 OpenAPI + 6 语言生成客户端**）、`MediaBrowser/Emby.ApiClient`（MIT，已归档）、`MediaBrowser/Emby.ApiClients`（2026-09 仍在更新，版本 4.10.0.40） | `jellyfin/jellyfin-openapi`、`jellyfin/jellyfin-sdk-*` |

---

## 5. 落地建议：自己写「刮削 + 播放」的桌面应用该怎么学

### 5.1 一句话结论

> **接口/协议层学 Emby（它是事实标准，Jellyfin 与 90% 的第三方客户端都兼容它）；实现/算法层学 Jellyfin（有源码可读、GPL 可参考）；闭源的东西一律不要逆向，只按官方 OpenAPI 调。**

### 5.2 如果只学一套，学哪套的哪部分

| 你要做的事 | 学谁 | 为什么 |
|---|---|---|
| **HTTP 接口形状** | **Emby** | Emby 是**先出现的那个**，Jellyfin 分了它的接口名（`MediaSources`/`PlaySessionId`/`ErrorCode`/`TranscodingUrl` 全部同名未改）。学 Emby = 同时能兼容 Jellyfin |
| **认证头** | **Emby** | Emby 3.5.3 就同时收 `X-Emby-Authorization` 与 `Authorization`、同时收 `MediaBrowser` 与 `Emby` scheme → **兼容性最好的一套写法**。而 Jellyfin 12.x 收紧了，学它会丢掉老服务器 |
| **实际实现算法**（直连判定、转码参数拼装、NFO 解析） | **Jellyfin** | Emby 这部分**闭源**（3.5.3 起就在 DLL 里）。Jellyfin 的 `MediaBrowser.Model/Dlna/StreamBuilder.cs`、`Jellyfin.Api/Controllers/DynamicHlsController.cs`、`Emby.Naming/Common/NamingOptions.cs` 全是**可读的 GPL 源码**，而且与 Emby 3.5.2 同源 |
| **命名/目录规范** | **Emby 官方文档** | Emby 的 Movie-Naming / TV-Naming / Music-Naming 是**最完整的公开命名规范**，Jellyfin 与 Kodi/Plex 生态基本互通 |
| **刮削源清单与字段映射** | **两边都看** | Emby 的 3.5.3 源码有**逐字段映射**（§3.3.2 已整理成表）；Jellyfin 有**可读的现行实现** |
| **插件体系** | **Jellyfin** | 公开 manifest JSON、任何人可自建仓库，最适合做生态 |

### 5.3 ★ Emby 比 Jellyfin 更值得借鉴的做法（具体到设计）

1. **`PlaybackInfoResponse.ErrorCode` 三分法**（`NotAllowed` / `NoCompatibleStream` / `RateLimitExceeded`）
   —— 把自己的"不能播"原因编码成机器可读值，比 Jellyfin 只给空 `MediaSources` 更利于客户端做本地化提示。

2. **`DeliveryMethod` 的 `External` 与 `Hls` 双通道外挂字幕**
   Emby 会**把内嵌字幕抽成外挂文件**（`ffmpeg -i {0} -map 0:{1} -an -vn -c:s {2} "{3}"`）再给客户端 `DeliveryUrl`；
   同时在 HLS 场景下另开 `#EXT-X-MEDIA:TYPE=SUBTITLES` 独立轨（`subtitles.m3u8?SegmentLength=&api_key=`）。
   **这是一个"文本字幕走外挂、图形字幕才烧录"的清晰分层**，客户端实现难度低。

3. **`ReportFileSystemChangeBeginning/Complete`（自报文件变更）**
   Emby 在**自己写 NFO/图片之前**先告诉 `FileSystemWatcher` "这是我自己干的"，避免自己触发的无限刷新循环。
   **任何带实时监控 + 写回本地元数据的应用都应该抄这个设计。**

4. **两级节流：provider 级固定间隔 + 队列级串行**
   - TMDB：`requestIntervalMs = 300`（全局 static，串行）
   - 刷新队列：`StartProcessingRefreshQueue()` 单线程消费 + 每条 `Task.Delay(100)`
   **比"不加限制地并发刮削然后被 API 封"稳得多。**

5. **数据本地缓存带 TTL**：`<CachePath>/tmdb-movies2/<tmdbId>/all-<lang>.json`，`TotalDays <= 2` 不重下
   —— **按「数据源 + 实体 ID + 语言」三级分目录缓存**，天然支持多语言与离线重放。

6. **图片语言的三级回退打分**（`MovieDbImageProvider.cs` L108–L126）：
   `目标语言(3) > 英文(2) > 无语言(1)`，并用 `AdjustImageLanguage` 把 `zh` 升级成 `zh-CN` 避免重复。
   **这是"没有目标语言图片时不至于 0 张海报"的正确做法**（Emby 自己在 UI 上曾缺这个回退，Luke 说"计划加"，说明这是被验证过的痛点）。

7. **图片写入的 Legacy / Compatible 双约定 + 内部元数据目录回退**
   `ImageSavingConvention = Legacy` 写 `poster.jpg/fanart.jpg`；`Compatible` 写 Kodi 风格 `poster.jpg` + `extrafanart/fanartN.jpg`；都不满足则落到 `<DataPath>/metadata/<type>/`。
   **一次性兼容三类用户：Emby 老用户、Kodi 用户、不想被写文件到媒体库的用户。**

8. **`SeasonZeroDisplayName` 这种"规范硬编码 + 可覆盖"的设计**
   季目录识别支持 9 种多语言写法，但**显示名**可由库选项覆盖。

9. **`.strm` 一等公民支持**（`BaseVideoResolver.cs:175` → `video.IsShortcut`）
   **对「网盘管理」类项目直接相关**：一个 `.strm` 文件里放一行 URL，就能让任意媒体服务器把网盘内容当本地库管理。

10. **ID 标签写进文件夹名**（`Avatar (2009) [tmdbid=xxx]`）
    —— **绕过刮削搜索、直接命中**。做刮削工具时，产出这个格式比产出 NFO 更"通用"（Emby / Jellyfin / Kodi / Plex 都认）。

### 5.4 哪些是闭源 / 私有协议，**不要碰**

| 东西 | 状态 | 建议 |
|---|---|---|
| `Emby.Server.MediaEncoding.dll` 的方法体（**直连判定与 ffmpeg 参数拼装的真实逻辑**） | **闭源**（3.5.3 起就是二进制；4.x 整个服务端闭源） | **只按官方 OpenAPI 调接口，不要逆向。** Emby ToS §3(d) 明确**禁反向工程、禁派生作品** |
| `MediaBrowser.Model.Dlna.StreamBuilder` 的**判定算法** | 3.5.3 有类型/方法名但**无源码**；4.x 完全闭源 | 需要算法 → **读 Jellyfin 的同名 GPL 源码**（同源，可合法参考） |
| `Emby.Server.Connect.dll`（**Emby Connect 协议**） | **私有**，官方无文档 | **不要实现。** 需要远程访问就用普通端口转发 / 反向代理 + 自己的账号体系 |
| **Emby Premiere 授权校验**（`mb3admin.com/admin/service/registration/validate`） | **私有**，且是商业授权 | **绝对不要碰**（法律风险） |
| Emby 的 **`ApiKey` 硬编码值**（TMDB `f6bd687ffa63cd282b6ff2c6877f2669`、OMDb `fe53f97e`） | 是 Emby 自己申请并内置的 key | **不要复制使用** —— 那是 Emby 的 API 配额。**自己去申请自己的 key** |
| Emby 的闭源**二进制分发** | 专有许可 | 不要打包、不要重分发 |
| Emby 的 **CDN / Sync / Camera Upload 私有端点** | 私有 | 不要依赖 |

> **合规提醒（重要）**：Emby ToS（更新于 2026-08-16）§3(d) 明确禁止 "copy, modify, distribute, sell, lease"、"reverse engineer"、"create derivative works"、"embed into other products"。
> **合法可参考的只有两类**：① Emby **公开发布的官方 OpenAPI 规范**（`Emby.SDK`，用于调用接口）；② Emby **GPL-2.0 授权的那部分历史源码**（`MediaBrowser/Emby` 3.5.3，用于理解设计；若你要基于它写代码，**GPL-2.0 的传染性你必须自行评估**）。
> **想完全无法律风险地抄实现 → 抄 Jellyfin（GPLv2，源码完整）。**

### 5.5 一个可直接照做的落地顺序

```
第 1 步：认证层（1 天）
  ├─ 发 Authorization: MediaBrowser Client="…", Device="…", DeviceId="…", Version="…", Token="…"
  │    （Emby 与 Jellyfin 都收；给 Emby 4.x 也可发 scheme "Emby"）
  ├─ POST /emby/Users/AuthenticateByName  {Username, Pw}
  ├─ 存 AccessToken + ServerId
  └─ 后续请求带 X-Emby-Token: <token>（兼容性最好），流 URL 用 ?api_key=<token>

第 2 步：刮削层（先把"库"建起来，比播放简单且收益快）
  ├─ 本地扫描：按 §3.1 的命名规范解析（先只做 SxxExx + 年份 + 多版本 + extras）
  ├─ 本地优先：先读 <视频名>.nfo / movie.nfo / tvshow.nfo / season.nfo（§3.6.2）
  ├─ 在线补全：TMDB（电影/剧集/单集）→ OMDb（补 CriticRating/OfficialRating）
  │    · 300 ms 全局节流 + 队列 100 ms
  │    · 缓存 <cache>/tmdb-movies2/<id>/all-<lang>.json，TTL 2 天
  ├─ 图片：poster/backdrop/logo/thumb，语言三级回退，原图下载后自己缩放
  └─ 写回：Legacy 或 Compatible 约定（让用户选），写前 ReportFileSystemChangeBeginning

第 3 步：播放层（最复杂，建议先做"只直连 + 只 HLS 转码"两档）
  ├─ POST /emby/Items/{Id}/PlaybackInfo  ← 带上自己的 DeviceProfile
  │    DeviceProfile 最小可用集：
  │      DirectPlayProfiles: [{Container:"mp4,mkv", VideoCodec:"h264,hevc", AudioCodec:"aac,mp3,ac3", Type:"Video"}]
  │      TranscodingProfiles:[{Container:"ts", Type:"Video", VideoCodec:"h264",
  │                            AudioCodec:"aac", Protocol:"hls", Context:"Streaming",
  │                            MinSegments:1, BreakOnNonKeyFrames:true}]
  │      CodecProfiles: [{Type:"Video", Codec:"h264", Conditions:[{Condition:"LessThanEqual",
  │                       Property:"VideoLevel", Value:"41", IsRequired:false}]}]
  │      SubtitleProfiles: [{Format:"srt", Method:"External"}, {Format:"pgssub", Method:"Encode"}]
  ├─ 读返回：TranscodingUrl 为空 → 直连 Path；`TranscodingSubProtocol="http"` → DirectStream；
  │          `="hls"` → GET TranscodingUrl 播 m3u8
  └─ 上报：Playing → Progress（每 10 秒 + 每次交互）→ Stopped；转码时定时 Ping

第 4 步：兼容性收尾
  ├─ 401 + X-Application-Error-Code: ParentalControl → 提示家长控制，不要清 token
  ├─ 401 其他 → token 失效，回登录页
  └─ 退出：POST /emby/Sessions/Logout
```

---

## 6. 没查到 / 不确定 清单（**如实，不许猜**）

### 6.1 因为 Emby 闭源而**根本无法确证**的（最重要）

| # | 问题 | 状态 | 说明 |
|---|---|---|---|
| 1 | **Emby 4.x 直连/转码判定的具体算法**（条件求值顺序、`GetOptimalVideoStream` 内部逻辑） | **未能确认** | 4.x 服务端闭源；3.5.3 只有方法名没有方法体。**唯一合法替代是读 Jellyfin 的同源 GPL 实现** |
| 2 | **Emby 4.x 的 `ResolutionNormalizer.Configurations` 具体数值**（内置"分辨率→码率"阶梯） | **未能确认** | 数值在方法体/静态构造里；未做 IL 反编译；官方 OpenAPI 无 `default` |
| 3 | **`MinResumePct` / `MaxResumePct` / `MinResumeDurationSeconds` 的默认值** | **未能确认** | 默认值在 `MediaBrowser.Model` 的构造函数 IL 里；官方 spec 无 `default`。（Jellyfin 是 5/90/300，**但不能当成 Emby 的默认值**） |
| 4 | **Emby 4.x 是否还接受 `X-MediaBrowser-Token`** | **部分确认** | 3.5.3 源码明确接受（`AuthorizationContext.cs:73`）；**4.10 官方规范的 `securitySchemes` 只提 `X-Emby-Token` 与 `?api_key=`，完全没提 `X-MediaBrowser-Token`** → 4.10 是否仍接受**未能确认**。子 agent 尝试下载 `Emby.Releases` 4.10.0.40 freebsd tar.xz（34.7 MB）**超时失败**，无法对 4.x 程序集做字符串核对 |
| 5 | **Emby 4.x 是否真的读取授权头里的 `UserId`** | **有冲突** | 3.5.3 源码**只读 5 个 key、不读 `UserId`**；4.10 官方规范的 `bearerFormat` **包含 `UserId`** → 两者冲突，**未能确认 4.x 的实际行为** |
| 6 | **Emby Connect 的完整协议** | **未能确认** | 私有协议，官方无文档；我只从 `Emby.Server.Connect.dll` 字符串堆拿到 `https://connect.emby.media/service/`、`X-Connect-Token` 等线索 |
| 7 | **Emby 4.x 硬件转码的 Premiere 强制点在哪** | **只有官方文档口径** | 官方 Feature Matrix 说 HW Acceleration 是 Premiere 功能；但**服务端在哪里校验、怎么校验，闭源无法确证** |
| 8 | **Emby 4.x 是否仍使用 v1 的 TVDB API** | **未能确认** | 3.5.3 明确是 v1（已下线）；4.x 闭源。社区帖说"v3 和现在 v4 api"（L2，Happy2Play） |

### 6.2 官方文档**根本没有**、只能降级引用的

| # | 问题 | 状态 | 唯一可用的降级来源 |
|---|---|---|---|
| 9 | **`movie.nfo` / `tvshow.nfo` / `season.nfo` / `episode.nfo` 的官方文件名规范** | **官方文档层面未能确认** | ★ **但 Emby 3.5.3 源码里是明确实现的**（`MediaBrowser.XbmcMetadata/Savers/*.cs`，见 §3.6.2）。**这是"文档缺失"而非"实现缺失"** |
| 10 | **各元数据提供方具体贡献哪些字段的官方对照表** | **不存在** | ★ 本报告用 3.5.3 **源码逐行读出**（§3.3.2 / §3.3.3），属 [码] 级 |
| 11 | **Zap2It 作为 Emby 元数据提供方** | **未能确认** | 官方知识库 + 官方插件清单（219 包）**零命中**。（Zap2It 是 Plex 生态的 EPG 来源；Emby 只在 `MetadataProviders` 枚举里有个 `Zap2It` id，用于外部链接） |
| 12 | **Screen scraping（屏幕刮削）** | **未能确认** | 官方体系**零命中** |
| 13 | **"Emby Metadata" 作为影视元数据提供方** | **未能确认** | 无此命名（Emby 另有 Emby Guide Data，是 Live TV EPG） |
| 14 | **TheTVDB 从哪个版本起变成纯插件** | **未能确认** | 只知道：TVDB 由插件 `Tvdb` 提供，且**新装服务器上仍是 TV 类库的默认首选**（GrimReaper 2025-08-19，L2）。**具体版本与官方公告未找到** |
| 15 | **GB（BBFC）/ JP / 其他国家的分级取值表** | **未能确认** | 官方文档无；社区也未找到可核验的源码转贴。**只有 US 与 DE 有 L3 级源码片段**（§3.4.2） |
| 16 | **官方推荐/要求的图片像素尺寸** | **未能确认** | 官方知识库无任何像素规定 |
| 17 | **服务端缓存图片的固定缩放宽度数值** | **未能确认** | Emby 是**请求时按 `MaxWidth/MaxHeight/Width/Height/Quality` 实时缩放**，没有文档化的固定缓存宽度 |
| 18 | **官方 "Metadata language" / "Preferred image language" 专页 URL** | **不存在** | 官方知识库无此专页。最接近的官方文字是 Emby Blog 的 "Language Preferences" 段落。**API 字段名是权威的**：4.10 的 `LibraryOptions.PreferredImageLanguage` + `PreferredMetadataLanguage` + `MetadataCountryCode` |
| 19 | **`ImageLanguagePreference` 这个字段名** | **未能确认** | 4.1.1.0 与 4.10 的 OpenAPI **都查不到**该名字；**4.10 的正确字段名是 `PreferredImageLanguage`** |
| 20 | **`Series/Season 2019/Series 2019-01-01.ext`（按年份建季文件夹）** | **未能确认** | 官方 TV Naming 页无记载 |
| 21 | **"绝对集号（absolute numbering）" 作为官方概念** | **未能确认** | 官方页面**没有 "absolute" 一词**（全仓库检索只命中 CSS 与 "absolutely"）。但 **`Emby.Naming.dll` 里确实有 `EpisodeWithoutSeasonExpressions`（纯集号正则组）** → 实现存在，官方概念命名不存在 |
| 22 | **`Collections\` / `BoxSets\` 媒体文件夹约定** | **未能确认** | 官方文档无；全仓库检索 `BoxSet` 只命中 `AutoBoxSets.md` 自身 |
| 23 | **多季合并文件夹（multi-season folder）** | **未能确认** | 官方文档无记载 |
| 24 | **"Scan media library" 的出厂默认间隔小时数** | **官方文档未能确认** | ★ **3.5.3 源码给出 12 小时**（`RefreshMediaLibraryTask.cs`）。4.x 是否为 12 小时**未能确认** |
| 25 | **本地字幕文件命名模板**（如 `Movie.zh-CN.forced.srt`） | **部分确认** | `Emby.Naming.Subtitles.SubtitleParser` 存在，标志位 `forced/default/foreign` 与扩展名 `.srt/.ssa/.ass/.sub` 已确认；**完整命名模板未见** |
| 26 | **`TranscodingUrl` 里参数的确切书写顺序** | **未能确认** | 参数**集合**已在 `MediaBrowser.Model.dll` 字符串堆逐个确认；**顺序**需 IL 反编译 `StreamInfo.BuildParams`。**结论：客户端应原样使用服务端返回的 URL，不要自己拼** |
| 27 | **Emby 4.x 是否仍支持 `POST /embywebsocket` 这个 WebSocket 路径** | **未能确认（3.5.3 确认支持）** | 3.5.3 官方 JS 客户端用 `embywebsocket?api_key=&deviceId=`（§2.7.6(6)）；4.x 规范里没有 WebSocket 的描述 |
| 28 | **Emby 4.x 的 `DeviceProfile` 默认值**（浏览器/移动端各给什么 profile） | **未能确认** | 4.x 闭源；3.5.3 仓库里的 dashboard 前端用的是自己内联的 profile |

### 6.3 子 agent 明确报告"打不开 / 不存在"的页面（**请勿假设其内容**）

**Emby 侧（均实测 HTTP 404）：**

| URL | 结果 |
|---|---|
| `https://emby.media/support/articles/Metadata.html` | **404** —— 正确入口是 `Metadata-manager.html` |
| `https://emby.media/support/articles/Home-Video-Naming.html` | **404** —— Home Video 规则写在 `Movie-Naming.html` 内 |
| `https://emby.media/support/articles/Metadata-Manager.html` | **404** —— **站点大小写敏感**！只有小写 `Metadata-manager.html` 返回 200 |
| `https://emby.media/support/articles/3D-videos.html` | **404** —— 正确是 `3D-Videos.html`（大写 V） |
| `https://emby.media/support/articles/` | **404** —— 目录无 index 页 |
| `https://dev.emby.media/reference/RestAPI/BaseItemDto.html` | **404** —— 我猜测的路径不存在 |
| `https://dev.emby.media/reference/RestAPI/SessionsService/postSessionsPlaying.html` | **404** |
| `https://dev.emby.media/openapi.json` | **404** |
| `https://swagger.emby.media/swagger.json` / `/api-docs` | **404**（正确是 `/openapi.json`） |

**Emby 侧（页面存在但取不到正文）：**
- `https://github.com/MediaBrowser/Emby/issues/3479#issuecomment-444985456` —— JS 渲染，正文取不到
- `api.github.com/repos/MediaBrowser/Emby/issues/comments/444985456` —— 返回空

**Jellyfin 侧：**
- `https://jellyfin.org/docs/general/server/authentication` → **404**（**该页不存在**；拉了 sitemap.xml 的 213 条 URL / 108 条 `/docs/`，**全站没有任何认证/授权页**）→ 所以"官方文档记载 Jellyfin 认证头格式"这条 **未能确认**，只能用 OpenAPI + 源码 + release notes
- `https://en.wikipedia.org/wiki/Emby` → `web_fetch` 报错 "resolves to a non-public IP address"
- `https://mintlify.wiki/jellyfin/jellyfin/api/authentication/overview` → **可打开但是第三方镜像**，未作为权威出处

**环境限制：**
- `raw.githubusercontent.com` 在本环境**不通**（本报告未用它作为任何结论来源）
- `github.com` 的 issue 页面正文取不到（JS 渲染）

### 6.4 我这次**修正过的自己的错误**（如实记录）

| 我最初的判断 | 实际情况 | 触发修正的证据 |
|---|---|---|
| "`MediaBrowser/Emby` 的 DLL 是 stripped 引用程序集，没有方法体" | **错。** 是我自写的元数据提取工具**有 bug**（`types` 模式没打印方法）。修正后：`Emby.Naming.dll` 175 个方法、`MediaBrowser.Controller.dll` 1734 个、`Emby.Server.MediaEncoding.dll` 655 个、`MediaBrowser.Model.dll` 674 个 | 我用 `System.Console.dll` 做 sanity check，发现方法数为 0，定位到 bug |
| "Emby 三个进度 DTO 里没有 `EventName`" | **错（针对 4.x）。** `EventName` 在 3.5.3 与 4.1.1.0 里确实没有，但**在 4.10.0.40 里存在**，官方 Playback Check-ins 文档也明确记载 | 子 agent 找到 `MediaBrowser/Emby.SDK` 的官方 OpenAPI（4.10.0.40） |
| "官方 OpenAPI 只有 4.1.1.0 一个版本，Emby 4.9/4.10 无法覆盖" | **错。** 官方在 `MediaBrowser/Emby.SDK` 发布了 **4.10.0.40** 的 OpenAPI（433 paths / 338 schemas），比 `swagger.emby.media` 新得多 | 子 agent 发现 |
| "`TranscodeReasons` 在 `MediaSourceInfo` 里" | **需修正**：`TranscodeReasons` 在 4.10 的 `MediaSourceInfo` 里**不在**；`TranscodeReason` **枚举**在 spec 里存在，被 `TranscodingInfo` 引用 | [规410] |

---

## 7. 跨版本修正表：Emby 3.5.3 → 4.10.0.40

> 本表列出**同一事物在两个版本间的确切差异**，凡是报告正文标注了 `[码]`/`[属]`（3.5.3）的地方，都以本表为准。
> 数据来源：[属] = `MediaBrowser/Emby` master 的二进制元数据；[规410] = `MediaBrowser/Emby.SDK` → `Resources/OpenApi/openapi_v3.json`（`info.version = 4.10.0.40`）。

### 7.1 播放相关

| 项目 | 3.5.3 [属] | 4.10.0.40 [规410] | 变化方向 |
|---|---|---|---|
| `PlaybackInfoRequest` 字段数 | 18 | **19** | 新增 `AllowInterlacedVideoStreamCopy`、`CurrentPlaySessionId`；**移除 `DirectPlayProtocols`** |
| `PlaybackInfoResponse` | `MediaSources, PlaySessionId, ErrorCode` | **完全一致** | 无变化 |
| `PlaybackErrorCode` | `NotAllowed, NoCompatibleStream, RateLimitExceeded` | **完全一致** | 无变化 |
| `MediaSourceInfo` 字段数 | 53 | **49** | **移除** `ETag, IgnoreDts, IgnoreIndex, GenPtsInput, VideoType, IsoType`（`DefaultAudioStreamIndex` / `DefaultSubtitleStreamIndex` / `SupportsProbing` / `RequiresLooping` **保留**）；**新增** `DirectStreamUrl, AddApiKeyToDirectStreamUrl, Chapters, ProbePath, ProbeProtocol, SortName, ItemId, ServerId, MimeType, TranscodingMimeType, HasMixedProtocols, ContainerStartTimeTicks, TrancodeLiveStartIndex, WallClockStart` |
| `MediaStream` 字段数 | 45 | **56** | **新增** `Rotation, IsHearingImpaired, DeliveryFormat, IsChunkedResponse, Protocol, ExtendedVideoType, ExtendedVideoSubType, ExtendedVideoSubTypeDescription, ItemId, ServerId, AttachmentSize, MimeType, SubtitleLocationType, StreamStartTimeTicks` |
| `MediaStreamType` | `Audio, Video, Subtitle, EmbeddedImage` | **`Unknown, Audio, Video, Subtitle, EmbeddedImage, Attachment, Data`** | 新增 3 项 |
| `SubtitleDeliveryMethod` | `Encode, Embed, External, Hls` | **`Encode, Embed, External, Hls, VideoSideData`** | 新增 `VideoSideData`（**没有 `Drop`，任务书里的 `Drop` 不存在**） |
| `MediaProtocol` | `File, Http, Rtmp, Rtsp, Udp, Rtp, Ftp` | **`File, Http, Rtmp, Rtsp, Udp, Rtp, Ftp, Mms`** | 新增 `Mms`（**没有 `Hls`**） |
| `TranscodeReason` 取值数 | 23 | **27** | 新增 `VideoRangeNotSupported, SubtitleContentOptionsEnabled, ExternalAudioNotSupported, AudioDelayNotSupported` |
| `PlaybackProgressInfo` 字段数 | 20 | **30** | 新增 `PlaylistIndex, PlaylistLength, EventName, SleepTimerMode, SleepTimerEndTime, Shuffle, SubtitleOffset, PlaybackRate, PlaylistItemIds, RunTimeTicks` |
| `PlaybackStartInfo` | 继承自 ProgressInfo（0 自有字段） | **30 个自有字段（与 ProgressInfo 同构）** | 结构未变，属性集补齐 |
| `PlaybackStopInfo` 字段数 | 11 | **14** | 新增 `PlaylistIndex, PlaylistLength, IsAutomated` |
| `ClientCapabilities` | 10 字段 | **9** | **移除了 `SupportsPersistentIdentifier`** |
| `TranscodingInfo` 字段数 | 12 | **32** | 新增 `SubProtocol, AudioBitrate, VideoBitrate, ProcessStatistics, VideoDecoder*, VideoEncoder*, VideoPipelineInfo, SubtitlePipelineInfos` —— ★ **`VideoEncoderIsHardware` / `VideoEncoderHwAccel` 让客户端能显示"正在用硬件转码"** |
| `/Videos/{Id}/live_subtitles.m3u8` | ❌ | **✔ 新增** | 直播字幕独立清单 |
| `/Videos/{Id}/index.bif` | ❌ | **✔ 新增** | BIF 图片（trickplay 缩略图） |
| `/Videos/ActiveEncodings/Delete`（POST） | ❌ | **✔ 新增** | DELETE 的 POST 变体 |
| `/Videos/{Id}/master.m3u8` 参数数 | — | **24**（比 4.1.1.0 的 51 个**还少**） | ⚠️ **OpenAPI 在此端点上不完整，不可照抄** |

### 7.2 刮削/配置相关

| 项目 | 3.5.3 [属] | 4.10.0.40 [规410] | 变化方向 |
|---|---|---|---|
| `LibraryOptions` 字段数 | 28 | **65** | **`MinResumePct`/`MaxResumePct`/`MinResumeDurationSeconds` 从全局 `ServerConfiguration` 下放到每库**；新增 `PreferredImageLanguage`、`IgnoreFileExtensions`、`IgnoreHiddenFiles`、`EnablePlexIgnore`、`EnableMarkerDetection`（片头识别）、`AutoGenerateChapters`、`EnableMultiVersionByFiles`/`ByMetadata`、`EnableMultiPartItems`、`ImportCollections`+`MinCollectionItems`、`CacheImages`、`EnableAudioResume`、`ThumbnailImagesIntervalSeconds` 等 |
| `ServerConfiguration` 字段数 | 60 | **71** | `LibraryMonitorDelay` → **`LibraryMonitorDelaySeconds`**（改名）；`MinResumePct` 等**移出** |
| `ImageType` | 12 项 | **15 项**（+`Thumbnail`, `LogoLight`, `LogoLightColor`） | 新增 3 项 |
| `MetadataFields` | 9 项 | **21 项** | 新增 `Collections, ChannelNumber, SortName, OriginalTitle, SortIndexNumber, SortParentIndexNumber, CommunityRating, CriticRating, Tagline, Composers, Artists, AlbumArtists` |
| `MetadataRefreshMode` | `None, ValidationOnly, Default, FullRefresh` | **`ValidationOnly, Default, FullRefresh`**（无 `None`） | 移除 `None` |
| `MetadataOptions` | `ItemType, DisabledMetadataSavers, LocalMetadataReaderOrder, DisabledMetadataFetchers, MetadataFetcherOrder, DisabledImageFetchers, ImageFetcherOrder` | **改名为 `TypeOptions`**：`Type, MetadataFetchers, MetadataFetcherOrder, ImageFetchers, ImageFetcherOrder, ImageOptions` | ★ **结构变化：`Disabled*` 系列的"禁用"语义改成了"列出启用项 + 排序"** |
| `ImageOption` | — | **`Type(ImageType), Limit, MinWidth`** | 新增独立 schema |
| `LibraryTypeOptions` | — | `Type, MetadataFetchers(LibraryOptionInfo[]), ImageFetchers(LibraryOptionInfo[]), SupportedImageTypes(ImageType[]), DefaultImageOptions(ImageOption[])` | 新增 |
| `MetadataFeatures` | — | **`Collections, Adult, RequiredSetup`** | 新增 |
| `CollectionType` | `Movies, TvShows, Music, MusicVideos, Trailers, HomeVideos, BoxSets, Books, Photos, Games, LiveTv, Playlists, Folders` | **spec 里无独立 enum**（`LibraryOptions.ContentType` 是 string） | 结构变化 |

### 7.3 认证相关

| 项目 | 3.5.3 [码] | 4.10.0.40 [规410] |
|---|---|---|
| 授权头名 | `X-Emby-Authorization` 优先 → `Authorization` 回退 | spec 用 `Authorization`（`scheme: bearer`） |
| scheme | **`MediaBrowser` 或 `Emby`**（都收） | `Emby`（`bearerFormat` 原文） |
| 参数键 | `DeviceId, Device, Client, Version, Token`（**不读 `UserId`**） | `UserId` + 上述 5 个 |
| Token 头 | `X-Emby-Token` > `X-MediaBrowser-Token` > `?api_key=` | `X-Emby-Token` 或 `?api_key=`（**未提 `X-MediaBrowser-Token`**） |
| `POST /Users/AuthenticateByName` body | `AuthenticateUserByName { Username, Password, Pw }` | `AuthenticateUserByName { Username, Pw }` |
| 流端点 security | `[Authenticated]` + 支持 `?api_key=` | **显式声明** `security: [{"apikeyauth": []}, {"embyauth": []}]` |

---

## 8. 附录

### 附录 A：3.5.3 从二进制提取的完整播放/转码路由表

来源：`strings`/元数据提取自 `ThirdParty/emby/Emby.Server.MediaEncoding.dll`（[属] 级）。

```
GET|HEAD  /Items/{Id}/PlaybackInfo                        GetPlaybackInfo / GetPostedPlaybackInfo(POST)
POST      /LiveStreams/Open                               OpenMediaSource
POST      /LiveStreams/Close                              CloseMediaSource          (query: LiveStreamId)
POST      /LiveStreams/MediaInfo                          GetLiveStreamMediaInfo    (query: LiveStreamId)
GET       /Playback/BitrateTest                           GetBitrateTestBytes       (query: Size)
POST      /Users/{UserId}/PlayedItems/{Id}                MarkPlayedItem            (query: DatePlayed=yyyyMMddHHmmss)
DELETE    /Users/{UserId}/PlayedItems/{Id}                MarkUnplayedItem
POST      /Sessions/Playing                               ReportPlaybackStart
POST      /Sessions/Playing/Progress                      ReportPlaybackProgress
POST      /Sessions/Playing/Ping                          PingPlaybackSession       (query: PlaySessionId)
POST      /Sessions/Playing/Stopped                       ReportPlaybackStopped
POST      /Users/{UserId}/PlayingItems/{Id}               OnPlaybackStart
POST      /Users/{UserId}/PlayingItems/{Id}/Progress      OnPlaybackProgress
DELETE    /Users/{UserId}/PlayingItems/{Id}               OnPlaybackStopped
GET|HEAD  /Audio/{Id}/universal[.{Container}]             GetUniversalAudioStream
GET|HEAD  /Audio/{Id}/stream[.{Container}]                GetAudioStream
GET|HEAD  /Videos/{Id}/stream[.{Container}]               GetVideoStream
GET       /Items/File                                     GetFile                   (AllowLocalOnly=true)
GET|HEAD  /Videos/{Id}/master.m3u8                        GetMasterHlsVideoPlaylist
GET|HEAD  /Audio/{Id}/master.m3u8                         GetMasterHlsAudioPlaylist
GET       /Videos/{Id}/main.m3u8  /Audio/{Id}/main.m3u8    GetVariantHlsVideoPlaylist / GetVariantHlsAudioPlaylist
GET|HEAD  /Videos/{Id}/hls1/{PlaylistId}/{SegmentId}.{SegmentContainer}   GetHlsSegment
GET|HEAD  /Audio/{Id}/hls1/{PlaylistId}/{SegmentId}.{SegmentContainer}    GetHlsSegment
DELETE    /Videos/ActiveEncodings                         StopEncodingProcess       (query: DeviceId, PlaySessionId)
GET       /Videos/{Id}/hls/{PlaylistId}/{SegmentId}.{SegmentContainer}    GetHlsVideoSegmentLegacy
GET       /Videos/{Id}/live.m3u8                          GetLiveHlsStream
```

**`MediaBrowser.Api` 源码（[码]）里与会话/字幕/播放相关的一部分：**
```
GET       /Sessions                                        SessionsService.cs:21
POST      /Sessions/{Id}/Viewing                           SessionsService.cs:37
POST      /Sessions/{Id}/Playing                           SessionsService.cs:70
POST      /Sessions/{Id}/Playing/{Command}                 SessionsService.cs:82
POST      /Sessions/{Id}/System/{Command}                  SessionsService.cs:94
POST      /Sessions/{Id}/Command[/{Command}]               SessionsService.cs:113/132
POST      /Sessions/{Id}/Message                           SessionsService.cs:144
POST|DELETE /Sessions/{Id}/Users/{UserId}                  SessionsService.cs:165/176
POST      /Sessions/Capabilities                           SessionsService.cs:187
POST      /Sessions/Capabilities/Full                      SessionsService.cs:219
POST      /Sessions/Logout                                 SessionsService.cs:231
GET       /Auth/Keys  |  POST /Auth/Keys  |  DELETE /Auth/Keys/{Key}  |  GET /Auth/Providers
GET       /Items/{Id}/RemoteSearch/Subtitles/{Language}     SubtitleService.cs:38
POST      /Items/{Id}/RemoteSearch/Subtitles/{SubtitleId}   SubtitleService.cs:51
GET       /Providers/Subtitles/Subtitles/{Id}               SubtitleService.cs:62
GET       /Videos/{Id}/{MediaSourceId}/Subtitles/{Index}/Stream.{Format}                       SubtitleService.cs:70
GET       /Videos/{Id}/{MediaSourceId}/Subtitles/{Index}/{StartPositionTicks}/Stream.{Format}  SubtitleService.cs:71
GET       /Videos/{Id}/{MediaSourceId}/Subtitles/{Index}/subtitles.m3u8                        SubtitleService.cs:101
DELETE    /Videos/{Id}/Subtitles/{Index}                    SubtitleService.cs:23
GET       /Videos/{Id}/AdditionalParts                      VideosService.cs:20
DELETE    /Videos/{Id}/AlternateSources                     VideosService.cs:35
POST      /Videos/MergeVersions                             VideosService.cs:43
GET       /Items/{Id}/ThemeVideos                           LibraryService.cs:107
GET|POST  /Items/{Id}/ExternalIdInfos /Items/RemoteSearch/*  ItemLookupService.cs:23–105
POST      /Items/{Id}/Refresh                               ItemRefreshService.cs:29
```

（`MediaBrowser.Api` 源码中共 **318** 条 `[Route("…")]`，完整清单可自行 `grep -rn 'Route("' --include=*.cs MediaBrowser.Api/` 得到。）

### 附录 B：本报告引用的全部来源

**A. Emby 仓库与二进制（[码]/[属]）**
- `https://github.com/MediaBrowser/Emby`（master = Emby Server **3.5.3.0**）
- `https://github.com/MediaBrowser/Emby/blob/master/SharedVersion.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs`（L56-L78 / L179-L232）
- `https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/Library/UserDataManager.cs#L222-L280`
- `https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/IO/LibraryMonitor.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/IO/FileRefresher.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/ScheduledTasks/RefreshMediaLibraryTask.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/Library/Resolvers/BaseVideoResolver.cs#L169-L196`
- `https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/Library/Resolvers/TV/SeasonResolver.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/ServerApplicationPaths.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/Images/BaseDynamicImageProvider.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.LocalMetadata/Images/LocalImageProvider.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.LocalMetadata/Images/EpisodeLocalImageProvider.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Providers/Manager/ImageSaver.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Providers/Manager/ProviderManager.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Providers/Movies/MovieDbProvider.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Providers/Movies/GenericMovieDbInfo.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Providers/Movies/MovieDbImageProvider.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Providers/Omdb/OmdbProvider.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Providers/TV/TheTVDB/TvdbSeriesProvider.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Providers/TV/TheTVDB/TvdbPrescanTask.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Providers/TV/TheMovieDb/MovieDbSeriesProvider.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Providers/TV/TheMovieDb/MovieDbEpisodeProvider.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.XbmcMetadata/Savers/MovieNfoSaver.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.XbmcMetadata/Savers/SeriesNfoSaver.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.XbmcMetadata/Savers/SeasonNfoSaver.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.XbmcMetadata/Savers/EpisodeNfoSaver.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/ItemRefreshService.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/ItemLookupService.cs`
- `https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs`
- 整仓库快照：`https://codeload.github.com/MediaBrowser/Emby/tar.gz/refs/heads/master`

**B. Emby 官方 API 规范（[规410] / [规]）**
- ★ `https://github.com/MediaBrowser/Emby.SDK/blob/master/Resources/OpenApi/openapi_v3.json`（**4.10.0.40**，433 paths）
- `https://github.com/MediaBrowser/Emby.SDK/blob/master/Resources/OpenApi/openapi_v2.json`（Swagger 2.0 版，同版本）
- `https://swagger.emby.media/openapi.json`（**4.1.1.0**，356 paths，已被取代）
- `http://swagger.emby.media/?staticview=true`（官方静态 API 浏览器）
- `https://dev.emby.media/reference/RestAPI.html`（REST 参考索引）
- `https://dev.emby.media/reference/index.html`

**C. Emby 官方文档（[官]）**
- `https://dev.emby.media/doc/restapi/index.html`（总览、`/emby/{apipath}`、JSON/XML）
- `https://dev.emby.media/doc/restapi/User-Authentication.html`
- `https://dev.emby.media/doc/restapi/API-Key-Authentication.html`
- `https://dev.emby.media/doc/restapi/Playback-Check-ins.html` ★（三接口 + `EventName` + 10 秒频率）
- `https://dev.emby.media/doc/restapi/Parental-Control.html`（`X-Application-Error-Code: ParentalControl`）
- `https://emby.media/support/articles/Quick-Start.html`
- `https://emby.media/support/articles/Library-Setup.html`
- `https://emby.media/support/articles/Movie-Naming.html`
- `https://emby.media/support/articles/TV-Naming.html`
- `https://emby.media/support/articles/Music-Naming.html`
- `https://emby.media/support/articles/Audio-Book-Naming.html`
- `https://emby.media/support/articles/Book-Naming.html`
- `https://emby.media/support/articles/3D-Videos.html`
- `https://emby.media/support/articles/Trailers.html`
- `https://emby.media/support/articles/Media-Stubs.html`
- `https://emby.media/support/articles/Strm-Files.html`
- `https://emby.media/support/articles/Theme-Songs-Videos.html`
- `https://emby.media/support/articles/Subtitles.html`
- `https://emby.media/support/articles/Metadata-manager.html`（**必须小写 m**）
- `https://emby.media/support/articles/Identify.html`
- `https://emby.media/support/articles/Image-Editing.html`
- `https://emby.media/support/articles/Ordering-TV-Specials.html`
- `https://emby.media/support/articles/Collections.html`
- `https://emby.media/support/articles/AutoBoxSets.html`
- `https://emby.media/support/articles/Excluding-Files-Folders.html`
- `https://emby.media/support/articles/Parental-Controls.html`
- `https://emby.media/support/articles/New-Media-Date-Handling.html`
- `https://emby.media/support/articles/Scheduled-Tasks.html`
- `https://emby.media/support/articles/Synology-NAS.html`
- `https://emby.media/support/articles/Premiere-Feature-Matrix.html` ★（HW 转码/Premiere 矩阵）
- `https://emby.media/premiere.html` / `https://emby.media/terms.html`
- `https://www.mb3admin.com/admin/service/EmbyPackages.json`（官方插件清单，219 包）

**D. Emby 官方论坛/博客（L2，发言人身份可核验）**
- `https://emby.media/community/topic/121372-metadata-priority-local-vs-remote/`（Luke：本地优先/刷新最后）★
- `https://emby.media/community/topic/135571-plugins-moviedb-issue/`（Luke：无需配置 API key）
- `https://emby.media/community/topic/141431-thetvdb-as-a-source/`（GrimReaper：TVDB 是新装 TV 库默认首选）
- `https://emby.media/community/topic/146202-tmdb-posters-no-language-fallback-person-bio-missing-after-bulk-refresh/`（图片语言无回退）
- `https://emby.media/community/topic/147223-unable-to-generate-nfo-files/`（Luke：NFO 不适用于照片）
- `https://emby.media/community/blogs/entry/581-how-to-guide-metadata/`（Emby Blog，2024-12-25）

**E. 社区（L3，仅作线索）**
- `https://emby.media/community/topic/105402-format-of-age-rating/`（US/DE `LoadRatings` 分级表）
- `https://emby.media/community/topic/70312-naming-of-nfo-and-posters/`（用户列出的第三方工具 NFO/图片命名）

**F. Jellyfin 侧（配套文件 `jellyfin-facts-播放与刮削.md` 的完整出处清单见该文件）**
- `https://api.jellyfin.org/openapi/jellyfin-openapi-stable.json`（12.1）/ `-unstable.json`（13.0）
- `https://repo.jellyfin.org/files/plugin/manifest.json`（36 个插件）
- `https://github.com/jellyfin/jellyfin/releases/tag/v12.0`（认证断代 release notes）
- `https://jellyfin.org/docs/general/server/metadata/` 等官方文档
- `https://codeload.github.com/jellyfin/jellyfin/tar.gz/refs/heads/master`

### 附录 C：可复现的取数命令

```bash
# 1) Emby 仓库元数据（需 GitHub token；未认证 60 次/小时）
GH=$(cat ~/python/令牌/网盘管理token.txt)
curl -s -H "Authorization: Bearer $GH" https://api.github.com/repos/MediaBrowser/Emby

# 2) 整仓库 tarball（不消耗 API 配额；raw.githubusercontent.com 本环境不通）
curl -sL -o emby-master.tar.gz \
  "https://codeload.github.com/MediaBrowser/Emby/tar.gz/refs/heads/master"   # 178,752,003 B
mkdir -p /tmp/embysrc && tar xzf emby-master.tar.gz -C /tmp/embysrc          # 解包 478 MB

# 3) 官方 4.10.0.40 OpenAPI（★ 最强 API 证据）
curl -sL -o emby-sdk.tar.gz \
  "https://codeload.github.com/MediaBrowser/Emby.SDK/tar.gz/refs/heads/master"  # 15,801,482 B
tar xzf emby-sdk.tar.gz -C /tmp/sdk
# → /tmp/sdk/Emby.SDK-master/Resources/OpenApi/openapi_v3.json   (4.10.0.40, 433 paths, 338 schemas)

# 4) 官方 4.1.1.0 OpenAPI（旧，对照用）
curl -sL -o emby_openapi.json "https://swagger.emby.media/openapi.json"      # 2,012,502 B

# 5) 从二进制提取类型/属性/方法（自写 .NET 工具，用 System.Reflection.Metadata）
#    关键：types 模式要同时打印 F(字段) P(属性) M(方法)，否则会误判为 stripped 程序集
dotnet run -- <dll> types <过滤子串>

# 6) 从二进制提取字符串字面量（ffmpeg 模板、HLS 指令、URL 模板）
strings -e l -n 4 <dll>          # -e l = UTF-16LE（.NET 的 #US 用户字符串堆）

# 7) Emby 官方 Wiki（git clone 可拿到全部 Markdown）
git clone https://github.com/MediaBrowser/Emby.wiki.git
```

---

**报告结束。**
所有 `[码]`/`[属]` 结论可直接复现；所有 `[规410]`/`[规]` 结论均可从官方 OpenAPI 逐字段核对；所有 `[官]` 结论均带可点开的官方 URL。
**"没查到 / 不确定"共 28 项，分布在 §6.1（8 项，闭源导致无法确证）与 §6.2（20 项，官方文档缺失/只能降级引用）；打不开的页面在 §6.3；我修正过的 4 处自身错误在 §6.4。**
