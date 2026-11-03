const ACCOUNT_PATHS=/^\/(?:explore|features|works|my-works|recent|bookmarks|my-prompts|multiplayer|community|rankings|creators|tasks|earnings|affiliate|published|authors|worlds|library|stories|books|downloads|notebook|insights|conversations|settings|product|help)(?:\/|$)/
export function loginDestination(value:unknown,fallback='/explore'){
  if(typeof value!=='string'||!value.startsWith('/')||value.startsWith('//')||/[\\\x00-\x20]/.test(value))return fallback
  try{const url=new URL(value,'https://story.invalid');if(url.origin!=='https://story.invalid'||!ACCOUNT_PATHS.test(url.pathname))return fallback;return url.pathname+url.search+url.hash}catch{return fallback}
}
