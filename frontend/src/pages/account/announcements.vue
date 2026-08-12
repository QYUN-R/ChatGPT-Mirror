<template>
  <section class="admin-section">
    <div class="section-heading">
      <div>
        <h2>公告</h2>
        <p>发布服务器迁移、故障修复、维护和售后通知</p>
      </div>
      <t-button theme="primary" @click="openDialog()">
        <template #icon><t-icon name="add" /></template>
        新建公告
      </t-button>
    </div>
    <t-loading :loading="loading">
      <t-table :data="announcements" :columns="columns" row-key="id">
        <template #audience="{ row }">{{ audienceLabel(row.audience, row.plan_name) }}</template>
        <template #status="{ row }">
          <t-tag :theme="row.is_published ? 'success' : 'default'" variant="light">{{ row.is_published ? '已发布' : '草稿' }}</t-tag>
        </template>
        <template #acknowledgement="{ row }">
          <t-tag :theme="row.requires_acknowledgement ? 'warning' : 'default'" variant="light">
            {{ row.requires_acknowledgement ? '强制确认' : '普通通知' }}
          </t-tag>
        </template>
        <template #published_at="{ row }">{{ formatDateTime(row.published_at) }}</template>
        <template #op="{ row }">
          <t-space size="small">
            <t-link theme="primary" @click="openDialog(row)">编辑</t-link>
            <t-link v-if="!row.is_published" theme="primary" @click="publish(row)">发布</t-link>
            <t-popconfirm v-else content="撤回后已生成的用户通知仍会保留" @confirm="unpublish(row)">
              <t-link theme="danger">撤回</t-link>
            </t-popconfirm>
          </t-space>
        </template>
      </t-table>
    </t-loading>

    <t-dialog
      :visible="dialogVisible"
      :header="form.id ? '编辑公告' : '新建公告'"
      :confirm-btn="{ loading: submitting }"
      width="620px"
      @confirm="save(false)"
      @close="dialogVisible = false"
    >
      <t-form :data="form" label-width="92px">
        <t-form-item label="标题"><t-input v-model="form.title" /></t-form-item>
        <t-form-item label="类型">
          <t-select v-model="form.category">
            <t-option value="NOTICE" label="普通通知" />
            <t-option value="MAINTENANCE" label="维护" />
            <t-option value="INCIDENT" label="故障" />
            <t-option value="MIGRATION" label="迁移" />
            <t-option value="SUPPORT" label="售后" />
          </t-select>
        </t-form-item>
        <t-form-item label="级别">
          <t-radio-group v-model="form.severity">
            <t-radio value="INFO">信息</t-radio>
            <t-radio value="WARNING">提醒</t-radio>
            <t-radio value="CRITICAL">重要</t-radio>
          </t-radio-group>
        </t-form-item>
        <t-form-item label="发送范围">
          <t-select v-model="form.audience">
            <t-option value="ALL" label="全部用户" />
            <t-option value="ACTIVE_SUBSCRIBERS" label="有效订阅用户" />
            <t-option value="PLAN" label="指定套餐" />
          </t-select>
        </t-form-item>
        <t-form-item v-if="form.audience === 'PLAN'" label="指定套餐">
          <t-select v-model="form.plan_id">
            <t-option v-for="plan in plans.filter(item => !item.is_archived)" :key="plan.id" :value="plan.id" :label="plan.name" />
          </t-select>
        </t-form-item>
        <t-form-item label="公告内容"><t-textarea v-model="form.content" :autosize="{ minRows: 6, maxRows: 12 }" /></t-form-item>
        <t-form-item label="强制确认"><t-switch v-model="form.requires_acknowledgement" /></t-form-item>
        <t-form-item label="失效时间"><t-date-picker v-model="form.expires_at" enable-time-picker clearable /></t-form-item>
      </t-form>
      <template #footer>
        <t-space>
          <t-button variant="outline" @click="dialogVisible = false">取消</t-button>
          <t-button variant="outline" :loading="submitting" @click="save(false)">保存草稿</t-button>
          <t-button theme="primary" :loading="submitting" @click="save(true)">保存并发布</t-button>
        </t-space>
      </template>
    </t-dialog>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import request from '@/api/request'
import { formatDateTime } from '@/utils/billing'

const loading = ref(false)
const submitting = ref(false)
const dialogVisible = ref(false)
const announcements = ref<any[]>([])
const plans = ref<any[]>([])
const columns = [
  { colKey: 'title', title: '标题', minWidth: 220 },
  { colKey: 'category', title: '类型', width: 110 },
  { colKey: 'severity', title: '级别', width: 90 },
  { colKey: 'audience', title: '发送范围', cell: 'audience', width: 140 },
  { colKey: 'acknowledgement', title: '阅读方式', cell: 'acknowledgement', width: 110 },
  { colKey: 'status', title: '状态', cell: 'status', width: 90 },
  { colKey: 'published_at', title: '发布时间', cell: 'published_at', width: 170 },
  { colKey: 'op', title: '操作', cell: 'op', width: 150 }
]
const form = reactive<any>({ id: 0, title: '', content: '', category: 'NOTICE', severity: 'INFO', audience: 'ALL', plan_id: null, requires_acknowledgement: false, expires_at: '' })

const audienceLabel = (value: string, planName?: string) => {
  if (value === 'ACTIVE_SUBSCRIBERS') return '有效订阅用户'
  if (value === 'PLAN') return planName || '指定套餐'
  return '全部用户'
}

const loadData = async () => {
  loading.value = true
  const [announcementData, planData] = await Promise.all([
    request('/0x/admin/announcements'),
    request('/0x/admin/plans')
  ])
  announcements.value = announcementData?.announcements || []
  plans.value = planData?.plans || []
  loading.value = false
}

const openDialog = (row?: any) => {
  Object.assign(form, row ? {
    id: row.id, title: row.title, content: row.content, category: row.category, severity: row.severity,
    audience: row.audience, plan_id: row.plan || null, requires_acknowledgement: Boolean(row.requires_acknowledgement), expires_at: row.expires_at || ''
  } : { id: 0, title: '', content: '', category: 'NOTICE', severity: 'INFO', audience: 'ALL', plan_id: null, requires_acknowledgement: false, expires_at: '' })
  dialogVisible.value = true
}

const save = async (publishNow: boolean) => {
  submitting.value = true
  const data = await request('/0x/admin/announcements', 'POST', {
    action: publishNow ? 'publish' : 'save',
    ...form,
    plan_id: form.audience === 'PLAN' ? form.plan_id : null
  })
  submitting.value = false
  if (!data) return
  dialogVisible.value = false
  MessagePlugin.success(publishNow ? '公告已发布' : '草稿已保存')
  loadData()
}

const publish = async (row: any) => {
  Object.assign(form, { ...row, plan_id: row.plan || null })
  await save(true)
}

const unpublish = async (row: any) => {
  const data = await request('/0x/admin/announcements', 'POST', { action: 'unpublish', id: row.id })
  if (data) {
    MessagePlugin.success('公告已撤回')
    loadData()
  }
}

onMounted(loadData)
</script>

<style scoped>
.admin-section { padding: 22px 24px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.section-heading h2 { font-size: 18px; font-weight: 600; }
.section-heading p { margin-top: 5px; color: var(--app-text-muted); font-size: 13px; }
@media (max-width: 720px) { .admin-section { padding: 18px 14px; } .section-heading { align-items: flex-start; flex-direction: column; } }
</style>
