/** Reuse a verified current controller without waiting for a network update job. */
export async function prepareWorker(container:ServiceWorkerContainer,options:{release?:string;origin:string;online:boolean}){
 const script='/sw.js'+(options.release?'?release='+encodeURIComponent(options.release):'')
 let registration=await container.getRegistration('/')
 if(registration?.active&&registration.active===container.controller&&registration.active.scriptURL===new URL(script,options.origin).href){
  const current=registration
  return {registration,checkForUpdate:()=>current.update().then(()=>{})}
 }
 if(!registration||options.online){
  try{registration=await container.register(script,{updateViaCache:'none'})}
  catch(error){if(!registration?.active||!container.controller)throw error}
 }
 if(!registration)throw new Error('Service worker unavailable')
 return {registration,checkForUpdate:undefined}
}
