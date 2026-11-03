import {ContractError} from '../domain/local-contract'
/** Hold an ambiguous credential prefix across chunks before publishing it. */
export class WritingSecretFilter {
 private pending='';private prefix:number[]=[]
 constructor(private secret:string){for(let i=1,j=0;i<secret.length;i++){while(j&&secret[i]!==secret[j])j=this.prefix[j-1]||0;if(secret[i]===secret[j])j++;this.prefix[i]=j}}
 check(value:string){if(this.secret&&value.includes(this.secret))throw new ContractError('WRITING_SECRET_ECHO','供应商回显了通道凭据，已拒绝保存；原请求不会重发')}
 feed(text:string){const combined=this.pending+text;if(!this.secret)return combined;this.check(combined);let j=0;for(let i=0;i<combined.length;i++){const char=combined[i]!;while(j&&char!==this.secret[j])j=this.prefix[j-1]||0;if(char===this.secret[j])j++}this.pending=j?combined.slice(-j):'';return combined.slice(0,combined.length-j)}
 finish(){const text=this.pending;this.pending='';return text}
}
