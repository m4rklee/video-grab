# VideoGrab AI：视频解析与下载实现总结

本文档沉淀当前 MVP 中「链接解析 → 选格式 → 异步下载 → 浏览器取文件」的工程实现，便于交接与二次开发。

## 产品目标

- 用户粘贴公开视频链接，服务端解析标题、封面、时长、可选格式列表。
- 用户选择格式后创建下载任务，后端异步拉流写入临时目录，完成后通过 HTTP 提供文件供浏览器保存。
- 无数据库、无账号体系；不做 DRM 破解或绕过授权。

## 技术架构

| 层级 | 技术栈 | 说明 |
|------|--------|------|
| 前端 | Vue 3 + Vite + TypeScript | 默认开发端口 `9280`（`strictPort`），通过 Vite 代理访问后端。 |
| 后端 | FastAPI + uvicorn | 默认建议端口 `8028`（与前端代理一致）。 |
| 通用下载 | yt-dlp | `extract_info`（探测）与 `download`（拉取合并）。 |
| 系统依赖 | ffmpeg | 音视频合并等；健康检查 `GET /api/health` 会报告是否可用。 |

核心 HTTP API（与根目录 `README.md` 一致）：

- `GET /api/health`：依赖探测。
- `POST /api/video/probe`：解析链接，返回标题与格式选项。
- `POST /api/downloads`：创建任务。
- `GET /api/downloads/{job_id}`：任务状态。
- `GET /api/downloads/{job_id}/file`：完成后下载文件。

编排入口：`backend/app/services.py`（`probe_video`、`create_download_job`、`_run_download` 等）。

## 通用站点（非抖音）

流程：规范化 URL → `YoutubeDL(extract_info)` 得到 info → `_build_format_options` 映射为前端格式卡片；下载时 `YoutubeDL(download)`，模板输出到任务工作目录。

## 抖音（Douyin）专用链路

抖音在 `services` 中单独分支，核心模块为 `backend/app/douyin_web.py`（签名算法在 `backend/app/douyin_abogus.py`，依赖 `gmssl`）。

### 1. 链接与 ID

- **`normalize_video_url`**（`services.py`）：将 `jingxuan?modal_id=`、`modalId`、`aweme_id` 等规范为 `https://www.douyin.com/video/{数字ID}`。
- **`resolve_share_redirect`**（`douyin_web.py`）：跟随 `v.douyin.com` 短链至落地 URL，再由上层规范化。
- **`extract_aweme_id`**：从 `douyin.com/video/{id}` 或 `iesdouyin.com/share/video/{id}` 提取作品 ID。

### 2. 作品详情 `fetch_aweme_detail`（按顺序尝试）

1. **IES 分享页（优先）**  
   请求 `https://www.iesdouyin.com/share/video/{aweme_id}/`，使用移动端 Safari 风格 UA。从 HTML 中的 `window._ROUTER_DATA` 解析 JSON，在 `loaderData.*.videoInfoRes.item_list[0]` 取与 Web 详情相近的结构。  
   若遇 WAF 挑战页，会尝试按 MIT 参考实现中的思路解算 Cookie 后重试。  
   **参考**：解析与 WAF 处理思路参考开源项目 [rathodpratham-dev/douyin_video_downloader](https://github.com/rathodpratham-dev/douyin_video_downloader)（MIT），与源码文件头注释一致。

2. **www 站 Web API + a_bogus（回退）**  
   - 先尝试 **curl-cffi** 模拟 Chrome TLS（`chrome131`），查询参数与 UA/指纹对齐后请求 `https://www.douyin.com/aweme/v1/web/aweme/detail/`。  
   - 再回退 **httpx**：预热首页与视频页，可选从 HTML 提取 `msToken` 参与签名，带 `a_bogus` 查询串。

3. **全部失败**：抛出聚合错误信息（便于排查风控、网络环境）。

### 3. 探测与格式

- `aweme_detail_to_probe_info`：从详情中的 `video.play_addr`、`download_addr`、`bit_rate` 等组装与 yt-dlp 类似的 `formats` 列表。
- **`playwm` → `play`**：对播放 URL 做启发式替换，倾向无水印流（不保证全量场景永久有效）。

### 4. 解析阶段再回退（probe）

若抖音 Web 链路整体仍失败，`probe_video` 会再用 **yt-dlp** 对规范化后的 `www.douyin.com/video/{id}` 做一次 `extract_info`（便于本机已配置 Cookie 的用户）。

### 5. 下载阶段

- 优先 **`_run_douyin_download`**：`pick_download_url` 选择 CDN 直链，`httpx.stream` 写入文件。
- **`media_request_headers(aweme_id, media_url)`**：对 `aweme.snssdk.com`、`douyinvod.com` 等使用移动端 UA + `Referer: https://www.douyin.com/`，减少 CDN 拒绝或异常。
- 直链失败时 **`_run_download`** 会回退到 yt-dlp；若用户选择的是 `douyin|...` 合成格式，回退时会改用 `bv*+ba/b` 等 yt-dlp 可理解的格式串。

## Python 依赖要点

见 `backend/requirements.txt`，主要包括：`fastapi`、`uvicorn`、`yt-dlp`、`httpx`、`curl-cffi`、`gmssl`、`pytest`。

## 限制与合规

- 抖音各接口受 **地区、IP、风控策略** 影响；IES 页或 www 接口可能在不同时期改版，需持续维护。
- 工具仅应用于 **有权下载的公开或自有内容**；用户需遵守平台服务条款与当地法律。
- 部分环境仍需浏览器 **Cookie**（尤其是 yt-dlp 官方 Douyin 提取器路径）；文档与 UI 错误提示中已说明常见原因。

## 相关文件索引

| 文件 | 职责 |
|------|------|
| `backend/app/main.py` | 路由注册 |
| `backend/app/services.py` | 探测、任务、下载编排、URL 规范化 |
| `backend/app/douyin_web.py` | 抖音详情、分享页解析、媒体请求头 |
| `backend/app/douyin_abogus.py` | a_bogus 签名（来自 f2 生态，Apache 2.0） |
| `backend/tests/test_api.py` | API 与抖音分支单测（含 mock 与部分行为） |

---

*文档版本与代码库同步维护；修改下载链路时请同步更新本节。*
