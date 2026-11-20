/**
 * 聊天正文的轻量格式解析与按天/发言人的行分组。
 * 纯文本渲染：只处理首尾对称的 `**强调**` 与单行 `*动作*`，其余原样输出（不做 Markdown 全量解析）。
 */
export type ChatSegmentKind='text'|'act'|'strong'
export interface ChatSegment{kind:ChatSegmentKind;text:string}

const ACT_LIMIT=200

function pushText(out:ChatSegment[],text:string):void{
  if(!text)return
  const last=out[out.length-1]
  if(last&&last.kind==='text')last.text+=text
  else out.push({kind:'text',text})
}

/** 把一段消息文本切成 text / act / strong 片段；未闭合、跨行或超长标记按普通文本处理。 */
export function splitSegments(input:string):ChatSegment[]{
  const text=typeof input==='string'?input:''
  if(!text)return []
  const out:ChatSegment[]=[]
  let buffer=''
  let index=0
  while(index<text.length){
    if(text.startsWith('**',index)){
      const end=text.indexOf('**',index+2)
      const content=end>index+2?text.slice(index+2,end):null
      if(content!==null&&!content.includes('\n')&&content.trim()){
        pushText(out,buffer);buffer=''
        out.push({kind:'strong',text:content})
        index=end+2
        continue
      }
      buffer+='**'
      index+=2
      continue
    }
    if(text[index]==='*'){
      const end=text.indexOf('*',index+1)
      const content=end>index+1?text.slice(index+1,end):null
      if(content!==null&&!content.includes('\n')&&content.length<=ACT_LIMIT&&content.trim()){
        pushText(out,buffer);buffer=''
        out.push({kind:'act',text:content})
        index=end+1
        continue
      }
      buffer+='*'
      index+=1
      continue
    }
    buffer+=text[index]
    index+=1
  }
  pushText(out,buffer)
  return out
}

export interface ChatRowMessage{
  id:string
  role:'user'|'assistant'|string
  createdAt?:string|null
  speakerCardId?:string|null
  speakerName?:string|null
}

export interface ChatDayRow{kind:'day';key:string;label:string}
export interface ChatMessageRow<T extends ChatRowMessage=ChatRowMessage>{kind:'message';key:string;message:T;showSpeaker:boolean;compact:boolean;time:string|null}
export type ChatRow<T extends ChatRowMessage=ChatRowMessage>=ChatDayRow|ChatMessageRow<T>

function pad(value:number):string{return value<10?'0'+value:String(value)}

/** 本地日期键 YYYY-MM-DD；无效/缺失返回 null。 */
export function dateKeyOf(value?:string|null):string|null{
  if(!value)return null
  const date=new Date(value)
  if(Number.isNaN(date.getTime()))return null
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}`
}

export function dayLabel(key:string,now:Date=new Date()):string{
  const [year,month,day]=key.split('-').map(Number)
  const todayKey=dateKeyOf(now.toISOString())
  const yesterday=new Date(now.getFullYear(),now.getMonth(),now.getDate()-1)
  const yesterdayKey=dateKeyOf(yesterday.toISOString())
  if(key===todayKey)return '今天'
  if(key===yesterdayKey)return '昨天'
  return year===now.getFullYear()?`${month}月${day}日`:`${year}年${month}月${day}日`
}

export function clockOf(value?:string|null):string|null{
  if(!value)return null
  const date=new Date(value)
  if(Number.isNaN(date.getTime()))return null
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export interface BuildChatRowsOptions{isGroup?:boolean;now?:Date}

/**
 * 按本地日期切分消息，插入日期行；同日同一发言人的连续消息标记 compact（用于收窄间距）。
 * createdAt 缺失时归入当前分组（首条缺失则归今天）。
 */
export function buildChatRows<T extends ChatRowMessage>(messages:readonly T[],options:BuildChatRowsOptions={}):ChatRow<T>[]{
  const isGroup=!!options.isGroup
  const now=options.now??new Date()
  const todayKey=dateKeyOf(now.toISOString())
  const rows:ChatRow<T>[]=[]
  let currentDay:string|null=null
  let previous:T|null=null
  for(const message of messages){
    const sourceDay:string=dateKeyOf(message.createdAt)??currentDay??todayKey??'today'
    if(sourceDay!==currentDay){
      currentDay=sourceDay
      previous=null
      rows.push({kind:'day',key:'day:'+sourceDay,label:dayLabel(sourceDay,now)})
    }
    const isAssistant=message.role==='assistant'
    const speaker=isAssistant?(message.speakerCardId??message.speakerName??'assistant'):'user'
    const sameSpeaker=!!previous&&(()=>{
      const prevAssistant=previous.role==='assistant'
      if(isAssistant!==prevAssistant)return false
      if(!isAssistant)return true
      return (previous.speakerCardId??previous.speakerName??'assistant')===(message.speakerCardId??message.speakerName??'assistant')
    })()
    rows.push({
      kind:'message',
      key:'message:'+message.id,
      message,
      showSpeaker:isGroup&&isAssistant&&(!previous||!sameSpeaker),
      compact:sameSpeaker,
      time:clockOf(message.createdAt),
    })
    previous=message
  }
  return rows
}
