import type {WorldEntity} from './types'
import type {DialogueSegment} from './scene-dialogue'
import type {ScenePoint} from './scene-types'
export const expressionLabels={neutral:'自然',smile:'微笑',serious:'认真',surprised:'惊讶'} as const
export type PortraitExpression=keyof typeof expressionLabels
export interface StageCharacter extends WorldEntity {visibility:'visible'|'voice';expressions:Partial<Record<PortraitExpression,string>>;artworkId:string|null}
export interface PortraitSceneView {
 worldId:string;lineId:string;checkpointId:string;placeName:string;description:string;timeLabel:string;
 characters:StageCharacter[];background:{url:string;sha256:string;readOnly:true;initialYaw:number}|null;
 conversations:{turnId:string;partnerId:string;partnerName:string;user:string;reply:string;segments?:DialogueSegment[]}[];
 responses:{id:string;taskId?:string;text:string;draft:boolean;partnerId?:string;user?:string}[];
 placeId:string;exits:ScenePoint[]
}
export function stageActors(people:StageCharacter[],selectedId:string):StageCharacter[]{
 const target=people.find(p=>p.id===selectedId)
 if(!target||target.visibility!=='visible'||!target.expressions.neutral)return []
 // Being present in the scene is not participation in a private conversation.
 return [target]
}
export function stageSpeaker(parts:DialogueSegment[],people:StageCharacter[],pendingTarget?:string):string|null{
 const allowed=new Set(people.map(p=>p.id)),speech=parts.filter(p=>p.kind==='speech')
 if(speech.length){const last=speech.at(-1);return last?.speakerId&&allowed.has(last.speakerId)?last.speakerId:null}
 return pendingTarget&&allowed.has(pendingTarget)?pendingTarget:null
}
