import {describe,expect,it} from 'vitest'
import {buildChatRows,clockOf,dateKeyOf,dayLabel,splitSegments} from './chat-format'

describe('splitSegments',()=>{
  it('returns no segments for empty text',()=>{
    expect(splitSegments('')).toEqual([])
  })

  it('keeps plain text as a single segment',()=>{
    expect(splitSegments('雨还在下。')).toEqual([{kind:'text',text:'雨还在下。'}])
  })

  it('marks single-asterisk actions as act',()=>{
    expect(splitSegments('*推门*')).toEqual([{kind:'act',text:'推门'}])
  })

  it('marks double-asterisk emphasis as strong and merges the text around',()=>{
    expect(splitSegments('风声**很轻**，雨声更密。')).toEqual([
      {kind:'text',text:'风声'},
      {kind:'strong',text:'很轻'},
      {kind:'text',text:'，雨声更密。'},
    ])
  })

  it('keeps an unclosed action marker as literal text',()=>{
    expect(splitSegments('*推门')).toEqual([{kind:'text',text:'*推门'}])
  })

  it('does not treat cross-line markers as actions',()=>{
    expect(splitSegments('*推\n门*')).toEqual([{kind:'text',text:'*推\n门*'}])
  })

  it('rejects actions longer than 200 characters',()=>{
    const long='雨'.repeat(201)
    expect(splitSegments(`*${long}*`)).toEqual([{kind:'text',text:`*${long}*`}])
  })

  it('does not confuse emphasis markers with the bold opener',()=>{
    expect(splitSegments('**强调**和*动作*')).toEqual([
      {kind:'strong',text:'强调'},
      {kind:'text',text:'和'},
      {kind:'act',text:'动作'},
    ])
  })
})

describe('date helpers',()=>{
  it('parses valid timestamps and rejects invalid ones',()=>{
    expect(dateKeyOf('2026-11-19T08:00:00+08:00')).toBe('2026-11-19')
    expect(dateKeyOf('')).toBeNull()
    expect(dateKeyOf('not-a-date')).toBeNull()
  })

  it('labels today, yesterday and other dates',()=>{
    const now=new Date('2026-11-19T12:00:00+08:00')
    expect(dayLabel('2026-11-19',now)).toBe('今天')
    expect(dayLabel('2026-11-18',now)).toBe('昨天')
    expect(dayLabel('2026-03-02',now)).toBe('3月2日')
    expect(dayLabel('2025-12-31',now)).toBe('2025年12月31日')
  })

  it('formats clock time in local hours and minutes',()=>{
    expect(clockOf(new Date(2026,10,19,8,5).toISOString())).toBe('08:05')
    expect(clockOf(null)).toBeNull()
  })
})

describe('buildChatRows',()=>{
  const now=new Date('2026-11-19T12:00:00+08:00')
  const at=(day:number,hour:number)=>new Date(2026,10,day,hour,0).toISOString()

  it('inserts a day row and keeps message order',()=>{
    const rows=buildChatRows([
      {id:'a',role:'user',createdAt:at(19,8)},
      {id:'b',role:'assistant',createdAt:at(19,8)},
    ],{now})
    expect(rows.map(row=>row.kind)).toEqual(['day','message','message'])
    expect(rows[0]).toMatchObject({label:'今天'})
  })

  it('marks consecutive messages from the same speaker as compact',()=>{
    const rows=buildChatRows([
      {id:'a',role:'user',createdAt:at(19,8)},
      {id:'b',role:'user',createdAt:at(19,9)},
    ],{now})
    const messages=rows.filter(row=>row.kind==='message')
    expect(messages.map(row=>row.kind==='message'&&row.compact)).toEqual([false,true])
  })

  it('shows the speaker only for the first message of a new group speaker',()=>{
    const rows=buildChatRows([
      {id:'a',role:'assistant',speakerCardId:'c1',speakerName:'林渡',createdAt:at(19,8)},
      {id:'b',role:'assistant',speakerCardId:'c1',speakerName:'林渡',createdAt:at(19,9)},
      {id:'c',role:'assistant',speakerCardId:'c2',speakerName:'阿青',createdAt:at(19,10)},
    ],{now,isGroup:true})
    const messages=rows.filter(row=>row.kind==='message')
    expect(messages.map(row=>row.kind==='message'&&row.showSpeaker)).toEqual([true,false,true])
  })

  it('never shows a speaker in a single chat and carries missing timestamps forward',()=>{
    const rows=buildChatRows([
      {id:'a',role:'assistant',speakerCardId:'c1',speakerName:'林渡',createdAt:at(19,8)},
      {id:'b',role:'assistant',speakerCardId:'c1',speakerName:'林渡'},
    ],{now})
    const messages=rows.filter(row=>row.kind==='message')
    expect(messages.every(row=>row.kind==='message'&&!row.showSpeaker)).toBe(true)
    expect(rows.filter(row=>row.kind==='day').length).toBe(1)
  })

  it('keeps the time label on the message row',()=>{
    const rows=buildChatRows([{id:'a',role:'user',createdAt:at(19,20)}],{now})
    const message=rows.find(row=>row.kind==='message')
    expect(message).toMatchObject({time:'20:00'})
  })
})
