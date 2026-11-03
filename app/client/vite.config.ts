import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
export default defineConfig({base:process.env.APP_BASE||'/',plugins:[vue()],build:{rollupOptions:{input:{app:'index.html',insights:'insights.html'}}},server:{proxy:{'/api': process.env.DEV_API_TARGET || 'http://127.0.0.1:8810'}},test:{include:['src/**/*.test.ts']}})
