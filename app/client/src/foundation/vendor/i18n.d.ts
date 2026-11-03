export function createLocaleController(options:Record<string,unknown>): {ready:Promise<LocaleResult>;switchLocale(code:string):Promise<LocaleResult>;current():string}
export interface LocaleResult {locale:string;messages:Record<string,string>;applied:boolean}
