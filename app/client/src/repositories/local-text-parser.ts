import {sha256,ContractError,type Chapter,type Paragraph} from '../domain/local-contract'
export interface ParsedText {chapters:Chapter[];paragraphs:Paragraph[]}
export interface ParsingProgress {paragraphs:number;chapters:number}
/** Positions refer to the decoded original UTF-16 text, including its original
 * CR/LF units. Headings and blank lines keep their exact source positions. */
export async function parseImportedText(text:string,suffix:string,parseHash:string,progress:(p:ParsingProgress)=>void):Promise<ParsedText>{
  if(text.includes('\0'))throw new ContractError('IMPORT_LIMIT','正文不能包含空字符')
  const chapters:Chapter[]=[],paragraphs:Paragraph[]=[];let chapter:Chapter|undefined,offset=0,characters=0,lines=0
  const heading=/^\s*(?:第[零〇一二三四五六七八九十百千万0-9]+[章节卷回部].*|chapter\s+\d+.*)\s*$/i
  for(const line of text.match(/[^\r\n]*(?:\r\n|\r|\n|$)/g)||[]){
    const body=line.replace(/[\r\n]+$/,''),markdown=suffix!=='txt'?body.match(/^ {0,3}#{1,6}\s+(.+?)\s*#*$/):null
    if(heading.test(body)||markdown){chapter={id:'chapter-'+parseHash.slice(0,16)+'-'+chapters.length,title:markdown?markdown[1]!.trim():body.trim(),paragraphIds:[]};chapters.push(chapter)}
    else if(body.trim()){
      for(const _character of body)characters++
      if(characters>3_000_000)throw new ContractError('IMPORT_LIMIT','正文超过 300 万字符')
      if(!chapter){chapter={id:'chapter-'+parseHash.slice(0,16)+'-0',title:'第一章',paragraphIds:[]};chapters.push(chapter)}
      const id='paragraph-'+parseHash.slice(0,20)+'-'+paragraphs.length;paragraphs.push({id,chapterId:chapter.id,text:body,sourceStart:offset,sourceEnd:offset+body.length,textHash:''});chapter.paragraphIds.push(id)
    }
    offset+=line.length
    if(chapters.length>10000)throw new ContractError('CHAPTER_LIMIT','章节超过 10,000 章限额')
    if(++lines%128===0){progress({paragraphs:paragraphs.length,chapters:chapters.length});await new Promise<void>(resolve=>setTimeout(resolve,0))}
  }
  if(!paragraphs.length)throw new ContractError('IMPORT_EMPTY','文件没有可阅读的正文')
  for(let start=0;start<paragraphs.length;start+=64)await Promise.all(paragraphs.slice(start,start+64).map(async p=>{p.textHash=await sha256(p.text)}))
  progress({paragraphs:paragraphs.length,chapters:chapters.length});return {chapters,paragraphs}
}
