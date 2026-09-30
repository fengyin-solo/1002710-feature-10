<template>
  <section class="page tally-page" data-module="tally">
    <header class="page-head">
      <div>
        <h2>理货批量核对</h2>
        <p class="page-desc">
          同船箱位多选整组建批进入待核区，实收箱量逐条填、残损情况统一录；
          溢短条目自动挑出单列，核对一致才转入已核。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reload">刷新工作台</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">待核批次</span>
        <strong class="stat-value">{{ stats.reviewingBatches }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待核条目</span>
        <strong class="stat-value">{{ stats.pendingEntries }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">溢短条目（待处理）</span>
        <strong class="stat-value warn">{{ stats.overshortEntries }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已核完批次</span>
        <strong class="stat-value">{{ stats.doneBatches }}</strong>
      </article>
    </div>

    <p v-if="message" class="banner" :class="messageKind">{{ message }}</p>

    <!-- 第一步：选船 + 多选箱位整组建批 -->
    <section class="panel">
      <h3 class="panel-title">① 选船建批 · 勾选箱位整组进入待核区</h3>
      <form class="create-bar" @submit.prevent="createBatch">
        <label class="field">
          <span>对应船舶</span>
          <select v-model="form.vessel" @change="onVesselChange">
            <option value="" disabled>请选择船名</option>
            <option v-for="v in vessels" :key="v.vessel" :value="v.vessel">
              {{ v.vessel }}（{{ v.voyage }}，{{ v.total_slots }} 个箱位
              <template v-if="v.busy_slot_ids.length">，{{ v.busy_slot_ids.length }} 个在待核区）</template>
            </option>
          </select>
        </label>
        <label class="field">
          <span>理货人员</span>
          <input v-model="form.operator" placeholder="如：王理货" />
        </label>
        <div class="field grow">
          <span>
            箱位多选
            <button class="link" type="button" @click="toggleAllSlots(true)">全选空闲</button>
            <span class="sep">·</span>
            <button class="link" type="button" @click="toggleAllSlots(false)">清空</button>
          </span>
          <div v-if="form.vessel" class="slot-picker">
            <label
              v-for="slot in vesselSlots"
              :key="slot.id"
              class="slot-chip"
              :class="{ checked: form.slotIds.includes(slot.id), busy: busyIds.includes(slot.id) }"
            >
              <input
                type="checkbox"
                :value="slot.id"
                :disabled="busyIds.includes(slot.id)"
                v-model="form.slotIds"
              />
              <span>{{ slot.slot_no }}</span>
              <em>单据 {{ slot.expected_qty }}</em>
            </label>
          </div>
          <p v-else class="hint">请先选择一条船，再勾选该船箱位。</p>
        </div>
        <button class="btn primary" type="submit">整组进入待核区</button>
      </form>
    </section>

    <!-- 第二步：待核区 -->
    <section v-for="batch in reviewingBatches" :key="batch.id" class="panel batch-panel">
      <header class="batch-head">
        <div>
          <h3 class="panel-title">② 待核区 · {{ batch.batch_no }}</h3>
          <p class="hint">
            船名「{{ batch.vessel }}」 · {{ batch.operator }} · 建批 {{ batch.created_at }}
          </p>
        </div>
        <div class="summary-chips">
          <span>单据箱量 <strong>{{ batch.summary.expected_qty }}</strong></span>
          <span>实收箱量 <strong :class="{ warn: batch.summary.difference_qty !== 0 }">{{ batch.summary.actual_qty }}</strong></span>
          <span>差额 <strong :class="diffClass(batch.summary.difference_qty)">{{ signed(batch.summary.difference_qty) }}</strong></span>
          <span>待核 <strong>{{ batch.summary.pending }}</strong></span>
          <span>已核 <strong>{{ batch.summary.verified }}</strong></span>
          <span class="warn">溢短 {{ batch.summary.overshort_count }}</span>
        </div>
      </header>

      <!-- 残损情况统一录 -->
      <div class="damage-bar">
        <label class="field grow">
          <span>残损情况（统一录入到本批未核条目；无残损请选「无残损」）</span>
          <div class="damage-input-row">
            <input v-model="damageDraft[batch.id]" list="damage-options" placeholder="如：无残损 / 箱门凹陷 / 角件变形…" />
            <datalist id="damage-options">
              <option value="无残损"></option>
              <option value="箱体轻微划痕"></option>
              <option value="箱门凹陷"></option>
              <option value="角件变形"></option>
            </datalist>
            <button class="btn" type="button" @click="applyDamage(batch)">统一录入</button>
          </div>
        </label>
      </div>

      <table class="data-table entry-table">
        <thead>
          <tr>
            <th>箱位</th>
            <th>单据箱量</th>
            <th>实收箱量</th>
            <th>溢短</th>
            <th>残损情况</th>
            <th>状态</th>
            <th>备注</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="entry in batch.entries" :key="entry.slot_id" :class="rowClass(entry)">
            <td>{{ entry.slot_no }}</td>
            <td>{{ entry.expected_qty }}</td>
            <td>
              <input
                v-if="entry.status !== '已核'"
                class="qty-input"
                type="number"
                min="0"
                :value="qtyDraft[batch.id]?.[entry.slot_id] ?? ''"
                @input="onQtyInput(batch.id, entry.slot_id, ($event.target as HTMLInputElement).value)"
                :placeholder="String(entry.expected_qty)"
              />
              <span v-else>{{ entry.actual_qty }}</span>
            </td>
            <td>
              <span v-if="liveDiff(batch, entry) === null" class="hint">—</span>
              <strong v-else :class="diffClass(liveDiff(batch, entry))">{{ signed(liveDiff(batch, entry) as number) }}</strong>
            </td>
            <td>{{ entry.damage || '未录' }}</td>
            <td>
              <span class="badge" :class="badgeClass(entry.status)">{{ entry.status }}</span>
            </td>
            <td class="hint">{{ entry.reject_reason || '' }}</td>
          </tr>
        </tbody>
      </table>

      <!-- 溢短条目自动单列 -->
      <div v-if="liveOvershort(batch).length" class="overshort-panel">
        <h4>溢短条目（自动挑出，需处理后才能核）</h4>
        <ul>
          <li v-for="e in liveOvershort(batch)" :key="e.slot_id">
            <strong>{{ e.slot_no }}</strong>
            单据 {{ e.expected_qty }}，实收 {{ e.actual_qty }}，
            <span :class="diffClass(liveDiff(batch, e) as number)">
              {{ (liveDiff(batch, e) as number) > 0 ? '溢' : '短' }} {{ Math.abs(liveDiff(batch, e) as number) }}
            </span>
          </li>
        </ul>
      </div>

      <div class="batch-actions">
        <button class="btn" type="button" :disabled="busy" @click="saveFill(batch)">暂存核对数据</button>
        <button class="btn primary" type="button" :disabled="busy" @click="submitBatch(batch)">整组提交核对</button>
        <span class="hint">仅核对一致且残损已录的条目转入已核；溢短条目留在本待核区。</span>
      </div>

      <!-- 已核条目：可勾选打回 -->
      <div v-if="verifiedOf(batch).length" class="verified-panel">
        <h4>本批已核条目（勾选后可打回，未勾选的保持已核不动）</h4>
        <div class="reject-bar">
          <label v-for="entry in verifiedOf(batch)" :key="entry.slot_id" class="reject-chip">
            <input
              type="checkbox"
              :value="entry.slot_id"
              v-model="rejectDraft[batch.id]"
            />
            {{ entry.slot_no }}
          </label>
        </div>
        <div class="reject-action">
          <input v-model="rejectReason[batch.id]" placeholder="打回原因（必填）" />
          <button
            class="btn danger"
            type="button"
            :disabled="busy"
            @click="rejectEntries(batch)"
          >打回选中 {{ rejectDraft[batch.id]?.length || 0 }} 条</button>
        </div>
      </div>
    </section>

    <!-- 已核完批次 -->
    <section class="panel">
      <h3 class="panel-title">③ 已核完批次（{{ doneBatches.length }}）</h3>
      <table v-if="doneBatches.length" class="data-table">
        <thead>
          <tr>
            <th>批次号</th>
            <th>船名</th>
            <th>理货人员</th>
            <th>箱位数</th>
            <th>单据/实收/差额</th>
            <th>完成日期</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="batch in doneBatches" :key="batch.id">
            <td>{{ batch.batch_no }}</td>
            <td>{{ batch.vessel }}</td>
            <td>{{ batch.operator }}</td>
            <td>{{ batch.summary.total }}</td>
            <td>
              {{ batch.summary.expected_qty }} / {{ batch.summary.actual_qty }} /
              <strong :class="diffClass(batch.summary.difference_qty)">{{ signed(batch.summary.difference_qty) }}</strong>
            </td>
            <td>{{ batch.created_at }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="hint">暂无核完的批次。</p>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type EntryStatus = '待核' | '已核' | '打回'

interface TallySlot {
  id: number
  vessel: string
  voyage: string
  slot_no: string
  expected_qty: number
}

interface VesselCard {
  vessel: string
  voyage: string
  total_slots: number
  busy_slot_ids: number[]
}

interface TallyEntry {
  slot_id: number
  slot_no: string
  expected_qty: number
  actual_qty: number | null
  damage: string
  status: EntryStatus
  reject_reason: string
  difference: number | null
  is_overshort: boolean
  damage_filled: boolean
}

interface TallyBatch {
  id: number
  batch_no: string
  vessel: string
  operator: string
  status: '待核中' | '已核完'
  created_at: string
  entries: TallyEntry[]
  overshort_entries: TallyEntry[]
  summary: {
    total: number
    verified: number
    pending: number
    filled: number
    expected_qty: number
    actual_qty: number
    difference_qty: number
    over_count: number
    short_count: number
    overshort_count: number
  }
}

interface BatchResult {
  ok: boolean
  message: string
  batch: TallyBatch | null
  held: Array<{ slot_id: number; slot_no: string; difference: number }>
}

function token(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `t-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

const vessels = ref<VesselCard[]>([])
const vesselSlots = ref<TallySlot[]>([])
const batches = ref<TallyBatch[]>([])
const busy = ref(false)
const message = ref('')
const messageKind = ref<'ok' | 'err'>('ok')

const form = reactive<{ vessel: string; operator: string; slotIds: number[] }>({
  vessel: '',
  operator: '',
  slotIds: [],
})

// 每个待核批次一份本地草稿：实收箱量、残损、打回勾选/原因
const qtyDraft = reactive<Record<number, Record<number, string>>>({})
const damageDraft = reactive<Record<number, string>>({})
const rejectDraft = reactive<Record<number, number[]>>({})
const rejectReason = reactive<Record<number, string>>({})

const reviewingBatches = computed(() => batches.value.filter((b) => b.status === '待核中'))
const doneBatches = computed(() => batches.value.filter((b) => b.status === '已核完'))
const busyIds = computed(() => {
  const card = vessels.value.find((v) => v.vessel === form.vessel)
  return card ? card.busy_slot_ids : []
})

const stats = computed(() => ({
  reviewingBatches: reviewingBatches.value.length,
  doneBatches: doneBatches.value.length,
  pendingEntries: reviewingBatches.value.reduce((sum, b) => sum + b.summary.pending, 0),
  overshortEntries: reviewingBatches.value.reduce(
    (sum, b) => b.entries.filter((e) => e.status !== '已核' && e.difference !== null && e.difference !== 0).length + sum,
    0,
  ),
}))

function flash(text: string, kind: 'ok' | 'err' = 'ok') {
  message.value = text
  messageKind.value = kind
}

function signed(value: number): string {
  return value > 0 ? `+${value}` : String(value)
}

function diffClass(value: number | null): string {
  if (value === null || value === 0) return ''
  return value > 0 ? 'diff-over' : 'diff-short'
}

function badgeClass(status: EntryStatus): string {
  if (status === '已核') return 'badge-ok'
  if (status === '打回') return 'badge-reject'
  return 'badge-pending'
}

function rowClass(entry: TallyEntry): Record<string, boolean> {
  return {
    'row-verified': entry.status === '已核',
    'row-overshort': entry.status !== '已核' && entry.difference !== null && entry.difference !== 0,
  }
}

function liveDiff(batch: TallyBatch, entry: TallyEntry): number | null {
  if (entry.status === '已核') return entry.difference
  const raw = qtyDraft[batch.id]?.[entry.slot_id]
  if (raw === undefined || raw === '') return entry.actual_qty === null ? null : entry.difference
  const qty = Number(raw)
  return Number.isFinite(qty) ? qty - entry.expected_qty : null
}

function liveOvershort(batch: TallyBatch): TallyEntry[] {
  return batch.entries.filter((e) => e.status !== '已核' && (liveDiff(batch, e) ?? 0) !== 0 && (qtyDraft[batch.id]?.[e.slot_id] !== undefined || e.actual_qty !== null))
}

function verifiedOf(batch: TallyBatch): TallyEntry[] {
  return batch.entries.filter((e) => e.status === '已核')
}

async function onVesselChange() {
  form.slotIds = []
  vesselSlots.value = []
  if (!form.vessel) return
  const response = await request(`/api/tally/work/slots?vessel=${encodeURIComponent(form.vessel)}`)
  if (response.ok) {
    const payload = (await response.json()) as { items: TallySlot[] }
    vesselSlots.value = payload.items
  }
}

function toggleAllSlots(check: boolean) {
  if (check) {
    form.slotIds = vesselSlots.value.filter((s) => !busyIds.value.includes(s.id)).map((s) => s.id)
  } else {
    form.slotIds = []
  }
}

function initDrafts(batch: TallyBatch) {
  if (!qtyDraft[batch.id]) qtyDraft[batch.id] = {}
  if (rejectDraft[batch.id] === undefined) rejectDraft[batch.id] = []
  if (rejectReason[batch.id] === undefined) rejectReason[batch.id] = ''
  for (const entry of batch.entries) {
    if (entry.status !== '已核' && qtyDraft[batch.id][entry.slot_id] === undefined) {
      qtyDraft[batch.id][entry.slot_id] = entry.actual_qty === null ? '' : String(entry.actual_qty)
    }
  }
}

async function reload() {
  const [vesselResp, batchResp] = await Promise.all([
    request('/api/tally/work/vessels'),
    request('/api/tally/work/batches'),
  ])
  if (vesselResp.ok) {
    vessels.value = ((await vesselResp.json()) as { items: VesselCard[] }).items
  }
  if (batchResp.ok) {
    const payload = (await batchResp.json()) as { items: TallyBatch[] }
    batches.value = payload.items
    batches.value.forEach(initDrafts)
  } else {
    flash('理货工作台加载失败', 'err')
  }
}

async function createBatch() {
  if (!form.vessel) {
    flash('请先选择一条船', 'err')
    return
  }
  if (!form.slotIds.length) {
    flash('请至少勾选一个箱位', 'err')
    return
  }
  busy.value = true
  try {
    const response = await request('/api/tally/work/batches', {
      method: 'POST',
      body: JSON.stringify({
        vessel: form.vessel,
        slot_ids: form.slotIds,
        operator: form.operator,
        client_token: token(),
      }),
    })
    const result = (await response.json()) as BatchResult
    if (!result.ok) {
      flash(result.message, 'err')
      return
    }
    flash(result.message)
    form.slotIds = []
    await reload()
  } finally {
    busy.value = false
  }
}

function collectItems(batch: TallyBatch) {
  return batch.entries
    .filter((e) => e.status !== '已核')
    .map((e) => {
      const raw = qtyDraft[batch.id]?.[e.slot_id]
      return { slot_id: e.slot_id, actual_qty: raw === '' || raw === undefined ? null : Number(raw) }
    })
}

async function saveFill(batch: TallyBatch, damage?: string): Promise<TallyBatch | null> {
  const response = await request(`/api/tally/work/batches/${batch.id}/fill`, {
    method: 'POST',
    body: JSON.stringify({
      items: collectItems(batch),
      damage: damage ?? damageDraft[batch.id] ?? '',
      apply_all: true,
    }),
  })
  const result = (await response.json()) as BatchResult
  if (!result.ok) {
    flash(result.message, 'err')
    return null
  }
  return result.batch
}

async function applyDamage(batch: TallyBatch) {
  const text = (damageDraft[batch.id] ?? '').trim()
  if (!text) {
    flash('残损情况不能为空：无残损请录「无残损」', 'err')
    return
  }
  busy.value = true
  try {
    const updated = await saveFill(batch, text)
    if (updated) {
      flash('残损情况已统一录入到本批未核条目')
      await reload()
    }
  } finally {
    busy.value = false
  }
}

async function submitBatch(batch: TallyBatch) {
  busy.value = true
  const submitToken = token()
  try {
    // 先把当前页面的箱量与残损草稿暂存，再用服务端按明细重算的汇总提交核对
    const saved = await saveFill(batch)
    if (!saved) return
    const response = await request(`/api/tally/work/batches/${batch.id}/submit`, {
      method: 'POST',
      body: JSON.stringify({ client_token: submitToken, summary: saved.summary }),
    })
    const result = (await response.json()) as BatchResult
    if (!result.ok) {
      flash(result.message, 'err')
      return
    }
    flash(result.message + (result.held.length ? `；溢短箱位：${result.held.map((h) => h.slot_no).join('、')}` : ''))
    damageDraft[batch.id] = ''
    await reload()
  } finally {
    busy.value = false
  }
}

async function rejectEntries(batch: TallyBatch) {
  const ids = rejectDraft[batch.id] ?? []
  if (!ids.length) {
    flash('请先勾选要打回的已核条目', 'err')
    return
  }
  const reason = (rejectReason[batch.id] ?? '').trim()
  busy.value = true
  try {
    const response = await request(`/api/tally/work/batches/${batch.id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ slot_ids: ids, reason, client_token: token() }),
    })
    const result = (await response.json()) as BatchResult
    if (!result.ok) {
      flash(result.message, 'err')
      return
    }
    flash(result.message)
    rejectDraft[batch.id] = []
    rejectReason[batch.id] = ''
    await reload()
  } finally {
    busy.value = false
  }
}

function onQtyInput(batchId: number, slotId: number, value: string) {
  if (!qtyDraft[batchId]) qtyDraft[batchId] = {}
  qtyDraft[batchId][slotId] = value
}

onMounted(reload)
</script>

<style scoped>
.tally-page .panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
  margin-bottom: 14px;
}
.panel-title { font-size: 15px; margin: 0; }
.hint { color: var(--muted); font-size: 12px; margin: 2px 0; }
.sep { margin: 0 4px; color: var(--border); }
.warn { color: #b54708; }
.diff-over { color: #b42318; }
.diff-short { color: #b45309; }

.create-bar { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; margin-top: 10px; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.field.grow { flex: 1; min-width: 280px; }
.field select, .field input, .reject-action input {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
  min-width: 200px;
}
.slot-picker { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 2px; }
.slot-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  font-style: normal;
  cursor: pointer;
  background: #fff;
}
.slot-chip em { color: var(--muted); font-style: normal; font-size: 11px; }
.slot-chip.checked { border-color: var(--brand); background: #eff6ff; color: var(--brand); }
.slot-chip.busy { opacity: 0.45; cursor: not-allowed; background: #f1f5f9; }

.batch-head { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.summary-chips { display: flex; gap: 8px; flex-wrap: wrap; font-size: 12px; color: var(--muted); }
.summary-chips span {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 4px 8px;
  background: #f8fafc;
}
.summary-chips strong { color: #1f2937; margin-left: 4px; }

.damage-bar { margin: 12px 0; }
.damage-input-row { display: flex; gap: 8px; }
.damage-input-row input { flex: 1; border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 13px; }

.entry-table { margin-top: 8px; }
.entry-table .qty-input { width: 80px; border: 1px solid var(--border); border-radius: 6px; padding: 4px 6px; }
.row-verified { background: #f0fdf4; }
.row-overshort { background: #fef2f2; }
.badge { border-radius: 999px; padding: 2px 8px; font-size: 11px; }
.badge-ok { background: #dcfce7; color: #166534; }
.badge-pending { background: #f1f5f9; color: #475569; }
.badge-reject { background: #fee2e2; color: #991b1b; }

.overshort-panel {
  margin-top: 10px;
  border: 1px dashed #fca5a5;
  background: #fff7f7;
  border-radius: 8px;
  padding: 8px 12px;
}
.overshort-panel h4, .verified-panel h4 { margin: 4px 0 6px; font-size: 13px; }
.overshort-panel ul { margin: 0; padding-left: 18px; font-size: 13px; }

.batch-actions { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
.verified-panel { margin-top: 12px; border-top: 1px dashed var(--border); padding-top: 10px; }
.reject-bar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
.reject-chip {
  font-size: 12px;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 3px 10px;
  cursor: pointer;
}
.reject-action { display: flex; gap: 8px; }
.reject-action input { flex: 1; max-width: 320px; min-width: 180px; }
.btn.danger { border-color: #fca5a5; color: #b42318; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }

.banner { border-radius: 6px; padding: 8px 12px; font-size: 13px; margin: 0 0 12px; }
.banner.ok { background: #ecfdf3; border: 1px solid #abefc6; color: #067647; }
.banner.err { background: #fef3f2; border: 1px solid #fda29b; color: #b42318; }
</style>
