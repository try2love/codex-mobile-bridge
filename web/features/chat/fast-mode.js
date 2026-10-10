'use strict';
class FastModeControl {
  constructor({root,input,status}){
    Object.assign(this,{root,input,status});this.reset();
    input.onchange=()=>{this.dirty=true;this.render();};
  }
  reset(){this.catalog=null;this.view=null;this.model=null;this.dirty=false;this.busy=false;this.render();}
  open(catalog,view,model){this.catalog=catalog;this.view=view;this.select(model);}
  select(model){this.model=Array.isArray(this.catalog?.models)?this.catalog.models.find(m=>m.id===model):null;this.dirty=false;this.render();}
  sync(view){this.view=view;this.render();}
  setBusy(value){this.busy=value;this.render();}
  available(){return this.catalog?.fastMode?.allowed===true&&this.view?.provider==='openai'&&!!this.model?.fastTier;}
  payload(){return this.available()&&this.dirty?{fastMode:this.input.checked}:{};}
  render(){
    this.root.hidden=!this.available();this.input.disabled=this.busy||!this.view?.connected;
    if(this.root.hidden)return;
    const hasTier=this.view&&'serviceTier' in this.view;
    const tier=hasTier?this.view.serviceTier:'currentServiceTier' in this.catalog?this.catalog.currentServiceTier:this.catalog.fastMode?.defaultServiceTier??this.model?.defaultServiceTier;
    const fast=['fast','priority',this.model.fastTier].includes(tier),other=tier!=null&&!['default','standard'].includes(tier)&&!fast;
    if(!this.dirty){this.input.checked=fast;this.input.indeterminate=other;}
    else this.input.indeterminate=false;
    this.status.textContent=BridgeI18n.t(!this.view?.connected?'连接桌面后可更改':this.dirty?'保存后从下一轮生效':other?'当前使用其他速度档位；切换将替换该设置。':fast?'Fast 已开启':'标准速度');
  }
}
if(typeof module!=='undefined')module.exports={FastModeControl};
