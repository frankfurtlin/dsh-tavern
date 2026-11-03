import type {WorldEntity} from './types'
// A recognizable target prefill survives input caching and reloads. Changing
// the target replaces that prefill once, preserving the reader's actual words.
export function targetDraft(input:string,target:WorldEntity,people:WorldEntity[]){
  let body=input
  for(const person of people.filter(e=>e.type==='character')){
    const prefixes=[`我对${person.name}说：“`,`我想寻找${person.name}。`]
    const previous=prefixes.find(prefix=>body.startsWith(prefix))
    if(previous){body=body.slice(previous.length);break}
  }
  return (target.nearby?`我对${target.name}说：“`:`我想寻找${target.name}。`)+body
}
