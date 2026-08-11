import dayjs from 'dayjs'

export const formatDateTime = (value?: string | null) => {
  if (!value) return '-'
  return dayjs(value).format('YYYY-MM-DD HH:mm')
}

export const formatMoney = (cents?: number, currency = 'CNY') => {
  const amount = Number(cents || 0) / 100
  if (currency === 'CNY') return `¥${amount.toFixed(2)}`
  return `${currency} ${amount.toFixed(2)}`
}

export const statusLabel = (status?: string) => {
  const labels: Record<string, string> = {
    PENDING: '待支付',
    PAID: '已支付',
    CLOSED: '已关闭',
    FAILED: '失败',
    REFUNDED: '已退款',
    ACTIVE: '生效中',
    SUSPENDED: '已暂停',
    EXPIRED: '已到期',
    CANCELLED: '已取消'
  }
  return labels[status || ''] || status || '-'
}

export const statusTheme = (status?: string) => {
  if (['PAID', 'ACTIVE', 'HEALTHY', 'SUCCESS'].includes(status || '')) return 'success'
  if (['PENDING', 'DEGRADED'].includes(status || '')) return 'warning'
  if (['FAILED', 'REFUNDED', 'SUSPENDED', 'EXPIRED', 'DISABLED'].includes(status || '')) return 'danger'
  return 'default'
}

export const remainingDays = (endsAt?: string | null) => {
  if (!endsAt) return 0
  return Math.max(dayjs(endsAt).diff(dayjs(), 'day'), 0)
}
