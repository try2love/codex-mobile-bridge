'use strict';

// Selection is observed, never cancelled: native copy and touch callouts remain available.
class ComposerQuotes {
  constructor({messages,input,container,scope,onChange,onPrompt,onError=()=>{},canQuote=()=>true,translate=text=>text}){
    Object.assign(this,{messages,input,scope,onChange,onPrompt,onError,canQuote,translate});this.quotes=[];this.snapshot=null;
    const doc=input.ownerDocument;this.doc=doc;
    this.root=doc.createElement('div');this.root.className='composer-quotes';this.root.hidden=true;
    this.action=doc.createElement('button');this.action.type='button';this.action.className='composer-quote-selection';this.action.hidden=true;
    this.action.textContent=translate('引用所选文字');
    const tools=input.closest('.composer')?.querySelector('.composer-tools');
    if(tools){
      this.selectionRow=doc.createElement('div');this.selectionRow.className='composer-quote-actions';this.selectionRow.hidden=true;tools.after(this.selectionRow);this.selectionRow.append(this.action);
    }else this.root.append(this.action);
    container.insertBefore(this.root,input);
    this.action.addEventListener('pointerdown',()=>{this.pressed=this.snapshot;});
    this.action.addEventListener('pointercancel',()=>{this.pressed=null;this.capture();});
    this.action.addEventListener('click',()=>{const selected=this.pressed||this.snapshot;this.pressed=null;if(this.valid(selected))this.add(selected.text,selected.source);this.snapshot=null;this.action.hidden=true;this.visibility();});
    doc.addEventListener('selectionchange',()=>this.capture());
    messages.addEventListener('bridge-compose-quote',event=>{
      if(!messages.contains(event.target)||!event.target.isConnected||!event.target.closest('.message.assistant,.message.user'))return;
      this.quoteMessage(event.detail?.getText,event.detail?.source,event.target);
    });
    input.addEventListener('focus',()=>{this.snapshot=null;this.action.hidden=true;this.visibility();});
    messages.addEventListener('bridge-compose-prompt',event=>{
      if(!this.available()||!messages.contains(event.target)||!event.target.isConnected||!event.target.closest('.message.assistant'))return;
      const text=event.detail?.text;if(typeof text!=='string'||!text.trim())return;
      onPrompt(text);
    });
  }
  identity(){return JSON.stringify(this.scope()||null);}
  available(){return !!this.scope()&&this.messages.isConnected&&!this.messages.closest('[hidden]');}
  valid(value){return !!value&&this.canQuote()&&this.available()&&value.scope===this.identity()&&value.body.isConnected&&this.messages.contains(value.body);}
  capture(){
    const selection=this.doc.getSelection();
    if(!this.canQuote()||!this.available()||!selection||selection.isCollapsed||!selection.rangeCount){
      if(!this.pressed&&this.doc.activeElement!==this.action){this.snapshot=null;this.action.hidden=true;this.visibility();}return;
    }
    const element=node=>node?.nodeType===1?node:node?.parentElement;
    const range=selection.getRangeAt(0),body=element(range.startContainer)?.closest('.message-body');
    if(!body||!body.closest('.message.assistant')||!this.messages.contains(body)||!body.contains(range.endContainer)){this.snapshot=null;this.action.hidden=true;this.visibility();return;}
    const text=selection.toString().trim();if(!text)return;
    this.pressed=null;this.snapshot={text,body,scope:this.identity(),source:String(body.__mermaidIdentity||'')};this.action.textContent=this.translate('引用所选文字');this.action.hidden=false;this.visibility();
  }
  async quoteMessage(getText,source='',target){
    if(!this.canQuote()||!this.available()||typeof getText!=='function'||!target?.isConnected||!this.messages.contains(target))return;
    const scope=this.identity();
    try{
      const text=await getText();
      if(this.identity()===scope&&target.isConnected&&this.messages.contains(target)&&typeof text==='string')this.add(text,source);
    }catch(error){if(this.identity()===scope&&target.isConnected)this.onError(error.message||this.translate('读取完整正文失败，请重试'));}
  }
  add(text,source=''){if(!this.canQuote()||!this.available()||!text.trim()||this.quotes.length>=8)return;if(text.length>100000){this.onError(this.translate('消息含引用超过 100000 字符，请缩短后发送'));return;}this.quotes.push({text:text.slice(0,100000),source:String(source).slice(0,500),provider:this.scope().provider});this.onChange();this.render();}
  restore(quotes=[]){this.reset();this.quotes=ComposerQuotes.normalize(quotes);this.render();}
  reset(){this.quotes=[];this.snapshot=null;this.pressed=null;this.action.hidden=true;this.render();}
  render(){
    for(const node of [...this.root.children])if(node!==this.action)node.remove();
    this.quotes.forEach((quote,index)=>{const chip=this.doc.createElement('div');chip.className='composer-quote';const text=this.doc.createElement('details');const summary=this.doc.createElement('summary');summary.textContent=quote.text.slice(0,160);const full=this.doc.createElement('div');full.className='composer-quote-full';full.textContent=quote.text;text.append(summary,full);if(quote.source)text.title=[quote.provider,quote.source].filter(Boolean).join(' · ');const remove=this.doc.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label',this.translate('移除引用'));remove.addEventListener('click',()=>{this.quotes.splice(index,1);this.onChange();this.render();});chip.append(text,remove);this.root.append(chip);});
    this.visibility();
  }
  visibility(){this.root.hidden=(!this.root.contains(this.action)||this.action.hidden)&&!this.quotes.length;if(this.selectionRow)this.selectionRow.hidden=!this.selectionRow.contains(this.action)||this.action.hidden;}
  serialize(text){return ComposerQuotes.serialize(text,this.quotes);}
  static normalize(quotes){return Array.isArray(quotes)?quotes.filter(value=>value&&typeof value.text==='string'&&value.text.trim()).slice(0,8).map(value=>({text:value.text.slice(0,100000),source:typeof value.source==='string'?value.source.slice(0,500):'',provider:typeof value.provider==='string'?value.provider.slice(0,40):''})):[];}
  static serialize(text,quotes){const quoted=ComposerQuotes.normalize(quotes).map(value=>value.text.split('\n').map(line=>'> '+line).join('\n')).join('\n\n');return quoted?quoted+(text?'\n\n'+text:''):text;}
}
if(typeof module!=='undefined')module.exports={ComposerQuotes};
