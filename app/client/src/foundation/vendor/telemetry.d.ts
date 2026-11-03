export function sanitizeMetadata(value: unknown, depth?: number): unknown
export function createTracker(options: Record<string, unknown>): {start():void;stop():void;clear():void;flush():Promise<void>;track(type:string,name:string,fields?:Record<string,unknown>):void;attachRouter(router:unknown):unknown;pending():number}
