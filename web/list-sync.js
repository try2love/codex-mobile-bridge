'use strict';

class ListSync {
  constructor({root,status,refresh,visible,ready}) {
    Object.assign(this,{root,status,refresh,visible,ready});this.busy=false;
    status.onclick=()=>this.update(true);
    const resume=()=>{if(!document.hidden)this.update();};
    document.addEventListener('visibilitychange',resume);window.addEventListener('focus',resume);
    root.addEventListener('touchstart',event=>{
      this.start=!this.busy&&root.scrollTop<=0&&event.touches.length===1?{x:event.touches[0].clientX,y:event.touches[0].clientY}:null;this.distance=0;
    },{passive:true});
    root.addEventListener('touchmove',event=>{
      if(!this.start||event.touches.length!==1)return;
      const dx=event.touches[0].clientX-this.start.x,dy=event.touches[0].clientY-this.start.y;
      if(Math.abs(dx)>40||dy<0){this.start=null;return;}
      if(dy>10){event.preventDefault();this.distance=dy;status.textContent=dy>=72?'松开刷新聊天列表':'下拉刷新聊天列表';status.hidden=false;}
    },{passive:false});
    root.addEventListener('touchend',()=>{const refresh=this.start&&this.distance>=72;this.start=null;if(refresh)this.update(true);else if(!this.busy)status.hidden=true;});
    root.addEventListener('touchcancel',()=>{this.start=null;if(!this.busy)status.hidden=true;});
    this.schedule();
  }
  schedule(){clearTimeout(this.timer);this.timer=setTimeout(async()=>{await this.update();this.schedule();},15000);}
  async update(explicit=false){
    if(this.busy||document.hidden||!this.visible()||!this.ready()||(!explicit&&this.root.scrollTop>24))return;
    this.busy=true;clearTimeout(this.hideTimer);
    if(explicit){this.status.hidden=false;this.status.textContent='正在刷新聊天列表…';}
    try{await this.refresh();if(explicit){this.status.textContent='聊天列表已更新';this.hideTimer=setTimeout(()=>this.status.hidden=true,2500);}}
    catch(e){this.status.hidden=false;this.status.textContent='列表暂未更新，点击重试';}
    finally{this.busy=false;}
  }
}
