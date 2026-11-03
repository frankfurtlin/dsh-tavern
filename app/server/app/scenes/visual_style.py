"""A world's visual setting is shared by portraits and environments."""


def visual_context(world,definition=None):
    definition = world.definition if definition is None else definition
    source=getattr(world,'source',{}) or {}
    provenance=source.get('script',{}).get('provenance',{})
    return {
        'genre':definition.get('rules', {}).get('genre', 'general'),
        'narrativeStyle':definition.get('narrativeStyle', definition.get('rules', {}).get('style', '')),
        'setting':{'title':world.name, 'description':definition.get('description', '')[:1000],
                   'sourceIdentity':{k:provenance[k] for k in ('title','author','year','language') if k in provenance},
                   'openingExcerpt':(source.get('blocks') or [{}])[0].get('text','')[:900],
                   'places':[{'name':e['name'], 'description':e.get('description', '')[:300]}
                             for e in definition.get('entities', []) if e['type'] == 'place'][:12]},
        'visualSettingVersion':5,
    }


def style_prompt(descriptor):
    from ..domain import canonical
    return ('Use one coherent painterly illustrated realism style, natural proportions and muted colours. '
            'Derive the historical era, geography, ethnicity, architecture and clothing from this world setting: '
            + canonical({k:v for k,v in descriptor.get('setting', {'title':descriptor.get('worldName','')}).items() if k in ('title','sourceIdentity','description','places')})
            + '. Narrative context: ' + descriptor.get('narrativeStyle', '')
            + '. Preserve each character’s own identity, age, gender and occupation. Historical European settings require their period European clothing; Chinese cultivation settings require their own traditional fantasy clothing. Do not apply one world’s costumes to another. No photographic people pasted onto painted scenery.')


def environment_style_prompt(descriptor):
    import re
    narrative=descriptor.get('narrativeStyle','')
    context='; '.join(part.strip() for part in re.split(r'[。；;]',narrative)
                     if part.strip() and not any(word in part for word in ('人物','角色','服饰','服装','衣服','穿着','绅士','女仆','叙事','character','costume')))
    era=descriptor.get('setting',{}).get('sourceIdentity',{}).get('year','')
    return ('An EMPTY game environment background, coherent painterly illustrated realism, muted natural colours, detailed architecture and materials. '
            + 'Physical setting and atmosphere: '+context+'. Historical year: '+str(era)
            + '. Draw architecture, furnishings, surfaces and light ONLY. No human subject or character design.')
