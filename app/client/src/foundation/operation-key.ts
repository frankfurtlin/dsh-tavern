import {accountId} from '../platform/bridge'
const prefix='ainovel.product.operation.'
export function operationKey(name:string){const key=prefix+accountId()+'.'+name;let value=sessionStorage.getItem(key);if(!value){value=crypto.randomUUID();sessionStorage.setItem(key,value)}return value}
export function completeOperation(name:string){sessionStorage.removeItem(prefix+accountId()+'.'+name)}
export function clearProductOperations(){for(let i=sessionStorage.length-1;i>=0;i--){const key=sessionStorage.key(i);if(key?.startsWith(prefix))sessionStorage.removeItem(key)}}
