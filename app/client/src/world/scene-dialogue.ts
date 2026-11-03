export interface DialogueSegment {kind:'speech'|'narration';text:string;speakerId?:string;speakerName?:string}

function channelFrames(text:string):string[]{
  const frames:string[]=[];let start=0,depth=0,quoted=false,escaped=false
  for(let i=0;i<text.length;i++){
    const c=text[i]!
    if(depth===0){if(/\s/.test(c)){start=i+1;continue}if(c==='\\'&&['n','r'].includes(text[i+1]||'')){i++;start=i+1;continue}if(c!=='{'){frames.push(text.slice(i));return frames}start=i}
    if(quoted){if(escaped)escaped=false;else if(c==='\\')escaped=true;else if(c==='"')quoted=false}
    else if(c==='"')quoted=true
    else if(c==='{')depth++
    else if(c==='}'){
      depth--;if(depth===0){frames.push(text.slice(start,i+1));start=i+1}
    }
  }
  if(depth>0)frames.push(text.slice(start))
  return frames
}

function channelObject(line:string):{kind?:string;text?:string;speakerId?:string}{
  try{return JSON.parse(line)}catch(error){
    const match=line.match(/^\s*\{\s*"kind"\s*:\s*"(narration|speech)"\s*,\s*(?:"speakerId"\s*:\s*"([^"\\]*)"\s*,\s*)?"text"\s*:\s*"(.*)"\s*}\s*$/)
    if(!match||/"\s*,\s*"[^"\n]+"\s*:/.test(match[3]!))throw error
    let escaped=false,text=''
    for(const char of match[3]!){text+=char==='"'&&!escaped?'\\"':char;escaped=char==='\\'?!escaped:false}
    return {kind:match[1],speakerId:match[2],text:JSON.parse('"'+text+'"')}
  }
}

// Compatibility for saved responses from the old mixed narrator/chat prompt.
// Detect protocol text, not English: quotations and English story text survive.
export function storyText(text:string,streaming=false):string{
  const meta=/^(?:outputMode\s+is\b|(?:The\s+)?outputMode\s*[=:]|(?:Since|Because)\b[^\n]*\boutputMode\b|(?:I\s+(?:will|should|need to)|I['’]ll)\s+(?:use|follow|output|write)\b[^\n]*(?:dialogue format|narrative format|Chinese prose)|输出模式(?:为|是|[=:：])|(?:根据|按照)\s*outputMode)/i
  const prefixes=['outputMode is','The outputMode =','Since','Because',"I'll",'I’ll','I will','I should','I need to','输出模式','根据 outputMode','按照 outputMode']
  let value=text.trimStart()
  while(value){
    const newline=value.indexOf('\n'),line=newline<0?value:value.slice(0,newline)
    if(meta.test(line)){if(newline<0&&streaming)return '';value=newline<0?'':value.slice(newline+1).trimStart();continue}
    if(streaming&&newline<0&&prefixes.some(p=>p.toLowerCase().startsWith(line.toLowerCase())))return ''
    break
  }
  return value
}

export function separateStageDirections(segments:DialogueSegment[],partner=''):DialogueSegment[]{
  return segments.flatMap(part=>{
    if(part.kind!=='speech')return [{...part}]
    const escape=(value:string)=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')
    const actor='(?:'+[part.speakerName||partner,'他','她','对方'].filter(Boolean).map(escape).join('|')+')'
    if(new RegExp('^'+actor+'(?:摆了|挥了|退开|放下|收起|起身|负手|抬起|转身)').test(part.text)&&!/[：:“”「」"]/.test(part.text))return [{kind:'narration' as const,text:part.text}]
    const direction=new RegExp('(?:^|(?<=[。！？\\n]))'+actor+'(?:指了指[^，。！？\\n]{0,40}[，。！？]|(?:收钱入账|伸出[^。！？\\n]{0,40}(?:手指|手)|顿了顿|抬手|抬起头|看你一眼|看了你一眼|点了点头|摇了摇头)[^。！？：:\\n]{0,70}[：:。！？])','g')
    const out:DialogueSegment[]=[];let cursor=0
    for(const match of part.text.matchAll(direction)){
      const before=part.text.slice(cursor,match.index).trim();if(before)out.push({...part,text:before})
      out.push({kind:'narration',text:match[0].trim()});cursor=match.index!+match[0].length
    }
    const tail=part.text.slice(cursor).trim();if(tail)out.push({...part,text:tail});return out
  })
}

// Mirrors the public dialogue contract, with legacy prose support while a
// response is streaming or a saved scene predates explicit channels.
export function dialogueSegments(text:string,partner='',streaming=false):DialogueSegment[]{
  text=storyText(text,streaming)
  const result:DialogueSegment[]=[]
  function add(kind:DialogueSegment['kind'],value:string){
    value=value.trim();if(!value)return
    const last=result.at(-1);if(last?.kind===kind)last.text+='\n\n'+value;else result.push({kind,text:value})
  }
  const markers=[...text.matchAll(/【(旁白|台词)】/g)]
  if(markers.length){
    add('narration',text.slice(0,markers[0]!.index))
    markers.forEach((marker,index)=>{
      let value=text.slice(marker.index!+marker[0].length,markers[index+1]?.index??text.length)
      if(streaming)value=value.replace(/【(?:旁(?:白)?|台(?:词)?)?$/,'')
      if(marker[1]==='台词'){
        let direction:RegExpMatchArray|null
        while((direction=value.match(/^\s*[（(]([^）)]*)[）)]\s*/))){add('narration',direction[1]!);value=value.slice(direction[0].length)}
      }
      add(marker[1]==='台词'?'speech':'narration',value)
    });return separateStageDirections(result,partner)
  }
  if(streaming&&/^【(?:旁(?:白)?|台(?:词)?)?$/.test(text.trim()))return []
  const escape=(value:string)=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')
  const actor='(?:'+[partner,'他','她','对方'].filter(Boolean).map(escape).join('|')+')'
  const attribution=new RegExp(actor+'[^。！？“”「」"\\n]{0,24}(?:说|答|问|道|嘀咕|低语)[^。！？“”「」"\\n]{0,8}[：:,，]?\\s*$')
  const narrative=new RegExp('雾气|远处|水面|天色|夜色|风声|海风|没有说话|没有开口|沉默|抬头|低头|转身|看着你|望着你|你站在|你走到|你看见|你听见|'+actor+'(?:正|正在|已经|缓缓|轻轻|忽然|仍|没有|并未|把|将|抬|低|转|站|坐|望|看|整理|停|沉默|说|答|问|笑|点头|摇头|收|伸|走|拿|的|手上|微微)|^[^，。：:\\n“”「」]{1,12}(?:说|问|答)(?:道)?[：:]')
  const direct=/^(?:我(?:们)?(?:[，,。!?]|会|想|要|是|在|不|没|也|和|陪|带|等)|你好|好[，。,！!]|嗯|别|请)/
  for(const paragraph of text.split(/\n\s*\n/).map(value=>value.trim()).filter(Boolean)){
    let cursor=0
    for(const quote of paragraph.matchAll(/“([^”\n]*)”|「([^」\n]*)」|"([^"\n]*)"/g)){
      const before=paragraph.slice(0,quote.index),after=paragraph.slice(quote.index!+quote[0].length)
      if(attribution.test(before)||!before.trim()&&!after.trim()||!before.trim()&&new RegExp('^'+actor+'(?:说|答|问|道)').test(after.replace(/^[，,。 ]+/,''))){
        add('narration',paragraph.slice(cursor,quote.index));add('speech',quote[1]??quote[2]??quote[3]??'');cursor=quote.index!+quote[0].length
      }
    }
    if(cursor)add('narration',paragraph.slice(cursor))
    else add(narrative.test(paragraph)&&!direct.test(paragraph)?'narration':'speech',paragraph)
  }
  return separateStageDirections(result,partner)
}

// Resolve speakers from public actions and explicit channels. Never infer a
// speaker merely because a name occurs inside the words addressed to them.
export function sceneDialogueSegments(text:string,people:{id:string;name:string}[],partnerId?:string,streaming=false):DialogueSegment[]{
  text=storyText(text,streaming)
  if(/^\s*\{\s*"kind"\s*:/.test(text)){
    const result:DialogueSegment[]=[]
    for(const line of channelFrames(text)){
      if(!line.trim())continue
      let value:{kind?:string;text?:string;speakerId?:string}|undefined
      try{value=channelObject(line)}catch{
        if(streaming){
          const kind=line.match(/"kind"\s*:\s*"(speech|narration)"/)?.[1]
          const speakerId=line.match(/"speakerId"\s*:\s*"([^"\\]*)"/)?.[1]
          const fragment=line.match(/"text"\s*:\s*"((?:[^"\\]|\\.)*)/)?.[1]
          if(fragment!==undefined){try{value={kind,speakerId,text:JSON.parse('"'+fragment.replace(/\\$/,'')+'"')}}catch{}}
        }
      }
      if(!value||!value.text||!['speech','narration'].includes(value.kind||''))continue
      const speaker=people.find(p=>p.id===value?.speakerId||p.name===value?.speakerId)
      if(value.kind==='speech'&&speaker)result.push({kind:'speech',text:value.text,speakerId:speaker.id,speakerName:speaker.name})
      else result.push({kind:'narration',text:value.text})
    }
    return result
  }
  if(streaming)text=text.replace(/【(?:旁(?:白)?|台(?:词)?(?:[：:][^】]*)?)?$/,'')
  type Person={id:string;name:string}
  const out:DialogueSegment[]=[],fallback=people.find(p=>p.id===partnerId)
  const escape=(value:string)=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')
  let lastSpeaker:Person|undefined=fallback
  const namedActor=(context:string)=>people.map(person=>({person,index:[...context.matchAll(new RegExp(escape(person.name)+'(?:的[^，。！？\\n]{0,10}(?:声音|投影|身影|视线)|[，,：:]|\\s*(?:说|问|答|道|笑|抬|低|点|转|看|见|站|坐|收|从|把|伸|屈|压|开|头|手|放|摆|挥|退|起身|负手|打量|上下|仍|正|已经|没有|缓缓|淡淡))','g'))].at(-1)?.index??-1})).filter(p=>p.index>=0).sort((a,b)=>b.index-a.index)[0]?.person
  function add(kind:DialogueSegment['kind'],value:string,speaker?:Person){
    value=value.trim();if(!value)return
    out.push({kind,text:value,...(kind==='speech'&&speaker?{speakerId:speaker.id,speakerName:speaker.name}:{})})
    if(kind==='narration')lastSpeaker=namedActor(value)||lastSpeaker
    else if(speaker)lastSpeaker=speaker
  }
  const isStage=(value:string)=>/^(?:他|她|你)(?:侧|收|抬|低|转|看|望|见|把|从|依|没有|握|点|摇|伸|屈|走|顿|摆|挥|退|放|闭|瘫|抱|垂|盯|攥|舔|起身|负手)|^(?:说到这里|说完|话音刚落)|^(?:松风|雾气|鼎内|药汁|天色|日头|小药鼎|柜台|林掌柜)(?:掠|在|的|见|把|抬|传|开始)/.test(value.trim())||people.some(p=>new RegExp('^'+escape(p.name)+'(?:的|抬|低|转|看|望|见|把|从|收|伸|屈|顿|笑|摆|挥|退|放|闭|瘫|抱|垂|盯|攥|舔|起身|负手|没有)').test(value.trim()))
  function legacy(value:string,forced?:Person,channel=false,narrationOnly=false){
    for(const [paragraphIndex,paragraph] of value.split(/\n\s*\n/).map(p=>p.trim()).filter(Boolean).entries()){
      let cursor=0,found=false
      const quotes=[...paragraph.matchAll(/“([^”\n]*)”|「([^」\n]*)」|"([^"\n]*)"/g)]
      for(const quote of quotes){
        const before=paragraph.slice(cursor,quote.index),after=paragraph.slice(quote.index!+quote[0].length)
        const clause=before.split(/[。！？\n]/).at(-1)||''
        const named=namedActor(clause),trailing=people.find(p=>new RegExp('^[，,。\s]*'+escape(p.name)+'(?:说|问|答|道|把|抬|转|压|笑)').test(after))
        const attributed=/(?:说|问|答|道|低语|嘀咕|响起|传来|传音|开口|声音|口气|补了一句)[^。！？]{0,14}[：:,，]?\s*$/.test(clause)
        const standalone=!before.trim()&&(!after.trim()||!!trailing)
        const pronoun=/(?:^|[，,])\s*(?:他|她)/.test(clause.trim())
        const speaker=(!before.trim()?trailing:undefined)||(attributed?(named||(pronoun?lastSpeaker:undefined)):undefined)||(standalone?lastSpeaker||forced:undefined)
        if(speaker){
          const stage=before.replace(new RegExp('(?:^|(?<=[。！？\\n]))\\s*'+escape(speaker.name)+'(?:说|问|答|道)(?:道)?[：:]\\s*$'),'')
          add('narration',stage);add('speech',quote[1]??quote[2]??quote[3]??'',speaker);cursor=quote.index!+quote[0].length;found=true
        }
      }
      if(found){add('narration',paragraph.slice(cursor));continue}
      const prefix=people.find(p=>paragraph.startsWith(p.name+'：')||paragraph.startsWith(p.name+':'))
      if(prefix){add('speech',paragraph.slice(prefix.name.length+1),prefix);continue}
      if(channel&&forced&&!isStage(paragraph)&&!(paragraphIndex>0&&namedActor(paragraph)))add('speech',paragraph,forced)
      else if(!channel&&!narrationOnly&&fallback&&!quotes.length&&!namedActor(paragraph)&&!isStage(paragraph)&&dialogueSegments(paragraph,fallback.name).every(p=>p.kind==='speech'))add('speech',paragraph,fallback)
      else add('narration',paragraph)
    }
  }
  const markers=[...text.matchAll(/【(旁白|台词)(?:[：:]([^】]+))?】/g)]
  if(!markers.length){legacy(text);return separateStageDirections(out,fallback?.name)}
  legacy(text.slice(0,markers[0]!.index))
  for(const [i,m] of markers.entries()){
    let value=text.slice(m.index!+m[0].length,markers[i+1]?.index??text.length)
    if(streaming)value=value.replace(/【(?:旁(?:白)?|台(?:词)?(?:[：:][^】]*)?)?$/,'')
    if(m[1]==='旁白'){legacy(value,undefined,false,true);continue}
    let speaker=m[2]?people.find(p=>p.id===m[2]||p.name===m[2]):lastSpeaker||fallback,direction:RegExpMatchArray|null
    while((direction=value.match(/^\s*[（(]([^）)]*)[）)]\s*/))){speaker=namedActor(direction[1]!)||speaker;add('narration',direction[1]!);value=value.slice(direction[0].length)}
    if(speaker&&(value.startsWith(speaker.name+'：')||value.startsWith(speaker.name+':')))value=value.slice(speaker.name.length+1).trimStart()
    legacy(value,speaker,true)
  }
  return separateStageDirections(out,fallback?.name)
}

export function streamStoryText(text:string,people:{id:string;name:string}[]):string{
  const value=storyText(text,true)
  if(/^\s*\{/.test(value)){
    if(!/^\s*\{\s*"kind"\s*:/.test(value))return ''
    return sceneDialogueSegments(value,people,undefined,true).map(part=>part.kind==='speech'?`${part.speakerName}说：“${part.text}”`:part.text).join('\n\n')
  }
  return value
}
