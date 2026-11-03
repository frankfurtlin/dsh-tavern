import {it,expect} from 'vitest'
import {dialogueSegments,sceneDialogueSegments,separateStageDirections,storyText,streamStoryText} from './scene-dialogue'

it('renders complete escaped channel separators without changing quoted newlines',()=>{
  const wire=JSON.stringify({kind:'narration',text:'你递出便笺。\n柜台上有盒子{A}。'})+'\\n'+JSON.stringify({kind:'speech',speakerId:'h',text:'我收到了。'})
  const people=[{id:'h',name:'店主'}]
  expect(streamStoryText(wire,people)).toBe('你递出便笺。\n柜台上有盒子{A}。\n\n店主说：“我收到了。”')
  for(let i=1;i<=wire.length;i++)expect(streamStoryText(wire.slice(0,i),people)).not.toMatch(/"kind"|speakerId|"text"/)
})

it('renders structured live prose without exposing any JSON prefix',()=>{
  const people=[{id:'h',name:'侦探'}]
  const wire='{"kind":"narration","text":"门开了。"}\n{"kind":"speech","speakerId":"h","text":"你好。"}'
  for(let end=1;end<=wire.length;end++){
    const shown=streamStoryText(wire.slice(0,end),people)
    expect(shown).not.toMatch(/kind|speakerId|text|[{}]/)
  }
  expect(streamStoryText(wire,people)).toBe('门开了。\n\n侦探说：“你好。”')
})

it('hides the observed protocol preface in every streamed prefix while preserving English story text',()=>{
  const prefix='outputMode is "dialogue", so I\'ll use the dialogue format.\n\n'
  for(let i=1;i<=prefix.length;i++)expect(storyText(prefix.slice(0,i),true)).toBe('')
  expect(sceneDialogueSegments(prefix+'【旁白】你看见石阶。',[])).toEqual([{kind:'narration',text:'你看见石阶。'}])
  expect(storyText('Welcome to the city.')).toBe('Welcome to the city.')
  expect(storyText('“outputMode is dialogue”，纸条上写着。')).toContain('outputMode')
})

it('repairs the actual returned-mountain reply with unnamed markers and unmarked narration',()=>{
  const text='小药鼎架在青石台中央。药老的淡色投影立在鼎旁，双手拢在袖中，听见脚步也没回头——他早在等你了。\n\n【台词】材料既然齐了，就摆出来吧。\n\n他侧过身，目光在你包袱上一扫。\n\n【台词】今天这第一炉，我亲自教你控火。\n\n松风掠过石台，鼎底还留着上回熏出的浅痕。'
  const parts=sceneDialogueSegments(text,[{id:'yao',name:'药老'}])
  expect(parts.filter(p=>p.kind==='speech').map(p=>[p.speakerName,p.text])).toEqual([['药老','材料既然齐了，就摆出来吧。'],['药老','今天这第一炉，我亲自教你控火。']])
  expect(parts.filter(p=>p.kind==='narration').map(p=>p.text).join('')).toContain('他侧过身')
  expect(parts.filter(p=>p.kind==='speech').every(p=>!p.text.includes('松风'))).toBe(true)
  expect(sceneDialogueSegments(text.slice(0,text.indexOf('就摆')+2),[{id:'yao',name:'药老'}],undefined,true).some(p=>p.speakerId==='yao')).toBe(true)
})

it('keeps the received furnace sound paragraph out of the teacher bubble without a keyword list',()=>{
  const text='药老的视线落在你握着火钳的手上。\n\n【台词】手别抖。声响密而不炸是正好。\n\n噼啪声连成一片，白雾由清甜转为微苦。药老伸手掀开鼎盖一角，示意你将魔核投入。'
  const parts=sceneDialogueSegments(text,[{id:'yao',name:'药老'}])
  expect(parts.filter(p=>p.kind==='speech').map(p=>p.text)).toEqual(['手别抖。声响密而不炸是正好。'])
  expect(parts.at(-1)?.kind).toBe('narration')
})

it('keeps actual multi-person targeted chat from assigning the shopkeeper line to the teacher',()=>{
  const text='纳戒微微一颤，药老的声音压得很低，只送进你一个人耳里，柜台后的林掌柜浑然不觉。\n\n“连着三回不留名，这里头必有蹊跷。”\n\n他顿了顿，语气缓了几分。\n\n“先回后山，把第一炉炼出来给我看。”\n\n林掌柜见你沉吟，把账簿合上一半，抬眼等你示下。\n\n“小少爷，怎么个说法？”'
  const parts=sceneDialogueSegments(text,[{id:'yao',name:'药老'},{id:'lin',name:'林掌柜'}],'yao')
  expect(parts.filter(p=>p.kind==='speech').map(p=>p.speakerName)).toEqual(['药老','药老','林掌柜'])
})

it('restores quote-first, pronoun follow-up and named channels without turning objects into dialogue',()=>{
  const people=[{id:'lin',name:'林掌柜'}]
  const parts=sceneDialogueSegments('「小少爷爽快。」他转身拉开药屉。\n\n「点点数。」林掌柜把账簿翻过一页，压低了声音，「日后有事只管来。」',people)
  expect(parts.filter(p=>p.kind==='speech').map(p=>p.text)).toEqual(['点点数。','日后有事只管来。'])
  expect(sceneDialogueSegments('【台词：林掌柜】你好。\n\n他转身收起账簿。',people).map(p=>p.kind)).toEqual(['speech','narration'])
  expect(sceneDialogueSegments('【台词：不在场的人】你好。',people).every(p=>p.kind==='narration')).toBe(true)
})

it('renders the reported shop greeting and hidden spirit voice as distinct speech, keeping actions in narration',()=>{
  const text='药老的投影在你走近铺子时悄然散去，声音只在你纳戒里响起：「就是这里，价码你自己谈，别露怯。」林掌柜抬起头，打量了你一眼，笑问：「小少爷要点什么？」'
  const parts=sceneDialogueSegments(text,[{id:'yao',name:'药老'},{id:'lin',name:'林掌柜'}])
  expect(parts.filter(p=>p.kind==='speech').map(p=>[p.speakerName,p.text])).toEqual([['药老','就是这里，价码你自己谈，别露怯。'],['林掌柜','小少爷要点什么？']])
  expect(parts.filter(p=>p.kind==='narration').map(p=>p.text).join('')).toContain('林掌柜抬起头，打量了你一眼，笑问：')
  expect(parts.filter(p=>p.kind==='speech').every(p=>!p.text.includes('抬起头'))).toBe(true)
  expect(sceneDialogueSegments('他看着写有「药材清单」的纸条。',[{id:'lin',name:'林掌柜'}])).toEqual([{kind:'narration',text:'他看着写有「药材清单」的纸条。'}])
})

it('separates received embedded acting directions and retains the surrounding speech',()=>{
  const text='小少爷点点。他收钱入账，又压低了声音：您若得空，只管来问。'
  const expected=[{kind:'speech',text:'小少爷点点。'},{kind:'narration',text:'他收钱入账，又压低了声音：'},{kind:'speech',text:'您若得空，只管来问。'}]
  expect(dialogueSegments('【台词】'+text,'林掌柜')).toEqual(expected)
  const saved=[{kind:'speech' as const,text}];expect(separateStageDirections(saved,'林掌柜')).toEqual(expected);expect(saved).toEqual([{kind:'speech',text}])
  expect(dialogueSegments('【台词】他的手受过伤。我告诉他：别动。','药老')).toEqual([{kind:'speech',text:'他的手受过伤。我告诉他：别动。'}])
})

const silent='雾气贴着水面。你站在东岸码头，林渡正在整理他的船缆，动作不快，目光偶尔朝北岸方向扫一眼。远处仓库那边隐约传来一声闷响，像是木料断裂，随即又安静下去。林渡手上的动作停了半拍，没有说话。'
it('shows the reported silent response entirely as narration',()=>{
  expect(dialogueSegments(silent,'林渡')).toEqual([{kind:'narration',text:silent}])
})
it('keeps alternating narration and spoken words in order',()=>{
  expect(dialogueSegments('【旁白】林渡收起船缆。\n【台词】你好，有事吗？\n【旁白】他向台阶指了指。\n【台词】我们过去说。','林渡')).toEqual([
    {kind:'narration',text:'林渡收起船缆。'},{kind:'speech',text:'你好，有事吗？'},{kind:'narration',text:'他向台阶指了指。'},{kind:'speech',text:'我们过去说。'}
  ])
})
it('streams partial channel markers without displaying them',()=>{
  expect(dialogueSegments('【旁','林渡',true)).toEqual([])
  expect(dialogueSegments('【旁白】林渡点头。\n【台','林渡',true)).toEqual([{kind:'narration',text:'林渡点头。'}])
  expect(dialogueSegments('【旁白】林渡点头。\n【台词】你','林渡',true).at(-1)).toEqual({kind:'speech',text:'你'})
})
it('extracts attributed legacy speech but leaves quoted objects in narration',()=>{
  expect(dialogueSegments('林渡说：“你好。”他收起写着“夜渡”的木牌。','林渡')).toEqual([
    {kind:'narration',text:'林渡说：'},{kind:'speech',text:'你好。'},{kind:'narration',text:'他收起写着“夜渡”的木牌。'}
  ])
})
it('retains bare replies and restored quoted speech',()=>{
  expect(dialogueSegments('好，我们一起走。','陆遥')).toEqual([{kind:'speech',text:'好，我们一起走。'}])
  expect(dialogueSegments('林渡收起船缆。\n\n“你好，有什么事？”','林渡')).toEqual([{kind:'narration',text:'林渡收起船缆。'},{kind:'speech',text:'你好，有什么事？'}])
})
it('does not confuse first-person dialogue with scene descriptions or another speaker',()=>{
  expect(dialogueSegments('我在远处等你。','林渡')).toEqual([{kind:'speech',text:'我在远处等你。'}])
  expect(dialogueSegments('梅舟说：“我先走了。”','林渡')).toEqual([{kind:'narration',text:'梅舟说：“我先走了。”'}])
})
it('shows the reported untargeted opening draft as Guni speech and stage directions',()=>{
  const parts=sceneDialogueSegments('柜台前空着。\n【台词】（谷尼头也没抬，合上匣盖）雅妃，柜台前说话便好。\n【旁白】他抬眼看向你。',[{id:'guni',name:'谷尼'}])
  expect(parts.filter(p=>p.kind==='speech')).toEqual([{kind:'speech',text:'雅妃，柜台前说话便好。',speakerId:'guni',speakerName:'谷尼'}])
  expect(parts.filter(p=>p.kind==='narration').map(p=>p.text)).toEqual(['柜台前空着。','谷尼头也没抬，合上匣盖','他抬眼看向你。'])
})
it('does not attribute an absent character or an unidentified opening speaker',()=>{
  expect(sceneDialogueSegments('【台词】（林渡转身）你好。',[{id:'guni',name:'谷尼'}]).every(p=>p.kind==='narration')).toBe(true)
  expect(sceneDialogueSegments('【台词】你好。',[{id:'guni',name:'谷尼'}]).every(p=>p.kind==='narration')).toBe(true)
})
it('supports several present speakers and preserves dialogue order',()=>{
  const people=[{id:'g',name:'谷尼'},{id:'y',name:'雅妃'}]
  expect(sceneDialogueSegments('谷尼说：“请进。”雅妃问：“拍卖开始了吗？”',people).filter(p=>p.kind==='speech').map(p=>[p.speakerName,p.text])).toEqual([['谷尼','请进。'],['雅妃','拍卖开始了吗？']])
  expect(sceneDialogueSegments('【台词】（他点头）请进。',people,'g').filter(p=>p.kind==='speech')[0]?.speakerId).toBe('g')
})
it('uses the bubble name instead of repeating a bare speech attribution',()=>{
  const people=[{id:'h',name:'福尔摩斯'}]
  expect(sceneDialogueSegments('福尔摩斯说：“请进。”',people)).toEqual([{kind:'speech',speakerId:'h',speakerName:'福尔摩斯',text:'请进。'}])
  expect(sceneDialogueSegments('福尔摩斯放下烟斗，说：“请进。”',people)[0]).toEqual({kind:'narration',text:'福尔摩斯放下烟斗，说：'})
})
it('resolves stable speaker IDs and retains later quotes attributed by pronoun',()=>{
  const people=[{id:'merchant',name:'林掌柜'},{id:'spirit',name:'药老'}]
  const parts=sceneDialogueSegments('【旁白】林掌柜看你一眼。【台词：merchant】要点什么？【旁白】药老顿了顿。【台词：spirit】先看药材。',people)
  expect(parts.filter(p=>p.kind==='speech').map(p=>[p.speakerId,p.text])).toEqual([['merchant','要点什么？'],['spirit','先看药材。']])
  const legacy=sceneDialogueSegments('药老说：“第一句。”他袖袍一挥，低声道：“第二句。”',people)
  expect(legacy.filter(p=>p.kind==='speech').map(p=>p.text)).toEqual(['第一句。','第二句。'])
})

it('renders the real father and spirit acting directions as narration on stream and restored receipts',()=>{
  const text='他摆了摆手，目光落回摊开的货单。厅外日头正盛，木梁的影子挪过桌角。'
  expect(sceneDialogueSegments('【台词：father】'+text,[{id:'father',name:'萧战'}]).every(p=>p.kind==='narration')).toBe(true)
  expect(separateStageDirections([{kind:'speech',speakerName:'萧战',text}])).toEqual([{kind:'narration',text}])
  expect(separateStageDirections([{kind:'speech',speakerName:'药老',text:'他负手退开半步，白雾衣袖垂落。'}]).every(p=>p.kind==='narration')).toBe(true)
})

it('extracts a real inline pointing action without swallowing the following dialogue',()=>{
  const parts=separateStageDirections([{kind:'speech',speakerName:'药老',text:'第三。药老指了指你腰间纳戒，那瓶灵液一次没碰。'}])
  expect(parts.map(p=>p.kind)).toEqual(['speech','narration','speech'])
  expect(parts[1]?.text).toBe('药老指了指你腰间纳戒，')
})

it('keeps the received confession draft stage paragraphs out of the suspect speech bubble',()=>{
  const people=[{id:'ryder',name:'詹姆斯·赖德'},{id:'holmes',name:'福尔摩斯'}]
  const text='詹姆斯·赖德说：“我交代。”\n\n他舔了舔干裂的嘴唇，声音发颤。\n\n说到这里他瘫进椅子里，面如死灰。\n\n詹姆斯·赖德闭上眼，算是默认。'
  const parts=sceneDialogueSegments(text,people,'ryder')
  expect(parts.filter(p=>p.kind==='speech').map(p=>p.text)).toEqual(['我交代。'])
  expect(parts.filter(p=>p.kind==='narration')).toHaveLength(3)
})

it('preserves inner quotation marks in complete received channels while streaming and after refresh',()=>{
  const raw='{"kind":"narration","text":"末尾落款"米特尔谷尼"并盖上私印。"}\n{"kind":"speech","speakerId":"g","text":"他说"拿好"。"}'
  for(const streaming of [true,false])expect(sceneDialogueSegments(raw,[{id:'g',name:'谷尼'}],undefined,streaming)).toEqual([
    {kind:'narration',text:'末尾落款"米特尔谷尼"并盖上私印。'},
    {kind:'speech',speakerId:'g',speakerName:'谷尼',text:'他说"拿好"。'}
  ])
})
