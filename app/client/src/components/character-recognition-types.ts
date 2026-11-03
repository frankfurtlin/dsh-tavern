import type {Anchor} from '../api/types'
export interface Recognition {version:number;aliases:string[];identityStatus:'confirmed'|'uncertain';reason:string|null;evidence:{id:string;quote:string;anchor:Anchor}[];selectedEvidenceIds?:string[];scope:{bookId:string;readerBranchId:string;revisionId:string;anchor:Anchor};history:{version:number;displayName:string;aliases:string[];identityStatus:string;reason:string;createdAt:string}[]}
export interface RecognizedCard {id:string;name:string;available:boolean;recognition?:Recognition}
