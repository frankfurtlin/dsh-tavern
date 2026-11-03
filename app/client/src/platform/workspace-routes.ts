export const ACCOUNT_HOME='/explore'
export const HISTORY_IMPORT='/settings/import-history'
const legacyRoutes:Record<string,string>={
 '/local':'/works','/local/library':'/works','/local/discover':ACCOUNT_HOME,
 '/local/new-world':'/worlds/scripts','/local/new-story':'/worlds/scripts',
 '/local/settings':'/settings','/local/settings/models':'/settings/models',
 '/local/settings/media':'/settings/models','/local/settings/tasks':'/settings/models',
 '/local/settings/appearance':'/settings?section=reading','/local/settings/data':HISTORY_IMPORT,
 '/local/notebook':'/notebook','/local/insights':'/insights','/local/characters':'/conversations',
}
/** Old links resolve inside the authenticated app, never into another workspace. */
export function legacyDestination(path:string){
 if(legacyRoutes[path])return legacyRoutes[path]
 if(/^\/local\/(read|lines|stage|chat-stage|chat|play|companions)\//.test(path))return HISTORY_IMPORT
 return '/features?group='+encodeURIComponent('扩展能力')
}
