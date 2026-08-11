<template>
  <div class="admin-page">
    <section class="admin-section">
      <div class="section-heading">
        <div>
          <h2>售后支持</h2>
          <p>维护用户后台展示的售后方式、联系方式和二维码</p>
        </div>
        <t-button theme="primary" @click="openDialog()">
          <template #icon><t-icon name="add" /></template>
          新增联系方式
        </t-button>
      </div>

      <t-loading :loading="loading">
        <t-table :data="contacts" :columns="columns" row-key="id">
          <template #contact="{ row }">
            <span class="contact-value">{{ row.contact }}</span>
          </template>
          <template #qr="{ row }">
            <t-tag :theme="row.qr_image ? 'success' : 'default'" variant="light">
              {{ row.qr_image ? '已上传' : '未上传' }}
            </t-tag>
          </template>
          <template #status="{ row }">
            <t-tag :theme="row.is_active ? 'success' : 'default'" variant="light">
              {{ row.is_active ? '用户可见' : '已隐藏' }}
            </t-tag>
          </template>
          <template #op="{ row }">
            <t-space size="small">
              <t-link theme="primary" @click="openDialog(row)">编辑</t-link>
              <t-popconfirm content="删除后用户将无法再看到此联系方式" @confirm="removeContact(row)">
                <t-link theme="danger">删除</t-link>
              </t-popconfirm>
            </t-space>
          </template>
        </t-table>
      </t-loading>
    </section>

    <t-dialog
      :visible="dialogVisible"
      :header="form.id ? '编辑售后联系方式' : '新增售后联系方式'"
      :confirm-btn="{ loading: submitting }"
      width="600px"
      @confirm="saveContact"
      @close="dialogVisible = false"
    >
      <t-form :data="form" label-width="100px">
        <t-form-item label="显示名称">
          <t-input v-model="form.name" placeholder="例如：售后客服" />
        </t-form-item>
        <t-form-item label="联系方式类型">
          <t-input v-model="form.channel" placeholder="例如：微信、QQ群、邮箱" />
        </t-form-item>
        <t-form-item label="联系方式">
          <t-input v-model="form.contact" placeholder="填写微信号、群号、邮箱或链接说明" />
        </t-form-item>
        <t-form-item label="补充说明">
          <t-textarea v-model="form.description" :autosize="{ minRows: 2, maxRows: 4 }" placeholder="例如：工作日 10:00-22:00 回复" />
        </t-form-item>
        <t-form-item label="售后二维码">
          <div class="qr-field">
            <input ref="qrFileInput" class="file-input" type="file" accept="image/png,image/jpeg,image/webp" @change="onQrFileChange" />
            <div class="qr-actions">
              <t-button variant="outline" @click="selectQrFile">
                <template #icon><t-icon name="upload" /></template>
                选择二维码
              </t-button>
              <t-button v-if="form.qr_image" variant="text" theme="danger" @click="clearQrImage">移除</t-button>
            </div>
            <p>仅支持 PNG、JPEG 或 WebP，文件不超过 512 KB。</p>
            <img v-if="form.qr_image" class="qr-preview" :src="form.qr_image" alt="售后二维码预览" />
          </div>
        </t-form-item>
        <t-form-item label="用户可见"><t-switch v-model="form.is_active" /></t-form-item>
        <t-form-item label="展示排序"><t-input-number v-model="form.sort_order" :min="0" /></t-form-item>
      </t-form>
    </t-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import request from '@/api/request'

const loading = ref(false)
const submitting = ref(false)
const dialogVisible = ref(false)
const contacts = ref<any[]>([])
const qrFileInput = ref<HTMLInputElement | null>(null)

const columns = [
  { colKey: 'name', title: '名称', width: 150 },
  { colKey: 'channel', title: '方式', width: 130 },
  { colKey: 'contact', title: '联系方式', cell: 'contact', minWidth: 220 },
  { colKey: 'qr', title: '二维码', cell: 'qr', width: 100 },
  { colKey: 'sort_order', title: '排序', width: 80 },
  { colKey: 'status', title: '状态', cell: 'status', width: 100 },
  { colKey: 'op', title: '操作', cell: 'op', width: 120 }
]

const form = reactive<any>({
  id: 0,
  name: '',
  channel: '',
  contact: '',
  description: '',
  qr_image: '',
  is_active: true,
  sort_order: 0
})

const resetForm = () => {
  Object.assign(form, {
    id: 0,
    name: '',
    channel: '',
    contact: '',
    description: '',
    qr_image: '',
    is_active: true,
    sort_order: 0
  })
}

const loadData = async () => {
  loading.value = true
  try {
    const data = await request('/0x/admin/support-contacts')
    contacts.value = data?.contacts || []
  } finally {
    loading.value = false
  }
}

const openDialog = (row?: any) => {
  if (row) {
    Object.assign(form, {
      id: row.id,
      name: row.name,
      channel: row.channel,
      contact: row.contact,
      description: row.description,
      qr_image: row.qr_image,
      is_active: row.is_active,
      sort_order: row.sort_order
    })
  } else {
    resetForm()
  }
  dialogVisible.value = true
}

const selectQrFile = () => qrFileInput.value?.click()

const clearQrImage = () => {
  form.qr_image = ''
  if (qrFileInput.value) qrFileInput.value.value = ''
}

const onQrFileChange = (event: Event) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  const acceptedTypes = ['image/png', 'image/jpeg', 'image/webp']
  if (!acceptedTypes.includes(file.type)) {
    MessagePlugin.error('二维码仅支持 PNG、JPEG 或 WebP 图片')
    return
  }
  if (file.size > 512 * 1024) {
    MessagePlugin.error('二维码图片不能超过 512 KB')
    return
  }

  const reader = new FileReader()
  reader.onload = () => {
    form.qr_image = String(reader.result || '')
  }
  reader.onerror = () => MessagePlugin.error('二维码读取失败，请重新选择')
  reader.readAsDataURL(file)
}

const saveContact = async () => {
  submitting.value = true
  try {
    const data = await request('/0x/admin/support-contacts', 'POST', { action: 'save', ...form })
    if (!data) return
    dialogVisible.value = false
    MessagePlugin.success('售后联系方式已保存')
    await loadData()
  } finally {
    submitting.value = false
  }
}

const removeContact = async (row: any) => {
  const data = await request('/0x/admin/support-contacts', 'POST', { action: 'delete', id: row.id })
  if (!data) return
  MessagePlugin.success('售后联系方式已删除')
  await loadData()
}

onMounted(loadData)
</script>

<style scoped>
.admin-page { display: grid; gap: 20px; }
.admin-section { min-width: 0; padding: 22px 24px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.section-heading h2 { margin: 0; font-size: 18px; font-weight: 600; }
.section-heading p { margin-top: 5px; color: var(--app-text-muted); font-size: 13px; }
.contact-value { overflow-wrap: anywhere; }
.file-input { display: none; }
.qr-field { display: grid; gap: 10px; width: 100%; }
.qr-actions { display: flex; align-items: center; gap: 8px; }
.qr-field p { color: var(--app-text-muted); font-size: 12px; line-height: 1.5; }
.qr-preview { display: block; width: 144px; height: 144px; object-fit: contain; background: #fff; border: 1px solid var(--app-border); border-radius: 6px; }
@media (max-width: 720px) { .admin-section { padding: 18px 14px; } .section-heading { align-items: flex-start; flex-direction: column; } }
</style>
