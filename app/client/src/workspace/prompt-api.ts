import {api} from '../api'
export interface AuthorPrompt {id:string;title:string;description:string;language:string;content:string;status:'active'|'archived';version:number;characters:number;utf8Bytes:number;updatedAt:string;visibility:'private'}
export interface PromptSelection {version:number;promptIds:string[];characters?:number;utf8Bytes?:number}
export const promptApi={list:()=>api<{items:AuthorPrompt[]}>('/product/prompts'),selection:(id:string)=>api<PromptSelection>('/product/prompts/world/'+id),select:(id:string,value:PromptSelection)=>api<PromptSelection>('/product/prompts/world/'+id,{method:'PUT',body:{expectedVersion:value.version,promptIds:value.promptIds}})}
