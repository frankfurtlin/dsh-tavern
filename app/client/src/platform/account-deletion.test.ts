import {describe,it,expect} from 'vitest'
import {completeAccountDeletion} from './account-deletion'

const receipt={id:'current-response',base:''}
describe('accepted account deletion completion',()=>{
  it('revokes the same account after navigation, before native persistence finishes',async()=>{
    let mounted=true,owner='same-owner',written=false,release!:(value:{localSaved:boolean;nativeSaved:boolean})=>void
    const pending=new Promise<{localSaved:boolean;nativeSaved:boolean}>(resolve=>{release=resolve}),order:string[]=[]
    mounted=false
    const done=completeAccountDeletion(receipt,{sameAccount:()=>owner==='same-owner',native:true,
      saveReceipt:value=>{expect(value).toEqual(receipt);written=true;order.push('local');return pending},
      clearPrivate:async()=>{expect(written).toBe(true);owner='';order.push('revoke')}
    })
    expect(mounted).toBe(false);expect(owner).toBe('');expect(order).toEqual(['local','revoke'])
    release({localSaved:true,nativeSaved:true});expect(await done).toEqual({cleared:true,receiptStatus:'saved',cleanupFailed:false})
  })
  it('cannot revoke another account or overwrite its receipt when the response is stale',async()=>{
    let writes=0,clears=0
    const result=await completeAccountDeletion(receipt,{sameAccount:()=>false,native:true,
      saveReceipt:async()=>{writes++;return{localSaved:true,nativeSaved:true}},clearPrivate:async()=>{clears++}
    })
    expect(result.cleared).toBe(false);expect(writes).toBe(0);expect(clears).toBe(0)
  })
  it('still clears private data when both receipt copies fail',async()=>{
    let clears=0
    expect(await completeAccountDeletion(receipt,{sameAccount:()=>true,native:true,
      saveReceipt:async()=>{throw new Error('disk unavailable')},clearPrivate:async()=>{clears++}
    })).toEqual({cleared:true,receiptStatus:'failed',cleanupFailed:false})
    expect(clears).toBe(1)
  })
  it('reports local-only persistence accurately without delaying revocation',async()=>{
    let clears=0
    expect(await completeAccountDeletion(receipt,{sameAccount:()=>true,native:true,
      saveReceipt:async()=>({localSaved:true,nativeSaved:false}),clearPrivate:async()=>{clears++}
    })).toEqual({cleared:true,receiptStatus:'local',cleanupFailed:false})
    expect(clears).toBe(1)
  })
  it('keeps a successful same-origin receipt and exposes a private cleanup failure after revocation',async()=>{
    let persisted=false
    expect(await completeAccountDeletion(receipt,{sameAccount:()=>true,native:false,
      saveReceipt:async()=>{persisted=true;return{localSaved:true,nativeSaved:false}},clearPrivate:async()=>{throw new Error('credential cleanup failed')}
    })).toEqual({cleared:true,receiptStatus:'saved',cleanupFailed:true})
    expect(persisted).toBe(true)
  })
})
