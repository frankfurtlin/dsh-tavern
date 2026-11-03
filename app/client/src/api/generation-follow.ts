// Only the draft body belongs to this controller: never scroll Reader/window.
export function createGenerationFollowing(){
  let following=true,newWords=false,lastTop=0,touchY=0,programmatic=false
  return {
    get following(){return following},get newWords(){return newWords},
    stop(){following=false;programmatic=false},
    wheel(y:number){if(y<0){following=false;programmatic=false}},key(k:string){if(['ArrowUp','PageUp','Home'].includes(k)){following=false;programmatic=false}},
    touchStart(y:number){touchY=y},touchMove(y:number){if(y>touchY+3){following=false;programmatic=false}},
    scroll(top:number,height:number,client:number){if(!programmatic){if(top<lastTop-1)following=false;else if(height-client-top<=12){following=true;newWords=false}}lastTop=top},
    render(area:{scrollTop:number;scrollHeight:number},changed:boolean){if(!following){if(changed)newWords=true;return false}programmatic=true;area.scrollTop=area.scrollHeight;lastTop=area.scrollTop;newWords=false;return true},
    settled(){programmatic=false},latest(){following=true;newWords=false},
  }
}
