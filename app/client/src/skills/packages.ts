import {canonical,ContractError} from '../domain/local-contract'
import {readBoundedZip,writeStoredZip} from '../repositories/bounded-zip'
import {validateSkillManifest,type SkillManifest} from './contract'
const encoder=new TextEncoder(),decoder=new TextDecoder('utf-8',{fatal:true})
export async function importSkillPackage(bytes:Uint8Array,signal:AbortSignal):Promise<SkillManifest>{
 if(bytes.length>512*1024)throw new ContractError('SKILL_PACKAGE_LIMIT','技能包限 512 KiB')
 let value:unknown
 try{if(bytes[0]===0x50&&bytes[1]===0x4b){const files=await readBoundedZip(bytes,signal,512*1024);if(files.size!==2||!files.has('manifest.json')||!files.has('SKILL.md'))throw new ContractError('SKILL_PACKAGE_FILES','技能包只接受 manifest.json 和 SKILL.md；脚本不执行');value=JSON.parse(decoder.decode(files.get('manifest.json')!));if(!value||typeof value!=='object'||Array.isArray(value))throw Error();value={...value,skillMarkdown:decoder.decode(files.get('SKILL.md')!)}}else value=JSON.parse(decoder.decode(bytes))}catch(e){if(e instanceof ContractError)throw e;throw new ContractError('SKILL_PACKAGE_INVALID','技能包编码或 JSON 无效')}
 validateSkillManifest(value);scanSkill(value);signal.throwIfAborted();return value
}
export function scanSkill(value:SkillManifest){validateSkillManifest(value);const allowed=['format','id','name','version','description','operation','capabilities','inputSchema','outputSchema','skillMarkdown'];if(Object.keys(value).some(key=>!allowed.includes(key)))throw new ContractError('SKILL_PACKAGE_FIELD','技能含不受支持的扩展字段；没有安装或执行');if(/[\u0000\u202a-\u202e\u2066-\u2069]/.test(value.skillMarkdown))throw new ContractError('SKILL_PACKAGE_TEXT','技能说明含控制字符')}
export function exportSkillPackage(value:SkillManifest){scanSkill(value);const {skillMarkdown,...manifest}=value;return writeStoredZip(new Map([['manifest.json',encoder.encode(canonical(manifest))],['SKILL.md',encoder.encode(skillMarkdown)]]),512*1024)}
/** A public raw repository folder: two static files, no credentials or executable package content. */
export async function importSkillRepository(folder:string,signal:AbortSignal,transport:typeof fetch=fetch):Promise<SkillManifest>{
 let url:URL;try{url=new URL(folder)}catch{throw new ContractError('SKILL_REPOSITORY','请输入公开仓库的 raw 目录地址')}
 if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash)throw new ContractError('SKILL_REPOSITORY','仓库目录需为不含凭据的 HTTPS 地址')
 if(!url.pathname.endsWith('/'))url.pathname+='/'
 const timer=new AbortController(),abort=()=>timer.abort();signal.addEventListener('abort',abort,{once:true});const timeout=setTimeout(abort,15000);let used=0
 const read=async(name:string)=>{signal.throwIfAborted();const response=await transport(new URL(name,url),{signal:timer.signal,credentials:'omit',referrerPolicy:'no-referrer',redirect:'error'});if(!response.ok||!response.body)throw new ContractError('SKILL_REPOSITORY','未读取到 '+name+'；没有安装技能');const reader=response.body.getReader(),chunks:Uint8Array[]=[];try{while(true){const part=await reader.read();signal.throwIfAborted();if(part.done)break;used+=part.value.length;if(used>512*1024)throw new ContractError('SKILL_PACKAGE_LIMIT','仓库技能包限 512 KiB');chunks.push(part.value)}const bytes=new Uint8Array(chunks.reduce((n,c)=>n+c.length,0));let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length}return decoder.decode(bytes)}finally{await reader.cancel().catch(()=>{});reader.releaseLock()}}
 try{const manifest=JSON.parse(await read('manifest.json')) as Record<string,unknown>,skillMarkdown=await read('SKILL.md'),value={...manifest,skillMarkdown};scanSkill(value as SkillManifest);signal.throwIfAborted();return value as SkillManifest}catch(error){if(error instanceof ContractError||signal.aborted)throw error;throw new ContractError('SKILL_REPOSITORY','仓库读取或格式校验失败；没有安装技能')}finally{clearTimeout(timeout);signal.removeEventListener('abort',abort)}
}
