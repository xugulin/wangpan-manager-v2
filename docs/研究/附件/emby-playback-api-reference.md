# Emby 播放（Playback）HTTP API 参考 —— 全部结论可溯源

> 调研对象：Emby 媒体服务器（emby.media）**播放相关** REST API。
> **本报告不发明任何路径、参数名或字段名**：每一条都给出可点击来源；凡无法验证者一律写 **未能确认**。
> 调研日期基准：`MediaBrowser/Emby.SDK` 4.10.0.40 / `MediaBrowser/Emby.ApiClients` 4.10.0.40 / `MediaBrowser/Emby`（master = 3.5.3.0）快照。

---

## 0. 证据分级与抓取方式（先读这一节）

| 级别 | 含义 | 载体 |
|---|---|---|
| **【L1-API】** | Emby 官方发布的机器可读 API 规范（OpenAPI） | `MediaBrowser/Emby.SDK` 仓库 `Resources/OpenApi/openapi_v3.json`（`info.version = 4.10.0.40`）、`https://swagger.emby.media/openapi.json`（`info.version = 4.1.1.0`，较旧） |
| **【L1-DOC】** | Emby 官方开发者文档站（静态站，源即上面 Emby.SDK 仓库） | `https://dev.emby.media/reference/RestAPI/...`、`https://dev.emby.media/reference/pluginapi/...` |
| **【L1-WIKI】** | Emby 官方 Wiki（`MediaBrowser/Emby.wiki.git`，最后提交 2025-07-16） | `https://github.com/MediaBrowser/Emby/wiki/...` |
| **【L1-SRC】** | Emby **开源部分**源码（GPL，`MediaBrowser/Emby` master = 3.5.3.0） | GitHub blob + 行号 |
| **【L1-CLIENT】** | Emby 官方客户端库源码 | `MediaBrowser/Emby.ApiClient`（.NET，已归档）、`MediaBrowser/Emby.ApiClient.Javascript`（已归档）、`MediaBrowser/Emby.ApiClients`（多语言，2026 年仍在更新） |
| **【L3】** | 第三方捕获的真实响应 / 社区帖（有出处，但非官方规范） | 见各条出处 |

### 0.1 一个必须先纠正的前提

任务书里给出的源码路径 **`https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Model/Entities/MediaSourceInfo.cs` 不存在**。

实测证据：

- `MediaBrowser/Emby` master 分支（`SharedVersion.cs` = `AssemblyVersion("3.5.3.0")`）的完整文件树（`git/trees/master?recursive=1`，7031 个 blob）中，**以 `MediaBrowser.Model` 开头的文件数为 0**。
- `MediaBrowser.sln` 只包含 `MediaBrowser.Api`、`MediaBrowser.WebDashboard`、`MediaBrowser.Providers`、`Emby.Server.Implementations` 等工程，**没有 MediaBrowser.Model / MediaBrowser.Controller / Emby.Naming 工程**。
- `MediaBrowser.Api/packages.config` 明确写着 Model 来自 NuGet 二进制包：
  ```xml
  <package id="MediaBrowser.Common" version="3.5.0" targetFramework="net47" />
  <package id="MediaBrowser.Server.Core" version="3.5.0" targetFramework="net47" />
  ```
  `MediaBrowser.Api.csproj` 的 112–114 行也指向 `..\packages\MediaBrowser.Common.3.5.0\lib\netstandard2.0\MediaBrowser.Model.dll`。
- 打 `3.5.2.0` tag 的 tarball 同样没有 `MediaBrowser.Api/Playback/` 与 `MediaBrowser.Model/` 目录（只有 `MediaBrowser.Api/` 下 76 个文件，其中与播放相关的仅 `VideosService.cs`、`PlaylistService.cs`）。
  来源：`https://codeload.github.com/MediaBrowser/Emby/tar.gz/refs/tags/3.5.2.0`（本次实测下载并解包核对）。

**结论**：Emby 从 3.5.x 起，`MediaBrowser.Model`、`MediaBrowser.Controller`、转码与播放 API 的实现（`Emby.Server.MediaEncoding`、`MediaBrowser.Api/Playback/*`、`MediaBrowser.Model/MediaInfo/*`）就**已经是二进制闭源件**。

因此本报告对"模型字段"的权威来源改用两条 L1 路径：
1. **OpenAPI 规范**：`https://github.com/MediaBrowser/Emby.SDK/blob/master/Resources/OpenApi/openapi_v3.json`（用 JSON Pointer 定位，如 `#/components/schemas/MediaSourceInfo/properties/TranscodingUrl`）；
2. **官方 pluginapi 文档**：`https://dev.emby.media/reference/pluginapi/<命名空间>.<类型>.html`，例如 `MediaBrowser.Model.Dto.MediaSourceInfo.html`。
   → 注意：**`MediaSourceInfo` 属于命名空间 `MediaBrowser.Model.Dto`，不是 `MediaBrowser.Model.Entities`**；`MediaStream` 才在 `MediaBrowser.Model.Entities`。

### 0.2 本机无法访问的来源（影响可核查性）

- `raw.githubusercontent.com`：本次实测返回 HTTP 000（不通）。
- `api.github.com` 匿名额度：本次实测 `remaining: 0`（60/小时已耗尽）；带令牌可用（5000/小时）。
- `swagger.emby.media/openapi.json`：可下载，但 `info.version = 4.1.1.0`，比 SDK 仓库里的 4.10.0.40 旧，且**缺失 `PlaybackInfoRequest` 等 schema 定义**（实测：该文件的 `components.schemas` 中不存在 `PlaybackInfoRequest`）。**引用请优先用 Emby.SDK 仓库里的 `openapi_v3.json`。**

---

## 1. 认证（Authentication）

### 1.1 `X-Emby-Authorization` 的确切语法

**官方权威表述（【L1-DOC】）**，出现在 `POST /Users/AuthenticateByName` 的 `X-Emby-Authorization` 头说明中：

> The authorization header can be either named 'Authorization' or 'X-Emby-Authorization'.
> It must be of the following schema:
> `Emby UserId="(guid)", Client="(string)", Device="(string)", DeviceId="(string)", Version="string", Token="(string)"`

来源：[postUsersAuthenticatebyname](https://dev.emby.media/reference/RestAPI/UserService/postUsersAuthenticatebyname.html)
（同一段文字也在 OpenAPI 的 `components.securitySchemes.embyauth.bearerFormat` 中：`https://github.com/MediaBrowser/Emby.SDK/blob/master/Resources/OpenApi/openapi_v3.json` → `#/components/securitySchemes/embyauth`）

#### 服务端解析规则（【L1-SRC】，逐行可核对）

文件：`Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs`（3.5.3.0）

| 行为 | 行号 | 源码 |
|---|---|---|
| 先读 `X-Emby-Authorization` | [L184](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs#L184) | `var auth = httpReq.Headers["X-Emby-Authorization"];` |
| 没有再退回标准 `Authorization` | [L188](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs#L188) | `auth = httpReq.Headers["Authorization"];` |
| **只按第一个空格**切成 2 段（scheme + 参数串） | [L203](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs#L203) | `authorizationHeader.Split(new[] { ' ' }, 2)` |
| 段数必须为 2，否则整个头被忽略 | [L206](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs#L206) | `if (parts.Length != 2) return null;` |
| **接受的 scheme 名**：`MediaBrowser` 与 `Emby` | [L208](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs#L208)–[L211](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs#L211) | `var acceptedNames = new[] { "MediaBrowser", "Emby" };` + `StringComparer.OrdinalIgnoreCase` |
| 参数之间用 **逗号** 分割 | [L218](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs#L218) | `parts = authorizationHeader.Split(',');` |
| 每项按 **第一个 `=`** 切成 key/value | [L224](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs#L224) | `item.Trim().Split(new[] { '=' }, 2)` |
| **双引号是可选装饰**：值两侧的 `"` 被剥掉 | [L228](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs#L228) | `NormalizeValue(param[1].Trim(new[] { '"' }))` |
| **key 名大小写不敏感** | [L220](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs#L220) | `new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)` |
| **服务端实际读取的参数只有 5 个**：`DeviceId`、`Device`、`Client`、`Version`、`Token` | [L59](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs#L59)–[L63](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs#L63) | `auth.TryGetValue("DeviceId", out deviceId); ... TryGetValue("Token", out token);` |

⚠️ **重要细节**：官方 schema 里写了 `UserId="(guid)"`，但 **3.5.3.0 开源服务端代码并不读取 `UserId`**（L59–L63 只取 5 个 key）。`UserId` 是否在 4.x 被使用 **未能确认**。

#### 各客户端实际发出的形式（【L1-CLIENT】）

**(a) 当前 Web 客户端 / JS ApiClient** —— `MediaBrowser/Emby.ApiClient.Javascript`，文件 `apiclient.js`：

| 行为 | 行号 | 源码 |
|---|---|---|
| 拼装 scheme 与分隔符 | [L725](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L725) | ``const auth = `MediaBrowser ${values.join(', ')}`;`` |
| 写入头名 | [L726](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L726) | `headers['X-Emby-Authorization'] = auth;` |
| 参数顺序：`Client` | [L673](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L673) | ``values.push(`Client="${appName}"`)`` |
| 参数顺序：`Device`（**值做 `encodeURIComponent`**） | [L681](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L681) | ``values.push(`Device="${encodeURIComponent(this._deviceName)}"`)`` |
| 参数顺序：`DeviceId` | [L689](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L689) | ``values.push(`DeviceId="${this._deviceId}"`)`` |
| 参数顺序：`Version` | [L697](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L697) | ``values.push(`Version="${this._appVersion}"`)`` |
| 参数顺序：`Token` | [L705](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L705) | ``values.push(`Token="${accessToken}"`)`` |

→ 实际发出的字符串形如：
```
X-Emby-Authorization: MediaBrowser Client="Emby Web", Device="Chrome", DeviceId="xxx", Version="4.10.0.40", Token="<access token>"
```
（参数之间是 **逗号 + 一个空格**；顺序为 `Client, Device, DeviceId, Version, Token`；`UserId` 不在其中。）

**(b) .NET ApiClient（已归档）** —— `MediaBrowser/Emby.ApiClient`：

- `Emby.ApiClient/BaseApiClient.cs` [L167](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/BaseApiClient.cs#L167)：`AuthorizationScheme => "MediaBrowser"`；
  [L176](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/BaseApiClient.cs#L176)–L190：参数拼装为
  `Client="{0}", DeviceId="{1}", Device="{2}", Version="{3}"`（**顺序与 JS 客户端不同**，并可选追加 `, UserId="{0}"`）。
- `Emby.ApiClient/Net/HttpWebRequestClient.cs` [L250](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/Net/HttpWebRequestClient.cs#L250)–[L251](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/Net/HttpWebRequestClient.cs#L251)：`request.Headers["X-Emby-Authorization"] = "{scheme} {parameter}";`
- `Emby.ApiClient/ConnectionManager.cs` [L519](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ConnectionManager.cs#L519)：Connect 交换时硬编码 `MediaBrowser Client="…", Device="…", DeviceId="…", Version="…"`。

**结论（顺序）**：参数顺序 **不固定、也不重要**（服务端按名取值，见上表 L224/L220）。但 `scheme` 必须是 `Emby` 或 `MediaBrowser` 之一（L208）。引号可省（L228），但官方 schema 与全部官方客户端都带引号。

### 1.2 `X-Emby-Token` / `X-MediaBrowser-Token` / `api_key`：谁在用

#### 服务端取值优先级（【L1-SRC】）

`AuthorizationContext.cs`：

```csharp
// L57-64：先看授权头里的 Token= 参数
if (auth != null) { ... auth.TryGetValue("Token", out token); }
// L66-69
if (string.IsNullOrEmpty(token)) { token = httpReq.Headers["X-Emby-Token"]; }
// L71-74
if (string.IsNullOrEmpty(token)) { token = httpReq.Headers["X-MediaBrowser-Token"]; }
// L75-78
if (string.IsNullOrEmpty(token)) { token = httpReq.QueryString["api_key"]; }
```

来源：[AuthorizationContext.cs#L66-L78](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs#L66-L78)

**优先级：授权头内 `Token=` > `X-Emby-Token` > `X-MediaBrowser-Token` > 查询串 `api_key`。**

CORS 白名单同样把两个名字都放行（3.5.3.0）：
- `Emby.Server.Implementations/HttpServer/ResponseFilter.cs` [L29](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/ResponseFilter.cs#L29)（`…, X-MediaBrowser-Token, X-Emby-Authorization`）
- `Emby.Server.Implementations/HttpServer/HttpListenerHost.cs` [L543](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/HttpListenerHost.cs#L543)

#### 官方文档推荐（【L1-WIKI】）

- 用户令牌：**"The return result object will have an **AccessToken** property. This should be included in all subsequent Http requests using the header **X-Emby-Token**."**
  → [User-Authentication](https://github.com/MediaBrowser/Emby/wiki/User-Authentication)（该行在页面 "Authenticating a user" 小节）
- API Key：**"Send the API key string in an http request heaser named **X-Emby-Token**."** / **"transmit the API key as a query string parameter name **api_key**"**
  → [API-Key-Authentication](https://github.com/MediaBrowser/Emby/wiki/API-Key-Authentication)

#### OpenAPI 声明（【L1-API】）

`openapi_v3.json` → `#/components/securitySchemes`：

```json
"apikeyauth": { "type": "apiKey", "name": "api_key", "in": "query",
  "description": "… The api key can alternatively be specified in an http header named _X-Emby-Token_ …" },
"embyauth":  { "type": "http", "scheme": "bearer",
  "bearerFormat": "Emby UserId=\"(guid)\", Client=\"(string)\", Device=\"(string)\", DeviceId=\"(string)\", Version=\"string\", Token=\"(string)\"" }
```
来源：`https://github.com/MediaBrowser/Emby.SDK/blob/master/Resources/OpenApi/openapi_v3.json` → `#/components/securitySchemes`

#### 谁发什么（实测源码）

| 客户端 | 令牌头 | 出处 |
|---|---|---|
| 当前 Web/JS 客户端 | `X-Emby-Authorization`（含 `Token=`），**或**拆分模式下的 `X-Emby-Token` | [apiclient.js#L726](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L726)、[#L703](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L703) |
| .NET ApiClient（归档） | **`X-MediaBrowser-Token`** | [HttpHeaders.cs#L26-L30](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/Net/HttpHeaders.cs#L26-L30) |

**回答"当前 Emby 发送/接受哪一个"**：
- **发送**：`X-Emby-Authorization`（内含 `Token=`）为主；新版 JS 客户端在 `separateHeaderValues` 模式下改为发 5 个独立头 `X-Emby-Client` / `X-Emby-Device-Name` / `X-Emby-Device-Id` / `X-Emby-Client-Version` / `X-Emby-Token`（[apiclient.js#L671-L703](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L671-L703)）。
- **接受**：3.5.3.0 源码明确同时接受 `X-Emby-Token`、`X-MediaBrowser-Token`、`api_key`、以及授权头里的 `Token=`。
- **4.x**：官方文档与 OpenAPI 只声明 `X-Emby-Token` / `api_key`；**4.x 服务端是否仍接受 `X-MediaBrowser-Token` 未能确认**（4.x 该文件闭源，且本次实测下载 `Emby.Releases` 二进制（4.10.0.40 freebsd tar.xz, 34.7 MB）超时失败，无法反查字符串）。

### 1.3 `GET /Users/Public`

- 官方摘要：**"Gets a list of publicly visible users for display on a login screen."**，返回 `UserDto[]`。
  来源：[getUsersPublic](https://dev.emby.media/reference/RestAPI/UserService/getUsersPublic.html)；OpenAPI `#/paths/~1Users~1Public/get`
- Wiki 补充：**"Public users are users that the server admin has allowed to be displayed visually on the login screen. … Each user has a HasPassword property. This is used to determine if the user should be prompted to input a password."**
  来源：[User-Authentication](https://github.com/MediaBrowser/Emby/wiki/User-Authentication)
- 关键返回字段（【L1-API】`#/components/schemas/UserDto`）：`Name`、`ServerId`、`ServerName`、`Prefix`、`ConnectUserName`、`ConnectLinkType`、`Id`、`PrimaryImageTag`、`HasPassword`、`HasConfiguredPassword`、`EnableAutoLogin`、`LastLoginDate`、`LastActivityDate`、`Configuration`、`Policy`、`PrimaryImageAspectRatio`、`UserItemShareLevel`。
- 官方文档 site 的返回说明里把类型渲染成 `UserDto []`（即数组）。⚠️ 注意：该静态文档在 `PlaybackProgressInfo.Item` 等数组字段上也用同一套渲染器，呈现为 `Type[]`，**读官方文档站时不要把 `Xxx[]` 当成"字段是数组"**——以 OpenAPI 的 JSON Schema 为准（例如 `Item` 实际是单个 `BaseItemDto`）。
- .NET ApiClient 调用点：[ApiClient.cs#L471](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ApiClient.cs#L471) `GetPublicUsersAsync()` → `GetApiUrl("Users/Public")`（[L473](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ApiClient.cs#L473)）。

### 1.4 `POST /Users/AuthenticateByName`

- 路径与方法（【L1-API】`#/paths/~1Users~1AuthenticateByName/post`）：`POST /Users/AuthenticateByName`，tag `UserService`，摘要 "Authenticates a user"。
- **必需请求头**：`X-Emby-Authorization`（在规范里被标记为 `required: true`，`in: header`）。这是唯一被 OpenAPI 显式声明为必需头的端点。
- **请求体**（`#/components/schemas/AuthenticateUserByName`）：只有两个字段 —— `Username`（string）、`Pw`（string 明文密码）。
- **响应体**（`#/components/schemas/Authentication.AuthenticationResult`）：`User`（`UserDto`）、`SessionInfo`（`Session.SessionInfo`）、`AccessToken`（string，"The authentication token."）、`ServerId`（string，"The server identifier."）。
- 状态码：200 = 成功；400/500 区间 = 失败。官方原文：**"Authenticate a user by nane and password. A 200 status code indicates success, while anything in the 400 or 500 range indicates failure"**
  来源：[postUsersAuthenticatebyname](https://dev.emby.media/reference/RestAPI/UserService/postUsersAuthenticatebyname.html)
- Wiki 说明：**"The password must be sent in the body: - pw - password in plain text"**（[User-Authentication](https://github.com/MediaBrowser/Emby/wiki/User-Authentication)）。

**大小写与历史兼容**：

| 客户端 | 实际发送 | 出处 |
|---|---|---|
| 当前 JS 客户端 | URL 写成 `Users/authenticatebyname`（小写），body `{"Username": name, "Pw": password \|\| ''}` | [apiclient.js#L1042-L1048](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L1042-L1048) |
| .NET ApiClient（归档） | URL `Users/AuthenticateByName`；body 走 form 编码，键为 `username` / `pw` / `password`(SHA1 十六进制) / `passwordMD5` | [ApiClient.cs#L1287-L1299](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ApiClient.cs#L1287-L1299) |

→ 服务端路径匹配不区分大小写（JS 客户端发小写仍可用）；body 键名按不区分大小写绑定，`pw` 是当前官方字段名。

### 1.5 登录后设置令牌

`.NET ApiClient`：`SetAuthenticationInfo(accessToken, userId)`（[BaseApiClient.cs#L200-L205](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/BaseApiClient.cs#L200-L205)），随后 `ResetHttpHeaders()` 把 token 写进头（[L228-L243](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/BaseApiClient.cs#L228-L243)）。

---

## 2. 播放决策端点：`/Items/{Id}/PlaybackInfo`

### 2.1 端点形态

| 方法 | 路径 | 必需参数 | 请求体 | 响应 |
|---|---|---|---|---|
| **POST** | `/Items/{Id}/PlaybackInfo` | `Id`（path） | `PlaybackInfoRequest`（`application/json` 或 `application/xml`） | `PlaybackInfoResponse` |
| **GET**（legacy） | `/Items/{Id}/PlaybackInfo` | `Id`（path）、**`UserId`（query，required）** | — | `PlaybackInfoResponse` |

来源（【L1-API】）：`openapi_v3.json` → `#/paths/~1Items~1{Id}~1PlaybackInfo/post` 与 `.../get`；
官方页：[postItemsByIdPlaybackinfo](https://dev.emby.media/reference/RestAPI/MediaInfoService/postItemsByIdPlaybackinfo.html)、[getItemsByIdPlaybackinfo](https://dev.emby.media/reference/RestAPI/MediaInfoService/getItemsByIdPlaybackinfo.html)

- 官方摘要：POST = "Gets live playback media info for an item"；"Requires authentication as user"。
- GET 变体在 4.10 规范里**仍然存在**，但只接受 `Id` + `UserId`（**不支持 DeviceProfile**，因此无法做真正的能力协商）。
  归档 .NET ApiClient 正是用 GET：`GetApiUrl("Items/" + request.Id + "/PlaybackInfo", dict)`，`dict` 里只塞了 `UserId`
  → [ApiClient.cs#L2605-L2617](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ApiClient.cs#L2605-L2617)

### 2.2 `PlaybackInfoRequest` 完整字段（【L1-API】）

来源：`openapi_v3.json` → `#/components/schemas/PlaybackInfoRequest`（官方页也逐字段列出：[postItemsByIdPlaybackinfo](https://dev.emby.media/reference/RestAPI/MediaInfoService/postItemsByIdPlaybackinfo.html)）
官方类型名：`MediaBrowser.Model.MediaInfo.PlaybackInfoRequest`
（官方 pluginapi 页：`https://dev.emby.media/reference/pluginapi/MediaBrowser.Model.MediaInfo.PlaybackInfoRequest.html`）

共 **19 个字段**（全部在 POST body 里，**没有一个是 query 参数**）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `Id` | string | 条目 Id（与 path 里的 `{Id}` 重复） |
| `UserId` | string | 用户 Id |
| `MaxStreamingBitrate` | int64 | 最大流码率（bps） |
| `StartTimeTicks` | int64 | 起始位置（100 ns tick） |
| `AudioStreamIndex` | int32 | 音轨索引 |
| `SubtitleStreamIndex` | int32 | 字幕流索引（-1 = 关闭） |
| `MaxAudioChannels` | int32 | 最大声道数 |
| `MediaSourceId` | string | 指定媒体版本 |
| `LiveStreamId` | string | 直播流 Id |
| `DeviceProfile` | `DeviceProfile` | **核心**：客户端能力声明 |
| `EnableDirectPlay` | boolean | 允许直接播放 |
| `EnableDirectStream` | boolean | 允许直接串流 |
| `EnableTranscoding` | boolean | 允许转码 |
| `AllowInterlacedVideoStreamCopy` | boolean | 允许隔行视频 stream copy |
| `AllowVideoStreamCopy` | boolean | 允许视频 stream copy |
| `AllowAudioStreamCopy` | boolean | 允许音频 stream copy |
| `IsPlayback` | boolean | 是否真正要播放（false = 仅探测） |
| `AutoOpenLiveStream` | boolean | 自动打开直播流 |
| `CurrentPlaySessionId` | string | 当前 PlaySessionId |

#### ❌ 纠正：任务书里列的参数中，这些**不在** `PlaybackInfoRequest` 里

经全量扫描 `openapi_v3.json`（同时扫 `parameters` 与所有 `requestBody` 的 schema 属性）：

| 任务书猜测 | 实情 |
|---|---|
| `SubtitleMethod` | **不在** PlaybackInfo 里。它是 `/Videos/{Id}/stream*`、`/Videos/{Id}/master.m3u8`、`/Audio/{Id}/*` 等 12 个流式端点的 **query 参数**（类型 `SubtitleDeliveryMethod`） |
| `TranscodeReasons` | **不在** PlaybackInfo 请求里（它只出现在响应侧的 `TranscodingInfo.TranscodeReasons`）。但在真实 `TranscodingUrl` 里会作为 **query 参数**回传给服务端（见 §3.3） |
| `EnableDirectPlay` 作为 query | 仅存在于 **body** |
| `AutoOpenLiveStream` | 仅存在于 body（`openapi_v3.json` 中仅 1 处：`PlaybackInfoRequest`） |
| `CurrentPlaySessionId` | 仅存在于 body（全规范仅 1 处） |
| `IsPlayback` | 仅存在于 body（全规范仅 1 处） |

### 2.3 `PlaybackInfoResponse`（【L1-API】）

来源：`openapi_v3.json` → `#/components/schemas/PlaybackInfoResponse`；官方类型 `MediaBrowser.Model.MediaInfo.PlaybackInfoResponse`

| 字段 | 类型 | 说明 |
|---|---|---|
| `MediaSources` | `MediaSourceInfo[]` | 媒体版本列表 |
| `PlaySessionId` | string | 本次播放会话 Id（后续上报与停止转码都要用） |
| `ErrorCode` | string，enum = `NotAllowed` / `NoCompatibleStream` / `RateLimitExceeded` | 失败原因（类型 `PlaybackErrorCode`） |

> `PlaybackErrorCode` 的三个值来自 `#/components/schemas/PlaybackErrorCode`。前端对 `ErrorCode` 的处理见 Emby Web 客户端 `playbackmanager.js` 的 `validatePlaybackInfoResult` / `PlaybackError{code}` 字符串。

### 2.4 `MediaSourceInfo` 完整字段（【L1-API】+【L1-DOC】）

官方类型：`MediaBrowser.Model.Dto.MediaSourceInfo`
官方页：`https://dev.emby.media/reference/pluginapi/MediaBrowser.Model.Dto.MediaSourceInfo.html`
OpenAPI：`#/components/schemas/MediaSourceInfo`
多语言生成模型（可核行号）：`https://github.com/MediaBrowser/Emby.ApiClients/blob/master/Clients/Net.RestSharp/EmbyClient.Dotnet/Model/MediaSourceInfo.cs`

| 字段 | 类型 | 备注 |
|---|---|---|
| `Chapters` | `ChapterInfo[]` | |
| `Protocol` | `MediaProtocol` | enum：`File`/`Http`/`Rtmp`/`Rtsp`/`Udp`/`Rtp`/`Ftp`/`Mms` |
| `Id` | string | 媒体源 Id |
| `Path` | string | 本地/远端路径 |
| `EncoderPath` / `EncoderProtocol` | string / `MediaProtocol` | |
| `Type` | `MediaSourceType` | enum：`Default`/`Grouping`/`Placeholder` |
| `ProbePath` / `ProbeProtocol` | string / `MediaProtocol` | |
| `Container` | string | 容器（注意：任务书把 `Container` 写作 `Container`，字符串型没错，但**它不是枚举**） |
| `Size` | int64 | |
| `Name` / `SortName` | string | |
| `IsRemote` | boolean | "Differentiate internet url vs local network" |
| `HasMixedProtocols` | boolean | |
| `RunTimeTicks` | int64 | |
| `ContainerStartTimeTicks` | int64 | |
| `SupportsTranscoding` | boolean | |
| `TrancodeLiveStartIndex` | int32 | ⚠️ 官方拼写就是 `Trancode`（少一个 `s`），**照抄不要改** |
| `WallClockStart` | date-time | |
| `SupportsDirectStream` | boolean | |
| `SupportsDirectPlay` | boolean | |
| `IsInfiniteStream` | boolean | |
| `RequiresOpening` / `OpenToken` / `RequiresClosing` | bool / string / bool | |
| `LiveStreamId` | string | |
| `BufferMs` | int32 | |
| `RequiresLooping` | boolean | |
| `SupportsProbing` | boolean | |
| `Video3DFormat` | `Video3DFormat` | enum：`HalfSideBySide`/`FullSideBySide`/`FullTopAndBottom`/`HalfTopAndBottom`/`MVC` |
| `MediaStreams` | `MediaStream[]` | |
| `Formats` | string[] | |
| `Bitrate` | int32 | |
| `Timestamp` | `TransportStreamTimestamp` | |
| `RequiredHttpHeaders` | object (map) | |
| **`DirectStreamUrl`** | string | 直接串流 URL（相对路径，客户端需自行补 `/emby` 前缀） |
| `AddApiKeyToDirectStreamUrl` | boolean | |
| **`TranscodingUrl`** | string | 转码 URL（见 §3） |
| **`TranscodingSubProtocol`** | string | 实测值 `hls` / `http`（**规范把它定义为 string，不是 enum**） |
| **`TranscodingContainer`** | string | 例：`ts` |
| `AnalyzeDurationMs` | int32 | |
| `ReadAtNativeFramerate` | boolean | |
| `DefaultAudioStreamIndex` / `DefaultSubtitleStreamIndex` | int32 | |
| `ItemId` / `ServerId` | string | "Used only by our Windows app. Not used by Emby Server." |
| `MimeType` / `TranscodingMimeType` | string | |

#### ❌ 纠正：任务书里的这些字段名在 4.10 规范中**不存在**

| 任务书猜测 | 实情 |
|---|---|
| `MediaAttachments` | **全规范中不存在**（`MediaSourceInfo`、`MediaStream`、`BaseItemDto` 都没有此字段） |
| `PlaybackMediaSource` | **不存在**（规范里只有 `MediaSourceInfo` / `PlayerStateInfo.MediaSource`） |
| `Type = Default/Grouping/Placeholder` | ✅ 正确，但要注意 `Type` 的类型名是 `MediaSourceType`（不是 `MediaSourceInfoType`） |
| `Protocol = File/Http/Hls` | ⚠️ `Hls` **不是** `MediaProtocol` 的值。`MediaProtocol` = `File/Http/Rtmp/Rtsp/Udp/Rtp/Ftp/Mms`。HLS 是 `TranscodingSubProtocol` 的取值 |

### 2.5 `MediaStream` 完整字段（【L1-API】+【L1-DOC】）

官方类型：`MediaBrowser.Model.Entities.MediaStream`
官方页：`https://dev.emby.media/reference/pluginapi/MediaBrowser.Model.Entities.MediaStream.html`
OpenAPI：`#/components/schemas/MediaStream`

任务书点名的字段全部存在：`Codec`、`Index`、`IsExternal`、`DeliveryMethod`、`DeliveryUrl`、`IsInterlaced`、`BitRate`、`Width`、`Height`、`Language`、`IsDefault`、`IsForced`、`Title`、`DisplayTitle`、`VideoRange`、`Type`。

补充字段（同 schema）：`CodecTag`、`ColorTransfer`、`ColorPrimaries`、`ColorSpace`、`Comment`、`StreamStartTimeTicks`、`TimeBase`、`Extradata`、`DisplayLanguage`、`NalLengthSize`、`IsAVC`、`ChannelLayout`、`BitDepth`、`RefFrames`、`Rotation`、`Channels`、`SampleRate`、`IsHearingImpaired`、`AverageFrameRate`、`RealFrameRate`、`Profile`、`AspectRatio`、`DeliveryFormat`、`IsExternalUrl`、`IsChunkedResponse`、`IsTextSubtitleStream`、`SupportsExternalStream`、`Path`、`Protocol`、`PixelFormat`、`Level`、`IsAnamorphic`、`ExtendedVideoType`、`ExtendedVideoSubType`、`ExtendedVideoSubTypeDescription`、`ItemId`、`ServerId`、`AttachmentSize`、`MimeType`、`SubtitleLocationType`。

官方文档还给出了每个字段与 ffprobe 字段的对应关系（例如 `IsInterlaced` ← `field_order != progressive`、`BitRate` ← `bit_rate`，并附注 **"THIS VALUE IS PROCESSED BY CUSTOM LOGIC AND DOES NOT NECESSARILY MATCH FFPROBE RESULTS!"**）。

#### 枚举值（【L1-API】，逐字摘自 `#/components/schemas/*`）

```
MediaStreamType       = Unknown, Audio, Video, Subtitle, EmbeddedImage, Attachment, Data
MediaSourceType       = Default, Grouping, Placeholder
MediaProtocol         = File, Http, Rtmp, Rtsp, Udp, Rtp, Ftp, Mms
SubtitleDeliveryMethod= Encode, Embed, External, Hls, VideoSideData
PlayMethod            = Transcode, DirectStream, DirectPlay
RepeatMode            = RepeatNone, RepeatAll, RepeatOne
ProgressEvent         = TimeUpdate, Pause, Unpause, VolumeChange, RepeatModeChange, AudioTrackChange,
                        SubtitleTrackChange, PlaylistItemMove, PlaylistItemRemove, PlaylistItemAdd,
                        QualityChange, StateChange, SubtitleOffsetChange, PlaybackRateChange,
                        ShuffleChange, SleepTimerChange
PlaybackErrorCode     = NotAllowed, NoCompatibleStream, RateLimitExceeded
TranscodeReason       = ContainerNotSupported, VideoCodecNotSupported, AudioCodecNotSupported,
                        ContainerBitrateExceedsLimit, AudioBitrateNotSupported, AudioChannelsNotSupported,
                        VideoResolutionNotSupported, UnknownVideoStreamInfo, UnknownAudioStreamInfo,
                        AudioProfileNotSupported, AudioSampleRateNotSupported, AnamorphicVideoNotSupported,
                        InterlacedVideoNotSupported, SecondaryAudioNotSupported, RefFramesNotSupported,
                        VideoBitDepthNotSupported, VideoBitrateNotSupported, VideoFramerateNotSupported,
                        VideoLevelNotSupported, VideoProfileNotSupported, AudioBitDepthNotSupported,
                        SubtitleCodecNotSupported, DirectPlayError, VideoRangeNotSupported,
                        SubtitleContentOptionsEnabled, ExternalAudioNotSupported, AudioDelayNotSupported
SleepTimerMode        = None, AfterItem, AtTime
SubtitlePlaybackMode  = Default, Always, OnlyForced, None, Smart, HearingImpaired
```

#### ❌ 纠正：`DeliveryMethod` 的取值

任务书猜测 `Encode/Embed/External/Hls/Drop`。**规范里的 4.10 取值是 `Encode, Embed, External, Hls, VideoSideData` —— 没有 `Drop`**，多了一个 `VideoSideData`。
（`Drop` 在更老的 Emby 版本中存在过，但在 4.10 规范里查无此值 → **4.x 中不存在该取值**。）

### 2.6 Emby Web 客户端实际发出的 PlaybackInfo 调用（【L1-CLIENT】，佐证）

`MediaBrowser/emby-webcomponents` → `playback/playbackmanager.js`（未压缩版）：

- `query.IsPlayback` / `query.AutoOpenLiveStream`：[L500-L504](https://github.com/MediaBrowser/emby-webcomponents/blob/master/playback/playbackmanager.js#L500-L504)
- `query.EnableDirectPlay` [L514](https://github.com/MediaBrowser/emby-webcomponents/blob/master/playback/playbackmanager.js#L514)、`query.EnableDirectStream` [L518](https://github.com/MediaBrowser/emby-webcomponents/blob/master/playback/playbackmanager.js#L518)
- `query.AllowVideoStreamCopy` [L521](https://github.com/MediaBrowser/emby-webcomponents/blob/master/playback/playbackmanager.js#L521)、`query.AllowAudioStreamCopy` [L524](https://github.com/MediaBrowser/emby-webcomponents/blob/master/playback/playbackmanager.js#L524)
- `query.MaxStreamingBitrate` [L533](https://github.com/MediaBrowser/emby-webcomponents/blob/master/playback/playbackmanager.js#L533)
- `query.EnableMediaProbe` [L536](https://github.com/MediaBrowser/emby-webcomponents/blob/master/playback/playbackmanager.js#L536)、`query.CurrentPlaySessionId` [L539](https://github.com/MediaBrowser/emby-webcomponents/blob/master/playback/playbackmanager.js#L539)
- `query.DirectPlayProtocols` [L543](https://github.com/MediaBrowser/emby-webcomponents/blob/master/playback/playbackmanager.js#L543)
- 最终 POST：[apiclient.js#L1889-L1901](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L1889-L1901) —— `POST Items/{itemId}/PlaybackInfo`，body 只放 `{"DeviceProfile": deviceProfile}`，其余全走 query string。

⚠️ 注意 `EnableMediaProbe` 与 `DirectPlayProtocols` **在 4.10 OpenAPI 规范里查不到**（规范里 `EnableMediaProbe`、`DirectPlayProtocols` 均不存在）。它们由 Emby Web 客户端发送，属于规范未覆盖的参数 → **文档缺失，但客户端确实在用**（源码可核）。

---

## 3. 转码 / 流式端点

### 3.1 端点总表（【L1-API】`openapi_v3.json` → `#/paths/...`）

| 方法 | 路径 | 官方 tag | OpenAPI 列出的 query 参数 | 备注 |
|---|---|---|---|---|
| GET | `/Videos/{Id}/stream` | `VideoService` | 24 个（见下） | 通用视频流 |
| GET | `/Videos/{Id}/stream.{Container}` | `VideoService` | 同上（`Container` 变成 path 参数） | 带扩展名别名 |
| GET | `/Videos/{Id}/master.m3u8` | `DynamicHlsService` | 同上 24 个 | HLS 主播放列表 |
| GET | `/Videos/{Id}/main.m3u8` | `DynamicHlsService` | 同上 24 个 | |
| GET | `/Videos/{Id}/live.m3u8` | `DynamicHlsService` | 同上 24 个 | |
| GET | `/Videos/{Id}/subtitles.m3u8` | `DynamicHlsService` | `Id`、`SubtitleSegmentLength`、`ManifestSubtitles` | 字幕专用清单 |
| GET | `/Videos/{Id}/live_subtitles.m3u8` | `DynamicHlsService` | — | |
| GET/HEAD | `/Videos/{Id}/hls1/{PlaylistId}/{SegmentId}.{SegmentContainer}` | `DynamicHlsService` | **只有 path 参数 4 个**：`SegmentContainer`、`SegmentId`、`Id`、`PlaylistId` | TS/fMP4 分片 |
| GET | `/Videos/{Id}/hls/{PlaylistId}/{SegmentId}.{SegmentContainer}` | `DynamicHlsService` | 同上 | 旧版分片路径 |
| GET/HEAD | `/Videos/{Id}/{StreamFileName}` | `VideoService` | — | 文件名别名（如 `stream.mp4`） |
| **DELETE** | `/Videos/ActiveEncodings` | `HlsSegmentService` | **`DeviceId`（required）、`PlaySessionId`（required）** | **停止转码** |
| POST | `/Videos/ActiveEncodings/Delete` | `HlsSegmentService` | 同上 | 不支持 DELETE 的客户端用这个 |
| GET | `/Playback/BitrateTest` | `MediaInfoService` | `Size` | 码率探测 |

`/Videos/{Id}/stream`（及其全部别名）的 **24 个 query 参数**（逐字，注意大小写）：

```
DeviceProfileId, Id(path/必需), DeviceId, Container(必需), AudioCodec, EnableAutoStreamCopy,
AudioSampleRate, AudioBitRate, AudioChannels, MaxAudioChannels, Static, CopyTimestamps,
StartTimeTicks, Width, Height, MaxWidth, MaxHeight, VideoBitRate, SubtitleStreamIndex,
SubtitleMethod, MaxVideoBitDepth, VideoCodec, AudioStreamIndex, VideoStreamIndex
```
（`Id` 与 `Container` 为必需；`master.m3u8` 的 `Container` 亦为必需）

#### ❌ 大小写纠正（很重要，写客户端会踩）

| 任务书写法 | 规范里的真实写法 |
|---|---|
| `AudioBitrate` | **`AudioBitRate`**（大写 R） |
| `VideoBitrate` | **`VideoBitRate`**（大写 R） |
| `MaxStreamingBitrate`（PlaybackInfo body） | ✅ 这个确实是 `MaxStreamingBitrate`（小写 r）—— **两个端点的大小写不一致，别抄错** |

#### ❌ 任务书列出但**不是**这些流式端点 query 参数的项

经过对全规范（参数 + 所有 requestBody 属性）扫描，以下名字 **在整个 4.10 规范中不存在**：

```
Params, TranscodeReasons, SegmentLength, MinSegments, BreakOnNonKeyFrames,
AllowInterlacedVideoStreamCopy(在流式端点上), RequireAvc, Profile, TranscodingMaxAudioChannels,
VideoLevel, EnableAutoStreamCopy 以外的 …（见下）
```

但**它们并非凭空存在**，而是出现在别处：

| 名字 | 真实归属 | 出处 |
|---|---|---|
| `TranscodeReasons` | **响应** `TranscodingInfo.TranscodeReasons`（`TranscodeReason[]`）；同时作为 query 出现在真实 `TranscodingUrl` 里回传 | `#/components/schemas/TranscodingInfo`；§3.3 实例 |
| `TranscodingMaxAudioChannels` | `DeviceProfile` 相关；真实 `TranscodingUrl` query 中出现 | §3.3 实例 |
| `MinSegments`、`SegmentLength`、`BreakOnNonKeyFrames`、`AllowInterlacedVideoStreamCopy`、`ManifestSubtitles`、`MaxWidth`、`MaxHeight` | **`MediaBrowser.Model.Dlna.TranscodingProfile` 的字段**（客户端在 `DeviceProfile.TranscodingProfiles[]` 里声明，服务端据此生成 URL，而不是作为独立 query 参数） | 官方页 [postItemsByIdPlaybackinfo](https://dev.emby.media/reference/RestAPI/MediaInfoService/postItemsByIdPlaybackinfo.html) 的 `TranscodingProfile` 定义节 |
| `Profile` | 只在 `/Sync/Jobs`、`/Sync/Jobs/{Id}` 的 `SyncJob` body 里 | `#/components/schemas/SyncJob` |
| `Level` | 只在 `/Notifications/Admin` 的 query 里 | `#/paths/~1Notifications~1Admin/post` |
| `RequireAvc`、`Params`、`VideoLevel` | **全规范中不存在** | — |

> `TranscodingProfile` 的完整字段（【L1-DOC】，`MediaBrowser.Model.Dlna.TranscodingProfile`）：
> `Container`、`Type`(`DlnaProfileType`)、`VideoCodec`、`AudioCodec`、`Protocol`、`EstimateContentLength`、`EnableMpegtsM2TsMode`、`TranscodeSeekInfo`、`CopyTimestamps`、`Context`(`EncodingContext`)、`MaxAudioChannels`、`MinSegments`、`SegmentLength`、`BreakOnNonKeyFrames`、`AllowInterlacedVideoStreamCopy`、`ManifestSubtitles`、`MaxManifestSubtitles`、`MaxWidth`、`MaxHeight`、`FillEmptySubtitleSegments`
> 出处：[postItemsByIdPlaybackinfo](https://dev.emby.media/reference/RestAPI/MediaInfoService/postItemsByIdPlaybackinfo.html)
> `DlnaProfileType` = `Audio, Video, Photo`；`EncodingContext` = `Streaming, Static`；`TranscodeSeekInfo` = `Auto, Bytes`

#### `SubtitleMethod` 取值

类型 = `SubtitleDeliveryMethod`，取值 = `Encode, Embed, External, Hls, VideoSideData`（**无 `Drop`**，见 §2.5）。

#### `Static` 的含义（【L1-WIKI】）

> **"To direct stream a video file, simply use the static=true parameter."**
> **"When direct streaming, the file will be served statically and client-side seeking will be possible. When transcoding, this will not be possible. In order to seek you'll have to stop the stream and start a new one using the StartTimeTicks parameter."**

来源：[Video-Streaming](https://github.com/MediaBrowser/Emby/wiki/Video-Streaming)

### 3.2 ⚠️ OpenAPI 在这些端点上**不完整**（实测发现）

官方 Wiki 明确列出**必需参数**，而这些参数**在 4.10 OpenAPI 规范里查不到**：

[Video-Streaming](https://github.com/MediaBrowser/Emby/wiki/Video-Streaming) 原文：

> The following parameters are required:
> * Id
> * **MediaSourceId**
> * **PlaySessionId** - comes from the PlaybackInfo response as part of our MediaSource api. If you are not using the MediaSource api, then generate a random alpha-numeric string.

[Http-Live-Streaming](https://github.com/MediaBrowser/Emby/wiki/Http-Live-Streaming) 原文：

> The required paramaters are:
> * Id (in path)
> * **MediaSourceId**
> * **DeviceId**
> … After playback is complete, it is necessary to inform the server to stop any related HLS transcoding. This is accomplished via an HTTP DELETE to:
> `/Videos/ActiveEncodings?DeviceId=xxx`

**实测核对**：`openapi_v3.json` 中 `/Videos/{Id}/stream`、`/Videos/{Id}/master.m3u8` 的参数列表里 **没有 `MediaSourceId`、`PlaySessionId`、`api_key`**；`/Videos/ActiveEncodings` 的 DELETE 参数里也**没有 `DeviceId`（只有 `PlaySessionId`）**，而 Wiki 写的是 `?DeviceId=xxx`，4.10 规范写的是 `DeviceId` + `PlaySessionId` **都 required**。

→ **结论：`/Videos/*` 流式端点的 OpenAPI 参数表不完整，必须以官方 Wiki + 真实 PlaybackInfo 返回的 `TranscodingUrl` 为准。**（这也解释了为什么很多第三方实现"照 OpenAPI 写"会失败。）

### 3.3 `TranscodingUrl` 的真实形态（【L3】第三方捕获的真实响应）

**出处**：`AkimioJR/MediaWarp`（一个 Emby/Jellyfin 中间件）的开发者文档，明确标注 **"EmbyServer version 4.8"** 的 `playbackInfo` 真实响应：
`https://github.com/AkimioJR/MediaWarp/blob/070ad99cb32e940b2b8ccb7c55b6efb7d311eac5/docs/DEV.md#L75-L85`

第 77 行原文（逐字复制）：

```
"TranscodingUrl": "/videos/61162/master.m3u8?DeviceId=e12ab84a-2948-463a-9cb0-ecebcac2949e&MediaSourceId=37bcb3827aeb2095f1b6293e9a189072&PlaySessionId=e643532a23ca4a60b2c70d4210c018a9&api_key=e824c422e02047dfa820dbcab5490bb9&VideoCodec=h264,h265,hevc,av1&VideoBitrate=1400000&TranscodingMaxAudioChannels=2&SegmentContainer=ts&MinSegments=1&BreakOnNonKeyFrames=True&SubtitleStreamIndexes=-1&ManifestSubtitles=vtt&h264-profile=high,main,baseline,constrainedbaseline,high10&h264-level=62&hevc-codectag=hvc1,hev1,hevc,hdmv&TranscodeReasons=ContainerBitrateExceedsLimit"
```

同一响应里的其它关键字段（同文件 L75-L84）：

```json
"DirectStreamUrl": "/videos/61162/master.m3u8?DeviceId=…&MediaSourceId=…&PlaySessionId=…&api_key=…&…",
"AddApiKeyToDirectStreamUrl": false,
"TranscodingSubProtocol": "hls",
"TranscodingContainer": "ts",
"ReadAtNativeFramerate": false,
"ItemId": "61162"
…
"PlaySessionId": "e643532a23ca4a60b2c70d4210c018a9"
```

第二个样例（普通视频，同文件 L466-L469）额外出现 `AudioCodec=ac3,mp3,aac`、`AudioBitrate=320000`、`AudioStreamIndex=1`。

**从真实样例中可确证的 `TranscodingUrl` query 参数集合**（这是**服务端生成**的，不是客户端拼的）：
```
DeviceId, MediaSourceId, PlaySessionId, api_key,
VideoCodec, AudioCodec, VideoBitrate, AudioBitrate, AudioStreamIndex,
TranscodingMaxAudioChannels, SegmentContainer, MinSegments, BreakOnNonKeyFrames,
SubtitleStreamIndexes, ManifestSubtitles, h264-profile, h264-level, hevc-codectag,
TranscodeReasons
```

**溯源与可信度说明**：
- 路径前缀写的是小写 `/videos/`（客户端调用前会经 `apiClient.getUrl()` 拼接 baseurl；Emby 路由不区分大小写）。
- `VideoCodec=h264,h265,hevc,av1`、`h264-profile=…` 这类**带连字符的动态键**（`h264-profile`、`h264-level`、`hevc-codectag`）来自客户端提交的 `DeviceProfile.CodecProfiles` 条件。它们**不在 OpenAPI 规范里**，是 Emby 特有的"把 profile 条件摊平进 query"的做法。
- ⚠️ `SubtitleStreamIndexes`（复数）与规范里的 `SubtitleStreamIndex`（单数）**不是同一个名字**——复数形式在 OpenAPI 中不存在。
- 该样例来源为**第三方项目文档中的实机捕获**（【L3】），**不是 Emby 官方规范**。用于"确证真实线上形态"是充分的，但不能当作规范条文。

**早期形态（【L3】，2015 年社区帖，仅供对比）**：
`{server}/emby/Videos/{itemid}/master.m3u8?MediaSourceId={itemid}&VideoCodec=h264&AudioCodec=ac3&MaxAudioChannels=6&deviceId={deviceid}&VideoBitrate=664000&AudioStreamIndex=1&SubtitleStreamIndex=5&AudioBitrate=384000`
出处：`https://emby.media/community/index.php?/topic/28926-transcode/`
（该帖是用户手拼的 URL，`deviceId` 小写写法未被规范支持；Emby 管理员 Luke 在同一帖确认"条目的默认媒体源使用条目 Id 作为 MediaSourceId"。）

### 3.4 停止转码

| 方法 | 路径 | 参数 | 出处 |
|---|---|---|---|
| DELETE | `/Videos/ActiveEncodings` | `DeviceId`(req), `PlaySessionId`(req) | `openapi_v3.json` `#/paths/~1Videos~1ActiveEncodings/delete` |
| POST | `/Videos/ActiveEncodings/Delete` | 同上 | `#/paths/~1Videos~1ActiveEncodings~1Delete/post` |

官方文档页：[deleteVideosActiveencodings](https://dev.emby.media/reference/RestAPI/HlsSegmentService/deleteVideosActiveencodings.html)

当前 JS 客户端实现（`stopActiveEncodings(playSessionId)`）只带 `deviceId` + 可选 `PlaySessionId`：
[apiclient.js#L2665-L2681](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L2665-L2681)
（注意它传的 query 键名是 `deviceId` 小写 —— 与规范里的 `DeviceId` 不同；Emby 服务端 query 绑定不区分大小写。）

---

## 4. 会话与进度上报（Session / Playback Reporting）

### 4.1 端点总表

| 方法 | 路径 | 请求体 / 参数 | 官方 tag | 来源 |
|---|---|---|---|---|
| POST | `/Sessions/Playing` | body `PlaybackStartInfo` | `PlaystateService` | `#/paths/~1Sessions~1Playing/post` |
| POST | `/Sessions/Playing/Progress` | body `PlaybackProgressInfo` | `PlaystateService` | `#/paths/~1Sessions~1Playing~1Progress/post` |
| POST | `/Sessions/Playing/Stopped` | body `PlaybackStopInfo` | `PlaystateService` | `#/paths/~1Sessions~1Playing~1Stopped/post` |
| **POST** | **`/Sessions/Playing/Ping`** | **query `PlaySessionId`** | `PlaystateService` | `#/paths/~1Sessions~1Playing~1Ping/post` |
| GET | `/Sessions` | query `ControllableByUserId`、`DeviceId`、`Id` | `SessionsService` | 见 §4.4 |
| POST | `/Sessions/Capabilities` | query `Id`(req)、`PlayableMediaTypes`、`SupportedCommands`、`SupportsMediaControl`、`SupportsSync` | `SessionsService` | 见 §4.5 |
| POST | `/Sessions/Capabilities/Full` | query `Id`(req) + body `ClientCapabilities` | `SessionsService` | 见 §4.5 |
| POST | `/Sessions/Logout` | — | `SessionsService` | 撤销 token |
| GET | `/Sessions/PlayQueue` | query `Id`、`DeviceId` → `QueryResult<BaseItemDto>` | `SessionsService` | |
| POST | `/Sessions/{Id}/Playing` | query `ItemIds`(req)、`StartPositionTicks`、`PlayCommand`(req) + body `PlayRequest` | `SessionsService` | |
| POST | `/Sessions/{Id}/Playing/{Command}` | path `Command`(`PlaystateCommand`) + body `PlaystateRequest` | `SessionsService` | |
| POST | `/Sessions/{Id}/Command` | body `GeneralCommand` | `SessionsService` | |
| POST | `/Sessions/{Id}/Command/{Command}` | path `Command`（string，无 schema） | `SessionsService` | |
| POST | `/Sessions/{Id}/System/{Command}` | path `Command` | `SessionsService` | |
| POST | `/Sessions/{Id}/Message` | query `Text`(req)、`Header`(req)、`TimeoutMs` | `SessionsService` | |
| POST | `/Sessions/{Id}/Viewing` | query `ItemType`、`ItemId`、`ItemName` | `SessionsService` | 让会话浏览到某条目 |
| POST/DELETE | `/Sessions/{Id}/Users/{UserId}` | — | `SessionsService` | 多用户共用会话 |

### 4.2 `/Sessions/Playing/Ping` —— 存在（任务书要求"确认或否认"）

**确认存在。** 
- 【L1-API】`openapi_v3.json` → `#/paths/~1Sessions~1Playing~1Ping/post`：摘要 **"Pings a playback session"**，参数 **`PlaySessionId`（query，string）**，响应 200 = "Operation successful. Empty response."
- 【L1-DOC】`https://dev.emby.media/reference/RestAPI/PlaystateService/postSessionsPlayingPing.html`（本次实测 HTTP 200，页面内容与上述一致）

### 4.3 `PlaybackProgressInfo` / `PlaybackStartInfo` / `PlaybackStopInfo` 完整字段

【L1-API】`#/components/schemas/PlaybackProgressInfo`、`.../PlaybackStartInfo`、`.../PlaybackStopInfo`
官方页：[postSessionsPlayingProgress](https://dev.emby.media/reference/RestAPI/PlaystateService/postSessionsPlayingProgress.html)
官方类型：`MediaBrowser.Model.Session.PlaybackProgressInfo` 等

**`PlaybackProgressInfo`（= `PlaybackStartInfo` 的全部字段）共 31 个字段：**

| 字段 | 类型 | 备注 |
|---|---|---|
| `CanSeek` | boolean | "A value indicating whether this instance can seek." |
| `NowPlayingQueue` | `QueueItem[]` | `QueueItem` = `{ Id:int64, PlaylistItemId:string }` |
| `PlaylistItemId` | string | |
| `SessionId` | string | "The session id." |
| `AudioStreamIndex` | int32 | |
| `SubtitleStreamIndex` | int32 | |
| `IsPaused` | boolean | |
| `PlaylistIndex` | int32 | |
| `PlaylistLength` | int32 | |
| `IsMuted` | boolean | |
| `RunTimeTicks` | int64 | |
| `PlaybackStartTimeTicks` | int64 | |
| `VolumeLevel` | int32 | "0-100"（Wiki） |
| `Brightness` | int32 | |
| `AspectRatio` | string | |
| **`EventName`** | `ProgressEvent` | 见下方枚举 |
| **`PlayMethod`** | `PlayMethod` | `Transcode` / `DirectStream` / `DirectPlay` |
| `RepeatMode` | `RepeatMode` | `RepeatNone` / `RepeatAll` / `RepeatOne` |
| `SleepTimerMode` | `SleepTimerMode` | `None` / `AfterItem` / `AtTime` |
| `SleepTimerEndTime` | date-time | |
| `Shuffle` | boolean | |
| `SubtitleOffset` | int32 | |
| `PlaybackRate` | double | |
| `PlaylistItemIds` | string[] | |
| `PlaySessionId` | string | "The play session identifier." |
| `ItemId` | string | "The item identifier." |
| `LiveStreamId` | string | |
| `MediaSourceId` | string | "The media version identifier." |
| `Item` | `BaseItemDto` | 播放**非库内**内容时用它替代 `ItemId`（见 Wiki） |
| `PositionTicks` | int64 | "The position ticks." |

**`PlaybackStopInfo` 共 16 个字段：**
`NowPlayingQueue`、`PlaylistItemId`、`PlaylistIndex`、`PlaylistLength`、`SessionId`、`IsAutomated`、**`Failed`**（"A value indicating whether this MediaBrowser.Model.Session.PlaybackStopInfo is failed."）、`NextMediaType`、`PlaySessionId`、`ItemId`、`LiveStreamId`、`MediaSourceId`、`Item`、`PositionTicks`
（**注意 `PlaybackStopInfo` 里没有 `PlayMethod` / `PositionTicks` 之外的进度字段**；它有 `Failed` 和 `IsAutomated` 这两个独有字段。）

#### `EventName` 取值（【L1-API】`#/components/schemas/ProgressEvent`）

```
TimeUpdate, Pause, Unpause, VolumeChange, RepeatModeChange, AudioTrackChange, SubtitleTrackChange,
PlaylistItemMove, PlaylistItemRemove, PlaylistItemAdd, QualityChange, StateChange,
SubtitleOffsetChange, PlaybackRateChange, ShuffleChange, SleepTimerChange
```

#### ⚠️ 大小写：`EventName` 的序列化值

任务书猜测为小写 `timeupdate`/`pause`/…。**规范里的枚举值是 PascalCase（`TimeUpdate`/`Pause`/…）**。
但 Emby Web 客户端**发送的是小写**：
- `sendProgressUpdate(player, "timeupdate")`、`"pause"`、`"unpause"`、`"volumechange"`、`"repeatmodechange"`、`"playlistitemmove"`、`"playlistitemremove"`、`"playlistitemadd"`
  出处（未压缩）：[playbackmanager.js](https://github.com/MediaBrowser/emby-webcomponents/blob/master/playback/playbackmanager.js) 中 `events.on(player,"timeupdate",onPlaybackTimeUpdate)` 等绑定；`reportPlayback(…)` 里 `info.EventName = progressEventName`
- JS ApiClient 内部也用小写判断：`if ((options.EventName || 'timeupdate') === 'timeupdate')` → [apiclient.js#L4285](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L4285)
- 词典翻译文件里同时存在 `"TimeUpdate"` 键（`emby-webcomponents/strings/*.json`）。

→ **服务端接受大小写不敏感的值（否则官方 Web 客户端会失效），但规范文档写的是 PascalCase。写第三方客户端时建议照 Web 客户端用小写，或照规范用 PascalCase —— 二者都能被当前服务端接受这一点，✅ 已由 Web 客户端实际行为与规范枚举并存佐证；服务端反序列化细节因闭源 未能逐行确认。**

#### Wiki 里"过时/超前"的字段

[Playback-Check-ins](https://github.com/MediaBrowser/Emby/wiki/Playback-Check-ins) 里列了一个 **`QueueableMediaTypes`** 字段，**在 4.10 规范中不存在**（`PlaybackProgressInfo`/`PlaybackStartInfo`/`PlaybackStopInfo` 均无此字段；`SessionInfo` 里也没有）→ 该 Wiki 段落已过时。

Wiki 里也列出的 `SubtitleOffset`、`PlaybackRate`、`PlaylistIndex`、`PlaylistLength` ✅ 均存在于 4.10 规范。

### 4.4 `GET /Sessions`

- 【L1-API】`#/paths/~1Sessions/get`，tag `SessionsService`，摘要 "Gets a list of sessions"，返回 `Session.SessionInfo[]`（**数组**）。
  参数：`ControllableByUserId`、`DeviceId`、`Id`（均为可选 query）。"Requires authentication as user"。
  官方页：[getSessions](https://dev.emby.media/reference/RestAPI/SessionsService/getSessions.html)
- 【L1-SRC】3.5.3.0 的 `MediaBrowser.Api/Session/SessionsService.cs`：
  - 路由 [L21](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L21) `[Route("/Sessions", "GET", ...)]`，返回类型 `IReturn<SessionInfo[]>`
  - `ControllableByUserId` [L25-L26](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L25-L26)（"Filter by sessions that a given user is allowed to remote control."）
  - `DeviceId` [L28-L29](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L28-L29)
  - `ActiveWithinSeconds` [L31](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L31)（**4.10 规范里没有这个参数** → 该参数已被移除或未文档化）
- `Session.SessionInfo` 字段（【L1-API】`#/components/schemas/Session.SessionInfo`，官方类型 `MediaBrowser.Model.Session.SessionInfo`）：
  `PlayState`(`PlayerStateInfo`)、`AdditionalUsers`(`SessionUserInfo[]`)、`RemoteEndPoint`、`Protocol`、`PlayableMediaTypes`、`PlaylistItemId`、`PlaylistIndex`、`PlaylistLength`、`Id`、`ServerId`、`UserId`、`PartyId`、`UserName`、`UserPrimaryImageTag`、`Client`、`LastActivityDate`、`DeviceName`、`DeviceType`、`NowPlayingItem`(`BaseItemDto`)、`InternalDeviceId`、`DeviceId`、`ApplicationVersion`、`AppIconUrl`、`SupportedCommands`(string[])、`TranscodingInfo`、`SupportsRemoteControl`
- `TranscodingInfo`（转码进度，同一响应里读）：
  `AudioCodec`、`VideoCodec`、`SubProtocol`、`Container`、`IsVideoDirect`、`IsAudioDirect`、`Bitrate`、`AudioBitrate`、`VideoBitrate`、`Framerate`、`CompletionPercentage`、`TranscodingPositionTicks`、`TranscodingStartPositionTicks`、`Width`、`Height`、`AudioChannels`、`TranscodeReasons`(`TranscodeReason[]`)、`CurrentCpuUsage`、`AverageCpuUsage`、`CpuHistory`、`ProcessStatistics`、`CurrentThrottle`、`VideoDecoder`、`VideoDecoderIsHardware`、`VideoDecoderMediaType`、`VideoDecoderHwAccel`、`VideoEncoder`、`VideoEncoderIsHardware`、`VideoEncoderMediaType`、`VideoEncoderHwAccel`、`VideoPipelineInfo`、`SubtitlePipelineInfos`

**远程控制命令（【L1-WIKI】[Remote-control](https://github.com/MediaBrowser/Emby/wiki/Remote-control)）**：

- 播放指令 `POST /Sessions/{Id}/Playing`，post data：`ItemIds`（逗号分隔）、`PlayCommand`（`PlayNow`/`PlayNext`/`PlayLast`）、`StartPositionTicks`、`MediaSourceId`、`AudioStreamIndex`、`SubtitleStreamIndex`、`StartIndex`。
  4.10 规范补充 `PlayCommand` 还支持 `PlayInstantMix`、`PlayShuffle`（`#/components/schemas/PlayCommand`）。
- 播放状态指令 `/Sessions/{Id}/Playing/{Command}`：`Stop`、`Pause`、`Unpause`、`PlayPause`、`NextTrack`、`PreviousTrack`、`Seek?SeekPositionTicks=xxx`
  4.10 规范的 `PlaystateCommand` = `Stop, Pause, Unpause, NextTrack, PreviousTrack, Seek, Rewind, FastForward, PlayPause, SeekRelative`（比 Wiki 多了 `Rewind`/`FastForward`/`SeekRelative`）。
  `PlaystateRequest`（body）= `Command`(`PlaystateCommand`)、`SeekPositionTicks`、`ControllingUserId`。
- 通用指令 `/Sessions/{Id}/Command/{CommandName}`，body `{"Arguments":{"Name":"Value"}}`；`GeneralCommand` schema = `Name`(string)、`ControllingUserId`(string)、`Arguments`(object)。
- Wiki 列出的命令名（含参数）：`MoveUp, MoveDown, MoveLeft, MoveRight, PageUp, PageDown, PreviousLetter, NextLetter, ToggleOsdMenu, ToggleContextMenu, Select, Back, TakeScreenshot, SendKey, SendString(String), GoHome, GoToSettings, VolumeUp, VolumeDown, Mute, Unmute, ToggleMute, SetVolume(Volume 0-100), SetAudioStreamIndex(Index), SetSubtitleStreamIndex(Index; -1 关闭), ToggleFullscreen, DisplayContent(ItemName,ItemId,ItemType), GoToSearch, PlayMediaSource, PlayTrailers(ItemId), DisplayMessage(Header,Text,TimeoutMs), SetPlaybackRate(PlaybackRate), SetSubtitleOffset(SubtitleOffset), IncrementSubtitleOffset(Increment)`
  ⚠️ `GeneralCommandType` 这个枚举 **在 4.10 规范中不存在**（`GeneralCommand.Name` 是裸 string）→ 命令名清单**只有 Wiki 有权威列表**。
- 显示消息：`POST /Sessions/{Id}/Message`，参数 `Header`(req)、`Text`(req)、`TimeoutMs`（Wiki 说"若省略 TimeoutMs 则为模态"）。`MessageCommand` schema **在 4.10 规范中不存在**。

### 4.5 客户端能力注册 `POST /Sessions/Capabilities/Full`

- 【L1-API】`#/paths/~1Sessions~1Capabilities~1Full/post`：query 参数 **`Id`（required）**，body schema **`ClientCapabilities`**。
  官方页：[postSessionsCapabilitiesFull](https://dev.emby.media/reference/RestAPI/SessionsService/postSessionsCapabilitiesFull.html)
- `ClientCapabilities` 字段（4.10，`#/components/schemas/ClientCapabilities`）：
  `PlayableMediaTypes`(string[])、`SupportedCommands`(string[])、`SupportsMediaControl`(bool)、`PushToken`(string)、`PushTokenType`(string)、`SupportsSync`(bool)、`DeviceProfile`(`DeviceProfile`)、`IconUrl`(string)、`AppId`(string)
- 旧的 query 版 `POST /Sessions/Capabilities`：`Id`(req)、`PlayableMediaTypes`、`SupportedCommands`、`SupportsMediaControl`、`SupportsSync`。
- 【L1-SRC】3.5.3.0 `MediaBrowser.Api/Session/SessionsService.cs`：
  - `/Sessions/Capabilities` 路由 [L187](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L187)，字段 `Id` [L195](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L195)、`PlayableMediaTypes`（"Audio, Video, Book, Game, Photo."）[L198](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L198)、`SupportedCommands` [L201](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L201)、`SupportsMediaControl` [L204](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L204)、`SupportsSync` [L207](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L207)、**`SupportsPersistentIdentifier`** [L210](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L210)（默认 true）
  - `/Sessions/Capabilities/Full` 路由 [L219](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L219)：`PostFullCapabilities : ClientCapabilities` + `Id` query 参数 [L227](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L227)
- ❌ 任务书里的 **`SupportedLiveStreams`** 字段：**在 4.10 规范与 3.5.3.0 源码中都不存在**。
- ✅ 任务书里的 `PlayableMediaTypes`、`SupportedCommands`、`SupportsMediaControl`、`SupportsSync` 均确认存在。
- 客户端调用点：[apiclient.js#L2683-L2693](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L2683-L2693)（`POST Sessions/Capabilities/Full`，body 为整个 options 对象）；[ApiClient.cs#L1717-L1727](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ApiClient.cs#L1717-L1727)。

### 4.6 WebSocket 上报（【L1-WIKI】+【L1-SRC】）

- Wiki：**"For improved performance, the progress messages can also be sent to the server via the web socket connection. The structure of the messages are as follows: `{MessageType: "ReportPlaybackProgress", Data: {...}}`"**
  来源：[Playback-Check-ins](https://github.com/MediaBrowser/Emby/wiki/Playback-Check-ins)
- .NET ApiClient 实现了这条路径：`if (IsWebSocketConnected) return SendWebSocketMessage("ReportPlaybackProgress", …)`
  → [ApiClient.cs#L1077-L1080](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ApiClient.cs#L1077-L1080)
- 3.5.3.0 服务端从 query string 取 `api_key` 用于 WebSocket 认证：[SessionWebSocketListener.cs#L77](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/Session/SessionWebSocketListener.cs#L77)

### 4.7 上报节奏（【L1-WIKI】）

> Playback progress should be reported at the following times: **Automatically every 10 seconds**; **Immediately following any user interaction with the player, for example, pause, un-pause, etc.**
> "The server will automatically increment playback progress every second, so it is not necessary to automatically report more often than at 10 second intervals."

来源：[Playback-Check-ins](https://github.com/MediaBrowser/Emby/wiki/Playback-Check-ins)

当前 JS 客户端在 `reportPlaybackProgress` 里做了 10 秒节流：
[apiclient.js#L4285-L4300](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L4285-L4300)（`if (msSinceLastReport <= 10000) …`）

---

## 5. Emby Connect 与多用户

### 5.1 Connect 云服务端点（【L1-WIKI】+【L1-CLIENT】）

**Base URL**：`https://connect.emby.media/service/` —— 由 .NET ApiClient 硬编码：
[ConnectService.cs#L255-L262](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ConnectService.cs#L255-L262)
```csharp
private string GetConnectUrl(string handler) { return "https://connect.emby.media/service/" + handler; }
```

| 方法 | 相对路径 | 用途 | 出处 |
|---|---|---|---|
| POST | `user/authenticate` | 用户名/密码登录 Connect | [ConnectService.cs#L49-L59](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ConnectService.cs#L49-L59) |
| POST | `user/logout` | 注销 | [#L62-L73](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ConnectService.cs#L62-L73) |
| POST | `pin` | 创建 PIN（TV 端登录） | [#L76-L83](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ConnectService.cs#L76-L83) |
| GET | `pin` | 查询 PIN 状态 | [#L86-L93](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ConnectService.cs#L86-L93) |
| POST | `pin/authenticate` | 用 PIN 换取 ConnectAccessToken | [#L110-L118](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ConnectService.cs#L110-L118) |
| GET | `user` | 查询 Connect 用户 | [#L145-L175](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ConnectService.cs#L145-L175) |
| GET | `servers?userId={ConnectUserId}` | 列出该用户的服务器 | [#L193-L208](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ConnectService.cs#L193-L208) |
| POST | `register` | 注册新 Connect 账号 | [#L264-L284](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ConnectService.cs#L264-L284) |

**请求头**（源码逐行）：
- `X-Application: {appName}/{appVersion}` → [ConnectService.cs#L252](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ConnectService.cs#L252)
- `X-Connect-UserToken: {accessToken}` → [ConnectService.cs#L239](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ConnectService.cs#L239)
- 注册时：`X-Connect-Token: CONNECT-REGISTER` → [ConnectService.cs#L295](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ConnectService.cs#L295)

**Wiki 记载的等价流程（【L1-WIKI】[Emby-Connect](https://github.com/MediaBrowser/Emby/wiki/Emby-Connect)）**：
- 登录：`POST https://connect.emby.media/service/user/authenticate`，`Content-Type: application/json`，body `{ nameOrEmail: username, rawpw: password }`，头 `X-Application: AppName/AppVersion`；成功返回 **`ConnectAccessToken`**、**`ConnectUserId`**。
- 列服务器：`GET https://connect.emby.media/service/servers?userId={ConnectUserId}`，头 `X-Application` + `X-Connect-UserToken`；返回数组，元素含 `AccessKey`、`SystemId`、`Name`、`Url`、`LocalAddress`。
- ⚠️ Wiki 说"the ConnectUserToken value"，而标题写的是 `ConnectAccessToken` —— Wiki 自身命名不一致；以源码头名 `X-Connect-UserToken` 为准。

### 5.2 用 Connect 换取本地令牌：`GET /Connect/Exchange`

- 【L1-API】`#/paths/~1Connect~1Exchange/get`：摘要 "Gets the corresponding local user from a connect user id"，参数 **`ConnectUserId`（query，required）**，返回 **`Connect.ConnectAuthenticationExchangeResult`** = `{ LocalUserId: string, AccessToken: string }`。
  官方页：[getConnectExchange](https://dev.emby.media/reference/RestAPI/ConnectService/getConnectExchange.html)
- 【L1-WIKI】原文：
  > To perform the exchange, send a GET to the Emby Server: `GET /Connect/Exchange?format=json&ConnectUserId={ConnectUserId}`
  > The headers should contain: `X-Emby-Token` - the AccessKey from EmbyConnect; `X-Emby-Authorization` - See Authorization Request Header
  > You'll receive a json response containing credentials local to the particular Emby Server: **LocalUserId**, **AccessToken**
- 【L1-CLIENT】.NET ApiClient 实现（`ConnectionManager.cs` L513-L521）：
  `url += "/emby/Connect/Exchange?format=json&ConnectUserId=" + credentials.ConnectUserId;`，头 `X-MediaBrowser-Token: {ExchangeToken}`（来自 `HttpHeaders.SetAccessToken`）+ `X-Emby-Authorization: MediaBrowser Client="…", Device="…", DeviceId="…", Version="…"`
  → [ConnectionManager.cs#L513-L521](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ConnectionManager.cs#L513-L521)
  响应反序列化为 `ConnectAuthenticationExchangeResult`，取 `auth.LocalUserId` / `auth.AccessToken`。
- 其它 Connect 端点（【L1-API】）：`GET /Connect/Pending`；`POST /Users/{Id}/Connect/Link?ConnectUsername=…`（**"Requires authentication as administrator"**，返回 `Connect.UserLinkResult` = `{IsPending, IsNewUserInvitation, GuestDisplayName}`）；`DELETE /Users/{Id}/Connect/Link`；`POST /Users/{Id}/Connect/Link/Delete`。
- `UserDto` 上与 Connect 相关的字段：`ConnectUserName`（"The name of the connect user."）、`ConnectLinkType`（`Connect.UserLinkType`，enum = `LinkedUser` / `Guest`）。
- **Connect 对播放的影响**：Connect 只改变"拿到本地 AccessToken 的方式"；**拿到之后的播放 API 完全一致**（同一个 `AccessToken`，同样的 `PlaybackInfo` / `/Videos/*` / `/Sessions/Playing*`）。→ 该结论由 §5.2 的"换取本地令牌"设计直接推出，并见 Wiki 原句 "These credentials can then be used to make requests to other endpoints on that server."

### 5.3 影响播放的每用户策略字段 `UserPolicy`（【L1-API】，当前 4.10）

官方类型：`MediaBrowser.Model.Users.UserPolicy`
OpenAPI：`#/components/schemas/UserPolicy`
官方页：`https://dev.emby.media/reference/pluginapi/MediaBrowser.Model.Users.UserPolicy.html`
由 `POST /Users/{Id}/Policy` 修改（`#/paths/~1Users~1{Id}~1Policy/post`）；通过 `GET /Users/{Id}` 或 `GET /Users/Public` 的 `UserDto.Policy` 读取。

**与播放直接相关的字段（逐字）：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `EnableMediaPlayback` | boolean | 总开关，能否播放媒体 |
| `EnableAudioPlaybackTranscoding` | boolean | ✅ 任务书命中 |
| `EnableVideoPlaybackTranscoding` | boolean | ✅ 任务书命中 |
| `EnablePlaybackRemuxing` | boolean | ✅ 任务书命中 |
| `EnableTranscodingQuality` | boolean | 是否允许用户改转码质量 |
| `AutoRemoteQuality` | integer | 远程自动质量档 |
| `RemoteClientBitrateLimit` | integer | ✅ 任务书命中；按用户限制远程码率 |
| `EnableSyncTranscoding` | boolean | ✅ 任务书命中 |
| `EnableContentDownloading` | boolean | 下载（下载后的离线播放也受此影响） |
| `EnableContentDeletion` / `EnableContentDeletionFromFolders` | bool / string[] | |
| `EnableSubtitleDownloading` / `EnableSubtitleManagement` | boolean | 字幕 |
| `EnableMediaConversion` | boolean | |
| `SimultaneousStreamLimit` | integer | 并发流上限 |
| `EnableLiveTvAccess` / `EnableLiveTvManagement` | boolean | 直播电视播放 |
| `EnabledDevices` / `EnableAllDevices` | string[] / boolean | |
| `EnabledChannels` / `EnableAllChannels` | string[] / boolean | |
| `EnabledFolders` / `EnableAllFolders` / `ExcludedSubFolders` | string[] / bool / string[] | |
| `BlockedTags` / `IncludeTags` / `IsTagBlockingModeInclusive` / `AllowTagOrRating` | string[] / string[] / bool / bool | |
| `MaxParentalRating` / `BlockUnratedItems`(`UnratedItem[]`) / `AccessSchedules`(`AccessSchedule[]`) | | 家长控制 |
| `EnableRemoteAccess` / `EnableRemoteControlOfOtherUsers` / `EnableSharedDeviceControl` | boolean | |
| `AuthenticationProviderId` | string | |
| `RestrictedFeatures` | string[] | |

#### ❌ 名字纠正：任务书里的这些 `UserPolicy` 字段名

| 任务书猜测 | 实情 |
|---|---|
| `EnableDownloading` | **不存在**。正确的名字是 **`EnableContentDownloading`** |
| `BlockedChannels` | **不存在**（4.10）。对应能力由 **`EnabledChannels` + `EnableAllChannels`** 表达 |
| `AllowedTags` | **不存在**（4.10）。对应名字是 **`IncludeTags`**（配合 `BlockedTags` 与 `IsTagBlockingModeInclusive`） |

### 5.4 3.5.x 与当前 4.x 的差异（明确区分）

**3.5.x 侧的独立证据**：官方 NuGet 包 `MediaBrowser.Common 3.5.0` 内含 `MediaBrowser.Model.dll`（`AssemblyVersion 3.5.0.0`）。
- 包地址：`https://www.nuget.org/api/v2/package/MediaBrowser.Common/3.5.0`
- 成员名可从程序集元数据字符堆提取（`get_*` 访问器名）。本次实测存在：`get_TranscodingUrl`、`get_TranscodingSubProtocol`、`get_TranscodingContainer`、`get_TranscodeReasons`、`get_TranscodingMaxAudioChannels`、`get_TranscodingProfiles`、`get_SupportsTranscoding`、`get_DeliveryMethod`、`get_DeliveryUrl`、`get_IsInterlaced`、`get_VideoRange`、`get_ReadAtNativeFramerate`、`get_CopyTimestamps`、`get_PlaybackStartTimeTicks`、`get_PlaylistItemId`、`get_ErrorCode`、`get_EnableAudioPlaybackTranscoding`、`get_EnableVideoPlaybackTranscoding`、`get_EnablePlaybackRemuxing`、`get_RemoteClientBitrateLimit`、`get_EnableSyncTranscoding`、`get_EnableContentDownloading`、`get_BlockedChannels`、`get_BlockedTags`、`get_EnabledChannels`、`get_EnableAllChannels`、`get_EnableMediaPlayback`、`get_Profile`、`get_Level`。
- 未在 3.5.0 程序集中出现（→ 该版本尚无）：`EnableDownloading`、`AllowedTags`、`MediaAttachments`、`PlaybackMediaSource`。
  ⚠️ **`get_BlockedChannels` 在 3.5.0 存在、在 4.10 `UserPolicy` 中不存在** —— 这是本报告发现的一处**真实跨版本重命名/移除**。
  ⚠️ 另注意：`get_DirectStreamUrl`、`get_MediaStream`、`get_PlaylistLength`、`get_EventName`、`get_SubtitleMethod`、`get_AllowInterlacedVideoStreamCopy` 在 3.5.0 程序集字符堆中**未能直接命中**（.NET 字符串堆会做后缀共享，未命中≠不存在，**不能据否定**）。→ 这几项在 3.5.x 的存在性 **未能确认**。

| 项目 | 3.5.x（源码/官方二进制可核） | 4.10（官方 OpenAPI/SDK 可核） |
|---|---|---|
| `MediaSourceInfo` 源码位置 | 无源码，`MediaBrowser.Model.dll` 二进制 | 无源码，官方 pluginapi 文档 + OpenAPI |
| `SupportsPersistentIdentifier`（Capabilities） | 存在于 [SessionsService.cs#L210](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L210) | **不在** 4.10 `ClientCapabilities` 中 |
| `ActiveWithinSeconds`（GET /Sessions） | 存在于 [SessionsService.cs#L31](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L31) | **不在** 4.10 规范中 |
| `QueueableMediaTypes` | Wiki 有记载 [Playback-Check-ins](https://github.com/MediaBrowser/Emby/wiki/Playback-Check-ins) | **不在** 4.10 规范中 |
| `BlockedChannels`（UserPolicy） | 存在于 3.5.0 `MediaBrowser.Model.dll` | **不在** 4.10 `UserPolicy` 中 |
| `X-MediaBrowser-Token` | 服务端接受（[AuthorizationContext.cs#L73](https://github.com/MediaBrowser/Emby/blob/master/Emby.Server.Implementations/HttpServer/Security/AuthorizationContext.cs#L73)） | 官方只声明 `X-Emby-Token`；是否仍接受**未能确认** |
| `/Sessions/Playing/Ping` | 3.5.3.0 开源 API 目录中**没有** Playstate 服务（该服务在闭源件里）→ 3.5.x 是否存在**未能确认** | 存在 ✅ |

**关于"3.5.3.0 开源快照能证明什么"的边界**：`MediaBrowser/Emby` master（3.5.3.0）里能读到源码的只有 `MediaBrowser.Api`（且 **`Playback/` 子目录已被移除**）、`Emby.Server.Implementations`（HTTP 栈、会话 WebSocket、安全上下文）、`Emby.Dlna`、`MediaBrowser.Providers`、Dashboard 前端。
`/Sessions/Playing`、`/Items/{Id}/PlaybackInfo`、`/Videos/{Id}/stream` 等**处理器的实现都不在开源部分**（在 `Emby.Server.MediaEncoding.dll` / `MediaBrowser.Server.Core` 里）。→ 这些端点的**服务端实现细节无法引用 Emby 源码**，只能用 OpenAPI/官方文档/官方客户端库。

---

## 6. 端点总表（含来源）

| # | 方法 | 路径 | 关键参数 | 来源（含锚点） |
|---|---|---|---|---|
| 1 | POST | `/Users/AuthenticateByName` | 头 `X-Emby-Authorization`(必需)；body `Username`,`Pw` | [官方页](https://dev.emby.media/reference/RestAPI/UserService/postUsersAuthenticatebyname.html) · OpenAPI `#/paths/~1Users~1AuthenticateByName/post` · [JS 客户端 L1042-L1048](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L1042-L1048) |
| 2 | GET | `/Users/Public` | — → `UserDto[]` | [官方页](https://dev.emby.media/reference/RestAPI/UserService/getUsersPublic.html) · [ApiClient.cs#L471](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ApiClient.cs#L471) |
| 3 | POST | `/Items/{Id}/PlaybackInfo` | body 19 字段（见 §2.2） | [官方页](https://dev.emby.media/reference/RestAPI/MediaInfoService/postItemsByIdPlaybackinfo.html) · OpenAPI `#/components/schemas/PlaybackInfoRequest` |
| 4 | GET | `/Items/{Id}/PlaybackInfo` | `Id`(path,req)、`UserId`(query,req) | [官方页](https://dev.emby.media/reference/RestAPI/MediaInfoService/getItemsByIdPlaybackinfo.html) · [ApiClient.cs#L2605-L2617](https://github.com/MediaBrowser/Emby.ApiClient/blob/master/Emby.ApiClient/ApiClient.cs#L2605-L2617) |
| 5 | GET | `/Videos/{Id}/stream`、`/Videos/{Id}/stream.{Container}` | 24 个 query（见 §3.1）+ Wiki 要求 `MediaSourceId`/`PlaySessionId` | OpenAPI · [Video-Streaming](https://github.com/MediaBrowser/Emby/wiki/Video-Streaming) |
| 6 | GET/HEAD | `/Videos/{Id}/master.m3u8`（及 `main.m3u8`、`live.m3u8`） | 同上 24 个 + Wiki 要求 `MediaSourceId`/`DeviceId` | OpenAPI · [Http-Live-Streaming](https://github.com/MediaBrowser/Emby/wiki/Http-Live-Streaming) |
| 7 | GET/HEAD | `/Videos/{Id}/hls1/{PlaylistId}/{SegmentId}.{SegmentContainer}` | path 参数 4 个 | OpenAPI `#/paths/~1Videos~1{Id}~1hls1~1{PlaylistId}~1{SegmentId}.{SegmentContainer}/get` |
| 8 | DELETE | `/Videos/ActiveEncodings` | `DeviceId`(req)、`PlaySessionId`(req) | [官方页](https://dev.emby.media/reference/RestAPI/HlsSegmentService/deleteVideosActiveencodings.html) · [apiclient.js#L2665-L2681](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L2665-L2681) |
| 9 | POST | `/Videos/ActiveEncodings/Delete` | 同上 | OpenAPI |
| 10 | GET | `/Playback/BitrateTest` | `Size` | OpenAPI `#/paths/~1Playback~1BitrateTest/get` |
| 11 | POST | `/Sessions/Playing` | body `PlaybackStartInfo` | [官方页](https://dev.emby.media/reference/RestAPI/PlaystateService/postSessionsPlaying.html) · [Playback-Check-ins](https://github.com/MediaBrowser/Emby/wiki/Playback-Check-ins) |
| 12 | POST | `/Sessions/Playing/Progress` | body `PlaybackProgressInfo`（含 `EventName`） | [官方页](https://dev.emby.media/reference/RestAPI/PlaystateService/postSessionsPlayingProgress.html) |
| 13 | POST | `/Sessions/Playing/Stopped` | body `PlaybackStopInfo` | [官方页](https://dev.emby.media/reference/RestAPI/PlaystateService/postSessionsPlayingStopped.html) |
| 14 | POST | `/Sessions/Playing/Ping` | `PlaySessionId`(query) | [官方页](https://dev.emby.media/reference/RestAPI/PlaystateService/postSessionsPlayingPing.html) |
| 15 | GET | `/Sessions` | `ControllableByUserId`,`DeviceId`,`Id` → `SessionInfo[]` | [官方页](https://dev.emby.media/reference/RestAPI/SessionsService/getSessions.html) · [SessionsService.cs#L21](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L21) |
| 16 | POST | `/Sessions/Capabilities/Full` | `Id`(query,req) + body `ClientCapabilities` | [官方页](https://dev.emby.media/reference/RestAPI/SessionsService/postSessionsCapabilitiesFull.html) |
| 17 | POST | `/Sessions/Capabilities` | `Id`,`PlayableMediaTypes`,`SupportedCommands`,`SupportsMediaControl`,`SupportsSync` | [SessionsService.cs#L187-L216](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L187-L216) |
| 18 | POST | `/Sessions/Logout` | — | [SessionsService.cs#L231](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L231) |
| 19 | POST | `/Sessions/{Id}/Playing` | `ItemIds`(req),`PlayCommand`(req),`StartPositionTicks` + body `PlayRequest` | [Remote-control](https://github.com/MediaBrowser/Emby/wiki/Remote-control) |
| 20 | POST | `/Sessions/{Id}/Playing/{Command}` | `Command` ∈ `PlaystateCommand` + body `PlaystateRequest` | [Remote-control](https://github.com/MediaBrowser/Emby/wiki/Remote-control) · [SessionsService.cs#L82](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L82) |
| 21 | POST | `/Sessions/{Id}/Command` | body `GeneralCommand`{Name,ControllingUserId,Arguments} | [SessionsService.cs#L132](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L132) |
| 22 | POST | `/Sessions/{Id}/Command/{Command}` | path `Command` | [SessionsService.cs#L113](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L113) |
| 23 | POST | `/Sessions/{Id}/Message` | `Header`(req),`Text`(req),`TimeoutMs` | [SessionsService.cs#L144](https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Api/Session/SessionsService.cs#L144) |
| 24 | GET | `/Connect/Exchange` | `ConnectUserId`(req) → `{LocalUserId, AccessToken}` | [官方页](https://dev.emby.media/reference/RestAPI/ConnectService/getConnectExchange.html) · [Emby-Connect](https://github.com/MediaBrowser/Emby/wiki/Emby-Connect) |
| 25 | POST | `/Users/{Id}/Connect/Link` | `ConnectUsername`(req) → `UserLinkResult`（**管理员**） | OpenAPI `#/paths/~1Users~1{Id}~1Connect~1Link/post` |
| 26 | DELETE | `/Users/{Id}/Connect/Link` | —（**管理员**） | OpenAPI |
| 27 | POST | `/Users/{Id}/Policy` | body `UserPolicy` | OpenAPI `#/paths/~1Users~1{Id}~1Policy/post` |
| 28 | POST | `/LiveStreams/Open` | body `LiveStreamRequest`（含 `DeviceProfile`,`OpenToken`,`MaxStreamingBitrate`,`AllowVideoStreamCopy`,`AllowAudioStreamCopy`,`EnableTranscoding`,`AllowInterlacedVideoStreamCopy`） | OpenAPI `#/components/schemas/LiveStreamRequest` · [playbackmanager.js `getLiveStream`](https://github.com/MediaBrowser/emby-webcomponents/blob/master/playback/playbackmanager.js) |
| 29 | POST | `/LiveStreams/Close` | `LiveStreamId`(req)、`PlaySessionId`(req) | OpenAPI `#/paths/~1LiveStreams~1Close/post` |
| 30 | POST | `/LiveStreams/MediaInfo` | body `{LiveStreamId}` | [apiclient.js#L1904-L1914](https://github.com/MediaBrowser/Emby.ApiClient.Javascript/blob/master/apiclient.js#L1904-L1914) |

---

## 7. 未能确认 / 任务书前提有误的清单

### 7.1 任务书里我**无法证实**的说法

1. **`https://github.com/MediaBrowser/Emby/blob/master/MediaBrowser.Model/Entities/MediaSourceInfo.cs` 不存在**。Emby 仓库里 `MediaBrowser.Model` 全部是二进制（NuGet `MediaBrowser.Common 3.5.0` / `MediaBrowser.Server.Core 3.5.0`）。而且**该类型的官方命名空间是 `MediaBrowser.Model.Dto`，不是 `MediaBrowser.Model.Entities`**。
2. **`MediaProtocol` 没有 `Hls` 取值**（只有 `File/Http/Rtmp/Rtsp/Udp/Rtp/Ftp/Mms`）。HLS 体现在 `TranscodingSubProtocol`。
3. **`SubtitleDeliveryMethod` 没有 `Drop` 取值**（4.10：`Encode/Embed/External/Hls/VideoSideData`）。
4. **`UserPolicy` 没有 `EnableDownloading`、`BlockedChannels`、`AllowedTags`**（当前对应名：`EnableContentDownloading`、`EnabledChannels`+`EnableAllChannels`、`IncludeTags`）。`BlockedChannels` 在 **3.5.0 的 Model.dll 中存在**，属已被移除/改名的字段。
5. **`ClientCapabilities` 没有 `SupportedLiveStreams`**（4.10 与 3.5.3.0 皆无）。
6. **`MediaAttachments`、`PlaybackMediaSource` 字段全规范不存在**。
7. **`Params`、`RequireAvc`、`VideoLevel` 在 4.10 规范中完全不存在**。
8. **`/Videos/*` 与 `/Videos/ActiveEncodings` 的 OpenAPI 参数表不完整**：`MediaSourceId`、`PlaySessionId`（Wiki 称必需）不在其中；`DELETE /Videos/ActiveEncodings` 的 `DeviceId` 在 Wiki 里是唯一参数、在 4.10 规范里是必需参数之一，但 3.5.3.0 的源码里查不到该处理器（闭源）。
9. **`SubtitleMethod` 不是 PlaybackInfo 的参数**（是流式端点的 query 参数）。
10. **`EnableMediaProbe`、`DirectPlayProtocols`** 由 Emby Web 客户端发送，但**不在 4.10 规范中** → 属"实际可用但未文档化"。
11. **`SubtitleStreamIndexes`（复数）** 出现在真实 `TranscodingUrl` 中，但**不在规范中**。
12. **`QueueableMediaTypes`** 只存在于 Wiki，规范与源码中都没有 → Wiki 该段已过时。
13. **`EventName` 的序列化大小写**：规范枚举是 PascalCase（`TimeUpdate`），官方 Web 客户端实际发小写（`timeupdate`）。服务端的实际反序列化行为**未能逐行确认**（处理器闭源）。
14. **Emby 4.x 是否仍接受 `X-MediaBrowser-Token`** —— 未能确认。3.5.3.0 源码接受；4.x 闭源，官方只文档化 `X-Emby-Token`。本次尝试下载 `Emby.Releases` 4.10.0.40 二进制（freebsd tar.xz，34.7 MB）**超时失败**，无法对 4.x 程序集做字符串核对。
15. **`GET /Items/{Id}/PlaybackInfo` 是否在 4.x 仍被服务端实现** —— 规范里存在该 operation（`Id` + `UserId` 必需），但未在官方文档任何"已废弃"列表中确认。✅ 该 operation 在 4.10 规范中**存在**；是否已废弃**未能确认**。
16. **`X-Emby-Authorization` 里的 `UserId` 参数是否被 4.x 服务端使用** —— 未能在源码层确认（3.5.3.0 明确**不使用**）。
17. **`MinSegments`/`SegmentLength`/`BreakOnNonKeyFrames` 等是否也能直接作为 `/Videos/{Id}/master.m3u8` 的 query 参数** —— 它们在 4.10 规范里只作为 `TranscodingProfile` 的字段出现；但真实 `TranscodingUrl` 里它们**确实以 query 形式出现**。→ 结论：**服务端生成 URL 时会带，但规范未把它们声明为这些端点的可接受参数**。
18. **3.5.3.0（而非 3.5.2.0）的完整开源快照**：`MediaBrowser/Emby` master 的 `SharedVersion.cs` 是 `3.5.3.0`，但**仓库里没有 `3.5.3.0` 这个 git tag**（实测 `git ls-remote --tags` 共 765 条，最新为 `3.5.2.0`）。所以"3.5.3.0 源码"只能通过 **master 分支**引用，无法用 tag 固定。

### 7.2 本次调研中确认可用的抓取路径（供复用）

- `https://codeload.github.com/<owner>/<repo>/tar.gz/refs/heads/<branch>` —— **不消耗 API 额度**，实测可用（Emby 主仓 ~173 MB、Emby.SDK ~15 MB、Emby.ApiClients ~4.7 MB、Emby.ApiClient.Javascript ~0.8 MB）。
- `https://codeload.github.com/MediaBrowser/Emby/tar.gz/refs/tags/3.5.2.0` —— tag 亦可。
- `https://github.com/MediaBrowser/Emby.wiki.git` —— **git clone 可拿到全部官方 Wiki Markdown**，不消耗 API 额度。
- `https://www.nuget.org/api/v2/package/MediaBrowser.Common/3.5.0` —— 拿到 3.5.0 的 `MediaBrowser.Model.dll`（官方二进制，可做成员名核对）。
- `https://dev.emby.media/` 与 `https://swagger.emby.media/openapi.json` —— 均可直连。
- `raw.githubusercontent.com` —— **不通**（HTTP 000）。

---

## 8. 一句话结论（给决策用）

- **播放主链路**：`POST /Users/AuthenticateByName`（拿 `AccessToken`）→ `POST /Items/{Id}/PlaybackInfo`（带 `DeviceProfile` + `IsPlayback=true` + `AutoOpenLiveStream=true`）→ 按 `MediaSources[0]` 的 `SupportsDirectPlay` / `SupportsDirectStream` / `SupportsTranscoding` 三态二选一：直接用 `Path` / 用 `DirectStreamUrl`（或自拼 `/Videos/{Id}/stream.{Container}?Static=true&MediaSourceId=…&PlaySessionId=…`）/ 用 `TranscodingUrl` → 播放期间 `POST /Sessions/Playing`、`/Sessions/Playing/Progress`（≤10 s 一次）、结束时 `/Sessions/Playing/Stopped` + `DELETE /Videos/ActiveEncodings?DeviceId=…&PlaySessionId=…`。
- **认证**：优先 `X-Emby-Authorization: MediaBrowser Client="…", Device="…", DeviceId="…", Version="…", Token="…"`（官方 schema 允许 scheme 用 `Emby`），或等价地 `X-Emby-Token`；`api_key` 查询参数用于流式/图片/WebSocket 等无法带头部的场景。
- **最容易被第三方实现搞错的三点**：①`AudioBitRate`/`VideoBitRate` 的大写 R；②`PlaybackInfo` 的参数**全在 body**（只有 `Id` 在 path），而 `GET` 变体只认 `UserId`；③流式端点必须带 `MediaSourceId` + `PlaySessionId`（OpenAPI 没写，Wiki 写了）。
