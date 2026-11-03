export function seoMetadata({ origin, path='/', title, description, language='zh-CN', indexable=false, alternates={} }) {
  const url=new URL(path,origin)
  if(!['http:','https:'].includes(url.protocol)||url.origin!==new URL(origin).origin)throw new Error('Invalid canonical URL')
  url.search='';url.hash=''
  return {title,description,canonical:url.href,language,robots:indexable?'index,follow':'noindex,nofollow',alternates,
    openGraph:{title,description,url:url.href,type:'website'}}
}
export function applySeo(metadata, documentImpl=globalThis.document) {
  if(!documentImpl)return
  documentImpl.title=metadata.title;documentImpl.documentElement.lang=metadata.language
  for(const [name,content] of Object.entries({description:metadata.description,robots:metadata.robots})){
    let element=documentImpl.head.querySelector(`meta[name="${name}"]`);if(!element){element=documentImpl.createElement('meta');element.name=name;documentImpl.head.append(element)}element.content=content||''
  }
  let canonical=documentImpl.head.querySelector('link[rel="canonical"]');if(!canonical){canonical=documentImpl.createElement('link');canonical.rel='canonical';documentImpl.head.append(canonical)}canonical.href=metadata.canonical
}
export function sitemapXml(urls) {
  const esc=value=>String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[char]))
  return `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls.map(url=>`<url><loc>${esc(url)}</loc></url>`).join('')}</urlset>`
}
