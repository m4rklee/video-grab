<script setup lang="ts">
import { bilibiliCookieRequestHeaders } from './apiConfig'
import BulkDownloadModal, { type QualityPreset } from './BulkDownloadModal.vue'
import { computed, onUnmounted, ref, watch } from 'vue'
import { ChevronLeft, ChevronRight, Download, Film, Loader2, Search, Sparkles, Trash2 } from 'lucide-vue-next'

type SearchStreamEvent =
  | { type: 'meta'; sources: ('youtube' | 'bilibili')[] }
  | { type: 'items'; source: 'youtube' | 'bilibili'; items: SearchItemRow[] }
  | { type: 'warning'; message: string }
  | { type: 'done'; total: number }
  | { type: 'error'; message: string }

export type SearchItemRow = {
  id: string
  title: string
  url: string
  thumbnail: string | null
  duration: number | null
  uploader: string | null
  source: 'youtube' | 'bilibili'
  extractor: string | null
}

const props = defineProps<{
  depsReady: boolean
  summarizeLlmReady: boolean
  fetchJson: <T>(path: string, init?: RequestInit) => Promise<T>
  resolveUrl: (path: string) => string
}>()

const emit = defineEmits<{
  'prefill-download': [url: string]
  'start-summarize': [url: string]
  'batch-created': [batchId: string]
}>()

const query = ref('')
const sourcesYoutube = ref(true)
const sourcesBilibili = ref(true)
const page = ref(1)
const pageSize = ref<10 | 20 | 30>(20)
const allItems = ref<SearchItemRow[]>([])
const total = ref(0)
const warning = ref('')
const loading = ref(false)
const searchError = ref('')
/** 最近一次点击「搜索」时使用的关键词；未搜索前不展示「无结果」 */
const lastSearchedQuery = ref('')
const selected = ref(new Map<string, { id: string; url: string; title: string }>())
const bulkBusy = ref(false)
const bulkModalOpen = ref(false)
const toast = ref('')
let searchAbort: AbortController | null = null

onUnmounted(() => {
  searchAbort?.abort()
})

const activeSources = computed(() => {
  const out: ('youtube' | 'bilibili')[] = []
  if (sourcesYoutube.value) out.push('youtube')
  if (sourcesBilibili.value) out.push('bilibili')
  return out
})

watch([sourcesYoutube, sourcesBilibili], () => {
  selected.value = new Map()
  lastSearchedQuery.value = ''
})

watch(pageSize, () => {
  page.value = 1
})

const items = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return allItems.value.slice(start, start + pageSize.value)
})

const hasMore = computed(() => page.value * pageSize.value < allItems.value.length)

function formatDuration(sec: number | null) {
  if (sec == null || Number.isNaN(sec)) return '—'
  const s = Math.floor(sec)
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${String(r).padStart(2, '0')}`
}

function thumbProxy(u: string | null) {
  if (!u) return ''
  return props.resolveUrl(`/api/image-proxy?url=${encodeURIComponent(u)}`)
}

function isSelected(id: string) {
  return selected.value.has(id)
}

function toggleRow(it: SearchItemRow, on: boolean) {
  const m = new Map(selected.value)
  if (on) m.set(it.id, { id: it.id, url: it.url, title: it.title })
  else m.delete(it.id)
  selected.value = m
}

function onRowCheck(it: SearchItemRow, ev: Event) {
  const t = ev.target as HTMLInputElement
  toggleRow(it, t.checked)
}

function clearSelection() {
  selected.value = new Map()
}

const selectedCount = computed(() => selected.value.size)

function applyStreamEvent(ev: SearchStreamEvent, urlSet: Set<string>) {
  if (ev.type === 'items') {
    const batch: SearchItemRow[] = []
    for (const it of ev.items) {
      if (urlSet.has(it.url)) continue
      urlSet.add(it.url)
      batch.push(it)
    }
    if (batch.length) {
      allItems.value = [...allItems.value, ...batch]
      total.value = allItems.value.length
    }
    return
  }
  if (ev.type === 'warning') {
    warning.value = warning.value ? `${warning.value}；${ev.message}` : ev.message
    return
  }
  if (ev.type === 'error') {
    searchError.value = ev.message
    return
  }
  if (ev.type === 'done') {
    total.value = ev.total
  }
}

async function runSearch(resetPage: boolean) {
  if (!query.value.trim()) {
    searchError.value = '请输入搜索关键词'
    return
  }
  if (!activeSources.value.length) {
    searchError.value = '请至少勾选一个搜索网站'
    return
  }
  if (resetPage) page.value = 1

  searchAbort?.abort()
  const ac = new AbortController()
  searchAbort = ac

  loading.value = true
  searchError.value = ''
  warning.value = ''
  allItems.value = []
  total.value = 0
  const urlSet = new Set<string>()
  let streamDone = false

  try {
    const res = await fetch(props.resolveUrl('/api/search'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/x-ndjson',
        ...bilibiliCookieRequestHeaders(),
      },
      body: JSON.stringify({
        query: query.value.trim(),
        sources: activeSources.value,
      }),
      signal: ac.signal,
    })

    if (!res.ok) {
      let detail = res.statusText
      try {
        const data = (await res.json()) as { detail?: string }
        detail = data.detail || detail
      } catch {
        // non-JSON error body
      }
      throw new Error(detail)
    }

    const reader = res.body?.getReader()
    if (!reader) throw new Error('搜索响应无内容')

    const decoder = new TextDecoder()
    let buf = ''

    const consumeLine = (line: string) => {
      const trimmed = line.trim()
      if (!trimmed) return
      const ev = JSON.parse(trimmed) as SearchStreamEvent
      applyStreamEvent(ev, urlSet)
      if (ev.type === 'done') streamDone = true
    }

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let nl = buf.indexOf('\n')
      while (nl >= 0) {
        consumeLine(buf.slice(0, nl))
        buf = buf.slice(nl + 1)
        nl = buf.indexOf('\n')
      }
    }
    if (buf.trim()) consumeLine(buf)
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') return
    searchError.value = e instanceof Error ? e.message : '搜索失败'
    if (!streamDone) {
      allItems.value = []
      total.value = 0
    }
  } finally {
    if (searchAbort === ac) {
      loading.value = false
      lastSearchedQuery.value = query.value.trim()
    }
  }
}

function nextPage() {
  if (hasMore.value) page.value += 1
}

function prevPage() {
  if (page.value > 1) page.value -= 1
}

const bulkModalItems = computed(() =>
  [...selected.value.values()].map((r) => ({ url: r.url, title: r.title || r.url })),
)

function downloadSelected() {
  if (!selected.value.size) {
    toast.value = '请先勾选视频'
    return
  }
  toast.value = ''
  bulkModalOpen.value = true
}

async function onBulkConfirm(preset: QualityPreset) {
  const rows = [...selected.value.values()]
  if (!rows.length) return
  bulkBusy.value = true
  bulkModalOpen.value = false
  try {
    const titles: Record<string, string> = {}
    for (const r of rows) titles[r.url] = r.title || r.url
    const res = await props.fetchJson<{ batch_id: string }>('/api/download-batches', {
      method: 'POST',
      body: JSON.stringify({
        urls: rows.map((r) => r.url),
        quality_preset: preset,
        titles,
      }),
    })
    toast.value = `已创建批量任务（${rows.length} 条），正在「下载任务」查看进度`
    emit('batch-created', res.batch_id)
  } catch (e) {
    toast.value = e instanceof Error ? e.message : '批量下载失败'
  } finally {
    bulkBusy.value = false
  }
}

function summarizeOne(it: SearchItemRow) {
  if (!props.summarizeLlmReady) {
    toast.value = '请在右上角「设置」中填写 OpenAI API Key 后再使用 AI 总结'
    return
  }
  emit('start-summarize', it.url)
}

function openInDownloadTab(url: string) {
  emit('prefill-download', url)
}
</script>

<template>
  <section class="search-page" aria-label="视频搜索">
    <header class="search-hero content-band">
      <h1>多平台视频搜索</h1>
      <p class="search-hero-lead">YouTube 与哔哩哔哩关键词检索，勾选后可批量下载或 AI 总结</p>
    </header>

    <div class="search-panel content-band">
    <p class="search-disclaimer">
      可同时勾选 <strong>YouTube</strong> 与 <strong>哔哩哔哩</strong> 进行搜索（B
      站在部分网络环境可能受限）。其它站点请改用「视频下载」粘贴链接。
    </p>

    <div class="search-toolbar">
      <form class="search-form" @submit.prevent="runSearch(true)">
        <fieldset class="search-sources" :disabled="loading">
          <legend class="sr-only">搜索网站（可多选）</legend>
          <label class="search-source-chip">
            <input v-model="sourcesYoutube" type="checkbox" :disabled="loading || !depsReady" />
            <span>YouTube</span>
          </label>
          <label class="search-source-chip">
            <input v-model="sourcesBilibili" type="checkbox" :disabled="loading || !depsReady" />
            <span>哔哩哔哩</span>
          </label>
        </fieldset>
        <label class="input-wrap search-input-wrap">
          <Search :size="18" />
          <input
            v-model="query"
            type="search"
            placeholder="输入关键词，例如 MV 歌名、教程标题…"
            autocomplete="off"
            :disabled="loading || !depsReady"
          />
        </label>
        <button
          type="submit"
          class="primary-btn"
          :disabled="loading || !depsReady || activeSources.length === 0"
        >
          <Loader2 v-if="loading" class="spin" :size="18" />
          <Search v-else :size="18" />
          搜索
        </button>
      </form>

      <p v-if="loading" class="search-loading-hint" role="status">
        正在加载更多结果…
        <template v-if="total">已加载 {{ total }} 条</template>
      </p>

      <div class="search-pagebar">
        <label>
          <select v-model.number="pageSize" class="search-pagesize" :disabled="loading">
            <option :value="10">10</option>
            <option :value="20">20</option>
            <option :value="30">30</option>
          </select>
          条
        </label>
        <span class="search-page-label">
          第 {{ page }} 页
          <template v-if="total"> · 共 {{ total }} 条</template>
        </span>
        <button type="button" class="secondary-btn" :disabled="loading || page <= 1" @click="prevPage">
          <ChevronLeft :size="18" /> 上一页
        </button>
        <button type="button" class="secondary-btn" :disabled="loading || !hasMore" @click="nextPage">
          下一页 <ChevronRight :size="18" />
        </button>
      </div>
    </div>

    <div v-if="!depsReady" class="warning-banner" role="status">
      <span>请等待后端就绪后再搜索。</span>
    </div>
    <div v-if="searchError" class="error-box">
      <span>{{ searchError }}</span>
    </div>
    <div v-if="warning" class="search-warning">
      {{ warning }}
    </div>
    <div v-if="toast" class="search-toast">
      {{ toast }}
    </div>

    <div class="search-results-head">
      <div class="search-bulk">
        <span class="search-selected">已选 {{ selectedCount }} 条</span>
        <button type="button" class="secondary-btn" :disabled="selectedCount === 0" @click="clearSelection">
          <Trash2 :size="16" aria-hidden="true" />
          清空勾选
        </button>
        <button type="button" class="primary-btn" :disabled="selectedCount === 0 || bulkBusy" @click="downloadSelected">
          <Loader2 v-if="bulkBusy" class="spin" :size="18" />
          <Download v-else :size="18" />
          下载选中
        </button>
      </div>
    </div>

    <ul class="search-list" role="list">
      <li v-for="it in items" :key="it.id" class="search-card" role="listitem">
        <label class="search-check">
          <input type="checkbox" :checked="isSelected(it.id)" @change="onRowCheck(it, $event)" />
        </label>
        <div class="search-card-thumb">
          <img v-if="it.thumbnail" :src="thumbProxy(it.thumbnail)" :alt="''" @error="($event.target as HTMLImageElement).style.display = 'none'" />
          <div v-else class="search-thumb-fallback"><Film :size="28" /></div>
        </div>
        <div class="search-card-body">
          <h3 class="search-card-title">{{ it.title }}</h3>
          <p class="search-card-meta">
            <span>{{ it.uploader || '—' }}</span>
            <span> · {{ formatDuration(it.duration) }}</span>
            <span class="search-card-src"> · {{ it.source === 'youtube' ? 'YouTube' : '哔哩哔哩' }}</span>
          </p>
          <div class="search-card-actions">
            <button type="button" class="linkish-btn" @click="openInDownloadTab(it.url)">解析下载</button>
          </div>
        </div>
        <div class="search-card-side">
          <button
            type="button"
            class="secondary-btn search-summarize-btn"
            :disabled="!summarizeLlmReady"
            @click="summarizeOne(it)"
          >
            <Sparkles :size="16" />
            视频总结
          </button>
        </div>
      </li>
    </ul>

    <p
      v-if="
        !loading &&
        !total &&
        !searchError &&
        depsReady &&
        query.trim() &&
        query.trim() === lastSearchedQuery
      "
      class="search-empty"
    >
      无结果，可换关键词或搜索源后再试。
    </p>

    <BulkDownloadModal
      :open="bulkModalOpen"
      :items="bulkModalItems"
      @cancel="bulkModalOpen = false"
      @confirm="onBulkConfirm"
    />
    </div>
  </section>
</template>
