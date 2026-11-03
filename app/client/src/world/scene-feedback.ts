import type {SceneView} from './scene-types'

export function sceneCooldownRemaining(scene:SceneView|null,now=Date.now()){
  const until=Date.parse(scene?.recovery?.retryAt||'')
  return Number.isFinite(until)?Math.max(0,Math.ceil((until-now)/1000)):0
}

export function sceneFeedback(scene:SceneView|null,loading:boolean,now=Date.now()){ 
  const r=scene?.recovery
  const remaining=sceneCooldownRemaining(scene,now)
  if(!loading&&remaining>0)return {title:r?.imageRetained?'图片已保存，等待校验服务恢复':'等待场景校验服务恢复',description:`服务要求等待，还需 ${Math.floor(remaining/60)} 分 ${remaining%60} 秒。到期后可手动继续；等待期间不会自动发起新请求，可以先继续文字游玩。`}
  if(scene?.errorCode==='SCENE_STOPPED')return {title:'场景生成已停止',description:'已保存收到的图片与请求记录，主线进度不变。可继续文字游玩，也可复用图片继续检查；已发出的调用仍会保留实际用量。'}
  if(loading)return {title:r?.imageRetained?'正在校验并补全场景':'正在绘制这一刻',description:r?.imageRetained?'已保存的图片会继续使用，确认环境后即可先行环顾；人物与交互会在校验后显示。':'环境确认后即可先行环顾，人物与交互完成后会自动显示。'}
  if(r?.stage==='inspection'&&r.imageRetained){
    if(r.httpStatus===429||r.rejectionCategory==='rate_limit')return {title:'图片已保存，校验服务繁忙',description:'当前请求较多，请稍后复用图片继续核验，或先继续文字游玩。已发出的请求记录会保留，不会自动重新出图。'}
    if(r.httpStatus===402||r.rejectionCategory==='quota')return {title:'图片已保存，校验额度不足',description:'平台的视觉校验额度暂不可用，请联系管理员处理后复用图片继续核验。可先继续文字游玩，无需重新出图。'}
    const reason=r.rejectionCategory==='model_unavailable'?'当前视觉模型暂不可用':r.httpStatus?`视觉校验服务返回 HTTP ${r.httpStatus}`:'视觉校验尚未完成'
    return {title:'图片已保存，场景尚未完成',description:reason+'。可复用已保存图片继续完成场景，或先继续文字游玩。'}
  }
  if(scene?.status==='unknown')return {title:'图片生成结果尚未确认',description:'暂未确认收到图片。当前请求已保留，不会自动重复出图；可先继续文字游玩。'}
  if(scene?.status==='ready'&&scene.image?.anchorStatus&&scene.image.anchorStatus!=='complete')return {title:'图片已保存，交互尚待确认',description:'可以继续环顾已保存画面；人物和通路确认后再开放交互，也可先继续文字游玩。'}
  if(scene?.status==='failed')return {title:'场景生成未完成',description:'这次未能完成场景。平台会保留已有图片与请求记录，可以继续完成场景，或先继续文字游玩。'}
  return {title:'场景暂未展开',description:''}
}
