/** Linear-time exact byte matching; untrusted media cannot amplify long credential scans. */
export function includesSecret(bytes:Uint8Array,pattern:Uint8Array):boolean {
  if(!pattern.length||pattern.length>bytes.length)return false
  const prefix=new Uint32Array(pattern.length)
  for(let i=1,j=0;i<pattern.length;i++){while(j&&pattern[i]!==pattern[j])j=prefix[j-1]!;if(pattern[i]===pattern[j])j++;prefix[i]=j}
  for(let i=0,j=0;i<bytes.length;i++){while(j&&bytes[i]!==pattern[j])j=prefix[j-1]!;if(bytes[i]===pattern[j])j++;if(j===pattern.length)return true}
  return false
}
