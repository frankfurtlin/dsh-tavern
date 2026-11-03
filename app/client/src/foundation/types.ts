export interface ProductAccount {id:string;username:string;role:'USER'|'ADMIN';displayName:string;language:'zh-CN'|'en';avatarId:string|null}
export interface CatalogProduct {id:string;sku:string;title:string;kind:'credits'|'member';amountMinor:number;currency:string;credits:number;memberDays:number;dailyCredits:number}
export interface ProductOrder {id:string;product:CatalogProduct;state:string;provider:string;environment:string;checkoutUrl:string|null;createdAt:string;paidAt:string|null;expiresAt:string}
export interface PaymentOptions {enabled:boolean;provider:string;environment:string;reason:string}
export interface ProductWallet {unlimitedUsage?:boolean;available:number;frozen:number;billingEnabled:boolean;payment:PaymentOptions;buckets:Record<string,{available:number;frozen:number}>;memberships:{id:string;title:string;startsAt:string;endsAt:string;dailyCredits:number;source:string}[];ledger:{id:string;kind:string;amount:number;reference:string;createdAt:string;details:Record<string,unknown>}[];holds:{id:string;taskId:string;operation:string;reserved:number;state:string}[];orders:ProductOrder[]}
export interface KnowledgeDoc {id:string;title:string;objectId:string;sourceVersion:string;scope:string;chunks:number}
export interface KnowledgeHit {text:string;blockId:string;documentId?:string;title?:string;sourceVersion:string;scope:string;score:number}
