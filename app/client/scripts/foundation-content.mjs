import {Marked} from 'marked'
import {readdir,readFile,writeFile,mkdir} from 'node:fs/promises'
import {resolve,relative,extname,join} from 'node:path'
import {fileURLToPath} from 'node:url'
import {sitemapXml} from '../src/foundation/vendor/seo.js'
const esc=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]))
const parser=new Marked({renderer:{html:({text})=>esc(text)},walkTokens:token=>{
  if(['link','image'].includes(token.type)&&!(/^(https?:\/\/|mailto:|\/|#|\.?\.?\/)/i.test(token.href)||!token.href.includes(':')))token.href='#'
}})
export function renderMarkdown(source){return parser.parse(source)}
export async function generateSite({input,output,origin='https://example.com',siteName='Product documentation',basePath='',indexable=false}){
  input=resolve(input);output=resolve(output)
  if(output===input||!relative(input,output).startsWith('..'))throw new Error('Output must be outside the input directory')
  const parsed=new URL(origin);if(!['https:','http:'].includes(parsed.protocol))throw new Error('Invalid site origin')
  const pages=[]
  async function visit(dir){for(const entry of await readdir(dir,{withFileTypes:true})){const path=join(dir,entry.name);if(entry.isDirectory())await visit(path);else if(entry.isFile()&&extname(path)==='.md')pages.push(path)}}
  await visit(input);const urls=[]
  for(const path of pages){
    const source=await readFile(path,'utf8'),slug=relative(input,path).replaceAll('\\','/').replace(/\.md$/,'')
    const route=basePath.replace(/\/$/,'')+(slug==='index'?'/':`/${slug}/`),url=new URL(route,origin).href,title=source.match(/^#\s+(.+)$/m)?.[1]||siteName
    const html=`<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(title)} · ${esc(siteName)}</title><link rel="canonical" href="${esc(url)}"><meta name="robots" content="${indexable?'index,follow':'noindex,nofollow'}"><style>body{font:18px/1.7 system-ui;max-width:850px;margin:60px auto;padding:0 24px;color:#263044}pre{padding:16px;background:#f1f4f8;overflow:auto}a{color:#315b9e}</style></head><body><nav><a href="/welcome">${esc(siteName)}</a></nav><main>${renderMarkdown(source)}</main></body></html>`
    const target=join(output,slug==='index'?'':slug);await mkdir(target,{recursive:true});await writeFile(join(target,'index.html'),html)
    urls.push(url)
  }
  await mkdir(output,{recursive:true});await writeFile(join(output,'sitemap.xml'),sitemapXml(indexable?urls:[]));await writeFile(join(output,'robots.txt'),indexable?`User-agent: *\nAllow: ${basePath||'/'}\nSitemap: ${new URL(basePath+'/sitemap.xml',origin).href}\n`:'User-agent: *\nDisallow: /\n')
  return {pages:pages.length,output}
}
if(process.argv[1]===fileURLToPath(import.meta.url))console.log(await generateSite({input:fileURLToPath(new URL('../content/',import.meta.url)),output:fileURLToPath(new URL('../dist/',import.meta.url))}))
