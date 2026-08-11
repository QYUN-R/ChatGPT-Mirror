<template>
  <section class="support-page">
    <div class="section-heading">
      <div>
        <h2>售后支持</h2>
        <p>服务使用、续费和异常问题可通过以下方式联系</p>
      </div>
      <t-button variant="outline" @click="loadData">
        <template #icon><t-icon name="refresh" /></template>
        刷新
      </t-button>
    </div>

    <t-loading :loading="loading">
      <div v-if="contacts.length" class="contact-grid">
        <article v-for="contact in contacts" :key="contact.id" class="contact-card">
          <div class="contact-heading">
            <div>
              <h3>{{ contact.name }}</h3>
              <span>{{ contact.channel }}</span>
            </div>
            <t-button v-if="contact.qr_image" size="small" variant="outline" @click="openQrDialog(contact)">
              查看二维码
            </t-button>
          </div>
          <strong class="contact-value">{{ contact.contact }}</strong>
          <p v-if="contact.description">{{ contact.description }}</p>
        </article>
      </div>
      <div v-else class="empty-state">
        暂未配置售后联系方式，请通过站内通知关注服务动态。
      </div>
    </t-loading>

    <t-dialog :visible="qrDialogVisible" :header="selectedContact?.name || '售后二维码'" width="360px" :footer="false" @close="qrDialogVisible = false">
      <div v-if="selectedContact" class="qr-dialog-content">
        <img :src="selectedContact.qr_image" :alt="`${selectedContact.name} 二维码`" />
        <strong>{{ selectedContact.contact }}</strong>
        <span>{{ selectedContact.channel }}</span>
      </div>
    </t-dialog>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import request from '@/api/request'

const loading = ref(false)
const contacts = ref<any[]>([])
const selectedContact = ref<any>(null)
const qrDialogVisible = ref(false)

const loadData = async () => {
  loading.value = true
  try {
    const data = await request('/0x/billing/support-contacts')
    contacts.value = data?.contacts || []
  } finally {
    loading.value = false
  }
}

const openQrDialog = (contact: any) => {
  selectedContact.value = contact
  qrDialogVisible.value = true
}

onMounted(loadData)
</script>

<style scoped>
.support-page { min-width: 0; padding: 22px 24px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.section-heading h2 { margin: 0; font-size: 18px; font-weight: 600; }
.section-heading p { margin-top: 5px; color: var(--app-text-muted); font-size: 13px; line-height: 1.5; }
.contact-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }
.contact-card { display: grid; gap: 14px; min-width: 0; padding: 18px; background: #f7f7f5; border: 1px solid #e3e3df; border-radius: 7px; }
.contact-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.contact-heading h3 { margin: 0; font-size: 16px; font-weight: 600; }
.contact-heading span { display: block; margin-top: 5px; color: var(--app-text-muted); font-size: 12px; }
.contact-value { overflow-wrap: anywhere; color: var(--app-text); font-size: 15px; font-weight: 600; }
.contact-card p { color: var(--app-text-muted); font-size: 13px; line-height: 1.6; }
.empty-state { padding: 30px 18px; color: var(--app-text-muted); font-size: 13px; text-align: center; background: #f7f7f5; border: 1px dashed var(--app-border-strong); border-radius: 7px; }
.qr-dialog-content { display: grid; justify-items: center; gap: 10px; padding: 4px 0 12px; text-align: center; }
.qr-dialog-content img { width: min(240px, 100%); aspect-ratio: 1; object-fit: contain; background: #fff; border: 1px solid var(--app-border); border-radius: 6px; }
.qr-dialog-content strong { overflow-wrap: anywhere; font-size: 15px; font-weight: 600; }
.qr-dialog-content span { color: var(--app-text-muted); font-size: 12px; }
@media (max-width: 620px) { .support-page { padding: 18px 14px; } .section-heading { align-items: flex-start; flex-direction: column; } }
</style>
