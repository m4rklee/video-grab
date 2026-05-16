<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { Archive, CheckCircle2, Loader2, XCircle } from 'lucide-vue-next'

type QualityPreset = 'best' | '1080' | '720' | '480'
type BatchStatus = 'queued' | 'running' | 'packaging' | 'completed' | 'failed' | 'partial'

type BatchItem = {
  url: string
  title: string
  job_id: string | null
  status: 'queued' | 'downloading' | 'completed' | 'failed'
  progress: number
  error: string | null
  filename: string | null
  download_url: string | null
}

type BatchDetail = {
  batch_id: string
  quality_preset: QualityPreset
  status: BatchStatus
  progress: number
  error: string | null
  zip_ready: boolean
  zip_download_url: string | null
  counts: { total: number; completed: number; failed: number; running: number }
  items: BatchItem[]
  created_at: number
}

type BatchListItem = {
  batch_id: string
  quality_preset: QualityPreset
  status: BatchStatus
  progress: number
  counts: BatchDetail['counts']
  created_at: number
  zip_ready: boolean
}

const props = defineProps<{
  focusBatchId: string | null
  fetchJson: <T>(path: string, init?: RequestInit) => Promise<T>
  resolveUrl: (path: string) => string
}>()

const activeBatchId = ref<string | null>(null)
const detail = ref<BatchDetail | null>(null)
const history = ref<BatchListItem[]>([])
const loadError = ref('')
let pollTimer: number | undefined

const presetLabel: Record<QualityPreset, string> = {
  best: '尽量高清',
  '1080': '1080p',
  '720': '720p',
  '480': '480p',
}

const statusLabel: Record<BatchStatus, string> = {
  queued: '排队中',
  running: '下载中',
  packaging: '正在打包',
  completed: '已完成',
  failed: '失败',
  partial: '部分完成',
}

const canDownloadZip = computed(
  () =>
    detail.value &&
    detail.value.zip_ready &&
    (detail.value.status === 'completed' || detail.value.status === 'partial'),
)

function itemStatusText(s: BatchItem['status']) {
  if (s === 'queued') return '排队'
  if (s === 'downloading') return '下载中'
  if (s === 'completed') return '完成'
  return '失败'
}

async function loadHistory() {
  try {
    const res = await props.fetchJson<{ batches: BatchListItem[] }>('/api/download-batches')
    history.value = res.batches
    if (!activeBatchId.value && res.batches.length) {
      activeBatchId.value = res.batches[0].batch_id
    }
  } catch {
    /* 列表可选 */
  }
}

async function pollActive() {
  const id = activeBatchId.value
  if (!id) return
  try {
    detail.value = await props.fetchJson<BatchDetail>(`/api/download-batches/${id}`)
    loadError.value = ''
    const terminal = ['completed', 'failed', 'partial'].includes(detail.value.status)
    if (terminal) {
      void loadHistory()
    }
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载任务失败'
  }
}

function startPolling() {
  clearInterval(pollTimer)
  void pollActive()
  pollTimer = window.setInterval(() => void pollActive(), 1200)
}

function selectBatch(id: string) {
  activeBatchId.value = id
  detail.value = null
  startPolling()
}

function zipHref() {
  const p = detail.value?.zip_download_url
  return p ? props.resolveUrl(p) : ''
}

watch(
  () => props.focusBatchId,
  (id) => {
    if (id) {
      activeBatchId.value = id
      startPolling()
    }
  },
  { immediate: true },
)

watch(activeBatchId, (id) => {
  if (id) startPolling()
})

onUnmounted(() => {
  clearInterval(pollTimer)
})

void loadHistory()
</script>

<template>
  <section class="tasks-panel content-band" aria-label="下载任务">
    <div v-if="history.length" class="tasks-history">
      <span class="tasks-history-label">最近任务</span>
      <button
        v-for="b in history"
        :key="b.batch_id"
        type="button"
        class="tasks-history-chip"
        :class="{ active: activeBatchId === b.batch_id }"
        @click="selectBatch(b.batch_id)"
      >
        {{ presetLabel[b.quality_preset] }} · {{ b.counts.completed }}/{{ b.counts.total }}
        <small>{{ statusLabel[b.status] }}</small>
      </button>
    </div>

    <p v-if="loadError" class="error-box" role="alert">{{ loadError }}</p>

    <p v-if="!activeBatchId" class="tasks-empty">暂无批量任务。在「视频搜索」中勾选多条后点「下载选中」。</p>

    <template v-else-if="detail">
      <div class="progress-panel tasks-progress-head">
        <div class="tasks-progress-meta">
          <h2 class="panel-title">批量下载</h2>
          <span class="status-pill">{{ statusLabel[detail.status] }}</span>
          <span class="tasks-preset">{{ presetLabel[detail.quality_preset] }}</span>
        </div>
        <p class="tasks-counts">
          已完成 {{ detail.counts.completed }} / {{ detail.counts.total }}
          <template v-if="detail.counts.failed"> · 失败 {{ detail.counts.failed }}</template>
          <template v-if="detail.counts.running"> · 进行中 {{ detail.counts.running }}</template>
        </p>
        <div class="progress-bar-wrap" role="progressbar" :aria-valuenow="detail.progress" aria-valuemin="0" aria-valuemax="100">
          <div class="progress-bar-fill" :style="{ width: `${Math.min(100, detail.progress)}%` }" />
        </div>
        <p v-if="detail.status === 'packaging'" class="tasks-packaging">
          <Loader2 class="spin" :size="16" /> 正在打包 ZIP…
        </p>
        <p v-if="detail.error" class="error-box">{{ detail.error }}</p>
        <div v-if="canDownloadZip" class="tasks-zip-row">
          <a class="primary-btn" :href="zipHref()" download>
            <Archive :size="18" />
            下载全部（ZIP）
          </a>
        </div>
      </div>

      <div class="tasks-table-wrap">
        <table class="tasks-table">
          <thead>
            <tr>
              <th>视频</th>
              <th>状态</th>
              <th>进度</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(it, idx) in detail.items" :key="it.job_id || it.url + idx">
              <td class="tasks-cell-title">
                <span class="tasks-title">{{ it.title }}</span>
                <span class="tasks-url">{{ it.url }}</span>
              </td>
              <td>
                <span class="tasks-item-status" :class="it.status">
                  <CheckCircle2 v-if="it.status === 'completed'" :size="14" />
                  <XCircle v-else-if="it.status === 'failed'" :size="14" />
                  <Loader2 v-else class="spin" :size="14" />
                  {{ itemStatusText(it.status) }}
                </span>
                <p v-if="it.error" class="tasks-item-error">{{ it.error }}</p>
              </td>
              <td>{{ Math.round(it.progress) }}%</td>
              <td>
                <a
                  v-if="it.download_url"
                  class="secondary-btn tasks-file-link"
                  :href="resolveUrl(it.download_url)"
                  download
                >
                  单文件
                </a>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <p v-else class="tasks-loading"><Loader2 class="spin" :size="18" /> 加载任务…</p>
  </section>
</template>
