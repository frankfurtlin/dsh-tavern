import {it,expect} from 'vitest'
import {validateMainReadingLine,mainLineReferences,validateMainLineRelations,mainLineKey} from './main-line'
const v={assetType:'main-reading-line',storyId:'book',branchId:'alternative'} as const
it('keeps exact story and branch dependency references',()=>{expect(()=>validateMainReadingLine(v,mainLineKey('book'))).not.toThrow();expect(mainLineReferences(v)).toEqual([{id:'book',kind:'story'},{id:'alternative',kind:'branch'}])})
it('allows switching back to original without requiring a branch',()=>{const value={...v,branchId:null};expect(()=>validateMainLineRelations(value,id=>id==='book'?{kind:'story',value:{}}:undefined)).not.toThrow();expect(mainLineReferences(value)).toHaveLength(1)})
it('rejects cross-book preferences even if the branch exists',()=>{expect(()=>validateMainLineRelations(v,id=>id==='book'?{kind:'story',value:{}}:{kind:'branch',value:{storyId:'another'}})).toThrow()})
it('rejects wrong setting key, missing/null story, and unrecognized fields',()=>{for(const [value,id] of [[v,mainLineKey('another')],[{...v,storyId:null},undefined],[{...v,branchId:''},undefined],[{...v,promoteSource:true},undefined]] as const)expect(()=>validateMainReadingLine(value,id)).toThrow()})
