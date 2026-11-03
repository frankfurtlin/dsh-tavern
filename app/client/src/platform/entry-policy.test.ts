import {it,expect} from 'vitest'
import {deviceEntryDestination} from './entry-policy'
it('sends a new device to the account login and an authenticated device to the app',()=>{
 expect(deviceEntryDestination(false,false)).toBe('/login')
 expect(deviceEntryDestination(false,true)).toBe('/explore')
})
it('keeps historical encrypted data accessible without requiring a new account or vault',()=>{
 expect(deviceEntryDestination(true,false)).toBeNull()
 expect(deviceEntryDestination(true,true)).toBeNull()
})
