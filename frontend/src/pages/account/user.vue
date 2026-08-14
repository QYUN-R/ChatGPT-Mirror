<template>
  <div>
    <t-card title="用户" subtitle="管理可访问系统的用户、账号池和模型权限" :bordered="false">
      <template #actions>
        <t-button theme="primary" @click="showAddDialog">
          <template #icon><local-icon name="add" /></template>
          添加用户
        </t-button>
      </template>
      <div class="table-toolbar">
        <t-input v-model="query" clearable placeholder="搜索用户名、邮箱或备注" @enter="applyFilters" />
        <t-select v-model="statusFilter" clearable placeholder="全部状态" @change="applyFilters">
          <t-option value="active" label="启用" />
          <t-option value="inactive" label="禁用" />
        </t-select>
        <t-button variant="outline" @click="applyFilters">查询</t-button>
        <t-button variant="outline" :disabled="!selectedRowKeys.length" @click="batchAction('activate')">批量启用</t-button>
        <t-button variant="outline" :disabled="!selectedRowKeys.length" @click="batchAction('deactivate')">批量禁用</t-button>
        <t-button variant="outline" :disabled="!selectedRowKeys.length" @click="openBatchDeviceDialog">批量设备策略</t-button>
      </div>

      <div class="desktop-user-table">
        <t-table
          :data="tableData"
          :columns="columns"
          :loading="loading"
          :pagination="pagination"
          @page-change="onPageChange"
          row-key="id"
          v-model:selected-row-keys="selectedRowKeys"
        >
        <template #is_active="{ row }">
          <t-tag :theme="row.is_active ? 'success' : 'danger'">
            {{ row.is_active ? '启用' : '禁用' }}
          </t-tag>
        </template>
        <template #email="{ row }">
          <div class="email-cell">
            <span class="email-value">{{ row.email || '未绑定' }}</span>
            <t-tag v-if="row.email" :theme="row.email_verified ? 'success' : 'warning'" size="small" variant="light">
              {{ row.email_verified ? '已验证' : '待验证' }}
            </t-tag>
          </div>
        </template>
        <template #expired_date="{ row }">
          {{ row.expired_date || '永久' }}
        </template>
        <template #model_limit="{ row }">
          <t-space size="small" v-if="normalizedModelLimits(row.model_limit).length > 0">
            <t-tag v-for="model in normalizedModelLimits(row.model_limit).slice(0, 2)" :key="model" size="small">
              {{ model }}
            </t-tag>
            <t-tag v-if="normalizedModelLimits(row.model_limit).length > 2" size="small">
              +{{ normalizedModelLimits(row.model_limit).length - 2 }}
            </t-tag>
          </t-space>
          <span v-else class="text-gray">全部模型</span>
        </template>
        <template #force_chat_mode="{ row }">
          <t-tag :theme="row.force_chat_mode !== false ? 'success' : 'default'">
            {{ row.force_chat_mode !== false ? '自动切回' : '允许 Work' }}
          </t-tag>
        </template>
        <template #device_policy="{ row }">
          <div class="device-policy-cell">
            <strong>{{ row.device_policy?.enabled ? `${row.device_policy.limit} 台` : '仅 1 台' }}</strong>
            <span>{{ devicePolicySource(row.device_policy) }} · 在线 {{ row.active_device_count || 0 }}</span>
          </div>
        </template>
        <template #subscription="{ row }">
          <div v-if="row.subscription" class="subscription-cell">
            <strong>{{ row.subscription.plan_name }}</strong>
            <span>{{ row.subscription.status }}</span>
          </div>
          <span v-else class="text-gray">未开通</span>
        </template>
        <template #op="{ row }">
            <t-space>
              <t-link theme="primary" @click="showEditDialog(row)">编辑</t-link>
              <t-link theme="primary" @click="openDeviceDialog(row)">设备</t-link>
            <t-popconfirm content="确定删除该用户吗？" @confirm="handleDelete(row)">
              <t-link theme="danger">删除</t-link>
            </t-popconfirm>
          </t-space>
        </template>
        </t-table>
      </div>

      <div class="mobile-user-list">
        <article v-for="row in tableData" :key="row.id" class="mobile-user-card">
          <div class="mobile-user-heading">
            <div>
              <strong>{{ row.username }}</strong>
              <span>ID {{ row.id }}</span>
            </div>
            <t-tag :theme="row.is_active ? 'success' : 'danger'" variant="light">
              {{ row.is_active ? '启用' : '禁用' }}
            </t-tag>
          </div>
          <dl>
            <div><dt>邮箱</dt><dd>{{ row.email || '未绑定' }}<span v-if="row.email"> · {{ row.email_verified ? '已验证' : '待验证' }}</span></dd></div>
            <div><dt>套餐</dt><dd>{{ row.subscription?.plan_name || '未开通' }}</dd></div>
            <div><dt>设备</dt><dd>{{ row.device_policy?.enabled ? `${row.active_device_count || 0} / ${row.device_policy.limit} 台` : '仅 1 台' }} · {{ devicePolicySource(row.device_policy) }}</dd></div>
            <div><dt>过期</dt><dd>{{ row.expired_date || '永久' }}</dd></div>
            <div><dt>备注</dt><dd>{{ row.remark || '-' }}</dd></div>
          </dl>
          <div class="mobile-user-actions">
            <t-button variant="outline" @click="showEditDialog(row)">编辑</t-button>
            <t-button variant="outline" @click="openDeviceDialog(row)">设备</t-button>
            <t-popconfirm content="确定删除该用户吗？" @confirm="handleDelete(row)">
              <t-button theme="danger" variant="outline">删除</t-button>
            </t-popconfirm>
          </div>
        </article>
        <div v-if="!loading && !tableData.length" class="mobile-empty">暂无用户</div>
        <t-pagination
          v-if="pagination.total > pagination.pageSize"
          class="mobile-pagination"
          :current="pagination.current"
          :page-size="pagination.pageSize"
          :total="pagination.total"
          :show-page-number="false"
          :show-page-size="false"
          @change="onPageChange"
        />
      </div>
    </t-card>

    <!-- 添加/编辑对话框 -->
    <t-dialog
      :visible="dialogVisible"
      :header="isEdit ? '编辑用户' : '添加用户'"
      :confirm-btn="{ loading: submitLoading }"
      @confirm="handleSubmit"
      @close="dialogVisible = false"
      width="600px"
    >
      <t-form :data="formData" :rules="formRules" ref="formRef" label-width="100px">
        <t-form-item label="用户名" name="username">
          <t-input v-model="formData.username" :disabled="isEdit" placeholder="请输入用户名" />
        </t-form-item>
        <t-form-item label="验证邮箱" name="email">
          <t-input v-model="formData.email" placeholder="请输入 QQ、网易或 Google 邮箱" />
          <template #help>
            <span class="form-help">修改后会取消验证状态，并要求用户使用新邮箱完成验证码确认</span>
          </template>
        </t-form-item>
        <t-form-item label="密码" name="password">
          <t-input v-model="formData.password" type="password" :placeholder="isEdit ? '留空则不修改' : '请输入密码'" />
        </t-form-item>
        <t-form-item label="是否启用" name="is_active">
          <t-switch v-model="formData.is_active" />
        </t-form-item>
        <t-form-item label="账号独立会话" name="isolated_session">
          <t-switch v-model="formData.isolated_session" />
        </t-form-item>
        <t-form-item label="设备策略" name="device_policy_managed_by_plan">
          <t-radio-group v-model="formData.device_policy_managed_by_plan">
            <t-radio :value="true">跟随套餐</t-radio>
            <t-radio :value="false">单独设置</t-radio>
          </t-radio-group>
          <template #help>
            <span class="form-help">跟随套餐时自动采用当前套餐上限；没有套餐时使用系统默认值</span>
          </template>
        </t-form-item>
        <template v-if="!formData.device_policy_managed_by_plan">
          <t-form-item label="允许多设备" name="multi_device_enabled">
            <t-switch v-model="formData.multi_device_enabled" />
          </t-form-item>
          <t-form-item label="设备上限" name="device_limit">
            <t-input-number v-model="formData.device_limit" :min="1" :max="50" :disabled="!formData.multi_device_enabled" />
          </t-form-item>
        </template>
        <t-form-item label="新设备验证" name="new_device_verification_enabled">
          <t-switch v-model="formData.new_device_verification_enabled" />
          <template #help><span class="form-help">开启后新浏览器首次登录需要邮箱验证码；管理员账号自动豁免</span></template>
        </t-form-item>
        <t-form-item label="自动退出 Work" name="force_chat_mode">
          <t-switch v-model="formData.force_chat_mode" />
          <template #help>
            <span class="form-help">开启后检测到 Work 模式会自动点击“聊天 / Chat”切回聊天模式</span>
          </template>
        </t-form-item>
        <t-form-item label="过期日期" name="expired_date">
          <t-date-picker v-model="formData.expired_date" placeholder="留空则永久有效" />
        </t-form-item>
        <t-form-item label="每日配额" name="daily_quota">
          <t-input-number v-model="formData.daily_quota" :min="0" />
        </t-form-item>
        <t-form-item label="每月配额" name="monthly_quota">
          <t-input-number v-model="formData.monthly_quota" :min="0" />
        </t-form-item>
        <t-form-item label="关联号池" name="gptcar_list">
          <t-select
            v-model="formData.gptcar_list"
            multiple
            :disabled="Boolean(activeSubscription)"
            :placeholder="activeSubscription ? '由套餐配置自动管理' : '请选择传统号池'"
          >
            <t-option v-for="car in carOptions" :key="car.id" :value="car.id" :label="car.car_name" />
          </t-select>
          <template #help>
            <span class="form-help">
              {{ activeSubscription ? `${activeSubscription.plan_name} 用户只能使用该套餐关联的套餐号池` : '套餐账号请到“套餐号池”页面维护' }}
            </span>
          </template>
        </t-form-item>
        <t-form-item label="模型限制" name="model_limit">
          <t-textarea
            v-model="modelLimitInput"
            placeholder="多个模型用逗号或换行分隔，留空表示可使用全部模型"
            :autosize="{ minRows: 3, maxRows: 6 }"
          />
          <template #help>
            <span class="form-help">按上游 Django 后台协议直接提交模型 ID 列表，不再依赖 /0x/models/* 接口</span>
          </template>
        </t-form-item>
        <t-form-item label="备注" name="remark">
          <t-textarea v-model="formData.remark" placeholder="请输入备注" />
        </t-form-item>
      </t-form>
    </t-dialog>

    <t-dialog
      :visible="batchDeviceVisible"
      header="批量设备策略"
      :confirm-btn="{ loading: batchDeviceSubmitting }"
      width="520px"
      @confirm="submitBatchDevicePolicy"
      @close="batchDeviceVisible = false"
    >
      <t-form :data="batchDeviceForm" label-width="100px">
        <t-form-item label="策略来源">
          <t-radio-group v-model="batchDeviceForm.follow_plan">
            <t-radio :value="true">跟随套餐</t-radio>
            <t-radio :value="false">批量覆盖</t-radio>
          </t-radio-group>
        </t-form-item>
        <template v-if="!batchDeviceForm.follow_plan">
          <t-form-item label="允许多设备"><t-switch v-model="batchDeviceForm.multi_device_enabled" /></t-form-item>
          <t-form-item label="设备上限"><t-input-number v-model="batchDeviceForm.device_limit" :min="1" :max="50" :disabled="!batchDeviceForm.multi_device_enabled" /></t-form-item>
        </template>
        <t-form-item label="新设备验证"><t-switch v-model="batchDeviceForm.new_device_verification_enabled" /></t-form-item>
      </t-form>
    </t-dialog>

    <t-dialog
      :visible="deviceVisible"
      :header="`${deviceUser?.username || ''} 的设备`"
      :footer="false"
      width="720px"
      @close="deviceVisible = false"
    >
      <t-loading :loading="deviceLoading">
        <div class="device-summary" v-if="devicePolicy">
          <strong>{{ devicePolicy.enabled ? `允许 ${devicePolicy.limit} 台设备` : '仅允许 1 台设备' }}</strong>
          <span>{{ devicePolicySource(devicePolicy) }}</span>
        </div>
        <div class="device-list">
          <article v-for="device in deviceSessions" :key="device.id" class="device-row">
            <div>
              <strong>{{ deviceLabel(device) }}</strong>
              <span>{{ device.ip_address || '未知 IP' }} · 最近活跃 {{ formatDateTime(device.last_seen_at) }}</span>
            </div>
            <t-space size="small">
              <t-tag v-if="device.is_primary" size="small" variant="light">主设备</t-tag>
              <t-popconfirm content="确定让这台设备退出登录吗？" @confirm="revokeDevice(device)">
                <t-button theme="danger" variant="text" size="small">下线</t-button>
              </t-popconfirm>
            </t-space>
          </article>
          <div v-if="!deviceLoading && !deviceSessions.length" class="mobile-empty">暂无在线设备</div>
        </div>
      </t-loading>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import request from '@/api/request'

const loading = ref(false)
const submitLoading = ref(false)
const dialogVisible = ref(false)
const isEdit = ref(false)
const formRef = ref()
const tableData = ref<any[]>([])
const carOptions = ref<any[]>([])
const modelLimitInput = ref('')
const activeSubscription = ref<any>(null)
const query = ref('')
const statusFilter = ref('')
const selectedRowKeys = ref<Array<number | string>>([])
const batchDeviceVisible = ref(false)
const batchDeviceSubmitting = ref(false)
const deviceVisible = ref(false)
const deviceLoading = ref(false)
const deviceUser = ref<any>(null)
const devicePolicy = ref<any>(null)
const deviceSessions = ref<any[]>([])
const batchDeviceForm = reactive({
  follow_plan: true,
  multi_device_enabled: true,
  device_limit: 3,
  new_device_verification_enabled: true
})

const pagination = reactive({
  current: 1,
  pageSize: 10,
  total: 0
})

const columns = [
  { colKey: 'row-select', type: 'multiple', width: 46 },
  { colKey: 'id', title: 'ID', width: 80 },
  { colKey: 'username', title: '用户名' },
  { colKey: 'email', title: '邮箱与验证', cell: 'email', width: 220 },
  { colKey: 'is_active', title: '状态', cell: 'is_active', width: 80 },
  { colKey: 'model_limit', title: '模型限制', cell: 'model_limit', width: 180 },
  { colKey: 'force_chat_mode', title: 'Work 模式', cell: 'force_chat_mode', width: 110 },
  { colKey: 'device_policy', title: '设备策略', cell: 'device_policy', width: 135 },
  { colKey: 'subscription', title: '套餐', cell: 'subscription', width: 130 },
  { colKey: 'expired_date', title: '过期日期', cell: 'expired_date', width: 120 },
  { colKey: 'remark', title: '备注', ellipsis: true },
  { colKey: 'op', title: '操作', cell: 'op', width: 180 }
]

const formData = reactive({
  id: 0,
  username: '',
  email: '',
  password: '',
  is_active: true,
  isolated_session: true,
  device_policy_managed_by_plan: true,
  multi_device_enabled: true,
  device_limit: 3,
  new_device_verification_enabled: true,
  force_chat_mode: true,
  expired_date: '',
  gptcar_list: [] as number[],
  model_limit: [] as string[],
  remark: '',
  daily_quota: 0,
  monthly_quota: 0
})

const formRules = {
  username: [{ required: true, message: '请输入用户名' }]
}

const normalizedModelLimits = (value: unknown) => {
  if (!Array.isArray(value)) return []
  return [...new Set(
    value
      .filter((item): item is string => typeof item === 'string')
      .map(item => item.trim())
      .filter(item => item && !['[object Object]', 'undefined', 'null'].includes(item))
  )]
}

onMounted(() => {
  fetchData()
  fetchCarOptions()
})

const fetchData = async () => {
  loading.value = true
  const params = new URLSearchParams({
    page: String(pagination.current),
    page_size: String(pagination.pageSize)
  })
  if (query.value.trim()) params.set('q', query.value.trim())
  if (statusFilter.value) params.set('status', statusFilter.value)
  const data = await request(`/0x/user?${params.toString()}`)
  loading.value = false

  if (data) {
    tableData.value = data.results || []
    pagination.total = data.count || 0
  }
}

const fetchCarOptions = async () => {
  const data = await request('/0x/chatgpt/car-enum')
  if (data) {
    carOptions.value = data.data || []
  }
}

const onPageChange = (pageInfo: any) => {
  pagination.current = pageInfo.current
  pagination.pageSize = pageInfo.pageSize
  fetchData()
}

const showAddDialog = () => {
  isEdit.value = false
  Object.assign(formData, {
    id: 0,
    username: '',
    email: '',
    password: '',
    is_active: true,
    isolated_session: true,
    device_policy_managed_by_plan: true,
    multi_device_enabled: true,
    device_limit: 3,
    new_device_verification_enabled: true,
    force_chat_mode: true,
    expired_date: '',
    gptcar_list: [],
    model_limit: [],
    remark: '',
    daily_quota: 0,
    monthly_quota: 0
  })
  modelLimitInput.value = ''
  activeSubscription.value = null
  dialogVisible.value = true
}

const showEditDialog = (row: any) => {
  isEdit.value = true
  Object.assign(formData, {
    id: row.id,
    username: row.username,
    email: row.email || '',
    password: '',
    is_active: row.is_active,
    isolated_session: row.isolated_session ?? true,
    device_policy_managed_by_plan: row.device_policy_managed_by_plan !== false,
    multi_device_enabled: row.multi_device_enabled !== false,
    device_limit: Number(row.device_limit || 3),
    new_device_verification_enabled: row.new_device_verification_enabled !== false,
    force_chat_mode: row.force_chat_mode ?? true,
    expired_date: row.expired_date || '',
    gptcar_list: row.gptcar_list || [],
    model_limit: normalizedModelLimits(row.model_limit),
    remark: row.remark || '',
    daily_quota: Number(row.daily_quota || 0),
    monthly_quota: Number(row.monthly_quota || 0)
  })
  modelLimitInput.value = normalizedModelLimits(row.model_limit).join(', ')
  activeSubscription.value = row.subscription || null
  dialogVisible.value = true
}

const handleSubmit = async () => {
  const valid = await formRef.value?.validate()
  if (valid !== true) return

  submitLoading.value = true
  const modelLimit = modelLimitInput.value
    .split(/[,\n]/)
    .map(item => item.trim())
    .filter(Boolean)

  const url = '/0x/user'
  const method = 'POST'
  const payload = {
    username: formData.username,
    email: formData.email.trim(),
    is_active: formData.is_active,
    isolated_session: formData.isolated_session,
    device_policy_managed_by_plan: formData.device_policy_managed_by_plan,
    multi_device_enabled: formData.multi_device_enabled,
    device_limit: formData.multi_device_enabled ? formData.device_limit : 1,
    new_device_verification_enabled: formData.new_device_verification_enabled,
    force_chat_mode: formData.force_chat_mode,
    gptcar_list: activeSubscription.value ? [] : formData.gptcar_list,
    model_limit: modelLimit,
    remark: formData.remark,
    daily_quota: formData.daily_quota,
    monthly_quota: formData.monthly_quota
  }

  if (formData.password.trim()) {
    Object.assign(payload, { password: formData.password })
  }

  if (formData.expired_date) {
    Object.assign(payload, { expired_date: formData.expired_date })
  }

  const data = await request(url, method, payload)
  submitLoading.value = false

  if (data) {
    MessagePlugin.success(isEdit.value ? '更新成功' : '添加成功')
    dialogVisible.value = false
    fetchData()
  }
}

const handleDelete = async (row: any) => {
  const data = await request('/0x/user', 'DELETE', { username: row.username })
  if (data) {
    MessagePlugin.success('删除成功')
    fetchData()
  }
}

const applyFilters = () => {
  pagination.current = 1
  fetchData()
}

const batchAction = async (action: 'activate' | 'deactivate') => {
  const data = await request('/0x/user/batch', 'POST', {
    user_id_list: selectedRowKeys.value.map(Number),
    action
  })
  if (data) {
    selectedRowKeys.value = []
    MessagePlugin.success(data.message)
    fetchData()
  }
}

const devicePolicySource = (policy: any) => {
  if (policy?.source === 'plan') return policy.plan_name ? `跟随 ${policy.plan_name}` : '跟随套餐'
  if (policy?.source === 'user') return '单独设置'
  return '系统默认'
}

const openBatchDeviceDialog = () => {
  Object.assign(batchDeviceForm, {
    follow_plan: true,
    multi_device_enabled: true,
    device_limit: 3,
    new_device_verification_enabled: true
  })
  batchDeviceVisible.value = true
}

const submitBatchDevicePolicy = async () => {
  batchDeviceSubmitting.value = true
  const data = await request('/0x/user/batch', 'POST', {
    user_id_list: selectedRowKeys.value.map(Number),
    action: batchDeviceForm.follow_plan ? 'device_follow_plan' : 'device_override',
    multi_device_enabled: batchDeviceForm.multi_device_enabled,
    device_limit: batchDeviceForm.multi_device_enabled ? batchDeviceForm.device_limit : 1,
    new_device_verification_enabled: batchDeviceForm.new_device_verification_enabled
  })
  batchDeviceSubmitting.value = false
  if (!data) return
  batchDeviceVisible.value = false
  selectedRowKeys.value = []
  MessagePlugin.success(data.message)
  fetchData()
}

const loadDevices = async () => {
  if (!deviceUser.value) return
  deviceLoading.value = true
  const data = await request(`/0x/user/devices?user_id=${deviceUser.value.id}`)
  deviceLoading.value = false
  if (!data) return
  devicePolicy.value = data.policy || null
  deviceSessions.value = data.sessions || []
}

const openDeviceDialog = async (row: any) => {
  deviceUser.value = row
  devicePolicy.value = row.device_policy || null
  deviceSessions.value = []
  deviceVisible.value = true
  await loadDevices()
}

const revokeDevice = async (device: any) => {
  const data = await request('/0x/user/devices', 'DELETE', {
    user_id: deviceUser.value.id,
    session_id: device.id
  })
  if (!data) return
  MessagePlugin.success(data.message)
  await Promise.all([loadDevices(), fetchData()])
}

const deviceLabel = (device: any) => {
  const type = device.device_type === 'mobile' ? '手机' : device.device_type === 'tablet' ? '平板' : '电脑'
  return `${type} · ${device.browser_name || '其他浏览器'} · ${device.os_name || '其他系统'}`
}

const formatDateTime = (value: string) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
</script>

<style scoped>
.text-gray {
  color: var(--app-text-muted);
}
.email-cell {
  display: grid;
  min-width: 0;
  gap: 4px;
}
.email-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.form-help {
  color: var(--app-text-muted);
  font-size: 12px;
  line-height: 1.6;
}
.subscription-cell strong,
.subscription-cell span {
  display: block;
}
.device-policy-cell, .device-summary, .device-row > div { display: grid; gap: 4px; }
.device-policy-cell strong { font-size: 13px; font-weight: 600; }
.device-policy-cell span, .device-summary span, .device-row span { color: var(--app-text-muted); font-size: 11px; }
.device-summary { margin-bottom: 14px; padding: 12px 14px; background: #f7f7f5; border: 1px solid var(--app-border); border-radius: 6px; }
.device-list { display: grid; gap: 10px; }
.device-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 13px 14px; border: 1px solid var(--app-border); border-radius: 6px; }
.subscription-cell strong {
  font-size: 13px;
  font-weight: 600;
}
.subscription-cell span {
  margin-top: 3px;
  color: var(--app-text-muted);
  font-size: 11px;
}
.table-toolbar {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) 160px auto auto auto auto;
  gap: 10px;
  margin-bottom: 16px;
}
.mobile-user-list { display: none; }
@media (max-width: 760px) {
  .desktop-user-table { display: none; }
  .mobile-user-list { display: grid; gap: 12px; }
  .table-toolbar { grid-template-columns: 1fr; }
  .table-toolbar > .t-button { width: 100%; }
  .mobile-user-card { padding: 16px; background: #fafaf8; border: 1px solid var(--app-border); border-radius: 8px; }
  .mobile-user-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
  .mobile-user-heading strong, .mobile-user-heading span { display: block; }
  .mobile-user-heading strong { overflow-wrap: anywhere; font-size: 16px; }
  .mobile-user-heading span { margin-top: 3px; color: var(--app-text-muted); font-size: 12px; }
  .mobile-user-card dl { display: grid; gap: 9px; margin-top: 14px; }
  .mobile-user-card dl > div { display: grid; grid-template-columns: 58px minmax(0, 1fr); gap: 8px; font-size: 13px; }
  .mobile-user-card dt { color: var(--app-text-muted); }
  .mobile-user-card dd { min-width: 0; overflow-wrap: anywhere; }
  .mobile-user-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 16px; }
  .mobile-user-actions .t-button { width: 100%; min-height: 44px; }
  .mobile-empty { padding: 28px 12px; color: var(--app-text-muted); text-align: center; }
  .mobile-pagination { justify-content: center; padding-top: 4px; }
  .device-row { align-items: flex-start; flex-direction: column; }
}
</style>
