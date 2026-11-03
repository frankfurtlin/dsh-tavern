export function taskError(code:string,state:string,hasText=true){
  if(state==='cancelled')return hasText?'已停止，草稿已保留。':'已停止，本轮没有收到正文。'
  const detail=({
    CONTEXT_BUDGET_EXCEEDED:'本轮必要上下文超过模型窗口，请缩短行动或检查模型的上下文设置。模型尚未收到本轮请求。',
    BUDGET_NOT_AUTHORIZED:'模型额度不足，本轮尚未发送。调整模型额度后可以重新提交。',
    WORLD_LOCATION_INVALID:'模型给出的目的地不属于已登记地点，请从地图选择出口重试。',
    WORLD_RESOURCE_INVALID:'模型给出的付款或资源变更无效，请重新提交这次交易。',
    WORLD_INVENTORY_INVALID:'本轮物品来源或数量未通过核对，请检查背包后重试。',
    WORLD_OUTPUT_INVALID:'模型返回的行动结果格式不完整，请重新提交。',
    WORLD_NARRATIVE_CONFLICT:'这次正文与实际物品、人物或任务状态不一致，请重试。',
  } as Record<string,string>)[code]
  if(detail)return detail+(hasText?' 已收到的正文可在草稿中查看，世界状态未推进。':' 本轮没有可保留的正文，世界状态未推进。')
  if(!hasText&&code==='PROVIDER_STATUS_UNKNOWN')return '模型响应暂时无法确认，目前没有收到正文。请查看这次请求的状态，世界状态未推进。'
  if(!hasText&&['WORLD_ROUTE_RULE','WORLD_DECISION_REQUIRED','WORLD_RESOURCE_RULE','WORLD_PLAYER_IDENTITY_CONFLICT'].includes(code))return ({WORLD_ROUTE_RULE:'这条路线暂时无法通行，请从地图选择已有出口。',WORLD_DECISION_REQUIRED:'途中有需要决定的事情，请先处理当前选择。',WORLD_RESOURCE_RULE:'这次行动的资源结算未通过检查，请检查持有物品和资源。',WORLD_PLAYER_IDENTITY_CONFLICT:'这次回复混淆了你扮演的角色。'} as Record<string,string>)[code]+' 本轮没有收到正文，世界状态未推进。'
  return ({
    PROVIDER_STATUS_UNKNOWN:'模型响应暂时无法确认。已收到的正文会保留，请查看这次请求。',
    PROVIDER_CONNECTION_FAILED:'暂时连接不上模型，当前步骤尚未发送。已有进度已保留，请稍后再试。',
    PROVIDER_HOST_UNAVAILABLE:'暂时无法连接模型地址，当前步骤尚未发送。请稍后再试。',
    WORLD_ROUTE_RULE:'这条路线暂时无法通行。已收到的正文已保留，请选择已有出口，或先解决路上的阻碍。',
    WORLD_DECISION_REQUIRED:'途中有需要你决定的事情。这次正文已保留，请先处理当前选择。',
    WORLD_RESOURCE_RULE:'这次行动的资源结算未通过检查，正文已保留。请查看持有物品和资源后再行动。',
    WORLD_PLAYER_IDENTITY_CONFLICT:'这次回复混淆了你扮演的角色。正文已保留为草稿，角色身份和世界状态没有改变。',
  } as Record<string,string>)[code]||(hasText?'这次未完成，正文已作为草稿保留。':'这次未完成，没有收到正文，世界状态未推进，可以重新提交。')
}
