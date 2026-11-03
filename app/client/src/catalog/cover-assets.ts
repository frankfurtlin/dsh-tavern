import {withBase} from '../platform/base'
/** Immutable bundled IDs remain portable; private cover associations use assets. */
const covers:Record<string,string>={
 'final-cover-fog-v1':'fog-harbor-cover-400.webp',
 'final-cover-clock-garden-v1':'clock-garden-cover-v1-400.webp',
 'final-cover-seventh-page-v1':'seventh-page-cover-v1-400.webp',
 'final-cover-slow-mail-v1':'slow-mail-cover-v1-400.webp',
 'final-cover-rain-street-v1':'rain-street-cover-v1-400.webp',
 'final-cover-wind-city-v1':'wind-city-cover-v1-400.webp'
}
export function bundledCover(id:string|undefined){return id&&covers[id]?withBase('/assets/original/final-product/'+covers[id]):null}
