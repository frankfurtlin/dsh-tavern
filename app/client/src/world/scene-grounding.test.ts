import {describe,it,expect} from 'vitest'
import {imageBound,localPoints,picturePoints,pictureExits,mapExits,inspectingPicture} from './scene-grounding'
import type {SceneView} from './scene-types'

describe('image evidence for scene navigation',()=>{
  const point=(id:string,bound?:boolean)=>({id,name:id,direction:id,yaw:.8,pitch:-.45,imageBound:bound})
  it('keeps logical destinations accessible without rendering guessed image markers',()=>{
    const scene={pointId:'entry',points:[point('entry',true),point('bench',true),point('portal',true),point('unmeasured')],exits:[{...point('yard',true),portalId:'portal'},point('town')]} as SceneView
    expect(picturePoints(scene).map(p=>p.id)).toEqual(['bench'])
    expect(localPoints(scene).map(p=>p.id)).toEqual(['bench','unmeasured'])
    expect(pictureExits(scene).map(p=>p.id)).toEqual(['yard'])
    expect(mapExits(scene).map(p=>p.id)).toEqual(['yard'])
    expect(scene.exits.map(p=>p.id)).toEqual(['yard','town'])
    expect(picturePoints(scene)[0]?.pitch).toBe(-.45)
  })
  it('does not accept legacy, nonfinite or impossible picture coordinates',()=>{
    expect(imageBound(point('legacy'))).toBe(false)
    expect(imageBound({...point('bad',true),pitch:NaN})).toBe(false)
    expect(imageBound({...point('bad',true),yaw:Infinity})).toBe(false)
    expect(imageBound({...point('bad',true),pitch:Math.PI})).toBe(false)
  })
  it('continues polling during the bounded corrective image, then stops',()=>{
    for(const anchorStatus of ['queued','repairing'])expect(inspectingPicture({image:{anchorStatus}} as SceneView)).toBe(true)
    for(const anchorStatus of ['complete','incomplete','unavailable'])expect(inspectingPicture({image:{anchorStatus}} as SceneView)).toBe(false)
  })
})
