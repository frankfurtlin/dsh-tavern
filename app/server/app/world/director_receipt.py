"""One budgeted repair of a received proposal; never infer inventory from prose."""
import re
from pydantic import ValidationError
from copy import deepcopy
from ..errors import Problem
from ..models import uid
from .schemas import DirectorOutput
from .state import parse_json
from .turn_contract import reference_wire, resolve_plan

DOCUMENT = re.compile(r'鉴定(?:书|说明|报告)|收据|借据|通行证|凭据抄本|证明书|契约|抄件|证书')
CREATE = re.compile(r'出具|写好|写明|写下|签发|开具|抄好|抄录|署名|落款|盖章')
DELIVER = re.compile(r'交予|交给|递给|递到|交到|转交|移交|交付')


def missing_document_receipts(plan, state, definition):
    """Check explicit completed deliveries, including transfers of an old document."""
    registered = {e['id']: e for e in definition.get('entities', [])}
    added = {e['id']: e for e in plan.get('additions', []) if e.get('type') == 'item'}
    items = {**registered, **added}
    people = [e for e in registered.values() if e.get('type') == 'character']
    texts = [plan.get('publicOutcome', '')] + [e.get('description', '') for e in plan.get('events', [])]
    issues = []
    for text in texts:
        for clause in re.split(r'[。！？\n]', text):
            docs = DOCUMENT.findall(clause)
            if not docs or not DELIVER.search(clause):continue
            if re.search(r'尚未|还没|没有|(?:若|如果|待到|准备|打算|将会|可以|请求|要求).{0,35}(?:交予|交给|递给|递到|交到|转交|移交|交付)', clause):continue
            # Named recipient wins over the default reader. Ambiguous deliveries
            # are left to the normal director/narrative validation, not guessed.
            recipients = [e['id'] for e in people if re.search(
                r'(?:交予|交给|递给|递到|交到|转交|移交|交付)(?:了|给)?\s*' + re.escape(e['name']), clause)]
            if not recipients and re.search(r'(?:交予|交给|递给|递到|交到|交付)(?:了)?(?:你|玩家|手中|手里)', clause):recipients = [state['playerId']]
            if not recipients:continue
            for doc in set(docs):
                matching = {key for key, item in items.items() if item.get('type') == 'item' and
                            (doc in item['name'] or (doc.startswith('鉴定') and '鉴定' in item['name']))}
                for recipient in set(recipients):
                    deliveries = [t for t in plan.get('transfers', []) if t.get('itemId') in matching and t.get('toId') == recipient and t.get('quantity', 0) > 0]
                    held = any(state.get('entities', {}).get(recipient, {}).get('inventory', {}).get(item, 0) > 0 for item in matching)
                    if not deliveries and (CREATE.search(clause) or not held):
                        issues.append(f'正文宣告{doc}已交付给{recipient}，但没有该文书的实际物品交割。新文书须登记source=game的item并transfers入包；原有文书须由真实持有人交付。')
    return list(dict.fromkeys(issues))


def director_proposal(task_id, index, data, definition, base, limit, call, saved=None, validate=None):
    """Only complete, received responses can trigger one correction call."""
    saved = deepcopy(saved or {})
    payload = {**data, '_structured': True}
    wire, references = reference_wire(payload, definition)
    issues = []
    invalid = None
    if saved:
        plan = saved['plan']
    else:
        response = call(task_id, f'analysis:director:{index}', 'world_director', wire, limit)
        if response.get('finishReason') not in (None, 'stop'):
            raise Problem('WORLD_OUTPUT_INCOMPLETE', '行动结果尚未完整返回')
        try:
            plan = resolve_plan(parse_json(response['text'], DirectorOutput), references)
        except Problem as error:
            if error.code != 'WORLD_OUTPUT_INVALID':raise
            plan = None
            invalid = response['text']
            issues = ['上一份已收到的响应未符合DirectorOutput格式。请完整重新输出紧凑JSON，必填publicOutcome不得为空；禁止null占位条目。updates每条只允许entityId、field、value、reason、visibleTo，不允许operation或append。known更新必须是完整已知ID列表，也可以省略玩家known更新，仅用learned及真实知识事件建立新知识。新物品交接必须引用additions里的同一个NEW_ID。不得补猜任何尚未收到的事实。']
            try:
                DirectorOutput.model_validate_json(invalid)
            except ValidationError as schema_error:
                issues.extend('.'.join(map(str, e['loc'])) + ': ' + e['msg']
                              for e in schema_error.errors(include_input=False, include_url=False)[:8])
    if plan is not None:
        issues = missing_document_receipts(plan, base, definition)
        if validate:
            try:validate(plan)
            except Problem as error:
                # Death policies, locked routes and ability requirements are
                # world rules, not malformed output. Keep their normal rejection
                # and draft path without spending a correction call.
                if error.code in {'WORLD_ENTITY_INVALID','WORLD_INVENTORY_INVALID','WORLD_RESOURCE_INVALID','WORLD_RELATION_INVALID','WORLD_STATE_INVALID','WORLD_LORE_INVALID','WORLD_KNOWLEDGE_INVALID','WORLD_OUTPUT_INVALID','WORLD_SETTLEMENT_INVALID'}:
                    issues.append(f'{error.code}: {error.message}。修正真实状态方案，既有物品沿用原ID；确为另一份新文书时创建独立NEW_物品并使用与原件不同的准确名称。不得重建既有实体或凭空授予物品。')
    attempted = saved.get('repairAttempted', False)
    if issues and not attempted:
        correction = {**payload, 'repairIssues': issues, 'previousProposal': plan if plan is not None else invalid}
        correction['repairRule'] = '仅修复已收到方案的结构、真实交割与登记善后协议。保持原行动授权、实际持有人、资源数量与知识边界。options.kind只允许action/dialogue/observe/wait，移动建议使用action，禁止travel。所有scriptGuide.protectedFields不得出现在updates，即使描述中间调查状态也不允许；需要办理时仅用已有transitionIds。不替玩家选结局、不补办整组善后。新文书必须创建独立item并实际交付；不得伪造收费、消费、任务成功或人物同行。输出完整紧凑DirectorOutput JSON。'
        corrected_wire, corrected_references = reference_wire(correction, definition)
        response = call(task_id, f'analysis:director-repair:{index}', 'world_director', corrected_wire, limit)
        if response.get('finishReason') not in (None, 'stop'):
            raise Problem('WORLD_OUTPUT_INCOMPLETE', '行动修正结果尚未完整返回')
        plan = resolve_plan(parse_json(response['text'], DirectorOutput), corrected_references)
        attempted = True
    return {**saved, 'plan': plan, 'checkpointId': saved.get('checkpointId') or uid(), 'repairAttempted': attempted}
