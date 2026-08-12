<template>
  <section class="notification-page">
    <div class="section-heading">
      <div>
        <h2>通知</h2>
        <p>{{ unreadCount }} 条未读消息</p>
      </div>
      <t-button variant="outline" @click="loadData">
        <template #icon><t-icon name="refresh" /></template>
        刷新
      </t-button>
    </div>
    <t-loading :loading="loading">
      <div class="notification-list">
        <article v-for="item in notifications" :key="item.id" :class="['notification-row', { unread: !item.is_read }]">
          <div class="notification-marker"></div>
          <div class="notification-content">
            <div class="notification-title">
              <div>
                <strong>{{ item.title }}</strong>
                <t-tag v-if="item.requires_acknowledgement" theme="warning" variant="light" size="small">需确认</t-tag>
              </div>
              <span>{{ formatDateTime(item.created_at) }}</span>
            </div>
            <p>{{ item.content }}</p>
          </div>
          <t-button v-if="!item.is_read" size="small" variant="text" @click="markRead(item)">确认已读</t-button>
        </article>
        <div v-if="!notifications.length" class="empty-row">暂无通知</div>
      </div>
    </t-loading>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import request from '@/api/request'
import { formatDateTime } from '@/utils/billing'

const loading = ref(false)
const notifications = ref<any[]>([])
const unreadCount = ref(0)

const loadData = async () => {
  loading.value = true
  const data = await request('/0x/billing/notifications?page_size=50')
  notifications.value = data?.results || []
  unreadCount.value = data?.unread_count || 0
  loading.value = false
}

const markRead = async (item: any) => {
  const data = await request(`/0x/billing/notifications/${item.id}/read`, 'POST', {})
  if (!data) return
  item.is_read = true
  unreadCount.value = Math.max(unreadCount.value - 1, 0)
}

onMounted(loadData)
</script>

<style scoped>
.notification-page { padding: 22px 24px; background: var(--app-surface); border: 1px solid var(--app-border); border-radius: 8px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.section-heading h2 { font-size: 18px; font-weight: 600; }
.section-heading p { margin-top: 5px; color: var(--app-text-muted); font-size: 13px; }
.notification-list { border: 1px solid #e7e7e3; border-radius: 7px; }
.notification-row { display: grid; grid-template-columns: 8px minmax(0, 1fr) auto; align-items: start; gap: 14px; padding: 18px; border-bottom: 1px solid #e7e7e3; }
.notification-row:last-child { border-bottom: 0; }
.notification-row.unread { background: #f7faf8; }
.notification-marker { width: 7px; height: 7px; margin-top: 7px; background: #bdbdb8; border-radius: 50%; }
.notification-row.unread .notification-marker { background: var(--app-success); }
.notification-title { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.notification-title strong { font-size: 14px; font-weight: 600; }
.notification-title > div { display: flex; align-items: center; gap: 8px; }
.notification-title span { color: var(--app-text-muted); font-size: 12px; white-space: nowrap; }
.notification-content p { margin-top: 8px; color: #555550; font-size: 13px; line-height: 1.7; white-space: pre-wrap; }
.empty-row { padding: 34px; color: var(--app-text-muted); font-size: 13px; text-align: center; }
@media (max-width: 620px) { .notification-page { padding: 18px 14px; } .notification-row { grid-template-columns: 8px minmax(0, 1fr); } .notification-row > .t-button { grid-column: 2; justify-self: start; } .notification-title { align-items: flex-start; flex-direction: column; gap: 4px; } }
</style>
