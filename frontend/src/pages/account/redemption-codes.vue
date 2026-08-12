<template>
  <div class="redemption-admin">
    <section class="admin-section settings-section">
      <div class="section-heading">
        <div>
          <h2>卡密兑换设置</h2>
          <p>统一管理兑换开关和外部购买地址</p>
        </div>
        <t-button theme="primary" :loading="savingSettings" @click="saveSettings">保存设置</t-button>
      </div>
      <div class="settings-grid">
        <t-form-item label="允许用户兑换">
          <t-switch v-model="settings.redemption_enabled" />
        </t-form-item>
        <t-form-item label="统一购买链接">
          <t-input v-model="settings.purchase_url" placeholder="https://example.com/buy" clearable />
        </t-form-item>
      </div>
    </section>

    <section class="admin-section">
      <div class="section-heading">
        <div>
          <h2>卡密批次</h2>
          <p>卡密绑定生成时选择的套餐价格方案，明文仅展示一次</p>
        </div>
        <t-button theme="primary" @click="openGenerateDialog">
          <template #icon><t-icon name="add" /></template>
          批量生成
        </t-button>
      </div>
      <div class="table-scroll">
      <t-table :data="batches" :columns="batchColumns" row-key="id" :loading="loadingBatches">
        <template #offer="{ row }">
          <div class="primary-cell"><strong>{{ row.plan_name }}</strong><span>{{ row.offer_name }} · {{ row.entitlement_months }} 个月</span></div>
        </template>
        <template #expires_at="{ row }">{{ row.expires_at ? formatDateTime(row.expires_at) : '永久有效' }}</template>
        <template #counts="{ row }">
          <div class="count-strip">
            <span class="available">未用 {{ row.available_count }}</span>
            <span class="redeemed">已用 {{ row.redeemed_count }}</span>
            <span>撤销 {{ row.revoked_count }}</span>
          </div>
        </template>
        <template #status="{ row }">
          <t-tag :theme="row.is_archived ? 'default' : row.is_active ? 'success' : 'warning'" variant="light">
            {{ row.is_archived ? '已归档' : row.is_active ? '启用' : '已停用' }}
          </t-tag>
        </template>
        <template #op="{ row }">
          <t-space size="small">
            <t-link theme="primary" @click="filterBatch(row)">查看卡密</t-link>
            <t-link v-if="!row.is_archived" :theme="row.is_active ? 'warning' : 'success'" @click="batchAction(row, row.is_active ? 'disable' : 'enable')">
              {{ row.is_active ? '停用' : '启用' }}
            </t-link>
            <t-popconfirm v-if="!row.is_archived" content="归档后整批卡密不能兑换，审计记录仍会保留" @confirm="batchAction(row, 'archive')">
              <t-link theme="danger">归档</t-link>
            </t-popconfirm>
          </t-space>
        </template>
      </t-table>
      </div>
    </section>

    <section class="admin-section">
      <div class="section-heading">
        <div>
          <h2>卡密明细</h2>
          <p>已使用卡密标红，并显示兑换用户、时间与来源 IP</p>
        </div>
        <t-space>
          <t-button variant="outline" @click="lookupVisible = true">核验完整卡密</t-button>
          <t-button variant="outline" :disabled="!selectedRedeemedIds.length" @click="codeAction('archive_redeemed')">归档已使用</t-button>
          <t-button variant="outline" :disabled="!selectedAvailableIds.length" @click="codeAction('revoke')">撤销未使用</t-button>
          <t-button v-if="includeArchived" variant="outline" :disabled="!selectedArchivedIds.length" @click="codeAction('restore')">恢复归档</t-button>
        </t-space>
      </div>
      <div class="toolbar">
        <t-select v-model="filters.batch_id" clearable placeholder="全部批次" @change="applyFilters">
          <t-option v-for="batch in batches" :key="batch.id" :value="batch.id" :label="batch.batch_no" />
        </t-select>
        <t-select v-model="filters.status" clearable placeholder="全部状态" @change="applyFilters">
          <t-option value="AVAILABLE" label="未使用" />
          <t-option value="REDEEMED" label="已使用" />
          <t-option value="REVOKED" label="已撤销" />
        </t-select>
        <t-input v-model="filters.q" clearable placeholder="序列号、用户、邮箱或订单号" @enter="applyFilters" />
        <t-input v-model="filters.ip" clearable placeholder="兑换 IP" @enter="applyFilters" />
        <t-checkbox v-model="includeArchived" @change="applyFilters">显示已归档</t-checkbox>
        <t-button variant="outline" @click="applyFilters">查询</t-button>
      </div>
      <div class="table-scroll">
      <t-table
        v-model:selected-row-keys="selectedRowKeys"
        :data="codes"
        :columns="codeColumns"
        :pagination="pagination"
        :loading="loadingCodes"
        :row-class-name="rowClassName"
        row-key="id"
        @page-change="onPageChange"
      >
        <template #identity="{ row }"><div class="primary-cell"><strong>{{ row.serial_no }}</strong><span>{{ row.code_mask }}</span></div></template>
        <template #offer="{ row }"><div class="primary-cell"><strong>{{ row.plan_name }}</strong><span>{{ row.offer_name }}</span></div></template>
        <template #status="{ row }">
          <t-tag :theme="statusTheme(row)" variant="light">{{ statusLabel(row) }}</t-tag>
        </template>
        <template #redeemer="{ row }"><div class="primary-cell"><strong>{{ row.username || '-' }}</strong><span>{{ row.redeemed_email || '-' }}</span></div></template>
        <template #redeemed_at="{ row }">{{ formatDateTime(row.redeemed_at) }}</template>
        <template #redeemed_ip="{ row }"><span class="mono">{{ row.redeemed_ip || '-' }}</span></template>
        <template #order="{ row }"><div class="primary-cell"><strong>{{ row.order_no || '-' }}</strong><span>{{ formatDateTime(row.subscription_ends_at) }}</span></div></template>
      </t-table>
      </div>
      <div class="summary-line">
        <span>未使用 {{ summary.available }}</span><span>已使用 {{ summary.redeemed }}</span><span>已撤销 {{ summary.revoked }}</span><span>已归档 {{ summary.archived }}</span>
      </div>
    </section>

    <t-dialog v-model:visible="lookupVisible" header="核验完整卡密" width="620px" :footer="false" @close="clearLookup">
      <div class="lookup-form">
        <t-input v-model="lookupCode" type="password" clearable maxlength="128" placeholder="粘贴客户提供的完整卡密" @enter="lookupFullCode" />
        <t-button theme="primary" :loading="lookupLoading" :disabled="!lookupCode.trim()" @click="lookupFullCode">安全核验</t-button>
      </div>
      <div v-if="lookupResult" class="lookup-result">
        <div><span>序列号</span><strong>{{ lookupResult.serial_no }}</strong></div>
        <div><span>状态</span><strong>{{ statusLabel(lookupResult) }}</strong></div>
        <div><span>套餐</span><strong>{{ lookupResult.plan_name }} · {{ lookupResult.offer_name }}</strong></div>
        <div><span>兑换用户</span><strong>{{ lookupResult.username || '-' }} / {{ lookupResult.redeemed_email || '-' }}</strong></div>
        <div><span>兑换时间</span><strong>{{ formatDateTime(lookupResult.redeemed_at) }}</strong></div>
        <div><span>兑换 IP</span><strong class="mono">{{ lookupResult.redeemed_ip || '-' }}</strong></div>
        <div><span>订单与到期</span><strong>{{ lookupResult.order_no || '-' }} / {{ formatDateTime(lookupResult.subscription_ends_at) }}</strong></div>
      </div>
    </t-dialog>

    <t-dialog
      v-model:visible="generateVisible"
      header="批量生成卡密"
      width="560px"
      :confirm-btn="{ content: '生成卡密', loading: generating }"
      @confirm="generateBatch"
    >
      <t-form :data="generateForm" label-width="100px">
        <t-form-item label="价格方案">
          <t-select v-model="generateForm.offer_id" placeholder="选择普通月卡、季卡或高级方案">
            <t-option v-for="offer in offerOptions" :key="offer.id" :value="offer.id" :label="offer.label" />
          </t-select>
        </t-form-item>
        <t-form-item label="生成数量"><t-input-number v-model="generateForm.quantity" :min="1" :max="1000" /></t-form-item>
        <t-form-item label="失效时间"><t-date-picker v-model="generateForm.expires_at" enable-time-picker clearable placeholder="留空为永久有效" /></t-form-item>
        <t-form-item label="批次备注"><t-input v-model="generateForm.note" maxlength="240" /></t-form-item>
      </t-form>
    </t-dialog>

    <t-dialog v-model:visible="plaintextVisible" header="卡密已生成" width="760px" :footer="false" :close-on-overlay-click="false" @close="clearGeneratedCodes">
      <t-alert theme="warning" message="完整卡密只在本窗口显示一次。关闭前请复制或下载 CSV，后台之后无法恢复明文。" />
      <div class="plaintext-actions">
        <strong>{{ generatedBatch?.batch_no }} · {{ generatedCodes.length }} 张</strong>
        <t-space><t-button variant="outline" @click="copyGenerated">复制全部</t-button><t-button theme="primary" @click="downloadCsv">下载 CSV</t-button></t-space>
      </div>
      <div class="plaintext-list">
        <div v-for="item in generatedCodes" :key="item.serial_no"><span>{{ item.serial_no }}</span><code>{{ item.code }}</code></div>
      </div>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import request from '@/api/request'
import { formatDateTime } from '@/utils/billing'

const settings = reactive({ redemption_enabled: false, purchase_url: '' })
const batches = ref<any[]>([])
const codes = ref<any[]>([])
const plans = ref<any[]>([])
const loadingBatches = ref(false)
const loadingCodes = ref(false)
const savingSettings = ref(false)
const generating = ref(false)
const generateVisible = ref(false)
const plaintextVisible = ref(false)
const lookupVisible = ref(false)
const lookupLoading = ref(false)
const lookupCode = ref('')
const lookupResult = ref<any>(null)
const includeArchived = ref(false)
const selectedRowKeys = ref<Array<number | string>>([])
const generatedCodes = ref<any[]>([])
const generatedBatch = ref<any>(null)
const summary = reactive({ available: 0, redeemed: 0, revoked: 0, archived: 0 })
const pagination = reactive({ current: 1, pageSize: 20, total: 0 })
const filters = reactive<any>({ batch_id: null, status: '', q: '', ip: '' })
const generateForm = reactive<any>({ offer_id: null, quantity: 10, expires_at: '', note: '' })

const offerOptions = computed(() => plans.value.flatMap(plan => (plan.offers || [])
  .filter((offer: any) => !offer.is_archived && !offer.is_draft)
  .map((offer: any) => ({ id: offer.id, label: `${plan.name} · ${offer.name} · ${offer.months} 个月` }))))
const selectedRows = computed(() => {
  const selected = new Set(selectedRowKeys.value.map(value => Number(value)))
  return codes.value.filter(row => selected.has(Number(row.id)))
})
const selectedRedeemedIds = computed(() => selectedRows.value.filter(row => row.status === 'REDEEMED' && !row.is_archived).map(row => row.id))
const selectedAvailableIds = computed(() => selectedRows.value.filter(row => row.status === 'AVAILABLE' && !row.is_archived).map(row => row.id))
const selectedArchivedIds = computed(() => selectedRows.value.filter(row => row.is_archived).map(row => row.id))

const batchColumns = [
  { colKey: 'batch_no', title: '批次号', width: 220 }, { colKey: 'offer', title: '套餐方案', cell: 'offer', width: 180 },
  { colKey: 'quantity', title: '总数', width: 80 }, { colKey: 'counts', title: '状态统计', cell: 'counts', width: 220 },
  { colKey: 'expires_at', title: '失效时间', cell: 'expires_at', width: 170 }, { colKey: 'status', title: '批次状态', cell: 'status', width: 100 },
  { colKey: 'note', title: '备注', ellipsis: true }, { colKey: 'op', title: '操作', cell: 'op', width: 210, fixed: 'right' }
]
const codeColumns = [
  { colKey: 'row-select', type: 'multiple', width: 46 }, { colKey: 'identity', title: '序列号 / 掩码', cell: 'identity', width: 260 },
  { colKey: 'batch_no', title: '批次', width: 210 }, { colKey: 'offer', title: '套餐', cell: 'offer', width: 150 },
  { colKey: 'status', title: '状态', cell: 'status', width: 100 }, { colKey: 'redeemer', title: '兑换用户', cell: 'redeemer', width: 170 },
  { colKey: 'redeemed_at', title: '兑换时间', cell: 'redeemed_at', width: 170 }, { colKey: 'redeemed_ip', title: '兑换 IP', cell: 'redeemed_ip', width: 150 },
  { colKey: 'order', title: '订单 / 到期', cell: 'order', width: 210 }
]

const statusLabel = (row: any) => row.is_archived ? '已归档' : row.status === 'REDEEMED' ? '已使用' : row.status === 'REVOKED' ? '已撤销' : '未使用'
const statusTheme = (row: any) => row.is_archived ? 'default' : row.status === 'REDEEMED' ? 'danger' : row.status === 'REVOKED' ? 'warning' : 'success'
const rowClassName = ({ row }: any) => row.status === 'REDEEMED' ? 'redeemed-row' : row.is_archived ? 'archived-row' : ''

const loadSettings = async () => { const data = await request('/0x/admin/redemption-settings'); if (data?.settings) Object.assign(settings, data.settings) }
const loadPlans = async () => { const data = await request('/0x/admin/plans'); plans.value = data?.plans || [] }
const loadBatches = async () => {
  loadingBatches.value = true
  const data = await request(`/0x/admin/redemption-batches?include_archived=${includeArchived.value ? 1 : 0}`)
  batches.value = data?.batches || []
  loadingBatches.value = false
}
const loadCodes = async () => {
  loadingCodes.value = true
  const params = new URLSearchParams({ page: String(pagination.current), page_size: String(pagination.pageSize), include_archived: includeArchived.value ? '1' : '0' })
  if (filters.batch_id) params.set('batch_id', String(filters.batch_id)); if (filters.status) params.set('status', filters.status)
  if (filters.q.trim()) params.set('q', filters.q.trim()); if (filters.ip.trim()) params.set('ip', filters.ip.trim())
  const data = await request(`/0x/admin/redemption-codes?${params.toString()}`)
  codes.value = data?.results || []; pagination.total = data?.count || 0; Object.assign(summary, data?.summary || {})
  selectedRowKeys.value = []; loadingCodes.value = false
}
const saveSettings = async () => { savingSettings.value = true; const data = await request('/0x/admin/redemption-settings', 'POST', settings); savingSettings.value = false; if (data) MessagePlugin.success('卡密设置已保存') }
const openGenerateDialog = () => { generateForm.offer_id = offerOptions.value[0]?.id || null; generateForm.quantity = 10; generateForm.expires_at = ''; generateForm.note = ''; generateVisible.value = true }
const generateBatch = async () => {
  generating.value = true
  const data = await request('/0x/admin/redemption-batches', 'POST', { action: 'generate', ...generateForm, expires_at: generateForm.expires_at || null })
  generating.value = false
  if (!data?.batch) return
  generatedBatch.value = data.batch; generatedCodes.value = data.plaintext_codes || []; generateVisible.value = false; plaintextVisible.value = true
  MessagePlugin.success(`已生成 ${generatedCodes.value.length} 张卡密`); await Promise.all([loadBatches(), loadCodes()])
}
const batchAction = async (row: any, action: string) => { const data = await request('/0x/admin/redemption-batches', 'POST', { action, batch_id: row.id }); if (data) { MessagePlugin.success('批次状态已更新'); await Promise.all([loadBatches(), loadCodes()]) } }
const codeAction = async (action: string) => {
  const ids = action === 'archive_redeemed' ? selectedRedeemedIds.value : action === 'revoke' ? selectedAvailableIds.value : selectedArchivedIds.value
  const data = await request('/0x/admin/redemption-codes', 'POST', { action, code_ids: ids })
  if (data) { MessagePlugin.success(`已处理 ${data.count} 张卡密`); await Promise.all([loadBatches(), loadCodes()]) }
}
const lookupFullCode = async () => {
  if (!lookupCode.value.trim()) return
  lookupLoading.value = true
  const data = await request('/0x/admin/redemption-codes', 'POST', { action: 'lookup', code: lookupCode.value.trim() })
  lookupLoading.value = false
  lookupCode.value = ''
  lookupResult.value = data?.code || null
}
const clearLookup = () => { lookupCode.value = ''; lookupResult.value = null }
const filterBatch = (row: any) => { filters.batch_id = row.id; pagination.current = 1; loadCodes() }
const applyFilters = () => { pagination.current = 1; Promise.all([loadBatches(), loadCodes()]) }
const onPageChange = (info: any) => { pagination.current = info.current; pagination.pageSize = info.pageSize; loadCodes() }
const generatedText = () => generatedCodes.value.map(item => `${item.serial_no}\t${item.code}`).join('\n')
const copyGenerated = async () => { await navigator.clipboard.writeText(generatedText()); MessagePlugin.success('卡密已复制') }
const csvEscape = (value: string) => `"${String(value).replace(/"/g, '""')}"`
const downloadCsv = () => {
  const csv = '\uFEFF序列号,卡密\r\n' + generatedCodes.value.map(item => `${csvEscape(item.serial_no)},${csvEscape(item.code)}`).join('\r\n')
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); const link = document.createElement('a')
  link.href = url; link.download = `${generatedBatch.value?.batch_no || 'redemption-codes'}.csv`; link.click(); URL.revokeObjectURL(url)
}
const clearGeneratedCodes = () => { generatedCodes.value = []; generatedBatch.value = null }

onMounted(() => Promise.all([loadSettings(), loadPlans(), loadBatches(), loadCodes()]))
</script>

<style scoped>
.redemption-admin { display: grid; gap: 20px; min-width: 0; }
.admin-section { min-width: 0; padding: 22px 24px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.section-heading h2 { margin: 0; font-size: 18px; font-weight: 600; }
.section-heading p { margin-top: 5px; color: var(--app-text-muted); font-size: 13px; }
.settings-grid { display: grid; grid-template-columns: 220px minmax(320px, 1fr); gap: 20px; }
.toolbar { display: grid; grid-template-columns: 190px 140px minmax(220px, 1fr) 160px auto auto; gap: 10px; align-items: center; margin-bottom: 16px; }
.table-scroll { width: 100%; max-width: 100%; min-width: 0; overflow-x: auto; overscroll-behavior-inline: contain; }
.table-scroll :deep(.t-table) { min-width: 1120px; }
.primary-cell { display: grid; min-width: 0; gap: 4px; }.primary-cell strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }.primary-cell span { color: var(--app-text-muted); font-size: 12px; }
.count-strip { display: flex; gap: 10px; font-size: 12px; }.count-strip .available { color: #39724b; }.count-strip .redeemed { color: #b33a3a; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }
.summary-line { display: flex; justify-content: flex-end; gap: 20px; padding-top: 14px; color: var(--app-text-muted); font-size: 13px; }
.redemption-admin :deep(.redeemed-row > td) { background: #fff1f0 !important; }.redemption-admin :deep(.archived-row > td) { opacity: 0.68; }
.plaintext-actions { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 18px 0 12px; }
.lookup-form { display: grid; grid-template-columns: 1fr 110px; gap: 10px; }.lookup-form :deep(.t-input__inner) { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }
.lookup-result { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; margin-top: 16px; overflow: hidden; border: 1px solid var(--app-border); border-radius: 7px; background: var(--app-border); }
.lookup-result > div { min-width: 0; padding: 14px; background: var(--app-surface); }.lookup-result span { display: block; color: var(--app-text-muted); font-size: 12px; }.lookup-result strong { display: block; margin-top: 6px; overflow-wrap: anywhere; font-size: 13px; }
.plaintext-list { max-height: 430px; overflow: auto; border: 1px solid var(--app-border); border-radius: 7px; }
.plaintext-list > div { display: grid; grid-template-columns: 240px 1fr; gap: 16px; padding: 10px 12px; border-bottom: 1px solid var(--app-border); }.plaintext-list > div:last-child { border-bottom: 0; }
.plaintext-list span { color: var(--app-text-muted); font-size: 12px; }.plaintext-list code { font-size: 13px; user-select: all; }
@media (max-width: 900px) {
  .settings-grid, .toolbar, .lookup-form, .lookup-result { grid-template-columns: 1fr; }
  .section-heading { width: 100%; align-items: flex-start; flex-direction: column; }
  .section-heading > .t-space { width: 100%; flex-wrap: wrap; }
  .admin-section { max-width: 100%; padding: 18px 14px; overflow: hidden; }
  .summary-line { justify-content: flex-start; flex-wrap: wrap; gap: 8px 16px; }
  .plaintext-list > div { grid-template-columns: 1fr; gap: 5px; }
}
</style>
