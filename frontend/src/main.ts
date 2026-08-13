import { createApp } from 'vue'
import { createPinia } from 'pinia'
import TDesign from 'tdesign-vue-next'
import 'tdesign-vue-next/es/style/index.css'

import App from './App.vue'
import LocalIcon from './components/LocalIcon'
import router from './router'

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(TDesign)
app.component('LocalIcon', LocalIcon)

app.mount('#app')
