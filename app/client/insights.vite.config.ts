import {defineConfig} from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({plugins:[vue(),{name:'insights-entry',configureServer(server){
  server.middlewares.use((req,_res,next)=>{if(req.url==='/'||req.url?.startsWith('/?'))req.url='/insights.html';next()})
}}],server:{host:'127.0.0.1',port:18869,strictPort:true,proxy:{'/api':process.env.DEV_API_TARGET||'http://127.0.0.1:18867'}}})
