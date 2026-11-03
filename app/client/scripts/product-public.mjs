import {writeFileSync} from 'node:fs'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {loadEnv} from 'vite'
import {generateSite} from './foundation-content.mjs'
import {sitemapXml} from '../src/foundation/vendor/seo.js'
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),dist=path.join(root,'dist')
const configured=process.env.VITE_PUBLIC_ORIGIN||loadEnv('production',process.cwd(),'VITE_').VITE_PUBLIC_ORIGIN||''
let origin='http://127.0.0.1:18868'
if(configured){const url=new URL(configured);if(url.protocol!=='https:'||url.pathname!=='/'||url.search||url.hash||url.username||url.password)throw new Error('VITE_PUBLIC_ORIGIN must be an HTTPS origin');origin=url.origin}
await generateSite({input:path.join(root,'content'),output:path.join(dist,'guide'),origin,siteName:'故事之间',basePath:'/guide',indexable:!!configured})
const publicPaths=['/help','/cases/blue-carbuncle','/guide/','/guide/blue-carbuncle/']
writeFileSync(path.join(dist,'sitemap.xml'),sitemapXml(configured?publicPaths.map(route=>origin+route):[]))
writeFileSync(path.join(dist,'robots.txt'),configured?`User-agent: *\nDisallow: /\nAllow: /help\nAllow: /cases/blue-carbuncle\nAllow: /guide/\nDisallow: /worlds\nDisallow: /books\nDisallow: /local\nDisallow: /product\nDisallow: /settings\nDisallow: /api/\nSitemap: ${origin}/sitemap.xml\n`:'User-agent: *\nDisallow: /\n')
console.log('Public help export ready; indexable:',!!configured)
