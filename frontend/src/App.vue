<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  AlertCircle,
  ArrowDownToLine,
  BadgeCheck,
  Bolt,
  CheckCircle2,
  Clock3,
  Download,
  Film,
  Gauge,
  Link,
  ListChecks,
  Loader2,
  LockKeyhole,
  Search,
  ShieldCheck,
  Sparkles,
  Subtitles,
  WandSparkles,
} from 'lucide-vue-next'

type Health = {
  status: 'ok'
  ytdlp_available: boolean
  ffmpeg_available: boolean
  ffmpeg_path: string | null
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
}

type JobStatus = {
  job_id: string
  status: 'queued' | 'downloading' | 'completed' | 'failed'
  progress: number
  filename: string | null
  error: string | null
  download_url: string | null
}

const videoUrl = ref('')
const health = ref<Health | null>(null)
/** 是否已完成一次 /api/health 请求（避免 null 被误判为两项都未就绪） */
const healthFetched = ref(false)
/** 健康检查请求失败（多为后端未启动或代理/CORS 问题） */
const healthBackendUnreachable = ref(false)
const probe = ref<ProbeResponse | null>(null)
const selectedFormat = ref('')
const job = ref<JobStatus | null>(null)
const isProbing = ref(false)
const isStartingDownload = ref(false)
const errorMessage = ref('')
let poller: number | undefined

const canProbe = computed(() => videoUrl.value.trim().length > 0 && !isProbing.value)
const selectedFormatInfo = computed(() =>
  probe.value?.formats.find((format) => format.format_id === selectedFormat.value),
)
const thumbnailUrl = computed(() => (probe.value?.thumbnail ? `/api/image-proxy?url=${encodeURIComponent(probe.value.thumbnail)}` : ''))
const isBusy = computed(
  () => isProbing.value || isStartingDownload.value || job.value?.status === 'downloading' || job.value?.status === 'queued',
)

const depsReady = computed(
  () => healthFetched.value && !!health.value && health.value.ffmpeg_available && health.value.ytdlp_available,
)

const features = [
  { icon: Film, title: '多平台解析', text: '以 yt-dlp 为核心，覆盖主流公开视频网站和内容平台。' },
  { icon: Gauge, title: '高清优先', text: '自动推荐最佳画质，也可选择更轻量的清晰度。' },
  { icon: ArrowDownToLine, title: '本地保存', text: '服务器临时中转，电脑和手机浏览器都能保存文件。' },
  { icon: LockKeyhole, title: '不锁文件', text: '下载完成后得到常规媒体文件，便于归档、剪辑和复习。' },
]

const roadmap = [
  { icon: ListChecks, title: '批量下载', text: '订阅用户可粘贴多个链接，自动排队处理。' },
  { icon: Sparkles, title: 'AI 视频搜索', text: '按主题聚合公开视频，快速找到可保存素材。' },
  { icon: WandSparkles, title: '视频总结', text: '下载后自动生成摘要、章节和重点片段。' },
  { icon: Subtitles, title: '字幕翻译', text: '自动提取字幕并翻译为中文或英文。' },
]

onMounted(async () => {
  await loadHealth()
})

async function loadHealth() {
  healthFetched.value = false
  healthBackendUnreachable.value = false
  try {
    health.value = await requestJson<Health>('/api/health')
    healthBackendUnreachable.value = false
  } catch {
    health.value = null
    healthBackendUnreachable.value = true
  } finally {
    healthFetched.value = true
  }
}

async function probeVideo() {
  if (!canProbe.value) return
  clearPolling()
  resetResult()
  isProbing.value = true

  try {
    probe.value = await requestJson<ProbeResponse>('/api/video/probe', {
      method: 'POST',
      body: JSON.stringify({ url: videoUrl.value.trim() }),
    })
    selectedFormat.value = probe.value.formats[0]?.format_id ?? ''
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
    const created = await requestJson<{ job_id: string }>('/api/downloads', {
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
      job.value = await requestJson<JobStatus>(`/api/downloads/${jobId}`)
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

function resetResult() {
  probe.value = null
  selectedFormat.value = ''
  job.value = null
  errorMessage.value = ''
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

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
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

function getErrorText(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="#" aria-label="VideoGrab AI 首页">
        <span class="brand-mark"><Film :size="20" /></span>
        <span>VideoGrab AI</span>
      </a>
      <nav class="nav-links" aria-label="主要导航">
        <a href="#features">能力</a>
        <a href="#roadmap">AI 扩展</a>
        <a href="#pricing">Pro</a>
      </nav>
      <a class="topbar-cta" href="#downloader">立即下载</a>
    </header>

    <main>
      <section id="downloader" class="hero-section">
        <div class="hero-copy">
          <p class="eyebrow"><BadgeCheck :size="16" /> 轻量、高清、跨端的视频保存工具</p>
          <h1>把公开视频安全保存到本地，下一步再交给 AI 处理</h1>
          <p class="hero-lead">
            粘贴链接即可解析视频信息，选择画质后由服务器临时下载中转。适合课程归档、素材备份、离线观看和团队知识沉淀。
          </p>
          <div class="hero-stats" aria-label="产品价值">
            <span><strong>1000+</strong> yt-dlp 支持站点</span>
            <span><strong>0</strong> 客户端安装</span>
            <span><strong>Pro</strong> 批量与 AI 能力预留</span>
          </div>
        </div>

        <section class="download-panel" aria-label="视频下载工具">
          <div class="panel-head">
            <div>
              <p class="panel-kicker">Paste video URL</p>
              <h2>一键解析并保存</h2>
            </div>
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

          <p class="compliance-note">
            <ShieldCheck :size="16" />
            请仅下载你拥有保存权利或已获授权的内容。平台不支持绕过 DRM 或加密保护。
          </p>

          <div v-if="healthFetched && healthBackendUnreachable" class="warning-box">
            <AlertCircle :size="18" />
            <span>
              无法访问后端健康检查接口（/api/health）。请在本机启动 FastAPI（例如在 backend 目录执行
              <code>uvicorn app.main:app --reload --host 0.0.0.0 --port 8028</code>），并确认前端开发代理仍指向
              <code>http://127.0.0.1:8028</code>（或你设置的 <code>VITE_API_TARGET</code>）。
            </span>
          </div>

          <div
            v-else-if="healthFetched && health && (!health.ffmpeg_available || !health.ytdlp_available)"
            class="warning-box"
          >
            <AlertCircle :size="18" />
            <span>
              后端依赖未完全就绪。ffmpeg：{{ health.ffmpeg_available ? '已安装' : '未检测到' }}；yt-dlp：{{
                health.ytdlp_available ? '可用' : '不可用'
              }}。
            </span>
          </div>

          <div v-if="errorMessage" class="error-box">
            <AlertCircle :size="18" />
            <span>{{ errorMessage }}</span>
          </div>

          <article v-if="probe" class="result-panel">
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
          </article>

          <section v-if="job" class="progress-panel" aria-live="polite">
            <div class="progress-head">
              <span>{{ job.status === 'completed' ? '下载完成' : job.status === 'failed' ? '下载失败' : '正在下载' }}</span>
              <strong>{{ Math.round(job.progress) }}%</strong>
            </div>
            <div class="progress-track">
              <span :style="{ width: `${job.progress}%` }"></span>
            </div>
            <a v-if="job.status === 'completed' && job.download_url" class="download-link" :href="job.download_url">
              <ArrowDownToLine :size="18" />
              保存到本地
            </a>
          </section>
        </section>
      </section>

      <section id="features" class="content-band">
        <div class="section-heading">
          <p>Core Value</p>
          <h2>先把下载这件事做到顺手</h2>
        </div>
        <div class="feature-grid">
          <article v-for="feature in features" :key="feature.title" class="feature-item">
            <component :is="feature.icon" :size="24" />
            <h3>{{ feature.title }}</h3>
            <p>{{ feature.text }}</p>
          </article>
        </div>
      </section>

      <section id="roadmap" class="content-band roadmap-band">
        <div class="section-heading">
          <p>AI Roadmap</p>
          <h2>为付费能力预留增长路径</h2>
        </div>
        <div class="roadmap-list">
          <article v-for="item in roadmap" :key="item.title" class="roadmap-item">
            <component :is="item.icon" :size="22" />
            <div>
              <h3>{{ item.title }}</h3>
              <p>{{ item.text }}</p>
            </div>
          </article>
        </div>
      </section>

      <section id="pricing" class="pricing-strip">
        <div>
          <p>VideoGrab AI Pro</p>
          <h2>批量下载、AI 总结、字幕翻译和 Stripe 订阅将在下一阶段接入。</h2>
        </div>
        <a href="#downloader"><Bolt :size="18" /> 先体验 MVP</a>
      </section>
    </main>
  </div>
</template>
