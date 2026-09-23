# Emby 官方文档调研报告：媒体库命名 + 元数据刮削

> 调研对象：Emby（闭源媒体服务器，emby.media）**官方文档**。
> 所有结论均附可点击来源。凡官方文档未记载者，一律标注 **未能确认**，不作推测。
> 调研日期基准：文档站点源码仓库 `EmbySupport/Emby.Docs` master 分支快照（含 `toc.yml`），以及 emby.media / dev.emby.media / swagger.emby.media 实时抓取。

---

## 0. 证据分级（阅读本报告前必读）

本报告把来源分为三级，正文中用【L1】【L2】【L3】标注。**L1 之外的结论不可当作"官方规范"引用。**

| 级别 | 含义 | 载体 |
|---|---|---|
| 【L1】官方知识库 | Emby 官方文档站（GitHub Pages，源仓库 `EmbySupport/Emby.Docs`）。命名的**唯一权威来源** | `https://emby.media/support/articles/*.html` |
| 【L1-API】官方开发者文档 | Emby 官方 REST API 静态规范与 SDK 参考。字段名/枚举的权威来源 | `https://swagger.emby.media/openapi.json`、`https://dev.emby.media/` |
| 【L1-cat】官方插件仓库 | Emby 官方插件清单 JSON（插件 GUID、分类、版本） | `https://www.mb3admin.com/admin/service/EmbyPackages.json` |
| 【L2】官方博客 / 官方论坛官方人员发言 | Emby Team / Administrators / Moderators 在 emby.media/community 的发言。**非正式文档，但发言人身份可核验** | `emby.media/community/...` |
| 【L3】社区用户帖 | 含社区成员转贴的服务端源码片段。**只能作为线索，不能作为规范** | `emby.media/community/...` |

**关键结论（先说最要紧的一条）**：
Emby 官方知识库**根本没有"元数据提供方"专页，也没有任何"NFO"专页**。整个 `Emby.Docs` 仓库中 `nfo` 仅出现 3 次，且都是顺带提及（见 §2.2）。因此：

- 任何声称"Emby 官方文档规定 `movie.nfo` / `tvshow.nfo` / `season.nfo` / `episode.nfo` 文件名"的说法，**在官方文档中找不到依据 → 未能确认**。
- 任何声称"Emby 官方文档列出 TheMovieDb / TheTVDB / OMDb / MusicBrainz / AudioDb / TVmaze / Zap2It / Screen scraping 各自贡献哪些字段"的说法，**在官方文档中不存在该页面 → 未能确认**。

官方文档"Manage Metadata"分类（`toc.yml` 第 66–76 行）**只有 4 篇文章**：Metadata Manager、Identify、Ordering TV Special/Extras、Image Editing & Image Types。

---

## 1. 视频命名 / 目录结构规范（【L1】）

### 1.1 电影

来源：[Movie Naming](https://emby.media/support/articles/Movie-Naming.html)

- 建库时内容类型须选 **Movies**、**Home videos** 或 **Music videos**。
- 推荐格式：`MovieName (year).extension`，例如 `Top Gun (1986).mp4`、`Avatar (2009).mkv`。
- 推荐每部电影一个独立文件夹，**用文件夹名判定影片**：

```
\Movies\Avatar (2009)\Avatar (2009).mkv
\Movies\Pulp Fiction (1994)\Pulp Fiction (1994).mp4
\Movies\Reservoir Dogs (1992)\Reservoir Dogs (1992).mp4
\Movies\The Usual Suspects (1995)\The Usual Suspects (1995).mkv
\Movies\Top Gun (1986)\Top Gun (1986).mp4
```

- 原文明确：**"Movie folders are used internally to improve detection and would only be reflected in the library 'Folders' view."**（电影文件夹只用于内部提升识别率，仅在"Folders"视图体现。）
- 允许在库根与电影文件夹之间增加自定义分类层级（如按年代分段 `Movies 1925-1954\...`），这些层级同样只出现在 **Folders** 视图。

#### ID 标签（写在文件夹/文件名里）

支持格式（8 种）：

```
Name (Year) [tmdbid=xxxx]     Name (Year) [tmdbid-xxxx]
Name (Year) [tmdb=xxxx]       Name (Year) [tmdb-xxxx]
Name (Year) {tmdbid=xxxx}     Name (Year) {tmdbid-xxxx}
Name (Year) {tmdb=xxxx}       Name (Year) {tmdb-xxxx}
```

支持的 ID 名称：**tvdb、tmdb (Moviedb)、imdb**。示例：`Casino Royale (2006) [tmdbid=36557]`。
文档中给出的对应站点：tmdbid → `https://www.themoviedb.org/`，imdbid → `https://www.imdb.com/`，tvdbid → `https://thetvdb.com/`。

#### 多版本（Multi-version）

**多版本必须放在同一个电影文件夹内**，每个版本名必须以"文件夹名"开头，后接 `" - "`：

```
/Movies/300 (2006)/300 (2006) - 1080p.mkv
/Movies/300 (2006)/300 (2006) - 4K.mkv
/Movies/300 (2006)/300 (2006) - 720p.mp4
/Movies/300 (2006)/300 (2006) - extended edition.mp4
/Movies/300 (2006)/300 (2006) - directors cut.mp4
/Movies/300 (2006)/300 (2006) - 3D.hsbs.mp4
```

- 原文：**"If using the dash method anything following the dash will be what you see in the Emby client app."**
- 官方限制：**"Up to 8 different versions will appear in a list of a movie versions."**
- 官方提醒：该特性主要面向同一影片的多种画质；用于不同"剪辑版"时**可能存在限制**。

#### 电影 Extras（额外内容）

放在电影文件夹下的 extras 子文件夹中，**不支持嵌套文件夹**。支持的子文件夹名（原文列表）：

`extras`、`specials`、`shorts`、`scenes`、`featurettes`、`behind the scenes`、`deleted scenes`、`interviews`、`trailers`

```
/Movies/Home Alone (1990)/Home Alone (1990).mkv
/Movies/Home Alone (1990)/extras/deleted-scenes.mkv
/Movies/Home Alone (1990)/behind the scenes/video1.mkv
/Movies/Home Alone (1990)/interviews/video1.mkv
```

官方警告：**须先有正片文件再加 extras，否则会误识别**；所有类型 extras 会统一出现在详情页的 "Extras" 行。

#### 其他官方支持的格式

- **DVD/Blu-ray 目录结构**：含 `VIDEO_TS` 子目录或 `VIDEO_TS.ifo` 文件 → 识别为 DVD；含 `BDMV` 子目录 → 识别为 Blu-ray。
- **ISO**：文件名含 `.dvd` 或 `.bluray` 可自动判定 ISO 类型；**不含则默认按 DVD 处理**。
- **分卷（file stacking）**默认后缀：`part#`、`cd#`、`dvd#`、`pt#`、`disk#`、`disc#`（# 可取 1–9 或 A–D），另支持 `moviename#.ext`（# 为 A–D）。要求：各分卷在同一电影文件夹内，且**该文件夹内无其他视频**。
- 3D 命名：见 [3D Video Naming](https://emby.media/support/articles/3D-Videos.html)（需同时含 `3D` 标签 + `hsbs/fsbs/sbs/htab/ftab/mvc` 之一；分隔符可为空格、`-`、`.`、`_`；大小写不敏感）。
- 媒体占位文件 `.disc`：见 [Media Stubs](https://emby.media/support/articles/Media-Stubs.html)。
- `.strm`：见 [Strm Files](https://emby.media/support/articles/Strm-Files.html)。
- 预告片命名：见 [Trailer Naming](https://emby.media/support/articles/Trailers.html)（与正片同名 + `-trailer` 后缀，如 `Home Alone (1990)-trailer.mp4`；或放 `trailers` 子文件夹，**蓝光/DVD 目录rip 必须用 trailers 子文件夹**）。

### 1.2 电视剧

来源：[TV Naming](https://emby.media/support/articles/TV-Naming.html)

- 建库时内容类型须选 **TV**。原文：**"It is mandatory for each TV show to have its own folder under the library folder path(s)."**（每部剧必须有独立文件夹——强制性。）
- 推荐结构：`Series (year)\Season #\Episode`：

```
\TV\Glee (2009)\Season 1\Glee S01E01.mp4
\TV\Seinfeld (1989)\Seinfeld S01E01.mp4
```

- 年份非强制，但对重启剧（Battlestar Galactica 1978 vs 2003）与易混淆剧名（Africa (2013)）**非常有帮助**。
- TV 也支持同样的 8 种 ID 标签格式（`[tvdbid=xxxx]` 等），示例 `The Vampire Diaries (2009) [tvdbid=95491]`。

#### 剧集命名约定（官方完整列表）

```
show name - S01E01 - Episode Name.ext
show name S01E01 Episode Name.ext
anything_s01e02.ext
anything_s1e2.ext
anything_s01.e02.ext
anything_s01_e02.ext
anything_1x02.ext
anything_102.ext
anything_1x02.ext
02 Episode Name.ext
s01e02.ext
1x02.ext
```

#### 按日期命名（官方列表）

```
anything_1996.11.14.ext
anything_1996-11-14.ext
anything_14.11.1996.ext
```

> ⚠️ **重要否证**：任务书中提到的 `Series/Season 2019/Series 2019-01-01.ext`（按年份建 Season 文件夹）**未出现在官方 TV Naming 页面中**。官方仅记载"文件名按日期"，**未记载"Season 年份文件夹"** → **未能确认**。
> 同理，**绝对集号（absolute numbering）**：官方页面**没有** "absolute" 一词（已对全仓库 `*.md` 检索 `absolute`，仅命中 CSS 与"absolutely"）。官方列出的 `anything_102.ext`、`02 Episode Name.ext`、`s01e02.ext`、`1x02.ext` 是命名**格式**列表，官方**未**把它们归类为"绝对集号"→ **未能确认**"绝对集号"这一官方概念。
> 另：未找到"多季合并文件夹 / multi-season folder"的官方记载（全仓库检索 `multi-season`、`Season 20\d\d` 均无命中）→ **未能确认**。

#### 多版本剧集

```
show name - S01E01 - Display Name 1.ext
show name - S01E01 - Display Name 2.ext
```

示例：`Star Trek, The Next Generation - S01E01 - Original Broadcast.mkv`。破折号后到扩展名之间的内容即客户端版本选择器显示名。官方限制：**最多 8 个版本**。

#### 多集文件（Multi-Episode）

官方给出 20+ 种写法，**全部要求属于同一季**，例如：

```
01x02x03 episode name.ext        S01E02E03 episode name.ext
S01xE02xE03 episode name.ext     S01E02-E03 episode name.ext
S01E02-X03 episode name.ext      01x02 01x03 episode name.ext
01x02 - 01x05 episode name.ext   (includes episodes 2,3,4 and 5)
S01x02 - S01x03 episode name.ext
```

#### Specials（Season 00）

季文件夹名必须是以下之一：**`Season 0`**、**`Season 00`**、**`Specials`**。示例：

```
\TV\Glee (2009)\Season 0\Glee S00E01.mp4
```

Special 的剧情时间线排布见 [Ordering TV Show Special/Extras](https://emby.media/support/articles/Ordering-TV-Specials.html)（把 extras 命名为 `S00Eyy`，与 TVDB 的 `S00Exx` **留出编号间隔**（官方建议从 50 开始），再在剧集元数据里指定其应在哪一季之后显示）。

#### 混合内容库（Mixed Content）

来源同上页 + [Library Setup](https://emby.media/support/articles/Library-Setup.html)

- 建库时选 **Unset** 内容类型即可混装不同类型内容；**此场景下 TV 剧集必须有季子文件夹**。
- Library Setup 原文警告：**"Please note that support for mixed content is limited."**（混合内容支持有限。）初始只能添加一个媒体路径，之后可再加。

#### 复杂目录结构

若顶级目录在剧集文件夹之前还有细分（如 `\TV\A-M\Glee (2009)`），**官方推荐做法**是：建一个 TV 媒体库，然后把 `A-M`、`N-Z` 分别作为**库路径**加入，而不是添加顶层 `\TV`。

#### DVD/Blu-ray 剧集

季文件夹下可放 `\Glee S01E01-E04\VIDEO_TS`、`VIDEO_TS.IFO` 或 `\BDMV`。文件夹名任意，但**用集号命名能改善元数据下载与显示**。

#### TV Extras

可在**剧集级、季级、集级**放 extras 文件夹。支持的名字与电影相同（`extras`、`specials`、`shorts`、`scenes`、`featurettes`、`behind the scenes`、`deleted scenes`、`interviews`、`trailers`）。

也支持用**文件名后缀**标明 extra 类型（官方列表）：

```
-behindthescenes   -deleted   -featurette   -interview
-other             -scene     -short        -trailer
```

示例：`Anne With An E S01E01 You Will Shall Decide Your Destiny-short.mp4`。

> 官方重要说明：**"Emby will not look for external metadata provider (e.g. TVDB) matches for episode-level extras; to potentially match, the extras need to be at the series or season level."**
> （剧集级 extras 不会去外部元数据提供方匹配；要能匹配必须放在剧集级或季级。）
> 另官方不建议给单个剧集建文件夹，除非同时还要放该集的 extras 子文件夹。

### 1.3 音乐 / 有声书 / 图书

- **音乐**：[Music Naming](https://emby.media/support/articles/Music-Naming.html)
  **完全基于标签（tag based）**，Emby 4.6+ **不再强制目录结构**。推荐 `\Music\Artist Name\Album Name\1- Song.mp3`。若要**把元数据文件（nfo、图片等）写到媒体文件夹里**，则必须使用结构化目录（官方建议用 artist\album 布局）。
  编译专辑要让所有曲目的 **album** 与 **album artist** 字段一致，否则专辑会被拆开；无单一艺术家时 album artist 可填 `Various`。`artist` 多值用 **`"; "`（分号+空格）**分隔。同名专辑（同艺术家）需用 `mbzalbumids` 或改名区分，否则会被合并。
  Music Videos 用 **Music Videos** 库类型，命名规则**与电影相同**；文件需按 `https://imvdb.com/` 的命名来命名。
- **有声书**：[Audio Book Naming](https://emby.media/support/articles/Audio-Book-Naming.html)
- **图书**：[Book Naming](https://emby.media/support/articles/Book-Naming.html)
  内容类型选 **Books**。电子书支持格式：**pdf、epub、mobi、cbr、cbz、azw3**；有声书支持音乐库的全部音频格式。命名建议**仅"标题 + 年份"**，年份加括号置于文件/文件夹名末尾：`\Books\Pulp Fiction (1994).pdf`；也支持 `Avatar (2009)-cd1.pdf` / `Avatar (2009)-cd2.pdf`，以及 `\Books\Avatar (2009)\somefilename.pdf`。

### 1.4 合集（Collections）与 BoxSet 文件夹

来源：[Collections](https://emby.media/support/articles/Collections.html)、[AutoBoxSets](https://emby.media/support/articles/AutoBoxSets.html)

- 电影合集可**自动创建**：在每个电影库的设置里有两个选项——（1）是否导入合集信息；（2）构成合集所需的**最少电影数量**。
- 手动建合集：任意条目右键 / 三点菜单 → **"Add to Collection"**。要自动抓海报与元数据，**合集名应与 TheMovieDb.Org 上的 collection 名一致**（如 "Star Wars Collection"）。
- [AutoBoxSets](https://emby.media/support/articles/AutoBoxSets.html) 原文：**"The Auto Box Sets features have now been merged into Emby Server."** 原 Auto Box Sets 插件**已无功能，可卸载**。

> ⚠️ **重要否证**：官方文档**没有**任何名为 `Collections` 或 `BoxSets` 的**媒体文件夹**命名约定（即在 `\Movies\` 下放 `BoxSets\` 文件夹那种做法）。全仓库检索 `BoxSet` 仅命中 `AutoBoxSets.md` 自身的 uid/title/legacyUrl。若要主张"Emby 支持 BoxSets 文件夹"，**在官方文档中无依据 → 未能确认**。

### 1.5 排除扫描的文件/文件夹

来源：[Excluding Files & Folders](https://emby.media/support/articles/Excluding-Files-Folders.html)

- **Emby Server 4.9 及以后**：在库根或任意子文件夹放 `.embyignore` 文件。规则：
  - `#` 开头为注释；空行忽略；每行一个 pattern/文件名/路径。
  - 文件名/文件夹名 pattern 可用通配符 `*`。
  - **目录分隔符统一用正斜杠 `/`（Windows 亦然）**。
  - 使用目录分隔符时，pattern 相对于**存放 `.embyignore` 的目录**；不使用分隔符时，可匹配该目录下**任意层级**。
  - 另**新增支持 `.plexignore`**，库设置里有开关。
- **Emby Server 4.8**：在文件夹内放 `.ignore` 文件即可跳过该文件夹及其所有子文件夹。
- 官方注：Emby 对 `.plexignore` 的行为与 Plex 可能存在差异，差异**不算缺陷**。

### 1.6 适合流媒体的封装（官方工程建议）

[Movie Naming](https://emby.media/support/articles/Movie-Naming.html) 原文建议：**MP4 或 MKV 容器 + H.264（或 H.265）视频 + 至少一条 2 声道 AAC 默认音轨**，其他音轨（Dolby Digital、DTS）并列。目的是**减少服务端实时转码**。原文说明这是"强烈建议"（strongly encouraged）而非硬性要求。

---

## 2. 本地元数据优先级 / NFO / 图片文件名

### 2.1 本地 vs 在线：官方人员明确表态【L2】

**来源：[Metadata Priority - Local vs Remote（Emby 官方论坛 Developer API 版块）](https://emby.media/community/topic/121372-metadata-priority-local-vs-remote/)**

Emby 管理员（Administrators 组，Emby Team 徽章）**Luke** 于 2023-09-09 原文回复：

> **"Hi, local metadata is always preferred first on a normal scan. It's only when you refresh metadata that it moves to last. There's no way to change that."**
> （正常扫描时本地元数据**总是优先**；只有在"刷新元数据"时本地才被排到最后。**无法更改此行为**。）

同帖 Luke 补充：他希望将来"取消 local/remote 的区分，改成一份可任意排序的提供方列表"，但**当时尚未实现**。同帖 Emby Dev **softworkz** 补充了插件层面的顺序控制接口：`ICustomMetadataProvider<T>` 在 local 与 remote 之后执行；不要实现 `IPreRefreshProvider`（会在所有其他之前执行）；用 `IHasOrder` 设顺序（仅在同类型提供方之间生效）；`IForcedProvider` 可让提供方在条目被锁定时仍执行。

**这是"Emby 是否偏好本地 NFO/图片"这一问题的准确答案：扫描时本地优先，刷新时本地最后。官方知识库中无此说明——它只存在于官方论坛。**

### 2.2 NFO：官方知识库中的全部记载（仅 3 处）

对 `Emby.Docs` 全仓库 `*.md` 检索 `\bnfo\b|\.nfo`，**仅 3 处命中**，且**均未给出任何 NFO 文件名**：

1. [Backup & Restore to New System](https://emby.media/support/articles/Backup-Restore-To-New-System.html)
   > "Any artwork/nfo files stored alongside the media files, should be included in your migration of the media to the new system."
2. [Backup Using Plugin](https://emby.media/support/articles/Backup-Using-Plugin.html)
   > "You can configure your libraries to save nfo and image files alongside the media within the media folders and they would then be included in your own library media content backups."
3. [Music Naming](https://emby.media/support/articles/Music-Naming.html)
   > "if you wish to save metadata files directly into your media folders such as nfo, images, etc, then a structured folder layout is necessary..."

**结论：**
- 官方文档确认存在"把元数据保存为 nfo 并写入媒体文件夹"的**能力**与**库级开关**；
- 但官方文档**从未列出** `movie.nfo` / `tvshow.nfo` / `season.nfo` / `episode.nfo` / `<videoname>.nfo` 这些文件名 → 这些具体文件名**属于未能确认（官方文档层面）**。它们能在社区帖里看到用户实际使用（例如 [Naming of .nfo and posters](https://emby.media/community/topic/70312-naming-of-nfo-and-posters/) 用户列出的 `tvshow.nfo`、`season.nfo`、`<videoname>.nfo`、`<videoname>-thumb.jpg` 等），但那是**用户第三方工具（meta<browser/>）产出**的目录，**不是 Emby 官方规范**。
- 【L1-cat】官方插件仓库中有一个名为 **"Nfo Metadata"** 的插件（GUID `E610BA80-9750-47BC-979D-3F0FC86E0081`，category=Metadata，owner=luke，overview="Nfo metadata support"，共 56 个版本，最新 `1.0.11`）。→ **NFO 读取能力在当代 Emby 中是由插件承载的**。来源：[EmbyPackages.json](https://www.mb3admin.com/admin/service/EmbyPackages.json)。
- 【L2】Emby 管理员 Luke 明确：**NFO 不适用于照片（photos）**。原文："**Nfo's are not used with photos.**"（[Unable to generate NFO files](https://emby.media/community/topic/147223-unable-to-generate-nfo-files/)，2026-04）。同帖 Luke 表示未来考虑改为把元数据写入图片文件内嵌元数据。
- 【L1-API】库选项字段（`LibraryOptions`）中包含 `SaveLocalMetadata`、`SaveLocalThumbnailSets`、`MetadataSavers`、`DisabledLocalMetadataReaders`、`LocalMetadataReaderOrder`。来源：[swagger.emby.media/openapi.json](https://swagger.emby.media/openapi.json)（spec title "Emby Server API"，version 4.1.1.0）。**这些字段名是权威的，但官方文档未解释其取值含义。**

### 2.3 图片文件名 —— 这部分官方文档**有**明确规定（【L1】）

#### 电影 / 视频（含 Home Video、Music Video）

来源：[Movie Naming § Video images](https://emby.media/support/articles/Movie-Naming.html)

支持的图片扩展名：**jpg、jpeg、png、gif、tbn**。同一图片类型支持多个文件名时，**按下列顺序依次检查**（官方原文："They are listed in the order that they're checked for."）：

| Image Type | 支持的（官方原文）文件名，按检查顺序 |
|---|---|
| Primary | `{name}.ext`、`{name}-poster.ext`、`{name}-cover.ext`、`{name}-default.ext`、`{name}-movie.ext`、`folder.ext`、`poster.ext`、`cover.ext`、`default.ext`、`movie.ext` |
| Art | `{name}-clearart.ext`、`clearart.ext` |
| Backdrop | `backdrop.ext, backdropX.ext`、`fanart.ext, fanart-X.ext`、`background.ext, background-X.ext`、`art.ext, art-X.ext`、`extrafanart (子文件夹)/fanartX.ext` |
| Banner | `{name}-banner.ext`、`banner.ext` |
| Disc | `{name}-disc.ext`、`{name}-cdart.ext`、`disc.ext`、`cdart.ext` |
| Logo | `{name}-clearlogo.ext`、`clearlogo.ext`、`{name}-logo.ext`、`logo.ext` |
| Thumb | `{name}-thumb.ext`、`{name}-landscape.ext`、`thumb.ext`、`landscape.ext` |

- `{name}` = **视频文件名去扩展名**。官方原文：**"For videos that are not contained within their own folder, only the conventions using {name} are supported."**（未各自建文件夹的视频，只能用 `{name}` 系列写法。）
- `X` 为数字，可有任意多个编号 backdrop（`backdrop.ext`、`backdrop1.ext`、`backdrop2.ext`…）。

> 对任务书列出的文件名逐项核对：`poster.jpg` ✅、`folder.jpg` ✅、`fanart.jpg` ✅、`backdrop.jpg` ✅、`landscape.jpg` ✅、`logo.png` ✅、`thumb.jpg` ✅、`<videoname>-thumb.jpg` ✅、`banner.jpg` ✅、`disc.png` ✅、`clearlogo.png` ✅ —— **全部在官方表格内**。
> 唯一注意点：官方表格用的是**按类型分组的通配写法**（如 `logo.ext`），扩展名可取 jpg/jpeg/png/gif/tbn。

#### 剧集 / 季

来源：[TV Naming § Series & Season Images](https://emby.media/support/articles/TV-Naming.html)

支持的图片扩展名：**jpg、jpeg、png、tbn**（**注意：此处官方未列 gif**）。

| Image Type | 支持的（官方原文）文件名，按检查顺序 |
|---|---|
| Primary | `folder.ext`、`poster.ext`、`cover.ext`、`default.ext`、`show.ext`（**仅剧集文件夹**）、`seasonXX-poster.ext`（仅剧集文件夹）、`season-specials-poster.ext`（仅剧集文件夹） |
| Art | `clearart.ext` |
| Backdrop | `backdrop.ext, backdropX.ext`、`fanart.ext, fanart-X.ext`、`background.ext, background-X.ext`、`art.ext, art-X.ext`、`extrafanart (子文件夹)/fanartX.ext`、`seasonXX-fanart.ext`（仅剧集文件夹）、`season-specials-fanart.ext`（仅剧集文件夹） |
| Banner | `banner.ext`、`seasonXX-banner.ext`、`season-specials-banner.ext` |
| Disc | `disc.ext`、`cdart.ext` |
| Logo | `clearlogo.ext`、`logo.ext` |
| Thumb | `thumb.ext`、`landscape.ext`、`seasonXX-landscape.ext`、`season-specials-landscape.ext` |

**不使用季文件夹时**，季图片仍可直接放在剧集文件夹，用命名表明季号：`seasonXX-poster.ext`、`season-specials-poster.ext`、`seasonXX-fanart.ext`、`season-specials-fanart.ext`、`seasonXX-banner.ext`、`season-specials-banner.ext`、`seasonXX-landscape.ext`、`season-specials-landscape.ext`（示例用 `season01-poster.jpg`、`season-specials-poster.jpg`）。

**剧集（episode）图片**（官方给的**全部**约定，只有两条）：

- `{name}-thumb.ext`（放在**同一文件夹**）
- `{name}.ext`（放在 **metadata 子文件夹**）

#### 音乐（艺术家 / 专辑）

来源：[Music Naming § Music Images](https://emby.media/support/articles/Music-Naming.html)

扩展名：**jpg、jpeg、png、tbn**。

| Image Type | 支持的文件名 |
|---|---|
| Primary | `folder.ext`、`poster.ext`、`cover.ext`、`default.ext`、`artist.ext`、`artist-cover.ext`、`artist-default.ext`、`artist-folder.ext`、`artist-poster.ext` |
| Backdrop | `backdrop.ext, backdropX.ext`、`fanart.ext, fanartX.ext, fanart-X.ext`、`background.ext, background-X.ext`、`art.ext, art-X.ext`、`artist-art.ext, artist-artX.ext`、`artist-backdrop.ext, artist-backdropX.ext`、`artist-background.ext, artist-backgroundX.ext`、`artist-fanart.ext, artist-fanartX.ext`、子文件夹 `extrafanart`：`fanartX.ext, fanart-X.ext` |
| Disc | `disc.ext`、`cdart.ext` |
| Logo | `logo.ext`、`clearlogo.ext` |
| Thumb | `thumb.ext`、`landscape.ext`、`` `<folder name>.ext` `` |

官方示例：艺术家文件夹名 "Ed Sheeran" → thumb 文件为 `ed sheeran.jpg`；专辑文件夹 "Pink Floyd - The Dark Side of the Moon" → `Pink Floyd - The Dark Side of the Moon.jpg`。
官方版本要求：**`artist.ext` 与 `artist-xxxx.ext` 系列文件名需要 Emby Server 4.10**。
官方注意：若同一专辑文件夹在库中出现多次，**各处的自定义图片必须一致**。

---

## 3. Identify / 手动匹配（【L1】）

来源：[Identify](https://emby.media/support/articles/Identify.html)、[Metadata Manager](https://emby.media/support/articles/Metadata-manager.html)

### 3.1 Identify 是什么

`Identify` 是一个**针对单个条目**的纠错功能，用于把被识别错的条目重新匹配到互联网元数据。

操作路径：Web 应用 → 打开条目详情页 → 点 **3 点菜单** → **Identify**。
然后：先用名称试搜，或尽量填满各字段以获得最佳匹配 → 从返回结果中选择正确的一条 → 确认。

> 官方限制（原文）：**"The IDENTIFY option is only available for Emby administrators."**（Identify **仅管理员可用**。）

### 3.2 提供方 ID 的两种注入方式

来源：[Metadata Manager § Database IDs](https://emby.media/support/articles/Metadata-manager.html)

1. **UI 直接填 ID**：原文 "if you already know the database IDs for your incorrectly identified item, simply insert the correct IDs into the database fields and refresh the item."
2. **写进文件夹/文件名**（免刮削直接命中）：见 §1.1 / §1.2 的 `[tmdbid=xxxx]`、`[tvdbid=xxxx]`、`[imdbid=xxxx]` 等 8 种格式。

官方对"ID 为何关键"的说明（原文要点）：
- "Database IDs are extremely important to the server to determine what each media item is and whether it has been watched."
- **副作用警告**：同一 ID 的两个条目（例如同一电影的 3D 与 2D 版）会被服务端视为**完全相同的条目**，可能导致它重复出现在 Resume 标签等位置；且**看了 3D 版会把 2D 版也标记为已看**。

### 3.3 刷新元数据的两种模式（影响本地 NFO 是否被覆盖）

来源：[Metadata Manager](https://emby.media/support/articles/Metadata-manager.html)

Refresh Metadata 的选项屏提供两种 **Refresh modes**：
- **刷新全部元数据（默认）**：用外部数据库的元数据**覆盖所有字段**。
- **仅刷新缺失数据**：保留全部已有元数据，**只填充缺失字段**。

另有 **"Replace existing images"**：强制删除现有图片并重新下载全部图片。官方警告：**该选项也会删掉你的自定义图片**。

### 3.4 元数据字段锁（lock）

官方原文：改完任何元数据**必须点 Save**，否则离开页面即丢失；若希望改动**在刷新后仍然保留**，必须**锁定该字段或锁定整个条目**（lock the metadata field / lock the entire item）。目标：让 Tags 之类的字段在库刷新后不消失。

---

## 4. 元数据提供方（Providers）

### 4.1 官方知识库说了什么 —— 很少

**（a）** [Metadata Manager § Images](https://emby.media/support/articles/Metadata-manager.html) 原文：

> **"Images are downloaded from Fanart.tv, TheMovieDB, The Open Movie Database, and TheTVDB."**

→ 这是官方知识库对**图片**来源的**唯一**枚举：**Fanart.tv、TheMovieDB、The Open Movie Database(OMDb)、TheTVDB**（4 个）。

**（b）** 同页另一处原文（关于文本元数据）：

> "All media can get their information from online databases such as **TheMovieDB** and **TheTVDB**."

→ 官方知识库点名的**文本元数据**库只有 **TheMovieDB** 与 **TheTVDB**。另有 [Collections](https://emby.media/support/articles/Collections.html) 提到合集信息来自 **TheMovieDb.Org**。

**（c）** [Metadata Manager](https://emby.media/support/articles/Metadata-manager.html) 说明刷新行为：**"Selecting Refresh will pull fresh metadata from each of the databases that the server is configured to pull from for the library."** → 提供方是**按库配置**的（每库可各自启用/排序）。

### 4.2 官方博客（L2，Emby Team 撰写）

来源：[How to guide: Metadata（Emby Blog，2024-12-25，作者 sross44，Emby Team）](https://emby.media/community/blogs/entry/581-how-to-guide-metadata/)

原文要点：
- "Select metadata providers like **TheMovieDB** (for movies), **TheTVDB** (for TV shows), or **MusicBrainz** (for music)."
- "**Drag and drop the providers to prioritize which one Emby should use first.**"（提供方可拖拽排序决定优先级。）
- "Make sure **'Download metadata from the internet'** is checked."
- "**Language Preferences**: Specify the language for metadata **and artwork** to match your preference."
- 关于 NFO："In the Metadata settings, **enable saving metadata as NFO files**. These are XML files stored alongside your media, allowing you to edit them directly." 并提到可用 `Media Companion`、`TinyMediaManager` 批量产出 NFO 供 Emby 使用。
- "**Use Local Metadata**: Preload metadata by saving it as NFO files or by downloading artwork to local folders."

→ 该博客确认：**TheMovieDB = 电影、TheTVDB = 电视剧、MusicBrainz = 音乐**，且提供方**可拖拽排序**。但它是博客，**不是**正式规范页。

### 4.3 官方插件仓库给出的"今天的提供方清单"（【L1-cat】，最可核验）

来源：[Emby 官方插件清单 EmbyPackages.json](https://www.mb3admin.com/admin/service/EmbyPackages.json)（共 219 个包；下表筛出 `category: Metadata` 及元数据相关项）

| 插件名 | GUID | category | owner | overview（清单原文） |
|---|---|---|---|---|
| **MovieDb** | `3A63A9F3-810E-44F6-910A-14D6AD1255EC` | Metadata | luke | "MovieDb metadata for movies"（81 个版本） |
| **Tvdb** | `7FB7FF5E-5407-4F74-8990-B7AA643085D2` | Metadata | luke | "Tvdb metadata provider for tv shows"（86 个版本） |
| **OMDb** | `C25B3C85-1880-4827-9C72-0FA74314F428` | Metadata | luke | "OMDb metadata provider for Emby."（21 个版本） |
| **MusicBrainz** | `341944AF-4959-47E5-8ACE-398520208A71` | Metadata | luke | "A MusicBrainz plugin for Emby."（25 个版本） |
| **TheAudioDb** | `18CFFD2C-74F5-4EDE-8DAD-BE339443AFE4` | Metadata | luke | "A metadata provider using TheAudioDb."（目标文件名 `AudioDb.dll`） |
| **TV Maze Metadata Provider** | `F4A6AE33-4466-437C-9824-60098197B1AD` | Metadata | **softworkz** | TVmaze provider，声明提供 `TvMazeSeriesProvider`、`TvMazeEpisodeProvider`、`TvMazeSeasonProvider`、`TvMazePersonProvider` 及 4 个对应 ImageProvider |
| **TV Maze Metadata Provider (old)** | `6c39ca29-6ec8-4e72-a471-9d23136adaf3` | — | — | 旧版 |
| **Nfo Metadata** | `E610BA80-9750-47BC-979D-3F0FC86E0081` | Metadata | luke | "Nfo metadata support" |
| **Fanart.tv** | `8D7D93B2-01DC-48DC-8C5D-4E7ABBD9F9EB` | Metadata | luke | "An Emby Server plugin for fanart.tv" |
| **StudioImages** | `C68856B8-6031-480D-B08E-43B9114ADDB2` | Metadata | luke | "Images for Studios" |
| **Xml Metadata** | `2850d40d-9c66-4525-aa46-968e8ef04e97` | Metadata | luke | "Adds **legacy xml** support for movies, tv series, and home videos." |
| **Collection Manager** | `80FDA42F-C32A-4BAE-8757-4DD49EF331A0` | General | flawkee | 第三方插件；用 studio 元数据建 Studios Collections，用 **TMDB** 建电影系列 binge 播放列表、用 **TVMaze** 建剧集宇宙 |

**由此可确认**：
- **TheMovieDb（插件名 `MovieDb`）、TheTVDB（插件名 `Tvdb`）、OMDb、MusicBrainz、TheAudioDb、TVmaze、Fanart.tv** 在今天的 Emby 中都以**插件**形式分发。**未能确认**其中哪些同时仍有服务端内建实现——插件清单无法区分"内建+可选插件"与"纯插件"。
- **Zap2It**：官方插件清单与官方知识库中**均无任何命中** → **未确认**（Emby 文档体系中查无此提供方；Zap2It 是 Plex 生态的 EPG 来源，不属于 Emby 元数据提供方）。
- **"Screen scraping"（屏幕刮削）**：官方插件清单与官方知识库中**均无任何命中** → **未确认**。
- **"Emby Metadata"** 作为**元数据提供方**：官方文档无此命名 → **未确认**。（Emby 官方另有 [Emby Guide Data](https://emby.media/support/articles/Emby-Guide-Data.html) 用于 Live TV EPG，属节目单数据而非影视元数据提供方。）

### 4.4 TheTVDB 是否"变成纯插件"？ —— 需分情况表述

**证据**：
- 【L2】Emby Moderator **GrimReaper**（2025-08-19）原文：
  > "TVDB is **default provider** for TV Shows-type libraries **on new server installations**. If you did not change anything, that should already be happening."
  当用户问"是否需要手动装 TVDB 插件"时，GrimReaper 答：
  > "If you've uninstalled it - yes. If you only disabled it (or moved it down from top-preferred metadata provider), you need to re-enable/re-arrange preferred providers."
  来源：[TheTVDB as a source](https://emby.media/community/topic/141431-thetvdb-as-a-source/)
- 【L2】同帖社区 Top Contributor **Happy2Play**：
  > "TVDB has always been a primary TV provider for **v3 and now v4 api**."
- 【L1-cat】官方插件清单中 `Tvdb` 排在 Metadata 分类，共 86 个版本。

**可安全表述的结论**：TVDB 由**插件**提供（插件名 `Tvdb`），但在**新装服务器上是 TV 类库的默认首选提供方**；若被卸载则需重新安装，若只是被禁用或降序则需重新启用/调整顺序。**"TVDB 在某个具体版本起变为纯插件、不再内建"这一点，官方文档未记载 → 未能确认（未能确认具体版本与变更公告）**。

### 4.5 TMDB API key 要求？ —— 官方口径：用户**不需要**填 key

**证据**：【L2】Emby 管理员 **Luke**（2025-01-18）原文：

> **"Hi, you do not have to configure any api key. This is all built into Emby Server."**

来源：[plugins MovieDb issue](https://emby.media/community/topic/135571-plugins-moviedb-issue/)

该帖中用户从服务端日志里贴出的请求形如
`GET https://api.themoviedb.org/3/configuration?api_key=****`（key 由日志脱敏脚本插入了不可见字符，直接点击会报 "Invalid API key"），Luke 与 Happy2Play 均指出那是**日志脱敏**造成的假象，真实失败原因是 **TMDB API 超时/被网络阻断**。

**结论**：截至 2025-01，Emby 自带 TMDB API key，**用户无需自行申请/配置** → **"TMDB 要求用户自备 API key"这一说法不成立（就官方口径而言）**。

### 4.6 各提供方"贡献哪些字段" —— 官方文档**没有**这类对照表

- 官方知识库与官方博客**都没有**逐提供方的字段贡献表 → **未能确认**。
- 【L1-API】官方 OpenAPI 中可见提供方的**查询输入**结构（而非输出字段）：`Providers.MovieInfo`、`Providers.SeriesInfo`、`Providers.AlbumInfo`、`Providers.ArtistInfo`、`Providers.BookInfo`、`Providers.BoxSetInfo`、`Providers.GameInfo`、`Providers.MusicVideoInfo`、`Providers.PersonLookupInfo`、`Providers.SongInfo`、`Providers.TrailerInfo`。其中 `Providers.MovieInfo` 与 `Providers.SeriesInfo` 的字段为：`Name`、`MetadataLanguage`、`MetadataCountryCode`、`ProviderIds`(map)、`Year`、`IndexNumber`、`ParentIndexNumber`、`PremiereDate`、`IsAutomated`（`SeriesInfo` 另有 `EpisodeAirDate`）。来源：[swagger.emby.media/openapi.json](https://swagger.emby.media/openapi.json)。
  另：提供方的**可配置性**由 `Configuration.TypeOptions` 描述，字段为 `MetadataFetchers`、`MetadataFetcherOrder`、`ImageFetchers`、`ImageFetcherOrder`、`ImageOptions`；可选提供方由 `Library.LibraryOptionInfo`（`Name`、`DefaultEnabled`）表示；`Library.LibraryTypeOptions` 则暴露某库类型的 `MetadataFetchers`、`ImageFetchers`、`SupportedImageTypes`、`DefaultImageOptions`。**这些是权威字段名，但没有官方文字解释每个提供方填哪些字段。**

---

## 5. 分级（Ratings）

### 5.1 字段语义

【L1-API】官方 OpenAPI `BaseItemDto` 中与分级相关的字段（权威字段名）：

| 字段 | JSON 类型 | 含义（依据字段名与官方文档上下文） |
|---|---|---|
| `OfficialRating` | `string` | "官方分级"——面向**内容分级制度**的字符串（如 `PG-13`、`R`、`FSK-12`）。用于家长控制比较。 |
| `CustomRating` | `string` | 用户/自定义分级，覆盖 `OfficialRating` 用于家长控制的判断。 |
| `CommunityRating` | `number(float)` | **社区/用户评分**（0–10 量级），即大众打分，**不是**内容分级。 |
| `CriticRating` | `number(float)` | **影评人评分**，同样是数值评分，**不是**内容分级。 |
| `IsoSpeedRating` | — | ISO 感光度（照片 EXIF 用途）。 |

来源：[swagger.emby.media/openapi.json](https://swagger.emby.media/openapi.json) → `components.schemas.BaseItemDto`。
相关：`Users.UserPolicy.MaxParentalRating`（用户级最大允许分级）、`MetadataEditorInfo.ParentalRatingOptions`（`ParentalRating[]`）、`QueryFiltersLegacy.OfficialRatings`、`UserLibrary.OfficialRatingItem`（只有 `Name` 一个字段）。

**注意**：`CommunityRating` / `CriticRating` 是**数值**，`OfficialRating` / `CustomRating` 是**字符串**——这是三者最本质的区别。

### 5.2 官方文档对"分级"怎么说

- [Parental Controls](https://emby.media/support/articles/Parental-Controls.html)：路径为 Dashboard → **Users** → 点用户 → **Parental Control**。原文 "**The simplest way is to set the max parental rating for a user.**"，并强调 "**Content with a higher rating will not be displayed. This value will not affect unrated content, but there are additional options to control that as well:**"（高于该分级的内容不显示；该值**不影响未分级内容**，另有单独选项处理未分级内容。）
- [Metadata Manager](https://emby.media/support/articles/Metadata-manager.html)：举 PG-13 / R 为例说明家长控制如何读取该字段。
- [Live TV Manage Channels](https://emby.media/support/articles/Live-TV-Manage-Channels.html)：频道也可设 Parental rating 与 tags。

> **官方知识库中不存在"各国分级制度一览表"（US MPAA / GB BBFC / DE FSK / JP …）** → **未能确认（官方文档层面）**。

### 5.3 分级取值表 —— 只在社区转贴的服务端源码片段里出现【L3】

来源：[Format of age rating（Emby 官方论坛 Feature Requests 版块，已标 Completed）](https://emby.media/community/topic/105402-format-of-age-rating/)

社区 Top Contributor **Happy2Play** 贴出了 Emby 服务端 `LoadRatings(...)` 的代码片段。**这是转贴的源码，不是官方规范文档**，引用时务必标注来源性质。

**`us`：**

```
LoadRatings("us", new[] {
  new ParentalRating("TV-Y", 1),      new ParentalRating("APPROVED", 1),
  new ParentalRating("G", 1),         new ParentalRating("E", 1),
  new ParentalRating("EC", 1),        new ParentalRating("TV-G", 1),
  new ParentalRating("TV-Y7", 3),     new ParentalRating("TV-Y7-FV", 4),
  new ParentalRating("PG", 5),        new ParentalRating("TV-PG", 5),
  new ParentalRating("PG-13", 7),     new ParentalRating("T", 7),
  new ParentalRating("TV-14", 8),     new ParentalRating("R", 9),
  new ParentalRating("M", 9),         new ParentalRating("TV-MA", 9),
  new ParentalRating("NC-17", 10),    new ParentalRating("AO", 15),
  new ParentalRating("RP", 15),       new ParentalRating("UR", 15),
  new ParentalRating("NR", 15),       new ParentalRating("X", 15),
  new ParentalRating("XXX", 15)
});
```

**`de`：**

```
LoadRatings("de", new[] {
  new ParentalRating("DE-0", 1),   new ParentalRating("FSK-0", 1),
  new ParentalRating("DE-6", 5),   new ParentalRating("FSK-6", 5),
  new ParentalRating("DE-12", 7),  new ParentalRating("FSK-12", 7),
  new ParentalRating("DE-16", 8),  new ParentalRating("FSK-16", 8),
  new ParentalRating("DE-18", 9),  new ParentalRating("FSK-18", 9)
});
```

**要点（若采信此 L3 来源）**：
- 分级是**按语言/国家分组**的字符串集合，每组有**数值索引**（原文："But all ratings have an index number per their language."）。
- 同帖进一步信息：分级字符串必须**精确匹配**；用户实测把 `DE:FSK-0` 与 `DE:FSK0`、`DE:FSK 0`、`DE:0` 等多个值混写在同一 `mpaa` 字段里会导致该条被判为**未知分级**（Happy2Play："If that entire string is listed I would assume it would be considered an unknown rating."）。

> ⚠️ GB（BBFC）、JP 及其他国家的分级表：**本次调研未找到**任何可靠来源（既无官方文档，也未找到可核验的源码转贴）→ **未能确认**。请在报告中对 GB/JP 明确写"未能确认"。
> 任务书给出的 US 列表（G/PG/PG-13/R/NC-17/NR/UR）**与该源码片段基本吻合**，但源码片段里还有 `APPROVED`、`E`、`EC`、`TV-Y`、`TV-G`、`TV-Y7`、`TV-Y7-FV`、`TV-PG`、`TV-14`、`TV-MA`、`T`、`M`、`AO`、`RP`、`X`、`XXX`（含影视与游戏分级混排）。

---

## 6. 图片（Images）

### 6.1 Emby 支持的图片类型（权威枚举，【L1-API】）

`ImageType` 枚举（OpenAPI，出现在 `ImageInfo.ImageType`、`Configuration.ImageOption.Type`、`Library.LibraryTypeOptions.SupportedImageTypes`）：

```
Primary, Art, Backdrop, Banner, Logo, Thumb, Disc,
Box, Screenshot, Menu, Chapter, BoxRear, Thumbnail
```

来源：[swagger.emby.media/openapi.json](https://swagger.emby.media/openapi.json)

**每种类型的官方用途说明**（【L1】）：[Image Editing & Image Types](https://emby.media/support/articles/Image-Editing.html)

| 类型 | 官方原文说明 |
|---|---|
| **Primary** | "This is the normal cover art used." |
| **Logo** | "Option image usually superimposed over backdrops" |
| **Backdrops/Fanart** | "This is the background used behind all other graphics and text. **Many clients can alternate these backgrounds if more than one graphic is present.**" |
| **Thumb** | "Used for thumbnail views" |
| **Banner** | "Used for banner views" |
| **Disc** | "Used for disk views" |
| **Art** | "Used in some clients similar to the Logo image type" |

同页还记录了 **Live TV 频道**专用图片类型（这部分与影视条目不同）：
- **Primary** — "This channel logo is the historic logo used for channels and third party plugins. **On newer Emby clients it is used on light colored backgrounds.**"
- **LogoLight & LogoLightColor** — "This channel logo is used on **dark colored backgrounds**. **LogoLightColor is the default logo used** but options in clients may allow for use of LogoLight as well."

同页还说明各视图的降级行为：banner / disc / logo / thumb 视图中，**没有对应图片的条目会回退使用 primary 图**。

> 注意：`LogoLight` / `LogoLightColor` 出现在官方文档正文中，但**不在** OpenAPI 的 `ImageType` 枚举里（该 spec 版本为 4.1.1.0，可能早于该特性）→ 若报告需严格引用枚举，请以文档正文为准并注明 API spec 版本。

### 6.2 图片尺寸 / 缩放宽度

**官方知识库没有任何"图片必须为 X×Y 像素"的规定** → **未能确认**是否存在官方推荐尺寸。

可核验的是**服务端出图接口的缩放参数**（【L1-API】）。所有图片取图端点（形如 `GET /Items/{Id}/Images/{Type}/{Index}`、`GET /Artists/{Name}/Images/{Type}/{Index}`、`GET /Genres/{Name}/Images/{Type}/{Index}` 等）都接受同一组参数：

```
MaxWidth   integer
MaxHeight  integer
Width      integer
Height     integer
Quality    integer
Tag        string
CropWhitespace   boolean
EnableImageEnhancers  boolean
Format     string
AddPlayedIndicator    boolean
PercentPlayed   number
UnplayedCount   integer
BackgroundColor string
ForegroundLayer string
Type       string
Index      integer
```

来源：[swagger.emby.media/openapi.json](https://swagger.emby.media/openapi.json)（对 `paths` 中所有 `/Images` 端点的 `parameters` 逐一核对）

→ **结论**：Emby 的图片缩放是**请求时按客户端需要指定 `MaxWidth`/`MaxHeight`/`Width`/`Height`/`Quality` 的服务端实时缩放**，**并非**文档化的固定"缓存宽度"常量。
**任务书问的"缓存/取图缩放宽度具体数值"**：官方文档与官方 API spec 中**均未给出固定数值** → **未能确认**（如需具体数值，只能查服务端源码/日志，不在官方文档范围内）。

### 6.3 每库的图片抓取选项

【L1-API】`Configuration.ImageOption` 结构（每个图片类型的抓取限制）：

```
Type      : ImageType 枚举
Limit     : integer   (数量上限)
MinWidth  : integer   (最小宽度门槛)
```

来源：[swagger.emby.media/openapi.json](https://swagger.emby.media/openapi.json)
【L2】[Metadata Manager](https://emby.media/support/articles/Metadata-manager.html) 亦说明：Library settings 内有图片下载选项（文中给出 Movies / TV Shows 库的选项截图）。

### 6.4 语言偏好（元数据语言 / 图片语言）

**官方文档层面的证据：**

- 【L2】[Emby Blog: How to guide: Metadata](https://emby.media/community/blogs/entry/581-how-to-guide-metadata/) 原文："**Language Preferences**: Specify the language for **metadata and artwork** to match your preference."
  → 官方博客确认**元数据语言与图片语言是两个可分别/一并指定的偏好**。
- 【L1】[Library Setup](https://emby.media/support/articles/Library-Setup.html) 记载了库级**字幕**语言选择（"you can select the subtitle language that Emby server will attempt to download"），但**未**在该页文字中说明元数据语言/图片语言下拉框。
- 【L1-API】权威字段：
  - 库级（`Configuration.LibraryOptions`）：**`PreferredMetadataLanguage`**、**`MetadataCountryCode`**
  - 服务端级（`Configuration.ServerConfiguration`）：`PreferredMetadataLanguage`、`MetadataCountryCode`、`MetadataPath`、`MetadataNetworkPath`、`ImageSavingConvention`（枚举 `Legacy` | `Compatible`）、`SaveMetadataHidden`、`ImageExtractionTimeoutMs`
  - 条目级（`BaseItemDto`）：`PreferredMetadataLanguage`、`PreferredMetadataCountryCode`
  - 取远程图片时可指定：`GET /Items/{Id}/RemoteImages` 参数 **`IncludeAllLanguages`**（boolean）
  来源：[swagger.emby.media/openapi.json](https://swagger.emby.media/openapi.json)
- **"ImageLanguagePreference" 这个字段名**：在官方 OpenAPI spec 中检索 **不存在**（检索 `"[A-Za-z]*ImageLanguage[A-Za-z]*"` 零命中）→ **未能确认**该字段名。
- **"Preferred image language" 作为一个 UI 设置确实存在**：【L2】用户帖 [TMDb posters: no language fallback…](https://emby.media/community/topic/146202-tmdb-posters-no-language-fallback-person-bio-missing-after-bulk-refresh/)（Emby Server 4.9.3.0）原文反复提到 "**Preferred image download language is set to English**"，并描述其行为：**图片查询按该语言过滤，且（当时）没有语言回退**，语言设成英文时日/韩片可能拿到 0 张海报。Emby 管理员 **Luke** 在同帖回复：**"Hi, we plan to add fallback options in future updates."**
- 图片语言在 TMDB 请求中的实际体现（来自服务端日志原文，【L2】同帖与 [plugins MovieDb issue](https://emby.media/community/topic/135571-plugins-moviedb-issue/)）：
  `GET https://api.themoviedb.org/3/tv/1415?api_key=…&append_to_response=alternative_titles,reviews,credits,images,keywords,external_ids,videos,content_ratings,episode_groups&language=zh-CN&include_image_language=zh-CN,null`
  → 可见服务端把元数据语言映射为 `language=`，图片语言映射为 `include_image_language=<lang>,null`。

> **注意**：**官方知识库中不存在名为"Metadata language"或"Preferred image language"的专页** → 任务书请求的"Official doc URL for Metadata language / Preferred image language"**不存在** → 请标注**未能确认（无官方专页）**，退而引用官方博客 + 官方论坛官方人员发言 + 官方 OpenAPI 字段名。

---

## 7. 媒体库扫描触发方式

### 7.1 实时监控（RTM）

【L1】[Library Setup § Enable real-time monitoring](https://emby.media/support/articles/Library-Setup.html) 原文：

> "To have Emby monitor changes to files and addition of content, **real-time monitoring should be enabled**."
> **Important**: "**This option is only available on supported file systems. You should restart your Emby Server after changing this option.**"

→ 两点硬信息：**仅在受支持的文件系统上可用**；**修改该选项后需重启 Emby Server**。

【L1-API】该开关的字段名：`Configuration.LibraryOptions.EnableRealtimeMonitor`（库级，每库独立）。来源：[swagger.emby.media/openapi.json](https://swagger.emby.media/openapi.json)。

【L1】另有一个实际运维坑（官方文档收录）：[Synology NAS](https://emby.media/support/articles/Synology-NAS.html) 指出，若媒体存放在 Synology NAS 而 RTM 找不到新增内容，需**调整 Synology 的 Inotify 与 Watches 设置**，并链接到官方论坛帖 [Fix for RTM not working caused by limited inotify instances/watches](https://emby.media/community/index.php?/topic/106276-fix-for-rtm-not-working-caused-by-limited-inotify-instanceswatches/)。

### 7.2 定时任务（Scheduled Tasks）与触发条件

【L1】[Scheduled Tasks](https://emby.media/support/articles/Scheduled-Tasks.html)

- 入口：Dashboard → **Scheduled Tasks**。
- 原文：任务 "Many of them take too long to be run within the normal library scan, so that's why we make them available as configurable scheduled tasks."（**"normal library scan"是一个独立存在的概念**，部分耗时操作被拆成可配置的定时任务。）
- **任何任务都可随时手动运行**：点右侧播放按钮。
- **插件可注册自己的定时任务**（原文举例：Trakt）。
- **可用的触发器（官方完整枚举）**：
  - Daily at set time
  - Weekly at set day and time
  - **Interval (Based on a number of hours)**
  - At application startup
  - When the server resumes from sleep
- 触发器可删（点减号），可加（"Add Trigger"）。

> **未记载**："Scan media library" 的**出厂默认间隔小时数**在官方文档中**没有给出** → **未能确认**具体默认值。
> 【L1-API】但可确认存在**按天**的自动刷新配置字段：`Configuration.LibraryOptions.AutomaticRefreshIntervalDays`（库级）。来源：[swagger.emby.media/openapi.json](https://swagger.emby.media/openapi.json)。

### 7.3 手动扫描

【L1】[Backup & Restore to New System](https://emby.media/support/articles/Backup-Restore-To-New-System.html) 与 [Add New Server](https://emby.media/support/articles/Backup-Restore-Add-New-Server.html) 均记载：Server Settings → **Library** 页 → 点 **"Scan Library Files"**。

【L1】[Library Setup](https://emby.media/support/articles/Library-Setup.html) 的 Advanced Settings 中包含（原文列举）：
- **Prefer embedded titles over filenames**
- **Extract chapter images during the library scan**
- **Enable Open Subtitles subtitles**

【L1-API】对应的库级布尔/整型字段还包括：`EnableChapterImageExtraction`、`ExtractChapterImagesDuringLibraryScan`、`DownloadImagesInAdvance`、`EnablePhotos`、`EnableEmbeddedTitles`、`EnableAutomaticSeriesGrouping`、`ImportMissingEpisodes`、`SaveLocalMetadata`、`SaveLocalThumbnailSets`、`SeasonZeroDisplayName`、`CollapseSingleItemFolders`、`EnableArchiveMediaFiles`、`ThumbnailImagesIntervalSeconds`、`MinResumePct`/`MaxResumePct`/`MinResumeDurationSeconds`。来源：[swagger.emby.media/openapi.json](https://swagger.emby.media/openapi.json)。

### 7.4 扫描时"新增媒体"的日期判定

【L1】[New Media Date Handling](https://emby.media/support/articles/New-Media-Date-Handling.html)：两种可选——用**扫描入库日期**，或用**媒体文件自身的创建时间戳**。设置路径：Library → 右上 "Advanced" 菜单。官方举例与使用建议（新建库指向既有媒体时建议先用文件创建日期）。**注意：改完必须点 Save。**

### 7.5 条目识别与扫描的关系

【L1】[Quick Start](https://emby.media/support/articles/Quick-Start.html) 原文（这是官方对"扫描器如何识别"的总体说明）：

> "**Emby identifies your media according to folder structure, file name, and type of library to which it is assigned.** Once identified, Emby downloads a rich assortment of information about your media including **ratings, cast, descriptions & poster art**. It can often provide other content media such as movie trailers."

→ **三大识别输入：目录结构 + 文件名 + 库内容类型**。这是报告里最值得直接引用的一句官方定义。

---

## 8. Emby Premiere / 授权

### 8.1 价格与许可

【L1】[Emby Premiere](https://emby.media/premiere.html)（页面抓取时的标价，请以实际页面为准）：

| 方案 | 价格 |
|---|---|
| Monthly | **US$4.99 / 月**（自动续费） |
| 1 Year | **US$54 / 年** |
| Lifetime | **US$119 / 一次性** |

- 原文：许可"are for **single household use** and carry a **30 device limit**"。超过需看 [Extended Premiere Options](https://emby.media/premiere-ext.html)。
- 原文强化："**Emby is NOT a media streaming service.** We provide no content."；"**Please do not purchase Premiere unless you are using your own installed server and your own content.**"
- 购买即视为接受 [Emby Premiere Terms of Service](https://emby.media/premiereterms.html)。

### 8.2 Premiere 到底 gate 了什么（权威矩阵，【L1】）

来源：[Emby Premiere Feature Matrix](https://emby.media/support/articles/Premiere-Feature-Matrix.html)

页面开头定义：**"Emby Premiere is tied to a server by installing a key in its configuration. Therefore, all of the Premiere features below apply when connected to a server with a valid Premiere key. The server connected at the time governs Premiere status."**（Premiere 绑定在**服务器**上，通过在服务器配置中安装 key 生效。）

**免费（Free）与 Premiere 相同、即不被 gate 的项目**（原文矩阵）：
- **Browse Media** ✔ / ✔
- **Library Organization and Metadata Gathering** ✔ / ✔ ← **元数据刮削与库组织不收费**
- **Users and user management** ✔ / ✔
- **Parental Controls** ✔ / ✔
- **Emby Connect (simplified remote login)** ✔ / ✔ ← **Emby Connect 不收费**

**仅 Premiere（Free 列为空）的项目**：
Intro Skipping、Cinema Intros、**Limit the number of concurrent video playback sessions**、Live TV、Free Guide Data (US Canada and UK)、DVR、**HDR Tone Mapping When Transcoding**、**In-playback subtitle search**、CoverArt Plug-in、Backup and Restore Plug-in、**Downloads & Sync**、LDAP Integration、Content Conversion、Smart Home (Alexa/Google Home)、Podcasts、Webhooks、Theme Songs and Videos、Lyrics display、Themes for Clients。

**Hardware Accelerated Transcoding**：Free 栏为 `*`，Premiere 栏 ✔。脚注原文：
> **"\* Nvidia Shield and WD NAS devices do not require Premiere for HW Acceleration"**

（即：硬件加速转码**受 Premiere 限制**，但 **Nvidia Shield 与 WD NAS 设备例外，无需 Premiere**。）

**按平台**（原文矩阵）：
- PC / Mobile Browser：Full Playback 免费；Live TV、TV Mode 需 Premiere。
- Emby Windows / Emby Linux / Emby for MacOS / Game Consoles (Xbox, PS4)：Free 仅 **Limited Playback (one-minute only)**；Full Playback 需 Premiere。
- Mobile Apps (iOS / Android)：Free 为 one-minute；**Full Playback 可单独 "App Unlock"**（矩阵中 App Unlock 列有 ✔）或 Premiere；CarPlay/Android Auto、Apple Watch、Camera Upload、Offline Media、Live TV 需 Premiere。
- Android on TV / Fire TV：Full Playback 免费（脚注 `*` **"Playback for up to 5 TV devices"**）；Live TV、Offline Media 需 Premiere。
- Roku / Apple TV / Smart TV (LG, Samsung)：Full Playback 免费；Live TV 需 Premiere。

【L1】[Emby Premiere](https://emby.media/premiere.html) 页面另列举 Premiere 卖点：Offline Media、Emby DVR、Free Apps（Android/Fire TV/iOS/Xbox One/PS4 全功能）、Hardware Accelerated Transcoding、Cinema Intros、CarPlay/Android Auto、Emby for TV、Automatically Convert Content、Cover Art、Backup and Restore、Folder Sync、Smart Home。
同一页面原文补充：**"Some apps have the option to unlock basic playback individually."**

**与元数据/命名相关的核心结论**：**库组织与元数据抓取（Library Organization and Metadata Gathering）在 Free 版即可用，不受 Premiere 限制** —— 来源见上矩阵。

---

## 9. 明确打不开 / 不存在的页面（请勿假设其内容）

以下 URL 均已实际请求并得到 **HTTP 404（GitHub Pages "Page not found"）**，**不存在**：

| URL | 结果 | 说明 |
|---|---|---|
| `https://emby.media/support/articles/Metadata.html` | **404** | 任务书假设的"元数据"总页**不存在**。官方"Manage Metadata"实际入口是 `Metadata-manager.html` |
| `https://emby.media/support/articles/Home-Video-Naming.html` | **404** | **不存在**。Home Video 的命名规则写在 `Movie-Naming.html` 内（该页开头即写 "This file naming guide applies to movies, home videos and music videos."） |
| `https://emby.media/support/articles/Metadata-Manager.html` | **404** | **大小写敏感**！只有 `Metadata-manager.html`（小写 m）返回 200。引用时务必用正确大小写 |
| `https://emby.media/support/articles/` | **404** | 目录无 index 页。文章索引在 `https://emby.media/support/` 与 `https://emby.media/support/articles/Home.html` |
| `https://emby.media/support/articles/3D-videos.html` | **404** | **大小写敏感**！`toc.yml` 里写的是 `3D-videos.md`，但线上正确 URL 是 `3D-Videos.html`（大写 V） |
| `https://dev.emby.media/reference/RestAPI/BaseItemDto.html` | **404** | 我猜测的 BaseItemDto 参考页路径**不存在**。`dev.emby.media/reference/index.html` 只给出两个入口：`RestAPI.html` 与 `pluginapi/Emby.Model.ProcessRun.html` |

> **大小写敏感性提醒**：`emby.media/support/articles/*.html` 是 **大小写敏感**的 GitHub Pages。已实测确认：`Metadata-manager.html` ✅ / `Metadata-Manager.html` ❌；`3D-Videos.html` ✅ / `3D-videos.html` ❌。**其余所有在附录 A 列出的 URL 均已实测返回 200**（`Strm-Files.html` 首次请求超时，重试两次均 200）。

**未能确认存在性的内容（非 404，而是"检索不到"）：**
- 官方知识库中**没有**：元数据提供方专页、NFO 专页、元数据语言专页、图片语言专页、分级制度一览表、绝对集号说明、Season 年份文件夹说明、BoxSets 文件夹说明、多季合并文件夹说明、图片推荐像素尺寸说明、`Scan media library` 默认间隔数值。
- 官方体系（知识库 + 插件清单）中**没有**：Zap2It、Screen scraping、"Emby Metadata"（作为影视元数据提供方）。

---

## 10. 未能确认清单（汇总，供报告直接引用）

以下各项，**在 Emby 官方文档中查无依据**，报告中应写"未能确认"或明确降级到 L2/L3 来源：

1. `movie.nfo` / `tvshow.nfo` / `season.nfo` / `episode.nfo` / `<videoname>.nfo` 的**官方**文件名规范 —— 官方文档零记载（nfo 全仓库仅 3 次顺带提及）。**只能引 L1-cat 的 "Nfo Metadata" 插件存在** + L2 的 Luke 表态（本地优先/刷新最后、NFO 不适用于照片）。
2. 各元数据提供方**具体贡献哪些字段**的官方对照表 —— 不存在。
3. **Zap2It**、**Screen scraping** 作为 Emby 元数据提供方 —— 官方体系中零命中。
4. **TheTVDB "在某版本起变为纯插件"** 的具体版本与官方公告 —— 未找到。只能说：TVDB 由 `Tvdb` 插件提供，且在新装服务器上是 TV 库默认首选（L2）。
5. **TMDB 要求用户自备 API key** —— 官方口径**否认**（Luke：key 内建于 Emby Server，用户无需配置）。2025 年之后是否变更，**未能确认**。
6. **GB（BBFC）/ JP / 其他国家的分级取值表** —— 官方文档无，社区也未找到可核验的源码转贴。**仅 US 与 DE 有 L3 级源码片段**。
7. **官方推荐/要求的图片像素尺寸**，以及**服务端缓存图片的固定缩放宽度数值** —— 官方文档与 API spec 均无。只能给出取图接口的 `MaxWidth`/`MaxHeight`/`Width`/`Height`/`Quality` 参数（L1-API）。
8. **`ImageLanguagePreference`** 这个字段名 —— 官方 OpenAPI 中不存在。UI 上的 "Preferred image download language" 有 L2 证据；API 层面的语言字段是 `PreferredMetadataLanguage` / `MetadataCountryCode`（库级与服务端级）与远程图片查询参数 `IncludeAllLanguages`。
9. **`Series/Season 2019/Series 2019-01-01.ext`（按年份建季文件夹）** 与 **绝对集号（absolute numbering）** —— 官方 TV Naming 页均无记载。
10. **`Collections\` / `BoxSets\` 媒体文件夹约定**、**多季合并文件夹** —— 官方文档无记载。
11. **"Scan media library" 定时任务的出厂默认间隔小时数** —— 官方文档未给出（只能确认存在 Interval trigger 与库级 `AutomaticRefreshIntervalDays` 字段）。
12. **官方"Metadata language"/"Preferred image language"专页 URL** —— 不存在；最接近的官方文字是 Emby Blog 的 "Language Preferences" 段落。

---

## 附录 A：本报告引用的全部可点击来源

**官方知识库（L1，均实测 HTTP 200）**
- https://emby.media/support/ （支持首页）
- https://emby.media/support/articles/Home.html （文档首页）
- https://emby.media/support/articles/Quick-Start.html
- https://emby.media/support/articles/Library-Setup.html
- https://emby.media/support/articles/Movie-Naming.html
- https://emby.media/support/articles/TV-Naming.html
- https://emby.media/support/articles/Music-Naming.html
- https://emby.media/support/articles/Audio-Book-Naming.html
- https://emby.media/support/articles/Book-Naming.html
- https://emby.media/support/articles/3D-Videos.html
- https://emby.media/support/articles/Trailers.html
- https://emby.media/support/articles/Media-Stubs.html
- https://emby.media/support/articles/Strm-Files.html
- https://emby.media/support/articles/Theme-Songs-Videos.html
- https://emby.media/support/articles/Subtitles.html
- https://emby.media/support/articles/Metadata-manager.html （注意小写 m）
- https://emby.media/support/articles/Identify.html
- https://emby.media/support/articles/Image-Editing.html
- https://emby.media/support/articles/Ordering-TV-Specials.html
- https://emby.media/support/articles/Collections.html
- https://emby.media/support/articles/AutoBoxSets.html
- https://emby.media/support/articles/Excluding-Files-Folders.html
- https://emby.media/support/articles/Parental-Controls.html
- https://emby.media/support/articles/New-Media-Date-Handling.html
- https://emby.media/support/articles/Scheduled-Tasks.html
- https://emby.media/support/articles/Backup-Restore-To-New-System.html
- https://emby.media/support/articles/Backup-Restore-Add-New-Server.html
- https://emby.media/support/articles/Backup-Using-Plugin.html
- https://emby.media/support/articles/Synology-NAS.html
- https://emby.media/support/articles/Live-TV-Manage-Channels.html
- https://emby.media/support/articles/Premiere-Feature-Matrix.html
- https://emby.media/premiere.html
- https://emby.media/premiere-ext.html
- https://emby.media/premiereterms.html

**官方开发者文档 / API（L1-API）**
- https://swagger.emby.media/openapi.json （spec: "Emby Server API", version 4.1.1.0；201 schemas）
- http://swagger.emby.media/?staticview=true
- https://dev.emby.media/
- https://dev.emby.media/doc/index.html
- https://dev.emby.media/reference/index.html

**官方插件仓库（L1-cat）**
- https://www.mb3admin.com/admin/service/EmbyPackages.json （219 个包）

**官方博客 / 官方论坛（L2；发言人身份可核验）**
- https://emby.media/community/blogs/entry/581-how-to-guide-metadata/ （Emby Blog，2024-12-25，sross44）
- https://emby.media/community/topic/121372-metadata-priority-local-vs-remote/ （Luke，2023-09-09：本地优先/刷新最后）
- https://emby.media/community/topic/147223-unable-to-generate-nfo-files/ （Luke：NFO 不适用于照片）
- https://emby.media/community/topic/141431-thetvdb-as-a-source/ （GrimReaper：TVDB 为新装 TV 库默认提供方）
- https://emby.media/community/topic/135571-plugins-moviedb-issue/ （Luke：无需配置 API key，内建于 Emby Server）
- https://emby.media/community/topic/146202-tmdb-posters-no-language-fallback-person-bio-missing-after-bulk-refresh/ （Preferred image download language 行为；Luke：计划加入回退）
- https://emby.media/community/forum/64-knowledge-base/ （论坛 KB 版块入口）

**社区（L3，仅作线索，不可当规范）**
- https://emby.media/community/topic/105402-format-of-age-rating/ （Happy2Play 转贴 `LoadRatings` 的 us / de 分级表）
- https://emby.media/community/topic/70312-naming-of-nfo-and-posters/ （用户列出第三方工具产出的 NFO/图片文件名）

**文档源仓库（用于确认页面存在性与 TOC 结构）**
- https://codeload.github.com/EmbySupport/Emby.Docs/tar.gz/refs/heads/master （master 快照，含 `toc.yml`）
- 站点每页页脚均提供 "View article source" 链接指向 `https://github.com/EmbySupport/Emby.Docs/blob/master/<Article>.md`

---

## 附录 B：报告写作时的三条硬提醒

1. **引用 `Metadata-manager.html` 时必须小写 m** —— `Metadata-Manager.html` 是 404。
2. **`Metadata.html` 与 `Home-Video-Naming.html` 不存在** —— 若原报告已引用这两个 URL，必须替换为 `Metadata-manager.html` 与 `Movie-Naming.html`。
3. **"Emby 官方文档规定 NFO 文件名"这句话不能写** —— 官方文档没有 NFO 页。可改写成："Emby 官方知识库确认可将元数据保存为 NFO 并写入媒体文件夹（库级开关），但**官方未公布 NFO 文件命名规范**；NFO 读取能力由官方插件 `Nfo Metadata` 提供；官方人员说明本地元数据在**正常扫描**时优先、在**刷新元数据**时排到最后。"
