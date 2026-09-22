# VidHub 身份核实 与 TMDB 刮削底座 技术报告

> 编写日期：2026-09-22（UTC）
> 编写方式：所有结论均来自**本次实际发起的网络请求**
> （GitHub API / codeload / TMDB 官方文档与 OpenAPI 规范 / TMDB API 条款 /
> TheTVDB 官方 OpenAPI / OMDb 官网 / VidHub 官方文档站 sitemap / Apple 官方 iTunes Lookup API）。
> **凡未能实际验证的，一律在文中标注「查不到」或「不确定」，并在第 5 节汇总。**
> 文中所有事实性陈述后附来源 URL；推测性内容显式标注「**推测**」；未实机验证的标注「**🧪 未实测**」。

---

## 0. 结论速览（先看这里）

| # | 问题 | 结论 | 置信度 |
|---|---|---|---|
| 1 | `github.com/nabilfarhann/Vidhub` 是什么？ | **PHP + MySQL 写的"YouTube 克隆"视频分享网站**，与影音播放器无关 | **确定**（GitHub API 原始元数据 + 已下载源码逐文件核实） |
| 2 | 它有没有"刮削 / TMDB"概念？ | **完全没有**。全仓库检索 `tmdb/themoviedb/tvdb/scraper/nfo/imdb/douban/metadata` 零命中 | **确定**（已下载完整源码后 grep） |
| 3 | 用户给的链接和 VidHub App 是同一个东西吗？ | **不是**。属于同名不同物，用户很可能混淆了 | **确定**（两个实体的性质、平台、语言完全不同） |
| 4 | 市面上有几个叫 "VidHub" 的东西？ | **至少三个**：①这个 GitHub 仓库 ②南京欧米的播放器 App（`com.mac.utility.media.hub`）③MOBILE ALCHEMY LTD 的另一个 App（`vidhub.app`） | **确定**（Apple 官方 iTunes Lookup API 实测） |
| 5 | 那款 VidHub 播放器 App 支持 TMDB 刮削吗？ | **官方明确确认**：*"Movie and TV metadata comes from … TMDB"* | **确定**（官方文档 + App Store 官方描述原文） |
| 6 | 它支持 Emby/Jellyfin/Plex/SMB/WebDAV 吗？ | **官方明确确认全部支持** | **确定**（官方文档原文） |
| 7 | 它支持杜比全景声 / 音轨切换吗？ | **官方文档全文零命中** → ⚪ **查不到**（官方只确认了 **Dolby Vision**，未提 Atmos/DTS） | **确定"查不到"**（≠ 不支持） |
| 8 | TMDB 刮削底座能否直接照着写代码？ | **能**。本文第 2 节的接口/字段/参数均取自 TMDB 官方 OpenAPI 规范原文 | **确定** |
| 9 | TMDB 缓存条款到底怎么写的？ | 官方原文：**"Cache, for longer than 6 months, any information obtained through or from TMDB or the TMDB APIs."**（即：**不得缓存超过 6 个月**） | **确定**（API 使用条款原文） |
| 10 | TMDB 有中国大陆（CN）分级吗？ | **没有**。电影分级 45 国、剧集分级 40 国，**均不含 CN** | **确定**（官方认证表接口实测） |

---

# 第一部分：GitHub 仓库 `nabilfarhann/Vidhub` 的真实身份

## 1.1 核实方法（可复现）

```bash
# 1) 仓库元数据
curl -s https://api.github.com/repos/nabilfarhann/Vidhub
# 2) 语言构成
curl -s https://api.github.com/repos/nabilfarhann/Vidhub/languages
# 3) 完整文件树（默认分支 master）
curl -s "https://api.github.com/repos/nabilfarhann/Vidhub/git/trees/master?recursive=1"
# 4) README 原文
curl -s -H "Accept: application/vnd.github.raw" https://api.github.com/repos/nabilfarhann/Vidhub/readme
# 5) 全量源码（GitHub API 匿名额度耗尽后改用 codeload）
curl -sL -o vh.tar.gz "https://codeload.github.com/nabilfarhann/Vidhub/tar.gz/refs/heads/master"
```

> 说明：核实过程中匿名 `api.github.com` 的 60 次/小时额度被耗尽（返回 `API rate limit exceeded`），
> 因此第 5 步改用 `codeload.github.com` 下载全量源码（6,568,197 字节），后续源码级结论均基于该压缩包解压后的文件。

## 1.2 仓库元数据（GitHub API 原始返回）

| 字段 | 值 |
|---|---|
| `full_name` | `nabilfarhann/Vidhub` |
| `description` | **`Video sharing website (YouTube clone) with PHP`** |
| `language` | **PHP** |
| `stargazers_count` | **89** ← 与线索中的"89 星"一致 |
| `forks_count` | 21 |
| `subscribers_count` | 8 |
| `created_at` | 2019-10-19T21:46:21Z |
| `pushed_at` | **2021-06-25T08:43:16Z**（最后代码提交） |
| `updated_at` | 2026-09-11T04:28:27Z（元数据变动时间，非代码提交） |
| `archived` | **false**（未归档） |
| `disabled` | false |
| `license` | **null（无许可证）** |
| `default_branch` | **`master`**（注意：不是 `main`） |
| `open_issues_count` | 3 |
| `has_pages` / `has_discussions` / `has_downloads` | false / false / false |
| `homepage` | `""`（空） |
| `topics` | `mysql`, `php`, `web-application`, `web-development` |
| Releases | **0 个** |
| Contributors | 仅 `nabilfarhann`（23 次提交） |

最近 5 次提交：

```
2021-06-25T08:43:10Z | Nabil Farhan | Fixed incompatice box-shadow format
2021-06-25T08:42:13Z | Nabil Farhan | Fixed incorrect css file path
2021-06-25T08:41:57Z | Nabil Farhan | Fixed incorrect css style path
2021-06-25T08:41:30Z | Nabil Farhan | Change PDO connection for localhost
2021-06-25T08:00:12Z | Nabil Farhan | Removed link to demo site  Due to some people upload explicit content in it
```

> **观察**：项目自 **2021-06-25 起再无代码提交**，无 release、无许可证、单作者。
> 最后一条提交信息显示作者**主动移除了 demo 站点链接**，原因是"有人往上面传色情内容"。
> 这对任何"拿来当生产依赖"的想法都是一个明确的负面信号。

## 1.3 技术栈（有源码依据）

| 层 | 技术 | 证据 |
|---|---|---|
| 语言 | PHP（PHP 7.2.9 时代） | `sql/vidhub.sql` 头部注释 `-- PHP Version: 7.2.9` |
| 语言占比 | PHP 110,166 / CSS 104,900 / SCSS 57,450 / JavaScript 8,257 / Hack 1,264（字节） | `/languages` API |
| 数据库 | MySQL / MariaDB 10.1.35 | SQL dump 头部 `Server version: 10.1.35-MariaDB`；`includes/config.php` 用 PDO |
| DB 访问 | **PDO**，但连接参数**硬编码** | `includes/config.php`：`new PDO("mysql:host=localhost;dbname=vidhub", 'root', 'root')` |
| 依赖管理 | **实际没有依赖**（composer 只声明 PHP 扩展） | `composer.json` 内容仅 `{"require":{"ext-mysqli":"*"}}`，`composer.lock` 的 `packages` 为空数组 |
| 前端 | Bootstrap + jQuery 2.2.4 + 一套 SCSS 编译出的 CSS（`styles/style.css` 82KB） | `css/`、`js/`、`scss/` 目录 |
| 视频托管 | **Cloudinary**（云上传 widget） | `upload.php`：`<script src="https://widget.cloudinary.com/v2.0/global/all.js">` + `cloudinary.openUploadWidget({...})` |
| 本地转码（可选） | **FFmpeg / ffprobe 静态二进制**，通过 `exec()` / `shell_exec()` 调用 | `includes/classes/VideoProcessor.php` 第 128/131/184/215 行 |
| 伪静态 | Apache `.htaccess`（`RewriteRule ^([^\.]+)$ $1.php`） | `.htaccess` |
| 时区 | `Asia/Kuala_Lumpur` | `includes/config.php` |

> **安全提示（事实陈述）**：`includes/config.php` 里数据库口令以明文 `'root','root'` 硬编码并提交进了仓库；
> `upload.php` 里 Cloudinary 的 `cloud_name` / `upload_preset` 也是占位符硬编码。这不影响你判断它是"YouTube 克隆"，但**不要在任何机器上直接部署**。

## 1.4 功能清单（README 的 checkbox + 源码能对上的）

README 的 "List of Features" 原文（全部已勾选）：

```
- [x] Upload / Delete / Edit Video
- [x] Comment / Like / Dislike Video
- [x] Profile Page / Subscribe
- [x] Comment and Delete Comment
- [x] Search Video
- [x] Total views / Upload Date
- [x] Trending / Subscription Pages
```

源码中可对应的文件：

| 功能 | 实现文件 |
|---|---|
| 上传 / 处理 | `upload.php`、`processing.php`、`includes/classes/VideoProcessor.php` |
| 编辑 / 删除视频 | `editVideo.php`、`includes/classes/VideoDetailsFormProvider.php` |
| 播放页 | `watch.php`、`includes/classes/VideoPlayer.php`、`VideoInfoSection.php` |
| 评论（含回复） | `includes/classes/Comment.php`、`CommentSection.php`、`ajax/postComment.php`、`ajax/getCommentReplies.php` |
| 点赞 / 踩 | `ajax/likeVideo.php`、`ajax/dislikeVideo.php`、`ajax/likeComment.php`、`ajax/dislikeComment.php` |
| 订阅 | `ajax/subscribe.php`、`includes/classes/SubscriptionsProvider.php` |
| 搜索 | `search.php`、`includes/classes/SearchResultsProvider.php` |
| 趋势页 | `trending.php`、`includes/classes/TrendingProvider.php` |
| 个人主页 | `profile.php`、`includes/classes/ProfileData.php`、`ProfileGenerator.php` |
| 登录 / 注册 | `signIn.php`、`signUp.php`、`includes/classes/Account.php`、`User.php` |
| 缩略图选择 | `upload.php` + `includes/classes/SelectThumbnail.php` + `ajax/updateThumbnail.php` |

## 1.5 目录结构（API 返回 144 条目，`truncated: false`）

```
.htaccess  404.php  README.md  composer.json  composer.lock
editVideo.php  home.php  index.php  likedVideos.php  logout.php
processing.php  profile.php  search.php  settings.php  signIn.php
signUp.php  subscriptions.php  trending.php  upload.php  watch.php
ajax/            (8 个文件：点赞/踩/评论/订阅/缩略图)
css/             (9 个第三方 CSS：bootstrap/animate/font-awesome/owl-carousel...)
fonts/           (FontAwesome + themify + classy 字体文件)
img/             (含 2.1MB 的 Vidhub.png、2.1MB 的 pleasewait2.gif)
img/core-img/  img/coverPhotos/  img/icons/  img/profileImage/
includes/        (config.php, footer.php, header.php)
includes/classes/ (25 个 PHP 类)
js/  js/bootstrap/  js/jquery/  js/plugins/
scss/  scss/mixins/  scss/utilities/
sql/vidhub.sql   (6,123 字节)
styles/          (404style.css, style.css 82KB, style.css.map)
```

**数据库 8 张表**（`sql/vidhub.sql`）：`categories`、`comments`、`dislikes`、`likes`、`subscribers`、`thumbnails`、`users`、`videos`。

`videos` 表结构（**这是判定"有没有刮削"的关键证据**）：

```sql
CREATE TABLE `videos` (
  `id` int(11) NOT NULL,
  `uploadedBy` varchar(50) NOT NULL,
  `title` varchar(100) NOT NULL,        -- 上传者手填
  `description` text NOT NULL,          -- 上传者手填
  `privacy` int(11) NOT NULL,
  `filePath` varchar(250) NOT NULL,
  `category` int(11) NOT NULL,
  `uploadDate` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `views` int(11) NOT NULL,
  `duration` varchar(10) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=latin1;
```

`categories` 表预置数据就是 **YouTube 的 15 个官方分类**（Film & Animation / Autos & Vehicles / Music / … / Nonprofits & Activism），
这进一步印证它是"模仿 YouTube"的定位。

## 1.6 README 摘要（原文要点）

作者自述与使用说明（意译＋关键原文保留）：

1. **"Video sharing website (YouTube clone)"** —— 开篇即自认为 YouTube 克隆。
2. **"Vidhub is a video sharing website done using PHP and MySQL."**
3. 集成 **Cloudinary**：*"All videos that upload to Vidhub will be stored to Cloudinary."*
4. 部署步骤：克隆 → 建 `vidhub` 数据库 → 改 `includes/config.php` 的 host/user/password → 导入 `vidhub.sql` → 注册 Cloudinary 并**开启 unsigned uploading** → 把 cloud name / unsigned preset 填进 `upload.php` 的 Cloudinary widget → 运行。
5. 需要自行下载 **FFmpeg static** 放进 `ffmpeg` 文件夹（"for conversion of video and to generates Thumbnail"）。
6. 若要改为本地存储：把 `includes/classes/VideoProcessor-localhost` 重命名为 `VideoProcessor` 覆盖，并手工建 `uploads/videos/thumbnails` 三级目录。
7. 联系方式：`nabilfarhan.dev@gmail.com`。

## 1.7 与"影音播放器 VidHub"的关系：**同名不同物**

| 维度 | `nabilfarhann/Vidhub`（你给的链接） | VidHub App（那款播放器） |
|---|---|---|
| 形态 | **Web 站点**（PHP + MySQL + Apache） | **iOS / iPadOS / macOS / tvOS 客户端 App** |
| 干什么 | 用户**上传**视频、看别人的视频、点赞评论订阅 | 播放**你自己的**本地/网盘/媒体服务器影片，刮削海报墙 |
| 元数据 | **无任何刮削**，标题/简介靠上传者手填 | 主张自动刮削（详见 1.8） |
| 存储 | Cloudinary 云端 / 本地 `uploads/` | 读你已有的文件，不托管 |
| 代码可见性 | 开源（本仓库） | 闭源商业 App |
| 最后活动 | 2021-06 | 持续更新（见 1.8） |

**结论：用户很可能把两者搞混了。** 名字都叫 "Vidhub"/"VidHub"（大小写不同），
但一个是 2019 年的 PHP 课程级项目，一个是商业播放器 App；两者在功能、平台、代码上没有交集。

### 🔴 检索陷阱：市面上有 **三个** 不同的 "VidHub"

本次核实过程中发现，名字里的 "VidHub" 至少对应**三个互不相关的实体**，这是极易混淆的根源：

| # | 实体 | 标识 | 性质 |
|---|---|---|---|
| 1 | `nabilfarhann/Vidhub` | GitHub，PHP | YouTube 克隆网站（你给的链接） |
| 2 | **VidHub**（南京欧米软件） | App Store id `1659622164`，bundle `com.mac.utility.media.hub` | **就是那款影音播放器 App**，本文 1.8 节的主角 |
| 3 | **VidHub**（MOBILE ALCHEMY LTD） | App Store id `6761185000`，bundle **`vidhub.app`** | **另一家公司的另一个 App**，功能集不同 |

> **第 3 个实体的证据**（本次通过 Apple 官方 iTunes Lookup API 取得）：
> `https://itunes.apple.com/lookup?id=6761185000&country=cn` 返回：
> `trackName: VidHub`、**`sellerName: MOBILE ALCHEMY LTD`**、**`bundleId: vidhub.app`**、
> `version: 1.2`、`releaseDate: 2026-03-31`，描述中含 "Jellyfin、Emby 或 Plex"、"SMB 或 NFS"、"DLNA/UPnP" 等。
> **与南京欧米的 VidHub 是两个不同的 App**（销售方、bundle id、版本、描述均不同）。
>
> ⚠️ **因此**：搜索 "VidHub" 时命中的评测/讨论**可能说的是第 3 个 App 而不是第 2 个**。
> 引用任何第三方说法前，**必须先确认它指的是哪一个 bundle id**。

**关于开源**：本次未找到任何可确认属于第 2 个实体（南京欧米）的公开源码仓库。
App Store 页面亦未提供源码链接。→ **该 App 应为闭源**（此为**推断**，非官方声明）。

## 1.8 那款 VidHub App（南京欧米，id `1659622164`）的公开信息

> **本节证据分级标注**：
> - 🟢 **官方宣传/文档确认**：来自厂商官网、官方文档站、App Store 官方字段（经 Apple 官方 API 取得）
> - 🟡 **用户社区说法**：论坛、评测、第三方博客（**未经厂商确认**）
> - ⚪ **查不到**：本次未能取得可引用来源
> - 🧪 **未实测**：有官方文字，但我**没有安装/运行该 App 验证**

### 1.8.1 取证方式（可复现，与上一版结论的关键差异）

> **更正说明**：我最初尝试用 `web_fetch` 抓官网，只拿到 JS 导航壳，一度判定"大面积查不到"。
> **改用以下方法后取得了完整官方原文**，因此本节结论已全面更新：

| 方法 | 说明 |
|---|---|
| **官方文档站 sitemap** | `https://vidhub.okaapps.com/sitemap.xml` → `sitemap-posts.xml`，**列出全部 15 篇官方文档** |
| **官方文档页正文** | 这些页面**服务端渲染**，`curl` 即可取全文（此前用 `web_fetch` 失败是工具/渲染差异，非页面问题） |
| **Apple 官方 iTunes Lookup API** | `https://itunes.apple.com/lookup?id=1659622164&country=us`（及 `&country=cn`）→ **官方商品描述、版本、评分、bundle id** |

### 1.8.2 App 基本身份（🟢 官方，来自 Apple 官方 API）

| 字段 | US 区 | CN 区 |
|---|---|---|
| `trackName` | `VidHub -Video Library & Player` | `VidHub - 高清影片视频播放器，快速播放云盘网盘` |
| `sellerName` / `artistName` | **Nanjing Oumi Software Development Co., Ltd.** | 同 |
| `bundleId` | `com.mac.utility.media.hub` | 同 |
| `version` | **3.0.6** | 3.0.6 |
| `currentVersionReleaseDate` | **2026-09-12** | 2026-09-12 |
| `releaseDate`（首次上架） | **2023-05-18** | 2023-05-18 |
| `price` | **Free**（0.0） | 免费 |
| `averageUserRating` | 4.58（**3,068** 个评分） | 4.70（**17,911** 个评分） |
| `minimumOsVersion` | 16.0 | 16.0 |
| `primaryGenreName` | Entertainment / Utilities | 娱乐 / 工具 |
| 语言 | AR, NL, EN, FR, DE, IT, JA, KO, PT, RU, **ZH**(×2), SK, ES, TR, VI | 同 |
| `fileSizeBytes` | 178,254,848（≈170 MB） | 同 |
| 内容分级 | 4+ | 4+ |

> 官方站点版权署名：**"Copyright © 2026 Nanjing Oumi Software Development Co., Ltd."**（与 Apple 的 `sellerName` 一致）

### 1.8.3 官方文档页清单（sitemap 实测，共 15 篇，均标注 "Last updated on Aug 5, 2026"）

```
https://vidhub.okaapps.com/what-does-vidhub-do/            ← 功能总览
https://vidhub.okaapps.com/beginners-guide/                ← 新手引导
https://vidhub.okaapps.com/advanced-for-beginners/         ← 进阶（文件源/媒体服务器）
https://vidhub.okaapps.com/use-the-media-library/          ← 媒体库（Continue Watching 等）
https://vidhub.okaapps.com/use-file-source/                ← 文件源管理
https://vidhub.okaapps.com/vidhub-video-file-naming-conventions/  ← ★ 命名规则
https://vidhub.okaapps.com/modifying-file-names-single-file-batch-rename/
https://vidhub.okaapps.com/edit-video-information-in-the-media-library-correction/  ← ★ 手动纠错
https://vidhub.okaapps.com/video-playback-interface/       ← 播放界面
https://vidhub.okaapps.com/settings-overview/              ← ★ 设置总览
https://vidhub.okaapps.com/synchronize/                    ← ★ iCloud 同步
https://vidhub.okaapps.com/how-to-request-an-apple-app-refund/
https://vidhub.okaapps.com/fix-blurry-dark-scenes-on-apple-tv/
https://vidhub.okaapps.com/how-to-restore-your-in-app-purchase/
https://vidhub.okaapps.com/3rd-party-app-integration/      ← ★ 第三方 App 联动（URL scheme）
```

### 1.8.4 功能清单（逐条对应任务要求，全部 🟢 官方原文）

#### ① 刮削（TMDB）—— 🟢 **官方明确确认**

出自官方《VidHub Video File Naming Conventions》原文：

> *"VidHub automatically finds video files in your file sources and uses their file names to scrape movie or TV information, posters, cast details, and more. **The file name is the only factor used for automatic matching; folder names and folder structure do not affect it. Movie and TV metadata comes from the well-known open-source website TMDB.**"*

出自官方《Settings Overview》原文：

> *"**Refresh Online Metadata**: Retrieves movie and TV information **from TMDB** again. Run this after changing the Online Metadata Language to download data in the new language."*
> *"**Online Metadata Language**: Choose the language for online movie and TV metadata, or follow the system language automatically."*

出自 App Store 官方描述（US 区）原文：

> *"Note: **The metadata and artwork are sourced from TMDB**, a movie and TV show database that is maintained by the community."*

出自 App Store 官方描述（CN 区）原文：

> *"注意：影片资料和封面是由 TMDB 提供，TMDB 是一个由网络社区维护的电影和电视节目免费数据库。"*

**结论**：VidHub **确认使用 TMDB**，且**在 App Store 描述中做了 TMDB 署名**（与本文 2.7.4 的署名要求一致）。

**🔴 一个对你实现刮削器极有价值的官方设计事实**：

> *"**The file name is the only factor used for automatic matching; folder names and folder structure do not affect it.**"*
> *"For extras and special episodes, start with the series title, use **Season 00**, and specify the episode number."*

即：**VidHub 只用文件名匹配，不看目录结构**（`Season 01/` 目录对它无效）。
这与本文第三部分 3.2 节"从父目录兜底取季号"的设计**相反**。
**两种策略各有取舍**：只看文件名更简单可预测；看目录能救回"`Season 01/01.mkv`"这类无信息文件名。
→ **建议你两者都做，但把"文件名"作为主信号、"目录"作为兜底**，并在 UI 里说明。

#### ② 连接 Emby / Jellyfin / Plex / WebDAV / SMB —— 🟢 **官方明确确认**

出自官方《What does VidHub do?》原文：

> *"VidHub supports managing video files on **Aliyun Disk/Baidu Cloud/Google Drive/Dropbox/One Drive**, supports **samba(smb)/webdav** protocols, supports NAS network storage such as Synology, supports local files, and supports **Plex/Emby/Jellyfin media servers**."*

出自官方《Advanced for Beginners》原文（更细）：

> *"**Network Share**: Input the 'Server Address' (URL), Port, Username, Password, Path (optional) … **currently supports WebDav and SMB protocols**."*
> *"**Cloud Drive**: … Currently supports **Alibaba Cloud Drive/Baidu Cloud Drive/Dropbox/OneDrive/Google Drive/Premiumize**, etc."*
> *"**Media Servers**: VidHub supports adding **Plex/Emby/Jellyfin** media servers. By providing the server address, username, password, and other required information, you can log in and add a media server."*

出自 App Store 官方描述：

> *"Access your personal media libraries through **SMB/CIFS and WebDAV**, or connect your authorized accounts from supported cloud storage services, including **Dropbox, Google Drive, OneDrive, Baidu Netdisk, Aliyun Drive, China Mobile Cloud Drive, and 115**."*
> *"Support **direct-link to Emby, Jellyfin, Plex** media server."*

官方中文描述（zh.okaapps.com 首页）：

> *"支持管理本地/smb服务器/webdav服务器/阿里云盘/百度网盘/移动云盘/123 网盘视频文件，支持群晖等NAS网络存储。"*
> *"支持直连Emby、Jellyfin、Plex媒体库 - 连上即可播放"*

**云盘清单**（合并官方多处表述）：阿里云盘、百度网盘、移动云盘、115、123 网盘、Dropbox、OneDrive、Google Drive、Premiumize（官方措辞均带 *"etc."*，**不保证完整**）。
**网络协议**：**SMB/CIFS、WebDAV**（官方明确列出）。
**⚪ 未提及**：FTP / SFTP / NFS / DLNA —— **官方文档中未出现**（≠ 不支持，但**没有官方依据**）。

#### ③ 播放能力 —— 🟢 部分确认，⚪ 部分查不到

**🟢 官方确认的**：

| 能力 | 官方原文 | 来源 |
|---|---|---|
| HDR | *"including playback speed adjustment, subtitles, screenshots, **HDR, Dolby Vision**, and more."* | what-does-vidhub-do |
| **杜比视界（Dolby Vision）** | 同上 | what-does-vidhub-do；官方中文站 *"支持倍速，字幕，截图，HDR, 杜比视界"* |
| 高清 / 4K | *"High-quality video playback for **HD, 4K, HDR**, and various other resolutions"* | App Store 描述 |
| **倍速播放** | *"playback speed adjustment"*；*"**Always Remember Playback Speed**: Reuse the speed selected before you last exited playback."* | what-does-vidhub-do；Settings Overview |
| **字幕（内封+外挂）** | *"supports built-in subtitles in videos and can mount a variety of external subtitle formats, such as **SRT, SSA, ASS, SUB**."* | what-does-vidhub-do |
| 字幕自动加载规则 | *"Choose the rule used to load external subtitle files automatically. Enable or disable automatic loading of embedded subtitles. Choose the preferred embedded-subtitle language…"* | Settings Overview |
| **在线字幕搜索** | *"Choose the language used for **online subtitle searches**."* | Settings Overview |
| 硬解 | *"**Hardware Acceleration**: Use hardware acceleration to improve performance."* | Settings Overview |
| 连续播放（自动下一集） | *"**Continuous Playback**: Automatically play the next episode when enabled."* | Settings Overview |
| 后台播放 | *"**Background Playback**: Continue playing when the app moves to the background."* | Settings Overview |
| 截图 / GIF 录制 | *"**Screenshots and Recording**: Choose the screenshot image format. Choose the GIF frame rate. Choose the GIF resolution."* | Settings Overview |
| 视频格式 | *".mkv、.avi、.mp4、.mov、.rmvb、.wmv, and more"* | App Store 描述 |

**⚪ 查不到的**：

| 项 | 状态 |
|---|---|
| **杜比全景声（Dolby Atmos）** | ⚪ 官方文档**全文未出现** "Atmos" |
| **DTS / DTS:X** | ⚪ 官方文档**全文未出现** |
| **TrueHD** | ⚪ 未出现 |
| 音轨切换（多音轨选择） | ⚪ 官方设置页**未列出音轨切换项**（只列了字幕项）。**不确定**是否有此功能 |
| 字幕格式 VTT | ⚪ 官方只列 SRT/SSA/ASS/SUB（*注：第 3 个实体 MOBILE ALCHEMY 那个 VidHub 提到了 VTT，**不要混淆**）* |
| HDR10+ / HDR 具体制式 | ⚪ 官方只说 "HDR"，**未细化制式** |
| 是否支持 PGS/SUP 图形字幕 | ⚪ 未提及 |

> **对第③点的诚实总结**：官方**只承诺到 "HDR、Dolby Vision"**（视频动态范围），
> **音频侧的杜比全景声/DTS 完全没有官方文字**。
> 社区评测里常见的"支持杜比全景声"说法 → 🟡 **用户社区说法，我未取得官方依据**。

#### ④ 媒体库与海报墙 —— 🟢 官方确认

| 能力 | 官方原文 |
|---|---|
| 媒体库概念 | *"**Media Library**: All the displayed movies are sourced from the video files in the 'File Sources' section."* |
| 刮削定义（官方自述） | *"**Definition: Scraping refers to searching online for movie/show information and posters based on the video file names**, and then adding the search results to the media library after organizing them."* |
| 分类/排序 | *"customizable sorting options by **type, date, rating**, and more"*；*"Media Sort Order / File Sort Order"* |
| 全局搜索 | *"support **one-click global search**"* |
| 分类可编辑 | *"'Edit Media Library' allows you to 'show/hide/sort' categories within the media library."* |
| 隐藏剧透 | *"**Hide Spoilers**: When enabled, episode descriptions are hidden on series detail pages."* |
| 播放列表 / 收藏 | *"Playlists"*、*"Add to Favorites"*（默认隐藏，需在 Edit Media Library 中启用） |
| 其他分类 | 未刮削成功的会进 **"Other"** 分类（*"incorrectly scraped or classified as 'Other'"*） |

**人工纠错流程（官方原文，对你的"人工确认 UI"极有参考价值）**：

> *"Long press … the video poster in the media library, then select the '**Edit Information**' option."*
> *"**Input Box**: The input box … allows you to search for a movie name. Select the corresponding movie from the search results, and the information of the movie being edited will be replaced with the selected movie."*
> *"**Change to Movie** / **Change to TV Show**: For movies in other categories, after finding the correct movie information through search, apply it to move the movie to the … category."*
> *"**Use Original File Data**: … will move the movie to the other category."*
> *"**TV Series**: … select any episode from a specific season … After searching and selecting the TV series you want to modify, make the necessary changes. Wait for a moment, and **the information of all other episodes in the same season will be automatically updated**."*

→ 这**几乎就是**本文第三部分 3.3.3 节"人工确认 UI"的设计：**搜索选择 + 改类型 + 批量应用到同季**。

#### ⑤ 播放进度同步 —— 🟢 **官方确认（机制明确）**

出自官方《Synchronize》原文（**全文很短，这是最重要的几段**）：

> *"**iCloud Sync**: iCloud can synchronize **file sources, the Media Library, watch history, playlists, favorites, media servers**, and other information across multiple devices."*
> *"Sign in with the same iCloud account on every device. Enable iCloud Drive on every device. In VidHub, open **Settings > Sync > iCloud Sync**."*
> *"If iCloud storage is full or insufficient, imported files may be missing or videos may fail to play correctly."*

出自官方《Settings Overview》：

> *"**Sync** — iCloud Sync: Allow Media Library details to be synchronized through iCloud."*

**结论**：进度（watch history）同步**走 iCloud**，**不是**自建服务器，**也不是**回写到 Emby/Jellyfin/Plex。

**"继续观看"的官方行为（《Use the Media Library》原文）**：

> *"If you stop watching before a video ends, it appears in **Continue Watching** with its current progress. Click the white Play button to resume."*
> *"A completed movie moves to **Watched**."*
> *"For a TV series, **the next episode appears in Continue Watching after the current episode finishes. If the series has multiple seasons, finishing one season shows the first episode of the next season.** The series moves to Watched after all seasons are complete."*
> *"Long-press a video in Continue Watching and choose Mark as Watched. Three options: **Current Episode / Entire Season / Entire Series**."*
> *"If a title in Continue Watching is marked as watched and you later cancel that mark, **its previous playback history is not restored**."*

→ 这**正是**本文 3.5.2 / 3.5.6 节描述的"续播 + 下一集 + 整季标记"逻辑，可与你的设计互相印证。

#### ⑥ 定价 —— 🟢 部分确认

| 事实 | 来源 |
|---|---|
| App 本体**免费下载**（`price: 0.0`） | Apple API |
| 存在**内购/订阅**，权益名为 **"VidHub VIP"** | 官方《How to Restore Your In-App Purchase》原文：*"**VidHub VIP works across Apple's platforms**, including iPhone, iPad, Mac, and Apple TV. You can restore the same VIP entitlement on each platform."* |
| 是**订阅制**（含"活动订阅权益"表述） | 同上原文：*"Apple's billing system normally prevents the same Apple Account from being charged twice for the same active subscription entitlement."* |
| **具体价格、免费版与 VIP 的功能边界** | ⚪ **查不到**。Apple Lookup API 不返回内购价目表，官方文档亦未列出功能对比表。详见 5.1 |
| 中国大陆区有独立商店 | 官方原文：*"If VidHub was purchased from the **Mainland China store**, download it from that store…"* |

#### ⑦ 平台 —— 🟢 官方确认（含 Android！）

官方《What does VidHub do?》给出的系统要求：

| 平台 | 最低版本 |
|---|---|
| Mac | **macOS 10.15** 或更高 |
| iPhone | **iOS 14.1** 或更高 |
| iPad | **iPadOS 14.1** 或更高 |
| iPod touch | iOS 14.1 或更高 |
| Apple Vision | **VisionOS 1.0** 或更高 |
| Apple TV | **tvOS 14.0** 或更高 |

> ⚠️ **注意版本口径不一致**：官方文档说 iOS 14.1 / macOS 10.15，
> 而 **Apple Lookup API 返回 `minimumOsVersion: 16.0`**（当前 3.0.6 版）。
> 说明**最低系统要求随版本收紧过**，官方文档页（更新于 2026-08-05）**未同步更新**。
> → **以 App Store 的 16.0 为准**（那是安装时真正生效的）。

**Android 平台**：官方《Play Videos in VidHub from Third-Party Apps and Services》原文明确支持
**Android Mobile 与 Android TV**：

> *"The new `/play` method supports iPhone, iPad, Apple TV, Mac, **Android Mobile, and Android TV**."*
> *"Android Mobile and Android TV support only `/play`."*

且官方中文站自述 *"VidHub是一款**苹果/安卓全平台**视频播放器"*、英文站 *"VidHub is a video player for **all Apple/Android platforms**"*。

> ⚪ **但**：我**未查到** Android 版的 Google Play / 官网下载页链接（Apple Lookup API 只覆盖 Apple 平台）。
> Android 版的**分发渠道与版本号 → 查不到**。

#### ⑧ 官方文档未提及、但**其他来源**出现的功能（易混淆，单列）

| 功能 | 出现位置 | 归属 |
|---|---|---|
| **Trakt 登录 / Trakt Scrobbling** | 官方《3rd-party-app-integration》原文提到 *"These callbacks synchronize playback progress for this request and **do not depend on Trakt login or Trakt Scrobbling**."* | 🟢 **官方文字间接确认 VidHub 存在 Trakt 集成**（否则无需提及），但**具体支持范围未查到** |
| NFS、DLNA/UPnP、VTT 字幕 | App Store id **6761185000**（MOBILE ALCHEMY LTD）的描述 | 🔴 **属于第 3 个实体，不要归到南京欧米的 VidHub 上** |

### 1.8.5 一个对你**直接有用**的官方接口（可复用）

官方《Play Videos in VidHub from Third-Party Apps and Services》给出了一套**完整的
"外部 App 调起 VidHub 播放并拿回进度"** 的 URL scheme 协议。这对你做"网盘管理 → 播放器"联动**极具参考价值**：

```
open-vidhub://x-callback-url/play
```

| 参数 | 必填 | 官方说明 |
|---|---|---|
| `url` | 是 | *"Video URL to play"* |
| `position` | 否 | *"Starting position in seconds; must be a valid number from **0 through 31536000**"* |
| `filename` | 否 | *"File name or title shown in the player"* |
| `sub` | 否 | *"External subtitle URL"* |
| `x-success` | 否 | *"Callback after playback completes or the user exits playback"* |
| `x-error` / `x-cancel` | 否 | 失败 / 取消回调（**注意：取消只发生在"正式播放开始前"**） |
| `x-source` / `request-id` | 否 | 调用方标识 / 请求 id（原样回传） |

**`x-success` 回调参数**：`lastPlayedUrl`、`position`（*"Playback position on exit, in whole seconds rounded down"*）、`duration`、**`status`（`finished` 或 `stopped`）**、`request-id`

官方给的示例：

```
完成播放：
myapp://x-callback-url/success?lastPlayedUrl=...&position=599&duration=600&status=finished&request-id=movie-123
提前退出：
myapp://x-callback-url/success?lastPlayedUrl=...&position=38&duration=600&status=stopped&request-id=movie-123
```

官方**错误码表（逐字）**：

| Code | Meaning |
|---|---|
| 100 | Missing url parameter |
| 101 | Invalid video URL |
| 102 | Invalid position |
| 103 | Unsupported video URL scheme |
| 104 | Invalid callback URL |
| 200 | Playback failed |
| 201 | Player is busy and cannot accept another external request |
| 202 | Player cannot be presented |

> **对你项目的意义**：
> 如果你的 Python 桌面应用不想自己内嵌播放器，可以**按同样的设计思路**做一套自己的
> URL-scheme / IPC 协议，把播放交给用户已有的播放器（包括 VidHub），
> **并用回调把 `position` / `status` 拿回来写进 `播放进度` 表**。
> 官方这套协议的字段设计（`position` + `status: finished|stopped` + `request-id`）
> 可直接作为你的接口设计参考 —— **这是我本次核实中意外获得的最有复用价值的产出。**

---

# 第二部分：TMDB 刮削底座（可照着写代码）

> **文档获取方式的重要发现（强烈建议记录）**：
> TMDB 官方文档站在 HTML 里内嵌了一段提示：
> *"For AI agents: visit https://developer.themoviedb.org/llms.txt for an index of all pages formatted in Markdown and endpoints in OpenAPI. Append .md to any documentation page URL to get its markdown version."*
> 即：**任何文档页 URL 后加 `.md` 即可拿到干净的 Markdown 原文**。
> 本节的引用均通过该方式取得，避免了 JS 渲染导致的正文缺失。
>
> - 索引：<https://developer.themoviedb.org/llms.txt>
> - 示例：<https://developer.themoviedb.org/docs/rate-limiting.md>
>
> **接口/参数/字段的权威来源**：TMDB 官方 OpenAPI 规范
> （内嵌于各 `/reference/*` 页面的 `#ssr-props` 脚本中，`openapi: 3.1.0`，`filename: tmdb-api.json`，
> 规范内 `updated_at: 2026-03-23T14:27:00.500Z`，共 **148 个路径**）。
> 在线入口：<https://developer.themoviedb.org/openapi>

## 2.1 鉴权

来源：<https://developer.themoviedb.org/docs/authentication-application>（官方原文）

官方原文关键句：

> *"Application level authentication would generally be considered the default way of authenticating yourself on the API. **Version 3 is controlled by either a single query parameter, `api_key`, or by using your access token as a `Bearer` token.** You can request an API key by logging in to your account on TMDB and clicking here."*
>
> *"If you head into your account page, under the API settings section, you will see a new token listed called **API Read Access Token**."*
>
> *"Using the Bearer token has the added benefit that it is a single authentication process that you can use across both the v3 and v4 methods. **Both authentication methods provide the same level of access**, and which one you choose is completely up to you."*

### 两种用法对照

| 方式 | 写法 | 说明 |
|---|---|---|
| **v3 API Key**（查询参数） | `GET https://api.themoviedb.org/3/movie/11?api_key=<URLENCODED_API_KEY>` | API Key 需做 URL 编码；会出现在 URL / 访问日志里 |
| **v4 / Bearer Token**（推荐） | `GET https://api.themoviedb.org/3/movie/11`<br>`Authorization: Bearer <API_READ_ACCESS_TOKEN>` | 官方称为"默认方法"；v3/v4 通用；不进 URL |

官方 cURL 原文示例：

```bash
curl --request GET \
     --url 'https://api.themoviedb.org/3/movie/11' \
     --header 'Authorization: Bearer <<access_token>>'
```

**申请入口**：<https://www.themoviedb.org/settings/api>
（官方注明 *"the API registration process is not optimized for mobile devices so you should access these pages on a desktop computer and browser."* ——
见 <https://developer.themoviedb.org/docs/getting-started>）

**校验 Key 是否有效**：`GET /3/authentication`
（OpenAPI `operationId: authentication-validate-key`，summary "Validate Key"，
描述原文 *"Test your API Key to see if it's valid."*）。
成功返回示例：`{"success":true,"status_code":1,"status_message":"Success."}`
失效返回（401）：`{"status_code":7,"status_message":"Invalid API key: You must be granted a valid key.","success":false}`

**OpenAPI 里声明的安全方案**（原文）：

```json
"securitySchemes": {
  "sec0": { "type": "apiKey", "in": "header", "name": "Authorization",
            "x-bearer-format": "bearer" }
},
"security": [ { "sec0": [] } ]
```

> 注意：规范里只声明了 header 形式的 `Authorization`；`api_key` 查询参数形式仅存在于官方文档说明中，**未写入 OpenAPI 规范**。
> 两者官方都认可，但**新代码建议只用 Bearer**。

### 其他鉴权模式（本项目大概率不需要）

| 模式 | 说明 | 来源 |
|---|---|---|
| **User 会话** | `session_id` 查询参数，可让用户评分/维护收藏与 watchlist | <https://developer.themoviedb.org/docs/authentication-user> |
| **Guest Session** | 临时会话，用于游客评分 | <https://developer.themoviedb.org/docs/authentication-guest-sessions> |

> 自建刮削器**只需要 Application 级（Bearer）**，不要引入用户会话。

## 2.2 图片 CDN 与尺寸配置

### `/3/configuration`（`operationId: configuration-details`）

- **方法**：`GET`
- **路径**：`/3/configuration`
- **参数**：官方规范中**未记录任何参数**
- **描述原文**：*"Query the API configuration details."*

**响应示例（OpenAPI 规范内 `Result` 原文，逐字）**：

```json
{
  "images": {
    "base_url": "http://image.tmdb.org/t/p/",
    "secure_base_url": "https://image.tmdb.org/t/p/",
    "backdrop_sizes": ["w300", "w780", "w1280", "original"],
    "logo_sizes":     ["w45", "w92", "w154", "w185", "w300", "w500", "original"],
    "poster_sizes":   ["w92", "w154", "w185", "w342", "w500", "w780", "original"],
    "profile_sizes":  ["w45", "w185", "h632", "original"],
    "still_sizes":    ["w92", "w185", "w300", "original"]
  },
  "change_keys": ["adult", "air_date", "also_known_as", "…(共 57 项)"]
}
```

**可直接照抄的尺寸选择建议**（工程建议，非官方规定）：

| 用途 | 参数 | 选用尺寸 | 备注 |
|---|---|---|---|
| 海报墙（网格缩略） | `poster_sizes` | `w342` | 主流；`w185` 更省带宽但略糊 |
| 海报大图 / 详情页 | `poster_sizes` | `w500` | 详情页头部 |
| 背景图（详情页横幅） | `backdrop_sizes` | `w1280` | 4K 屏可上 `original` |
| 背景图（卡片小横幅） | `backdrop_sizes` | `w780` | |
| 演员头像 | `profile_sizes` | `w185` | `h632` 是竖版高的尺寸 |
| 剧集剧照 | `still_sizes` | `w300` | 集列表 |
| Logo（SVG/PNG） | `logo_sizes` | **只能 `original`** | 官方：*"For SVG's, you should call the original image size since we don't resize them."* |

> **务必运行时拉取 `/configuration`，不要把尺寸表写死**——
> 官方明确 `base_url` 与尺寸列表来自该接口（见下），且历史上有过变更。
> 但**必须准备一套硬编码兜底值**（即上面这份），保证首次离线/接口故障时仍能拼出 URL。

### 图片 URL 拼接规则

来源：<https://developer.themoviedb.org/docs/image-basics>（官方原文）

> *"In order to generate a fully working image URL, you'll need 3 pieces of data. Those pieces are a `base_url`, a `file_size` and a `file_path`."*
> *"The first two pieces can be retrieved by calling the `/configuration` API and the third is the file path you're wishing to grab on a particular media object."*

```
最终 URL = secure_base_url + file_size + file_path
```

官方给的完整示例（poster_path = `/1E5baAaEse26fej7uHcjOgEE2t2.jpg`，取 `w500`）：

```
https://image.tmdb.org/t/p/w500/1E5baAaEse26fej7uHcjOgEE2t2.jpg
```

**拼接实现要点**：
- 用 `secure_base_url`（`https://image.tmdb.org/t/p/`），**不要**用 `base_url`（http）。官方在 FAQ 中说：*"It's currently available API wide. This includes both the API endpoints and assets served via our CDN. **We strongly recommend you use SSL.**"*
- `file_path` 自带前导 `/`，`secure_base_url` 自带尾随 `/`，**直接字符串相加即可，不要再插 `/`**。
- 若 `poster_path` 为 `null`，说明该媒体在 TMDB 无海报，需回退（见 2.6）。

### 公司 / 电视台 Logo 的特殊规则

官方原文：

> *"Company and network logos are available in two formats, SVG and PNG. All of the `logo_path` fields will return a .png. This is to maintain backwards compatibility since SVG support was added after the fact."*
> *"When looking at the image methods there is a new field called `file_type` that will show you the original version of the asset that was uploaded. **For SVG's, you should call the original image size since we don't resize them.**"*

官方示例：

```
https://image.tmdb.org/t/p/original/wwemzKWzjKYJFfCeiB57q3r4Bcm.svg
https://image.tmdb.org/t/p/original/wwemzKWzjKYJFfCeiB57q3r4Bcm.png
https://image.tmdb.org/t/p/w500/wwemzKWzjKYJFfCeiB57q3r4Bcm.png
```

## 2.3 搜索与详情接口

以下所有路径均取自官方 OpenAPI 规范（`servers: [{"url": "https://api.themoviedb.org"}]`），
参数表来自规范中的 `parameters`，字段来自规范中的响应示例与 schema。

### 2.3.1 电影

#### `GET /3/search/movie` — 搜索电影

- `operationId`: `search-movie`
- 描述原文：*"Search for movies by their original, translated and alternative titles."*
- 返回顶层字段：`page`, `results`, `total_pages`, `total_results`

| 参数 | 必填 | 类型 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | **是** | string | — | 搜索词 |
| `include_adult` | 否 | boolean | `false` | |
| `language` | 否 | string | `en-US` | |
| `primary_release_year` | 否 | string | — | 只匹配**首要上映年** |
| `page` | 否 | integer | `1` | |
| `region` | 否 | string | — | ISO 3166-1，**仅为展示过滤器**（见 2.4.6） |
| `year` | 否 | string | — | 匹配上映年 |

**单条结果字段**（官方示例，Fight Club 等）：

```json
{
  "poster_path": "/IfB9hy4JH1eH6HEfIgIGORXi5h.jpg",
  "adult": false,
  "overview": "…",
  "release_date": "2016-10-19",
  "genre_ids": [53, 28, 80, 18, 9648],
  "id": 343611,
  "original_title": "Jack Reacher: Never Go Back",
  "original_language": "en",
  "title": "Jack Reacher: Never Go Back",
  "backdrop_path": "/4ynQYtSEuU5hyipcGkfD6ncwtwz.jpg",
  "popularity": 26.818468,
  "vote_count": 201,
  "video": false,
  "vote_average": 4.19
}
```

> **注意**：搜索结果是"瘦"对象 —— 有 `genre_ids`（数字）而非 `genres`（对象），
> **没有** `runtime` / `budget` / `revenue` / `production_companies`。
> 想要完整字段必须再调 `/movie/{id}`。

#### `GET /3/movie/{movie_id}` — 电影详情

- `operationId`: `movie-details`
- 描述原文：*"Get the top level details of a movie by ID."*

| 参数 | 必填 | 类型 | 默认 | 说明 |
|---|---|---|---|---|
| `movie_id` | **是** | integer | — | path |
| `append_to_response` | 否 | string | — | 逗号分隔，**最多 20 项** |
| `language` | 否 | string | `en-US` | |

**官方完整响应示例（Star Wars, id=11）逐字段**：

```json
{
  "adult": false,
  "backdrop_path": "/2w4xG178RpB4MDAIfTkqAuSJzec.jpg",
  "belongs_to_collection": {"id":10,"name":"Star Wars Collection",
                            "poster_path":"/pWVLFh4OuejTpUaDQbB1C4zoS2p.jpg",
                            "backdrop_path":"/iY2ujEY2m68OTTlPFTiHub9joHS.jpg"},
  "budget": 11000000,
  "genres": [{"id":12,"name":"Adventure"},{"id":28,"name":"Action"},{"id":878,"name":"Science Fiction"}],
  "homepage": "http://www.starwars.com/films/star-wars-episode-iv-a-new-hope",
  "id": 11,
  "imdb_id": "tt0076759",
  "origin_country": ["US"],
  "original_language": "en",
  "original_title": "Star Wars",
  "overview": "Princess Leia is captured and held hostage…",
  "popularity": 20.6912,
  "poster_path": "/6FfCtAuVAW8XJjZ7eWeLibRLWTw.jpg",
  "production_companies": [
    {"id":1,"logo_path":"/tlVSws0RvvtPBwViUyOFAO0vcQS.png","name":"Lucasfilm Ltd.","origin_country":"US"},
    {"id":25,"logo_path":"/qZCc1lty5FzX30aOCVRBLzaVmcp.png","name":"20th Century Fox","origin_country":"US"}
  ],
  "production_countries": [{"iso_3166_1":"US","name":"United States of America"}],
  "release_date": "1977-05-25",
  "revenue": 775398007,
  "runtime": 121,
  "spoken_languages": [{"english_name":"English","iso_639_1":"en","name":"English"}],
  "status": "Released",
  "tagline": "A long time ago in a galaxy far, far away...",
  "title": "Star Wars",
  "video": false,
  "vote_average": 8.2,
  "vote_count": 22061
}
```

> `imdb_id` 在电影详情里直接给出（无需额外调 `/external_ids`）——这点与剧集不同（见 2.3.2）。

#### `GET /3/movie/{movie_id}/credits` — 演职员

- `operationId`: `movie-credits`；参数：`movie_id`（必填）、`language`（默认 `en-US`）
- 返回：`id`, `cast[]`, `crew[]`

**`cast[]` 单条**（官方示例）：

```json
{"adult":false,"gender":2,"id":819,"known_for_department":"Acting",
 "name":"Edward Norton","original_name":"Edward Norton","popularity":26.99,
 "profile_path":"/8nytsqL59SFJTVYVrN72k6qkGgJ.jpg","cast_id":4,
 "character":"The Narrator","credit_id":"52fe4250c3a36847f80149f3","order":0}
```

**`crew[]` 单条**（官方示例）：

```json
{"adult":false,"gender":2,"id":376,"known_for_department":"Production",
 "name":"Arnon Milchan","original_name":"Arnon Milchan","popularity":2.931,
 "profile_path":"/b2hBExX4NnczNAnLuTBF4kmNhZm.jpg","credit_id":"55731b8192514111610027d7",
 "department":"Production","job":"Executive Producer"}
```

> **导演 / 编剧怎么筛（这是任务里问的重点）**：
> - **导演**：在 `crew[]` 里筛 `job == "Director"`（`department == "Directing"`）。
> - **编剧**：`job` 取值不止一个 —— 常见 `"Writer"`、`"Screenplay"`、`"Story"`、`"Novel"`（原著）、`"Characters"`。
>   建议按 `department == "Writing"` 先捞全集，再按 `job` 白名单排优先级：`Screenplay > Writer > Story > Novel`。
> - **演员排序**：用 `cast[].order` **升序**（`order: 0` 是第一主角）。官方示例中 Fighting Club 的
>   `order` 依次为 0/1/2/3，与 `cast_id`（4/5/285/7）**顺序不一致** ——
>   **`cast_id` 不是排序字段，必须用 `order`**。
> - `known_for_department` 是**该人物本人**最知名的领域，**不是**本条记录里的职务；判断本条职务请用 `job`/`department`/`character`。

#### `GET /3/movie/{movie_id}/images` — 图片

- 参数：`movie_id`（必填）、`include_image_language`（可选，*"specify a comma separated list of ISO-639-1 values to query, for example: `en-US,null`"*）、`language`（可选）
- 返回：`id`, `backdrops[]`, `logos[]`, `posters[]`

**单条图片对象字段**（官方示例，posters 共 81 条）：

```json
{"aspect_ratio":0.667,"height":900,"iso_639_1":"pt",
 "file_path":"/r3pPehX4ik8NLYPpbDRAh0YRtMb.jpg",
 "vote_average":5.258,"vote_count":6,"width":600}
```

字段全集：`aspect_ratio`, `file_path`, `height`, `iso_639_1`, `vote_average`, `vote_count`, `width`
（`logo` 另有 `file_type`，见 2.2）。

#### `GET /3/movie/{movie_id}/release_dates` — 分级与上映日期 ★

- `operationId`: `movie-release-dates`
- 描述原文：*"Get the release dates and certifications for a movie."*
- 参数：**仅 `movie_id`（必填）** —— **没有 `language`、没有 `region` 参数**
- 返回：`id`, `results[]`

**结构（官方示例，Fight Club）**：

```json
{
  "id": 550,
  "results": [
    {
      "iso_3166_1": "US",
      "release_dates": [
        {"certification":"","descriptors":[],"iso_639_1":"",
         "note":"CMJ Film Festival","release_date":"1999-09-21T00:00:00.000Z","type":1},
        {"certification":"R","descriptors":[],"iso_639_1":"",
         "note":"","release_date":"1999-10-15T00:00:00.000Z","type":3}
      ]
    },
    {
      "iso_3166_1": "DE",
      "release_dates": [
        {"certification":"18","descriptors":[],"iso_639_1":"","note":"",
         "release_date":"1999-11-04T00:00:00.000Z","type":3}
      ]
    }
  ]
}
```

**`release_dates[].type` 的取值含义**（官方明确定义）：

| Type | Release |
|---|---|
| 1 | Premiere |
| 2 | Theatrical (limited) |
| 3 | Theatrical |
| 4 | Digital |
| 5 | Physical |
| 6 | TV |

来源：<https://developer.themoviedb.org/docs/region-support#release-types>（官方表格，逐字）

> **⚠️ 中文用户最容易踩的坑**：`release_dates` 里**大量条目 `certification` 为空字符串 `""`**
> （上例 US 的 type=1 就是空）。**取分级时必须过滤掉空值，并按 `type` 优先级挑选**：
> 一般优先 `type=3`（院线）或 `type=4/5`（数字/实体发行），而不是取数组第一个。

#### `GET /3/movie/{movie_id}/videos` — 视频（预告片）

- 参数：`movie_id`（必填）、`language`（默认 `en-US`）
- 返回：`id`, `results[]`

```json
{"iso_639_1":"en","iso_3166_1":"US","name":"Fight Club (1999) Trailer…",
 "key":"O-b2VfmmbyA","site":"YouTube","size":720,"type":"Trailer",
 "official":false,"published_at":"2016-03-05T02:03:14.000Z","id":"639d5326be6d88007f170f44"}
```

> 播放链接需自行拼：`site == "YouTube"` → `https://www.youtube.com/watch?v={key}`。
> 建议筛 `type == "Trailer"` 且 `official == true` 优先。

#### `GET /3/movie/{movie_id}/recommendations` — 推荐

- 参数：`movie_id`（必填）、`language`（默认 `en-US`）、`page`（默认 `1`）
- 返回：`page`, `results`, `total_pages`, `total_results`
  （规范中该端点的主示例对象为空 `{}`，但 schema 与 `/similar` 同族，字段同搜索列表）

#### 补充：`GET /3/movie/{movie_id}/external_ids`

- 参数：仅 `movie_id`（必填）
- 返回示例：`{"id":550,"imdb_id":"tt0137523","wikidata_id":…,"facebook_id":…,"instagram_id":…,"twitter_id":…}`

#### 补充：`GET /3/movie/{movie_id}/alternative_titles`

- 参数：`movie_id`（必填）、`country`（可选，*"specify a ISO-3166-1 value to filter the results"*）
- 返回：`id`, `titles[]` —— **中文别名/港台译名的重要来源**

### 2.3.2 剧集

#### `GET /3/search/tv` — 搜索剧集

- `operationId`: `search-tv`
- 描述原文：*"Search for TV shows by their original, translated and also known as names."*

| 参数 | 必填 | 类型 | 默认 | 说明 |
|---|---|---|---|---|
| `query` | **是** | string | — | |
| `first_air_date_year` | 否 | integer | — | 只匹配首播年，*"Valid values are: 1000..9999"* |
| `include_adult` | 否 | boolean | `false` | |
| `language` | 否 | string | `en-US` | |
| `page` | 否 | integer | `1` | |
| `year` | 否 | integer | — | *"Search the first air date and all episode air dates"* —— **注意与 `first_air_date_year` 语义不同** |

> **匹配打分时的关键区别**：`year` 会匹配"任何一集的播出年"，所以一部 2011 年首播、2019 年完结的剧，
> `year=2015` 也能命中。**做精确年份匹配请用 `first_air_date_year`。**

#### `GET /3/tv/{series_id}` — 剧集详情

- `operationId`: `tv-series-details`；参数：`series_id`（必填）、`append_to_response`（最多 20 项）、`language`（默认 `en-US`）

**顶层字段全集（官方示例，Game of Thrones id=1399）**：

```
adult, backdrop_path, created_by, episode_run_time, first_air_date, genres,
homepage, id, in_production, languages, last_air_date, last_episode_to_air,
name, next_episode_to_air, networks, number_of_episodes, number_of_seasons,
origin_country, original_language, original_name, overview, popularity,
poster_path, production_companies, production_countries, seasons,
spoken_languages, status, tagline, type, vote_average, vote_count
```

**官方实际取值**：

```json
{
  "number_of_seasons": 8,
  "number_of_episodes": 73,
  "status": "Ended",
  "type": "Scripted",
  "original_name": "Game of Thrones",
  "first_air_date": "2011-04-17",
  "last_air_date": "2019-05-19",
  "vote_average": 8.438,
  "vote_count": 21390,
  "episode_run_time": [60],
  "origin_country": ["US"],
  "languages": ["en"],
  "in_production": false
}
```

**`seasons[]` 单条结构（关键）**：

```json
{"air_date":"2010-12-05","episode_count":272,"id":3627,
 "name":"Specials","overview":"","poster_path":"/kMTcwNRfFKCZ0O2OaBZS0nZ2AIe.jpg",
 "season_number":0,"vote_average":0}
```

```json
{"air_date":"2011-04-17","episode_count":10,"id":3624,"name":"Season 1",
 "overview":"Trouble is brewing in the Seven Kingdoms of Westeros.…",
 "poster_path":"/wgfKiqzuMrFIkU1M68DDDY8kGC1.jpg","season_number":1,"vote_average":8.4}
```

> **⚠️ 两个必须处理的坑**（均为官方数据实证）：
> 1. **`season_number: 0` 是"Specials"（特别篇）**，`episode_count` 高达 272，
>    **远大于正片任何一季**。若不排除第 0 季，会让"总集数"统计彻底失真。
> 2. **`number_of_episodes: 73` ≠ Σ`seasons[].episode_count`（= 272+10+…）**。
>    官方返回的 `number_of_episodes` 只统计正片季。**两者不可混用**。

**`last_episode_to_air` / `next_episode_to_air`** 结构（官方示例）：

```json
{"id":1551830,"name":"The Iron Throne","overview":"…","vote_average":4.809,
 "vote_count":241,"air_date":"2019-05-19","episode_number":6,
 "production_code":"806","runtime":80,"season_number":8,
 "show_id":1399,"still_path":"/zBi2O5EJfgTS6Ae0HdAYLm9o2nf.jpg"}
```

#### `GET /3/tv/{series_id}/season/{season_number}` — 季详情

- `operationId`: `tv-season-details`；参数：`series_id`（必填）、`season_number`（必填，integer）、`append_to_response`（最多 20 项）、`language`（默认 `en-US`）

**返回结构**：`_id`, `air_date`, `episodes[]`, `name`, `networks`, `overview`, `id`, `poster_path`, `season_number`, `vote_average`

> **注意字段名差异**：这里的内部 id 叫 **`_id`**（字符串，如 `"5256c89f19c2956ff6046d47"`），
> 而 `id` 是季的数字 id（如 `3624`）。别搞混。

**官方示例（GoT S1）**：

```json
{"_id":"5256c89f19c2956ff6046d47","air_date":"2011-04-17",
 "id":3624,"name":"Season 1","season_number":1,
 "overview":"Trouble is brewing…","poster_path":"/wgfKiqzuMrFIkU1M68DDDY8kGC1.jpg",
 "vote_average":8.4}
```

**`episodes[]` 单条结构（关键，含逐集演职员）**：

```json
{"air_date":"2011-04-17","episode_number":1,"episode_type":"standard","id":63056,
 "name":"Winter Is Coming","overview":"Jon Arryn, the Hand of the King, is dead.…",
 "production_code":"101","runtime":62,"season_number":1,"show_id":1399,
 "still_path":"/9hGF3WUkBf7cSjMg0cdMDHJkByd.jpg","vote_average":8.1,"vote_count":396,
 "crew":[{"department":"Directing","job":"Director","credit_id":"…",
          "id":44797,"name":"Tim Van Patten","profile_path":"/vwcARZBg4PEzOwnPsXdjRWeUVrZ.jpg"},
         {"job":"Writer","department":"Writing","credit_id":"…",
          "id":9813,"name":"David Benioff", …}],
 "guest_stars":[{"character":"Benjen Stark","credit_id":"…","order":61,
                 "id":119783,"name":"Joseph Mawle","profile_path":"…"}]}
```

> **单集导演/编剧就在这里拿**：`episodes[].crew[]` 筛 `job == "Director"` / `department == "Writing"`。
> 这意味着**刮削整季只需 1 次请求**（`/tv/{id}/season/{n}`），不必逐集请求。
> `episode_type` 是新增字段（官方示例中为 `"standard"`）。

#### `GET /3/tv/{series_id}/external_ids` — 剧集外部 ID

- 参数：仅 `series_id`（必填）
- 返回：`id`, `imdb_id`, `freebase_mid`, `freebase_id`, **`tvdb_id`**, `tvrage_id`, `wikidata_id`, `facebook_id`, `instagram_id`, `twitter_id`

> **`tvdb_id` 在这里**。这是打通 TMDB ↔ TheTVDB 的关键字段。
> 注意：**剧集详情 `/tv/{id}` 里没有 `imdb_id`**（电影详情里有），必须调本接口。

#### `GET /3/tv/{series_id}/credits` — 剧集演职员

- 描述原文：*"Get the latest season credits of a TV show."*
- 返回：`cast[]`, `crew[]`, `id`

> **⚠️ 语义警告**：官方描述明确说是 **"latest season"（最新一季）** 的演职员，
> **不是全剧**。想要全剧阵容**必须用 `aggregate_credits`**。

#### `GET /3/tv/{series_id}/aggregate_credits` — 全剧聚合演职员 ★

- 描述原文：*"Get the aggregate credits (cast and crew) that have been added to a TV show."*
- 参数：`series_id`（必填）、`language`（默认 `en-US`）

**`cast[]` 单条结构（与 `/credits` 不同！）**：

```json
{"adult":false,"gender":1,"id":1223786,"known_for_department":"Acting",
 "name":"Emilia Clarke","original_name":"Emilia Clarke","popularity":42.737,
 "profile_path":"/u59kTmNHXzaGZqokivxLPiBVIML.jpg",
 "roles":[{"credit_id":"5256c8af19c2956ff60479f6",
           "character":"Daenerys Targaryen","episode_count":78}],
 "total_episode_count":78,"order":6}
```

**`crew[]` 单条结构**：

```json
{"adult":false,"gender":1,"id":6411,"known_for_department":"Art",
 "name":"Deborah Riley","original_name":"Deborah Riley","popularity":1.4,
 "profile_path":"/cjhADpqdrnwB1PdDUKaBnWrIj2Q.jpg",
 "jobs":[{"credit_id":"54eee9e5c3a3686d5800584e",
          "job":"Production Design","episode_count":43}],
 "department":"Art","total_episode_count":43}
```

> **`aggregate_credits` 与 `credits` 的结构差异（务必区分，否则解析会崩）**：
>
> | 字段 | `/credits` | `/aggregate_credits` |
> |---|---|---|
> | 演员角色 | `character`（字符串） | **`roles[]`**（数组，可多角色） |
> | 剧组职务 | `job`（字符串） | **`jobs[]`**（数组，可多职务） |
> | 出场统计 | 无 | `total_episode_count`（演员）、`roles[].episode_count`、`jobs[].episode_count` |
> | `department` | 有 | 有（在 crew 顶层） |
> | `order` | 有 | 有 |
>
> **写解析器时这两种响应必须分开处理。**

#### `GET /3/tv/{series_id}/images` — 剧集图片

- 参数：`series_id`（必填）、`include_image_language`（可选，示例 `en-US,null`）、`language`（可选）
- 返回：`backdrops[]`, `id`, `logos[]`, `posters[]`（字段同电影图片）

#### `GET /3/tv/{series_id}/content_ratings` — 剧集分级 ★

- `operationId`: `tv-series-content-ratings`
- 描述原文：*"Get the content ratings that have been added to a TV show."*
- 参数：**仅 `series_id`（必填）** —— 无 `language`
- 返回：`results[]`, `id`

**官方示例（GoT）**：

```json
{"id":1399,
 "results":[
   {"descriptors":[],"iso_3166_1":"DE","rating":"16"},
   {"descriptors":[],"iso_3166_1":"AU","rating":"R18+"},
   {"descriptors":[],"iso_3166_1":"FR","rating":"16"},
   {"descriptors":[],"iso_3166_1":"US","rating":"TV-MA"},
   {"descriptors":[],"iso_3166_1":"CA","rating":"18+"},
   {"descriptors":[],"iso_3166_1":"RU","rating":"18+"},
   {"descriptors":[],"iso_3166_1":"KR","rating":"19"},
   {"descriptors":[],"iso_3166_1":"GB","rating":"18"},
   {"descriptors":[],"iso_3166_1":"BR","rating":"16"},
   {"descriptors":[],"iso_3166_1":"NL","rating":"16"},
   {"descriptors":[],"iso_3166_1":"PT","rating":"18"},
   {"descriptors":[],"iso_3166_1":"HU","rating":"18"},
   {"descriptors":[],"iso_3166_1":"ES","rating":"18"},
   {"descriptors":[],"iso_3166_1":"SG","rating":"R21"},
   {"descriptors":[],"iso_3166_1":"IN","rating":"A"},
   {"descriptors":[],"iso_3166_1":"MX","rating":"C"}
 ]}
```

**剧集分级的字段名是 `rating`，电影分级是 `release_dates[].certification` —— 两者不同名。**

#### 补充：`GET /3/tv/{series_id}/videos`

- 参数：`series_id`（必填）、**`include_video_language`**（可选，*"filter the list results by language, supports more than one value by using a comma"*）、`language`（默认 `en-US`）
- **注意剧集的视频接口参数名是 `include_video_language`，而图片接口是 `include_image_language`。**

#### 补充：其他常用剧集端点

| 端点 | 用途 |
|---|---|
| `GET /3/tv/{series_id}/recommendations` | 推荐 |
| `GET /3/tv/{series_id}/similar` | 相似 |
| `GET /3/tv/{series_id}/alternative_titles` | 别名（中文译名） |
| `GET /3/tv/{series_id}/translations` | 全部翻译 |
| `GET /3/tv/{series_id}/season/{n}/episode/{e}` | 单集详情（字段：`air_date, crew, episode_number, guest_stars, name, overview, id, production_code, runtime, season_number, still_path, vote_average, vote_count`） |
| `GET /3/tv/{series_id}/season/{n}/images` | 季图片 |
| `GET /3/tv/{series_id}/season/{n}/external_ids` | 季外部 ID |
| `GET /3/tv/{series_id}/season/{n}/aggregate_credits` | 季聚合演职员 |
| `GET /3/tv/{series_id}/episode_groups` | 剧集分组（应对"绝对集号"等播出顺序问题） |

### 2.3.3 人物

#### `GET /3/person/{person_id}` — 人物详情

- 参数：`person_id`（必填）、`append_to_response`（最多 20 项）、`language`（默认 `en-US`）
- 返回字段：`adult, also_known_as, biography, birthday, deathday, gender, homepage, id, imdb_id, known_for_department, name, place_of_birth, popularity, profile_path`

> `gender` 是整数编码（官方示例中为 1/2，0 通常表示未设置）。
> **性别编码的官方含义未在本次取得的文档中给出明确表格 → 标为不确定。**

#### `GET /3/person/{person_id}/combined_credits` — 全部作品

- 参数：**`person_id`（必填，注意规范中此处类型标为 `string`，其他端点标 `integer`）**、`language`（默认 `en-US`）
- 返回：`cast[]`, `crew[]`, `id`

官方示例中 `cast[]` 条目的结构（含 tv 的 `episode_count` 与 `seasons[]` 摘要）：

```json
{"adult":false,"…","popularity":898.378,"first_air_date":"2023-01-15",
 "vote_average":8.749,"vote_count":3341,"origin_country":["US"],
 "character":"Joel Miller","episodes":[],
 "seasons":[{"air_date":"2023-01-15","episode_count":9,"id":144593,
             "name":"Staffel 1","overview":"…","poster_path":"…","season_number":1,"show_id":100088}],
 "media_type":"tv","id":…}
```

> **`media_type` 字段**用于区分 movie / tv，做人物作品页必须按它分流。

#### 补充：`GET /3/person/{person_id}/images`

- 返回：`id`, `profiles[]`；单条字段 `aspect_ratio, height, iso_639_1, file_path, vote_average, vote_count, width`
- 官方示例：`{"aspect_ratio":0.666,"height":980,"iso_639_1":null,"file_path":"/cckcYc2v0yh1tc9QjRelptcOBko.jpg","vote_average":5.288,"vote_count":89,"width":653}`

#### 补充：`GET /3/search/person`

- 参数：`query`（必填）、`include_adult`（默认 false）、`language`（默认 `en-US`）、`page`（默认 1）

### 2.3.4 趋势与发现（可选）

#### `GET /3/trending/{media_type}/{time_window}`

- `media_type` ∈ `all` | `movie` | `tv` | `person`；`time_window` ∈ `day` | `week`（必填，枚举，默认 `day`）
- 参数：`language`（默认 `en-US`，*"`ISO-639-1`-`ISO-3166-1` code"*）
- 实际路径：`/3/trending/all/{time_window}`、`/3/trending/movie/{time_window}`、`/3/trending/tv/{time_window}`、`/3/trending/person/{time_window}`
- 返回：`page`, `results`, `total_pages`, `total_results`

#### `GET /3/discover/movie` — 电影发现

- 描述原文：*"Find movies using over 30 filters and sort options."*
- **关键参数**（规范原文枚举）：
  - `sort_by` 默认 `popularity.desc`，可选：`original_title.asc/desc`, `popularity.asc/desc`, `revenue.asc/desc`, `primary_release_date.asc/desc`, `title.asc/desc`, `vote_average.asc/desc`, `vote_count.asc/desc`
  - `certification` + `region` / `certification_country`；`certification.gte` / `certification.lte`（**分级范围筛选**）
  - `with_genres` / `without_genres`（逗号=AND，竖线=OR）
  - `with_cast` / `with_crew` / `with_people` / `with_companies` / `with_keywords`
  - `vote_average.gte/lte`、`vote_count.gte/lte`、`with_runtime.gte/lte`
  - `with_original_language`、`with_origin_country`、`year`、`primary_release_year`
  - `with_release_type`：*"possible values are: [1, 2, 3, 4, 5, 6]"*（含义见 2.3.1 的 release type 表）
  - `with_watch_providers` / `watch_region` / `with_watch_monetization_types`（*"possible values are: [flatrate, free, ads, rent, buy]"*）
  - `page`（默认 1）、`include_adult`（默认 false）、`include_video`（默认 false）

#### `GET /3/discover/tv` — 剧集发现

- `sort_by` 默认 `popularity.desc`，可选：`first_air_date.asc/desc`, `name.asc/desc`, `original_name.asc/desc`, `popularity.asc/desc`, `vote_average.asc/desc`, `vote_count.asc/desc`
- `with_status`：*"possible values are: [0, 1, 2, 3, 4, 5]"*
- `with_type`：*"possible values are: [0, 1, 2, 3, 4, 5, 6]"*
- `first_air_date.gte/lte`、`air_date.gte/lte`、`first_air_date_year`、`with_networks`、`with_genres`、`timezone`、`screened_theatrically` 等

> ⚠️ `with_status` / `with_type` 的**数字到含义的映射未在本次取得的官方文档中给出** → 标为不确定，不要猜。

#### 辅助端点

| 端点 | 用途 | 参数 |
|---|---|---|
| `GET /3/genre/movie/list` | 电影类型表 | `language`（默认 `en`） |
| `GET /3/genre/tv/list` | 剧集类型表 | `language`（默认 `en`） |
| `GET /3/configuration/countries` | 国家表（ISO 3166-1） | `language`（默认 `en-US`） |
| `GET /3/configuration/languages` | 语言表 | — |
| `GET /3/configuration/jobs` | **职务表（department/job 的权威枚举）** | — |
| `GET /3/configuration/primary_translations` | 主翻译列表 | — |
| `GET /3/configuration/timezones` | 时区表 | — |
| `GET /3/find/{external_id}` | **用 IMDb/TVDB ID 反查 TMDB** | `external_source`（必填） |
| `GET /3/certification/movie/list` | 电影分级含义表 | 无 |
| `GET /3/certification/tv/list` | 剧集分级含义表 | 无 |

> **`/3/find/{external_id}` 是刮削器的利器**：若文件名旁有 `.nfo` 或你已知 IMDb ID，
> 可直接反查，绕开标题模糊匹配。`external_source` 的可选值本次未完整取得 → 需实测。

## 2.4 关键字段的含义

### 2.4.1 评分与热度

| 字段 | 类型 | 含义 | 官方依据 |
|---|---|---|---|
| `vote_average` | number | 平均分。官方示例取值 8.2 / 8.438 / 4.19 | OpenAPI schema |
| `vote_count` | integer | 投票人数。官方示例 22061 / 21390 | OpenAPI schema |
| `popularity` | number | **"lifetime" 热度分**，用于搜索加权与 discover 排序 | <https://developer.themoviedb.org/docs/popularity-and-trending> |

**`popularity` 官方原文**：

> *"You can think of popularity as being a **'lifetime' popularity score** that is impacted by the attributes below. It's calculated quite differently than trending."*
>
> **Movies** 的影响因子：*"Number of votes for the day / Number of views for the day / Number of users who marked it as a 'favourite' for the day / Number of users who added it to their 'watchlist' for the day / Release date / Number of total votes / Previous days score"*
>
> **TV Shows** 的影响因子：同上，但把 *"Release date"* 换成 *"Next/last episode to air date"*
>
> **People**：*"Number of views for the day / Previous days score"*
>
> *"There is no API to explore this data right now"*

**Trending 官方原文**：

> *"Trending is another type of 'popularity' score on TMDB but unlike popularity (discussed above), **trending's time windows are much shorter (daily, weekly)**."*

> ⚠️ **重要**：官方**没有给出 `vote_average` 的分值范围（0–10）的明文说明**，
> 虽然实测数据（8.2、4.19、4.809）与社区共识都指向 0–10。
> **本次取得的官方文档中未找到该范围的明文定义 → 标为"实测推断，非官方明文"。**
> 同理，`popularity` **没有绝对上限，也没有跨类型可比性** —— 官方反复强调它只是"sort 用的提示值"。

### 2.4.2 标题与语言

| 字段 | 含义 | 说明 |
|---|---|---|
| `title` | **本地化标题**（受 `language` 参数影响） | 中文刮削要的就是它 |
| `original_title` | **原始标题**（不受 `language` 影响） | 用于匹配与展示原名 |
| `name` / `original_name` | 剧集版的 `title` / `original_title` | **剧集用 `name`，电影用 `title`** |
| `original_language` | 原始语言（ISO 639-1） | 官方示例 `"en"` |
| `spoken_languages` | 口语语言数组，元素为 `{english_name, iso_639_1, name}` | 电影详情有 |
| `languages` | 剧集的语言数组，**元素是纯字符串**（如 `["en"]`） | **与电影的 `spoken_languages` 结构不同** |
| `origin_country` | 原产国数组（ISO 3166-1） | 官方示例 `["US"]` |
| `production_countries` | `[{iso_3166_1, name}]` | |
| `production_companies` | `[{id, logo_path, name, origin_country}]` | 电影详情有 |

> **⚠️ 易错点**：电影的 `spoken_languages` 是**对象数组**，剧集的 `languages` 是**字符串数组**。
> 同一个概念两种结构，写 DTO 时要分开。

### 2.4.3 类型与时长

| 字段 | 含义 |
|---|---|
| `genres` | `[{id, name}]`（详情接口返回的是**对象数组**） |
| `genre_ids` | `[53, 28, 80]`（**只有搜索/列表接口返回数字数组**） |
| `runtime` | 电影时长，**单位分钟**（官方示例 `121`） |
| `episode_run_time` | 剧集单集时长，**数组**（官方示例 `[60]`），可能是空数组 |

> 需要"类型名"时：详情接口直接用 `genres[].name`；
> 搜索接口只有 `genre_ids`，需自行用 `/genre/movie/list`（或 `/genre/tv/list`）建映射表。
> **电影与剧集的 genre id 空间不同，必须用两张表分别映射。**

### 2.4.4 制作与商业

| 字段 | 含义 | 注意 |
|---|---|---|
| `budget` | 预算（美元，整数） | **常为 0**（表示未公开），别当成"真的 0 元" |
| `revenue` | 票房（美元，整数） | 同上，常为 0 |
| `status` | 状态（官方示例 `"Released"` / `"Ended"`） | 取值枚举未在本次文档中给出完整表 → 不确定 |
| `in_production` | 布尔，是否在制 | 剧集 |
| `type` | 剧集类型（官方示例 `"Scripted"`） | 枚举未取得完整表 → 不确定 |
| `tagline` | 宣传语 | |
| `homepage` | 官网 | 可能为空串 |
| `belongs_to_collection` | 所属系列（如"星球大战系列"） | **做"系列合集"功能的关键字段** |

### 2.4.5 剧集的季与集

| 字段 | 含义 | 坑 |
|---|---|---|
| `number_of_seasons` | 季数（官方示例 8） | **不含 Specials** |
| `number_of_episodes` | 集数（官方示例 73） | **≠ Σ seasons[].episode_count** |
| `seasons[]` | 季摘要数组 | **含 `season_number: 0` 的 Specials** |
| `seasons[].season_number` | 季号，**0 = Specials** | 排序/展示要特殊处理 |
| `seasons[].episode_count` | 该季集数 | Specials 可能异常大（示例 272） |
| `seasons[].air_date` | 该季首播日期 | 可能为 `null` |
| `seasons[].id` | 该季的 TMDB 季 id | 不是剧 id |
| `seasons[].name` | 季名，**受 `language` 影响**（官方示例出现德语 `"Staffel 1"`） | 不要用 `name` 判断季号，用 `season_number` |
| `seasons[].vote_average` | 该季评分 | |
| `seasons[].overview` | 该季简介 | 可能为空串 |
| `seasons[].poster_path` | 该季海报 | 可能为 `null` |

**单集（`episodes[]`）字段**：`air_date`, `episode_number`, `episode_type`, `id`, `name`, `overview`, `production_code`, `runtime`, `season_number`, `show_id`, `still_path`, `vote_average`, `vote_count`, `crew[]`, `guest_stars[]`

> **`episode_type`** 在官方示例中为 `"standard"`。**其他取值（如 finale/special）未在文档中列出 → 不确定。**

### 2.4.6 分级的国家代码与含义

**权威来源**：`GET /3/certification/movie/list` 与 `GET /3/certification/tv/list`
（描述原文：*"Get an up to date list of the officially supported movie certifications on TMDB."*）
返回结构：`{"certifications": { "<ISO 3166-1 国家码>": [ {"certification": "...", "meaning": "...", "order": N}, ... ] }}`

**本次实测结果（重要）**：

| 项 | 实测值 |
|---|---|
| 电影分级覆盖国家数 | **45** |
| 电影分级国家列表 | `AR, AU, BG, BR, CA, CA-QC, CH, DE, DK, ES, FI, FR, GB, GR, HK, HU, ID, IE, IL, IN, IT, JP, KR, LT, LU, LV, MO, MX, MY, NL, NO, NZ, PH, PR, PT, RU, SE, SG, SK, TH, TR, TW, US, VI, ZA` |
| 剧集分级覆盖国家数 | **40** |
| **`CN`（中国大陆）是否在其中** | **否！电影列表无 `CN`，剧集列表也无 `CN`** |

> **🔴 这是中文用户最需要知道的事实**：
> **TMDB 的官方分级表里根本没有中国大陆（CN）**。
> 因此 `/movie/{id}/release_dates` 与 `/tv/{id}/content_ratings` 的返回中，
> **不会有 `iso_3166_1 == "CN"` 的条目**（或即便有也无官方含义可查）。
> 想要"中国大陆分级"，TMDB **给不了** —— 这是数据源的固有缺口，不是代码问题。
> 中文场景应回退到 `HK`（I/IIA/IIB/III）或 `TW`（0+/6+/12+/15+/18+），或干脆显示"暂无分级"。

**US 电影分级（官方 `meaning` 原文摘录）**：

| certification | order | meaning（官方原文） |
|---|---|---|
| `NR` | 0 | *"No rating information."* |
| `G` | 1 | *"All ages admitted. There is no content that would be objectionable to most parents. This is one of only two ratings dating back to 1968 that still exists today."* |
| `PG` | 2 | *"Some material may not be suitable for children under 10. These films may contain some mild language, crude/suggestive humor, scary moments and/or violence. No drug content is present. …"* |
| `PG-13` | 3 | *"Some material may be inappropriate for children under 13. Films given this rating may contain sexual content, brief or partial nudity, some strong language and innuendo, humor, mature themes, political themes, terror and/or intense action violence. However, bloodshed is rarely present. This is the minimum rating at which drug content is present."* |
| `R` | 4 | *"Under 17 requires accompanying parent or adult guardian 21 or older. … These films may contain strong profanity, graphic sexuality, nudity, strong violence, horror, gore, and strong drug use. …"* |
| `NC-17` | 5 | *"These films contain excessive graphic violence, intense or explicit sex, depraved, abhorrent behavior, explicit drug abuse, strong language, explicit nudity, … NC-17 does not necessarily mean obscene or pornographic in the oft-accepted or legal meaning of those words."* |

**US 剧集分级（官方 `meaning` 原文摘录）**：

| certification | order | meaning（官方原文） |
|---|---|---|
| `NR` | 0 | *"No rating information."* |
| `TV-Y` | 1 | *"This program is designed to be appropriate for all children."* |
| `TV-Y7` | 2 | *"This program is designed for children age 7 and above."* |
| `TV-G` | 3 | *"Most parents would find this program suitable for all ages."* |
| `TV-PG` | 4 | *"This program contains material that parents may find unsuitable for younger children."* |
| `TV-14` | 5 | *"This program contains some material that many parents would find unsuitable for children under 14 years of age."* |
| `TV-MA` | 6 | *"This program is specifically designed to be viewed by adults and therefore may be unsuitable for children under 17."* |

**`order` 字段的作用**：官方给出 `order` 用于**排序比较**（数字越大越严格）。
`discover` 的 `certification.gte` / `certification.lte` 就是基于这个序。
**用 `order` 做"严于/松于"判断，不要自己按字符串猜。**

**JP 电影分级（官方原文）**：`G`(order 1) *"General, suitable for all ages."* / `PG12`(2) *"Parental guidance requested for young people under 12 years."* / `R15+`(3) *"No one under 15 admitted."* / `R18+`(4) *"No one under 18 admitted."*

**HK 电影分级**：`I`(1) / `IIA`(2) / `IIB`(3) / `III`(4) —— **`meaning` 字段为空字符串**，官方未填说明。

**TW 电影分级（官方原文）**：`0+`(1) *"Viewing is permitted for audiences of all ages."* / `6+`(2) / `12+`(3) / `15+`(4) / `18+`(5)

**KR 电影分级**：`All`(0) / `12`(1) / `15`(2) / `18`(3) / `Restricted Screening`(4)

**DE 电影分级**：`0`(1) / `6`(2) / `12`(3) / `16`(4) / `18`(5)

**GB 电影分级**：`U` / `PG` / `12A` / `12` / `15` / `18` / `R18`（TV 表里 `U` order=0）

> **实践建议**：**不要硬编码分级含义表**。
> 应用启动或定期调 `/3/certification/movie/list` 与 `/3/certification/tv/list` 落库，
> 这样当 TMDB 修订分级含义时你的展示会自动跟上。
> 但要准备兜底：**`CN` 永远查不到**，必须有"无分级"的 UI 态。

### 2.4.7 演职员的 `order` / `job` / `department`

已在 2.3.1 与 2.3.2 详述，此处归纳：

| 需求 | 正确做法 |
|---|---|
| 演员排序 | `cast[].order` **升序**（不是 `cast_id`！） |
| 导演 | `crew[]` 中 `job == "Director"` |
| 编剧 | `crew[]` 中 `department == "Writing"`，再按 `job` 细分 |
| 全剧阵容 | 必须用 `/tv/{id}/aggregate_credits`，`/credits` 只有最新一季 |
| 聚合数据 | `roles[]` / `jobs[]` / `total_episode_count` |
| 职务枚举 | `GET /3/configuration/jobs` |

## 2.5 语言与回退（中文用户最常踩的坑）

### 2.5.1 语言参数怎么传

来源：<https://developer.themoviedb.org/docs/languages>（官方原文）

> *"The language code system we use is **ISO 639-1**."*
> *"You'll usually find our language codes mated to a country code in the format of `en-US`. The country codes in use here are **ISO 3166-1**."*

官方示例：

```bash
curl --request GET \
     --url 'https://api.themoviedb.org/3/tv/1399?language=en-US' \
     --header 'Authorization: Bearer ACCESS_TOKEN'

curl --request GET \
     --url 'https://api.themoviedb.org/3/movie/popular?language=pt-BR' \
     --header 'Authorization: Bearer ACCESS_TOKEN'
```

**中文场景用 `language=zh-CN`**（也可试 `zh-TW` / `zh-HK` 取港台译名）。
默认值是 `en-US` —— **不传 `language` 就会拿到英文**，这是最常见的"为什么我的简介是英文"的原因。

> 官方明确说明的**覆盖缺口**：*"there are still a few gaps that do not. The two main areas that are not are **person names and characters**. We're working to support this."*
> —— **人名和角色名不本地化**，中文刮削也拿不到中文演员名。

### 2.5.2 图片的多语言回退 ★

来源：<https://developer.themoviedb.org/docs/image-languages>（官方原文，逐字）

**`poster_path` 的回退链**：

> *"The `poster_path` will query the language you specify in your query first and **default back to the highest rated image of the media's 'original language'** if it's present. **If that image doesn't exist, it simply falls back to the highest rated.** It's important to note that even though our language query parameter supports regional lookups, **these regional variants are not supported for images at this time**."*

即：`指定语言` → `原始语言中评分最高的` → `全局评分最高的`

**`backdrop_path` 的回退链**：

> *"Since 99% of backdrops don't contain a language, the default lookup for a backdrop is to simply query for **the highest rated backdrop with no language**. If it doesn't exist, then we return the overall highest rated."*

**`still_path`**：

> *"Like backdrops, TV episode images don't inherently have languages. We query for the highest rated."*

**`/images` 接口的过滤陷阱（官方原文）**：

> *"Remember, when you query one of the `/images` methods, **your `language` param will filter images**. Since you'll usually want to query additional languages, you'll want to use the **`include_image_language`** query parameter. Think of this as a means to provide a fallback."*

官方示例（英语 + 无语言标记）：

```bash
curl --request GET \
     --url 'https://api.themoviedb.org/3/movie/550/images?language=en-US&include_image_language=en,null' \
     --header 'Authorization: Bearer ACCESS_TOKEN'

# 或用 append_to_response 一次拿完
curl --request GET \
     --url 'https://api.themoviedb.org/3/movie/550?append_to_response=images&language=en-US&include_image_language=en-US,null' \
     --header 'Authorization: Bearer ACCESS_TOKEN'
```

> 官方说明：*"This query (`en-US,null`) is looking for all images that match English (in the United States) and those that haven't been set yet (null)."*

**中文刮削的正确写法**：

```
&language=zh-CN&include_image_language=zh,en,null
```

> **🔴 三个必须知道的坑**：
> 1. **`include_image_language` 里 `null` 是关键字**（表示"无语言标记"的图），不是字符串 `"null"` 的字面值语义 —— 必须原样传 `null`。
> 2. **图片不支持区域变体** —— 官方明说 `zh-CN` 的区域部分对图片无效。
>    所以应该传 **`zh`**（纯 ISO 639-1），而不是 `zh-CN`。传 `zh-CN` 可能导致过滤不到。
> 3. **`language` 会过滤 `/images` 结果集**。若你想"优先中文海报、没有则英文、再没有则任意"，
>    **必须在应用层排序，而不是依赖接口**——接口只按你给的列表过滤，不保证返回顺序。

### 2.5.3 文本字段的多语言

- `title` / `name` / `overview` / `tagline` 会随 `language` 变化。
- `original_title` / `original_name` **不随 `language` 变化** —— 永远保留原名用于匹配。
- **回退不在文本接口自动发生**：若某片没有中文翻译，`overview` 会返回**空字符串 `""`**（而非英文兜底）。
  → **必须自己实现二级请求**：`language=zh-CN` 拿到空 `overview` 时，再请求一次 `language=en-US` 兜底。
- 想一次拿全部语言：`GET /3/movie/{id}/translations`、`GET /3/tv/{id}/translations`（一次性返回所有翻译，适合入库）。
- 想拿中文别名/港台译名：`GET /3/movie/{id}/alternative_titles?country=CN`（也支持 `HK`/`TW`）。

## 2.6 图片怎么下

### 2.6.1 拼接

见 2.2：`secure_base_url + file_size + file_path`。

### 2.6.2 选图策略（工程建议，基于官方字段）

`/images` 返回的每个图片对象有 `vote_average`、`vote_count`、`iso_639_1`、`width`、`height`、`aspect_ratio`。官方**没有**给出"如何选图"的算法，以下是**工程建议**：

```python
def score_image(img, prefer_langs=("zh", "en", None), min_width=680):
    """分数越高越优先。注意：这是工程经验值，非 TMDB 官方算法。"""
    s = 0.0
    # 1) 语言优先级（最重要）
    if img["iso_639_1"] in prefer_langs:
        s += 1000 - 100 * prefer_langs.index(img["iso_639_1"])
    else:
        s += 0                      # 其他语言排最后
    # 2) 分辨率门槛：低于阈值直接重罚
    if (img.get("width") or 0) < min_width:
        s -= 500
    # 3) 投票质量
    s += (img.get("vote_average") or 0) * 10
    s += min((img.get("vote_count") or 0), 50)     # 封顶，避免刷票图碾压
    # 4) 海报应偏竖版
    if img.get("aspect_ratio") and abs(img["aspect_ratio"] - 0.667) < 0.05:
        s += 50
    return s
```

**关键原则**：
- **`vote_count` 要封顶**。官方示例中有 `vote_count: 1` 但 `vote_average: 5.312` 的图，
  与 `vote_count: 46`、`vote_average: 5.504` 的图分差很小；不封顶会让"单票高分图"胜出。
- **`w500` 要求原图宽度 ≥ 500**，但 TMDB 会**放大**小图（示例里有 `width: 600` 的图也能取 `w500`）。
  所以**必须按 `width` 过滤**，否则海报墙会糊。
- **`iso_639_1` 可能是 `null`**（无语言标记），中文优先策略里应把 `None` 放在 `zh` 之后、`en` 之前或之后（取决于你的偏好）。

### 2.6.3 下载与本地缓存

**目录结构建议**：

```
<data_dir>/
  images/
    poster/
      w342/{tmdb_id}.webp          # 网格缩略
      w500/{tmdb_id}.webp          # 详情页
    backdrop/
      w780/{tmdb_id}.webp
    profile/
      w185/{person_id}.webp
    still/
      w300/{episode_id}.webp
    logo/
      original/{company_id}.{ext}  # SVG 必须 original
```

> **为什么按尺寸分目录**：同一媒体在不同 UI 位置需要不同尺寸，
> 分目录可让"海报墙滚动的 5000 张缩略图"和"详情页大图"互不干扰地独立失效/重建。

**命名建议**：用 **TMDB id**（而非标题）作文件名 —— 避免中文/特殊字符/重名问题。
若一个媒体存多张图，用 `{tmdb_id}_{image_hash8}.jpg`，`image_hash8` 取 `file_path` 的哈希前 8 位
（`file_path` 在 TMDB 侧是稳定唯一标识）。

**并发与重试**：
- 官方限流约 **40 req/s**（见 2.7）。图片走 CDN，**不在 API 限流内**，但仍应礼貌。
- 建议：**API 调用并发 ≤ 4，图片下载并发 ≤ 8**。
- **重试策略**：只对 `429` / `5xx` / 网络超时重试；`401`（Key 无效）、`404`（id 不存在）**不要重试**。
  官方错误码表（<https://developer.themoviedb.org/docs/errors>）：
  - `429` → 错误码 `25`：*"Your request count (#) is over the allowed limit of (40)."*
  - `401` → 错误码 `7`：*"Invalid API key: You must be granted a valid key."*
  - `404` → 错误码 `34`：*"The resource you requested could not be found."*
  - `503` → 错误码 `46`：*"The API is undergoing maintenance. Try again later."*（**应重试，且退避**）
  - `504` → 错误码 `24`：*"Your request to the backend server timed out. Try again."*
- 指数退避：`sleep = min(2 ** attempt + random(), 60)`，最多 5 次。
- **`429` 时应优先读响应头做退避**（`Retry-After`）—— 但**官方文档未说明 TMDB 是否返回该头 → 不确定，需实测**。

**缓存失效**：
- 图片文件名若含 `file_path` 哈希，则 TMDB 换图 → `file_path` 变 → 自动重新下载。**推荐此方案**。
- 若用 `{tmdb_id}.jpg` 固定名，则需靠"媒体记录的 `updated_at`"或定期刷新来失效。

### 2.6.4 格式建议

TMDB CDN 返回 **JPEG**（`.jpg`）或 **PNG**（logo）。
- 本地可转 **WebP** 省 30–50% 空间（用 Pillow / `cwebp`）。
- **但要注意**：转为 WebP 属于"对 TMDB Content 的衍生"，
  官方条款禁止 *"Make derivatives of the TMDB APIs or TMDB Content."*（见 2.7）。
  **格式转换是否算"derivative"在条款中没有明确界定 → 存在法律不确定性，标为不确定。**
  **保守做法：保留原始 JPEG/PNG 字节，仅做尺寸选择（选 TMDB 已提供的尺寸），不做再编码。**

## 2.7 条款与限流

### 2.7.1 速率限制（官方原文）

来源：<https://developer.themoviedb.org/docs/rate-limiting>

> *"As of December 16, 2019, we have disabled the original API rate limiting (**40 requests every 10 seconds**.)"*
>
> *"While our legacy rate limits have been disabled for some time, we do still have some upper limits to help mitigate needlessly high bulk scraping. **They sit somewhere in the 40 requests per second range. This limit could change at any time** so be respectful of the service we have built and **respect the `429` if you receive one**."*

**归纳**：
- 旧的硬限流（**40 次 / 10 秒**）**已于 2019-12-16 取消**。
- 现行上限：**"40 次/秒左右"**，官方措辞是 *"somewhere in the … range"*，**不精确、可随时更改**。
- 出限流返回 **`429`**（官方错误表里错误码 `25` 的文案仍写着 *"over the allowed limit of (40)"*，属于历史文案残留）。

**工程结论**：**不要贴着 40/s 跑**。自建刮削器建议**全局令牌桶限制在 5–10 req/s**，
批量首次刮削用低并发 + 断点续传，夜间跑。

### 2.7.2 缓存要求（官方原文，逐字）★

来源：**TMDB API Terms of Use** — <https://www.themoviedb.org/documentation/api/terms-of-use>
（运营主体：*"operated by TiVo Platform Technologies LLC"*）

在 "1.C. Restrictions" 下列出的禁止项中，**原文**：

> **"Cache, for longer than 6 months, any information obtained through or from TMDB or the TMDB APIs."**

**正确理解**：这是**禁止性条款**，即
👉 **不得把从 TMDB 获取的任何信息缓存超过 6 个月。**

**注意这不是"必须缓存 6 个月"，而是"缓存上限 6 个月"。**
所以自建刮削器**必须实现缓存有效期**（建议设 30–90 天，远低于 6 个月上限），
并且在超过期限后**重新拉取或清除**。

同条款下其他与本项目相关的禁止项（原文）：

> *"Use, or create any Application that, in Our sole discretion, (i) uses an excessive amount of bandwidth, (ii) degrade or impairs, including in part, access to, or use of, TMDB, its systems and servers, or the TMDB APIs; or (iii) otherwise adversely impacts the stability of TMDB, its systems and servers, or adversely impacts other users of TMDB or the TMDB APIs."*
>
> *"Make derivatives of the TMDB APIs or TMDB Content."*
>
> *"Use TMDB as an image hosting service for banner advertisements, graphics, etc."*
>
> *"Sell, lease, or sublicense the TMDB APIs, access to the TMDB APIs, or TMDB Content, or derive revenues from the use or provision of TMDB, the TMDB APIs, or TMDB Content, whether for direct commercial or monetary gain or otherwise, except as expressly permitted in a written agreement between You and TMDB…"*
>
> *"Use the TMDB APIs or TMDB Content in connection with, including for training, a machine learning (ML) or artificial intelligence (AI) based Application."*

**终止条款（原文）**：

> *"If TMDB terminates Your license, or You terminate your license, You must immediately cease all use of the TMDB APIs, TMDB Content, and any TMDB API key(s), and you must promptly **delete or otherwise purge all TMDB Content, including any cached content**."*

> ⚠️ **对自建刮削器的实际含义**：
> 你的图片缓存 + SQLite 里的元数据 **都属于受此条款约束的 "cached content"**。
> 必须提供"**清除全部刮削数据**"的功能，以应对许可终止。

### 2.7.3 商用限制（官方原文）

> *"**Commercial Use Requires a Commercial Agreement.** The license in Paragraph 1.A above does not permit any commercial use of TMDB, the TMDB APIs, or TMDB Content. Selling, leasing, or sublicensing the TMDB APIs, access to the TMDB APIs, or TMDB Content, or **deriving revenues** from the use or provision of TMDB, the TMDB APIs, or TMDB Content, for commercial or monetary gain, directly or indirectly, is … considered a commercial use and is only permitted under a separate written agreement between You and TMDB."*

官方 FAQ 补充：

> *"Our API is free to use for **non-commercial purposes** as long as you attribute TMDB as the source of the data and/or images."*
> *"Your project is considered commercial if **the primary purpose is to create revenue for the benefit of the owner**."*

联系方式：`sales@themoviedb.org`；<https://www.themoviedb.org/api-for-business>

> **对你的项目**：个人自用的桌面刮削器 + 播放器 → 非商用，**免费**，但**必须署名**（见下）。
> 一旦对外销售或内嵌广告变现 → 需要商用协议。

### 2.7.4 署名要求（attribution，官方原文）

来源：<https://developer.themoviedb.org/docs/faq>（"What are the attribution requirements?"）

> *"You shall use the TMDB logo to identify your use of the TMDB APIs. **You shall place the following notice prominently on your application: 'This product uses the TMDB API but is not endorsed or certified by TMDB.'**"*
>
> *"Any use of the TMDB logo in your application shall be **less prominent** than the logo or mark that primarily describes the application and your use of the TMDB logo shall not imply any endorsement by TMDB. When attributing TMDB, **the attribution must be within your application's 'About' or 'Credits' type section**."*
>
> *"When using a TMDB logo, we require you to use **one of our approved logos**."*

**Logo 与品牌规范**：<https://www.themoviedb.org/about/logos-attribution>

官方品牌色（该页原文）：
- Primary Color (Dark blue) — Hex `#0d253f`, RGB `13, 37, 63`
- Secondary Color (Light blue) — Hex `#01b4e4`, RGB `1, 180, 228`
- Tertiary Color (Light green) — 页面亦列出（本次未完整摘录）

> *"As per our terms of use, every application that uses our data or images is required to properly attribute TMDB as the source."*

**必须做的三件事（抄进你的"关于"页面）**：
1. 放置**官方批准的 TMDB logo**（不得改色/改宽高比/翻转/旋转）；
2. 显著位置写明：**"This product uses the TMDB API but is not endorsed or certified by TMDB."**
3. 链接指向 <https://www.themoviedb.org>，且 logo 不得比你的应用自身标识更显眼。

> **中文文案参考（自拟，需与英文原文同时保留）**：
> "本产品使用 TMDB API，但未获得 TMDB 的认可或认证。"
> —— ⚠️ 官方**只规定了英文原文**，中文翻译是**我自拟的**，不具有条款效力，**建议中英并列**。

### 2.7.5 API Key 不能硬编码在客户端

**必须如实说明**：

- **TMDB 官方文档与 FAQ 中，本次核实未找到任何"不要把 API Key 放进客户端"的明文规定。**
  官方 FAQ 里相关的是"不得隐瞒身份"条款（见下）。
- 但**工程上必须避免硬编码**，理由与依据如下：

| 依据 | 内容 |
|---|---|
| 条款「不得隐瞒身份」 | 原文：*"Attempt to cloak or conceal Your identity, or the identity of any website, program, service, application, or other product (collectively 'Application(s)') that accesses TMDB or the TMDB APIs, when using or requesting authorization to use TMDB APIs."* |
| 条款「Key 可被停用」 | 错误码 `10`：*"Suspended API key: Access to your account has been suspended, contact TMDB."* —— Key 泄漏可能导致**你自己的配额被他人消耗直至被停用** |
| 条款「终止后须清除 Key」 | *"you must promptly delete or otherwise purge all TMDB Content, including any cached content"* 且须 *"cease all use of … any TMDB API key(s)"* —— 硬编码的 Key 无法远程失效 |
| 通用安全实践 | 反编译/抓包即可提取客户端内嵌 Key |

**推荐做法（工程建议）**：

1. **单机自用桌面应用（你的场景）**：官方文档明确允许 `api_key` 查询参数，
   而单机应用**没有"服务端"可以藏 Key**。此时可行方案是：
   - **让用户在设置页填自己的 Key**，本地加密保存（如 `keyring` 库 / Windows DPAPI / macOS Keychain）；
   - 首次启动引导用户到 <https://www.themoviedb.org/settings/api> 申请。
   - 这样 Key 属于用户自己，**不违反任何条款**，也不存在"你泄漏别人 Key"的问题。
2. **不要把 Key 提交进 Git**：`.gitignore` 掉配置文件；用环境变量或用户级配置。
3. **若要做多人/联网服务**：必须自建后端代理，Key 只存在服务端，客户端调你自己的 API。
   —— 但注意：**这会触发"不得转售/转授 API 访问权"的条款风险**（*"Sell, lease, or sublicense the TMDB APIs, access to the TMDB APIs"*）。
   **是否需要商用协议，取决于你的分发方式 → 建议直接咨询 `sales@themoviedb.org`。**

### 2.7.6 分级/评分数据的版权注意点

**官方原文（FAQ「API legal notice」）**：

> *"**We do not claim ownership of any of the images or data in the API.** We comply with the Digital Millennium Copyright Act (DMCA) and expeditiously remove infringing content when properly notified. Any data and/or images you upload you expressly grant us a license to use. **You are prohibited from using the images and/or data in connection with libelous, defamatory, obscene, pornographic, abusive or otherwise offensive content.**"*

**要点**：
1. **TMDB 不主张对其 API 中图片/数据的所有权** —— 版权可能属于**原始权利人**（制片方、发行方、分级机构）。
   → **你不能把 TMDB 的海报当作自己的素材再分发**，尤其是用于**商业宣传物料**。
2. **分级数据的来源**：TMDB 的分级由**社区贡献**（community-driven），
   官方认证表（`/certification/*/list`）是 TMDB 整理的结果，**不是**各国分级机构的官方授权数据。
   → 展示时**不应声称"数据来自 MPA/CBFC 官方"**。
   → 官方原文只保证这是 *"the officially supported movie certifications on TMDB"*
     —— **注意措辞是"TMDB 官方支持的"，而不是"官方分级机构认证的"**。
3. **评分数据的来源**：`vote_average` / `vote_count` / `popularity` 均为 **TMDB 用户投票与 TMDB 自有算法**，
   **不是** IMDb 评分、不是烂番茄。**不要标注成 IMDb 分数。**
   （`/external_ids` 里的 `imdb_id` 只是**标识符**，不含评分。）

## 2.8 TVDB / OMDb 的定位与差异

> 说明：本节只写**我实际查证到的**内容。凡查不到的明确标注。

### 2.8.1 TheTVDB（TVDB）

**来源**：官方 OpenAPI 规范 <https://thetvdb.github.io/v4-api/swagger.yml>
（本次成功下载 115,347 字节，`title: TVDB API V4`，`version: 4.7.10`，`servers: [{"url": "https://api4.thetvdb.com/v4"}]`）

**官方鉴权流程（规范内 `info.description` 原文）**：

> *"1. Use the `/login` endpoint and provide your API key as `apikey`. If you have a user-supported key, also provide your subscriber PIN as `pin`. Otherwise completely remove `pin` from your call."*
> *"2. Executing this call will provide you with a bearer token, which is **valid for 1 month**."*
> *"3. Provide your bearer token for subsequent API calls by … including in the header of all direct API calls: `Authorization: Bearer [your-token]`"*

**安全方案（规范原文）**：

```yaml
components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT
```

**`POST /login`**：summary 原文 *"create an auth token. The token has one month validation length."*
请求体必填 `apikey`（string），可选 `pin`（string）。返回 `data.token`。

**官方关于 `score` 的说明（原文，很有价值）**：

> *"'score' is a field across almost all entities. We generate scores for different types of entities in various ways, so **no assumptions should be made about the meaning of this value**. It is simply used to hint at relative popularity for sorting purposes."*

**官方关于搜索的说明（原文）**：

> *"Our search index includes series, movies, people, and companies. **Search is limited to 5k results max.**"*

**⚪ 查不到**：
- TVDB 的**速率限制数值**：我在官方 OpenAPI 规范中**全文检索 `rate` / `throttl` / `429` 均无命中**，
  且 `support.thetvdb.com` 与 `raw.githubusercontent.com` 上的 TVDB README **在本机网络下连接失败/超时**。
  → **TVDB 的具体免费额度与限流数字，我未能查证。**
- TVDB 的**免费/付费分层与价格**：同上，未能查证。
  （规范里出现 *"If you have a user-supported key, also provide your subscriber PIN"*，
   说明存在"用户支持型 key + 订阅者 PIN"的机制，但**具体条款未能查证**。）

**定位小结（基于已查证部分）**：
TVDB 是**剧集（TV）领域的元数据源**，其剧集/季/集的**播出顺序与分季结构**在社区中被认为比 TMDB 更贴近实际播出
（这是 🟡 **社区普遍说法，我未取得官方对比文档**）。
鉴权成本比 TMDB 高：需要 `apikey`（+可选 `pin`）换取 **1 个月有效期的 JWT**，再带 Bearer。
**与 TMDB 的打通点**：TMDB 的 `GET /3/tv/{id}/external_ids` 返回 `tvdb_id`。

### 2.8.2 OMDb

**来源**：官网 <https://www.omdbapi.com/> 与 <https://www.omdbapi.com/apikey.aspx>（本次两者均成功抓取）

**官方原文要点**：

> *"The OMDb API is a RESTful web service to obtain movie information, **all content and images on the site are contributed and maintained by our users**."*

**请求格式（官方原文）**：

```
http://www.omdbapi.com/?apikey=[yourkey]&      # 数据请求
http://img.omdbapi.com/?apikey=[yourkey]&      # Poster API
```

**参数（官方表）**：

| 参数 | 必填 | 有效值 | 默认 | 说明（官方原文） |
|---|---|---|---|---|
| `i` | 可选* | — | 空 | *"A valid IMDb ID (e.g. tt1285016)"* |
| `t` | 可选* | — | 空 | *"Movie title to search for."* |
| `type` | 否 | `movie, series, episode` | 空 | |
| `y` | 否 | — | 空 | *"Year of release."* |
| `plot` | 否 | `short, full` | `short` | |
| `r` | 否 | `json, xml` | `json` | |
| `callback` | 否 | — | 空 | JSONP |
| `v` | 否 | — | `1` | *"API version (reserved for future use)."* |

> 官方注：*"Please note while both 'i' and 't' are optional **at least one argument is required**."*

**搜索参数**：`s`（必填，*"Movie title to search for."*）、`type`、`y`、`r`、`page`（*"Page number to return."*，范围 1–100）、`callback`、`v`

**免费额度（官方原文）**：API Key 申请页 Account Type 下拉框中的选项为：

```
Patreon
FREE! (1,000 daily limit)
```

→ **免费层 = 1,000 次/天**（官方原文明示）。付费层通过 **Patreon** 赞助获得。

**Poster API 限制（官方原文）**：

> *"The Poster API is only available to patrons. Currently over 280,000 posters, updated daily with resolutions up to 2000x3000."*

**版权（官方原文）**：

> *"All content licensed under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)."*
> *"This site is not endorsed by or affiliated with IMDb.com."*

**⚠️ 关键法律点**：OMDb 的内容是 **CC BY-NC 4.0（署名 - 非商业性使用）**。
**任何商业用途都不被允许**（除非另有安排）。且**必须署名**。

**定位小结**：
OMDb 是**以 IMDb ID 为主键的轻量查询服务**，适合用来：
- 用 IMDb ID `tt...` 反查**IMDb 评分**（`imdbRating`）与 **Rotten Tomatoes / Metacritic 分数**；
- 拿 `totalSeasons` 等粗粒度剧集信息。
鉴权最简单（一个 `apikey` 查询参数，无 token 交换），但**免费额度低（1000/天）**、**Poster API 需付费**、**许可为 NC**。

### 2.8.3 三者对比（仅就已查证内容）

| 维度 | TMDB | TheTVDB | OMDb |
|---|---|---|---|
| 主键 | TMDB id（+ `imdb_id`/`tvdb_id` 可互查） | TVDB id | **IMDb id (`tt...`)** |
| 鉴权 | `api_key` 查询参数 **或** `Authorization: Bearer`（**两者等价**） | `POST /login` 换 JWT（**1 个月有效期**），再 Bearer | `apikey` 查询参数 |
| 语言支持 | **强**（`language` 参数 + `translations` + `alternative_titles`） | 规范含多语言别名（`Alias`/`TranslationSimple` 模型），细节未查证 | ⚪ 未查证 |
| 图片 CDN | **有**（`/configuration` 给全部尺寸） | 有 artwork 端点（`/artwork/{id}`），细节未查证 | **Poster API 仅付费** |
| 分级数据 | **有**（`release_dates` / `content_ratings` + 认证表；**无 CN**） | ⚪ 未查证 | ⚪ 未查证 |
| 速率限制 | **~40 req/s**（官方原文） | ⚪ **未查证** | **1,000 次/天**（免费层，官方原文） |
| 缓存条款 | **不得超过 6 个月**（官方条款原文） | ⚪ 未查证 | ⚪ 未查证（但内容为 CC BY-NC 4.0） |
| 许可 | 免费**非商用** + 强制署名；商用需协议 | ⚪ 未查证 | **CC BY-NC 4.0** |
| 适合拿什么 | **综合主力**：电影/剧集/人物/图片/分级/多语言 | 剧集的**播出顺序与分季结构**（社区说法） | **IMDb / RT / Metacritic 评分**，按 IMDb id 查 |

> **给自建刮削器的结论**：**以 TMDB 为主力，TVDB 仅用于补齐剧集播出顺序，OMDb 仅在需要 IMDb 评分时少量调用**（注意 1000/天 与 NC 许可）。

---

# 第三部分：落地蓝图（Python 桌面应用）

> 本节是**工程设计方案**，与前两节的"事实"性质不同。
> 其中的技术选型、打分公式、目录结构均为**我的设计建议**，已在必要处标注。

## 3.1 数据模型建议（SQLite）

### 3.1.1 设计原则

1. **中文表名可用，但建议加 ASCII 别名**。SQLite 支持 UTF-8 标识符（写 SQL 时用双引号或不加引号均可），
   但**跨语言 ORM / 迁移工具 / 命令行调试时中文表名容易出问题**。
   → 建议：**物理表名用 ASCII（如 `media`），在文档和 ORM 类名上用中文语义**；或反之但统一。
   下面两套都给出。
2. **TMDB 数据是"缓存"**，必须能整体清除（见 2.7.2 的终止条款）。
   → 建议把**用户数据**（播放进度、收藏）与**刮削缓存**分库或分表前缀，
   这样"清除刮削数据"不会误删用户进度。
3. **原图 URL 不落库**，只存 `file_path` + `size`，运行时拼（因为 `secure_base_url` 可能变）。

### 3.1.2 建表 SQL

```sql
PRAGMA journal_mode = WAL;        -- 并发读写（UI 线程 + 刮削线程）
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

-- ============================================================
-- 1. 媒体（电影 或 剧集，二合一）
-- ============================================================
CREATE TABLE IF NOT EXISTS "媒体" (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id         INTEGER,                      -- TMDB id
    media_type      TEXT    NOT NULL CHECK(media_type IN ('movie','tv')),
    title           TEXT    NOT NULL,             -- 本地化标题（zh-CN 优先）
    original_title  TEXT,                         -- 原始标题（不随语言变）
    sort_title      TEXT,                         -- 去冠词/去标点，用于排序
    year            INTEGER,                      -- 电影=release_date 年；剧集=first_air_date 年
    overview        TEXT,
    tagline         TEXT,
    runtime         INTEGER,                      -- 电影：分钟；剧集：NULL（看 season/episode）
    episode_run_time INTEGER,                     -- 剧集单集时长（取 episode_run_time[0]）
    vote_average    REAL,
    vote_count      INTEGER,
    popularity      REAL,
    status          TEXT,
    original_language TEXT,                       -- ISO 639-1
    origin_country  TEXT,                         -- JSON 数组
    poster_path     TEXT,                         -- TMDB file_path，不是完整 URL
    backdrop_path   TEXT,
    logo_path       TEXT,
    genres          TEXT,                         -- JSON: [{"id":18,"name":"剧情"}]
    production_companies TEXT,                    -- JSON
    spoken_languages     TEXT,                    -- JSON（注意电影/剧集结构不同，统一后存）
    budget          INTEGER,                      -- 电影，0 表示未公开
    revenue         INTEGER,
    homepage        TEXT,
    collection_id   INTEGER,                      -- belongs_to_collection.id
    collection_name TEXT,
    -- 刮削元数据
    scrape_state    TEXT NOT NULL DEFAULT 'pending'
                    CHECK(scrape_state IN ('pending','matched','confirmed','failed','ignored')),
    match_score     REAL,                         -- 自动匹配得分，便于人工复核排序
    scrape_lang     TEXT,                         -- 拉取时用的 language
    scraped_at      INTEGER,                      -- unix ts，用于 6 个月缓存策略
    tmdb_raw        TEXT,                         -- 原始 JSON（可选，便于字段扩展）
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_media_tmdb
    ON "媒体"(media_type, tmdb_id) WHERE tmdb_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_media_title      ON "媒体"(title);
CREATE INDEX IF NOT EXISTS ix_media_sort       ON "媒体"(sort_title, year);
CREATE INDEX IF NOT EXISTS ix_media_state      ON "媒体"(scrape_state);
CREATE INDEX IF NOT EXISTS ix_media_scraped_at ON "媒体"(scraped_at);

-- ============================================================
-- 2. 剧集（电视剧专属属性，与 媒体 1:1）
-- ============================================================
CREATE TABLE IF NOT EXISTS "剧集" (
    media_id            INTEGER PRIMARY KEY REFERENCES "媒体"(id) ON DELETE CASCADE,
    number_of_seasons   INTEGER,       -- 注意：不含 Specials
    number_of_episodes  INTEGER,       -- 注意：≠ Σseasons[].episode_count
    first_air_date      TEXT,
    last_air_date       TEXT,
    in_production       INTEGER,       -- bool
    series_type         TEXT,          -- tv.type，如 'Scripted'
    networks            TEXT,          -- JSON
    created_by          TEXT,          -- JSON
    next_episode_to_air TEXT,          -- JSON
    last_episode_to_air TEXT           -- JSON
);

-- ============================================================
-- 3. 季
-- ============================================================
CREATE TABLE IF NOT EXISTS "季" (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id      INTEGER NOT NULL REFERENCES "媒体"(id) ON DELETE CASCADE,
    tmdb_season_id INTEGER,            -- seasons[].id
    season_number INTEGER NOT NULL,    -- 0 = Specials ★
    name          TEXT,
    overview      TEXT,
    air_date      TEXT,                -- 可能为 NULL
    episode_count INTEGER,
    poster_path   TEXT,
    vote_average  REAL,
    scraped_at    INTEGER,
    UNIQUE(media_id, season_number)
);
CREATE INDEX IF NOT EXISTS ix_season_media ON "季"(media_id, season_number);

-- ============================================================
-- 4. 集
-- ============================================================
CREATE TABLE IF NOT EXISTS "集" (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id       INTEGER NOT NULL REFERENCES "季"(id) ON DELETE CASCADE,
    tmdb_episode_id INTEGER,
    episode_number  INTEGER NOT NULL,
    episode_type    TEXT,               -- 'standard' / ...（其他取值未查证）
    name            TEXT,
    overview        TEXT,
    air_date        TEXT,
    runtime         INTEGER,
    still_path      TEXT,
    vote_average    REAL,
    vote_count      INTEGER,
    production_code TEXT,
    -- 本地文件关联（一个"集"可能对应多个文件：多版本/多清晰度）
    has_file        INTEGER NOT NULL DEFAULT 0,
    scraped_at      INTEGER,
    UNIQUE(season_id, episode_number)
);
CREATE INDEX IF NOT EXISTS ix_episode_season ON "集"(season_id, episode_number);
CREATE INDEX IF NOT EXISTS ix_episode_air    ON "集"(air_date);

-- ============================================================
-- 5. 人物
-- ============================================================
CREATE TABLE IF NOT EXISTS "人物" (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_person_id     INTEGER NOT NULL UNIQUE,
    name               TEXT NOT NULL,   -- 官方：人名不本地化
    also_known_as      TEXT,            -- JSON
    gender             INTEGER,         -- 编码含义未查证
    birthday           TEXT,
    deathday           TEXT,
    place_of_birth     TEXT,
    known_for_department TEXT,
    biography          TEXT,
    profile_path       TEXT,
    popularity         REAL,
    imdb_id            TEXT,
    homepage           TEXT,
    scraped_at         INTEGER
);
CREATE INDEX IF NOT EXISTS ix_person_name ON "人物"(name);

-- ============================================================
-- 6. 媒体人物（演职员关联，多对多 + 职务）
-- ============================================================
CREATE TABLE IF NOT EXISTS "媒体人物" (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id     INTEGER NOT NULL REFERENCES "媒体"(id) ON DELETE CASCADE,
    person_id    INTEGER NOT NULL REFERENCES "人物"(id) ON DELETE CASCADE,
    -- 作用域：整部媒体 / 某一季 / 某一集
    scope        TEXT NOT NULL DEFAULT 'media'
                 CHECK(scope IN ('media','season','episode')),
    scope_id     INTEGER,               -- season.id 或 episode.id
    credit_type  TEXT NOT NULL CHECK(credit_type IN ('cast','crew','guest_star')),
    character    TEXT,                  -- cast：角色名（aggregate 时可能是 roles[] 聚合）
    role_json    TEXT,                  -- aggregate_credits 的 roles[]/jobs[] 原样 JSON
    department   TEXT,                  -- crew：部门（如 'Directing','Writing'）
    job          TEXT,                  -- crew：职务（如 'Director','Writer','Screenplay'）
    cast_order   INTEGER,               -- ★ cast[].order，升序 = 主演优先
    episode_count INTEGER,              -- aggregate：total_episode_count
    credit_id    TEXT
);
CREATE INDEX IF NOT EXISTS ix_mc_media   ON "媒体人物"(media_id, credit_type, cast_order);
CREATE INDEX IF NOT EXISTS ix_mc_person  ON "媒体人物"(person_id);
CREATE INDEX IF NOT EXISTS ix_mc_job     ON "媒体人物"(media_id, department, job);
CREATE INDEX IF NOT EXISTS ix_mc_scope   ON "媒体人物"(scope, scope_id);

-- ============================================================
-- 7. 图片（缓存清单）
-- ============================================================
CREATE TABLE IF NOT EXISTS "图片" (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_type   TEXT NOT NULL CHECK(owner_type IN ('movie','tv','season','episode','person','collection','company','network')),
    owner_id     INTEGER NOT NULL,      -- 对应本地表的 id
    image_type   TEXT NOT NULL CHECK(image_type IN ('poster','backdrop','logo','profile','still')),
    file_path    TEXT NOT NULL,         -- TMDB file_path
    iso_639_1    TEXT,                  -- NULL = 无语言标记
    vote_average REAL,
    vote_count   INTEGER,
    width        INTEGER,
    height       INTEGER,
    aspect_ratio REAL,
    is_primary   INTEGER NOT NULL DEFAULT 0,   -- 选图算法的胜出者
    -- 本地缓存状态
    local_path   TEXT,                  -- 相对 <data_dir>/images 的路径
    local_size   INTEGER,               -- 下载时用的尺寸档位（w342/w500/...）
    bytes        INTEGER,
    downloaded_at INTEGER,
    UNIQUE(owner_type, owner_id, image_type, file_path)
);
CREATE INDEX IF NOT EXISTS ix_image_owner     ON "图片"(owner_type, owner_id, image_type, is_primary);
CREATE INDEX IF NOT EXISTS ix_image_unfetched ON "图片"(local_path) WHERE local_path IS NULL;

-- ============================================================
-- 8. 分级（一分级一行，多国并存）
-- ============================================================
CREATE TABLE IF NOT EXISTS "分级" (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id     INTEGER NOT NULL REFERENCES "媒体"(id) ON DELETE CASCADE,
    country      TEXT NOT NULL,        -- ISO 3166-1，如 'US','GB','JP','HK','TW'
    certification TEXT,                -- 电影：release_dates[].certification
                                       -- 剧集：content_ratings[].rating
    -- 电影专属
    release_type INTEGER,              -- 1 Premiere / 2 Theatrical(limited) / 3 Theatrical
                                       -- 4 Digital / 5 Physical / 6 TV
    release_date TEXT,
    note         TEXT,
    descriptors  TEXT,                 -- JSON 数组（如 ['violence','language']）
    -- 查表得到的含义（来自 /certification/*/list）
    meaning      TEXT,
    cert_order   INTEGER,              -- 官方 order，用于"严于/松于"比较
    iso_639_1    TEXT,
    source       TEXT DEFAULT 'tmdb'
);
CREATE INDEX IF NOT EXISTS ix_cert_media   ON "分级"(media_id, country);
CREATE INDEX IF NOT EXISTS ix_cert_country ON "分级"(country, certification);

-- ============================================================
-- 9. 外部 ID（跨数据源打通）
-- ============================================================
CREATE TABLE IF NOT EXISTS "外部ID" (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_type  TEXT NOT NULL CHECK(owner_type IN ('movie','tv','season','episode','person')),
    owner_id    INTEGER NOT NULL,
    source      TEXT NOT NULL CHECK(source IN ('tmdb','imdb','tvdb','tvrage','wikidata','facebook','instagram','twitter','freebase')),
    external_id TEXT NOT NULL,
    UNIQUE(owner_type, owner_id, source)
);
CREATE INDEX IF NOT EXISTS ix_extid_lookup ON "外部ID"(source, external_id);
CREATE INDEX IF NOT EXISTS ix_extid_owner  ON "外部ID"(owner_type, owner_id);

-- ============================================================
-- 10. 本地文件（不属于任务清单，但没有它无法播放）
-- ============================================================
CREATE TABLE IF NOT EXISTS "文件" (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT NOT NULL UNIQUE,     -- 绝对路径
    size_bytes    INTEGER,
    mtime         INTEGER,
    -- 解析结果
    parsed_title  TEXT,
    parsed_year   INTEGER,
    parsed_season INTEGER,
    parsed_episode INTEGER,
    parsed_episode_end INTEGER,             -- 多集合并文件 E01-E02
    air_date      TEXT,                     -- 日期式命名
    absolute_episode INTEGER,               -- 绝对集号
    edition       TEXT,                     -- 多版本：1080p / 2160p / Director's Cut
    -- 关联
    media_id      INTEGER REFERENCES "媒体"(id) ON DELETE SET NULL,
    episode_id    INTEGER REFERENCES "集"(id) ON DELETE SET NULL,
    match_state   TEXT NOT NULL DEFAULT 'pending'
                  CHECK(match_state IN ('pending','matched','confirmed','failed','ignored')),
    last_seen_at  INTEGER,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_file_media ON "文件"(media_id, episode_id);
CREATE INDEX IF NOT EXISTS ix_file_state ON "文件"(match_state);

-- ============================================================
-- 11. 播放进度（用户数据，独立于刮削缓存）
-- ============================================================
CREATE TABLE IF NOT EXISTS "播放进度" (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id        INTEGER NOT NULL REFERENCES "文件"(id) ON DELETE CASCADE,
    position_ms    INTEGER NOT NULL DEFAULT 0,
    duration_ms    INTEGER,
    watched        INTEGER NOT NULL DEFAULT 0,   -- 是否看完
    play_count     INTEGER NOT NULL DEFAULT 0,
    audio_track    INTEGER,                      -- 上次音轨索引
    subtitle_track INTEGER,                      -- 上次字幕索引
    playback_rate  REAL DEFAULT 1.0,
    updated_at     INTEGER NOT NULL,
    UNIQUE(file_id)
);
CREATE INDEX IF NOT EXISTS ix_progress_updated ON "播放进度"(updated_at DESC);

-- ============================================================
-- 12. 分级含义表（从 /certification/*/list 同步，勿硬编码）
-- ============================================================
CREATE TABLE IF NOT EXISTS "分级含义" (
    media_type    TEXT NOT NULL CHECK(media_type IN ('movie','tv')),
    country       TEXT NOT NULL,
    certification TEXT NOT NULL,
    meaning       TEXT,
    cert_order    INTEGER,
    synced_at     INTEGER,
    PRIMARY KEY(media_type, country, certification)
);

-- ============================================================
-- 13. 刮削任务队列（断点续传 + 重试）
-- ============================================================
CREATE TABLE IF NOT EXISTS "刮削任务" (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,      -- 'search'|'details'|'season'|'images'|'credits'|'cert'
    payload     TEXT NOT NULL,      -- JSON
    state       TEXT NOT NULL DEFAULT 'queued'
                CHECK(state IN ('queued','running','done','failed','dead')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    next_run_at INTEGER,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_job_ready ON "刮削任务"(state, next_run_at);
```

### 3.1.3 关键索引说明

| 索引 | 为什么需要 |
|---|---|
| `ux_media_tmdb` | 防止同一 TMDB 实体被重复入库（刮削幂等性的基础） |
| `ix_media_sort` | 海报墙按标题排序（`sort_title` 已去掉 "The " 等冠词） |
| `ix_media_scraped_at` | **实现 6 个月缓存策略**：定期扫描过期记录重新刮削 |
| `ix_image_unfetched` | 部分索引，只索引未下载的图，让"待下载队列"查询极快 |
| `ix_mc_job` | 按 `department`/`job` 筛"导演/编剧" |
| `ix_mc_media` (含 `cast_order`) | 演员按 `order` 升序取前 N 位 |
| `ix_extid_lookup` | 用 IMDb/TVDB id 反查本地记录（避免重复调 API） |
| `ix_job_ready` | 任务队列取下一个待办 |
| WAL 模式 | UI 线程读、后台线程写，不互相阻塞 |

### 3.1.4 缓存时效（合规要点）

```sql
-- 找出超过 90 天未刷新的媒体（远低于官方 6 个月上限）
SELECT id, media_type, tmdb_id FROM "媒体"
WHERE scraped_at IS NULL
   OR scraped_at < strftime('%s','now') - 90*86400;

-- 提供"一键清除全部 TMDB 数据"（条款要求可 purge）
-- 注意：只清刮削缓存，保留 "播放进度"
DELETE FROM "图片"; DELETE FROM "分级"; DELETE FROM "媒体人物";
DELETE FROM "人物"; DELETE FROM "集"; DELETE FROM "季"; DELETE FROM "剧集";
DELETE FROM "媒体"; DELETE FROM "外部ID"; DELETE FROM "分级含义";
-- 再递归删除 <data_dir>/images 目录
```

## 3.2 命名解析规则

### 3.2.1 支持的命名模式

| # | 模式 | 示例 | 解析结果 |
|---|---|---|---|
| 1 | 电影：名称 (年份) | `影片名 (1999).mkv` | title=`影片名`, year=1999 |
| 2 | 电影：名称.年份 | `The.Matrix.1999.1080p.BluRay.mkv` | title=`The Matrix`, year=1999, edition=1080p |
| 3 | 电影：名称 年份 | `影片名 1999.mkv` | 同 1 |
| 4 | 剧集：SxxExx | `剧名 S01E02.mkv` | title, season=1, episode=2 |
| 5 | 剧集：SxxExx 多集 | `剧名 S01E01E02.mkv` / `S01E01-E02` | season=1, episode=1, episode_end=2 |
| 6 | 剧集：1x02 | `剧名 1x02.mkv` | season=1, episode=2 |
| 7 | 剧集：日期式（日番/脱口秀） | `剧名 2023-04-15.mkv` / `20230415` | air_date=2023-04-15（**无季集号**） |
| 8 | 剧集：绝对集号 | `剧名 - 123.mkv` / `剧名 第123话.mkv` | absolute_episode=123 |
| 9 | 剧集：中文季集 | `剧名 第01季第02集.mkv` / `剧名 第一季 第二集` | season=1, episode=2 |
| 10 | 剧集：Season 目录 | `剧名/Season 01/xxx.mkv` | season=1（**从父目录取**） |
| 11 | 多版本 | `剧名 S01E02 - 1080p.mkv` / `... - 2160p.mkv` | edition 记录，**两条 文件 记录指向同一 集** |
| 12 | 中文数字 | `剧名 第二季 第三集.mkv` | season=2, episode=3 |

### 3.2.2 解析流水线（推荐实现顺序）

```
输入：绝对路径 path，原始文件名 fname，父目录名 pname，祖父目录名 gpname

① 归一化
   - Unicode NFKC 规范化（全角→半角：ＡＢＣ→ABC、１２３→123）
   - 去扩展名
   - 把 . _ - 空格 统一视为分隔符，但**保留原始串**用于最终匹配
   - 中文数字→阿拉伯数字（只在"第X季/第X集/第X话"上下文中转换，避免误伤片名）

② 提取技术标签并剥离（写入 edition / 元信息，不进标题）
   - 分辨率：2160p|1080p|720p|480p|4K|UHD|8K
   - 来源：BluRay|BDRip|WEB-DL|WEBRip|HDTV|DVDRip|Remux|HDRip
   - 编码：x264|x265|H264|H265|HEVC|AVC|AV1|XviD
   - 音频：DTS(-HD)?|AC3|EAC3|TrueHD|Atmos|AAC|FLAC|DD5\.1|7\.1
   - HDR：HDR10\+?|HDR|DV|DoVi|Dolby ?Vision|SDR
   - 其他：PROPER|REPACK|EXTENDED|REMASTERED|IMAX|UNCUT|导演剪辑版|国语|粤语|中字|简繁
   - 发布组：结尾的 -GROUP（**仅在已剥离技术标签后**判断，避免把 "- 1080p" 当发布组）

③ 剧集标记提取（优先级从上到下，命中即停）
   a. S(\d{1,2})E(\d{1,3})([-~]?E?(\d{1,3}))?     → 模式 4/5
   b. (\d{1,2})x(\d{1,3})                        → 模式 6
   c. 第(\d+|[一二三四五六七八九十百]+)[季部]\s*第?(\d+|[一二三四五六七八九十百]+)[集话話]  → 模式 9/12
   d. (\d{4})[-._]?(\d{2})[-._]?(\d{2})          → 模式 7（日期式）
      ⚠️ 必须校验：年 1900-2099，月 1-12，日 1-31，否则可能是片名里的数字
   e. 第(\d+|[一二三四五六七八九十]+)[集话話]     → 绝对集号（模式 8）
   f. 末尾独立的 1-4 位数字（需配合父目录是剧集目录才敢用）→ 绝对集号
   g. 父目录匹配 Season\s*(\d+)|S(\d+)|第(\d+|[一二三四五六七八九十]+)季 → 模式 10

④ 年份提取（仅在未命中剧集标记时）
   - \(?((19|20)\d{2})\)?   → year
   - 优先取**括号内**的年份，其次取独立 token 的年份
   - 若同时存在两个年份（如 "1999 2019"），电影取第一个

⑤ 标题提取
   - 取"年份左边界"或"第一个技术标签左边界"之前的部分（取更早者）
   - 去掉首尾分隔符、方括号内容（[字幕组]、【高清】）
   - 保留原始大小写（TMDB 匹配时不区分大小写）

⑥ 目录兜底
   - 若文件名几乎全是数字/无意义（如 "CD1"、"01.mkv"），
     用父目录名当标题；若父目录是 "Season 01"/"S01"，则用祖父目录名
```

### 3.2.3 多版本（`- 1080p` / `- 2160p`）处理

**核心原则：一个"集"（或"电影"）可以对应多个"文件"。**

```
"文件" 表：
   │
   ├── 剧名 S01E02 - 1080p.mkv   → episode_id = 42, edition='1080p'
   ├── 剧名 S01E02 - 2160p.mkv   → episode_id = 42, edition='2160p'  ← 同一个 episode_id
   └── 剧名 S01E02 - 导演剪辑版.mkv → episode_id = 42, edition='导演剪辑版'
```

- **`文件` 表对 `episode_id` 不设唯一约束**（只对 `path` 唯一）。
- 播放时**由用户选择版本**，或按规则自动选（如优先 2160p > 1080p，或优先上次播放的 edition）。
- `播放进度` 挂在 **`file_id`** 上 —— 因为不同清晰度的播放位置理论上可以不同。
  若希望"进度跨版本共享"，改为挂在 `episode_id` 上，并在 UI 上说明。
- **UI 上集列表只显示一行**（按 `episode_id` 聚合），角标显示"2 个版本"。

### 3.2.4 用 Python 实现的库建议

**⚪ 调查限制说明**：本次任务未要求、我也未逐一实测第三方解析库。
→ **关于 `guessit` / `PTN` / `anitomy` 等库的具体能力与准确率，本报告不做断言。**
以下是**基于命名模式本身**的实现建议：

- **必须自研正则主流程**（上表 12 种模式是中文场景的核心，通用库对"第X季第X集"、
  "绝对集号"、"日期式"的支持通常需要自己补）。
- 可用通用库作**第一遍粗解析**，再用自研规则**覆盖/补充**中文特有模式。
- **必须写单元测试**：把上表 12 种模式各准备 3–5 个真实文件名样本，作为回归测试集。

## 3.3 刮削流程

### 3.3.1 总体流程

```
┌─────────────────────────────────────────────────────────────────┐
│ 阶段 0：扫描（Scan）                                             │
│  - 遍历媒体库根目录（可配置多个）                                 │
│  - 过滤：扩展名白名单（mkv/mp4/avi/ts/m2ts/wmv/mov/flv/webm/iso） │
│  - 过滤：忽略 @eaDir / .DS_Store / Thumbs.db / sample / trailer   │
│  - 用 (path, size, mtime) 判断是否新增/变更 → 只处理增量           │
│  - 对已消失的文件：标记 last_seen_at，不立即删除（可能是外接盘离线）│
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段 1：解析（Parse）—— 见 3.2                                   │
│  输出：parsed_title / year / season / episode / air_date / edition│
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段 2：搜索匹配（Search & Match）★ 见 3.3.2                     │
│  - 先查本地"外部ID"/已入库媒体（避免重复调 API）                  │
│  - 否则调 /search/movie 或 /search/tv                            │
│  - 打分 → 最高分 > 阈值 → 自动确认；否则进人工确认队列             │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段 3：详情（Details）                                          │
│  - /movie/{id}?append_to_response=credits,images,release_dates,  │
│                 videos,recommendations,external_ids             │
│  - /tv/{id}?append_to_response=aggregate_credits,images,         │
│              content_ratings,videos,external_ids                │
│  - 剧集再逐季：/tv/{id}/season/{n}（含 episodes[].crew/guest_stars）│
│  - 中文兜底：overview 为空 → 用 language=en-US 再请求一次        │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段 4：图片（Images）                                           │
│  - 用 include_image_language=zh,en,null 拉图                    │
│  - 按 2.6.2 打分选 is_primary                                    │
│  - 下载所需尺寸 → 本地缓存（并发 ≤ 8）                            │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段 5：入库（Persist）                                          │
│  - 单事务写入，幂等（依赖 ux_media_tmdb 唯一索引做 UPSERT）        │
│  - 记录 scraped_at                                              │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段 6：失败重试 / 人工确认                                       │
│  - 网络类失败 → 指数退避重试（最多 5 次）→ 仍失败标记 dead        │
│  - 匹配分低 / 多个候选分接近 → 进"待确认"列表，UI 让用户选        │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3.2 匹配度打分（核心算法）

**问题**：文件名经过清洗后是 `title` + `year`（+ 剧集还有 `season`/`episode`），
要和 TMDB 搜索返回的候选列表比对，挑出最可能的那一个。

**打分维度与权重建议**：

| 维度 | 权重 | 说明 |
|---|---|---|
| 标题相似度（归一化后） | 45 | 主力信号 |
| 年份匹配 | 30 | 强信号；**年份不符几乎一定是错配** |
| 别名/译名匹配 | 15 | 用 `alternative_titles` 与 `original_title` 补充 |
| 集数/剧集结构合理性 | 10 | 剧集：`number_of_seasons`/`number_of_episodes` 是否覆盖解析出的集号 |
| 热度微调 | ≤5 | `popularity` 只作**同分时的打破平局**，不能主导 |

**实现草案**：

```python
import re, unicodedata, difflib
from datetime import date

def normalize(s: str) -> str:
    """NFKC 归一化 + 转小写 + 去标点/空白 + 去常见冠词。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", s)      # 保留中文字符
    s = re.sub(r"\s+", " ", s).strip()
    # 去掉英文冠词前缀（the/a/an）
    s = re.sub(r"^(the|a|an)\s+", "", s)
    return s

def title_sim(a: str, b: str) -> float:
    """0..1 的标题相似度。"""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # 包含关系给高分（"流浪地球" vs "流浪地球2" 不该是 1.0，故给 0.9）
    if na in nb or nb in na:
        return 0.90
    # 序列相似度
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    # 字符集合 Jaccard（对中文字序不敏感）
    sa, sb = set(na.replace(" ", "")), set(nb.replace(" ", ""))
    jac = len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0
    return max(ratio, jac * 0.95)

def score_candidate(parsed, cand, media_type: str) -> tuple[float, dict]:
    """
    parsed: {'title','year','season','episode','absolute_episode'}
    cand:   TMDB 搜索结果单条
    返回 (总分 0..100, 明细)
    """
    D = {}
    # ---------- 1) 标题（45 分）----------
    cand_titles = [cand.get("title") or cand.get("name") or "",
                   cand.get("original_title") or cand.get("original_name") or ""]
    cand_titles += cand.get("_alt_titles", [])          # 由 alternative_titles 补充
    best = max((title_sim(parsed["title"], t) for t in cand_titles if t), default=0.0)
    D["title"] = best * 45

    # ---------- 2) 年份（30 分）----------
    d = cand.get("release_date") or cand.get("first_air_date") or ""
    cand_year = int(d[:4]) if len(d) >= 4 and d[:4].isdigit() else None
    if parsed.get("year") and cand_year:
        diff = abs(parsed["year"] - cand_year)
        if diff == 0:
            D["year"] = 30
        elif diff == 1:
            D["year"] = 18        # 跨年上映很常见，宽容
        elif diff == 2:
            D["year"] = 6
        else:
            D["year"] = -25       # 年份差太多 = 强烈扣分（可负）
    else:
        D["year"] = 8             # 缺信息，给中性偏低分，避免误判为完美匹配

    # ---------- 3) 剧集结构合理性（10 分）----------
    D["struct"] = 0
    if media_type == "tv":
        ns, ne = cand.get("number_of_seasons"), cand.get("number_of_episodes")
        s, e = parsed.get("season"), parsed.get("episode")
        if s is not None and ns and s <= ns:
            D["struct"] += 5
        elif s is not None and ns:
            D["struct"] -= 10     # 解析出的季号超出该剧季数 → 很可能是错配
        if e is not None and ne and e <= ne:
            D["struct"] += 3
        elif parsed.get("absolute_episode") is not None and ne:
            D["struct"] += 3 if parsed["absolute_episode"] <= ne else -5
        # Specials 季号 0 是合法值，不扣分
        D["struct"] += 2

    # ---------- 4) 热度打破平局（≤5 分）----------
    pop = cand.get("popularity") or 0.0
    D["pop"] = min(pop / 100.0, 1.0) * 5

    # ---------- 5) 轻微惩罚热门错配 ----------
    # 若标题相似度极低但热度极高，往往是搜索把热门片排前面了
    if best < 0.35:
        D["pop"] = 0

    total = sum(D.values())
    return max(0.0, min(total, 100.0)), D

# ---------- 决策阈值（建议值，需按实际语料调）----------
#  >= 78 且 与第二名差距 >= 12  → 自动确认
#  >= 62                      → 入库但标记 "待人工确认"
#  <  62                      → 进入"未匹配"列表，UI 提供手动搜索
```

**为什么这样设计**（针对中文场景的考量）：

1. **年份是强信号**：中文片名大量重复（《无间道》有电影也有剧；同名翻拍很多），
   年份不符时**必须重罚**，否则会稳定错配到热门同名作品。
2. **`popularity` 不能主导**：TMDB 搜索会把热门片排前面。
   若用 popularity 主导，刮削《流浪地球》(2019) 时可能被其他高热度片抢走。
3. **包含关系给 0.90 而非 1.0**：`"流浪地球"` 被 `"流浪地球2"` 包含，
   若给 1.0 会导致续集错配。**年份维度会补上这一刀。**
4. **剧集结构校验很有用**：解析出 `S12E05` 但候选剧只有 3 季 → 几乎肯定是错配。
5. **绝对集号需特殊处理**：日番（如《海贼王》）用绝对集号，
   而 TMDB 用 `season_number`/`episode_number`。此时应：
   - 用 `/tv/{id}/episode_groups` 查是否有绝对顺序分组；
   - 或用 `episodes[].air_date` 反查（绝对集号 → 第 N 集 → 对应季集号）；
   - 兜底：只用**日期式命名**匹配（模式 7）。

### 3.3.3 人工确认 UI（必须有）

自动匹配不可能 100% 正确。**必须有**：
- **"待确认"列表**：显示 `本地文件名` | `当前匹配` | `候选列表（带海报缩略）` | `得分`；
- **手动搜索框**：调 `/search/multi`，让用户直接搜；
- **直接填 TMDB ID**：最高效的兜底（用户从 TMDB 网站复制 URL 里的数字）；
- **批量确认**：同目录同剧的文件应支持"应用到同目录全部"；
- **错误上报**：记录用户纠正样本，用于后续调阈值。

### 3.3.4 重试策略

```python
# 分类处理（依据官方错误码表 https://developer.themoviedb.org/docs/errors）
RETRYABLE_HTTP = {429, 500, 502, 503, 504}       # 限流 / 服务端 / 维护
NO_RETRY_HTTP  = {400, 401, 403, 404, 405, 422}  # 参数/鉴权/不存在 —— 重试无意义

# 401 → 提示用户 Key 失效（错误码 7/10/3）
# 404 → 标记该候选无效，回退到次高分候选（不要直接判失败）
# 429 → 指数退避 + 全局降速（令牌桶下调）
```

**退避**：`delay = min(2 ** attempt + random.uniform(0, 1), 60)`，`attempt` 上限 5。

**断点续传**：所有任务进 `刮削任务` 表，进程重启后从 `state='queued'` 继续。
**幂等**：入库用 `INSERT ... ON CONFLICT(media_type, tmdb_id) DO UPDATE`。

## 3.4 图片缓存与海报墙性能

### 3.4.1 尺寸选择（结合 2.2 与 2.6）

| 用途 | 尺寸档 | 为什么 |
|---|---|---|
| 海报墙网格（默认） | `w342` | 主流卡片宽度 150–200px，2x 屏需 ~400px |
| 海报墙网格（大屏/4K） | `w500` | |
| 详情页海报 | `w500` | |
| 详情页背景横幅 | `w1280` | |
| 卡片背景小横幅 | `w780` | |
| 演员头像 | `w185` | |
| 剧集剧照（集列表） | `w300` | |
| Logo | `original` | **官方：SVG 不缩放，必须 original** |

**磁盘占用估算**（用于给用户设预期）：
- `w342` 海报平均约 40–60 KB
- `w500` 海报平均约 80–120 KB
- `w1280` 背景平均约 200–400 KB
- **1,000 部电影（海报 w342 + 背景 w780 + 若干头像）≈ 150–300 MB**

### 3.4.2 本地目录结构

```
<data_dir>/
  images/
    poster/w342/<tmdb_id>_<hash8>.jpg
    poster/w500/<tmdb_id>_<hash8>.jpg
    backdrop/w780/<tmdb_id>_<hash8>.jpg
    backdrop/w1280/<tmdb_id>_<hash8>.jpg
    profile/w185/<person_tmdb_id>_<hash8>.jpg
    still/w300/<episode_tmdb_id>_<hash8>.jpg
    logo/original/<company_id>_<hash8>.<ext>     # SVG 只能 original
  cache/
    poster_thumbs/<tmdb_id>.webp      # 预生成的极小缩略图（见 3.4.3）
  db/
    library.sqlite
```

`<hash8>` = `md5(file_path)[:8]` —— TMDB 换图时 `file_path` 变，**自动失效**，无需额外逻辑。

### 3.4.3 海报墙性能（这是最容易做砸的地方）

**问题**：10,000 部影片的海报墙，若每张缩略图都从磁盘读 50 KB 再缩放 → 滚动必卡。

**方案（按性价比排序）**：

1. **预生成极小缩略图**
   - 启动后（或空闲时）把 `w342` 原图批量缩成 **高 270px、WebP 质量 75** 的缩略图
     （海报 2:3 比例 → 180×270），平均 **8–15 KB**。
   - 存到 `cache/poster_thumbs/`。
   - **内存占用**：180×270×4 字节 ≈ 194 KB/张（解码后）。
     一次性缓存 500 张 ≈ 97 MB —— **必须做 LRU 限制**。

2. **LRU 内存缓存**
   - 用 `functools.lru_cache(maxsize=N)` 或自研 LRU，**按屏位数量的 2–3 倍**设上限。
   - 只缓存**当前可见 + 前后各一屏**的缩略图。

3. **异步加载 + 占位符**
   - 网格先渲染灰色占位块（固定 2:3 尺寸），
   - 后台线程池（`ThreadPoolExecutor(max_workers=4)`）按可见性加载，
   - 加载完成后通过 Qt 信号回主线程更新（**不要在工作线程碰 UI**）。

4. **虚拟滚动 / 视图回收**
   - 只创建可见区域的 widget，滚出视口即回收（Qt 用 `QListView` + 自定义 delegate，
     **不要用 `QGridLayout` 塞几千个 `QLabel`** —— 这是新手最常见的性能杀手）。

5. **数据库查询优化**
   - 海报墙只需 `id, title, poster_path, year, vote_average` —— **别 `SELECT *`**。
   - 封面路径可直接由 `tmdb_id + hash8` 拼出，**避免 JOIN `图片` 表**；
     或建一个视图/物化表缓存"待展示的封面路径"。

**给 Qt 的具体建议（推测性设计建议）**：
- `QListView`（IconMode）+ 自定义 `QStyledItemDelegate`；
- delegate 只负责绘制（`paint()`），图像由外部 LRU 提供；
- 通过 `setUniformItemSizes(True)` 开启布局优化。

> ⚠️ **不确定项**：上述 Qt 类名与 API 行为基于我对 Qt 的既有知识，
> **本次核实未查证 Qt 官方文档**。落地前请以你所用的 Qt 版本文档为准（PySide6 / PyQt6 API 有差异）。

### 3.4.4 下载调度

```
优先级队列：
  1. 当前可见的海报（用户正在看）
  2. 详情页图片
  3. 后台批量补齐（夜间/idle）

并发：图片 ≤ 8；API ≤ 4（远低于官方 ~40/s）
去重：同一 file_path 只下一次（用 图片.file_path 唯一索引保证）
断点：下载中断留 .part 临时文件，成功后原子 rename
校验：Content-Length 与磁盘字节数比对；JPEG 校验 SOI/EOI 魔数
```

## 3.5 与播放器联动

### 3.5.1 技术选型（两条路，各有取舍）

| 方案 | 优点 | 缺点 |
|---|---|---|
| **A. 内嵌 libmpv**（`python-mpv` / `mpv` widget） | 完整控制播放状态；进度/字幕/音轨 API 齐全；渲染质量高 | 需打包 mpv 二进制；跨平台打包复杂 |
| **B. 调用外部播放器**（mpv/VLC/PotPlayer 命令行） | 实现极简；用户可用自己熟悉的播放器 | **拿不到实时进度**，只能靠 IPC/命令行参数近似 |

**推荐 A（内嵌 libmpv）**，理由：任务要求"续播、进度、字幕"，
方案 B 难以可靠拿到秒级进度（除非用 mpv 的 JSON IPC，那本质仍是 A 的变体）。

> ⚠️ **不确定项**：`python-mpv` 的具体 API 名称与版本兼容性，**本次未查证**，落地前请查其官方文档。

### 3.5.2 续播（核心交互）

```
打开影片
   │
   ├─ 查 "播放进度" WHERE file_id = ?
   │     │
   │     ├─ 记录存在 且 position_ms > 30_000 且 watched = 0
   │     │     → 弹"继续观看（从 12:34 开始）" / "从头播放"
   │     │
   │     ├─ watched = 1
   │     │     → 从头播放（但保留"已看"标记）
   │     │
   │     └─ 无记录 → 从头播放
   │
   └─ 播放中：每 5–10 秒（且状态变化时）写一次 "播放进度"
         ⚠️ 不要每帧写库 —— SQLite 会被打爆
```

**完成判定**：`position_ms / duration_ms > 0.92` 且剩余 < 3 分钟 → 标记 `watched = 1`。
（阈值可配置；0.92 是常见做法，**非官方标准**。）

**退出时**：`closeEvent` 里**同步写一次**最终进度（不能只靠定时器）。

### 3.5.3 进度同步的服务端维度（可选扩展）

若用户还有 Emby/Jellyfin/Plex 服务器，可考虑双向同步：
- **Emby/Jellyfin** 有官方的 Playback Reporting / Sessions API，可回写 `PositionTicks`；
- **Plex** 有 `PUT /:/progress` 端点。

> ⚠️ **本节为设计方向提示，未做核实**：
> Emby/Jellyfin/Plex 的具体端点路径、鉴权方式、`PositionTicks` 单位（100 纳秒？）等，
> **本次任务未查证，不要直接照抄**。若要做，请先查各家官方 API 文档。

### 3.5.4 字幕

| 需求 | 做法 |
|---|---|
| 内封字幕 | libmpv 自动识别 → `track-list` 列出 → 用户选 |
| 外挂字幕 | **自动匹配同名字幕**：`影片.mkv` ↔ `影片.srt` / `影片.chs.srt` / `影片.zh-CN.ass`；同目录 `Subs/` 子目录 |
| 字幕语言识别 | 从文件名后缀推断：`chs/cht/zh-CN/zh-TW/sc/tc/eng/en` |
| 字幕编码 | **中文 SRT 常见 GBK/Big5** → 读取时用 `chardet`/`charset-normalizer` 探测，转 UTF-8 存临时文件再喂给 mpv |
| 字幕下载 | **TMDB 不提供字幕**。需接第三方字幕源（如 OpenSubtitles）→ **本次未查证其 API 与条款** |
| 记录选择 | 把 `subtitle_track` 写进 `播放进度`，下次自动选中 |

### 3.5.5 音轨

- libmpv `track-list` 列出音轨 → UI 提供切换；
- 常见标记：`国语`/`粤语`/`日语`/`英语`/`Commentary`/`5.1`/`7.1`/`Atmos`；
- 把用户选择写进 `播放进度.audio_track`。

### 3.5.6 海报墙点击播放的完整链路

```
海报墙点击卡片
   │
   ├─ 电影 → 查 "文件" WHERE media_id = ? AND match_state IN ('matched','confirmed')
   │           │
   │           ├─ 多个文件（多版本）→ 弹版本选择
   │           └─ 单个文件 → 直接播放
   │
   └─ 剧集 → 进入剧集详情页
               ├─ 季选择器（★ 排除或单列 season_number = 0 的 Specials）
               ├─ 集列表（显示集名/剧照/简介/时长/已看标记）
               └─ 点击某一集 → 查 "文件" WHERE episode_id = ?
                                 → 版本选择（如有）→ 播放
```

**"下一集"**逻辑：
```
当前 (season, episode) → 同季 episode+1 → 若不存在 → 下一季 episode=1
→ 若都不存在 → 提示"已是最新一集"
（注意跳过 episode_number 不连续的缺口）
```

**未刮削/未匹配的影片**：海报墙应有"未识别"分组，显示文件名 + 通用占位图，
点击直接播放（**不要因为刮削失败就不能看片** —— 这是可用性底线）。

---

# 4. 交付物与目录

- 报告文件：`/home/xgl/python/网盘管理_V2/docs/研究/Vidhub_与TMDB刮削底座.md`
- 目录：`/home/xgl/python/网盘管理_V2/docs/研究/`

---

# 5. 「没查到 / 不确定」清单（如实汇总）

> 本节刻意写得直白。凡我**没有实际验证**的，一律列出，**不做圆场**。

## 5.1 关于 VidHub App（南京欧米，id `1659622164`）

> **状态已更新**：最初用 `web_fetch` 只拿到 JS 导航壳，一度判定"大面积查不到"（见 1.8.1 的更正说明）。
> 改用**官方 sitemap + curl + Apple 官方 iTunes Lookup API** 后，
> 任务清单中的五组功能**大部分已取得官方原文**。下表是**剩余**的未查到项。

### 5.1.1 ✅ 已确认（不再是"查不到"）

| 项 | 结论 | 证据类型 |
|---|---|---|
| 使用 TMDB 刮削 | **确认**，官方原文 *"Movie and TV metadata comes from … TMDB"* | 🟢 官方文档 + App Store 描述 |
| Emby / Jellyfin / Plex | **确认支持** | 🟢 官方文档 + App Store 描述 |
| WebDAV / SMB | **确认支持**（官方原文 *"currently supports WebDav and SMB protocols"*） | 🟢 官方文档 |
| 云盘（阿里/百度/移动云盘/115/123/Dropbox/OneDrive/Google Drive/Premiumize） | **确认支持** | 🟢 官方文档 + App Store 描述 |
| HDR / 杜比视界 | **确认支持**（官方原文含 "HDR, Dolby Vision"） | 🟢 官方文档 |
| 倍速播放 | **确认支持** | 🟢 官方文档 |
| 字幕（内封 + 外挂 SRT/SSA/ASS/SUB） | **确认支持**，含自动加载规则与在线字幕搜索 | 🟢 官方文档 |
| 媒体库 / 海报墙 / 分类排序 / 全局搜索 | **确认支持** | 🟢 官方文档 |
| 播放进度同步 | **确认走 iCloud**（同步 watch history 等） | 🟢 官方文档 |
| 平台 | **确认含 Android Mobile / Android TV** | 🟢 官方文档 |
| 版本与日期 | **3.0.6，2026-09-12**；首次上架 2023-05-18 | 🟢 Apple 官方 API |
| 主体公司 | **Nanjing Oumi Software Development Co., Ltd.** | 🟢 Apple 官方 API + 官网版权 |
| 免费下载 + "VidHub VIP" 内购 | **确认**（订阅制，跨 Apple 平台权益） | 🟢 官方文档 + Apple API |

### 5.1.2 ⚪ 仍然查不到 / 不确定

| # | 未查到/不确定的内容 | 我尝试了什么 | 状态 |
|---|---|---|---|
| A1 | **杜比全景声（Dolby Atmos）是否支持** | 抓取全部 15 篇官方文档 + App Store 描述，全文检索 `Atmos` | ⚪ **官方全文零命中**。**不能说不支持，但没有官方依据** |
| A2 | **DTS / DTS:X / TrueHD 是否支持** | 同上，检索 `DTS` / `TrueHD` | ⚪ **官方全文零命中** |
| A3 | **多音轨切换**是否支持 | 通读官方《Settings Overview》的 Playback 小节 | ⚪ 该节**只列了字幕设置，未列音轨切换**。**不确定**是有此功能但未文档化，还是确实没有 |
| A4 | **HDR 的具体制式**（HDR10 / HDR10+ / HLG） | 全文检索 | ⚪ 官方只写 "HDR"，**未细化制式** |
| A5 | **PGS / SUP 图形字幕**是否支持 | 全文检索 | ⚪ 官方只列 SRT/SSA/ASS/SUB |
| A6 | **VIP 的具体价格**（订阅档位/买断） | Apple Lookup API、官方文档 | ⚪ **查不到**。Lookup API 不返回内购价目表；官方文档无价格页 |
| A7 | **免费版与 VIP 的功能边界** | 同上 | ⚪ **查不到**。官方**未发布功能对比表** |
| A8 | **Trakt 集成的具体范围** | 官方《3rd-party-app-integration》提到 *"do not depend on Trakt login or Trakt Scrobbling"* | 🟢 间接确认**存在** Trakt 集成；⚪ 但**支持范围（scrobble 哪些内容、是否需 VIP）查不到** |
| A9 | **Android 版的分发渠道与版本号** | Apple Lookup API（只覆盖 Apple）；官方文档 | ⚪ 官方文档确认有 Android 版，但**未找到 Google Play / 官网下载页**。**版本号查不到** |
| A10 | **iOS/macOS 最低版本口径矛盾** | 官方文档 vs Apple API | ⚠️ 官方文档写 **iOS 14.1 / macOS 10.15**，Apple API 返回 **`minimumOsVersion: 16.0`**。**两者矛盾**，我判断是文档未随版本更新（**推断**），**以 App Store 的 16.0 为准** |
| A11 | **南京欧米 VidHub 的官方源码仓库** | 检索 GitHub | ⚪ **未找到**。应为闭源（**推断**，非官方声明） |
| A12 | **`id6761185000`（MOBILE ALCHEMY LTD）与南京欧米的关系** | Apple Lookup API 分别查询两个 id | ✅ **已确认二者无关**：`sellerName`、`bundleId`（`vidhub.app` vs `com.mac.utility.media.hub`）、描述、版本**均不同** → **是两个不同公司的不同 App**。⚪ 但**两者是否有商标纠纷/授权关系，查不到** |
| A13 | **各功能的实机表现** | — | 🧪 **我完全没有安装、购买或运行该 App**，**未做任何实机验证**。上表全部为**官方文字**，不等于实测结论 |
| A14 | **官方文档的"最后更新 Aug 5, 2026"是否覆盖当前 3.0.6 版** | 对比官方文档与 Apple API 的版本信息 | ⚠️ **不确定**。文档更新日期（2026-08-05）**早于**当前版本发布日（2026-09-12），**部分功能可能未文档化** |

### 5.1.3 关于"音轨/杜比"的特别说明

任务清单明确要求确认「**音轨**」与「**杜比**」。如实回答：

- **音轨切换**：官方文档**没有**这一项设置。⚪ **查不到**（既无法确认支持，也无法确认不支持）。
- **杜比**：官方**只确认了杜比视界（Dolby Vision，视频）**；**杜比全景声（Dolby Atmos，音频）官方全文零命中**。
- 因此：**如果你看到任何二手资料说"VidHub 支持杜比全景声"，那是没有官方依据的说法**，
  请自行在 App 里实测（我未实测）。

## 5.2 关于 TMDB

| # | 未查到/不确定的内容 | 说明 |
|---|---|---|
| B1 | `vote_average` 的分值范围（0–10） | 官方文档中**未见明文**。实测数据与社区共识指向 0–10，**但这是我基于示例数值的推断，非官方明文** |
| B2 | `popularity` 的量纲/上限/跨类型可比性 | 官方只说它是 *"lifetime popularity score"* 与排序提示值，**未给公式与范围**。**不可用于跨类型比较** |
| B3 | `status` 字段的完整枚举（电影/剧集） | 官方示例只见 `"Released"` / `"Ended"`，**无完整取值表** |
| B4 | 剧集 `type` 字段的完整枚举 | 官方示例只见 `"Scripted"`，**无完整取值表** |
| B5 | `episode_type` 的完整枚举 | 官方示例只见 `"standard"`，**无完整取值表** |
| B6 | `gender` 字段的整数编码含义 | 官方示例出现 0/1/2，**无官方对照表** |
| B7 | `with_status` / `with_type`（discover）数字枚举的含义 | 官方只给 `[0..5]` / `[0..6]`，**未给数字到语义的映射** |
| B8 | `/3/find/{external_id}` 的 `external_source` 可选值 | 官方规范中该参数存在但**未列出枚举** |
| B9 | TMDB 是否返回 `Retry-After` 响应头 | 官方限流文档**未提及**。**需实测** |
| B10 | 429 的具体触发阈值 | 官方只说 *"somewhere in the 40 requests per second range"*，**不精确且会变** |
| B11 | 「不得制作衍生作品」是否包含**图片格式转换**（JPEG→WebP） | 条款原文禁止 *"Make derivatives of the TMDB APIs or TMDB Content."*，但**未明确界定格式转换是否属于 derivative** → **法律不确定性**。保守做法：不做再编码 |
| B12 | 「不得把 API Key 放客户端」的官方明文 | **TMDB FAQ / 文档中我没有找到任何此类明文规定**。2.7.5 中的依据与做法**是我基于条款与通用安全实践给出的工程建议，不是官方要求** |
| B13 | `release_dates` 中 `descriptors` 数组的取值表 | 官方示例均为空数组 `[]`，**未给枚举** |
| B14 | 分级数据的具体来源机构/授权链条 | 官方只说 *"officially supported … on TMDB"*，**未说明上游**。可确定的是**社区贡献驱动**，**不应对外声称来自各国分级机构** |
| B15 | 中文（zh-CN）翻译的**覆盖率** | 未做统计。**实测建议：务必实现 en-US 兜底**（官方文档已说明存在覆盖缺口） |

## 5.3 关于 TheTVDB / OMDb

| # | 未查到/不确定的内容 | 我尝试了什么 | 状态 |
|---|---|---|---|
| C1 | **TheTVDB 的速率限制数值** | 下载并全文检索官方 OpenAPI 规范（115 KB）：`rate` / `throttl` / `429` **零命中**；尝试 `support.thetvdb.com` 与 `raw.githubusercontent.com/thetvdb/v4-api` → **连接失败/超时** | **未查证** |
| C2 | **TheTVDB 的免费/付费分层与价格** | 同上 | **未查证**。规范中出现 *"user-supported key"* + *"subscriber PIN"*，说明存在该机制，但**具体条款未查证** |
| C3 | TheTVDB 是否提供分级数据、图片 CDN 细节 | 规范中有 `/artwork/{id}` 等端点，但**未逐一核实** | **未查证** |
| C4 | TheTVDB 的许可条款与署名要求 | 规范中未含许可条款 | **未查证** |
| C5 | OMDb 的**付费层**（Patreon）具体额度与价格 | 只抓取到官网下拉框显示 `FREE! (1,000 daily limit)` | 付费档位**未查证** |
| C6 | OMDb 各字段（`imdbRating`/`Ratings[]`/`totalSeasons` 等）的完整定义 | 本次未展开抓取其 swagger | **未查证**（官网提到有 `swagger.yaml` / `swagger.json`，**我未下载**） |
| C7 | TVDB 与 TMDB 在"剧集播出顺序"上孰优的**官方**对比 | 未找到官方对比文档 | 我引用的"TVDB 分季更贴近播出"是 **🟡 社区说法**，**非官方结论** |

## 5.4 关于第三部分（蓝图）中的不确定项

| # | 内容 | 说明 |
|---|---|---|
| D1 | 第三方命名解析库（guessit / PTN / anitomy）的能力与准确率 | **本次未实测**，报告中**未做任何断言**，只给出自研正则方案 |
| D2 | Qt（PySide6/PyQt6）具体 API 名称与行为 | 基于既有知识，**未查证官方文档**。落地前请核对版本文档 |
| D3 | `python-mpv` 的 API 与版本兼容性 | **未查证** |
| D4 | Emby / Jellyfin / Plex 的进度回写端点、鉴权、`PositionTicks` 单位 | **未查证**。报告中已标注"不要照抄" |
| D5 | 字幕下载源（如 OpenSubtitles）的 API 与条款 | **未查证** |
| D6 | 匹配打分公式的具体权重（45/30/15/10/5）与阈值（78/62/12） | **我的设计建议，非行业标准**。必须用你自己的真实文件名语料调参 |
| D7 | `watched` 判定阈值 0.92、写进度间隔 5–10 秒 | **经验值，非标准** |
| D8 | 磁盘占用估算（40–60 KB / 1000 部 ≈ 150–300 MB） | **基于常见 JPEG 大小的估算，未实测 TMDB 实际文件尺寸** |
| D9 | SQLite 中文表名的跨工具兼容性 | **未实测**。我在 3.1.1 中已建议改用 ASCII 物理表名 |

## 5.5 关于 `nabilfarhann/Vidhub` —— 无不确定项

第一部分的事实（元数据、技术栈、功能、无刮削）**全部有原始证据**，
且通过**下载全量源码后逐文件核实**（`grep -rniE "tmdb|themoviedb|tvdb|scraper|scrape|nfo|imdb|douban|metadata|poster|banner|fanart"`
在业务代码中**零命中**）。

---

# 6. 来源清单

## 6.1 GitHub 仓库 `nabilfarhann/Vidhub`

| 内容 | URL |
|---|---|
| 仓库主页 | <https://github.com/nabilfarhann/Vidhub> |
| 元数据 API | <https://api.github.com/repos/nabilfarhann/Vidhub> |
| 语言构成 API | <https://api.github.com/repos/nabilfarhann/Vidhub/languages> |
| 文件树 API | `https://api.github.com/repos/nabilfarhann/Vidhub/git/trees/master?recursive=1` |
| README（raw） | `https://api.github.com/repos/nabilfarhann/Vidhub/readme`（`Accept: application/vnd.github.raw`） |
| 全量源码 | <https://codeload.github.com/nabilfarhann/Vidhub/tar.gz/refs/heads/master> |
| 关键源码文件 | `includes/config.php`、`includes/classes/VideoProcessor.php`、`upload.php`、`sql/vidhub.sql`、`.htaccess`、`composer.json` |

## 6.2 TMDB 官方

| 内容 | URL |
|---|---|
| **文档索引（Markdown 版）** | <https://developer.themoviedb.org/llms.txt> |
| 入门 | <https://developer.themoviedb.org/docs/getting-started> |
| FAQ（署名要求） | <https://developer.themoviedb.org/docs/faq> |
| 鉴权（Application） | <https://developer.themoviedb.org/docs/authentication-application> |
| 鉴权（User） | <https://developer.themoviedb.org/docs/authentication-user> |
| 限流 | <https://developer.themoviedb.org/docs/rate-limiting> |
| 图片基础 | <https://developer.themoviedb.org/docs/image-basics> |
| 图片语言/回退 | <https://developer.themoviedb.org/docs/image-languages> |
| 语言 | <https://developer.themoviedb.org/docs/languages> |
| 地区与 release type | <https://developer.themoviedb.org/docs/region-support> |
| append_to_response | <https://developer.themoviedb.org/docs/append-to-response> |
| 搜索与详情流程 | <https://developer.themoviedb.org/docs/search-and-query-for-details> |
| 热度与趋势 | <https://developer.themoviedb.org/docs/popularity-and-trending> |
| 错误码表 | <https://developer.themoviedb.org/docs/errors> |
| **API 使用条款（6 个月缓存）** | <https://www.themoviedb.org/documentation/api/terms-of-use> |
| Logo 与署名 | <https://www.themoviedb.org/about/logos-attribution> |
| API 注册 | <https://www.themoviedb.org/settings/api> |
| 商用 API | <https://www.themoviedb.org/api-for-business> |
| 服务状态 | <https://status.themoviedb.org> |
| OpenAPI 规范入口 | <https://developer.themoviedb.org/openapi> |
| 接口参考（示例） | <https://developer.themoviedb.org/reference/configuration-details> 等 `/reference/*` |

> 提示：以上 `/docs/*` 与 `/reference/*` 页面 URL **追加 `.md`** 均可取得 Markdown 版。

## 6.3 TheTVDB / OMDb

| 内容 | URL | 抓取状态 |
|---|---|---|
| TheTVDB API V4 OpenAPI 规范 | <https://thetvdb.github.io/v4-api/swagger.yml> | ✅ 成功（115,347 字节） |
| TheTVDB v4 API 仓库 | <https://github.com/thetvdb/v4-api> | ❌ 本机连接失败 |
| TheTVDB 支持站 FAQ | <https://support.thetvdb.com/kb/faq.php?id=62> | ❌ 本机连接失败/超时 |
| OMDb 官网（参数/许可） | <https://www.omdbapi.com/> | ✅ 成功 |
| OMDb API Key 申请（免费额度） | <https://www.omdbapi.com/apikey.aspx> | ✅ 成功 |

## 6.4 VidHub App（南京欧米，id `1659622164`）—— ✅ 已成功取得

| 入口 | URL | 抓取状态 |
|---|---|---|
| **官方文档索引（sitemap）** | <https://vidhub.okaapps.com/sitemap.xml> → `sitemap-posts.xml` | ✅ 成功（列出全部 15 篇） |
| 功能总览 | <https://vidhub.okaapps.com/what-does-vidhub-do/> | ✅ **全文成功**（curl） |
| 新手引导 | <https://vidhub.okaapps.com/beginners-guide/> | ✅ 成功 |
| 进阶（文件源/媒体服务器/云盘） | <https://vidhub.okaapps.com/advanced-for-beginners/> | ✅ 成功 |
| 媒体库（Continue Watching） | <https://vidhub.okaapps.com/use-the-media-library/> | ✅ 成功 |
| 文件源管理 | <https://vidhub.okaapps.com/use-file-source/> | ✅ 成功 |
| **命名规则** | <https://vidhub.okaapps.com/vidhub-video-file-naming-conventions/> | ✅ 成功 |
| 批量重命名 | <https://vidhub.okaapps.com/modifying-file-names-single-file-batch-rename/> | ✅ 成功 |
| **手动纠错（Edit Information）** | <https://vidhub.okaapps.com/edit-video-information-in-the-media-library-correction/> | ✅ 成功 |
| 播放界面 | <https://vidhub.okaapps.com/video-playback-interface/> | ✅ 成功 |
| **设置总览** | <https://vidhub.okaapps.com/settings-overview/> | ✅ 成功 |
| **iCloud 同步** | <https://vidhub.okaapps.com/synchronize/> | ✅ 成功 |
| 退款说明 | <https://vidhub.okaapps.com/how-to-request-an-apple-app-refund/> | 未展开 |
| Apple TV 暗场修正 | <https://vidhub.okaapps.com/fix-blurry-dark-scenes-on-apple-tv/> | ✅ 成功 |
| **内购恢复（VIP）** | <https://vidhub.okaapps.com/how-to-restore-your-in-app-purchase/> | ✅ 成功 |
| **第三方 App 联动（URL scheme）** | <https://vidhub.okaapps.com/3rd-party-app-integration/> | ✅ **全文成功** |
| 官方中文站（产品自述） | <https://zh.okaapps.com/> | ✅ 成功 |
| App Store 商品页（US） | <https://apps.apple.com/us/app/vidhub-video-library-player/id1659622164> | ⚠️ 浏览器访问被重定向到 Today 页；**改用 Apple 官方 API 取得全部元数据** |
| **Apple 官方 iTunes Lookup API（US）** | <https://itunes.apple.com/lookup?id=1659622164&country=us> | ✅ **成功（JSON，权威）** |
| **Apple 官方 iTunes Lookup API（CN）** | <https://itunes.apple.com/lookup?id=1659622164&country=cn> | ✅ 成功（含官方中文描述与 TMDB 署名） |
| 第 3 个实体（MOBILE ALCHEMY LTD） | <https://itunes.apple.com/lookup?id=6761185000&country=cn> | ✅ 成功（**证实是另一个 App**） |
| TestFlight 公测 | <https://testflight.apple.com/join/xnu9uZqE> | 未展开 |

> **方法学备注（供后续复用）**：
> 1. **Ghost 博客类官网**（本站即 Ghost 搭建）都有 `/sitemap.xml` → `sitemap-posts.xml`，**这是拿全站页面的最快路径**。
> 2. **Apple App Store 商品页**在浏览器中常因区域重定向拿不到，
>    但 **`https://itunes.apple.com/lookup?id=<APP_ID>&country=<CC>` 是 Apple 官方公开 API**，
>    返回官方 `description`、`version`、`price`、`sellerName`、`bundleId`、`averageUserRating` 等，**比抓网页可靠得多**。
> 3. **`bundleId` 是区分同名 App 的唯一可靠标识** —— 本次正是靠它发现"第三个 VidHub"。

---

# 7. 给决策者的一段话

1. **你给的 GitHub 链接不是那款播放器。** 它是 2019 年的 PHP "YouTube 克隆"网站，
   最后提交停在 2021-06，无许可证、无 release、单作者、数据库口令硬编码。
   **它对"网盘管理"项目没有可复用的价值**（既无刮削，也无播放器，也无网盘对接）。
   若要参考，唯一有价值的是"上传 → FFmpeg 生成缩略图 → 用户选封面"这条**很基础**的流程。

2. **那款 VidHub App 的功能清单，我这次取证成功了。**
   关键是换方法：官网是 Ghost 博客，走 `/sitemap.xml` 拿到全部 15 篇官方文档，`curl` 直接取全文；
   App Store 改用 **Apple 官方 iTunes Lookup API**（`itunes.apple.com/lookup?id=...`）拿官方元数据。
   **结论：TMDB 刮削、Emby/Jellyfin/Plex、SMB/WebDAV、各云盘、HDR/杜比视界、倍速、字幕、iCloud 进度同步 —— 全部有官方原文确认**（见 1.8.4）。
   **但仍是官方文字，不是实测** —— 我**没有**安装或运行该 App。

   另外发现一个**极易踩的坑**：App Store 上**有两个不同的 App 都叫 "VidHub"**：
   - 南京欧米（`com.mac.utility.media.hub`，id `1659622164`）← **这才是那款播放器**
   - MOBILE ALCHEMY LTD（`vidhub.app`，id `6761185000`）← **另一家公司的另一个 App**
   二手评测可能说的是后者。**引用任何第三方说法前先确认 bundle id。**

3. **一个意外收获，对你的"网盘管理 + 播放器"联动很有用**：
   VidHub 官方公开了一套完整的 URL-scheme 协议
   （`open-vidhub://x-callback-url/play`，带 `position` 入参 + `x-success` 回调返回 `position`/`duration`/`status: finished|stopped`）。
   你完全可以照这个设计做自己的"调起外部播放器并回收进度"接口，**甚至直接对接 VidHub**。详见 1.8.5。

4. **TMDB 底座部分可以直接开工。** 接口路径、参数、字段、图片尺寸、release type 枚举、
   语言回退规则、限流数字、6 个月缓存条款、署名文案 —— 全部有官方原文出处，本报告已逐条标注。
   顺带一个省时间的发现：**TMDB 官方文档页 URL 后面加 `.md` 就能拿到干净 Markdown**
   （索引：<https://developer.themoviedb.org/llms.txt>），比抓 HTML 可靠得多。

5. **三个最容易踩的中文坑，请务必记住**：
   - **TMDB 根本没有中国大陆（CN）分级**（电影 45 国、剧集 40 国，均无 CN）；
   - **`include_image_language` 要传 `zh` 而不是 `zh-CN`**（图片不支持区域变体），且必须带 `null`；
   - **`number_of_episodes` ≠ Σ`seasons[].episode_count`**（第 0 季 Specials 会污染统计）。

6. **合规三件事**：关于页面放 TMDB 官方 logo + 英文声明
   `"This product uses the TMDB API but is not endorsed or certified by TMDB."`；
   实现缓存过期（≤6 个月，建议 90 天）；实现"一键清除全部 TMDB 数据"。
   只要你是**个人自用、不收费**，就是免费且合规的。

7. **一个与 VidHub 官方设计相反的点，你需要做决策**：
   VidHub 官方明说 *"The file name is the only factor used for automatic matching; folder names and folder structure do not affect it."*
   —— 它**不看目录结构**。而本文 3.2 节建议**用 `Season 01/` 父目录兜底**。
   两者都成立，取舍是"可预测性"vs"召回率"。**建议主用文件名、目录仅作兜底**。
