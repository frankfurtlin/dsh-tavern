import {readdirSync,readFileSync,writeFileSync} from 'node:fs'
import path from 'node:path'
import {createHash} from 'node:crypto'
import {loadEnv} from 'vite'
function files(dir){return readdirSync(dir,{withFileTypes:true}).flatMap(entry=>entry.isDirectory()?files(path.join(dir,entry.name)):[path.join(dir,entry.name)])}
// Canonical order makes the shell version and asset manifest identical on
// Windows and Linux when the bundled files are identical.
const dist=process.argv[2]||'dist'
const all=files(dist).map(file=>file.replaceAll('\\','/')).filter(file=>!file.endsWith('sw.js')).sort()
const hash=createHash('sha256');all.forEach(file=>hash.update(readFileSync(file)))
const paths=all.map(file=>'/'+path.relative(dist,file).replaceAll('\\','/'))
// Cache large original display files on demand. Preload the established small demo pair.
const optional=paths.filter(file=>file.startsWith('/assets/original/')&&/\.(webp|png|jpe?g|avif)$/.test(file)&&!['/assets/original/final-product/fog-harbor-cover-400.webp','/assets/original/final-product/harbor-dawn-1280.webp'].includes(file))
const assets=paths.filter(file=>!optional.includes(file))
const entry=readFileSync(path.join(dist,'index.html'),'utf8')
const coreAssets=[...new Set(['/index.html',...Array.from(entry.matchAll(/(?:src|href)="([^\"]+)"/g),match=>match[1]).filter(file=>assets.includes(file))])]
const authCode=readFileSync('scripts/service-worker-auth.js','utf8')
const shellCode=readFileSync('scripts/service-worker-shell.js','utf8')
const apiBase=process.env.VITE_API_BASE_URL||loadEnv('production',process.cwd(),'VITE_').VITE_API_BASE_URL||''
const authOrigin=apiBase?new URL(apiBase).origin:''
hash.update(authCode);hash.update(authOrigin);hash.update(shellCode)
const code=`const AUTH_BACKEND_ORIGIN=${JSON.stringify(authOrigin)};\n${authCode}\nconst VERSION='reader-shell-${hash.digest('hex').slice(0,16)}';const ASSETS=${JSON.stringify(assets)};const CORE_ASSETS=${JSON.stringify(coreAssets)};const OPTIONAL_ASSETS=${JSON.stringify(optional)};
${shellCode}`
writeFileSync(path.join(dist,'sw.js'),code)
