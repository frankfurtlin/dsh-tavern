import type {WorldEntity} from './types'
import type {DialogueSegment} from './scene-dialogue'
export interface ScenePoint {id:string;name:string;direction:string;yaw:number;pitch:number;imageBound?:boolean;x?:number;y?:number;worldX?:number;worldZ?:number;source?:{entityId?:string;quote?:string};kind?:'place'|'exit';portalId?:string|null}
export interface CharacterArt {id:string;entityId:string;status:SceneView['status'];errorCode:string|null;imageUrl:string|null;width?:number;height?:number;presentation?:'solid'|'spirit'}
export interface SceneView {
  id:string;worldId:string;lineId:string;status:'queued'|'running'|'saving'|'ready'|'failed'|'unknown';sceneHash:string;generation:number;
  errorCode:string|null;imageUrl:string|null;checkpointId:string;pointId:string;timeLabel:string;
  progress?:{startedAt:string|null;elapsedSeconds:number;phase:'environment-image'|'environment-review'|'actor-image'|'actor-review'|'complete'|'stopped'|'failed';label:string;canStop:boolean;slow:boolean;inspectionCalls:number;inspectionCallLimit?:number};
  recovery?:{stage:'inspection'|'generation';imageRetained:boolean;canInspect:boolean;responseRejected?:boolean;httpStatus?:number;rejectionCategory?:string;rejectionCode?:string;usageState?:string;retryAt?:string;retryAfterSeconds?:number;savedImages?:{sceneId:string;generation:number;createdAt:string;previewUrl:string}[]};
  preview?:{url:string;sourceHash:string;revision:string;phase:'environment'|'characters';readOnly:true;width:number;height:number};
  invalidPreviewHashes?:string[];
  image?:{sha256?:string;anchorStatus?:'queued'|'repairing'|'incomplete'|'complete'|'unavailable';bindingTaskId?:string;anchorError?:string;missingCharacterIds?:string[];missingExitIds?:string[];missingFixtureIds?:string[]};
  descriptor:{placeId:string;placeName:string;description:string;pointId:string;pointLabel:string;lighting:string;viewpoint?:{initialYaw:number;x:number;z:number}};
  layout:{version:number;nodes:ScenePoint[];links:{from:string;to:string}[]};hotspots:Record<string,{yaw:number;pitch:number;distance?:number;height?:number}>;
  points:ScenePoint[];exits:ScenePoint[];characters:WorldEntity[];items:WorldEntity[];
  conversations?:{turnId:string;checkpointId:string;partnerId:string;partnerName:string;user:string;reply:string;segments?:DialogueSegment[]}[];
  characterArt?:Record<string,CharacterArt>;
  characterPresentation?:Record<string,{mode:'embedded'|'voice'|'offscreen';yaw?:number;pitch?:number;labelYaw?:number;labelLeftYaw?:number;labelPitch?:number}>;
  responses?:{id:string;taskId?:string;text:string;draft:boolean;partnerId?:string;user?:string;segments?:DialogueSegment[]}[];
}
