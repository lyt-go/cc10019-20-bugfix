<template>
  <section class="page" data-module="profile_card">
    <header class="page-head">
      <div>
        <h2>画像卡片</h2>
        <p class="page-desc">刷新画像卡片并核对本次实际采用的标签计算版本；差量结果不计入人群完整覆盖数。</p>
      </div>
      <div class="page-actions">
        <select v-model="selectedCard" data-testid="card-select" @change="loadCard">
          <option v-for="card in cards" :key="card.card_id" :value="card.card_id">
            {{ card.card_id }} · {{ card.card_name }}
          </option>
        </select>
        <button class="btn primary" type="button" data-testid="run-analysis" :disabled="loading" @click="refreshCard">
          {{ loading ? '刷新中…' : '刷新卡片' }}
        </button>
      </div>
    </header>

    <div class="analysis-summary">
      <article class="analysis-card" data-testid="kpi-card">
        <span style="font-size:12px;color:var(--muted)">覆盖总人数</span>
        <strong style="font-size:22px;display:block">{{ totalCoverage ?? '—' }}</strong>
        <span style="font-size:12px;color:var(--muted)">仅累计全量结果</span>
      </article>
      <article class="analysis-card" data-testid="kpi-card">
        <span style="font-size:12px;color:var(--muted)">展示标签</span>
        <strong style="font-size:22px;display:block">{{ tagRows.length }}</strong>
        <span style="font-size:12px;color:var(--muted)">含差量/增量口径</span>
      </article>
      <article class="analysis-card" data-testid="kpi-card">
        <span style="font-size:12px;color:var(--muted)">待就绪标签</span>
        <strong style="font-size:22px;display:block">{{ pendingTags.length }}</strong>
        <span style="font-size:12px;color:var(--muted)">{{ pendingTags.join('、') || '全部就绪' }}</span>
      </article>
    </div>

    <p v-if="pendingTags.length" class="pending-notice" data-testid="pending-tags">
      本次刷新未采用的标签：{{ pendingTags.join('、') }}（仍在计算或未产出，卡片保留其余已就绪结果）
    </p>
    <p v-if="builtAt" class="page-foot" style="justify-content:flex-start;gap:8px" data-testid="built-at">
      <span>本次刷新时间：{{ builtAt }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </p>

    <div v-if="chartData.length" class="analysis-chart">
      <div v-for="bar in chartData" :key="bar.label" class="analysis-bar" :style="{ height: barHeight(bar.value) + 'px' }">
        <span>{{ bar.label }}</span>
      </div>
    </div>

    <table class="data-table" v-if="tagRows.length">
      <thead>
        <tr><th>标签</th><th>任务编号</th><th>任务版本</th><th>计算范围</th><th>覆盖人数</th><th>结果条数</th><th>状态</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in tagRows" :key="row.tag_id" :data-testid="`tag-${row.tag_id}`" :data-ready="String(row.ready)">
          <td>{{ row.tag_name }}（{{ row.tag_id }}）</td>
          <td>{{ row.task_id || '—' }}</td>
          <td>{{ row.compute_version || '—' }}</td>
          <td>{{ scopeLabel(row.compute_scope) }}</td>
          <td>{{ row.coverage_count ?? '—' }}</td>
          <td>{{ row.result_count }}</td>
          <td>{{ row.status }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="!tagRows.length && !loading" style="text-align:center;color:var(--muted)">请选择卡片后刷新</p>

    <footer class="page-foot">
      <span>{{ cardSummary }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type CardRow = { card_id: string; card_name: string }
type TagRow = {
  tag_id: string
  tag_name: string
  task_id: string
  compute_version: string
  compute_scope: string
  is_full_coverage: boolean
  ready: boolean
  coverage_count: number | null
  result_count: number
  status: string
}
type CardResult = {
  card_id: string
  display_tags: TagRow[]
  pending_tags: string[]
  total_coverage: number | null
  built_at: string
}

const ENDPOINT = '/api/profile-card'
const cards = ref<CardRow[]>([])
const selectedCard = ref('')
const tagRows = ref<TagRow[]>([])
const pendingTags = ref<string[]>([])
const totalCoverage = ref<number | null>(null)
const builtAt = ref('')
const loading = ref(false)
const errorMessage = ref('')

const chartData = computed(() =>
  tagRows.value
    .filter((row) => row.ready && row.coverage_count !== null)
    .map((row) => ({ label: row.tag_id, value: row.coverage_count as number })),
)

const cardSummary = computed(() => {
  if (!selectedCard.value) return '请选择画像卡片'
  const pending = pendingTags.value.length
  const total = totalCoverage.value ?? '—'
  return `${selectedCard.value} 覆盖总量 ${total}${pending ? `，${pending} 个标签未就绪` : ''}`
})

function scopeLabel(scope: string) {
  return ({ all: '全量', incremental: '增量', delta: '差量' } as Record<string, string>)[scope] ?? scope ?? '—'
}

function barHeight(value: number) {
  const max = Math.max(...chartData.value.map((item) => item.value), 1)
  return Math.max(10, (value / max) * 140)
}

function applyResult(result: CardResult) {
  tagRows.value = result.display_tags ?? []
  pendingTags.value = result.pending_tags ?? []
  totalCoverage.value = result.total_coverage ?? null
  builtAt.value = result.built_at ?? ''
}

async function loadCard() {
  if (!selectedCard.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    applyResult(await fetchJson<CardResult>(`${ENDPOINT}/${selectedCard.value}/result`))
  } catch (error) {
    tagRows.value = []
    errorMessage.value = error instanceof Error ? error.message : '画像卡片读取失败'
  } finally {
    loading.value = false
  }
}

async function refreshCard() {
  if (!selectedCard.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${selectedCard.value}/build`, { method: 'POST' })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '刷新失败')
    applyResult(payload.entry as CardResult)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '刷新失败'
  } finally {
    loading.value = false
  }
}

async function loadCards() {
  try {
    const payload = await fetchJson<{ items: CardRow[] }>(ENDPOINT)
    cards.value = payload.items ?? []
    if (cards.value.length) {
      selectedCard.value = cards.value[0].card_id
      await loadCard()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '卡片列表读取失败'
  }
}

onMounted(loadCards)
</script>
<style scoped>
.pending-notice { margin: 12px 0 0; padding: 8px 12px; border-radius: 6px; background: #fff7ed; color: #b45309; font-size: 13px; }
.page-actions { display: flex; gap: 8px; align-items: center; }
.page-actions select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; background: #fff; }
</style>
