<script setup lang="ts">
import { ref, watch } from 'vue'
import { Download, X } from 'lucide-vue-next'

export type BulkDownloadItem = { url: string; title: string }
export type QualityPreset = 'best' | '1080' | '720' | '480'

const props = defineProps<{
  open: boolean
  items: BulkDownloadItem[]
}>()

const emit = defineEmits<{
  confirm: [preset: QualityPreset]
  cancel: []
}>()

const preset = ref<QualityPreset>('best')

watch(
  () => props.open,
  (v) => {
    if (v) preset.value = 'best'
  },
)

const options: { value: QualityPreset; label: string; hint: string }[] = [
  { value: 'best', label: '尽量高清', hint: '各平台可用的最高清晰度（推荐）' },
  { value: '1080', label: '1080p', hint: '不超过 1080p' },
  { value: '720', label: '720p', hint: '不超过 720p' },
  { value: '480', label: '480p', hint: '不超过 480p' },
]
</script>

<template>
  <div v-if="open" class="bulk-modal-backdrop" role="presentation" @click.self="emit('cancel')">
    <div
      class="bulk-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="bulk-modal-title"
      tabindex="-1"
      @keydown.escape.prevent="emit('cancel')"
    >
      <div class="bulk-modal-head">
        <h2 id="bulk-modal-title">批量下载</h2>
        <button type="button" class="settings-close" aria-label="关闭" @click="emit('cancel')">
          <X :size="20" />
        </button>
      </div>
      <p class="bulk-modal-lead">
        已选 <strong>{{ items.length }}</strong> 个视频。混选 YouTube / 哔哩哔哩时，将按各站规则自动匹配格式。
      </p>
      <fieldset class="bulk-quality-fieldset">
        <legend class="sr-only">选择画质</legend>
        <label v-for="opt in options" :key="opt.value" class="bulk-quality-option">
          <input v-model="preset" type="radio" name="bulk-quality" :value="opt.value" />
          <span class="bulk-quality-text">
            <strong>{{ opt.label }}</strong>
            <small>{{ opt.hint }}</small>
          </span>
        </label>
      </fieldset>
      <div class="bulk-modal-actions">
        <button type="button" class="secondary-btn" @click="emit('cancel')">取消</button>
        <button type="button" class="primary-btn" @click="emit('confirm', preset)">
          <Download :size="18" />
          开始下载
        </button>
      </div>
    </div>
  </div>
</template>
