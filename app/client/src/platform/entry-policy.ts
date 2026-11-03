/** Existing device data remains recoverable; new users enter the account app. */
export function deviceEntryDestination(hasVault:boolean,signedIn:boolean){
  return hasVault?null:signedIn?'/explore':'/login'
}
