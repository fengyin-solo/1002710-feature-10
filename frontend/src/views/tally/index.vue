<template>
  <section class="page" data-module="tally">
    <header class="page-head">
      <div>
        <h2>理货记录管理</h2>
        <p class="page-desc">同一条船的箱位多选后整组进入待核区，箱量逐条核对、残损统一登记，溢短自动单列，核对一致才转入已核。</p>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p v-if="errorMessage" class="banner error-text">{{ errorMessage }}</p>
    <p v-if="successMessage" class="banner ok-text">{{ successMessage }}</p>

    <section class="panel">
      <h3 class="panel-title">整组录入（同船箱位多选）</h3>
      <div class="filter-bar">
        <label class="filter-item">
          <span>批次号（重复提交只认第一次）</span>
          <input v-model="createForm.批次号" placeholder="批次号" />
        </label>
        <label class="filter-item">
          <span>对应船舶</span>
          <select v-model="createForm.对应船舶">
            <option value="" disabled>选择船舶</option>
            <option v-for="name in vesselOptions" :key="name" :value="name">{{ name }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>理货人员</span>
          <input v-model="createForm.理货人员" placeholder="理货人员" />
        </label>
      </div>
      <div class="position-grid">
        <label v-for="pos in positionOptions" :key="pos" class="position-item">
          <input type="checkbox" :checked="!!selectedPositions[pos]" @change="togglePosition(pos)" />
          <span class="position-name">{{ pos }}</span>
          <input
            v-if="selectedPositions[pos]"
            v-model="plannedInputs[pos]"
            class="planned-input"
            type="number"
            min="0"
            placeholder="应核箱量"
          />
        </label>
        <span v-if="!positionOptions.length" class="hint">箱位清单读取中或暂无箱位</span>
      </div>
      <div class="panel-actions">
        <button class="btn primary" type="button" @click="submitCreate">整组进入待核区</button>
        <span class="hint">已选 {{ selectedCount }} 个箱位，提交后整组进入待核区</span>
      </div>
    </section>

    <h3 class="section-title">理货批次</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>批次号</th>
          <th>对应船舶</th>
          <th>理货人员</th>
          <th>条目数</th>
          <th>待核</th>
          <th>已核</th>
          <th>溢短</th>
          <th>批次状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="batch in batches" :key="batch.id" :class="{ 'active-row': currentBatch?.id === batch.id }">
          <td>{{ batch.批次号 }}</td>
          <td>{{ batch.对应船舶 }}</td>
          <td>{{ batch.理货人员 || '—' }}</td>
          <td>{{ batch.汇总.条目数 }}</td>
          <td>{{ batch.汇总.待核数 }}</td>
          <td>{{ batch.汇总.已核数 }}</td>
          <td>{{ batch.汇总.溢短数 }}</td>
          <td><span class="badge" :class="batchStatusClass(batch.汇总.批次状态)">{{ batch.汇总.批次状态 }}</span></td>
          <td class="row-actions">
            <button class="link" type="button" @click="openBatch(batch.id)">核对</button>
          </td>
        </tr>
        <tr v-if="!batches.length">
          <td colspan="9" class="empty-state">暂无理货批次，可先在上方整组录入</td>
        </tr>
      </tbody>
    </table>

    <section v-if="currentBatch" class="panel">
      <h3 class="panel-title">批次核对：{{ currentBatch.批次号 }}（{{ currentBatch.对应船舶 }}）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>打回</th>
            <th>箱位编号</th>
            <th>应核箱量</th>
            <th>实核箱量</th>
            <th>核对结果</th>
            <th>溢短量</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in currentBatch.条目" :key="item.箱位编号">
            <td>
              <input
                type="checkbox"
                :disabled="item.状态 === '已核'"
                :checked="!!rejectSelection[item.箱位编号]"
                @change="toggleReject(item.箱位编号)"
              />
            </td>
            <td>{{ item.箱位编号 }}</td>
            <td>{{ item.应核箱量 }}</td>
            <td>
              <input
                v-if="item.状态 === '待核'"
                v-model="verifyInputs[item.箱位编号]"
                class="actual-input"
                type="number"
                min="0"
                placeholder="实核箱量"
              />
              <span v-else>{{ item.实核箱量 ?? '—' }}</span>
            </td>
            <td>{{ item.核对结果 || '—' }}</td>
            <td>{{ item.溢短量 || '—' }}</td>
            <td><span class="badge" :class="itemStatusClass(item.状态)">{{ item.状态 }}</span></td>
          </tr>
        </tbody>
      </table>

      <div class="verify-bar">
        <label class="filter-item grow">
          <span>残损情况（统一录，空着不许转入已核）</span>
          <input v-model="damageInput" placeholder="无残损请填「无残损」" />
        </label>
        <label class="filter-item">
          <span>核对汇总箱量（明细合计 {{ detailTotal }}）</span>
          <input v-model="summaryInput" type="number" min="0" />
        </label>
        <button class="btn primary" type="button" @click="submitVerify">提交核对</button>
        <button class="btn" type="button" @click="submitReject">打回所选</button>
      </div>

      <template v-if="currentBatch.溢短条目.length">
        <h4 class="exception-title">溢短清单（自动单列，打回后才能重填）</h4>
        <table class="data-table exception-table">
          <thead>
            <tr>
              <th>箱位编号</th>
              <th>应核箱量</th>
              <th>实核箱量</th>
              <th>核对结果</th>
              <th>溢短量</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in currentBatch.溢短条目" :key="item.箱位编号">
              <td>{{ item.箱位编号 }}</td>
              <td>{{ item.应核箱量 }}</td>
              <td>{{ item.实核箱量 }}</td>
              <td>{{ item.核对结果 }}</td>
              <td>{{ item.溢短量 }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </section>

    <footer class="page-foot">
      <span>共 {{ batches.length }} 个理货批次</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { fetchJson, request } from '@/api/client'

type TallyItem = {
  箱位编号: string
  应核箱量: number
  实核箱量: number | null
  核对结果: string
  溢短量: number
  状态: string
}

type BatchSummary = {
  条目数: number
  待核数: number
  已核数: number
  溢短数: number
  应核总量: number
  实核总量: number
  批次状态: string
}

type Batch = {
  id: number
  批次号: string
  对应船舶: string
  理货人员: string
  残损情况: string
  登记时间: string
  条目: TallyItem[]
  汇总: BatchSummary
  溢短条目: TallyItem[]
}

type Page<T> = { items: T[]; total: number }
type ActionReply = { ok: boolean; message: string; entry?: Batch }

const ENDPOINT = '/api/tally/batches'

const batches = ref<Batch[]>([])
const currentBatch = ref<Batch | null>(null)
const errorMessage = ref('')
const successMessage = ref('')

const createForm = ref({ 批次号: '', 对应船舶: '', 理货人员: '' })
const vesselOptions = ref<string[]>([])
const positionOptions = ref<string[]>([])
const selectedPositions = ref<Record<string, boolean>>({})
const plannedInputs = ref<Record<string, string>>({})

const verifyInputs = ref<Record<string, string>>({})
const damageInput = ref('')
const summaryInput = ref('')
const rejectSelection = ref<Record<string, boolean>>({})

const stats = computed(() => {
  let pending = 0
  let verified = 0
  let exception = 0
  for (const batch of batches.value) {
    pending += batch.汇总.待核数
    verified += batch.汇总.已核数
    exception += batch.汇总.溢短数
  }
  return [
    { label: '理货批次', value: batches.value.length },
    { label: '待核条目', value: pending },
    { label: '已核条目', value: verified },
    { label: '溢短条目', value: exception },
  ]
})

const selectedCount = computed(() => Object.keys(selectedPositions.value).length)

const detailTotal = computed(() => {
  if (!currentBatch.value) return 0
  let total = 0
  for (const item of currentBatch.value.条目) {
    if (item.状态 === '待核') {
      const raw = (verifyInputs.value[item.箱位编号] ?? '').trim()
      if (raw && /^\d+$/.test(raw)) total += Number(raw)
    } else if (item.实核箱量 !== null) {
      total += item.实核箱量
    }
  }
  return total
})

watch(detailTotal, (total) => {
  summaryInput.value = String(total)
})

function defaultBatchNo() {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const day = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`
  return `TB-${day}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
}

function clearMessages() {
  errorMessage.value = ''
  successMessage.value = ''
}

function itemStatusClass(status: string) {
  return {
    'is-pending': status === '待核',
    'is-verified': status === '已核',
    'is-exception': status === '有溢短',
  }
}

function batchStatusClass(status: string) {
  return {
    'is-pending': status === '待核中',
    'is-verified': status === '已核完成',
    'is-exception': status === '溢短待处理',
  }
}

function togglePosition(pos: string) {
  if (selectedPositions.value[pos]) {
    delete selectedPositions.value[pos]
    delete plannedInputs.value[pos]
  } else {
    selectedPositions.value[pos] = true
    plannedInputs.value[pos] = ''
  }
}

function toggleReject(pos: string) {
  if (rejectSelection.value[pos]) {
    delete rejectSelection.value[pos]
  } else {
    rejectSelection.value[pos] = true
  }
}

function applyBatch(batch: Batch) {
  currentBatch.value = batch
  verifyInputs.value = {}
  rejectSelection.value = {}
  damageInput.value = batch.残损情况
  summaryInput.value = String(batch.汇总.实核总量)
}

async function postAction(path: string, values: Record<string, unknown>): Promise<ActionReply> {
  const response = await request(path, { method: 'POST', body: JSON.stringify({ values }) })
  return (await response.json()) as ActionReply
}

async function loadBatches() {
  const payload = await fetchJson<Page<Batch>>(`${ENDPOINT}?size=200`)
  batches.value = payload.items
}

async function openBatch(id: number) {
  clearMessages()
  try {
    const batch = await fetchJson<Batch>(`${ENDPOINT}/${id}`)
    applyBatch(batch)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '理货批次读取失败'
  }
}

async function submitCreate() {
  clearMessages()
  if (!createForm.value.批次号.trim()) {
    errorMessage.value = '请填写批次号'
    return
  }
  if (!createForm.value.对应船舶) {
    errorMessage.value = '请选择对应船舶'
    return
  }
  const positions = Object.keys(selectedPositions.value)
  if (!positions.length) {
    errorMessage.value = '请先勾选同一条船下的箱位，整组进入待核区'
    return
  }
  const items = []
  for (const pos of positions) {
    const planned = (plannedInputs.value[pos] ?? '').trim()
    if (!planned || !/^\d+$/.test(planned)) {
      errorMessage.value = `箱位 ${pos} 的应核箱量要填非负整数`
      return
    }
    items.push({ 箱位编号: pos, 应核箱量: Number(planned) })
  }
  try {
    const reply = await postAction(ENDPOINT, { ...createForm.value, 条目: items })
    if (!reply.ok) {
      errorMessage.value = reply.message
      return
    }
    successMessage.value = reply.message
    selectedPositions.value = {}
    plannedInputs.value = {}
    createForm.value = { 批次号: defaultBatchNo(), 对应船舶: createForm.value.对应船舶, 理货人员: createForm.value.理货人员 }
    await loadBatches()
    if (reply.entry) applyBatch(reply.entry)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '理货批次登记失败'
  }
}

async function submitVerify() {
  clearMessages()
  const batch = currentBatch.value
  if (!batch) return
  const pendingItems = batch.条目.filter((item) => item.状态 === '待核')
  if (!pendingItems.length) {
    errorMessage.value = '本批次没有待核条目，已核完的不需要重复提交'
    return
  }
  if (!damageInput.value.trim()) {
    errorMessage.value = '残损情况未填写，空着不允许转入已核；无残损请填「无残损」'
    return
  }
  const details = []
  for (const item of pendingItems) {
    const raw = (verifyInputs.value[item.箱位编号] ?? '').trim()
    if (!raw || !/^\d+$/.test(raw)) {
      errorMessage.value = `箱位 ${item.箱位编号} 的实核箱量要填非负整数，核对结果要逐条填齐`
      return
    }
    details.push({ 箱位编号: item.箱位编号, 实核箱量: Number(raw) })
  }
  if (Number(summaryInput.value) !== detailTotal.value) {
    errorMessage.value = `核对汇总箱量 ${summaryInput.value || '（空）'} 与核对明细合计 ${detailTotal.value} 对不上`
    return
  }
  try {
    const reply = await postAction(`${ENDPOINT}/${batch.id}/verify`, {
      残损情况: damageInput.value.trim(),
      汇总箱量: Number(summaryInput.value),
      明细: details,
    })
    if (!reply.ok) {
      errorMessage.value = reply.message
      return
    }
    successMessage.value = reply.message
    if (reply.entry) applyBatch(reply.entry)
    await loadBatches()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '提交核对失败'
  }
}

async function submitReject() {
  clearMessages()
  const batch = currentBatch.value
  if (!batch) return
  const positions = Object.keys(rejectSelection.value)
  if (!positions.length) {
    errorMessage.value = '请先勾选要打回的箱位'
    return
  }
  try {
    const reply = await postAction(`${ENDPOINT}/${batch.id}/reject`, { 箱位: positions })
    if (!reply.ok) {
      errorMessage.value = reply.message
      return
    }
    successMessage.value = reply.message
    if (reply.entry) applyBatch(reply.entry)
    await loadBatches()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '打回失败'
  }
}

onMounted(async () => {
  createForm.value.批次号 = defaultBatchNo()
  try {
    const [vessels, positions] = await Promise.all([
      fetchJson<Page<Record<string, string>>>('/api/vessel?size=200'),
      fetchJson<Page<Record<string, string>>>('/api/yardplan?size=200'),
    ])
    vesselOptions.value = vessels.items.map((row) => row['船名']).filter(Boolean)
    positionOptions.value = positions.items.map((row) => row['箱位编号']).filter(Boolean)
  } catch {
    vesselOptions.value = []
    positionOptions.value = []
  }
  try {
    await loadBatches()
    if (batches.value.length) await openBatch(batches.value[0].id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '理货批次列表读取失败'
  }
})
</script>

<style scoped>
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 16px;
}
.panel-title {
  margin: 0 0 10px;
  font-size: 14px;
}
.section-title {
  font-size: 14px;
  margin: 16px 0 8px;
}
.banner {
  margin: 0 0 10px;
  font-size: 13px;
}
.ok-text {
  color: #067647;
}
.position-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.position-item {
  display: flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 13px;
  background: #fff;
}
.position-name {
  min-width: 80px;
}
.planned-input {
  width: 90px;
}
.panel-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.hint {
  color: var(--muted);
  font-size: 12px;
}
.active-row td {
  background: #eef4ff;
}
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
}
.badge.is-pending {
  background: #fff7e6;
  color: #b54708;
}
.badge.is-verified {
  background: #e7f6ec;
  color: #067647;
}
.badge.is-exception {
  background: #fee4e2;
  color: #b42318;
}
.verify-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  margin-top: 12px;
}
.filter-item.grow {
  flex: 1;
  min-width: 240px;
}
.filter-item input,
.filter-item select {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.filter-item span {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 2px;
}
.actual-input {
  width: 100px;
}
.exception-title {
  margin: 14px 0 6px;
  font-size: 13px;
  color: #b42318;
}
.exception-table th {
  background: #fef3f2;
}
</style>
