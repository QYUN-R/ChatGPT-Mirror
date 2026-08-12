<template>
  <section class="admin-section">
    <div class="section-heading">
      <div>
        <h2>审计日志</h2>
        <p>套餐、订单、支付、号池和管理员操作记录</p>
      </div>
      <t-button variant="outline" @click="loadData">
        <template #icon><local-icon name="refresh" /></template>
        刷新
      </t-button>
    </div>
    <div class="toolbar">
      <t-input v-model="actionFilter" clearable placeholder="筛选操作，例如 order 或 assignment" @enter="applyFilter" />
      <t-button variant="outline" @click="applyFilter">查询</t-button>
    </div>
    <t-loading :loading="loading">
      <t-table :data="logs" :columns="columns" row-key="id" :pagination="pagination" @page-change="onPageChange">
        <template #target="{ row }">{{ row.target_type }} #{{ row.target_id || '-' }}</template>
        <template #detail="{ row }"><code>{{ compactDetail(row.detail) }}</code></template>
        <template #created_at="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </t-table>
    </t-loading>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import request from '@/api/request'
import { formatDateTime } from '@/utils/billing'

const loading = ref(false)
const logs = ref<any[]>([])
const actionFilter = ref('')
const pagination = reactive({ current: 1, pageSize: 20, total: 0 })
const columns = [
  { colKey: 'created_at', title: '时间', cell: 'created_at', width: 170 },
  { colKey: 'actor_name', title: '操作者', width: 130 },
  { colKey: 'action', title: '操作', width: 210 },
  { colKey: 'target', title: '对象', cell: 'target', width: 180 },
  { colKey: 'detail', title: '详情', cell: 'detail', minWidth: 280 },
  { colKey: 'ip_address', title: 'IP', width: 140 }
]

const compactDetail = (detail: any) => {
  const text = JSON.stringify(detail || {})
  return text.length > 120 ? `${text.slice(0, 117)}...` : text
}

const loadData = async () => {
  loading.value = true
  const params = new URLSearchParams({ page: String(pagination.current), page_size: String(pagination.pageSize) })
  if (actionFilter.value.trim()) params.set('action', actionFilter.value.trim())
  const data = await request(`/0x/admin/audit-logs?${params.toString()}`)
  logs.value = data?.results || []
  pagination.total = data?.count || 0
  loading.value = false
}

const applyFilter = () => {
  pagination.current = 1
  loadData()
}

const onPageChange = (info: any) => {
  pagination.current = info.current
  pagination.pageSize = info.pageSize
  loadData()
}

onMounted(loadData)
</script>

<style scoped>
.admin-section { padding: 22px 24px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.section-heading h2 { font-size: 18px; font-weight: 600; }
.section-heading p { margin-top: 5px; color: var(--app-text-muted); font-size: 13px; }
.toolbar { display: grid; grid-template-columns: minmax(260px, 420px) auto; gap: 10px; margin-bottom: 16px; }
code { display: block; overflow: hidden; color: #555550; font-family: "SFMono-Regular", Consolas, monospace; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 720px) { .admin-section { padding: 18px 14px; } .section-heading { align-items: flex-start; flex-direction: column; } .toolbar { grid-template-columns: 1fr auto; } }
</style>
