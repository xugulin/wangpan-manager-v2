# Animeko 弹幕功能技术研究报告（可直接照着实现）

> **研究对象**：[`open-ani/animeko`](https://github.com/open-ani/animeko)（20.2k stars，Kotlin / Compose Multiplatform，AGPL-3.0）
> **快照版本**：默认分支 `main`，commit [`99fefc02fa62b15ecdfca03f6f70dfedea2c18f0`](https://github.com/open-ani/animeko/commit/99fefc02fa62b15ecdfca03f6f70dfedea2c18f0)（committer date 2026-09-22），`gradle.properties` 中 `version.name=4.9.0-dev`、`package.version=5.0.0`
> **取数方式**：`curl -sL https://codeload.github.com/open-ani/animeko/tar.gz/refs/heads/main` 下载全量源码后本地 `grep/read`；DanDanPlay 官方规范取自其在线 Swagger（`https://api.dandanplay.net/swagger/v2/swagger.json`）与官方文档站（`https://doc.dandanplay.com/open/`）
> **本文所有行号均对应该 commit 的文件内容**。链接统一写成 `blob/main/...`；若日后行号漂移，请以文件内容为准。
>
> **阅读提示**：本文把"**Animeko 实际实现的**"和"**上游协议规范规定但 Animeko 没做的**"严格区分开。凡是没在源码里找到证据的，第 10 节统一列出，正文中标注"未找到/不确定"。

---

## 目录

1. [总体架构与数据流](#1-总体架构与数据流)
2. [Q1 弹幕数据源](#2-q1-弹幕数据源)
3. [Q2 匹配算法（最关键）](#3-q2-匹配算法最关键)
4. [Q3 弹幕解析与数据模型](#4-q3-弹幕解析与数据模型)
5. [Q4 渲染引擎](#5-q4-渲染引擎)
6. [Q5 交互功能](#6-q5-交互功能)
7. [Q6 "弹幕云过滤"到底是什么](#7-q6-弹幕云过滤到底是什么)
8. [Q7 离线 / 缓存与多源合并](#8-q7-离线--缓存与多源合并)
9. [Q8 可移植性结论：搬到 Python + PySide6 + 自绘](#9-q8-可移植性结论搬到-python--pyside6--自绘)
10. [没查到 / 不确定的点（如实清单）](#10-没查到--不确定的点如实清单)
11. [附录 A：接口清单速查](#附录-a接口清单速查)
12. [附录 B：源码文件索引](#附录-b源码文件索引)
13. [附录 C：参考链接](#附录-c参考链接)

---

## 1. 总体架构与数据流

### 1.1 模块分层

| 模块 | 路径 | 职责 | 代码量 |
|---|---|---|---|
| 弹幕领域 API | `danmaku/api/` | 数据模型（`DanmakuInfo`）、源抽象（`DanmakuProvider`）、匹配器（`DanmakuMatcher`）、时间轴会话（`DanmakuCollection`/`DanmakuSession`）、清洗 | ~1.1k 行 |
| DanDanPlay 接入 | `danmaku/dandanplay/` | HTTP 客户端 + 签名 + 匹配流程 + p 串解析 | ~0.8k 行 |
| 渲染引擎 | `danmaku/ui/` | `DanmakuHost`（Compose Canvas）、轨道、绘制缓存、帧时间平滑 | ~2.2k 行 |
| 渲染配置 | `danmaku/ui-config/` | `DanmakuConfig` / `DanmakuStyle` / 参数范围 | ~180 行 |
| App 数据层 | `app/shared/app-data/` | 多源聚合、缓存（Room）、发送、过滤规则仓库、Animeko 官方弹幕源 | — |
| App UI 层 | `app/shared/`、`app/shared/ui-episode/`、`app/shared/ui-settings/` | 播放器弹幕层、发送框、弹幕列表、设置面板、手动匹配对话框 | — |

引用：
[`danmaku/api`](https://github.com/open-ani/animeko/tree/main/danmaku/api) ·
[`danmaku/dandanplay`](https://github.com/open-ani/animeko/tree/main/danmaku/dandanplay) ·
[`danmaku/ui`](https://github.com/open-ani/animeko/tree/main/danmaku/ui) ·
[`danmaku/ui-config`](https://github.com/open-ani/animeko/tree/main/danmaku/ui-config)

### 1.2 端到端数据流（照这个顺序实现即可）

```
[播放器时钟] player.currentPositionMillis ──┐
                                            │  Flow<Duration> progress
[弹幕列表来源]                               ▼
  ├─ LocalDanmakuProvider (Room 缓存)     ┌───────────────────────────────┐
  ├─ AniDanmakuProvider   (api.animeko.org)│ TimeBasedDanmakuSession       │
  └─ DandanplayDanmakuProvider             │  .at(progress, speed, filters)│
        └─ 拆成 Bilibili/Acfun/Tucao/Baha/ │   ↓ 每 50ms tick 一次          │
           Dandanplay 5 个 serviceId       │  DanmakuEvent.Add / Repopulate│
        ↓                                   └───────────────┬───────────────┘
   DanmakuFetchResult[]                                     │
        ↓                                                   ▼
  DanmakuLoaderImpl.computeFinalDanmakuResult          UIDanmakuEvent
   (多源合并 / 手动匹配覆盖)                                 │
        ↓                                                   ▼
  EpisodeDanmakuLoader.createDanmakuCollection      DanmakuHostState.trySend / repopulate
   (按源 enabled 过滤 + shiftMillis 平移 + 文本清洗)         │
        ↓                                                   ▼
  GetDanmakuRegexFilterListFlowUseCase ──► Regex 过滤    DanmakuHost (Canvas 画 ImageBitmap)
```

代码位置：
- 会话与算法：[`danmaku/api/src/commonMain/kotlin/DanmakuCollection.kt`](https://github.com/open-ani/animeko/blob/main/danmaku/api/src/commonMain/kotlin/DanmakuCollection.kt)
- 多源加载：[`app/shared/app-data/src/commonMain/kotlin/domain/danmaku/DanmakuLoader.kt`](https://github.com/open-ani/animeko/blob/main/app/shared/app-data/src/commonMain/kotlin/domain/danmaku/DanmakuLoader.kt)
- 播放器桥接：[`app/shared/app-data/src/commonMain/kotlin/domain/episode/EpisodeDanmakuLoader.kt`](https://github.com/open-ani/animeko/blob/main/app/shared/app-data/src/commonMain/kotlin/domain/episode/EpisodeDanmakuLoader.kt)
- UI 桥接：[`app/shared/src/commonMain/kotlin/ui/danmaku/PlayerDanmakuHost.kt`](https://github.com/open-ani/animeko/blob/main/app/shared/src/commonMain/kotlin/ui/danmaku/PlayerDanmakuHost.kt)、[`app/shared/src/commonMain/kotlin/ui/subject/episode/EpisodeViewModel.kt`](https://github.com/open-ani/animeko/blob/main/app/shared/src/commonMain/kotlin/ui/subject/episode/EpisodeViewModel.kt) `uiDanmakuEventFlow`（L666–698）

---

## 2. Q1 弹幕数据源

### 2.1 源清单（结论）

Animeko 只有 **2 个 `DanmakuProvider`**（远程）+ 1 个本地源：

```kotlin
// app/shared/app-data/src/commonMain/kotlin/domain/danmaku/DanmakuRepository.kt L76-L85
private val remoteProviders by lazy {
    listOf(
        AniDanmakuProvider(danmakuApi),                 // providerId = "animeko"
        DandanplayDanmakuProvider(                      // providerId = "dandanplay"
            dandanplayAppId = currentAniBuildConfig.dandanplayAppId,
            dandanplayAppSecret = currentAniBuildConfig.dandanplayAppSecret,
            httpClientProvider.get(),
        ),
    )
}
```

但 **UI 上能看到的"弹幕源"（`DanmakuServiceId`）有 6 个**：`Animeko`、`Acfun`、`Baha`、`Bilibili`、`Dandanplay`、`Tucao`。后 4 个（AcFun/Baha/Bilibili/Tucao）**不是 Animeko 直接请求的**，而是 DanDanPlay 通过 `withRelated=true` 聚合回来的第三方弹幕，Animeko 用 `senderId` 前缀把它们拆出来当独立源显示：

```kotlin
// danmaku/dandanplay/src/commonMain/kotlin/DandanplayDanmakuProvider.kt L51-L57
private enum class DanmakuOrigin(val serviceId: DanmakuServiceId) {
    BiliBili(DanmakuServiceId.Bilibili), AcFun(DanmakuServiceId.AcFun),
    Tucao(DanmakuServiceId.Tucao),       Baha(DanmakuServiceId.Baha),
    DanDanPlay(DanmakuServiceId.Dandanplay),
}

// 同文件 L94-L105：靠 senderId 前缀判定来源
private val DanmakuInfo.origin get() = when {
    senderId.startsWith("[BiliBili]") -> DanmakuOrigin.BiliBili  // 注释：这个是确定的, 其他的不确定
    senderId.startsWith("[Acfun]")    -> DanmakuOrigin.AcFun
    senderId.startsWith("[Tucao]")    -> DanmakuOrigin.Tucao
    senderId.startsWith("[Gamer]", ignoreCase = true)
        || senderId.startsWith("[Gamers]", ignoreCase = true)
        || senderId.startsWith("[Baha]", ignoreCase = true) -> DanmakuOrigin.Baha
    else -> DanmakuOrigin.DanDanPlay
}
```

> ⚠️ 注意：`DandanplayClient` 里**没有任何 bilibili.com / gamer.com.tw 的直连请求**。搜索全仓库 `api.dandanplay.net` 之外没有第二家弹幕站域名。README 也印证："Animeko 还会从弹弹play获取关联弹幕，弹弹play还会从其他弹幕平台例如哔哩哔哩港澳台和巴哈姆特获取弹幕。"（[README.md L150-L157](https://github.com/open-ani/animeko/blob/main/README.md)）

| UI 显示名 | `DanmakuServiceId.value` | 提供方 providerId | 是否需登录 | 显示名来源 |
|---|---|---|---|---|
| Animeko | `Animeko` | `animeko` | 读不需要 / 发需要 | `renderDanmakuServiceId` |
| 哔哩哔哩(港澳台) | `Bilibili` | `dandanplay` | 否 | 同上 |
| AcFun | `Acfun` | `dandanplay` | 否 | 同上 |
| Tucao | `Tucao` | `dandanplay` | 否 | 同上 |
| 巴哈姆特 | `Baha` | `dandanplay` | 否 | 同上 |
| 弹弹play | `Dandanplay` | `dandanplay` | 否 | 同上 |
| 本地缓存（不显示为源） | `Animeko`（`mainServiceId`） | `local` | 否 | `LocalDanmakuProvider` |

- 显示名映射：[`app/shared/ui-episode/src/commonMain/kotlin/ui/episode/danmaku/DanmakuStrings.kt` L20-L30](https://github.com/open-ani/animeko/blob/main/app/shared/ui-episode/src/commonMain/kotlin/ui/episode/danmaku/DanmakuStrings.kt)
- `DanmakuServiceId` 定义：[`danmaku/api/src/commonMain/kotlin/DanmakuInfo.kt` L57-L70](https://github.com/open-ani/animeko/blob/main/danmaku/api/src/commonMain/kotlin/DanmakuInfo.kt)
- `DanmakuProviderId` 定义：[`danmaku/api/src/commonMain/kotlin/provider/DanmakuProvider.kt` L40-L49](https://github.com/open-ani/animeko/blob/main/danmaku/api/src/commonMain/kotlin/provider/DanmakuProvider.kt)（`animeko` / `dandanplay` / `local`）

---

### 2.2 源 A：DanDanPlay（弹弹play）

**Host**：`https://api.dandanplay.net`（官方规范：`https://api.dandanplay.net`，[doc.dandanplay.com/open](https://doc.dandanplay.com/open/)）

**鉴权：签名验证模式**，三个请求头：

```kotlin
// danmaku/dandanplay/src/commonMain/kotlin/DandanplayClient.kt L53-L67
private fun HttpRequestBuilder.addAuthorizationHeaders() {
    header("X-AppId", appId)
    val time = currentTimeMillis() / 1000
    header("X-Timestamp", time)
    header("X-Signature", generateSignature(appId, time, url.encodedPath, appSecret))
}

private fun generateSignature(appId: String, timestamp: Long, path: String, appSecret: String): String {
    val data = appId + timestamp + path + appSecret
    return Base64.encode(data.encodeToByteString().digest(DigestAlgorithm.SHA256))
}
```

- 算法：`X-Signature = Base64( SHA256( AppId + Timestamp + Path + AppSecret ) )`，`Timestamp` 为 **Unix 秒**，`Path` 为**不含域名与 query 的路径**（如 `/api/v2/comment/123450001`）。
- 与官方规范逐字一致：[doc.dandanplay.com/open/ §5 签名验证模式指南](https://doc.dandanplay.com/open/)（官方也给出 Java/JS/PHP/.NET/Python/Go 示例；Python 版一行即 `base64.b64encode(hashlib.sha256((app_id+str(ts)+path+app_secret).encode()).digest())`）。
- 官方另一种"凭证模式"用 `X-AppId` + `X-AppSecret`（**Animeko 未使用**）。

**AppId / AppSecret 从哪来**（Animeko 未把密钥写进仓库）：

```kotlin
// app/shared/app-platform/build.gradle.kts L24-L25
val dandanplayAppId = getPropertyOrNull("ani.dandanplay.app.id") ?: ""
val dandanplayAppSecret = getPropertyOrNull("ani.dandanplay.app.secret") ?: ""
```
即来自 Gradle 属性（CI secret）→ 生成 `AniBuildConfig`。**你要自己实现必须去 [DevCenter](https://dev.dandanplay.com) 申请 AppId/AppSecret**（当前有"应用分层与配额管理"机制）。

**超时**：请求/连接/socket 全部 60s（源码注释"弹弹服务器请求比较慢"）——[`DandanplayClient.kt` L202-L208](https://github.com/open-ani/animeko/blob/main/danmaku/dandanplay/src/commonMain/kotlin/DandanplayClient.kt)

**Animeko 实际用到的 7 个接口**（全部在 `DandanplayClient.kt`）：

| # | 方法与路径 | 参数 | 源码行 | 用途 |
|---|---|---|---|---|
| 1 | `GET /api/v2/bangumi/season/anime/{year}/{month}` | path: 年、月 | L69-L84 | 按季度拉全部番剧，缩小匹配范围 |
| 2 | `GET /api/v2/search/anime` | `keyword` | L86-L103 | 按条目名搜番剧 |
| 3 | `GET /api/v2/search/episodes` | `anime`、`episode`(传 null) | L105-L121 | 搜剧集 |
| 4 | `GET /api/v2/bangumi/{bangumiId}` | dandanplay 的 animeId/bangumiId | L123-L136 | 拉某番全部剧集 |
| 5 | `GET /api/v2/bangumi/bgmtv/{bgmtvSubjectId}` | **Bangumi.tv subject id** | L138-L151 | 用 Bangumi 条目 ID 直接映射弹弹条目（最准） |
| 6 | `POST /api/v2/match` | JSON body | L153-L178 | 用文件名匹配剧集 |
| 7 | `GET /api/v2/comment/{episodeId}?chConvert=0&withRelated=true` | path: episodeId | L180-L199 | **取弹幕正文** |

**请求 6 的实际 body（关键！）**：

```kotlin
// DandanplayClient.kt L165-L172
setBody(buildJsonObject {
    put("fileName", filename)
    fileSize?.let { put("fileSize", fileSize) }
    put("videoDuration", videoDuration.inWholeSeconds)
    put("matchMode", "fileNameOnly")     // ← 硬编码
})
```
注意形参 `fileHash: String?` **取了但从未写进 body**（L154 声明，L165-172 未使用）。

**响应 7 的结构**（官方 Swagger 定义 + Animeko 的序列化模型）：

```jsonc
// CommentResponseV2
{ "count": 1234,
  "comments": [ { "cid": 1234567890, "p": "12.34,1,16777215,abc123", "m": "弹幕内容" } ] }
```
- `p` 字段官方定义：`出现时间,模式,颜色,用户ID`
  - 时间：秒，精确到小数点后两位
  - **模式：1-普通弹幕，4-底部弹幕，5-顶部弹幕**
  - 颜色：`R×256×256 + G×256 + B`，R/G/B 范围 0-255
  - 用户 ID：字符串
  （官方 Swagger `/api/v2/comment/{episodeId}` GET description + `CommentData` schema）
- ⚠️ 官方 `SendCommentRequest` schema 里写的是"4-顶部弹幕，5-底部弹幕"，与 `p` 字段说明**互相矛盾**；Animeko 按 `p` 的说明实现（4→BOTTOM、5→TOP）。官方文档内部不一致，实现时以实测为准。

**请求 7 的有效 query**：`chConvert=0`（不转换简繁，源码注释指向 issue #122，原本按系统语言传 1/2 但被注释掉了）、`withRelated=true`（拿第三方弹幕）。官方规范中该接口还支持 `from`（起始弹幕编号）。

---

### 2.3 源 B：Animeko 自己的公益弹幕服务

**Base URL**：**两个域名**，代码里都出现过：

```kotlin
// app/shared/app-data/src/commonMain/kotlin/data/network/danmaku/AniDanmakuProvider.kt L70-L75
object AniBangumiSeverBaseUrls {
    const val GLOBAL = "https://danmaku-global.myani.org"
    const val CN = "https://danmaku-cn.myani.org"
    val list = listOf(CN, GLOBAL)
}
```
但**这两个常量只被用在"设置页的服务器连通性测试"里**（`SettingsViewModel.danmakuServerTesters`，逐个 `GET $base/status`），见 [`app/shared/ui-settings/.../SettingsViewModel.kt` L279-L288](https://github.com/open-ani/animeko/blob/main/app/shared/ui-settings/src/commonMain/kotlin/ui/settings/SettingsViewModel.kt)。

真正的弹幕 API 客户端走的是**生成代码里的默认基址**：

```kotlin
// client/src/commonMain/gen/me/him188/ani/client/infrastructure/ApiClient.kt L60
const val BASE_URL: String = "https://api.animeko.org"
```
```kotlin
// client/build.gradle.kts L50-L51（生成 spec 时拉取 openapi.json 的地址）
val apiServer = getPropertyOrNull("ani.api.server")?.takeIf { it.isNotBlank() } ?: "https://api.animeko.org"
```
```kotlin
// app/shared/app-data/src/commonMain/kotlin/data/network/AniApiProvider.kt L49, L66
val danmakuApi = ApiInvoker(client) { DanmakuAniApi(baseurl, it) }
private inline val baseurl get() = ServerListFeatureConfig.Companion.MAGIC_ANI_SERVER  // "https://MAGIC_ANI_SERVER/"
```
运行期由 Ktor 插件 `ServerListFeatureHandler` 把 magic host 改写成 `ServerSelector` 选出的真实服务器（[`ScopedHttpClientFeature.kt` L209-L260](https://github.com/open-ani/animeko/blob/main/app/shared/app-data/src/commonMain/kotlin/domain/foundation/ScopedHttpClientFeature.kt)）。
> 也就是说：**`danmaku-cn.myani.org` / `danmaku-global.myani.org` 是弹幕服务器的对外域名（README 的 swagger 链接也指向 `danmaku-cn`），但 API 业务面在 `api.animeko.org`。** 两者的关系我没有在代码里找到显式绑定逻辑 —— 见第 10 节。

**契约（来自提交进仓库的 OpenAPI 生成代码）**：

```kotlin
// client/src/commonMain/gen/me/him188/ani/client/apis/DanmakuAniApi.kt L57-L115
// 获取弹幕：GET /v1/danmaku/{episodeId}
//   maxCount 最大数量，默认 8000
//   fromTime 过滤开始时间，单位毫秒，默认 0
//   toTime   过滤结束时间，单位毫秒，默认 -1（负数=不限制）
//   requiresAuthentication = false      ← 读弹幕无需登录
open suspend fun getDanmaku(episodeId: String, maxCount: Int?=null, fromTime: Long?=null, toTime: Long?=null): HttpResponse<AniDanmakuGetResponse>

// 发送弹幕：POST /v1/danmaku/{episodeId}，authNames = ["auth-jwt"]，requiresAuthentication = true
open suspend fun postDanmaku(episodeId: String, aniDanmakuPostRequest: AniDanmakuPostRequest?=null): HttpResponse<Unit>
```

模型（[`client/src/commonMain/gen/.../models/`](https://github.com/open-ani/animeko/tree/main/client/src/commonMain/gen/me/him188/ani/client/models)）：

```kotlin
data class AniDanmakuGetResponse(val danmakuList: List<AniDanmaku>)
data class AniDanmaku(val id: String, val senderId: String, val danmakuInfo: AniDanmakuInfo)
data class AniDanmakuInfo(val playTime: Long, val color: Int, val text: String, val location: AniDanmakuLocation)
enum class AniDanmakuLocation { TOP, BOTTOM, NORMAL }        // 序列化值就是大写字符串
data class AniDanmakuPostRequest(val danmakuInfo: AniDanmakuInfo)
```

**鉴权怎么带上去的**：`auth-jwt` = `HttpBearerAuth("bearer")`（`ApiClient.kt` L49-L52）；实际 token 是登录会话里的 `aniAccessToken`，由 `UseAniTokenFeatureHandler` 注入：

```kotlin
// app/shared/application/src/commonMain/kotlin/platform/CommonKoinModule.kt L177-L182
UseAniTokenFeatureHandler(
    sessionManager.sessionFlow.map { (it as? AccessTokenSession)?.tokens?.aniAccessToken },
    onRefresh = { null },
)
```
`TokenSave.AccessTokens { bangumiAccessToken, aniAccessToken, expiresAtMillis }` —— [`TokenRepository.kt` L112-L126](https://github.com/open-ani/animeko/blob/main/app/shared/app-data/src/commonMain/kotlin/data/repository/user/TokenRepository.kt)。登录是 Bangumi OAuth 经 Animeko 服务器（`/v1/login/bangumi/oauth...`）。

**实测连通性**（本机 curl，2026-09-22）：
- `GET https://danmaku-cn.myani.org/swagger/index.html` → **401**（需鉴权）
- `GET https://danmaku-global.myani.org/swagger/v1/swagger.json` → 404
- `GET https://api.animeko.org/openapi.json` → **401**
→ 官方 OpenAPI 文档站点对匿名用户不可读，上面的契约只能来自仓库内生成代码（这也是最可靠的来源）。

---

### 2.4 源 C：本地缓存源

`LocalDanmakuProvider`，`providerId = "local"`，`mainServiceId = Animeko`，直接从 Room 读（[`DanmakuDao.kt` L82-L111](https://github.com/open-ani/animeko/blob/main/app/shared/app-data/src/commonMain/kotlin/data/persistent/database/dao/DanmakuDao.kt)），详细见第 8 节。

### 2.5 源抽象接口（要照抄的部分）

```kotlin
// danmaku/api/src/commonMain/kotlin/provider/DanmakuProvider.kt L24-L38
sealed interface DanmakuProvider {
    val providerId: DanmakuProviderId
    val mainServiceId: DanmakuServiceId
    suspend fun fetchAutomatic(request: DanmakuFetchRequest): List<DanmakuFetchResult>
}

// danmaku/api/src/commonMain/kotlin/provider/SimpleDanmakuProvider.kt L24-L79
class DanmakuFetchResult(val providerId: DanmakuProviderId, val matchInfo: DanmakuMatchInfo, val list: List<DanmakuInfo>)
class DanmakuMatchInfo(val serviceId: DanmakuServiceId, val count: Int, val method: DanmakuMatchMethod)

sealed class DanmakuMatchMethod {
    data class Exact(subjectTitle, episodeTitle)                 // 精确
    data class ExactSubjectFuzzyEpisode(subjectTitle, episodeTitle) // 条目准、集名模糊
    data class Fuzzy(subjectTitle, episodeTitle)
    data class ExactId(subjectId: Int, episodeId: Int)           // 用 ID 直接命中（Animeko 官方源 / 本地缓存）
    data object NoMatch
}

// danmaku/api/src/commonMain/kotlin/provider/MatchingDanmakuProvider.kt L15-L34（支持手动选番/选集的源）
interface MatchingDanmakuProvider : DanmakuProvider {
    suspend fun fetchSubjectList(name: String): List<DanmakuSubject>
    suspend fun fetchEpisodeList(subject: DanmakuSubject): List<DanmakuEpisode>
    suspend fun fetchDanmakuList(subject: DanmakuSubject, episode: DanmakuEpisode): List<DanmakuFetchResult>
}
```
DanDanPlay 实现的是 `MatchingDanmakuProvider`（支持手动匹配），Animeko 官方源实现的是 `SimpleDanmakuProvider`（只按 Bangumi episodeId 精确取，不支持手动匹配）。

**单源超时与容错**（务必照抄，否则一个源挂掉会拖死整屏）：
```kotlin
// DanmakuRepository.kt L206-L264  DanmakuFetcher.fetch
withTimeout(60.seconds) { provider.fetchAutomatic(request) }
    .retry(1) { ... }                       // 失败重试 1 次
    .catch { emit(listOf(DanmakuFetchResult(providerId, NoMatch, emptyList()))) }  // 兜底，绝不上抛
```

---

## 3. Q2 匹配算法（最关键）

### 3.1 匹配输入：`DanmakuFetchRequest`

```kotlin
// danmaku/api/src/commonMain/kotlin/provider/DanmakuProvider.kt L52-L69
class DanmakuFetchRequest(
    val subjectId: Int,               // Bangumi subject id
    val subjectPrimaryName: String,   // 条目主名（SubjectInfo.displayName）
    val subjectNames: List<String>,   // 全部别名（SubjectInfo.allNames）
    val subjectPublishDate: PackedDate, // 首播日期（可为 Invalid）
    val episodeId: Int,               // Bangumi episode id
    val episodeSort: EpisodeSort,     // 系列内序号
    val episodeEp: EpisodeSort?,      // 本季内序号
    val episodeName: String,          // 集标题（EpisodeInfo.displayName）
    val filename: String?,            // 视频文件名或数据源标题
    val fileHash: String?,            // ← 见 3.5，实际上永远是占位符
    val fileSize: Long?,
    val videoDuration: Duration,
)
```

它是从 `SearchDanmakuRequest` 构造的（[`DanmakuLoader.kt` L220-L235](https://github.com/open-ani/animeko/blob/main/app/shared/app-data/src/commonMain/kotlin/domain/danmaku/DanmakuLoader.kt)），而 `SearchDanmakuRequest` 在播放器侧构造（[`EpisodeDanmakuLoader.kt` L84-L120](https://github.com/open-ani/animeko/blob/main/app/shared/app-data/src/commonMain/kotlin/domain/episode/EpisodeDanmakuLoader.kt)）：

```kotlin
SearchDanmakuRequest(
    info.subjectInfo, info.episodeInfo, info.episodeId,
    filename = mediaData.filenameOrNull ?: selectedMedia?.originalTitle,
    fileLength = when (mediaData) {
        is SeekableInputMediaData -> mediaData.fileLength()
        is UriMediaData -> null
    },
    videoDuration = duration,
)
```
- `subjectNames` = Bangumi 条目的**全部别名**（中文名/原名/别名），这是匹配质量的关键。
- `filename` 是**原始文件名或 BT 标题**，**Animeko 不做任何解析**就直接丢给弹弹（见 3.3）。
- 构造后 `.distinctUntilChanged().debounce { null→0ms, 否则→1s }`，避免拖动进度条/切源时抖动。

### 3.2 DanDanPlay 的完整匹配流程（源码注释就是最好的伪代码）

源码里有一段非常清晰的中文注释（[`DandanplayDanmakuProvider.kt` L107-L120](https://github.com/open-ani/animeko/blob/main/danmaku/dandanplay/src/commonMain/kotlin/DandanplayDanmakuProvider.kt)）：

```
获取剧集流程:
 1. 用 Bangumi subject id 请求弹弹 Play 的 Bangumi.tv 映射接口
 2. 若失败, 获取该番剧所属季度的所有番的名字, 匹配 bangumi 条目所有别名
 3. 若失败, 用番剧名字搜索, 匹配 bangumi 条目所有别名
 4. 如果按别名精准匹配到了, 那就获取该番的所有剧集
 5. 如果没有, 那就提交条目名字给弹弹直接让弹弹获取相关剧集 (很不准)

匹配剧集流程:
 1. 用剧集在系列中的序号 (sort) 匹配
 2. 用剧集在当前季度中的序号 (ep) 匹配
 3. 用剧集名字模糊匹配
```

**完整伪代码（我按源码整理，行号见备注）**：

```python
def fetch_impl(req) -> "DanmakuFetchResult":
    # 预先构造一个"期望集名"，用于和弹弹的 episodeTitle 比对
    # 源码 L121-L122：注意 removePrefix("0")，把 "01" 变成 "1"
    ep_no = req.episode_ep or req.episode_sort
    prefixed_expected_episode_name = f"第{str(ep_no).removeprefix('0')}话 " + req.episode_name
    matcher = most_relevant(req.subject_primary_name, prefixed_expected_episode_name)

    # ---- 路线 0：Bangumi subject id 直接映射（最准）L230-L259
    if req.subject_id > 0:
        r = ddp.GET(f"/api/v2/bangumi/bgmtv/{req.subject_id}")
        if r.success and r.bangumi and r.bangumi.episodes:
            eps = [(ep.episodeId, r.bangumi.animeTitle, ep.episodeTitle, ep.episodeNumber) ...]
            hit = try_match_episodes(eps, req, prefixed_expected_episode_name, matcher)
            if hit: return hit

    # ---- 路线 1：季度列表按别名精确匹配 → 拉该番剧集 L261-L277 + L307-L347
    eps = (fetch_episodes_by_exact_subject_match(req)          # 内部：
           #   若 subjectPublishDate 无效 → None
           #   a. GET /api/v2/bangumi/season/anime/{year}/{seasonMonth}
           #      → 取 animeTitle ∈ req.subjectNames 的第一个
           #   b. 否则对每个 subjectName 调 GET /api/v2/search/anime?keyword=
           #      → 取 animeTitle ∈ req.subjectNames 的第一个
           #   c. GET /api/v2/bangumi/{animeId or bangumiId}
           or fetch_episodes_by_fuzzy_episode_search(req))     # 对每个别名
           #   GET /api/v2/search/episodes?anime={name.trim().substringBeforeLast(" ")}&episode=null
    hit = try_match_episodes(eps, req, prefixed_expected_episode_name, matcher)
    if hit: return hit

    # ---- 路线 2：把文件名交给弹弹自己匹配（最不准）L151-L181
    if req.filename:
        resp = ddp.POST("/api/v2/match", json={
            "fileName": req.filename,
            "fileSize": req.file_size,          # 可能为 None（不下发）
            "videoDuration": req.video_duration_seconds,
            "matchMode": "fileNameOnly",        # 硬编码
        })
        if resp.isMatched:
            match = resp.matches[0]
        else:
            # 即使弹弹说不精确，也用我们自己的 Levenshtein 在候选里挑一个
            chosen = matcher.match([(e.episodeId, e.animeTitle, e.episodeTitle, None) for e in resp.matches])
            if not chosen: return NoMatch
            match = resp.matches[chosen.id]
        return create_result(match.episodeId, Fuzzy(match.animeTitle, match.episodeTitle))

    return NoMatch


def try_match_episodes(eps, req, prefixed_name, matcher) -> result | None:
    # 源码 L184-L228
    # ① 系列内序号精确匹配
    if ep := first(e for e in eps if e.epOrSort == req.episode_sort):
        return create_result(ep.id, Exact(ep.subjectName, ep.episodeName))
    # ② 本季内序号精确匹配
    if ep := first(e for e in eps if e.epOrSort == req.episode_ep):
        return create_result(ep.id, Exact(...))
    # ③ 集名精确匹配（先原集名，再 "第N话 xxx" 形式）
    if req.episode_name:
        if ep := (first(e.episodeName == req.episode_name) or first(e.episodeName == prefixed_name)):
            return create_result(ep.id, Exact(...))
    # ④ 全部候选丢给 Levenshtein 取最小
    if eps and (best := matcher.match(eps)):
        return create_result(best.id, ExactSubjectFuzzyEpisode(...))
    return None


def create_result(episode_id, method):
    comments = ddp.GET(f"/api/v2/comment/{episode_id}?chConvert=0&withRelated=true").comments
    list = [parse_p(c) for c in comments if parse_p(c)]
    return DanmakuFetchResult(providerId="dandanplay",
                              matchInfo=DanmakuMatchInfo(serviceId="Dandanplay", count=len(list), method=method),
                              list=list)
    # 之后 categorize_by_service() 再按 senderId 前缀拆成 5 个 serviceId（保证至少返回 Dandanplay）
```

### 3.3 文件名解析规则 —— **Animeko 在弹幕层完全不做解析**

这是最容易误解的一点，明确结论：

- **Animeko 不解析文件名**。`filename` 原样传给弹弹的 `/api/v2/match`，由弹弹服务器做 `[字幕组][番名][01][1080P]` 这类解析。
- 弹幕层里唯一对字符串做的"加工"有两处：
  1. `prefixedExpectedEpisodeName = "第${ep}话 " + episodeName`（[L121-L122](https://github.com/open-ani/animeko/blob/main/danmaku/dandanplay/src/commonMain/kotlin/DandanplayDanmakuProvider.kt)）——只是拼给 Levenshtein 比对的"期望集名"。
  2. `subjectName.trim().substringBeforeLast(" ")`（[L283](https://github.com/open-ani/animeko/blob/main/danmaku/dandanplay/src/commonMain/kotlin/DandanplayDanmakuProvider.kt)）——搜剧集前把别名里最后一个空格之后的内容砍掉（去掉 `"进击的巨人 第二季"` 这类后缀？代码没解释，我按字面描述）。
  被注释掉的一行显示他们曾试过显式带上集数搜：`// episodeName = "第${...}话"`，注释说明"弹弹的是 EP 顺序"、"弹弹数据库有时候会只有 '第x话' 没有具体标题, 所以不带标题搜索就够了"。
- 真正**自己写文件名/标题解析器**的地方在**数据源层**（BT/在线源的媒体选择，跟弹幕无关）：
  [`datasource/api/src/commonMain/kotlin/topic/titles/`](https://github.com/open-ani/animeko/tree/main/datasource/api/src/commonMain/kotlin/topic/titles)
  （`PatternBasedRawTitleParser`、`LabelFirstRawTitleParser`、`RawTitleParser`，输出的类型是 `EpisodeRange`/`Resolution`/`SubtitleLanguage` 等）。
  示例规则（`PatternBasedRawTitleParser.kt` L24-L28）：
  ```kotlin
  private val brackets = Regex("""[\[【(](.*?)[]】)]""")
  private val newAnime = Regex("(?:★?|★(.*)?)([0-9]|[一二三四五六七八九十]{0,4}) ?[月年] ?(?:新番|日剧)★?")
  private val specialEpisode = Regex("★特别篇")
  private val excludeTags = arrayOf(newAnime, specialEpisode, Regex("(短篇动画)|(招募)"))
  ```
  **弹幕匹配不用它**。

### 3.4 与 Bangumi / 其它条目的关联方式

| 关联方式 | 实现 | 源码 |
|---|---|---|
| **首选**：Bangumi subject id → 弹弹条目 | `GET /api/v2/bangumi/bgmtv/{bgmtvSubjectId}` | L138-L151, L230-L259 |
| Bangumi 条目名/别名 → 弹弹条目 | 季度列表精确名匹配（取首播年+`seasonMonth`）→ 搜索兜底 | L307-L347 |
| 集身份 | Bangumi `episodeSort`（系列内）/ `episodeEp`（季内）/ `displayName` | `DanmakuFetchRequest` |
| Animeko 官方源 | 直接用 **Bangumi episodeId** 当路径参数（`GET /v1/danmaku/{episodeId}`） | `AniDanmakuProvider.kt` L40-L67 |
| 本地缓存 | 用 `(subjectId, episodeId)` 主键查询 | `DanmakuDao.kt` L65-L69 |

`seasonMonth` 换算见 `me.him188.ani.datasources.api.seasonMonth`（`subjectPublishDate.seasonMonth`，0 表示无效 → 直接放弃季度路线）。季度路线能成立的关键前提是 **Bangumi 的首播日期**可靠。

### 3.5 文件哈希匹配 —— **Animeko 没有实现**（已穷尽验证）

**结论：Animeko 不计算视频文件 hash，`/api/v2/match` 也从不发送 `fileHash`。**证据链（4 条独立证据）：

1. **不发送**：`matchVideo()` 的函数签名收 `fileHash: String?`，但构造 body 时**没有把它写进去**，且 `matchMode` 硬编码为 `fileNameOnly`。
   [`DandanplayClient.kt` L153-L178](https://github.com/open-ani/animeko/blob/main/danmaku/dandanplay/src/commonMain/kotlin/DandanplayClient.kt)
2. **传过来的就是占位符**：`SearchDanmakuRequest.fileHash` 的默认值是 `"aa".repeat(16)`（即 32 个 `a`），而**唯一的生产者** `EpisodeDanmakuLoader` 构造该对象时**没有传 `fileHash`**，所以运行时它恒等于 `"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"`。
   [`SearchDanmakuRequest.kt` L18-L26](https://github.com/open-ani/animeko/blob/main/app/shared/app-data/src/commonMain/kotlin/data/repository/danmaku/SearchDanmakuRequest.kt) / [`EpisodeDanmakuLoader.kt` L94-L104](https://github.com/open-ani/animeko/blob/main/app/shared/app-data/src/commonMain/kotlin/domain/episode/EpisodeDanmakuLoader.kt)
3. **全仓库只有文档样例出现过 hash 匹配**：`grep -rn "hashAndFileName"` 在整个仓库里**只命中 1 个文件**，就是 `danmaku/dandanplay/dandanplay.http`（一份手写的接口示例，写的是官方文档的例子，不是代码）：
   ```
   ### Match episodeId
   POST https://api.dandanplay.net/api/v2/match
   { "fileName": "[爱恋字幕社][10月新番][葬送的芙莉莲][Sousou no Frieren][01][1080P][MP4][GB][简中]",
     "fileHash": "0805b7f980d59b64dd382fe00b3ba2a5", "fileSize": 129000,
     "videoDuration": 1440, "matchMode": "hashAndFileName" }
   ```
   [`danmaku/dandanplay/dandanplay.http`](https://github.com/open-ani/animeko/blob/main/danmaku/dandanplay/dandanplay.http)
4. **没有哈希实现**：全仓库对视频文件做 MD5/SHA 的代码只有 `utils/io` 的通用 digest 工具（给鉴权签名用的 `DigestAlgorithm`），没有任何"读前 16MB"的逻辑。

**但官方规范明确规定了 hash 怎么算**（如果你要实现，照这个做）：

> `fileHash`：**文件前 16MB（16 × 1024 × 1024 Byte）数据的 32 位 MD5 结果，不区分大小写。**
> `fileName`：视频文件名，**不包含文件夹名称和扩展名**，特殊字符需转义。
> `matchMode` 枚举：`hashAndFileName` | `fileNameOnly` | `hashOnly`
> `fileSize`：文件总长度，单位 Byte。`videoDuration`：32 位整数秒，默认 0。
> `isMatched=true` 表示"精确关联"，此时 `matches` 只含一个结果，客户端应直接采用。

来源：官方 Swagger `https://api.dandanplay.net/swagger/v2/swagger.json` → `components.schemas.MatchRequest` / `MatchMode` / `MatchResponseV2`（Swagger UI：`https://api.dandanplay.net/swagger`）；接口语义说明见 [`/api/v2/match` description](https://api.dandanplay.net/swagger)。

> 💡 **对你要做的播放器来说这是可改进点**：Animeko 主动放弃了 hash 匹配（可能因为流式播放/缓存文件拿不到稳定字节流，或为避免读 16MB 的 IO 开销），而本地文件播放器（Python + PySide6）恰恰**能**稳定读到文件头 —— 建议你实现 `hashAndFileName`，精确度会明显优于 Animeko。

### 3.6 匹配失败时的降级策略（完整清单）

| 层级 | 策略 | 触发条件 | UI 入口 |
|---|---|---|---|
| 1 | Bangumi id 直映 | `subjectId > 0` | 自动 |
| 2 | 季度列表别名精确匹配 | 首播日期有效 | 自动 |
| 3 | 逐别名搜索匹配 | 上一步失败 | 自动 |
| 4 | 集序号/集名精确匹配 | 拿到候选剧集 | 自动 |
| 5 | **Levenshtein 最近邻** | 上面都不中 | 自动 |
| 6 | 文件名丢给弹弹 `/match` + 再 Levenshtein 挑 | 全部失败且有 filename | 自动 |
| 7 | **手动匹配**（搜番 → 选番 → 选集） | 用户点"重新匹配" | `MatchingDanmakuDialog` |
| 8 | 关闭该弹幕源 | 用户手动 | 弹幕源 Chip 下拉 |
| 9 | 每源 ±30s 时间轴偏移 | 匹配对了但时间轴错位 | 弹幕源下拉 → 时间轴偏移 |

**Levenshtein 匹配器（只有 30 行，直接抄）**：

```kotlin
// danmaku/api/src/commonMain/kotlin/provider/DanmakuMatcher.kt L30-L71
object DanmakuMatchers {
    fun first() = DanmakuMatcher { it.firstOrNull() }

    fun mostRelevant(targetSubjectName: String, targetEpisodeName: String): DanmakuMatcher = DanmakuMatcher { list ->
        list.minByOrNull {
            levenshteinDistance(it.subjectName, targetSubjectName) +
            levenshteinDistance(it.episodeName, targetEpisodeName)     // ← 两个距离"相加"
        }
    }

    private fun levenshteinDistance(lhs: CharSequence, rhs: CharSequence): Int { /* 标准 DP，滚动数组 */ }
}
```
注意：**是"条目名距离 + 集名距离"之和取最小**，不是分开加权；没有任何相似度阈值 —— 哪怕最相似的候选差得很远，也会被选中（所以 `ExactSubjectFuzzyEpisode` 这个"半模糊"标记很重要，UI 会提示用户）。

**UI 上如何呈现匹配质量**：`DanmakuMatchMethod` → 三种文案（精确 / 半模糊 / 模糊 / 无结果），见 [`DanmakuMatchInfoGrid.kt`](https://github.com/open-ani/animeko/blob/main/app/shared/src/commonMain/kotlin/ui/subject/episode/details/components/DanmakuMatchInfoGrid.kt) 里的 `DanmakuMatchMethodView` 与 `Lang.subject_episode_danmaku_match_exact / _semi_fuzzy / _fuzzy / _none`。

**手动匹配的完整交互状态机**（`MatchingDanmakuPresenter`，[`MatchingDanmakuUiState.kt`](https://github.com/open-ani/animeko/blob/main/app/shared/ui-episode/src/commonMain/kotlin/ui/episode/danmaku/MatchingDanmakuUiState.kt)）：
`submitQuery(q)` → `fetchSubjectList` → 弹"选条目"对话框 → `selectSubject(s)` → `fetchEpisodeList` → 弹"选集"对话框 → `selectEpisode(e)` → `fetchDanmakuList(subject, episode)` → `isFlowComplete=true` → 回调把结果写成 **override**：
```kotlin
// DanmakuLoader.kt L175-L182
fun overrideResults(provider: DanmakuProviderId, result: List<DanmakuFetchResult>) { ... }
```
override 只对"执行匹配的那一集"生效（用 `DanmakuRequestKey(subjectId, episodeId)` 判定），换集自动作废，且不会因为 UI 短暂停止收集而被清空（源码注释引用了 issue #2801）。

---

## 4. Q3 弹幕解析与数据模型

### 4.1 领域模型（跨源统一模型，建议直接照搬）

```kotlin
// danmaku/api/src/commonMain/kotlin/DanmakuInfo.kt L17-L46
data class DanmakuInfo(
    val id: String,               // 源内唯一；跨源唯一键需用 serviceId + ":" + id
    val serviceId: DanmakuServiceId,  // 弹幕源（value class，值 = "Animeko"/"Bilibili"/...）
    val senderId: String,         // 发送者 ID；本地发送时是 Animeko 用户 id，未登录为 ""
    val content: DanmakuContent,
) {
    val playTimeMillis get() = content.playTimeMillis
    val color get() = content.color
    val text get() = content.text
    val location get() = content.location
}

@Serializable
data class DanmakuContent(
    val playTimeMillis: Long,   // 毫秒
    val color: Int,             // RGB（不含 alpha）
    val text: String,
    val location: DanmakuLocation,
)

enum class DanmakuLocation { TOP, BOTTOM, NORMAL }   // NORMAL = 滚动/浮动
```

**字段对照表**：

| 字段 | 类型 | 含义 | DanDanPlay 来源 | Animeko 服务来源 |
|---|---|---|---|---|
| `id` | String | 源内唯一 ID | `CommentData.cid`（Long→String） | `AniDanmaku.id` |
| `serviceId` | String | 源标识 | 由 `senderId` 前缀推断（Bilibili/Acfun/Tucao/Baha/Dandanplay） | 固定 `"Animeko"` |
| `senderId` | String | 发送者 | `p` 串第 4 段（用户 ID，带 `[BiliBili]` 等平台前缀） | `AniDanmaku.senderId` |
| `playTimeMillis` | Long | 出现时间（毫秒） | `p[0]` 秒 × 1000（`(timeSecs * 1000).toLong()`，**截断非四舍五入**） | `AniDanmakuInfo.playTime` |
| `color` | Int | RGB 整数 | `p[2]` | `AniDanmakuInfo.color` |
| `text` | String | 正文 | `m` | `AniDanmakuInfo.text` |
| `location` | enum | 显示模式 | `p[1]`：1→NORMAL、4→BOTTOM、5→TOP，**其他值直接丢弃该条** | `TOP/BOTTOM/NORMAL` 枚举 |

> **注意：Animeko 的数据模型里没有这些字段** —— 没有字号（`fontSize`）、没有弹幕池/层级（`pool`）、没有时间戳/发送时间、没有点赞数、没有 `isSelf`（`isSelf` 是**渲染期**用 `selfId == senderId` 现算的，见 `DanmakuPresentation`）。
> 也就是说：**"字号"和"池子"这两类字段在 Animeko 中不支持**，模型层就被丢掉了（弹弹的 `p` 串本来也没有这些字段；B 站的 `pool`/`fontsize` 在弹弹聚合时已丢失）。

### 4.2 DanDanPlay `p` 串解析（完整实现）

```kotlin
// danmaku/dandanplay/src/commonMain/kotlin/data/MatchVideo.kt L22-L61
@Serializable class DandanplayDanmaku(val cid: Long, val p: String, val m: String)

fun DandanplayDanmaku.toDanmakuOrNull(): DanmakuInfo? {
    // p 格式: 出现时间,模式,颜色,用户ID
    val (time, mode, color, userId) = p.split(",").let { if (it.size < 4) return null else it }
    val timeSecs = time.toDoubleOrNull() ?: return null
    val content = DanmakuContent(
        playTimeMillis = (timeSecs * 1000).toLong(),
        color = color.toIntOrNull() ?: return null,
        text = m,
        location = when (mode.toIntOrNull()) {
            1 -> DanmakuLocation.NORMAL
            4 -> DanmakuLocation.BOTTOM
            5 -> DanmakuLocation.TOP
            else -> return null            // 未知模式整条丢弃
        },
    )
    return DanmakuInfo(id = cid.toString(), serviceId = DanmakuServiceId.Dandanplay,
                       senderId = userId, content = content)
}
```
解析失败策略：`p` 分段 < 4 / 时间非数字 / 颜色非数字 / 模式不识别 → **返回 null（丢弃该条）**，不抛异常。

### 4.3 Animeko 服务侧的 JSON 模型

见 2.3 节（`AniDanmaku` / `AniDanmakuInfo` / `AniDanmakuLocation`）。转换成领域模型时 `location` 是枚举直映（`AniDanmakuProvider.kt` L77-L83）。

### 4.4 模式枚举总表

| Animeko `DanmakuLocation` | 语义 | DanDanPlay `p[1]` | DanDanPlay 发送 `mode` | Animeko 服务 `location` |
|---|---|---|---|---|
| `NORMAL` | 滚动（浮动） | `1` | 1 | `"NORMAL"` |
| `BOTTOM` | 底部固定 | `4` | 4（官方 send schema 写反了） | `"BOTTOM"` |
| `TOP` | 顶部固定 | `5` | 5（同上） | `"TOP"` |

**没有**"逆向弹幕"、"精确定位（advanced）"、"代码弹幕"、"BAS 弹幕"等模式。Animeko 只支持这 3 种。

### 4.5 文本清洗（两处，都要实现）

```kotlin
// ① danmaku/api/src/commonMain/kotlin/DanmakuSanitizer.kt L12-L25 —— 换行替换
object DanmakuSanitizer {
    fun sanitize(danmaku: DanmakuInfo): DanmakuInfo = danmaku.run {
        if (text.indexOf("\n") == -1) return@run this
        copy(content = content.copy(text = text.replace("\n\r"," ").replace("\r\n"," ").replace("\n"," ").trim()))
    }
}

// ② EpisodeDanmakuLoader.kt L210-L225 —— 丢弃空/纯控制字符弹幕（issue #1643）
private fun sanitizeDanmakuText(text: String): String? {
    if (text.isEmpty()) return null
    val result = text.trim { it.isWhitespace() || it.isISOControl() }
                     .filterNot { it.isISOControl() }
    return result.ifEmpty { null }          // 返回 null → 该条被丢弃
}
```
`TimeBasedDanmakuSession.create()` 还会对每条 `sanitize` 一次并按 `playTimeMillis` 排序（[`DanmakuCollection.kt` L88-L96](https://github.com/open-ani/animeko/blob/main/danmaku/api/src/commonMain/kotlin/DanmakuCollection.kt)）。

### 4.6 有没有 protobuf / 自定义二进制格式？

**没有**。全链路是 JSON（DanDanPlay REST + Animeko OpenAPI）。没有 protobuf、没有自定义流式协议。
（"B 站 protobuf 弹幕"在 Animeko 中不存在，因为 B 站弹幕是经弹弹聚合后以 DanDanPlay JSON 形态返回的。）

### 4.7 缓存格式与位置

Room（SQLite）表 `danmaku`：

```kotlin
// app/shared/app-data/src/commonMain/kotlin/data/persistent/database/dao/DanmakuDao.kt L30-L61
@Entity(
    tableName = "danmaku",
    primaryKeys = ["id"],
    foreignKeys = [
        ForeignKey(SubjectCollectionEntity::class, ["subjectId"], ["subjectId"], onDelete = CASCADE),
        ForeignKey(EpisodeCollectionEntity::class, ["subjectId","episodeId"], ["subjectId","episodeId"], onDelete = CASCADE),
    ],
    indices = [Index(["id"]), Index(["subjectId"]), Index(["subjectId","episodeId"])],
)
class DanmakuEntity(
    val id: String,
    val subjectId: Int,
    val episodeId: Int,
    val serviceId: DanmakuServiceId,             // 真实源（dandanplay/bilibili...）
    val presentationServiceId: DanmakuServiceId, // 外显源（Animeko 缓存后按这个分组回放）
    val senderId: String,
    @Embedded(prefix = "content_") val content: DanmakuContent,
)
```

| 项 | 值 | 来源 |
|---|---|---|
| 数据库文件 | `ani_room_database_main.db` | [`DatabaseStorage.desktop.kt` L25-L27](https://github.com/open-ani/animeko/blob/main/app/shared/app-data/src/desktopMain/kotlin/data/persistent/database/DatabaseStorage.desktop.kt)、`DatabaseStorage.android.kt` L17-L20、`DatabaseStorage.ios.kt` L21-L25 |
| 列前缀 | `content_`（`content_playTimeMillis` / `content_color` / `content_text` / `content_location`） | `@Embedded(prefix="content_")` |
| 过期策略 | 外键 `CASCADE`：删除 Bangumi 收藏/剧集记录会连带删弹幕 | 同上 |
| 过滤规则存储 | DataStore 文件 **`danmakuFilter`**（`List<DanmakuRegexFilter>` JSON） | [`SettingsStore.kt` L77-L86](https://github.com/open-ani/animeko/blob/main/app/shared/app-data/src/commonMain/kotlin/data/persistent/SettingsStore.kt) |
| 弹幕配置存储 | preference key **`danmaku_config`** | [`SettingsRepository.kt` L169-L170](https://github.com/open-ani/animeko/blob/main/app/shared/app-data/src/commonMain/kotlin/data/repository/user/SettingsRepository.kt) |
| 全局开关 | preference key **`danmaku_enabled`**（默认 true） | 同上 L168 |
| 发送样式 | `danmakuSettings`（`sendColor` / `sendLocation`） | 同上 + `EpisodeViewModel.kt` L1052-L1061 |

配置文件序列化（**字段名固定，可直接作为 JSON schema**）：
```kotlin
// app/shared/app-data/src/commonMain/kotlin/data/models/danmaku/DanmakuConfigSerializer.kt L26-L49
class DanmakuConfigData(style, speed, safeSeparation, displayArea,
                        enableColor, enableTop, enableFloating, enableBottom, isDebug)
class DanmakuStyleData(fontSize, fontWeight, alpha, strokeColor: ULong, strokeWidth)
```

---

## 5. Q4 渲染引擎

### 5.1 技术选型

**Compose Multiplatform `Canvas` + 自绘**，**没有用任何第三方弹幕库**（没有 DanmakuFlameMaster 之类）。核心特点：

- 每条弹幕在第一次绘制时被**预渲染成一张 `ImageBitmap`（含描边）并永久缓存**，之后每帧只做 `drawImage`。
- 弹幕位置是**帧时间的纯函数**（不做逐帧积分，因此无累积误差）。
- 轨道（Track）承担**避让判定**；渲染循环只负责把轨道里的对象画出来。

```kotlin
// danmaku/ui/src/commonMain/kotlin/StyledDanmaku.kt L72-L82
private var imageBitmap: ImageBitmapWithOffset? = null
internal fun DrawScope.draw(screenPosX: Float, screenPosY: Float) {
    val cachedImage = imageBitmap ?: createDanmakuImageBitmap(solidTextLayout, borderTextLayout)
        .also { imageBitmap = it }
    drawImage(cachedImage.bitmap, Offset(screenPosX, screenPosY) + cachedImage.offset)
}
```

位图生成（`skikoMain` 桌面/macOS/Linux 用 Skia Surface；`androidMain` 用 `Bitmap.Config.ARGB_8888`）：

```kotlin
// danmaku/ui/src/skikoMain/kotlin/StyledDanmaku.skiko.kt L19-L40
internal actual fun createDanmakuImageBitmap(solidTextLayout, borderTextLayout): ImageBitmapWithOffset {
    val width  = max(borderTextLayout?.size?.width  ?: 0, solidTextLayout.size.width ).coerceAtLeast(1)
    val height = max(borderTextLayout?.size?.height ?: 0, solidTextLayout.size.height).coerceAtLeast(1)
    val extraMargin = height shr 1                       // 上下左右各留 h/2 边距，防止描边被裁
    val destSurface = Surface.makeRasterN32Premul(width + extraMargin*2, height + extraMargin*2)
    destCanvas.translate(extraMargin, extraMargin)
    borderTextLayout?.let { destCanvas.paintIfNotEmpty(it) }   // ① 先画描边（Stroke）
    destCanvas.paintIfNotEmpty(solidTextLayout)                // ② 再画实心字（Fill）
    return ImageBitmapWithOffset(snapshot, Offset(-extraMargin, -extraMargin))
}
```
**描边 = 同一段文字用 `Stroke(width=strokeWidth, join=Round, miter=3)` 画两遍**（先生成 border layout，再生成 solid layout，最后叠画），见 `DanmakuStyle.styleForBorder()/styleForText()`（[`ui-config/DanmakuConfig.kt` L84-L114](https://github.com/open-ani/animeko/blob/main/danmaku/ui-config/src/commonMain/kotlin/DanmakuConfig.kt)）。

颜色处理（**很关键的一个位运算细节**）：
```kotlin
// StyledDanmaku.kt L53-L55
color = if (enableColor) Color(0xFF_00_00_00L or presentation.danmaku.color.toUInt().toLong()) else Color.White
```
即把 RGB 整数**强制置上不透明 alpha**，`enableColor=false` 时全部画白色。
自己的弹幕（`isSelf`）**加下划线**：`textDecoration = if (presentation.isSelf) TextDecoration.Underline else null`。

### 5.2 渲染组件与职责

| 类/文件 | 职责 |
|---|---|
| [`DanmakuHost`](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/DanmakuHost.kt) | Composable：测量宿主尺寸、创建 TextMeasurer、驱动帧循环与逻辑 tick、Canvas 绘制 |
| [`DanmakuHostState`](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/DanmakuHostState.kt) | 状态中枢：轨道集合、配置响应、`trySend`/`send`/`repopulate`/`setPaused` |
| [`FloatingDanmakuTrack`](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/FloatingDanmakuTrack.kt) | 滚动轨道：碰撞检测、放置、速度计算 |
| [`FixedDanmakuTrack`](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/FixedDanmakuTrack.kt) | 顶部/底部轨道：同一时刻只显示 1 条 + 1 条 pending |
| [`StyledDanmaku`](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/StyledDanmaku.kt) | 文本测量 + 位图缓存 + 绘制 |
| [`FrameTimeSmoother`](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/FrameTimeSmoother.kt) | 帧间隔 PLL 平滑（桌面 Skiko 必需） |
| [`DanmakuCollectionIterator`](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/DanmakuCollectionIterator.kt) | 把多条轨道拼成一个迭代器，供绘制遍历（不拷贝 list） |

### 5.3 轨道数量与轨道高度（怎么算）

```kotlin
// DanmakuHostState.kt L218-L234
val dummyTextLayout = dummyDanmaku(measurer, baseStyle, newConfig.style, dummyText = "哈哈哈哈")
val verticalPadding = with(density) { (danmakuTrackProperties.verticalPadding * 2).dp.toPx() }  // 1*2 dp
val trackHeight = (dummyTextLayout.danmakuHeight + verticalPadding).toInt()
val trackCount  = floor(newHeight / trackHeight * newConfig.displayArea).coerceAtLeast(1f).toInt()
```
- **轨道高度**：用固定样板文本 `"哈哈哈哈"`（全角，取最宽/最高）在当前字号下测量出的高度 + 2dp 内边距。
- **轨道数** = `floor(宿主高度 ÷ 轨道高度 × displayArea)`，至少 1。
- **基准宽度** `baseTrackSpeedWidth` = 同一个 `"哈哈哈哈"` 测量出的**宽度**，用于后面"按长度调速度"。
- 轨道实例创建（[L337-L377](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/DanmakuHostState.kt)）：
  - `floatingTrack` 数量 = `enableFloating ? trackCount : 0`
  - `topTrack` 数量 = `trackCount × enableTop`
  - `bottomTrack` 数量 = `trackCount × enableBottom`
  - 数量变化时 `setTrackCountImpl` 会**对被裁掉的轨道调用 `clearAll()`**（[L901-L911](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/DanmakuHostState.kt)）。

### 5.4 浮动轨道：放置与防重叠算法（核心中的核心）

**坐标定义**（注意方向）：

```
轨道宽度 trackWidth（px）
                      distanceX —— 弹幕已滚过的距离（>=0）
   ┌─────────────────────────────────────────────┐
   │                                             │
   └─────────────────────────────────────────────┘
   ↑                                             ↑
 左边缘(x=0)                                 右边缘(x=trackWidth)
   弹幕屏幕 x 坐标 = trackWidth - distanceX
   弹幕 left()  = trackWidth - distanceX
   弹幕 right() = left() + danmakuWidth + safeSeparation      ← 右侧含安全间隔
   弹幕消失条件 isGone(): right() <= 0
```
```kotlin
// FloatingDanmakuTrack.kt L171-L179
private fun FloatingDanmaku<T>.left()  = trackWidth.intValue.toFloat() - distanceX
private fun FloatingDanmaku<T>.right() = left() + danmaku.danmakuWidth + safeSeparation
private fun FloatingDanmaku<T>.isGone(): Boolean = right() <= 0
```

**位置公式（纯函数，无积分、无累积误差）**：
```kotlin
// FloatingDanmakuTrack.kt L304-L306
val distanceX: Float
    get() = ((frameTimeNanosState.longValue - placeFrameTimeNanos) / 1_000L) / 1_000_000f * speedPxPerSecond
//        = (当前帧时间 - 放置帧时间) 纳秒 → 毫秒 → 秒 × 速度(px/s)
```

**会不会撞车（`willClash`）—— 用"到达左边缘的剩余时间"比较，而不是比较距离**：
```kotlin
// FloatingDanmakuTrack.kt L181-L190
private fun willClash(previous: FloatingDanmaku<T>, next: FloatingDanmaku<T>): Boolean {
    val previousRightReachTrackLeftCostTime = previous.right() / previous.speedPxPerSecond
    val nextLeftReachTrackLeftCostTime      = next.left()  / next.speedPxPerSecond
    return previousRightReachTrackLeftCostTime > nextLeftReachTrackLeftCostTime
}
```
含义：前提是 `previous` 在 `next` 前方（`previous.left() < next.left()`）。如果"前车尾部出屏所需时间" > "后车头部出屏所需时间"，后车会追尾 → 撞车。
**这个判据天然支持"不同速度的弹幕"**（长弹幕跑得快也不会追尾短弹幕）。

**放置判定 + 二分插入（`isNonOverlapping`）**：
```kotlin
// FloatingDanmakuTrack.kt L196-L235（我整理成伪代码）
fun isNonOverlapping(list) -> Int:      # 返回插入索引；-1 表示放不下
    if list.isEmpty: return 0
    # fast path：新弹幕完全在最后一条右侧
    if left() >= list.last().right():
        return -1 if willClash(list.last(), this) else list.size
    # 二分找到按 left() 排序的插入点（list 恒按 distanceX 倒序 = left() 升序）
    idx = lowerBound(list, key = left())
    if idx < size and right() >= list[idx].left():   return -1    # 与右侧邻居重叠
    if idx > 0      and left() <= list[idx-1].right(): return -1  # 与左侧邻居重叠
    when:
        idx >= size -> -1 if willClash(list.last(), this) else size
        idx == 0    -> -1 if willClash(this, list[0])     else 0
        else        -> -1 if (willClash(list[idx-1], this) or willClash(this, list[idx])) else idx
```

**完整放置入口**：
```kotlin
// FloatingDanmakuTrack.kt L116-L140  checkPlaceableImpl
fun checkPlaceableImpl(danmaku, placeFrameTimeNanos, overrideSpeedMultiplier) -> (FloatingDanmaku, index)? {
    if (trackWidth <= 0) return null                                   // 轨道宽 0 一定不能放
    if (placeFrameTimeNanos != NOT_PLACED
        && frameTimeNanos - placeFrameTimeNanos < 0) return null        // 不能放到"未来"的轨道右侧之外
    val upcoming = danmaku.createFloating(placeFrameTimeNanos, overrideSpeedMultiplier)
    if (list.isEmpty()) return if (upcoming.isGone()) null else (upcoming, 0)
    if (upcoming.isGone()) return null
    val idx = upcoming.isNonOverlapping(list)
    return if (idx == -1) null else (upcoming, idx)
}
```
**放置时传入 `placeFrameTimeNanos < 当前帧时间` 是允许的**（表示"这条弹幕已经滚了一段了"），这正是 repopulate 能精确还原位置的基础。

### 5.5 速度模型（"每秒多少像素"到底怎么算）

**用户参数**：`DanmakuConfig.speed`，单位 **dp/s**，默认 **88f**（[`DanmakuConfig.kt` L38-L44](https://github.com/open-ani/animeko/blob/main/danmaku/ui-config/src/commonMain/kotlin/DanmakuConfig.kt)）。UI 上显示的"弹幕速度"是相对 `DanmakuConfig.Default.speed` 的百分比，可调范围 **0.2×–3×**。

**换算成 px/s**（在轨道构造/更新时一次性换算）：
```kotlin
// DanmakuHostState.kt L340
val newFloatingTrackSpeed = with(uiContext.density) { danmakuConfig.speed.dp.toPx() }
```
即 `baseSpeedPxPerSecond = speed(dp/s) × density`。**"不同屏幕密度怎么换算"的答案就在这里：Compose 的 `dp.toPx()`，等价于 `speed × (屏幕逻辑DPI/160)`。**

**按文本长度自适应加速（很妙的一段）**：
```kotlin
// FloatingDanmakuTrack.kt L144-L151
val multiplier = speedMultiplier.floatValue                       // 默认 1.14
        .pow(log(danmakuWidth.toFloat() / baseSpeedTextWidth, 2f)) // 以 2 为底的对数
        .coerceAtLeast(1f)
val finalSpeedMultiplier = if (randomizeSpeedFluctuation == 0f) multiplier
    else multiplier + (Random.nextFloat() - 0.5f) * 2f * randomizeSpeedFluctuation   // ±0.0875
```
即：`倍率 = 1.14 ^ log2(本弹幕宽度 / 基准宽度"哈哈哈哈"的宽度)`，下限 1.0，再叠加 **±8.75% 的随机抖动**（`randomizeSpeedFluctuation = 0.0875f`，[L43](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/FloatingDanmakuTrack.kt)）。
效果：**等于基准宽度的弹幕 = 1× 基础速度；2 倍宽度的弹幕 ≈ 1.14×**；随机抖动用来打散同屏弹幕的整齐感。

**速度变更不跳变**（改设置时）：
```kotlin
// FloatingDanmakuTrack.kt L318-L327
internal fun updateSpeed(newSpeedPxPerSecond: Float) {
    if (newSpeed == speedPxPerSecond || newSpeed <= 0f) return
    val currentDistanceX = distanceX                    // 用旧速度算出当前位置
    placeFrameTimeNanos = frameTimeNanos - (currentDistanceX / newSpeed * 1e9).toLong()  // 重新锚定
    speedPxPerSecond = newSpeed                         // 位置连续，不跳变
}
```
`baseSpeedPxPerSecond` 的 setter（[L50-L55](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/FloatingDanmakuTrack.kt)）会对轨道内所有弹幕调用 `updateSpeed`。

### 5.6 固定轨道（顶部 / 底部）

```kotlin
// FixedDanmakuTrack.kt L47-L60
override fun canPlace(danmaku, placeFrameTimeNanos): Boolean {
    if (currentDanmaku != null || pendingDanmaku != null) return false   // 每条轨道同时只能有 1+1 条
    if (placeFrameTimeNanos == NOT_PLACED) return true
    return frameTimeNanos - placeFrameTimeNanos < durationMillis         // 且放置时间不能太旧
}
```
- 每条固定轨道只有 **1 条正在显示 + 1 条 pending**；`tick()` 里超过 `durationMillis` 就清空 current 并消费 pending（[L85-L96](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/FixedDanmakuTrack.kt)）。
- **显示时长固定 5000ms**，源码注释明确："顶部或底部弹幕的显示时间，现在还不能自定义"（`DanmakuTrackProperties.fixedDanmakuPresentDuration = 5000`）。
- Y 坐标：顶部轨道 `y = index × trackHeight`；底部轨道 `y = hostHeight - (index+1) × trackHeight`（[L139-L145](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/FixedDanmakuTrack.kt)）。
- X 坐标：**水平居中** `x = (hostWidth - danmakuWidth) / 2f`（[`DanmakuHost.kt` L148-L151](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/DanmakuHost.kt)）。

### 5.7 `trySend` vs `send`（放不下的处理策略）

```kotlin
// DanmakuHostState.kt L516-L563
suspend fun trySend(danmaku, placeFrameTimeNanos = NOT_PLACED): Boolean
        // 依次尝试每个轨道 tryPlace，全失败返回 false（调用方丢弃该条）
        // 顺序：floatingTrack / topTrack / bottomTrack 里 firstNotNullOfOrNull

// DanmakuHostState.kt L572-L662  —— "保证发出去"
suspend fun send(danmaku: DanmakuPresentation)
        // NORMAL：遍历所有浮动轨道，找"最后一条弹幕距右边缘最近"的轨道，
        //         算出它尾部完全可见还需多少时间，把新弹幕的 placeFrameTimeNanos 推迟到那一刻：
        //   placeTimeNanos = (maxDanmakuRight.absoluteValue / floatingTrackSpeed * 1e9).toLong()
        //   track.place(styledDanmaku, currentElapsedFrameTimeNanos + placeTimeNanos)
        // TOP/BOTTOM：找最早空出的轨道 → track.setPending(...)；
        //             所有轨道都有 pending 时 随机挑一条覆盖 pending（保证自己发的弹幕一定上屏）
```
- **普通弹幕播放流用 `trySend`**（放不下就丢，符合弹幕语义）。
- **用户自己发的弹幕用 `send`**（一定要看到）。
- **同屏数量上限**：浮动轨道**没有硬上限**，只受"轨道数 × 避让判定"约束；固定轨道每条最多 2 条（1 显示 + 1 待发）。`DanmakuSessionFlowState.repopulateMaxCount` 默认值是 40，但生产路径把它设成 `Int.MAX_VALUE`（见 5.9）。

### 5.8 装填（repopulate）：快进/切源/改配置后如何重建屏幕

**两种模式**（[`DanmakuHostState.prepareRepopulate` L712-L761](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/DanmakuHostState.kt)）：

```kotlin
const val REPOPULATE_CONTINUOUS_THRESHOLD_MILLIS = 10_000L   // L839

fun prepareRepopulate(list, currentPlayMillis): List<DanmakuPresentation> {
    // 屏幕上"最新的弹幕发送时间"
    newestPresentPlayTime = max over all present danmaku of playTimeMillis
    isContinuous = list.isNotEmpty()
        && newestPresentPlayTime != MIN
        && abs(currentPlayMillis - newestPresentPlayTime) <= 10_000

    if (!isContinuous) { clearPresentDanmaku(); return list }   // 跳转 → 清屏重灌

    // 连续 → 增量更新：只在屏弹幕保持原位置与速度不变
    incomingKeys = { "${serviceId}:${id}" }        // ← 跨源唯一键
    coveredStartMillis = min(playTimeMillis of list)
    shouldRemove(info) = key(info) !in incomingKeys
                      && (info.playTimeMillis >= coveredStartMillis      // 已被过滤/源被关
                          || info.playTimeMillis > currentPlayMillis)    // 稍微快退了一点
    floatingTrack.forEach { it.removeAll { shouldRemove(...) } }
    topTrack/bottomTrack.forEach { it.removeCurrentIf { shouldRemove(...) } }
    return list.filter { key(it) !in presentKeys }   // 只返回需要新放置的
}
```
> 源码注释解释了为什么用"屏幕最新弹幕时间"而不是"上一次播放位置"来判断连续性：**这样判断不受播放倍速影响**。

**实际放置（`DanmakuRepopulator.repopulate`，[L848-L899](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/DanmakuHostState.kt)）**：
```kotlin
fun repopulate(list, currentPlayMillis) {
    sortedList = list.sortedBy { playTimeMillis }
    floating = sortedList.filter { location == NORMAL }
    if (floating.isNotEmpty()) {
        firstTime = floating.first().playTimeMillis
        firstPlaceFrameTime = currentFrameTimeNanos - (currentPlayMillis - firstTime) * 1_000_000L
        floating.forEach {
            placeFrameTime = firstPlaceFrameTime + (it.playTimeMillis - firstTime) * 1_000_000L
            if (placeFrameTime >= 0) onSend(it, placeFrameTime)     // 负数（超出屏幕）跳过
        }
    }
    fixed = sortedList.filterNot { NORMAL }
    if (fixed.isNotEmpty()) {
        // 固定弹幕**倒序**放置：后出现的先放，保证轨道上留下的是最早那条
        lastTime = fixed.last().playTimeMillis
        lastPlaceFrameTime = currentFrameTimeNanos - (currentPlayMillis - lastTime) * 1_000_000L
        fixed.asReversed().forEach {
            placeFrameTime = lastPlaceFrameTime - (lastTime - it.playTimeMillis) * 1_000_000L
            if (placeFrameTime >= 0) onSend(it, placeFrameTime)
        }
    }
}
```
**核心思想：把"弹幕时间轴"映射到"帧时间轴"，用 `placeFrameTimeNanos` 一次性定位，不需要逐帧推进。**

**装填时机**（[`DanmakuSessionAlgorithm` L308-L390](https://github.com/open-ani/animeko/blob/main/danmaku/api/src/commonMain/kotlin/DanmakuCollection.kt)）：
```kotlin
fun tick(sendEvent) {
    curTime = state.curTimeShared                       // = 播放器进度
    if (curTime == INFINITE) return
    if (state.lastTime == INFINITE                       // 第一帧
        || abs(curTime - state.lastTime) >= max(repopulateThreshold(), 3s)) {
        // —— 快进/快退超过阈值 → 重新装填
        targetTime = curTime - repopulateDistance
        state.lastIndex = binarySearch 定位到 targetTime 附近（-1 表示从头）
        emit(Repopulate(从 targetTime 开始到 curTime 为止（上限 repopulateMaxCount 条，跳过未到时间的）, curTime))
        return
    }
    // —— 正常推进：把"时间已到"的弹幕逐条 Add，遇到"还没到时间"就停（list 已排序）
    useEachDanmaku { item ->
        if (curTimeMillis < item.playTimeMillis) return
        if (!sendEvent(Add(item))) return               // channel 满 → 下一逻辑帧再试
    }
}
```
生产参数（[L132-L137](https://github.com/open-ani/animeko/blob/main/danmaku/api/src/commonMain/kotlin/DanmakuCollection.kt)）：
| 参数 | 值 | 含义 |
|---|---|---|
| `repopulateThreshold` | `3.seconds × playbackSpeed` | 进度跳变超过它才重新装填（**随倍速缩放**） |
| `repopulateDistance` | `20.seconds` | 重新装填时回溯 20s 的弹幕（与 `DanmakuTrackProperties.initialFrameTimeNanos = 20e9` 对应） |
| `repopulateMaxCount` | `Int.MAX_VALUE`（默认值 40 仅在单测中用） | 单次装填上限 |

**算法心跳：50ms**（20 Hz，`delay(1000/20)`，[L189](https://github.com/open-ani/animeko/blob/main/danmaku/api/src/commonMain/kotlin/DanmakuCollection.kt)）——这是"事件产生"频率，**不是渲染频率**。
**移除心跳：1000ms**（[`DanmakuHost.kt` L110-L117](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/DanmakuHost.kt)）——每秒清理一次已滚出屏幕的弹幕（配合 `clipToBounds` 所以偶尔多画一点不可见弹幕无妨）。

### 5.9 帧时间平滑（`FrameTimeSmoother`，桌面端的必修课）

源码注释写得很清楚，我直接引用并总结：

> 在桌面端 (Skiko)，帧回调的时间戳是渲染 tick 在 EDT 上开始执行时采样的 `System.nanoTime()`，而不是这一帧实际上屏 (vsync) 的时间。回调时刻受线程调度影响抖动很大（例如 12ms, 21ms, 14ms, ...），但画面上屏间隔是均匀的。如果直接用原始间隔积分弹幕位置，弹幕的速度会随测量噪声振荡，在匀速滚动中表现为肉眼可见的左右顿挫。（Android 的 Choreographer 提供 vsync 对齐时间戳，无此问题。）

```kotlin
// FrameTimeSmoother.kt L35-L97（简化 PLL）
class FrameTimeSmoother(
    periodSmoothingFactor = 1f/32f,   // 刷新周期 EMA 系数
    correctionGain        = 0.1f,     // 相位误差回授增益
    maxCorrectionRatio    = 0.2f,     // 单帧最大修正量（相对周期）
    snapThresholdNanos    = 250_000_000L,  // >250ms 视为真实长间隔，直接透传
) {
    fun smooth(rawDeltaNanos: Long): Long {
        if (rawDeltaNanos <= 0) return 0
        if (rawDeltaNanos >= snapThresholdNanos) { errorNanos = 0f; return rawDeltaNanos }   // 暂停恢复/遮挡
        if (periodNanos == 0f) { periodNanos = clamp(rawDelta); return rawDelta }
        clampedDelta = clamp(rawDelta, period/2, period*2).coerceIn(2ms, 50ms)  // 20Hz..500Hz
        periodNanos += (clampedDelta - periodNanos) * periodSmoothingFactor
        errorNanos  += rawDeltaNanos - periodNanos                    // 真实累计 - 平滑累计
        maxCorrection = periodNanos * maxCorrectionRatio
        correction = (errorNanos * correctionGain).coerceIn(-maxCorrection, maxCorrection)
        errorNanos -= correction
        return (periodNanos + correction).toLong().coerceAtLeast(0L)
    }
}
```
帧循环：
```kotlin
// DanmakuHostState.kt L447-L470
internal suspend fun interpolateFrameLoop() {
    var currentFrameTimeNanos = withFrameNanos { it }
    while (true) {
        withFrameNanos { nanos ->
            val rawDelta = nanos - currentFrameTimeNanos
            val delta = frameTimeSmoother.smooth(rawDelta)     // ← 平滑
            elapsedFrameTimeNanos += delta                      // ← 弹幕时钟（暂停时不累加）
            currentFrameTimeDeltaNanos = delta
            currentFrameRawDeltaNanos = rawDelta
            currentFrameTimeNanos = nanos
            calculateDanmakuInFrame()                           // 惰性计算 y
            danmakuUpdateSubscription++                         // 触发重绘
        }
    }
}
```
**`elapsedFrameTimeNanos` 初值 = `initialFrameTimeNanos` = 20 秒**（[`DanmakuTrackProperties.kt` L38](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/DanmakuTrackProperties.kt)），注释写"workaround for emulating frame time"——这样 `placeFrameTimeNanos` 有足够空间为负/为正而不出问题。

### 5.10 时间轴对齐：暂停 / 拖动 / 倍速 的行为（务必照抄语义）

| 场景 | 行为 | 实现 |
|---|---|---|
| 播放器时钟 | 弹幕时间 = `player.currentPositionMillis`（媒体时间），每帧写入 `state.curTimeShared` | `EpisodeDanmakuLoader.kt` L127-L131 |
| 暂停 | `player.state.collect { danmakuHostState.setPaused(!it.isPlaying) }`；暂停时**帧循环协程根本不启动**（`LaunchedEffect(state.paused) { if (!state.paused) interpolateFrameLoop() }`），`elapsedFrameTimeNanos` 停止累加 → 弹幕停在原地 | `PlayerDanmakuHost.kt` L31-L35、`DanmakuHost.kt` L108 |
| 恢复播放 | 帧循环重启；`rawDelta` 通常 < 250ms，若因暂停很久则走 `snapThreshold` 直接透传 | `FrameTimeSmoother` |
| 拖动进度条（seek） | 拖动时播放器一般处于 paused → 弹幕冻结；落点后进度跳变 > `3s×倍速` → 触发 `Repopulate`，`DanmakuHostState.repopulate` 清屏并按落点重灌最近 20s | `DanmakuSessionAlgorithm.tick` L316-L373 |
| **倍速播放** | ① 事件产生速率随进度流自然加快；② `repopulateThreshold` 乘上倍速（否则 2× 时正常播放会被误判为跳转）；③ **弹幕滚动速度不随倍速变化**（UI 明确写着"弹幕速度不会跟随视频倍速变化"，见 `EpisodeVideoSettings.kt` L159 + `app/shared/app-lang/src/androidMain/res/values/strings.xml` L1528） | `DanmakuCollection.kt` L134 |
| 切集 | `SearchDanmakuRequest` 变化 → `transformLatest` 先 `emit(null)` 清空历史弹幕 → 重新加载 | `DanmakuLoader.kt` L86-L88 |
| 改弹幕源开关 / 改时间轴偏移 | `config.mapLatest { }` 重建 `DanmakuSession` → 触发 repopulate（不换集，所以是"连续增量更新"路径，屏幕上的弹幕位置不跳变） | `EpisodeDanmakuLoader.kt` L126-L132 |

**暂停时也要能重绘**：`DanmakuHostState.repopulate` 与各配置观察者在 `paused` 时会手动 `calculateDanmakuInFrame()` + `danmakuUpdateSubscription++`（[L697-L701](https://github.com/open-ani/animeko/blob/main/danmaku/ui/src/commonMain/kotlin/DanmakuHostState.kt)、L430-L432、L319-L325）。

### 5.11 绘制与性能优化清单（逐条）

| 优化 | 做法 | 位置 |
|---|---|---|
| **每条弹幕一张位图缓存** | 首次绘制时把"描边+实心字"渲染成 `ImageBitmap` 缓存到 `StyledDanmaku.imageBitmap`，之后只 `drawImage` | `StyledDanmaku.kt` L72-L82 |
| **位置是帧时间纯函数** | 不做逐帧积分 → 无累积误差，重新放置无需反推 | `FloatingDanmakuTrack.kt` L304-L306 |
| **文本测量缓存** | `rememberTextMeasurer(3000)`（3000 条测量结果 LRU）；轨道样板测量单独用 `rememberTextMeasurer(1)` | `DanmakuHost.kt` L98-L99 |
| **二分插入 + 只比较邻居** | 放置时 `binarySearch` 找插入点，只检查前后各一条 + 撞车判定 O(1)；fast path 先比最后一条 | `FloatingDanmakuTrack.kt` L206-L235 |
| **迭代器零拷贝** | `DanmakuCollectionIterator` 直接遍历各轨道内部 list，不创建副本（注释明确说明为性能考虑） | `DanmakuCollectionIterator.kt`、`FloatingDanmakuTrack.kt` L244-L262 |
| **惰性算 y** | `y` 只在第一帧算一次（`if (it.y.isNaN())`），之后复用 | `DanmakuHostState.kt` L478-L490 |
| **重绘订阅闸门** | Canvas 只订阅 `danmakuUpdateSubscription`（每帧 +1），不订阅每个弹幕状态 | `DanmakuHost.kt` L130 |
| **裁剪** | `Modifier.clipToBounds()`；已出屏弹幕由 1Hz 的 `tick()` 移除 | `DanmakuHost.kt` L127 |
| **整体透明度** | `Modifier.alpha(state.canvasAlpha)` 一次作用于整层，而非逐条设置 alpha | `DanmakuHost.kt` L128 |
| **放置失败即丢弃** | 播放流用 `trySend`，绝不排队堆积 | `DanmakuHostState.kt` L516-L519 |
| **速度变化不重排** | `updateSpeed` 重新锚定，位置连续 | `FloatingDanmakuTrack.kt` L318-L327 |
| **字体/密度变化增量重排** | `repopulatePresentDanmaku()` 保留原锚点与速度系数重新放置，位置零跳变 | `DanmakuHostState.kt` L412-L433 |
| **帧时间平滑** | PLL 抑制桌面端帧时间抖动导致的顿挫 | `FrameTimeSmoother.kt` |
| **没有做的优化** | ❌ 没有时间窗口分块绘制；❌ 没有把整屏烘焙成 layer；❌ 没有 GPU 实例化；❌ 没有多线程绘制 | — |

### 5.12 关键常量总表（调参起点）

| 常量 | 值 | 出处 |
|---|---|---|
| 滚动基准速度 `speed` | **88 dp/s** | `DanmakuConfig.kt` L44 |
| 速度可调范围 | 0.2×–3× | `DanmakuConfigRanges.SpeedScale` |
| 最小间隔 `safeSeparation` | **36 dp**（桌面端可在 36–720dp 调，移动端 36–240dp） | `DanmakuConfig.kt` L48、`DanmakuConfigRanges.kt` L26 |
| 显示区域 `displayArea` | 默认 **0.25**（1/4 屏），可选 0 / 1/8 / 1/6 / 1/4 / 1/2 / 3/4 / 1 | `DanmakuConfig.kt` L54、`EpisodeVideoSettings.kt` L356-L364 |
| 字号 | 默认 **18 sp**，可调 0.5×–3× | `DanmakuStyle.fontSize`、`FontSizeScale` |
| 字重 | 默认 **W600**，可调 100–900 | `DanmakuStyle.fontWeight` |
| 透明度 | 默认 **0.8**，范围 0–1 | `DanmakuStyle.alpha` |
| 描边颜色/宽度 | 黑 / **4f**（可调 0–2×） | `DanmakuStyle.strokeColor/strokeWidth` |
| 阴影 | 默认 `null`（不画） | `DanmakuStyle.shadow` |
| 长弹幕加速倍率 `speedMultiplier` | **1.14** | `DanmakuTrackProperties.kt` L29 |
| 速度随机抖动 | **±0.0875**（±8.75%） | `FloatingDanmakuTrack.kt` L43 |
| 轨道垂直内边距 | **1**（`verticalPadding*2` = 2dp） | `DanmakuTrackProperties.kt` L24 |
| 固定弹幕显示时长 | **5000 ms**（不可配置） | `DanmakuTrackProperties.kt` L34 |
| 帧时间初值 | **20 s** | `DanmakuTrackProperties.kt` L38 |
| 装填回溯距离 | **20 s** | `DanmakuCollection.kt` L135 |
| 跳转判定阈值 | **3 s × 播放倍速**（下限 3s） | `DanmakuCollection.kt` L134, L317 |
| 连续播放判定阈值 | **10 000 ms** | `DanmakuHostState.kt` L839 |
| 会话算法心跳 | **50 ms**（20 Hz） | `DanmakuCollection.kt` L189 |
| 清理心跳 | **1000 ms** | `DanmakuHost.kt` L114 |
| 时间轴偏移范围 | **±30 000 ms**（每源独立） | `EpisodeDetails.kt` L689（`val sliderRange = -30_000f..30_000f`）、`TvEpisodeViewModel.kt` L674 |
| 单源请求超时 | 60 s（DanDanPlay 连接/请求/socket 全 60s） | `DanmakuRepository.kt` L214、`DandanplayClient.kt` L202-L208 |

---

## 6. Q5 交互功能

### 6.1 功能对照表（逐条回答）

| # | 功能 | 是否有 | 实现要点 / 位置 |
|---|---|---|---|
| 1 | **全局显示/隐藏** | ✅ | `danmaku_enabled`（默认 true）；UI 上用 `AniAnimatedVisibility(danmakuEnabled)` 包住整个弹幕层（**卸载而非隐藏**）。注意：**加载与缓存不受它影响**。[`EpisodeVideo.kt` L345-L352](https://github.com/open-ani/animeko/blob/main/app/shared/src/commonMain/kotlin/ui/subject/episode/EpisodeVideo.kt) |
| 2 | **透明度** | ✅ | `style.alpha` 0–1 → `Modifier.alpha(canvasAlpha)` 作用于整层 |
| 3 | **字号** | ✅ | `style.fontSize`，0.5×–3× 相对 18sp；改动会触发轨道高度重算 + 全屏重排（保留锚点不跳变） |
| 4 | **速度** | ✅ | `config.speed` dp/s，0.2×–3×；改动调用 `updateTrackStaticProperties` 重新锚定，位置连续 |
| 5 | **屏蔽类型（滚动/顶部/底部）** | ✅ | `enableFloating` / `enableTop` / `enableBottom` 三个开关，**通过把对应轨道数设为 0 实现**（不是过滤数据） |
| 6 | **彩色/白色** | ✅ | `enableColor`，关闭后所有弹幕画成白色 |
| 7 | **关键字屏蔽** | ❌ **没有**独立的关键字过滤 | 只能用正则实现（`屏蔽词` 等价于正则字面量） |
| 8 | **正则屏蔽** | ✅ | `DanmakuRegexFilter {id, name, regex, enabled}` + 全局开关 `enableRegexFilter`。语义是**黑名单**：`regex.find(text) != null` 就丢弃；大小写不敏感（`RegexOption.IGNORE_CASE`）。支持导入/导出 JSON |
| 9 | **屏蔽发送者** | ❌ **未实现** | `senderId` 只用于两处：① 判定 `isSelf`（自己发的加下划线）；② DanDanPlay 来源分类。全仓库无按 sender 过滤的代码 |
| 10 | **屏蔽顶部/底部** | ✅ | 同 #5 |
| 11 | **弹幕时间轴偏移 ±秒** | ✅ | **按源独立**：`DanmakuOriginConfig { enabled, shiftMillis }`，弹幕 `playTimeMillis + shiftMillis`；UI 滑块 **±30 000 ms**，TV 端 ±按钮（`coerceIn(-30_000, 30_000)`，步进见 `TvEpisodeViewModel.kt` L672-L674）。改动会重建 session → 增量 repopulate |
| 12 | **发弹幕** | ✅ | 只能发到 **Animeko 自己的公益服务器**（`POST /v1/danmaku/{episodeId}`），**必须登录**（Bearer JWT）。见 6.3 |
| 13 | **点赞** | ❌ 未实现 |
| 14 | **举报** | ❌ 未实现（README 明确写"并考虑未来增加举报和屏蔽功能"）。⚠️ 注意 `/v2/comments/reports` 是**剧集评论**的举报，不是弹幕 |
| 15 | **弹幕列表** | ✅ | 位置：剧集详情侧边栏/底部弹层；列出全部弹幕（文本 + `m:ss` 时间 + 源图标），自己的弹幕高亮。顶部是**弹幕源 Chip 组**（开关 / 重新匹配 / 时间轴偏移）。⚠️ **列表项不可点击，没有"点击跳转到该时间"** |
| 16 | **拖动进度条时的弹幕预览** | ❌ 未找到 | 进度条上没有弹幕预览/缩略图；拖动时弹幕只是冻结（因为播放器 paused）然后 repopulate |
| 17 | **长按弹幕查看详情/复制/举报** | ❌ 未找到 | `DanmakuHost` 是纯 Canvas，**完全没有 pointerInput**；手势层是独立的 `gestureHost`。仓库里所有 `combinedClickable` 都用在剧集列表/卡片上 |
| 18 | **弹幕密度（同屏密度）** | ✅ | 参数是 `safeSeparation`，UI 映射成 0–10 的"密度"档（密集/适中/稀疏）。**增大间隔=降低密度** |
| 19 | **显示区域（半屏/全屏等）** | ✅ | `displayArea` |
| 20 | **调试模式** | ✅ | `isDebug`：在弹幕文本后追加 `(分:秒)`，并叠加一层统计信息（宿主尺寸、轨道数、帧时间、在屏弹幕数、每轨道状态） |
| 21 | **手动重新匹配** | ✅ | 见 3.6 |
| 22 | **单源开关** | ✅ | 弹幕源 Chip 点击切换 |
| 23 | **发送样式选择（颜色/位置）** | ✅ | 11 个预设色 + 自定义色；位置 TOP/NORMAL/BOTTOM。默认白色 + 滚动。持久化在 `danmakuSettings.sendColor/sendLocation` |

### 6.2 正则过滤的完整实现（可直接照抄）

```kotlin
// 数据模型
@Serializable data class DanmakuRegexFilter(val id: String, val name: String = "", val regex: String = "", val enabled: Boolean = true)

// 取出生效的规则（danmaku/api）
// app/shared/app-data/src/commonMain/kotlin/domain/settings/GetDanmakuRegexFilterListFlowUseCase.kt L40-L52
combine(settingsRepository.danmakuFilterConfig.flow, danmakuRegexFilterRepository.flow) { config, list ->
    if (!config.enableRegexFilter) emptyList() else list.filter { it.enabled }.map { it.regex }
}

// 应用过滤（danmaku/api/src/commonMain/kotlin/DanmakuCollection.kt L99-L120）
fun filterList(list: List<DanmakuInfo>, danmakuRegexFilterList: List<String>): List<DanmakuInfo> {
    if (danmakuRegexFilterList.isEmpty()) return list
    val regexFilters = danmakuRegexFilterList.map { Regex(it, option = RegexOption.IGNORE_CASE) }  // 预编译
    return list.filter { danmaku -> regexFilters.none { it.find(danmaku.text) != null } }
}
```
- 存储在 DataStore 文件 `danmakuFilter`（JSON 数组），有 export/import（`DanmakuRegexFilterRepository.export()/import()`）。
- 规则变更时 `TimeBasedDanmakuSession` 的 `select` 分支会**用新规则重新过滤整份列表并 `requestRepopulate()`**（[L151-L159](https://github.com/open-ani/animeko/blob/main/danmaku/api/src/commonMain/kotlin/DanmakuCollection.kt)）。
- ⚠️ 正则非法会抛异常（`Regex(it)` 未做 try/catch 保护）—— 实现时建议加保护。
- **注意作用域**：正则过滤发生在 **session 层（渲染前）**，而"源开关 + shift"发生在**更上游的数据层**（`EpisodeDanmakuLoader.createDanmakuCollection`）。顺序是：取数 → 按源 enabled 过滤 → 加 shiftMillis → 文本清洗 → 正则过滤 → 排序 → 输出事件。

### 6.3 发送弹幕的完整流程（含登录要求）

```
用户在播放器输入框输入 → 选颜色/位置（可选）
  │
  ├─ 输入框获得焦点时：若 videoScaffoldConfig.pauseVideoOnEditDanmaku 且正在播放 → 暂停（失焦后恢复）
  │     EpisodeVideo.kt / DanmakuEditor.kt L124-L138
  │
  ├─ 点发送 → DanmakuContent(
  │        playTimeMillis = player.currentPositionMillis.value,   // ← 当前播放位置
  │        text = text, color = style.color, location = style.location)
  │     DanmakuEditor.kt L110-L123
  │
  ├─ DanmakuEditorState.post(content)：MonoTasker 串行化，发送中 isSending=true
  │     失败 → 把文本塞回输入框（不丢用户输入）；成功 → 清空输入框
  │     DanmakuEditorState.kt L49-L69
  │
  ├─ EpisodeViewModel.postDanmaku → DanmakuRepository.post → AniDanmakuSender.send
  │     └─ sendLock(Mutex) 串行 → POST /v1/danmaku/{episodeId}  (Bearer JWT，需登录)
  │        body: {"danmakuInfo": {"playTime":ms, "color":int, "text":str, "location":"TOP|NORMAL|BOTTOM"}}
  │     AniDanmakuSender.kt L48-L88
  │
  └─ 成功后本地伪造一条 DanmakuInfo 立即上屏（乐观更新，不等服务端回包）：
        id       = "self" + Random.nextInt()
        serviceId= DanmakuServiceId.Animeko
        senderId = selfId（未登录则空串）
     → onPostSuccess → danmakuHostState.send(DanmakuPresentation(danmaku, isSelf = true))
        （用 send 而不是 trySend：保证自己的弹幕一定发出去）
        → 因为 isSelf=true，渲染时会加下划线
     EpisodePage.kt L340-L353、EpisodeViewModel.kt L1043-L1047
```

**登录要求**：`POST /v1/danmaku/{episodeId}` 的 `requiresAuthentication = true`（生成的 API 定义），token 来自登录会话（Bangumi OAuth → Animeko 服务器 → `aniAccessToken`）。
**发到哪个源**：**只有 Animeko 公益服务器**。没有"发送到 B 站/弹弹"的选项。
**异常类型**（照抄这些语义）：`AuthorizationFailureException` / `RequestFailedException` / `NetworkErrorException`，统一包成 `RepositoryException`（`AniDanmakuSender.kt` L30-L37、`DanmakuRepository.kt` L174-L183）。

---

## 7. Q6 "弹幕云过滤"到底是什么

**结论：「弹幕云过滤」是一个"官方宣称但尚未实现"的功能，当前代码里完全不存在任何服务端共享屏蔽词/规则同步/上报机制。**

证据链：

1. **代码里没有**：全仓库（含 Kotlin / Kt / XML / JSON / Gradle）grep `云过滤`、`云屏蔽`、`cloudFilter`、`CloudFilter`、`cloud_filter` → **0 命中**。弹幕过滤只有本地正则那一条路径（第 6.2 节）。
2. **官方对外口径是"未来功能"**：Animeko 官网首页功能卡片原文（`open-ani/ani-website` 仓库 `src/components/home/Features.astro` L45-L51）：
   > **弹幕及弹幕云过滤**
   > Animeko 从弹弹play, 其他弹幕网站, 以及 Animeko 弹幕服务获取弹幕，你也可以发送弹幕到 Animeko 的公益弹幕服务器，**AI 团队将逐渐推进弹幕云过滤功能**。
   
   （"将逐渐推进"= 尚未落地。GitHub 仓库描述里的"弹幕云过滤"就是这句宣传语的缩写。）
3. **服务端 API 里没有**：把 Animeko OpenAPI 生成客户端里的**全部 endpoint** 列出来，与屏蔽/过滤相关的只有 `/v2/pfrule`（`PeerFilterRuleAniApi`），而它是 **BitTorrent peer 过滤规则**，数据模型是 `AniPeerFilterRule`（配合 `PeerFilterSubscriptionRepository` 使用），**与弹幕无关**。
   全部 endpoint 清单见附录 A。
4. **弹幕服务本身可能有服务端过滤**，但**客户端看不到、也无法配置** —— 公开的 swagger 需要鉴权（401），无法核实。见第 10 节。
5. 与"云"沾边的、Animeko 唯一做过的事是**过滤规则的导出/导入 JSON**（`DanmakuRegexFilterRepository.export()/import()`）—— 这是用户手动分享规则的途径，**不是云同步**（没有上传/下载端点）。

**对实现者的建议**（如果你要做"云过滤"）：
- 客户端侧最现实的设计是：屏蔽词表放服务端 → `GET /v1/danmaku/filters`（带 ETag/版本号）→ 本地缓存 → 与本地正则合并去重；**不要上报用户本地规则**（隐私）。
- Animeko 现有代码给出的、可复用的"隐私安全"边界参考：`AniDanmakuProvider` 读弹幕**不鉴权、不带用户标识**；只有**发送**和**评论**才带 JWT；`senderId` 只用于判断"是不是我自己"。
- 若要做举报/屏蔽发送者，注意服务端目前**把每条弹幕绑定 Bangumi 用户名**（README：*"每条弹幕都会以 Bangumi 用户名绑定以防滥用"*），即 `senderId` 对 Animeko 源而言是**可识别到用户**的 —— 这是隐私上必须谨慎处理的一点。

---

## 8. Q7 离线 / 缓存与多源合并

### 8.1 多源**是否合并显示** —— 是，全部叠加同屏

```kotlin
// EpisodeDanmakuLoader.kt L182-L208  createDanmakuCollection
TimeBasedDanmakuSession.create(
    danmakuListFlow.map { results ->
        results?.flatMap { result ->
            val config = config[result.matchInfo.serviceId] ?: DanmakuOriginConfig.Default
            if (!config.enabled) return@flatMap emptyList()          // 源被关 → 不贡献弹幕
            result.list.mapNotNull { danmaku ->
                val newText = sanitizeDanmakuText(danmaku.text) ?: return@mapNotNull null
                danmaku.copy(content = danmaku.content.copy(
                    playTimeMillis = danmaku.playTimeMillis + config.shiftMillis,   // 按源平移
                    text = newText))
            }
        } ?: emptyList()
    },
)
```
- 所有源的弹幕**拍平进同一个列表后按时间排序**，一起进轨道避让，**没有"每源独占轨道"或去重**。
- 同一条弹幕在不同源里出现两次就会**显示两次**（**没有做跨源去重**）。
- 每个源可以**单独开关**和**单独时间轴偏移**。
- **本地缓存与远程的合并策略**（`DanmakuLoaderImpl.originalFetchResultFlow`，[L86-L123](https://github.com/open-ani/animeko/blob/main/app/shared/app-data/src/commonMain/kotlin/domain/danmaku/DanmakuLoader.kt)）：
  ```
  并发发起：local 查询（ATOMIC 启动） + 全部 remote 查询
  ① 本地有可显示弹幕（any list.isNotEmpty）→ 先 emit 本地结果，loading = Success   ← 秒开
  ② 远程返回后：若 remote.canReplaceLocalCache()（任一条 list 非空 或 method != NoMatch）
              或 本地没有可显示弹幕 → emit 远程结果
  ```
  即 **"本地先上屏，远程回来替换或补充"**。

### 8.2 缓存策略（什么时候写缓存）

```kotlin
// DanmakuRepository.kt L148-L172
enum class DanmakuCacheStrategy {
    DON_NOT_CACHE,                          // 从不缓存
    CACHE_ON_MEDIA_CACHE,                   // 该集有 MediaCache（已下载/缓存）时缓存
    CACHE_ON_COLLECTION_DOING_MEDIA_PLAY,   // 有 MediaCache 或 该条目在"在看"收藏里
}
private suspend fun shouldCache(subjectId, episodeId): Boolean {
    val strategy = settingsRepository.mediaCacheSettings.flow.first().danmakuCacheStrategy
    val hasMediaCache = getMediaCacheUseCase(subjectId, episodeId).isNotEmpty()
    val isCollectionDoing = ...collectionType == UnifiedCollectionType.DOING
    return when (strategy) {
        DON_NOT_CACHE -> false
        CACHE_ON_MEDIA_CACHE -> hasMediaCache
        CACHE_ON_COLLECTION_DOING_MEDIA_PLAY -> hasMediaCache || isCollectionDoing
    }
}
```
- **写入时机**：结果累积完成后 **延迟 3 秒**再落库（`delay(3.seconds)` + `SingleTaskExecutor` 串行化），避免拖动/快速切集时反复写（`DanmakuLoader.kt` L143-L150）。
- **写入内容**：所有 `providerId != local` 的源的结果，逐条 upsert（`DanmakuRepository.saveToLocal` L132-L146）；源码里留了 TODO：*"db 里可能有已经被删除的弹幕, 可能需要清理一下"*（**没有做失效清理**）。
- **删除**：`deleteDanmakuIfDontNeeded`（策略不再要求缓存时删）；以及 Room 外键 `CASCADE`（删收藏/剧集连带删弹幕）。
- **离线可用性**：**离线可播放已缓存的弹幕**（`LocalDanmakuProvider` 直读 Room）；远程失败会 `catch` 成 `NoMatch` 空结果，**不会阻塞 UI**。
- **失败重试**：单源 `withTimeout(60s)` + `retry(1)`，最终兜底 emit 空结果（`DanmakuFetcher` L209-L248）。**没有指数退避、没有后台定时重试**。

### 8.3 加载状态机

```kotlin
// app/shared/app-data/src/commonMain/kotlin/domain/danmaku/DanmakuLoadingState.kt
sealed class DanmakuLoadingState { Idle, Loading, Success, Failed(cause) }
```
`Idle`（无请求）→ `Loading` → `Success` / `Failed`。切集时先 `emit(null)` 并回到 `Loading`。

---

## 9. Q8 可移植性结论：搬到 Python + PySide6 + 自绘

### 9.0 先说最重要的一条：License

Animeko 是 **AGPL-3.0**（`LICENSE.txt`，每个源文件头部都写着"此源代码的使用受 GNU AFFERO GENERAL PUBLIC LICENSE version 3 许可证的约束"）。
**直接复制代码进你的项目会传染 AGPL**。本文给的是"算法与数据结构的复述 + 伪代码"，你可以据此独立实现（算法本身（Levenshtein、轨道避让、PLL）不受版权保护）；**如果要逐行搬运，必须接受 AGPL 或获得授权**。

### 9.1 分模块可移植性总表

| 模块 | 可移植性 | 说明 |
|---|---|---|
| **数据源接入** | 🟢 **完全可搬**（协议层与语言无关） | DanDanPlay 签名 = `base64(sha256(appId+ts+path+secret))`，Python 一行搞定；Animeko 服务只需 `requests` + Bearer。**唯一门槛：DanDanPlay AppId/AppSecret 要自己申请** |
| **匹配算法** | 🟢 **完全可搬** | 纯字符串 + HTTP 编排，无平台依赖。建议**额外实现 16MB MD5 hash 匹配**（Animeko 没做） |
| **解析** | 🟢 **完全可搬** | `p` 串 split + 枚举映射，10 行 |
| **会话/装填算法** | 🟢 **完全可搬**（把 Flow 换成线程安全的队列/回调） | 逻辑是纯数学：`distanceX` 纯函数、二分插入、撞车判据。Kotlin `Flow` → Python `queue.Queue` / 回调 + 定时器 |
| **轨道/避让/速度模型** | 🟢 **完全可搬** | 全是浮点算术，照抄公式即可 |
| **渲染绘制** | 🟡 **算法可搬，API 要重写** | Compose `TextMeasurer` / `ImageBitmap` / `DrawScope` → Qt `QFontMetricsF` / `QPixmap` / `QPainter`。**架构（每条弹幕预渲染成 pixmap + 每帧 drawPixmap）可以 1:1 照搬** |
| **帧时间平滑** | 🟢 **应该搬** | Qt 的 `QTimer` 同样有抖动，PLL 逻辑与平台无关 |
| **配置/持久化** | 🟡 思路可搬 | `DanmakuConfig` 字段名可直接当你的 JSON schema；Room → sqlite3 |
| **发送弹幕** | 🟡 协议可搬，**但需要 Animeko 账号体系** | 对方要求 Bearer JWT（Bangumi OAuth 经其服务器）。你接不了别人的账号体系，**建议自建弹幕服务或只做只读聚合** |
| **云过滤** | ⚫ 对方没实现，无可搬 | — |

### 9.2 分模块落地建议

#### （A）数据源接入

```python
# 伪代码：DanDanPlay 客户端
import base64, hashlib, time, requests

class DandanplayClient:
    BASE = "https://api.dandanplay.net"
    def __init__(self, app_id, app_secret, timeout=60):
        self.app_id, self.app_secret, self.timeout = app_id, app_secret, timeout

    def _headers(self, path):                       # path 不含 query！
        ts = int(time.time())
        sig = base64.b64encode(
            hashlib.sha256(f"{self.app_id}{ts}{path}{self.app_secret}".encode()).digest()
        ).decode()
        return {"X-AppId": self.app_id, "X-Timestamp": str(ts), "X-Signature": sig}

    def comment(self, episode_id, ch_convert=0, with_related=True):
        path = f"/api/v2/comment/{episode_id}"
        r = requests.get(self.BASE + path,
                         params={"chConvert": ch_convert, "withRelated": str(with_related).lower()},
                         headers=self._headers(path), timeout=(60, 60))
        r.raise_for_status()
        return r.json()["comments"]                 # [{cid, p, m}]

    def match(self, filename, file_hash=None, file_size=None, duration_s=0):
        path = "/api/v2/match"
        body = {"fileName": filename, "fileSize": file_size,
                "videoDuration": duration_s,
                "matchMode": "hashAndFileName" if file_hash else "fileNameOnly"}
        if file_hash: body["fileHash"] = file_hash
        r = requests.post(self.BASE + path, json=body, headers=self._headers(path), timeout=(60, 60))
        return r.json()                              # {isMatched, matches:[{episodeId, animeTitle, ..., shift}]}
```
**要点/坑**：
- 签名用 **path 不含 query**；`X-Timestamp` 是**秒**；时间必须准（NTP 同步），否则 401。
- 用 `params=` 让 requests 编码 query，但**签名要用裸 path**。
- `fileName` 官方要求**不含扩展名与目录**（Animeko 直接传了带扩展名的原始名，属于将错就错）。你若做 hash 匹配，记得按规范裁剪。
- 超时给足（Animeko 用 60s，注释说弹弹很慢）。
- 配额：分应用分层限额，注意降级（失败就少一个源，不要卡 UI）。

**16MB hash（Animeko 没做，你要做）**：
```python
def dandanplay_file_hash(path, chunk=16 * 1024 * 1024):
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read(chunk))        # 官方规范：前 16MB（16*1024*1024 Byte）的 MD5，hex 小写
    return h.hexdigest()
```
> ⚠️ 对于**只在线的流媒体**（拿不到本地文件）就退回 `fileNameOnly`；对**本地/已下载文件**用 `hashAndFileName`。
> 另注：官方 schema 明确只哈希"**文件前 16MB**"，**不是"首尾各 16MB"**（网上常见的说法有误，以本报告的官方 schema 为准）。

#### （B）匹配

按 3.2 的伪代码实现；把 `DanmakuMatcher` 写成 Python：
```python
def levenshtein(a: str, b: str) -> int: ...          # 标准滚动数组 DP，O(len(a)*len(b))
def most_relevant(cands, target_subject, target_episode):
    return min(cands, key=lambda c: levenshtein(c.subject_name, target_subject)
                                   + levenshtein(c.episode_name, target_episode), default=None)
```
**建议增强（Animeko 的弱点）**：
1. 加**相似度阈值**（例如归一化距离 > 0.5 就判定为"无匹配"并要求手动选），Animeko 无阈值，会莫名其妙选中一个很差的候选。
2. 加**缓存**：把 `(文件名 hash / Bangumi episodeId) → dandanplay episodeId` 的映射持久化，第二次播放零延迟。
3. 用 **16MB hash 优先**（`isMatched=true` 时直接用唯一结果）。

#### （C）解析 & 数据模型

```python
from dataclasses import dataclass
from enum import Enum

class Location(Enum): TOP = "TOP"; BOTTOM = "BOTTOM"; NORMAL = "NORMAL"

@dataclass(slots=True)
class Danmaku:
    id: str; service_id: str; sender_id: str
    play_time_ms: int; color: int; text: str; location: Location

MODE_MAP = {1: Location.NORMAL, 4: Location.BOTTOM, 5: Location.TOP}
def parse_p(cid: int, p: str, m: str, service_id: str):
    parts = p.split(",")
    if len(parts) < 4: return None
    try:
        t = float(parts[0]); mode = int(parts[1]); color = int(parts[2])
    except ValueError: return None
    loc = MODE_MAP.get(mode)
    if loc is None: return None                       # 未知模式丢弃
    return Danmaku(str(cid), service_id, parts[3], int(t * 1000), color, m, loc)
```
用 `@dataclass(slots=True)` 或 `__slots__` —— 一集 8000 条弹幕，省内存很重要。

#### （D）渲染（QPainter / OpenGL）

**可以 1:1 照搬的三件事**：
1. **轨道避让数学**：`left()/right()/isGone()/willClash()/isNonOverlapping()` 全部是浮点运算，直接翻译。
2. **位置 = 帧时间纯函数**：`distance_x = (frame_nanos - place_frame_nanos) / 1e9 * speed_px_per_sec`。
3. **每条弹幕预渲染成 pixmap 缓存**：Qt 里等价于：
   ```python
   fm = QFontMetricsF(font)
   rect = fm.boundingRect(text)
   margin = int(rect.height() / 2)                    # 对应 extraMargin = height >> 1
   pm = QPixmap(int(rect.width()) + margin*2, int(rect.height()) + margin*2)
   pm.fill(Qt.transparent)
   p = QPainter(pm)
   p.translate(margin, margin)
   path = QPainterPath(); path.addText(0, fm.ascent(), font, text)
   p.setRenderHint(QPainter.Antialiasing)
   p.strokePath(path, QPen(stroke_color, stroke_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))  # 描边
   p.fillPath(path, QBrush(text_color))                                                          # 实心
   p.end()
   # 之后每帧： painter.drawPixmap(int(x - margin), int(y - margin), pm)
   ```
   `QPainterPath.addText + strokePath + fillPath` 正是 Compose 里"两遍绘制"的等价物。
4. **帧循环**：用 `QTimer`（16ms）或 `QOpenGLWidget` 的 `frameSwapped` 信号；`QElapsedTimer.nsecsElapsed()` 取真实时间，然后**把 Animeko 的 `FrameTimeSmoother` 原样搬过来**（那段 PLL 很重要，Qt 的 `QTimer` 抖动同样明显）。
5. **过渡/重排**：`repopulate` 的"帧时间轴映射"逻辑照搬，这是快进不闪屏的关键。

**必须替换的 Compose/Android 特有 API**：
| Compose/Android | Python/PySide6 替代 |
|---|---|
| `TextMeasurer` / `rememberTextMeasurer(3000)` | `QFontMetricsF.boundingRect()` + 自己做 `dict` LRU 缓存（key = (text, font_size, weight)） |
| `TextLayoutResult` / `TextPainter` | `QPainterPath.addText` / `QStaticText` |
| `ImageBitmap` / Skia `Surface` | `QPixmap` / `QImage` |
| `DrawScope.drawImage` | `QPainter.drawPixmap` |
| `Density.dp.toPx()` | `QGuiApplication.primaryScreen().devicePixelRatio()` + 自定 `dp = pt`（**注意 HiDPI：Qt 逻辑坐标已含 DPR，别重复乘**） |
| `withFrameNanos` | `QTimer` / `QOpenGLWidget.frameSwapped` / `QWindow.requestUpdate` |
| `Dispatchers.Main.immediate` | Qt 主线程 + `QMetaObject.invokeMethod(..., Qt.QueuedConnection)` |
| `Modifier.alpha()` 整层透明 | `painter.setOpacity(alpha)` 或 QGraphicsOpacityEffect |
| `Modifier.clipToBounds()` | `painter.setClipRect(...)` |
| `Color(0xFF000000L or rgb)` | `QColor.fromRgb(rgb)` |
| `Flow`（进度、配置变更） | `QTimer` 轮询播放位置 + `signal/slot` |
| `kotlin.concurrent.Volatile` / `@JvmField` 跨线程共享 | 全部收敛到 UI 线程，或 `threading.Lock` |

**最容易踩的坑（按踩坑概率排序）**：
1. **dp ↔ px 换算重复相乘**：Animeko 的 `speed` 是 **dp/s**，需要乘 density 得到 px/s；Qt 里 `devicePixelRatio` 的处理范式不同，很容易漏乘或乘两次 → 弹幕速度看起来是"一半"或"两倍"。**建议先用"一条弹幕从右到左的总时长(秒)"做验收：时长 = `屏幕宽度_px / (speed_dp * density)`，例如 1920px 宽、88dp/s、density=1 → 21.8s。**
2. **描边被裁**：位图必须四周留 `height/2` 边距，否则黑边会被切掉（Animeko 在 issue #1838 上踩过，代码注释保留着）。
3. **HiDPI 下 pixmap 模糊**：`QPixmap` 要以 `devicePixelRatio` 设置，或按 `dpr` 放大后再绘制；Compose 里 `prepareToDraw()` 就是做类似的事。
4. **每弹幕一张 pixmap 的内存**：8000 条弹幕 × 平均 300×60 px × 4B ≈ 576MB —— **必须做 LRU 上限**（Animeko 靠"同一时刻屏幕上的弹幕数量有限"隐式限制了总量，但它其实也没做上限）。建议：pixmap 只在"即将上屏/在屏"时创建，出屏后回收；或按 `(text,color,size,weight)` 共享 pixmap（**更好的做法：相同文本共享一张**）。
5. **帧时间抖动**：不要用 `QTimer` 的 interval 常量做积分，用真实时间差 + PLL。
6. **暂停/seek 语义**：暂停必须连"帧时钟"一起停；seek 必须触发 repopulate 而不是让弹幕"飞过去"。
7. **正则性能与崩溃**：预编译正则、`ignorecase`、捕获非法正则异常（Animeko 没捕获）。
8. **多源合并没有去重**：如果你想要"同一句弹幕只显示一次"，需要自己加 `(text, 量化后的时间, mode)` 去重（Animeko 不做）。
9. **AGPL**：见 9.0。
10. **Animeko 服务的发送弹幕你接不了**（需要它的账号体系）；只读弹幕不需要鉴权，可以接。

#### （E）交互

| 功能 | Python 实现建议 |
|---|---|
| 显示/隐藏 | 布尔开关 + `widget.setVisible()`（**注意 Animeko 是卸载组件；你若只 setVisible，加载/缓存仍会跑，这更接近"用户预期"**） |
| 透明度/字号/速度/密度/显示区域 | 与 `DanmakuConfig` 字段一一对应，存 JSON；字号/密度变更走"增量重排"（保留锚点） |
| 顶部/底部/滚动开关 | 直接把对应轨道数设为 0（照抄 Animeko 的做法，最省事） |
| 正则屏蔽 | 规则列表（id/name/regex/enabled）+ 总开关 + 导入导出 JSON |
| **关键字屏蔽** | Animeko 没有：建议按"正则转义后的字面量"实现，并统一进同一个规则表 |
| **屏蔽发送者** | Animeko 没有：建议加 `blocked_senders: set[str]`，在过滤阶段丢弃 |
| 时间轴偏移 | 按源 `shift_ms`，滑块 ±30s；注意是**在过滤前**加到 `play_time_ms` 上 |
| 弹幕列表 | `QListView` + model；**建议做得比 Animeko 好：列表项点击 → seek 到该时间** |
| 发送弹幕 | 见（A）/6.3；若自建服务，注意前端要做乐观上屏 + 失败回填输入框 |
| 长按/右键查看 | Animeko 没有：Qt 里可做右键菜单（复制文本 / 屏蔽该发送者 / 举报） |

#### （F）缓存

```sql
-- 建议直接照搬 Animeko 的表结构（去掉 Room 特有的东西）
CREATE TABLE danmaku (
  id TEXT NOT NULL,
  subject_id INTEGER NOT NULL,
  episode_id INTEGER NOT NULL,
  service_id TEXT NOT NULL,             -- 真实源
  presentation_service_id TEXT NOT NULL,-- 外显源（同一份数据回放时按它分组）
  sender_id TEXT NOT NULL,
  content_play_time_ms INTEGER NOT NULL,
  content_color INTEGER NOT NULL,
  content_text TEXT NOT NULL,
  content_location TEXT NOT NULL,       -- TOP/BOTTOM/NORMAL
  PRIMARY KEY (id)
);
CREATE INDEX idx_danmaku_subject_episode ON danmaku(subject_id, episode_id);
```
- 缓存键：`(subject_id, episode_id)`（即 Bangumi 的 subject/episode id）—— **不依赖文件路径**，所以换片源、换清晰度都不用重新匹配弹幕。
- **建议补上 Animeko 缺的两件事**：① 加 `fetched_at` 字段做 TTL/失效刷新；② 重新拉取时**清理已删除的弹幕**（Animeko 源码里挂着 TODO）。

---

## 10. 没查到 / 不确定的点（如实清单）

以下都是我**没有找到证据**或**无法核实**的内容，请勿当成结论使用：

1. **`danmaku-cn.myani.org` / `danmaku-global.myani.org` 与 `api.animeko.org` 的关系，未找到显式绑定代码。**
   代码里两套域名并存：`AniBangumiSeverBaseUrls`（danmaku-cn / danmaku-global）只用于**设置页的连通性测试**（`GET {base}/status`），而弹幕 API 走 `MAGIC_ANI_SERVER` → 由 `ServerSelector` 重写成真实服务器。我没有找到"弹幕请求发往 danmaku-cn"的直接代码路径，也没有找到 `ServerSelector` 的服务器列表来源（可能是远端配置）。**不确定实际弹幕请求落在哪个域名上。**
2. **Animeko 弹幕服务的完整 OpenAPI 规范无法核实。**
   `https://api.animeko.org/openapi.json` → 401；`https://danmaku-cn.myani.org/swagger/index.html` → 401；`https://danmaku-global.myani.org/swagger/v1/swagger.json` → 404。因此 `/v1/danmaku/{episodeId}` 的**完整参数/错误码/限流策略**只有生成代码里那点信息；`maxCount` 默认 8000、`fromTime`/`toTime` 单位毫秒、`toTime` 负数不限制 —— 这些来自生成代码的 KDoc，未经服务端文档交叉验证。
3. **DanDanPlay 侧 hash 匹配的实际效果未知。** 我确认了官方规范是"前 16MB 的 MD5"，但**没有实际调用验证**（需要 AppId/AppSecret 和真实视频文件）。
4. **`/api/v2/match` 返回的 `shift`（弹幕偏移秒数）Animeko 是否使用 —— 未找到使用点。**
   `DandanplayEpisode` 模型里解析了 `shift: Double`（`MatchVideo.kt` L75），但 `fetchImpl` 里 `match.episodeId` 之后**没有把 `shift` 传给任何地方**，时间轴偏移完全靠用户手动调。**我判断它被忽略，但不能 100% 排除在其他路径使用**（grep `shift` 在 dandanplay 模块内只有模型定义那一处）。
5. **"云过滤"的服务端实现细节完全未知。** 官网只说"将逐渐推进"。弹幕服务器 swagger 需鉴权，无法确认它是否已经在服务端做了过滤/关键词拦截。**未找到任何证据。**
6. **没有找到弹幕的"点赞/举报/发送者屏蔽"的任何客户端或服务端代码。** README 只说"考虑未来增加"。
7. **`DanmakuRepopulationTest.kt` 与当前主代码不匹配。** 该测试用 `DanmakuInfo(id=..., serviceId="provider1", playTimeMillis=..., senderId=..., location=..., text=..., color=...)` 这种**平铺构造**，而主代码的 `DanmakuInfo` 是 `(id, serviceId, senderId, content)` 嵌套结构 —— 该测试文件看起来是**历史遗留、与主代码不同步**（也可能被构建排除）。我**没有**把它当作算法依据，报告中的 repopulate 语义全部来自 `DanmakuHostState.kt` 与 `DanmakuRepopulator` 的源码本身。
8. **没有实际运行过 Animeko**，所有结论来自静态阅读源码 + 官方协议文档。涉及"视觉表现"的描述（如"弹幕看起来速度是否合适"）无法验证。
9. **`DanmakuHost` 的 `durationMillis` 注释说"现在还不能自定义"**，但我没有全局搜索确认是否在 TV 端或桌面端有别的入口覆盖它；默认为 5000ms。
10. **"B 站直连"未找到。** 我未在仓库中发现任何对 `api.bilibili.com` / `api.gamer.com.tw` 的直接请求；B 站/巴哈弹幕全部经 DanDanPlay 聚合。如果 Animeko 有隐藏的直连实现，我只 grep 了域名而没穷尽所有 HTTP 调用点，不能 100% 排除。
11. **`DanmakuConfig.visibilitySafeArea` 未找到使用点。** `DanmakuTrackProperties.visibilitySafeArea` 默认 0，但我没找到读取它的代码；同样 `DanmakuStyle.shadow` 默认 null，UI 上没有暴露入口。
12. **Animeko 的 `speed` 单位在 UI 上的表述**：设置页注释写着"弹幕速度不会跟随视频倍速变化"，`DanmakuConfig` KDoc 写的是"Time for the Danmaku to move from the right edge to the left edge of the screen. Unit: dp/s" —— 这两句话**语义上矛盾**（前者说时长、后者说速度），实际实现是**速度（dp/s）**，KDoc 那句话是错的/过时描述。

---

## 附录 A：接口清单速查

### A.1 DanDanPlay（`https://api.dandanplay.net`，签名头 `X-AppId`/`X-Timestamp`/`X-Signature`）

| 方法 | 路径 | 关键参数 | 说明 |
|---|---|---|---|
| POST | `/api/v2/match` | body `{fileName, fileHash?, fileSize?, videoDuration?, matchMode?}` | 文件识别。`matchMode ∈ {hashAndFileName, fileNameOnly, hashOnly}`；返回 `{isMatched, matches:[{episodeId, animeId, animeTitle, episodeTitle, type, typeDescription, shift, imageUrl}]}` |
| GET | `/api/v2/comment/{episodeId}` | `from`(默认0)、`withRelated`(默认false，推荐true)、`chConvert`(0/1/2) | 取弹幕。**会 302 跳到加速服务**。返回 `{count, comments:[{cid, p, m}]}` |
| POST | `/api/v2/comment/{episodeId}/app` | body `{time, mode, color, comment, userName}` | 开放弹幕网络"应用发送弹幕"（Animeko 未使用；官方要求 `社区合作`/`商业授权` 层级才有完整额度） |
| GET | `/api/v2/search/anime` | `keyword` | 搜番剧（`?v2=true` 可切新版搜索引擎） |
| GET | `/api/v2/search/episodes` | `anime`、`episode` | 搜剧集 |
| GET | `/api/v2/bangumi/season/anime/{year}/{month}` | — | 季度番剧列表 |
| GET | `/api/v2/bangumi/{bangumiId}` | — | 番剧详情 + 剧集列表 |
| GET | `/api/v2/bangumi/bgmtv/{bgmtvSubjectId}` | — | **按 Bangumi.tv subject id 映射** |
| GET | `/api/v2/match/batch` | — | 批量匹配（Animeko 未使用） |

来源：`https://api.dandanplay.net/swagger/v2/swagger.json`（Swagger UI：`https://api.dandanplay.net/swagger`），Animeko 侧实现见 `DandanplayClient.kt`。

### A.2 Animeko 服务（`https://api.animeko.org`，生成自 `openapi.json`）

| 方法 | 路径 | 鉴权 | 参数 |
|---|---|---|---|
| GET | `/v1/danmaku/{episodeId}` | 无 | `maxCount`(默认8000)、`fromTime`(毫秒,默认0)、`toTime`(毫秒,默认-1=不限) |
| POST | `/v1/danmaku/{episodeId}` | **Bearer JWT (`auth-jwt`)** | body `{"danmakuInfo":{playTime,color,text,location}}` |
| GET | `/v2/pfrule` | Bearer | **BT peer 过滤规则，与弹幕无关** |

### A.3 Animeko OpenAPI 中存在的全部 endpoint（用于证明"没有弹幕过滤 API"）

`/v1/danmaku/{episodeId}`、`/v1/login/bangumi{,/oauth,/oauth/callback,/oauth/refresh,/oauth/token}`、`/v1/me`、`/v1/schedule/{airing,seasons,seasons/latest,season/{id},subjects}`、`/v1/subject-relations/{id}`、`/v1/subs/proxy`、`/v1/trends`、`/v1/updates/{incremental,incremental/details,latest}`、`/v2/bangumi/sync{,...}`、`/v2/characters/...`、`/v2/comments/reports`、`/v2/episodes/{id}/{auto-skip,comments,skip-history}`、`/v2/home/recommendations`、`/v2/persons/...`、`/v2/pfrule`、`/v2/playback-histories/sync`、`/v2/subjects/...`
**其中没有任何 danmaku-filter / block-word / shared-filter 类接口。**

---

## 附录 B：源码文件索引

### 弹幕核心（`danmaku/`）

| 文件 | 内容 |
|---|---|
| `danmaku/api/src/commonMain/kotlin/DanmakuInfo.kt` | `DanmakuInfo`/`DanmakuContent`/`DanmakuLocation`/`DanmakuServiceId` |
| `danmaku/api/src/commonMain/kotlin/DanmakuCollection.kt` | `DanmakuCollection`/`DanmakuEvent`/`TimeBasedDanmakuSession`/`DanmakuSessionAlgorithm` |
| `danmaku/api/src/commonMain/kotlin/DanmakuSession.kt` | `DanmakuSession` 接口 |
| `danmaku/api/src/commonMain/kotlin/DanmakuSanitizer.kt` | 换行清洗 |
| `danmaku/api/src/commonMain/kotlin/provider/DanmakuProvider.kt` | 源接口 + `DanmakuFetchRequest` |
| `danmaku/api/src/commonMain/kotlin/provider/SimpleDanmakuProvider.kt` | `DanmakuFetchResult`/`DanmakuMatchInfo`/`DanmakuMatchMethod` |
| `danmaku/api/src/commonMain/kotlin/provider/MatchingDanmakuProvider.kt` | 手动匹配接口 |
| `danmaku/api/src/commonMain/kotlin/provider/DanmakuMatcher.kt` | Levenshtein 匹配器 |
| `danmaku/api/src/commonMain/kotlin/DanmakuProviderFactory.kt` | `DanmakuProviderConfig`（含 proxy/UA/appId/secret） |
| `danmaku/dandanplay/src/commonMain/kotlin/DandanplayClient.kt` | HTTP + 签名 |
| `danmaku/dandanplay/src/commonMain/kotlin/DandanplayDanmakuProvider.kt` | 匹配流程编排 |
| `danmaku/dandanplay/src/commonMain/kotlin/data/MatchVideo.kt` | `p` 串解析 + match 响应模型 |
| `danmaku/dandanplay/src/commonMain/kotlin/data/MatchEpisode.kt` | 搜索/番剧响应模型 |
| `danmaku/dandanplay/src/commonMain/kotlin/data/DandanplayBangumi.kt` | 番剧详情模型 |
| `danmaku/dandanplay/dandanplay.http` | 手写接口样例（含官方 hashAndFileName 示例） |
| `danmaku/ui/src/commonMain/kotlin/DanmakuHost.kt` | Compose 组件 + 帧循环 + 绘制 |
| `danmaku/ui/src/commonMain/kotlin/DanmakuHostState.kt` | 状态中枢（轨道、装填、发送） |
| `danmaku/ui/src/commonMain/kotlin/FloatingDanmakuTrack.kt` | 滚动轨道 + 避让算法 |
| `danmaku/ui/src/commonMain/kotlin/FixedDanmakuTrack.kt` | 顶/底轨道 |
| `danmaku/ui/src/commonMain/kotlin/DanmakuTrack.kt` | 轨道接口 |
| `danmaku/ui/src/commonMain/kotlin/DanmakuTrackProperties.kt` | 轨道静态参数 |
| `danmaku/ui/src/commonMain/kotlin/StyledDanmaku.kt` | 测量 + 位图缓存 + 绘制 |
| `danmaku/ui/src/skikoMain/kotlin/StyledDanmaku.skiko.kt` | 桌面/Skia 位图生成 |
| `danmaku/ui/src/androidMain/kotlin/StyledDanmaku.android.kt` | Android 位图生成 |
| `danmaku/ui/src/commonMain/kotlin/FrameTimeSmoother.kt` | 帧时间 PLL |
| `danmaku/ui/src/commonMain/kotlin/DanmakuCollectionIterator.kt` | 跨轨道迭代器 |
| `danmaku/ui/src/commonMain/kotlin/DanmakuPresentation.kt` / `SizeSpecifiedDanmaku.kt` | 渲染期数据包装 |
| `danmaku/ui-config/src/commonMain/kotlin/DanmakuConfig.kt` | `DanmakuConfig`/`DanmakuStyle` |
| `danmaku/ui-config/src/commonMain/kotlin/DanmakuConfigRanges.kt` | 所有 UI 参数范围 |
| `danmaku/ui/src/desktopTest/kotlin/DanmakuHostFrameUiTest.kt` | 逐帧验证匀速/重排不跳变 |
| `danmaku/ui/src/commonTest/kotlin/FloatingDanmakuTrackTest.kt` | 碰撞/放置的单测（含大量数值断言，适合当验收用例） |
| `danmaku/api/src/commonTest/kotlin/DanmakuSessionAlgorithmTest.kt` | 装填算法单测 |

### App 层（`app/shared/`）

| 文件 | 内容 |
|---|---|
| `app-data/.../domain/danmaku/DanmakuRepository.kt` | 多源聚合、缓存策略、发送 |
| `app-data/.../domain/danmaku/DanmakuLoader.kt` | 本地/远程合并、手动匹配覆盖、加载状态 |
| `app-data/.../domain/danmaku/DanmakuLoadingState.kt` | 加载状态 |
| `app-data/.../domain/episode/EpisodeDanmakuLoader.kt` | 播放器桥接、源配置、shift、清洗 |
| `app-data/.../domain/settings/GetDanmakuRegexFilterListFlowUseCase.kt` | 生效正则规则 |
| `app-data/.../data/network/danmaku/AniDanmakuProvider.kt` | Animeko 官方源 |
| `app-data/.../data/network/danmaku/AniDanmakuSender.kt` | 发送弹幕 |
| `app-data/.../data/persistent/database/dao/DanmakuDao.kt` | 缓存表 + 本地源 |
| `app-data/.../data/repository/player/DanmakuRegexFilterRepository.kt` | 正则规则仓库（含导入导出） |
| `app-data/.../data/models/danmaku/DanmakuRegexFilter.kt` / `DanmakuFilterConfig.kt` / `DanmakuConfigSerializer.kt` | 过滤与配置模型 |
| `app-data/.../data/repository/danmaku/SearchDanmakuRequest.kt` | 匹配请求（`fileHash` 占位符所在） |
| `src/.../ui/danmaku/PlayerDanmakuHost.kt` | 播放器 ↔ 弹幕层 |
| `src/.../ui/danmaku/DanmakuEditor.kt` / `DanmakuEditorState.kt` | 发送框 |
| `src/.../ui/danmaku/DanmakuStylePicker.kt` | 发送样式（颜色/位置） |
| `src/.../ui/subject/episode/EpisodeViewModel.kt` | `uiDanmakuEventFlow`、`postDanmaku`、shift 入口 |
| `src/.../ui/subject/episode/details/DanmakuListStateProducer.kt` / `DanmakuListSection.kt` | 弹幕列表 + 源 Chip |
| `src/.../ui/subject/episode/details/EpisodeDetails.kt` | `DanmakuTimeShiftDialog`（±30s） |
| `src/.../ui/subject/episode/details/components/DanmakuMatchInfoGrid.kt` | 匹配质量展示、shift 格式化 |
| `src/.../ui/subject/episode/video/settings/EpisodeVideoSettings.kt` | 弹幕设置面板 |
| `src/.../ui/subject/episode/video/sidesheet/EditDanmakuRegexFilterSideSheet.kt` | 正则规则编辑 |
| `ui-episode/.../ui/episode/danmaku/MatchingDanmakuDialogs.kt` / `MatchingDanmakuUiState.kt` | 手动匹配 |
| `ui-episode/.../ui/episode/danmaku/DanmakuStrings.kt` | 源显示名 |

### 生成的 API 客户端（`client/`）

| 文件 | 内容 |
|---|---|
| `client/src/commonMain/gen/me/him188/ani/client/apis/DanmakuAniApi.kt` | Animeko 弹幕 API 契约 |
| `client/src/commonMain/gen/me/him188/ani/client/models/AniDanmaku*.kt` | 模型 |
| `client/src/commonMain/gen/me/him188/ani/client/infrastructure/ApiClient.kt` | `BASE_URL = https://api.animeko.org`、`auth-jwt` |
| `client/build.gradle.kts` | OpenAPI 生成配置（`ani.api.server` 默认 api.animeko.org） |

---

## 附录 C：参考链接

**仓库**
- 主仓库：https://github.com/open-ani/animeko
- 本次快照 commit：https://github.com/open-ani/animeko/commit/99fefc02fa62b15ecdfca03f6f70dfedea2c18f0
- `danmaku/api`：https://github.com/open-ani/animeko/tree/main/danmaku/api
- `danmaku/dandanplay`：https://github.com/open-ani/animeko/tree/main/danmaku/dandanplay
- `danmaku/ui`：https://github.com/open-ani/animeko/tree/main/danmaku/ui
- `danmaku/ui-config`：https://github.com/open-ani/animeko/tree/main/danmaku/ui-config
- README（弹幕来源 FAQ）：https://github.com/open-ani/animeko/blob/main/README.md
- 官网功能页源码（"弹幕云过滤"原文）：`open-ani/ani-website` → `src/components/home/Features.astro`

**DanDanPlay 官方**
- 开放弹幕网络接入指南（含签名规范）：https://doc.dandanplay.com/open/
- API 变动日志：https://doc.dandanplay.com/open/changelog.html
- 客户端协议（专用链等）：https://doc.dandanplay.com/open/client-protocol.html
- 接口 Swagger UI：https://api.dandanplay.net/swagger
- 接口 OpenAPI JSON：https://api.dandanplay.net/swagger/v2/swagger.json
- 开发者中心（申请 AppId/AppSecret）：https://dev.dandanplay.com
- 官网：https://www.dandanplay.com

**Animeko 官方站点**
- 官网：https://myani.org/
- 更新日志：https://animeko.org/changelogs/
- 弹幕服务 swagger（需鉴权，401）：https://danmaku-cn.myani.org/swagger/index.html

---

*报告完。全文所有结论均可在上述源码路径 / 官方规范中查到出处；第 10 节列出的 12 条是本报告明确"没查到/不确定"的部分。*
