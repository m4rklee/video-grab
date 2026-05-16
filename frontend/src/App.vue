<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { zoomTransform } from 'd3'
import { Transformer } from 'markmap-lib'
import { Markmap } from 'markmap-view'
import {
  AlertCircle,
  Archive,
  ArrowDownToLine,
  Bolt,
  CheckCircle2,
  Clock3,
  Download,
  FileCode2,
  Film,
  Gauge,
  ImageDown,
  Link,
  Loader2,
  Maximize2,
  MessageSquare,
  Minimize2,
  Search,
  Settings,
  ShieldCheck,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-vue-next'
import {
  loadStoredApiBase,
  loadStoredBilibiliCookie,
  loadStoredOpenAiApiKey,
  loadStoredOpenAiBaseUrl,
  loadStoredSummarizeModel,
  persistApiBase,
  persistBilibiliCookie,
  persistOpenAiApiKey,
  persistOpenAiBaseUrl,
  persistSummarizeModel,
  userSettingsRequestHeaders,
  withApiBase,
} from './apiConfig'
import {
  buildXmindArchiveBlob,
  exportMarkmapPngBlob,
  exportMarkmapSvgBlob,
  sanitizeFilename,
  triggerDownload,
  type MindmapTreeNode,
} from './mindmapExport'
import SearchPanel from './SearchPanel.vue'
import TasksPanel from './TasksPanel.vue'

type Health = {
  status: 'ok'
  ytdlp_available: boolean
  ffmpeg_available: boolean
  ffmpeg_path: string | null
  summarize_llm_ready?: boolean
}

type FormatOption = {
  format_id: string
  label: string
  ext: string | null
  resolution: string | null
  filesize: number | null
  note: string | null
}

type ProbeResponse = {
  title: string
  webpage_url: string
  extractor: string | null
  duration: number | null
  thumbnail: string | null
  formats: FormatOption[]
  recommended_format_id?: string | null
}

type JobStatus = {
  job_id: string
  status: 'queued' | 'downloading' | 'completed' | 'failed'
  progress: number
  filename: string | null
  error: string | null
  download_url: string | null
}

type SummarizeStatus = {
  job_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress: number
  error: string | null
  subtitle_source: string | null
  webpage_url: string | null
  result: Record<string, unknown> | null
}

type MainTab = 'search' | 'download' | 'tasks'

const mainTab = ref<MainTab>('search')
const focusBatchId = ref<string | null>(null)
const videoUrl = ref('')
/** 非空时使用该绝对源请求 /api/*（存 localStorage，便于开源部署不暴露后端地址到仓库） */
const apiBaseUrl = ref(loadStoredApiBase())
const showSettings = ref(false)
const settingsDraft = ref('')
const bilibiliCookieDraft = ref('')
const openaiApiKeyDraft = ref('')
const openaiBaseUrlDraft = ref('')
const summarizeModelDraft = ref('')
const settingsError = ref('')

const health = ref<Health | null>(null)
/** 是否已完成一次 /api/health 请求（避免 null 被误判为两项都未就绪） */
const healthFetched = ref(false)
/** 健康检查请求失败（多为后端未启动或代理/CORS 问题） */
const healthBackendUnreachable = ref(false)
const probe = ref<ProbeResponse | null>(null)
const selectedFormat = ref('')
const job = ref<JobStatus | null>(null)
const summarizeJob = ref<SummarizeStatus | null>(null)
const isSummarizing = ref(false)
const summarizeError = ref('')
const chatInput = ref('')
const chatMessages = ref<{ role: string; content: string }[]>([])
const markmapSvgRef = ref<SVGSVGElement | null>(null)
const mindmapStageRef = ref<HTMLElement | null>(null)
const mindmapIsFullscreen = ref(false)
const mindmapExportBusy = ref(false)
const mindmapExportError = ref('')

const MINDMAP_ZOOM_STEP = 1.18
const MINDMAP_K_MIN = 0.12
const MINDMAP_K_MAX = 14
const resultSplitRef = ref<HTMLElement | null>(null)
const downloadColRef = ref<HTMLElement | null>(null)
let downloadColResizeObserver: ResizeObserver | null = null
const isProbing = ref(false)
const isStartingDownload = ref(false)
const errorMessage = ref('')
let poller: number | undefined
let summarizePoll: number | undefined
let summarizeMarkmap: Markmap | null = null

const canProbe = computed(() => videoUrl.value.trim().length > 0 && !isProbing.value)
const selectedFormatInfo = computed(() =>
  probe.value?.formats.find((format) => format.format_id === selectedFormat.value),
)
function apiUrl(path: string) {
  return withApiBase(apiBaseUrl.value, path)
}

const thumbnailUrl = computed(() =>
  probe.value?.thumbnail ? apiUrl(`/api/image-proxy?url=${encodeURIComponent(probe.value.thumbnail)}`) : '',
)
const isBusy = computed(
  () => isProbing.value || isStartingDownload.value || job.value?.status === 'downloading' || job.value?.status === 'queued',
)

const summarizeBusy = computed(
  () =>
    isSummarizing.value ||
    summarizeJob.value?.status === 'queued' ||
    summarizeJob.value?.status === 'running',
)

const depsReady = computed(
  () => healthFetched.value && !!health.value && health.value.ffmpeg_available && health.value.ytdlp_available,
)

const features = [
  { icon: Search, title: '关键词搜索', text: 'YouTube / B 站关键词检索，勾选批量创建下载（受站点与网络限制）。' },
  { icon: Film, title: '多平台解析', text: 'yt-dlp 驱动，支持常见公开视频页。' },
  { icon: Gauge, title: '画质与格式', text: '解析后自选清晰度或仅音频。' },
  { icon: ArrowDownToLine, title: '保存到本地', text: '浏览器取回文件，常规容器、便于归档。' },
]

const aboutRows = computed(() => features)

type AiSummaryTab = 'outline' | 'timeline' | 'mindmap' | 'chat'

const aiSummaryTab = ref<AiSummaryTab>('outline')

const hasMindmapInResult = computed(
  () =>
    !!summarizeJob.value?.result?.mindmap &&
    typeof summarizeJob.value.result.mindmap === 'object' &&
    !Array.isArray(summarizeJob.value.result.mindmap),
)

watch(hasMindmapInResult, (has) => {
  if (!has && aiSummaryTab.value === 'mindmap') aiSummaryTab.value = 'outline'
})

watch(aiSummaryTab, async (t, prev) => {
  if (prev === 'mindmap' && t !== 'mindmap') {
    summarizeMarkmap?.destroy?.()
    summarizeMarkmap = null
  }
  if (t !== 'mindmap') return
  await nextTick()
  await refreshMarkmap()
})

onMounted(async () => {
  await loadHealth()
  document.addEventListener('fullscreenchange', onMindmapFullscreenChange)
})

onBeforeUnmount(() => {
  document.removeEventListener('fullscreenchange', onMindmapFullscreenChange)
  clearPolling()
  clearSummarizePolling()
  teardownDownloadColHeightSync()
  summarizeMarkmap?.destroy?.()
  summarizeMarkmap = null
})

async function fetchApiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), {
    headers: {
      'Content-Type': 'application/json',
      ...userSettingsRequestHeaders(),
      ...(init?.headers ?? {}),
    },
    ...init,
  })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const data = await response.json()
      detail = data.detail || detail
    } catch {
      // Ignore non-JSON errors from proxies or dev servers.
    }
    throw new Error(Array.isArray(detail) ? detail.map((item) => item.msg).join('；') : detail)
  }

  return response.json() as Promise<T>
}

async function loadHealth() {
  healthFetched.value = false
  healthBackendUnreachable.value = false
  try {
    health.value = await fetchApiJson<Health>('/api/health')
    healthBackendUnreachable.value = false
  } catch {
    health.value = null
    healthBackendUnreachable.value = true
  } finally {
    healthFetched.value = true
  }
}

function teardownDownloadColHeightSync() {
  downloadColResizeObserver?.disconnect()
  downloadColResizeObserver = null
  resultSplitRef.value?.style.removeProperty('--result-download-height')
}

/** 桌面宽屏：右侧 AI 栏高度与左侧信息与下载栏一致，正文在面板内滚动 */
function syncAiColumnHeightToDownloadCol() {
  const split = resultSplitRef.value
  const col = downloadColRef.value
  if (!split || !col) return
  const h = col.offsetHeight
  if (h > 0) split.style.setProperty('--result-download-height', `${h}px`)
}

function setupDownloadColHeightSync() {
  teardownDownloadColHeightSync()
  const split = resultSplitRef.value
  const col = downloadColRef.value
  if (!split || !col || typeof ResizeObserver === 'undefined') return
  downloadColResizeObserver = new ResizeObserver(() => syncAiColumnHeightToDownloadCol())
  downloadColResizeObserver.observe(col)
  queueMicrotask(() => syncAiColumnHeightToDownloadCol())
}

watch(
  () => probe.value,
  async (p) => {
    await nextTick()
    if (p) setupDownloadColHeightSync()
    else teardownDownloadColHeightSync()
  },
)

async function probeVideo() {
  if (!canProbe.value) return
  clearPolling()
  resetResult()
  isProbing.value = true

  try {
    probe.value = await fetchApiJson<ProbeResponse>('/api/video/probe', {
      method: 'POST',
      body: JSON.stringify({ url: videoUrl.value.trim() }),
    })
    const rec = probe.value.recommended_format_id
    const legacy = probe.value.formats.find((f) => f.format_id.startsWith('bilibili_legacy|'))
    selectedFormat.value = rec || legacy?.format_id || probe.value.formats[0]?.format_id || ''
  } catch (error) {
    errorMessage.value = getErrorText(error, '无法解析该链接，请确认视频公开可访问。')
  } finally {
    isProbing.value = false
  }
}

async function startDownload() {
  if (!probe.value || !selectedFormat.value) return
  isStartingDownload.value = true
  errorMessage.value = ''

  try {
    const created = await fetchApiJson<{ job_id: string }>('/api/downloads', {
      method: 'POST',
      body: JSON.stringify({
        url: videoUrl.value.trim(),
        format_id: selectedFormat.value,
      }),
    })
    job.value = {
      job_id: created.job_id,
      status: 'queued',
      progress: 0,
      filename: null,
      error: null,
      download_url: null,
    }
    pollJob(created.job_id)
  } catch (error) {
    errorMessage.value = getErrorText(error, '下载任务创建失败，请稍后重试。')
  } finally {
    isStartingDownload.value = false
  }
}

function pollJob(jobId: string) {
  clearPolling()
  const tick = async () => {
    try {
      job.value = await fetchApiJson<JobStatus>(`/api/downloads/${jobId}`)
      if (job.value.status === 'completed' || job.value.status === 'failed') {
        clearPolling()
        if (job.value.status === 'failed') {
          errorMessage.value = job.value.error || '下载失败，请换一个链接或格式再试。'
        }
      }
    } catch (error) {
      clearPolling()
      errorMessage.value = getErrorText(error, '查询下载进度失败。')
    }
  }
  void tick()
  poller = window.setInterval(tick, 1200)
}

function clearPolling() {
  if (poller) {
    window.clearInterval(poller)
    poller = undefined
  }
}

function clearSummarizePolling() {
  if (summarizePoll) {
    window.clearInterval(summarizePoll)
    summarizePoll = undefined
  }
}

function mindmapToMarkdown(node: unknown, depth = 0): string {
  if (!node || typeof node !== 'object') return ''
  const n = node as { label?: string; children?: unknown[] }
  const label = String(n.label ?? '')
  const level = Math.min(6, depth + 1)
  let s = `${'#'.repeat(level)} ${label}\n`
  for (const c of n.children || []) {
    s += mindmapToMarkdown(c, depth + 1)
  }
  return s
}

async function refreshMarkmap() {
  await nextTick()
  const rootMind = summarizeJob.value?.result?.mindmap
  if (!markmapSvgRef.value || !rootMind || typeof rootMind !== 'object') return
  const transformer = new Transformer()
  const md = mindmapToMarkdown(rootMind)
  const { root } = transformer.transform(md)
  summarizeMarkmap?.destroy?.()
  summarizeMarkmap = Markmap.create(markmapSvgRef.value)
  summarizeMarkmap.setData(root)
  summarizeMarkmap.fit()
}

function mindmapDownloadBasename(): string {
  const r = summarizeJob.value?.result
  const fromResult = r && typeof r.title === 'string' ? r.title.trim() : ''
  const fromProbe = probe.value?.title?.trim() ?? ''
  const fromUrl = summarizeJob.value?.webpage_url?.trim() ?? ''
  return fromResult || fromProbe || fromUrl || 'mindmap'
}

function getMindmapTree(): MindmapTreeNode | null {
  const m = summarizeJob.value?.result?.mindmap
  if (m && typeof m === 'object' && !Array.isArray(m)) return m as MindmapTreeNode
  return null
}

async function ensureMarkmapInstance(): Promise<boolean> {
  if (summarizeMarkmap && markmapSvgRef.value) return true
  await refreshMarkmap()
  return !!(summarizeMarkmap && markmapSvgRef.value)
}

async function downloadMindmapSvg() {
  mindmapExportError.value = ''
  const tree = getMindmapTree()
  if (!tree || !markmapSvgRef.value) return
  if (!(await ensureMarkmapInstance())) {
    mindmapExportError.value = '思维导图尚未就绪，请稍后再试'
    return
  }
  mindmapExportBusy.value = true
  try {
    const blob = await exportMarkmapSvgBlob(summarizeMarkmap!, markmapSvgRef.value)
    triggerDownload(blob, sanitizeFilename(mindmapDownloadBasename(), 'svg'))
  } catch (e) {
    mindmapExportError.value = e instanceof Error ? e.message : '导出 SVG 失败'
  } finally {
    mindmapExportBusy.value = false
  }
}

async function downloadMindmapPng() {
  mindmapExportError.value = ''
  const tree = getMindmapTree()
  if (!tree || !markmapSvgRef.value) return
  if (!(await ensureMarkmapInstance())) {
    mindmapExportError.value = '思维导图尚未就绪，请稍后再试'
    return
  }
  mindmapExportBusy.value = true
  try {
    const blob = await exportMarkmapPngBlob(summarizeMarkmap!, markmapSvgRef.value)
    triggerDownload(blob, sanitizeFilename(mindmapDownloadBasename(), 'png'))
  } catch (e) {
    mindmapExportError.value = e instanceof Error ? e.message : '导出 PNG 失败'
  } finally {
    mindmapExportBusy.value = false
  }
}

async function downloadMindmapXmind() {
  mindmapExportError.value = ''
  const tree = getMindmapTree()
  if (!tree) return
  mindmapExportBusy.value = true
  try {
    const blob = await buildXmindArchiveBlob(tree)
    triggerDownload(blob, sanitizeFilename(mindmapDownloadBasename(), 'xmind'))
  } catch (e) {
    mindmapExportError.value = e instanceof Error ? e.message : '导出 XMind 失败'
  } finally {
    mindmapExportBusy.value = false
  }
}

function syncMindmapFullscreenFlag() {
  const el = mindmapStageRef.value
  const fs =
    document.fullscreenElement ??
    (document as Document & { webkitFullscreenElement?: Element | null }).webkitFullscreenElement ??
    null
  mindmapIsFullscreen.value = !!(el && fs === el)
}

function onMindmapFullscreenChange() {
  syncMindmapFullscreenFlag()
  void nextTick().then(() => summarizeMarkmap?.fit())
}

async function toggleMindmapFullscreen() {
  const el = mindmapStageRef.value
  if (!el) return
  try {
    if (document.fullscreenElement === el) {
      await document.exitFullscreen()
    } else {
      const anyEl = el as HTMLElement & { webkitRequestFullscreen?: () => Promise<void> }
      await (el.requestFullscreen?.() ?? anyEl.webkitRequestFullscreen?.() ?? Promise.resolve())
    }
  } catch {
    /* 全屏被浏览器拒绝或不可用 */
  }
  await nextTick()
  syncMindmapFullscreenFlag()
  await summarizeMarkmap?.fit()
}

function mindmapZoomIn() {
  if (!summarizeMarkmap || !markmapSvgRef.value) return
  const k = zoomTransform(markmapSvgRef.value).k
  if (k * MINDMAP_ZOOM_STEP > MINDMAP_K_MAX) return
  void summarizeMarkmap.rescale(MINDMAP_ZOOM_STEP)
}

function mindmapZoomOut() {
  if (!summarizeMarkmap || !markmapSvgRef.value) return
  const k = zoomTransform(markmapSvgRef.value).k
  if (k / MINDMAP_ZOOM_STEP < MINDMAP_K_MIN) return
  void summarizeMarkmap.rescale(1 / MINDMAP_ZOOM_STEP)
}

watch(
  () => summarizeJob.value?.result?.mindmap,
  async () => {
    if (summarizeJob.value?.status === 'completed') {
      await refreshMarkmap()
    }
  },
)

function pollSummarizeJob(jobId: string) {
  clearSummarizePolling()
  const tick = async () => {
    try {
      summarizeJob.value = await fetchApiJson<SummarizeStatus>(`/api/summarize/${jobId}`)
      if (summarizeJob.value.status === 'failed') {
        summarizeError.value = summarizeJob.value.error || 'AI 总结失败'
        clearSummarizePolling()
        isSummarizing.value = false
      }
      if (summarizeJob.value.status === 'completed') {
        clearSummarizePolling()
        isSummarizing.value = false
        chatMessages.value = []
        aiSummaryTab.value = 'outline'
        await refreshMarkmap()
      }
    } catch (error) {
      clearSummarizePolling()
      isSummarizing.value = false
      summarizeError.value = getErrorText(error, '查询总结进度失败')
    }
  }
  void tick()
  summarizePoll = window.setInterval(tick, 1400)
}

async function startSummarize() {
  if (!videoUrl.value.trim()) return
  if (!health.value?.summarize_llm_ready) {
    summarizeError.value = '请在右上角「设置」中填写 OpenAI API Key 后再使用 AI 总结。'
    return
  }
  summarizeError.value = ''
  mindmapExportError.value = ''
  aiSummaryTab.value = 'outline'
  isSummarizing.value = true
  summarizeJob.value = null
  chatMessages.value = []
  try {
    const created = await fetchApiJson<{ job_id: string }>('/api/summarize', {
      method: 'POST',
      body: JSON.stringify({ url: videoUrl.value.trim() }),
    })
    summarizeJob.value = {
      job_id: created.job_id,
      status: 'queued',
      progress: 0,
      error: null,
      subtitle_source: null,
      webpage_url: null,
      result: null,
    }
    pollSummarizeJob(created.job_id)
  } catch (error) {
    summarizeError.value = getErrorText(error, '无法创建总结任务')
    isSummarizing.value = false
  }
}

async function sendSummarizeChat() {
  const msg = chatInput.value.trim()
  if (!msg || !summarizeJob.value || summarizeJob.value.status !== 'completed') return
  chatMessages.value.push({ role: 'user', content: msg })
  chatInput.value = ''
  try {
    const { reply } = await fetchApiJson<{ reply: string }>(`/api/summarize/${summarizeJob.value.job_id}/chat`, {
      method: 'POST',
      body: JSON.stringify({ message: msg }),
    })
    chatMessages.value.push({ role: 'assistant', content: reply })
  } catch (error) {
    chatMessages.value.push({ role: 'assistant', content: `错误：${getErrorText(error, '请求失败')}` })
  }
}

function resetResult() {
  probe.value = null
  selectedFormat.value = ''
  job.value = null
  errorMessage.value = ''
  summarizeJob.value = null
  summarizeError.value = ''
  mindmapExportError.value = ''
  aiSummaryTab.value = 'outline'
  chatInput.value = ''
  chatMessages.value = []
  clearSummarizePolling()
  summarizeMarkmap?.destroy?.()
  summarizeMarkmap = null
}

function hideBrokenImage(event: Event) {
  const image = event.currentTarget as HTMLImageElement
  image.style.display = 'none'
}

function formatDuration(seconds: number | null) {
  if (!seconds) return '未知时长'
  const wholeSeconds = Math.floor(seconds)
  const minutes = Math.floor(wholeSeconds / 60)
  const rest = wholeSeconds % 60
  return `${minutes}:${String(rest).padStart(2, '0')}`
}

function formatTimestampMs(ms: number) {
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${String(r).padStart(2, '0')}`
}

function formatSize(bytes: number | null) {
  if (!bytes) return '自动估算'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(1)} ${units[unit]}`
}

function openSettings() {
  settingsDraft.value = apiBaseUrl.value
  bilibiliCookieDraft.value = loadStoredBilibiliCookie()
  openaiApiKeyDraft.value = loadStoredOpenAiApiKey()
  openaiBaseUrlDraft.value = loadStoredOpenAiBaseUrl()
  summarizeModelDraft.value = loadStoredSummarizeModel()
  settingsError.value = ''
  showSettings.value = true
}

function closeSettings() {
  showSettings.value = false
}

function applyApiSettings() {
  const raw = settingsDraft.value.trim()
  if (raw) {
    try {
      const candidate = raw.includes('://') ? raw : `http://${raw}`
      const u = new URL(candidate)
      if (u.protocol !== 'http:' && u.protocol !== 'https:') {
        settingsError.value = '仅支持 http 或 https'
        return
      }
      apiBaseUrl.value = u.origin
      persistApiBase(apiBaseUrl.value)
    } catch {
      settingsError.value = '请输入有效地址，例如 127.0.0.1:8028 或 http://127.0.0.1:8028'
      return
    }
  } else {
    apiBaseUrl.value = ''
    persistApiBase('')
  }
  persistBilibiliCookie(bilibiliCookieDraft.value)
  persistOpenAiApiKey(openaiApiKeyDraft.value)
  persistOpenAiBaseUrl(openaiBaseUrlDraft.value)
  persistSummarizeModel(summarizeModelDraft.value)
  settingsError.value = ''
  showSettings.value = false
  resetResult()
  clearPolling()
  clearSummarizePolling()
  loadHealth()
}

async function onSearchPrefillDownload(url: string) {
  mainTab.value = 'download'
  videoUrl.value = url
  await nextTick()
  if (depsReady.value) void probeVideo()
}

async function onBatchCreated(batchId: string) {
  focusBatchId.value = batchId
  mainTab.value = 'tasks'
}

async function onSearchStartSummarize(url: string) {
  mainTab.value = 'download'
  videoUrl.value = url
  summarizeError.value = ''
  errorMessage.value = ''
  await nextTick()
  if (!health.value?.summarize_llm_ready) {
    errorMessage.value = '请在右上角「设置」中填写 OpenAI API Key 后再使用 AI 总结。'
    return
  }
  const normalized = url.trim()
  const probed = probe.value?.webpage_url?.trim() || ''
  if (depsReady.value && probed !== normalized) {
    await probeVideo()
  }
  await nextTick()
  document.querySelector('.result-col-ai')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  void startSummarize()
}

function getErrorText(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="#" aria-label="拾影视频下载器首页" @click.prevent="mainTab = 'search'">
        <span class="brand-mark"><Film :size="20" /></span>
        <span>拾影视频下载器</span>
      </a>
      <nav class="main-nav" aria-label="主导航">
        <button
          type="button"
          class="main-nav-btn"
          :class="{ active: mainTab === 'search' }"
          @click="mainTab = 'search'"
        >
          视频搜索
        </button>
        <button
          type="button"
          class="main-nav-btn"
          :class="{ active: mainTab === 'download' }"
          @click="mainTab = 'download'"
        >
          视频下载
        </button>
        <button
          type="button"
          class="main-nav-btn"
          :class="{ active: mainTab === 'tasks' }"
          @click="mainTab = 'tasks'"
        >
          下载任务
        </button>
      </nav>
      <button type="button" class="topbar-settings" aria-label="打开设置" aria-haspopup="dialog" @click="openSettings">
        <Settings :size="18" aria-hidden="true" />
        设置
      </button>
    </header>

    <main>
      <SearchPanel
        v-show="mainTab === 'search'"
        :deps-ready="depsReady"
        :summarize-llm-ready="!!health?.summarize_llm_ready"
        :fetch-json="fetchApiJson"
        :resolve-url="apiUrl"
        @prefill-download="onSearchPrefillDownload"
        @start-summarize="onSearchStartSummarize"
        @batch-created="onBatchCreated"
      />

      <TasksPanel
        v-show="mainTab === 'tasks'"
        :focus-batch-id="focusBatchId"
        :fetch-json="fetchApiJson"
        :resolve-url="apiUrl"
      />

      <div v-show="mainTab === 'download'" class="download-tab">
      <section id="downloader" class="hero-section">
        <div class="hero-copy">
          <h1>公开视频一键解析下载</h1>
          <ul class="hero-bullets">
            <li>粘贴公开页链接 → 选格式 → 浏览器取回文件。</li>
            <li>需要可再开 AI：概要 / 时间轴 / 导图（服务端配密钥）。</li>
          </ul>
        </div>

        <section class="download-panel" aria-label="视频下载工具">
          <div class="panel-head">
            <h2 class="panel-title">解析下载</h2>
            <span class="status-pill" :class="{ ready: depsReady }">
              <Loader2 v-if="!healthFetched" class="spin" :size="16" />
              <CheckCircle2 v-else-if="depsReady" :size="16" />
              <AlertCircle v-else :size="16" />
              <template v-if="!healthFetched">检查后端…</template>
              <template v-else-if="healthBackendUnreachable">无法连接后端</template>
              <template v-else-if="depsReady">服务就绪</template>
              <template v-else>依赖待检查</template>
            </span>
          </div>

          <form class="url-form" @submit.prevent="probeVideo">
            <label class="input-wrap">
              <Link :size="18" />
              <input
                v-model="videoUrl"
                type="url"
                inputmode="url"
                placeholder="粘贴公开视频链接，例如 https://..."
                autocomplete="off"
              />
            </label>
            <button class="primary-btn" type="submit" :disabled="!canProbe">
              <Loader2 v-if="isProbing" class="spin" :size="18" />
              <Search v-else :size="18" />
              {{ isProbing ? '解析中' : '解析视频' }}
            </button>
          </form>

          <details class="compliance-details">
            <summary class="compliance-summary">
              <ShieldCheck :size="16" aria-hidden="true" />
              合规与版权提示
            </summary>
            <p class="compliance-body">
              请仅下载你拥有保存权利或已获授权的内容。平台不支持绕过 DRM 或加密保护。
            </p>
          </details>

          <div v-if="healthFetched && healthBackendUnreachable" class="warning-banner" role="status">
            <AlertCircle :size="18" aria-hidden="true" />
            <span class="warning-banner-summary">无法连接后端（健康检查失败）。</span>
            <details class="warning-banner-expand">
              <summary>排查步骤</summary>
              <div class="warning-banner-body">
                <p>
                  请在本机启动 FastAPI（例如在 backend 目录执行
                  <code>uvicorn app.main:app --reload --host 0.0.0.0 --port 8028</code>），确认后端可访问。前后端分开部署时在右上角
                  <strong>设置</strong> 填写 API 地址；本地开发一般留空即可（由当前站点代理 <code>/api</code>）。
                </p>
              </div>
            </details>
          </div>

          <div
            v-else-if="healthFetched && health && (!health.ffmpeg_available || !health.ytdlp_available)"
            class="warning-banner"
            role="status"
          >
            <AlertCircle :size="18" aria-hidden="true" />
            <span class="warning-banner-summary"
              >后端依赖未就绪（ffmpeg：{{ health.ffmpeg_available ? '已安装' : '未检测到' }}；yt-dlp：{{
                health.ytdlp_available ? '可用' : '不可用'
              }}）。</span
            >
            <details class="warning-banner-expand">
              <summary>说明</summary>
              <div class="warning-banner-body">
                <p>请在服务器环境安装 ffmpeg 与 yt-dlp，并重启后端以便健康检查通过。</p>
              </div>
            </details>
          </div>

          <div v-if="errorMessage" class="error-box">
            <AlertCircle :size="18" />
            <span>{{ errorMessage }}</span>
          </div>

          <article v-if="probe || summarizeJob || isSummarizing" class="result-panel">
            <div ref="resultSplitRef" class="result-split" :class="{ 'result-split-ai-only': !probe }">
              <div v-if="probe" ref="downloadColRef" class="result-col result-col-download">
                <div class="video-card">
                  <div class="thumb-shell">
                    <img v-if="thumbnailUrl" :src="thumbnailUrl" :alt="probe.title" @error="hideBrokenImage" />
                    <div class="thumb-fallback"><Film :size="36" /></div>
                  </div>
                  <div class="video-meta">
                    <span class="source-tag">{{ probe.extractor || 'Video' }}</span>
                    <h3>{{ probe.title }}</h3>
                    <p><Clock3 :size="15" /> {{ formatDuration(probe.duration) }}</p>
                  </div>
                </div>

                <div class="format-grid" role="radiogroup" aria-label="选择下载格式">
                  <button
                    v-for="format in probe.formats"
                    :key="format.format_id"
                    class="format-option"
                    :class="{ active: selectedFormat === format.format_id }"
                    type="button"
                    role="radio"
                    :aria-checked="selectedFormat === format.format_id"
                    @click="selectedFormat = format.format_id"
                  >
                    <span>{{ format.label }}</span>
                    <small>{{ format.resolution || 'Auto' }} · {{ format.ext || 'media' }} · {{ formatSize(format.filesize) }}</small>
                  </button>
                </div>

                <div class="download-actions">
                  <div class="selected-note">
                    <strong>{{ selectedFormatInfo?.label || '推荐格式' }}</strong>
                    <span>{{ selectedFormatInfo?.note || '自动选择适合保存的媒体格式' }}</span>
                  </div>
                  <button class="primary-btn wide" type="button" :disabled="isBusy || !selectedFormat" @click="startDownload">
                    <Loader2 v-if="isStartingDownload || job?.status === 'downloading' || job?.status === 'queued'" class="spin" :size="18" />
                    <Download v-else :size="18" />
                    {{ job?.status === 'downloading' || job?.status === 'queued' ? '下载中' : '开始下载' }}
                  </button>
                </div>
              </div>

              <aside class="result-col result-col-ai" aria-label="AI 视频总结">
                <section class="ai-summary-panel">
                  <div class="ai-summary-head">
                    <h3><Bolt :size="20" /> AI 视频总结</h3>
                    <p v-if="healthFetched && health && !health.summarize_llm_ready" class="ai-summary-hint">
                      请在右上角「设置」中填写 OpenAI API Key 后可生成大纲、分段要点、思维导图并与视频对话。
                    </p>
                  </div>
                  <button
                    type="button"
                    class="secondary-btn"
                    :disabled="!health?.summarize_llm_ready || summarizeBusy"
                    @click="startSummarize"
                  >
                    <Loader2 v-if="summarizeBusy" class="spin" :size="18" />
                    <MessageSquare v-else :size="18" />
                    {{ summarizeBusy ? '总结生成中…' : '生成 AI 总结' }}
                  </button>
                  <p v-if="summarizeError" class="error-box summarize-error">
                    <AlertCircle :size="18" />
                    <span>{{ summarizeError }}</span>
                  </p>
                  <div v-if="summarizeJob?.status === 'completed' && summarizeJob.result" class="ai-summary-body">
                    <div class="ai-tab-bar" role="tablist" aria-label="总结内容分区">
                      <button
                        type="button"
                        class="ai-tab"
                        role="tab"
                        :aria-selected="aiSummaryTab === 'outline'"
                        @click="aiSummaryTab = 'outline'"
                      >
                        概要
                      </button>
                      <button
                        type="button"
                        class="ai-tab"
                        role="tab"
                        :aria-selected="aiSummaryTab === 'timeline'"
                        @click="aiSummaryTab = 'timeline'"
                      >
                        时间轴
                      </button>
                      <button
                        v-if="hasMindmapInResult"
                        type="button"
                        class="ai-tab"
                        role="tab"
                        :aria-selected="aiSummaryTab === 'mindmap'"
                        @click="aiSummaryTab = 'mindmap'"
                      >
                        导图
                      </button>
                      <button
                        type="button"
                        class="ai-tab"
                        role="tab"
                        :aria-selected="aiSummaryTab === 'chat'"
                        @click="aiSummaryTab = 'chat'"
                      >
                        对话
                      </button>
                    </div>

                    <div v-show="aiSummaryTab === 'outline'" class="ai-tab-panel" role="tabpanel">
                      <p v-if="summarizeJob.subtitle_source" class="ai-meta">字幕来源：{{ summarizeJob.subtitle_source }}</p>
                      <h4 class="ai-tab-heading">大纲</h4>
                      <p class="outline-block">{{ String(summarizeJob.result.outline ?? '') }}</p>
                      <h4 class="ai-tab-heading">核心要点</h4>
                      <ul class="key-points">
                        <li v-for="(kp, idx) in (summarizeJob.result.key_points as string[]) || []" :key="idx">{{ kp }}</li>
                      </ul>
                    </div>

                    <div v-show="aiSummaryTab === 'timeline'" class="ai-tab-panel" role="tabpanel">
                      <h4 class="ai-tab-heading">分段摘要</h4>
                      <div class="segment-list">
                        <div
                          v-for="(seg, idx) in (summarizeJob.result.segments as { t_start_ms: number; t_end_ms: number; summary: string }[]) || []"
                          :key="idx"
                          class="segment-row"
                        >
                          <span class="segment-time"
                            >{{ formatTimestampMs(seg.t_start_ms) }} — {{ formatTimestampMs(seg.t_end_ms) }}</span
                          >
                          <p>{{ seg.summary }}</p>
                        </div>
                      </div>
                    </div>

                    <div v-if="hasMindmapInResult && aiSummaryTab === 'mindmap'" class="ai-tab-panel ai-tab-panel-mindmap" role="tabpanel">
                      <div class="mindmap-block">
                        <div ref="mindmapStageRef" class="mindmap-stage">
                          <div class="mindmap-toolbar" role="toolbar" aria-label="导图显示">
                            <button
                              type="button"
                              class="mindmap-tool-btn"
                              :title="mindmapIsFullscreen ? '退出全屏' : '全屏查看导图'"
                              @click="toggleMindmapFullscreen"
                            >
                              <Maximize2 v-if="!mindmapIsFullscreen" :size="18" aria-hidden="true" />
                              <Minimize2 v-else :size="18" aria-hidden="true" />
                              {{ mindmapIsFullscreen ? '退出全屏' : '放大' }}
                            </button>
                            <button
                              type="button"
                              class="mindmap-tool-btn mindmap-tool-btn-icon"
                              title="放大显示（缩放）"
                              aria-label="放大显示"
                              @click="mindmapZoomIn"
                            >
                              <ZoomIn :size="18" aria-hidden="true" />
                            </button>
                            <button
                              type="button"
                              class="mindmap-tool-btn mindmap-tool-btn-icon"
                              title="缩小显示（缩放）"
                              aria-label="缩小显示"
                              @click="mindmapZoomOut"
                            >
                              <ZoomOut :size="18" aria-hidden="true" />
                            </button>
                          </div>
                          <svg ref="markmapSvgRef" class="markmap-svg" role="img" aria-label="思维导图" />
                        </div>
                        <div class="mindmap-export-tools" role="group" aria-label="导出思维导图">
                          <span v-if="mindmapExportBusy" class="mindmap-export-status"
                            ><Loader2 class="spin" :size="16" /> 导出中…</span
                          >
                          <button
                            type="button"
                            class="mindmap-export-btn"
                            :disabled="mindmapExportBusy"
                            title="下载为 SVG 矢量图"
                            @click="downloadMindmapSvg"
                          >
                            <FileCode2 :size="16" />
                            SVG
                          </button>
                          <button
                            type="button"
                            class="mindmap-export-btn"
                            :disabled="mindmapExportBusy"
                            title="下载为 PNG 位图"
                            @click="downloadMindmapPng"
                          >
                            <ImageDown :size="16" />
                            PNG
                          </button>
                          <button
                            type="button"
                            class="mindmap-export-btn"
                            :disabled="mindmapExportBusy"
                            title="下载为 XMind 8 格式（.xmind）"
                            @click="downloadMindmapXmind"
                          >
                            <Archive :size="16" />
                            XMind
                          </button>
                        </div>
                        <p v-if="mindmapExportError" class="mindmap-export-error">{{ mindmapExportError }}</p>
                      </div>
                    </div>

                    <div v-show="aiSummaryTab === 'chat'" class="ai-tab-panel" role="tabpanel">
                      <h4 class="ai-tab-heading sr-only">与视频对话</h4>
                      <div class="chat-log">
                        <div v-for="(m, i) in chatMessages" :key="i" :class="['chat-bubble', m.role]">
                          <span class="chat-role">{{ m.role === 'user' ? '你' : 'AI' }}</span>
                          <p>{{ m.content }}</p>
                        </div>
                      </div>
                      <form class="chat-form" @submit.prevent="sendSummarizeChat">
                        <input v-model="chatInput" type="text" maxlength="2000" placeholder="基于上文与视频内容提问…" autocomplete="off" />
                        <button type="submit" class="primary-btn" :disabled="!chatInput.trim()">发送</button>
                      </form>
                    </div>
                  </div>
                </section>
              </aside>
            </div>
          </article>

          <section v-if="job" class="progress-panel" aria-live="polite">
            <div class="progress-head">
              <span>{{ job.status === 'completed' ? '下载完成' : job.status === 'failed' ? '下载失败' : '正在下载' }}</span>
              <strong>{{ Math.round(job.progress) }}%</strong>
            </div>
            <div class="progress-track">
              <span :style="{ width: `${job.progress}%` }"></span>
            </div>
            <a
              v-if="job.status === 'completed' && job.download_url"
              class="download-link"
              :href="apiUrl(job.download_url)"
            >
              <ArrowDownToLine :size="18" />
              保存到本地
            </a>
          </section>
        </section>
      </section>
      </div>

      <section v-if="mainTab !== 'tasks'" id="about" class="content-band about-band">
        <div class="section-heading">
          <p>About</p>
          <h2>能力一览</h2>
        </div>
        <div class="about-grid" role="list">
          <article
            v-for="row in aboutRows"
            :key="row.title"
            class="about-item"
            data-kind="cap"
            role="listitem"
          >
            <component :is="row.icon" class="about-icon" :size="22" aria-hidden="true" />
            <div class="about-item-body">
              <span class="about-tag">{{ row.title }}</span>
              <p>{{ row.text }}</p>
            </div>
          </article>
        </div>
      </section>
    </main>

    <footer class="site-footer" role="contentinfo">
      <div class="site-footer-inner">
        <p class="site-footer-line"><strong>拾影视频下载器</strong>（Video Grab）· 本地解析与保存</p>
        <p class="site-footer-note">
          请仅下载你拥有保存权利或已获授权的内容；遵守平台服务条款与当地法规。
        </p>
      </div>
    </footer>

    <div
      v-if="showSettings"
      class="settings-backdrop"
      role="presentation"
      @click.self="closeSettings"
    >
      <div
        class="settings-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        tabindex="-1"
        @keydown.escape.prevent="closeSettings"
      >
        <div class="settings-head">
          <h2 id="settings-title" class="settings-title">设置</h2>
          <button type="button" class="settings-close" aria-label="关闭" @click="closeSettings">
            <X :size="20" aria-hidden="true" />
          </button>
        </div>
        <div class="settings-body">
          <p class="settings-lead">
            以下配置保存在<strong>本机浏览器</strong>，仅会发向你指定的后端地址，不会写入 Git 仓库。
          </p>

          <section class="settings-section" aria-labelledby="api-base-heading">
            <h3 id="api-base-heading" class="settings-env-heading">连接</h3>
            <label class="settings-label" for="api-base-input">后端 API 地址（可选）</label>
            <input
              id="api-base-input"
              v-model="settingsDraft"
              class="settings-input"
              type="text"
              name="api-base"
              placeholder="留空则使用当前站点 /api，或填 http://127.0.0.1:8028"
              autocomplete="off"
              spellcheck="false"
            />
            <p class="settings-section-note">
              前后端分开部署时填写；本地开发一般<strong>留空</strong>即可。
            </p>
          </section>

          <section class="settings-section" aria-labelledby="llm-heading">
            <h3 id="llm-heading" class="settings-env-heading">AI 视频总结</h3>
            <label class="settings-label" for="openai-key-input">OpenAI API Key</label>
            <input
              id="openai-key-input"
              v-model="openaiApiKeyDraft"
              class="settings-input"
              type="password"
              name="openai-api-key"
              placeholder="sk-…"
              autocomplete="off"
              spellcheck="false"
            />
            <label class="settings-label" for="openai-base-input">API Base URL（可选）</label>
            <input
              id="openai-base-input"
              v-model="openaiBaseUrlDraft"
              class="settings-input"
              type="text"
              name="openai-base-url"
              placeholder="默认 https://api.openai.com/v1"
              autocomplete="off"
              spellcheck="false"
            />
            <label class="settings-label" for="summarize-model-input">模型名称（可选）</label>
            <input
              id="summarize-model-input"
              v-model="summarizeModelDraft"
              class="settings-input"
              type="text"
              name="summarize-model"
              placeholder="默认 gpt-4o-mini"
              autocomplete="off"
              spellcheck="false"
            />
            <p class="settings-section-note">
              兼容 OpenAI Chat Completions 的服务均可使用；保存后即可使用概要、时间轴、导图与对话。
            </p>
          </section>

          <section class="settings-section" aria-labelledby="bilibili-heading">
            <h3 id="bilibili-heading" class="settings-env-heading">哔哩哔哩</h3>
            <label class="settings-label" for="bilibili-cookie-input">Cookie（可选）</label>
            <input
              id="bilibili-cookie-input"
              v-model="bilibiliCookieDraft"
              class="settings-input"
              type="password"
              name="bilibili-cookie"
              placeholder="SESSDATA=… 或整段 Cookie"
              autocomplete="off"
              spellcheck="false"
            />
            <p class="settings-section-note">
              用于 B 站搜索与高清下载；登录 bilibili.com 后从开发者工具复制。
            </p>
          </section>

          <p v-if="settingsError" class="settings-error">{{ settingsError }}</p>
        </div>

        <div class="settings-actions">
          <button type="button" class="secondary-btn" @click="closeSettings">取消</button>
          <button type="button" class="primary-btn" @click="applyApiSettings">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
