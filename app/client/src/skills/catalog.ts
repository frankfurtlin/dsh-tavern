import {operationCapabilities,type SkillManifest,type SkillOperation,type SkillSchema} from './contract'
const text=(maxLength=20000)=>({type:'string' as const,maxLength}),count={type:'integer' as const,minimum:1,maximum:5}
const rows:[SkillOperation,string,string,SkillSchema['properties'],string[]][]=[
 ['novel-cards','小说建卡','从所选正文整理有原文依据的角色卡',{name:text(200),direction:text()},[]],
 ['text-lorebook','文本建世界书','把地点、人物关系与已出现设定整理为触发条目',{name:text(200)},[]],
 ['continue-story','续写','从当前阅读锚点接着写，复用逐章写作与候选机制',{count,direction:text()},['count']],
 ['rewrite-story','改写','从选定锚点分叉，保留原文和其他故事线',{count,direction:text()},['count','direction']],
 ['continuity-check','连续性检查','逐条列出有来源的潜在冲突，不覆盖正文',{direction:text()},[]],
 ['branch-compare','分支比较','比较两条故事线的共有段落、分歧与各自正文',{},[]],
 ['reading-notes','阅读笔记','为具体原文引用写笔记，确认后存入笔记库',{direction:text()},[]],
 ['source-summary','摘要','生成有引用的范围摘要，明确未覆盖内容',{direction:text()},[]],
 ['memory-transfer','记忆迁移','将明确保留的记忆复制到所选伴侣，原记忆保留',{targetId:text(160)},['targetId']],
 ['tavern-diagnosis','酒馆配置诊断','检查会话成员、候选、世界书和群聊设置',{},[]],
 ['card-repair','角色卡修复','去除重复问候和标签、整理空白，预览字段差异',{},[]],
 ['group-arrangement','群聊编排','为真实群聊设置发言方式与有限轮次',{mode:{type:'string',enum:['specified','sequential','random','natural']},count:{type:'integer',minimum:1,maximum:12},direction:text(2000)},['mode','count']],
 ['chapter-image','章节配图','为所选段落生成一张关联原文版本的插画',{direction:text()},[]],
 ['comic','漫画','生成分镜脚本，再逐张生成对应画面',{count:{type:'integer',minimum:1,maximum:4},direction:text()},['count']],
 ['stage-inventory','剧场资源整理','核对背景、角色立绘和场景关联的实际资产',{},[]],
 ['stickers','贴纸','用所选角色制作一张方形贴纸插画',{direction:text()},[]],
 ['theme','主题制作','制作受限色板并检查正文与主按钮对比度',{name:text(100),accent:text(7),paper:text(7),canvas:text(7),ink:text(7)},['name','accent','paper','canvas','ink']],
 ['companion-album','伴侣纪念册','从真实共同事件和明确记忆编排纪念册',{},[]],
 ['community-import','社区内容筛选导入','筛选用户提供的公开角色包，预览后导入副本',{packageJSON:text(),query:text(200)},['packageJSON']],
 ['card-design','AI 建卡','从设定文本或选定资料设计角色，预览后保存新卡',{text:text(),direction:text()},[]],
 ['card-revise','AI 修卡','按明确要求修订字段，确认后保存新修订',{direction:text()},['direction']]
]
export const builtinSkills:SkillManifest[]=rows.map(([operation,name,description,properties,required])=>({format:'reader-skill-v1',id:operation,name,version:'1.0.0',description,operation,capabilities:operationCapabilities[operation],inputSchema:{type:'object',properties,required,additionalProperties:false},outputSchema:{type:'object',format:operation+'-result-v1'},skillMarkdown:`# ${name}\n\n${description}。\n\n## 输入\n仅使用用户选择并冻结的资料与 manifest 的 inputSchema。\n\n## 执行\n使用 ${operation} 注册执行器。所需能力：${operationCapabilities[operation].join('、')}。模型模态：${operationCapabilities[operation].includes('image')?'图像'+(operationCapabilities[operation].includes('text')?'与文本':''):operationCapabilities[operation].includes('text')||operationCapabilities[operation].includes('writing')?'文本':'无模型'}。\n\n## 修改\n既有内容先预览差异；新内容保存副本；小说写作交给写作 runtime，绝不覆盖原文。\n\n## 失败\n未授权、来源变化、未知响应和存储错误停止执行，保留已收到文字与独立记录，不自动追加收费请求。\n\n## 验收\n成功产物必须通过 ${operation}-result-v1 结构与原文引用校验；失败用例包含缺少资料、输入无效和撤权。`}))
export const builtinSkill=(operation:SkillOperation)=>builtinSkills.find(row=>row.operation===operation)!
