import type {ScenePoint,SceneView} from './scene-types'

// A legal destination in the world graph is not yet a measured picture marker.
export function imageBound(point:ScenePoint):boolean {
  return point.imageBound===true&&Number.isFinite(point.yaw)&&Number.isFinite(point.pitch)&&Math.abs(point.pitch)<=Math.PI/2
}
// A map communicates real graph connections; only confirmed picture evidence
// authorizes compass directions. Unconfirmed routes stay in the route list.
export function mapExits(scene:SceneView):ScenePoint[] {
  return scene.exits.filter(imageBound)
}
export function localPoints(scene:SceneView):ScenePoint[] {
  return scene.points.filter(p=>p.id!==scene.pointId&&!scene.exits.some(e=>e.portalId===p.id||e.id==='exit:'+p.id))
}
export function picturePoints(scene:SceneView):ScenePoint[] {
  return localPoints(scene).filter(imageBound)
}
export function pictureExits(scene:SceneView):ScenePoint[] {
  return scene.exits.filter(imageBound)
}
export function inspectingPicture(scene:SceneView):boolean {
  return scene.image?.anchorStatus==='queued'||scene.image?.anchorStatus==='repairing'
}
