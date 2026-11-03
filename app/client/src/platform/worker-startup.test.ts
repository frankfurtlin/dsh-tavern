import {it,expect,vi} from 'vitest'
import {prepareWorker} from './worker-startup'
const options={release:'current',origin:'https://reader.test',online:true}
function fixture(url='https://reader.test/sw.js?release=current'){
 const active={scriptURL:url,state:'activated'},registration={active,update:vi.fn().mockResolvedValue(undefined)}
 const container={controller:active,getRegistration:vi.fn().mockResolvedValue(registration),register:vi.fn().mockResolvedValue(registration)}
 return {registration,container,call:()=>prepareWorker(container as unknown as ServiceWorkerContainer,options)}
}
it('does not block a current authenticated controller on a stalled registration job',async()=>{
 const f=fixture();f.container.register.mockReturnValue(new Promise(()=>{}));const result=await f.call();expect(result.registration).toBe(f.registration);expect(f.container.register).not.toHaveBeenCalled();expect(f.registration.update).not.toHaveBeenCalled();await result.checkForUpdate?.();expect(f.registration.update).toHaveBeenCalledOnce()
})
it('registers and waits on first installation without making a duplicate update request',async()=>{
 const f=fixture();f.container.getRegistration.mockResolvedValue(undefined);const result=await f.call();expect(result.registration).toBe(f.registration);expect(f.container.register).toHaveBeenCalledWith('/sw.js?release=current',{updateViaCache:'none'});expect(f.registration.update).not.toHaveBeenCalled()
})
it('keeps a release change on the existing installation and handshake path',async()=>{
 const f=fixture('https://reader.test/sw.js?release=older');await f.call();expect(f.container.register).toHaveBeenCalledOnce()
})
it('does not skip registration for an uncontrolled document',async()=>{
 const f=fixture();f.container.controller=null as any;await f.call();expect(f.container.register).toHaveBeenCalledOnce()
})
it('retains a controlled offline shell without a network update',async()=>{
 const f=fixture('https://reader.test/sw.js?release=older');await prepareWorker(f.container as unknown as ServiceWorkerContainer,{...options,online:false});expect(f.container.register).not.toHaveBeenCalled()
})
it('fails closed if installation fails and there is no active controller',async()=>{
 const f=fixture();f.container.controller=null as any;f.container.getRegistration.mockResolvedValue(undefined);f.container.register.mockRejectedValue(new Error('unavailable'));await expect(f.call()).rejects.toThrow('unavailable')
})
