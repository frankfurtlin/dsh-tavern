import {beforeEach,it,expect,vi} from 'vitest'
vi.mock('../api',()=>({api:vi.fn(),authorizationHeaders:vi.fn(()=>({})),authGeneration:()=>0,refreshAuth:vi.fn(),ApiError:class extends Error{}}))
vi.mock('../platform/bridge',()=>({backendBase:()=>''}))
import {api} from '../api'
import {exportWorldCharacterCard} from './api'
const mocked=vi.mocked(api)
beforeEach(()=>{mocked.mockReset();vi.unstubAllGlobals()})

it('exports a world character as a card through the world namespace',async()=>{
  mocked.mockResolvedValueOnce({cardId:'card-9'})
  await expect(exportWorldCharacterCard('w1','ch1')).resolves.toEqual({cardId:'card-9'})
  expect(mocked).toHaveBeenCalledWith('/worlds/w1/character-cards',{method:'POST',body:{characterId:'ch1'},namespace:'world'})
})
