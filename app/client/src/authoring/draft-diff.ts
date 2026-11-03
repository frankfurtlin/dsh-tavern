export interface DraftDifference{path:string;local:string;remote:string}
function display(value:unknown){if(typeof value==='string'&&value.startsWith('data:image/'))return '图片内容';const text=typeof value==='string'?value:JSON.stringify(value)??'未设置';return text.length>260?text.slice(0,260)+'…':text}
export function draftDiff(local:unknown,remote:unknown,path='配置'):DraftDifference[]{
 if(JSON.stringify(local)===JSON.stringify(remote))return []
 if(local&&remote&&typeof local==='object'&&typeof remote==='object'&&!Array.isArray(local)&&!Array.isArray(remote)){
  const a=local as Record<string,unknown>,b=remote as Record<string,unknown>
  return [...new Set([...Object.keys(a),...Object.keys(b)])].flatMap(key=>draftDiff(a[key],b[key],path+' / '+key)).slice(0,100)
 }
 return [{path,local:display(local),remote:display(remote)}]
}
