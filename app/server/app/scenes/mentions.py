"""Conservative current, public mentions shared by props and scene fixtures."""
import re

PAST=('昨天','昨日','昨晚','从前','曾经','以前','当年','往日','回忆','梦见','记忆里','记得')
ABSENT_BEFORE=('没有','没见','未见','不见','找不到','并无','不再有')
ABSENT_AFTER=('不在','不见了','消失了','已被拿走','已经拿走','被烧毁','已经烧毁','被拆走','已拆走','被搬走','已经搬走','已搬走','被移走','已经移走','已移走','不复存在','被拆除','已经拆除','已拆除')


def removal_after(after):
    # A current denial ("并没有被搬走") preserves the fixture. Do not let
    # the embedded removal verb override the negation immediately before it.
    return any(not re.search(r'(?:并没有|没有|并未|未曾|还没|尚未|并不|还未|不会|不曾|不可能)\s*$',after[:m.start()])
               for word in ABSENT_AFTER for m in re.finditer(re.escape(word),after))

def clauses(sentence,name):
    for clause in re.split(r'[，,；;：:。！？\n]',sentence):
        index=clause.find(name)
        if index>=0:yield clause,clause[:index],clause[index+len(name):]

def absent_object_mention(sentence,name):
    if any(w in sentence for w in PAST):return False
    return any(not any(w in clause for w in PAST) and
               (any(w in before for w in ABSENT_BEFORE) or removal_after(after))
               for clause,before,after in clauses(sentence,name))

def current_object_mention(sentence,name):
    # A past frame can cross a comma, whereas an unrelated negation cannot.
    if any(w in sentence for w in PAST):return False
    return any(not any(w in before for w in ABSENT_BEFORE) and not removal_after(after)
               for clause,before,after in clauses(sentence,name))


def local_scene_text(text,away_names,present_names=()):
    """Keep current scenery, excluding explicit descriptions of remote NPCs.

    Arrival prose can mention the people who stayed beside the origin's fire.
    Their furniture is not a newly discovered fixture at the destination. This
    filters only scene-extraction input; the archived story stays unchanged.
    """
    names=[name for name in away_names if isinstance(name,str) and name]
    if not names:return text
    subjects=r'你|我|You\b|I\b'
    for name in present_names:
        if isinstance(name,str) and name and name not in names:subjects+='|'+re.escape(name)+r'(?!的)'
    current=re.compile(r'^\s*(?:(?:而|但是|但|同时|与此同时|此时|如今|现在|However\b|Meanwhile\b|But\b)\s*)?(?:'+subjects+r')',re.I)
    remote=re.compile(r'(?:'+ '|'.join(re.escape(name) for name in names)+r')(?!的).{0,24}(?:留在|仍在|还在|留守|待在|坐在|站在|守在|待于|remains?\b|stays?\b|sits?\b|stands?\b)',re.I)
    pieces=re.findall(r'[^。！？.!?\n]+[。！？.!?\n]*|[\n]+',text);result=[]
    for sentence in pieces:
        clauses=re.split(r'([，,；;])',sentence);away=False
        for clause in clauses:
            if current.match(clause):away=False
            if remote.search(clause):away=True
            if not away:result.append(clause)
    return ''.join(result)
