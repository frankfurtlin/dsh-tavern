import {it,expect} from 'vitest'
import {proposalPreviewFields,cardPreviewLabels} from './card-preview'
it('shows readable card fields without exposing duplicate container metadata in the main preview',()=>{
 const card={name:'陆岚',description:'修书师',firstMessage:'晚上好',tags:[],personality:'',identityId:'private',source:{unknown:'KEEP'},data:{description:'修书师'},container:{kind:'png'}}
 expect(proposalPreviewFields('card',null,card)).toEqual(['name','description','firstMessage'])
 expect(proposalPreviewFields('card',card,{...card,firstMessage:''})).toEqual(['firstMessage'])
 expect(cardPreviewLabels.firstMessage).toBe('开场白')
 expect(proposalPreviewFields('note',null,{text:'原文笔记',anchor:{id:'p'}})).toEqual(['text','anchor'])
})
