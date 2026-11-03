import {inject,type InjectionKey,type Ref} from 'vue'
export interface CharacterAppearanceContext {avatars:Ref<Record<string,string>>;open:(entityId?:string)=>void}
export const characterAppearanceKey:InjectionKey<CharacterAppearanceContext>=Symbol('world-character-appearance')
export const accountAppearanceKey:InjectionKey<{src:Ref<string>;name:Ref<string>}>=Symbol('account-chat-appearance')
export function useCharacterAppearance(){return inject(characterAppearanceKey,null)}
