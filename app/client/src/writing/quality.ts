export interface WritingQuality {targetLength:number;actualLength:number;repeatedParagraphs:number;templatePhrases:string[];warnings:string[]}
export function assessWritingQuality(text:string,raw:string,finishReason:string|null,targetLength:number):WritingQuality{
 const actualLength=text.replace(/\s/g,'').length,paragraphs=text.split(/\n\s*\n/).filter(p=>p.trim().length>40),repeatedParagraphs=paragraphs.length-new Set(paragraphs).size,templatePhrases=['命运的齿轮开始转动','新的篇章即将展开','故事才刚刚开始'].filter(phrase=>text.includes(phrase)),warnings:string[]=[]
 if(actualLength<targetLength*.6)warnings.push('篇幅短于目标，正文仍可阅读和接受')
 if(!['stop','end_turn'].includes(finishReason||''))warnings.push('模型未完整结束')
 if(/^\s*(?:```|[\[{])/.test(raw)||/<\/?(?:think|analysis)>/i.test(raw))warnings.push('正文已解析，原返回保留在记录中')
 if(repeatedParagraphs)warnings.push('检测到重复段落')
 if(templatePhrases.length)warnings.push('检测到常见模板句，可编辑正文')
 return {targetLength,actualLength,repeatedParagraphs,templatePhrases,warnings}
}
