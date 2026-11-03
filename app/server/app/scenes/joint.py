"""Bounded local-preview full-panorama composition, with ordinary output review."""
import json
import io
from PIL import Image,ImageDraw


def scale_reference(source,descriptor):
    """A private composition proposal, never playable artwork or measured proof."""
    image=Image.open(io.BytesIO(source)).convert('RGB')
    draw=ImageDraw.Draw(image)
    proposals=descriptor['jointScaleProposals']
    assert set(proposals)=={p['entityId'] for p in descriptor['sceneCast']}
    for ident,box in proposals.items():
        assert len(box)==4 and all(type(v) in (int,float) and 0<=v<=1 for v in box)
        left,top,right,bottom=box
        assert 0<right-left<=.035 and .08<=bottom-top<=.14
        x=(left+right)/2*image.width;y=top*image.height
        width=(right-left)*image.width;height=(bottom-top)*image.height
        draw.ellipse((x-width*.23,y,x+width*.23,y+height*.17),fill='#9c9990')
        draw.polygon([(x-width*.33,y+height*.19),(x+width*.33,y+height*.19),
            (x+width*.30,y+height*.59),(x-width*.30,y+height*.59)],fill='#727c82')
        thickness=max(2,round(width*.16))
        for side in (-1,1):
            draw.line([(x+side*width*.31,y+height*.23),(x+side*width*.44,y+height*.59)],fill='#727c82',width=thickness)
            draw.line([(x+side*width*.15,y+height*.59),(x+side*width*.21,y+height)],fill='#5e666a',width=thickness)
    output=io.BytesIO();image.save(output,format='PNG')
    return output.getvalue()


def composition_prompt(descriptor):
    cast=descriptor['sceneCast']
    if descriptor.get('jointCompositionVersion')==3:
        visuals=descriptor['jointCastVisuals']
        assert set(visuals)=={p['entityId'] for p in cast}
        people=[{'name':p['name'],'appearance':visuals[p['entityId']],
            'proposedBox':descriptor['jointScaleProposals'][p['entityId']]} for p in cast]
        role=('Image 1 is the failed received full spherical panorama. Replace every wrong person with the specified small standing cast. '
              if descriptor.get('visualRepairAttempt') else
              'Image 1 is a full spherical panorama with '+str(len(cast))+' SMALL neutral anatomy sketches. Replace EACH sketch with exactly ONE standing physical person, at the SAME SMALL SIZE AND POSITION. ')
        return (role+
            'Repaint every grey guide pixel. Keep the actual forest background and full 360x180 equirectangular projection. '
            'Do not enlarge, move, duplicate or add figures. Both hands empty; feet actually on the ground. '
            'Images 2 onward supply faces, hair and clothes ONLY, never their large framing or backgrounds. '
            'Reference mapping: '+json.dumps(descriptor.get('characterReferenceOrder',[]),ensure_ascii=False)+'. '
            'The ONLY people: '+json.dumps(people,ensure_ascii=False)+'. '
            'No observer, additional people, portraits, tents or furniture beyond those already in Image 1. '
            'No new story actions or props. Keep paths open, matching light, natural anatomy and contact shadows. '
            'No grey residue, panels, writing, borders or edit seams. '
            +('CORRECTION: the input is the failed received panorama; remove wrong people, replace them with exactly '
              'the specified small standing people at the proposed boxes, never add to existing figures. '
              if descriptor.get('visualRepairAttempt') else ''))
    people=[{key:person.get(key) for key in ('name','description','appearance','expectedPose')} for person in cast]
    return (
        'Edit Image 1 into ONE seamless full 360x180 degree equirectangular panorama. '
        'It is the actual EMPTY environment. Preserve its forest, ground, tents or stream, '
        'camera, daylight and illustration style. Do not turn it into a normal perspective illustration. '
        'Add EXACTLY '+str(len(cast))+' physical people in TOTAL, ONE of each listed person. '
        'ALL are STANDING on visible ground, both hands EMPTY. None sitting, kneeling, '
        'leaning over a table, handling maps, or performing an action. '
        'Keep them 5 to 7 metres away, similar natural scale; full bodies visible, '
        'heads near the halfway horizon and feet below it. A whole person occupies '
        'about 10 percent of the full texture height, never a foreground portrait. '
        'Images 2 onward are APPEARANCE ONLY: match each person\'s own face, hair, gender '
        'and clothes using this mapping: '+json.dumps(descriptor.get('characterReferenceOrder',[]),ensure_ascii=False)+'. '
        'Do not copy those images\' white backgrounds, composition or large size. '
        'Current cast and physical positions: '+json.dumps(people,ensure_ascii=False)+'. '
        'Current observable scene: '+descriptor['description']+'. '
        'Required open walking connections: '+json.dumps(descriptor.get('exitRoutes',[]),ensure_ascii=False)+'. '
        'The observer is invisible. No extra people, duplicated blue-clothed boys, '
        'portraits, new buildings, magic effects, text or panels. Entire image must '
        'be painted coherently with matching shadows and no rectangular edit seams. '
        'The left and right edges meet; sky above, ground below, horizon halfway down. '
        +('CORRECTION: remove every duplicated person and replace every wrong pose; '
          'do not preserve the failed people from Image 1. ' if descriptor.get('visualRepairAttempt') else '')
    )


def correction_descriptor(descriptor, source_hash):
    """Bind an ordinary single correction to the pixels it actually edits."""
    return {**descriptor,'jointReferenceHash':source_hash}
