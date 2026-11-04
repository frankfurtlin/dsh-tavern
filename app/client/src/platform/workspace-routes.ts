export const ACCOUNT_HOME='/explore'
export const HISTORY_IMPORT='/settings/import-history'
const legacyRoutes:Record<string,string>={
 '/local':ACCOUNT_HOME,'/local/library':ACCOUNT_HOME,'/local/discover':ACCOUNT_HOME,
 '/local/new-world':'/worlds/scripts','/local/new-story':'/worlds/scripts',
 '/local/settings':ACCOUNT_HOME,'/local/settings/models':ACCOUNT_HOME,
 '/local/settings/media':ACCOUNT_HOME,'/local/settings/tasks':ACCOUNT_HOME,
 '/local/settings/appearance':ACCOUNT_HOME,'/local/settings/data':ACCOUNT_HOME,
 '/local/notebook':ACCOUNT_HOME,'/local/insights':'/insights','/local/characters':'/characters',
}
/** Old links resolve inside the authenticated app, never into another workspace. */
export function legacyDestination(path:string){
 if(legacyRoutes[path])return legacyRoutes[path]
 if(/^\/local\/(read|lines|stage|chat-stage|chat|play|companions)\//.test(path))return HISTORY_IMPORT
 return '/features?group='+encodeURIComponent('扩展能力')
}
