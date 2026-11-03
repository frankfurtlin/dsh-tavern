import {canonical,sha256,type Paragraph} from '../domain/local-contract'
export const COMPILER_VERSION='paragraph-beats-v1'
export interface StageCharacter {id:string;name:string;aliases:string[];portraitAssetId?:string}
export interface Beat {id:string;paragraphId:string;start:number;end:number;text:string;speakerId:string|null;confidence:number;kind:'narration'|'dialogue';sourceHash:string}
const pairs:Record<string,string>={'“':'”','「':'」','『':'』'}
const escaped=(value:string)=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')
function speaker(text:string,start:number,end:number,characters:StageCharacter[]):string|null {
  const before=text.slice(Math.max(0,start-64),start),after=text.slice(end,end+48)
  const matches=new Set<string>()
  for(const character of characters){for(const name of [character.name,...character.aliases]){
    if(!name.trim())continue
    const literal=escaped(name),left=new RegExp(literal+'(?:轻声|低声|小声|笑着|停顿后|缓缓)?(?:说|问|回答|喊|说道|道)[^。！？;；“”「」『』]{0,8}$'),right=new RegExp('^[，,、\\s]*'+literal+'(?:轻声|低声|小声|笑着)?(?:说|问|回答|喊|说道|道)')
    const collective=new RegExp('(?:和|与|及|、|跟)\\s*'+literal+'(?:轻声|低声|小声|笑着|停顿后|缓缓)?(?:说|问|回答|喊|说道|道)');if(!collective.test(before)&&(left.test(before)||right.test(after)))matches.add(character.id)
  }}
  return matches.size===1?[...matches][0]!:null
}
/** Exact UTF16 partition: ambiguous attribution stays narrator; no model calls. */
export async function compileParagraphsToBeats(paragraphs:Paragraph[],characters:StageCharacter[]):Promise<{compilerVersion:string;hash:string;beats:Beat[]}>{
  const beats:Beat[]=[]
  for(const paragraph of paragraphs){
    const ranges:{start:number;end:number;quoted:boolean}[]=[]
    let cursor=0,scan=0
    while(scan<paragraph.text.length){const opener=paragraph.text[scan]!,closer=pairs[opener];if(!closer){scan++;continue}const end=paragraph.text.indexOf(closer,scan+1);if(end===-1){scan++;continue}if(scan>cursor)ranges.push({start:cursor,end:scan,quoted:false});ranges.push({start:scan,end:end+1,quoted:true});scan=end+1;cursor=scan}
    if(cursor<paragraph.text.length)ranges.push({start:cursor,end:paragraph.text.length,quoted:false})
    for(const range of ranges){const speakerId=range.quoted?speaker(paragraph.text,range.start,range.end,characters):null
      beats.push({id:(await sha256(canonical({version:COMPILER_VERSION,paragraphId:paragraph.id,sourceHash:paragraph.textHash,start:range.start,end:range.end}))).slice(0,32),paragraphId:paragraph.id,start:range.start,end:range.end,text:paragraph.text.slice(range.start,range.end),speakerId,confidence:speakerId?1:range.quoted?0:1,kind:speakerId?'dialogue':'narration',sourceHash:paragraph.textHash})
    }
  }
  return {compilerVersion:COMPILER_VERSION,hash:await sha256(canonical({version:COMPILER_VERSION,characters,beats})),beats}
}
