import { defineComponent, h, type Component } from 'vue'
import AddIcon from 'tdesign-icons-vue-next/esm/components/add.js'
import CartIcon from 'tdesign-icons-vue-next/esm/components/cart.js'
import ChevronDownIcon from 'tdesign-icons-vue-next/esm/components/chevron-down.js'
import CloseIcon from 'tdesign-icons-vue-next/esm/components/close.js'
import CodeIcon from 'tdesign-icons-vue-next/esm/components/code.js'
import DashboardIcon from 'tdesign-icons-vue-next/esm/components/dashboard.js'
import FileIcon from 'tdesign-icons-vue-next/esm/components/file.js'
import HistoryIcon from 'tdesign-icons-vue-next/esm/components/history.js'
import InternetIcon from 'tdesign-icons-vue-next/esm/components/internet.js'
import LayersIcon from 'tdesign-icons-vue-next/esm/components/layers.js'
import MailIcon from 'tdesign-icons-vue-next/esm/components/mail.js'
import MenuIcon from 'tdesign-icons-vue-next/esm/components/menu.js'
import MoneyIcon from 'tdesign-icons-vue-next/esm/components/money.js'
import NotificationIcon from 'tdesign-icons-vue-next/esm/components/notification.js'
import OrderAscendingIcon from 'tdesign-icons-vue-next/esm/components/order-ascending.js'
import PlayCircleIcon from 'tdesign-icons-vue-next/esm/components/play-circle.js'
import RefreshIcon from 'tdesign-icons-vue-next/esm/components/refresh.js'
import RootListIcon from 'tdesign-icons-vue-next/esm/components/root-list.js'
import SearchIcon from 'tdesign-icons-vue-next/esm/components/search.js'
import SecuredIcon from 'tdesign-icons-vue-next/esm/components/secured.js'
import ServerIcon from 'tdesign-icons-vue-next/esm/components/server.js'
import ServiceIcon from 'tdesign-icons-vue-next/esm/components/service.js'
import TicketIcon from 'tdesign-icons-vue-next/esm/components/ticket.js'
import UploadIcon from 'tdesign-icons-vue-next/esm/components/upload.js'
import UserCircleIcon from 'tdesign-icons-vue-next/esm/components/user-circle.js'
import UserIcon from 'tdesign-icons-vue-next/esm/components/user.js'
import UsergroupIcon from 'tdesign-icons-vue-next/esm/components/usergroup.js'
import WalletIcon from 'tdesign-icons-vue-next/esm/components/wallet.js'

const icons: Record<string, Component> = {
  add: AddIcon,
  cart: CartIcon,
  'chevron-down': ChevronDownIcon,
  close: CloseIcon,
  code: CodeIcon,
  dashboard: DashboardIcon,
  file: FileIcon,
  history: HistoryIcon,
  internet: InternetIcon,
  layers: LayersIcon,
  mail: MailIcon,
  menu: MenuIcon,
  'money-circle': MoneyIcon,
  notification: NotificationIcon,
  'order-ascending': OrderAscendingIcon,
  'play-circle': PlayCircleIcon,
  refresh: RefreshIcon,
  'root-list': RootListIcon,
  search: SearchIcon,
  secured: SecuredIcon,
  server: ServerIcon,
  service: ServiceIcon,
  ticket: TicketIcon,
  upload: UploadIcon,
  'user-circle': UserCircleIcon,
  user: UserIcon,
  usergroup: UsergroupIcon,
  wallet: WalletIcon
}

export default defineComponent({
  name: 'LocalIcon',
  inheritAttrs: false,
  props: {
    name: {
      type: String,
      default: ''
    }
  },
  setup(props, { attrs }) {
    return () => h(icons[props.name] || DashboardIcon, attrs)
  }
})
