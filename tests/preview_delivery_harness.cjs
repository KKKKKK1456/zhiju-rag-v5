'use strict';
// Browser-independent fixture tests: no network, no model requests, no dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const {seconds,answerState,stageCopy,createHistory} = require('../preview/preview.js');
assert.equal(seconds(undefined),'未记录');
assert.equal(seconds(-1),'未记录');
assert.equal(seconds(0),'0.0 秒');
assert.equal(answerState({status:'partial'}),'部分回答');
assert.notEqual(answerState({status:'error'}),'已回答');
assert.equal(answerState(null),'仅检索');
assert.ok(stageCopy('support_check')[1].includes('尚未完成'));
const history=createHistory(2);
assert.equal(history.remember('a',{question:'A'}),true);
assert.equal(history.remember('a',{question:'wrong'}),false);
assert.equal(history.get('a').value.question,'A');
history.remember('b',{});history.remember('c',{});
assert.equal(history.get('a'),undefined);
assert.equal(history.list()[0].id,'c');

class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.listeners={};this.dataset={};this.attrs={};this._text='';this.hidden=false;this.open=false;this.checked=false;this.disabled=false;this.value='';this.classList={add:()=>{}};}
  set textContent(value){this._text=String(value??'');this.children=[];}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
  set innerHTML(_value){throw new Error('Unsafe HTML insertion');}
  append(child){this.children.push(child);}
  replaceChildren(...children){this._text='';this.children=children;}
  setAttribute(key,value){this.attrs[key]=value;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  async click(){if(this.listeners.click)await this.listeners.click();}
  focus(){}
  select(){}
}
const html=fs.readFileSync(path.join(root,'preview/index.html'),'utf8');
const nodes={};
for(const match of html.matchAll(/\bid="([^"]+)"/g)) nodes[match[1]]=new Element();
const fixture={question:'两年合同，六个月试用期，赔偿怎么算？',user_message:'两年合同，六个月试用期，赔偿怎么算？',cloud_attempts:4,planning_status:'ok',queries:[{kind:'question',query:'test'}],timings:{total_seconds:180,planning_seconds:3,recall_seconds:4,coarse_seconds:1,rerank_seconds:2,answer_seconds:170},chunks:[{rank:1,title:'测试法规',document_id:'d',chunk_id:'c',start:1,end:99,text:'<script>not executable</script>原文'}],answer:{status:'partial',support_check:true,answers:[{text:'示例结论',citations:[{id:'S1',title:'测试法规',source_rank:1,quote:'原文片段'}]}],gaps:['某条件尚不确定'],calculations:{outputs:[{label:'示例计算',value:'32000.00',unit:'元'}],inputs:[{name:'salary',label:'月工资',value:'8000',unit:'元',source:'Q',quote:'用户条件'}],steps:[{label:'示例步骤',expression:'8000 * 4',value:'32000',unit:'元',exact_value:'32000',citations:[{id:'S1',title:'测试法规',source_rank:1}]}],rounding:'四舍五入至分'}}};
let status={state:'ready',cloud_enabled:true,answer_enabled:true,configuration:{},job:{id:'j1',state:'complete',result:fixture}};
const requests=[],intervals=[];
const context={console,AbortController,Blob,Date,Number,JSON,Math,Map,URL,setTimeout:()=>1,clearTimeout:()=>{},setInterval:fn=>{intervals.push(fn);return 1;},document:{getElementById:id=>nodes[id]||null,createElement:tag=>new Element(tag),querySelector:()=>({content:'test-session'})},fetch:async(url,options)=>{requests.push({url,options});if(options.method==='POST')return {ok:true,json:async()=>({id:'j2'})};return {ok:true,json:async()=>status};}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root,'preview/preview.js'),'utf8'),context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const all=node=>[node,...node.children.flatMap(all)];
(async()=>{
  await flush();
  assert.equal(requests.filter(r=>r.options.method==='POST').length,0,'Opening UI must not submit a question');
  assert.equal(nodes.cloud.checked,false);assert.equal(nodes['answer-consent'].checked,false);
  assert.ok(nodes['answer-box'].textContent.includes('32000.00'));
  assert.ok(nodes['answer-box'].textContent.includes('部分回答'));
  assert.ok(nodes['answer-box'].textContent.includes('某条件尚不确定'));
  assert.equal(nodes['sources-box'].open,false);
  assert.equal(nodes['queries-box'].open,false);
  assert.ok(all(nodes['answer-box']).filter(n=>n.tagName==='details').every(n=>n.open===false),'Citations/calculation details must default closed');
  const source=all(nodes['answer-box']).find(n=>n.href==='#source-1');
  await source.click();assert.equal(nodes['sources-box'].open,true);
  assert.ok(nodes.results.textContent.includes('<script>not executable</script>'),'Untrusted content must remain text');
  nodes.cloud.checked=true;nodes['answer-consent'].checked=true;nodes.followup.checked=true;
  await nodes['new-question'].click();
  assert.equal(nodes.followup.checked,false);assert.equal(nodes.cloud.checked,false);assert.equal(nodes['answer-consent'].checked,false);
  assert.equal(nodes['answer-box'].hidden,true);
  intervals[0]();await flush();
  assert.equal(nodes['answer-box'].hidden,true,'Polling must not restore previous answer after New question');
  nodes.question.value='A new question';nodes.cloud.checked=true;nodes['answer-consent'].checked=true;
  status={...status,job:{id:'j2',state:'running',stage:'support_check',elapsed_seconds:123}};
  await nodes.submit.click();await flush();
  const submitted=requests.filter(r=>r.options.method==='POST');assert.equal(submitted.length,1);
  assert.deepEqual(JSON.parse(submitted[0].options.body),{question:'A new question',cloud_consent:true,answer_consent:true});
  assert.equal(nodes.cloud.checked,false);assert.equal(nodes['answer-consent'].checked,false);
  assert.ok(nodes.elapsed.textContent.includes('123 秒'),'Use server elapsed time, not fabricated percentage');
  assert.equal(nodes.submit.disabled,true);assert.equal(nodes['new-question'].disabled,true);
  await nodes.submit.click();assert.equal(requests.filter(r=>r.options.method==='POST').length,1,'No duplicate submission while running');
  status={...status,job:{id:'j2',state:'complete',result:{...fixture,user_message:'A new question'}}};
  intervals[0]();await flush();
  assert.equal(nodes.history.children.length,2);
  await nodes.history.children[1].click();
  assert.equal(nodes.followup.checked,false,'Browsing older results must not imply following them up');
  nodes.followup.checked=true;await nodes.followup.listeners.change();
  assert.ok(nodes['followup-help'].textContent.includes('A new question'),'Follow-up must use latest completed question');
  context.fetch=async()=>({ok:false,status:403,json:async()=>({message:'Local session required'})});
  intervals[0]();await flush();
  assert.ok(nodes.progress.textContent.includes('服务已重启，请刷新'),'Expired local session should have a useful Chinese recovery message');
  assert.equal(nodes.submit.disabled,true);
  console.log('UI fixture checks passed: consent, rendering, elapsed, history, follow-up, no auto/duplicate POST.');
})().catch(error=>{console.error(error);process.exitCode=1;});
