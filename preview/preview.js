'use strict';

// These helpers are shared with the offline UI contract tests.
function seconds(value, digits=1) {
  return Number.isFinite(value) && value >= 0 ? value.toFixed(digits) + ' 秒' : '未记录';
}
function answerState(answer) {
  if (!answer) return '仅检索';
  return ({answered:'已回答',partial:'部分回答',insufficient:'依据不足',error:'处理未完成',needs_clarification:'需要补充条件'})[answer.status] || '请核对结果';
}
function stageCopy(stage) {
  return ({
    queued:['已收到问题，准备开始','请求只提交一次，无需重复点击。'],
    conditions:['正在核对你更正的条件','保留未修改的条件，不采用助手旧答案。'],
    planning:['正在理解问题','把口语问题整理成需要查找的事项。'],
    retrieval:['正在知识库里查找','查找可能支持回答的原始文件。'],
    coarse:['正在筛选相关资料','保留与问题相关的候选依据。'],
    rerank:['正在比较资料相关性','本机重排可能需要一些时间。'],
    answer:['正在整理回答','依据找到的文件生成回答与计算方案。'],
    support_check:['正在核对回答依据与计算规则','此阶段可能超过一分钟；尚未完成，不代表结果已通过。']
  })[stage] || ['正在处理你的问题','完成后将展示回答状态；不会自动重试。'];
}
function createHistory(limit=20) {
  const entries=[];
  return {
    remember(id, value) {
      const index=entries.findIndex(entry=>entry.id===id);
      if(index>=0) return false;
      entries.push({id, value}); if(entries.length>limit) entries.shift(); return true;
    },
    get(id) {return entries.find(entry=>entry.id===id);},
    list() {return entries.slice().reverse();}
  };
}
if(typeof module !== 'undefined' && module.exports) module.exports={seconds,answerState,stageCopy,createHistory};

if(typeof document !== 'undefined') {
const $=id=>document.getElementById(id);
const token=document.querySelector('meta[name="v5-session"]').content;
const history=createHistory();
let result=null, active=null, selected=null, polling=false, submittedAt=null;
let submitting=false, submitError=null, editingNew=false, running=false, lastCompleted=null;
let latestObserved=null, configuration={};
async function api(path,data) {
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),data?30000:15000);
  try {
    const response=await fetch(path,{method:data?'POST':'GET',headers:{'X-V5-Session':token,...(data?{'Content-Type':'application/json'}:{})},body:data?JSON.stringify(data):undefined,cache:'no-store',signal:controller.signal});
    const value=await response.json();
    if(!response.ok) {
      const restarted=response.status===403 && /session required/i.test(value.message||'');
      const error=new Error(restarted?'服务已重启，请刷新页面后继续。':value.message||'服务请求失败');
      if(restarted)error.code='service_restarted';
      throw error;
    }
    return value;
  } finally {clearTimeout(timeout);}
}
function text(parent,tag,content,cls) {
  const node=document.createElement(tag); node.textContent=content;
  if(cls)node.className=cls; parent.append(node); return node;
}
function sourceLink(parent,c) {
  const link=text(parent,'a','['+c.id+'] '+(c.title||'用户提供的条件'));
  if(c.id==='Q') return link;
  link.href='#source-'+c.source_rank;
  link.addEventListener('click',()=>{$('sources-box').open=true;});
  return link;
}
function resetView() {
  result=null; $('download').disabled=true;
  for(const id of ['answer-box','queries-box','sources-box','export-box']) $(id).hidden=true;
  $('results').replaceChildren(); $('answer-box').replaceChildren(); $('queries').replaceChildren();
  $('export-json').value=''; $('metrics').textContent=''; $('elapsed').textContent='';
  $('welcome').hidden=false;
}
function renderHistory() {
  const host=$('history');host.replaceChildren();
  if(!history.list().length) {text(host,'p','完成提问后会显示在这里，仅保留本页最近 20 条记录。刷新后不保留。','small');return;}
  history.list().forEach(entry=>{
    const button=text(host,'button','','history-item');button.type='button';
    button.setAttribute('aria-current',entry.id===selected?'true':'false');
    text(button,'span',entry.value.user_message||entry.value.question);
    text(button,'small',answerState(entry.value.answer)+' · '+seconds(entry.value.timings?.total_seconds)+(entry.id===lastCompleted?' · 最近一题':''));
    button.disabled=running||submitting;
    button.addEventListener('click',()=>{
      selected=entry.id;editingNew=false;submitError=null;
      $('followup').checked=false;show(entry.value);renderHistory();
      $('progress').textContent=entry.id===lastCompleted?'正在查看最近完成的结果。':'正在查看本页较早的结果。';
      $('progress-hint').textContent='记录仅供回看。追问只会继续最近完成的一题，不会接续这条旧记录。';
      $('elapsed').textContent='';
    });
  });
}
function show(r) {
  result=r; $('welcome').hidden=true; $('export-box').hidden=true; $('export-json').value='';
  $('results').replaceChildren(); $('queries').replaceChildren(); $('answer-box').replaceChildren();
  $('queries-box').hidden=false;$('queries-box').open=false;
  $('sources-box').hidden=false;$('sources-box').open=false;
  $('answer-box').hidden=!r.answer;
  $('result-title').textContent='这次问题的结果';
  text($('answer-box'),'p',r.user_message||r.question,'notice');
  if(!r.answer) text($('results'),'p','本次问题：'+r.question);
  if(r.answer) {
    const a=r.answer;
    const heading=text($('answer-box'),'div','','answer-heading');
    text(heading,'h2','回答');
    const status=text(heading,'span',answerState(a),'answer-status');status.classList.add(a.status);
    (a.answers||[]).forEach(row=>{
      const card=text($('answer-box'),'article','','answer-card');text(card,'p',row.text);
      if(row.citations?.length) {
        const detail=text(card,'details','','citation-details');
        text(detail,'summary','依据 · '+row.citations.length+' 处引用');
        row.citations.forEach(c=>{sourceLink(detail,c);text(detail,'blockquote',c.quote);});
      }
    });
    if(a.calculations) {
      const calc=a.calculations;const card=text($('answer-box'),'article','','calculation-card');
      text(card,'h3','计算结果 · 本机执行');
      (calc.outputs||[]).forEach(o=>{
        const line=text(card,'div','','calc-output');text(line,'span',o.label);
        const value=text(line,'strong',String(o.value));text(value,'small',o.unit||'');
      });
      const detail=text(card,'details','','calc-detail');text(detail,'summary','查看计算过程、参数与来源');
      (calc.inputs||[]).forEach(i=>{
        text(detail,'p',i.label+' = '+i.value+' '+(i.unit||'')+'（'+i.name+' · '+i.source+'）');
        const origin=text(detail,'details','');text(origin,'summary','参数依据');text(origin,'blockquote',i.quote);
      });
      (calc.steps||[]).forEach(step=>{
        text(detail,'p',step.label);text(detail,'code',step.expression+' = '+step.value+' '+(step.unit||''));
        text(detail,'p','精确值：'+step.exact_value,'small');
        (step.citations||[]).forEach(c=>{sourceLink(detail,c);text(detail,'span',' ');});
      });
      if(calc.rounding)text(card,'p',calc.rounding,'notice');
    }
    (a.gaps||[]).forEach(g=>text($('answer-box'),'p',typeof g==='string'?g:(g.reason||JSON.stringify(g)),'gap'));
    if(!a.answers?.length&&!a.calculations&&!a.gaps?.length) text($('answer-box'),'p','本次尚未形成有依据的完整回答，请查看处理详情。','gap');
    text($('answer-box'),'p',a.support_check?'已进行模型依据核对，不等同人工法律审核。请留意上方未完成的部分。':'引用定位不代表已核对法律适用性。请结合原文、年份与具体条件判断。','notice');
  }
  if(r.condition_update) {
    $('answer-box').hidden=false;
    const detail=text($('answer-box'),'details','');
    text(detail,'summary','核对本次采用的条件');text(detail,'pre',r.canonical_question);
    (r.condition_update.patches||[]).forEach(p=>text(detail,'p',p.old+' → '+p.new));
  }
  const t=r.timings||{},chunks=r.chunks||[];
  $('metrics').textContent='本次耗时 '+seconds(t.total_seconds)+' · 找到 '+chunks.length+' 条原文 · '+answerState(r.answer);
  $('sources-label').textContent='查看知识库原文（'+chunks.length+' 条）';
  $('mode').textContent=r.mode==='clarification'?'需要补充条件，本次没有重新检索。':!r.answer?'本次仅检索，没有生成回答。勾选「允许 Kimi 阅读必要依据」后可提交完整问答。':r.planning_status!=='ok'?'本次使用原问题本地检索，未完成问题拆分；请核对是否覆盖了你要问的全部事项。':'以库内文件为依据，不代表正式法律意见；关键决定请核对原文、适用年份与条件。';
  (r.queries||[]).forEach((q,i)=>text($('queries'),'p',(i+1)+'. ['+q.kind+'] '+q.query));
  if(r.planning_failure_kind==='local_proxy')text($('queries'),'p','配置的本地模型代理未连接，未提交云端问题；仅按原问题本地检索，不代表库内缺资料。','notice');
  if(r.planning_status!=='ok')text($('queries'),'p','检索规划状态：'+(r.planning_error_code||r.planning_error||r.planning_status||'未记录'));
  text($('queries'),'p','理解问题 '+seconds(t.planning_seconds)+' / 召回 '+seconds(t.recall_seconds)+' / 粗筛 '+seconds(t.coarse_seconds)+' / 重排 '+seconds(t.rerank_seconds)+(r.answer?' / 回答 '+seconds(t.answer_seconds):'')+'。云端调用尝试 '+(r.cloud_attempts??'未记录')+' 次。分段时间不一定可直接相加。');
  if(r.evidence_packing) {
    const p=r.evidence_packing,d=text($('queries'),'details','');
    text(d,'summary','回答证据包：'+p.sent_count+'/'+p.candidate_count+' 条 · '+p.used_chars+'/'+p.limit_chars+' 字符');
    text(d,'p','方式：'+p.policy+'。已选入不代表云端已收到，也不代表依据齐全。');
    (p.decisions||[]).forEach(x=>text(d,'p','原文 '+x.source_rank+'：'+x.decision+' · '+x.chars+' 字符 · '+x.title));
  }
  if(r.model_call_metrics?.length) {
    const d=text($('queries'),'details','');text(d,'summary','模型请求耗时');
    r.model_call_metrics.forEach(m=>text(d,'p',(m.phase==='draft'?'整理回答':'依据审查')+' · 调用 '+m.call+' · '+(m.status==='ok'?'返回成功':'未完成')+'：'+(m.streaming?'首个事件 '+seconds(m.first_event_seconds)+' / 首个答案片段 '+seconds(m.first_content_seconds):'完整响应 '+seconds(m.response_ready_seconds))+' / 总耗时 '+seconds(m.total_seconds)+' / 答案字符 '+(m.output_chars||0)));
    text(d,'p','等待可能包含连接、排队和模型处理；这些指标不能单独证明是网络慢。不保存或展示模型推理内容。');
  }
  if(!chunks.length)text($('results'),'p','本次未返回片段，不代表知识库一定没有相关资料。');
  chunks.forEach(c=>{
    const card=text($('results'),'article','','card');card.id='source-'+c.rank;
    text(card,'h3',String(c.rank).padStart(2,'0')+'  '+c.title);
    text(card,'div','文档 '+c.document_id+' · 片段 '+c.chunk_id+' · 原文区间 '+c.start+'–'+c.end,'source');
    text(card,'pre',c.text);
  });
  $('download').disabled=false;
}
function buttonCopy() {
  $('submit').textContent=submitting?'正在提交…':running?'正在处理…':$('answer-consent').checked?'发送问题，生成回答':'查找文件依据';
}
async function poll() {
  if(polling)return;polling=true;
  try {
    const s=await api('/api/status');configuration=s.configuration||{};
    $('service').textContent=({starting:'正在加载',ready:'本地服务就绪',unavailable:'服务不可用'})[s.state]||s.state;
    $('service').dataset.state=s.state;
    const job=s.job;running=Boolean(job&&job.state==='running');
    $('cloud').disabled=!s.cloud_enabled||running||submitting;
    $('answer-consent').disabled=!s.answer_enabled||running||submitting;
    if(!s.cloud_enabled)$('cloud').checked=false;
    if(!s.answer_enabled)$('answer-consent').checked=false;
    $('new-question').disabled=running||submitting;
    $('followup').disabled=running||submitting||!s.cloud_enabled||!lastCompleted;
    $('submit').disabled=s.state!=='ready'||running||submitting;buttonCopy();
    $('permission').textContent=s.answer_enabled?'完整问答需勾选阅读依据许可。每次提交后清空许可，下一题由你选择。':'当前未启用云端回答，可以仅检索文件依据。';
    if(submitting)return;
    if(job) {
      active=job.id;
      if(running) {
        if(latestObserved!==job.id) {latestObserved=job.id;if(!submittedAt)submittedAt=Date.now();}
        const copy=stageCopy(job.stage);$('progress').textContent=copy[0];$('progress-hint').textContent=copy[1];
        const elapsed=Number.isFinite(job.elapsed_seconds)?job.elapsed_seconds:(Date.now()-submittedAt)/1000;
        $('elapsed').textContent=(Number.isFinite(job.elapsed_seconds)?'已处理 ':'本页已等待 ')+seconds(elapsed,0)+' · 不自动重试';
        $('welcome').hidden=true;submitError=null;
      } else if(job.state==='complete') {
        lastCompleted=job.id;
        const unseen=history.remember(job.id,job.result);
        if(unseen&&!editingNew) {
          selected=job.id;show(job.result);
          $('progress').textContent=job.result.answer?'本次处理结束 · '+answerState(job.result.answer):'检索完成';
          $('progress-hint').textContent='可以查看下方结果。继续提问时，请明确选择追问还是新问题。';
          $('elapsed').textContent='';submitError=null;
        }
        if(unseen)renderHistory();
        $('followup').disabled=!s.cloud_enabled;
      } else if(job.state==='error') {
        $('progress').textContent=(job.message||'处理未完成')+'（'+(job.error||'未知错误')+'）';
        $('progress-hint').textContent='这是处理故障，不代表库内没有资料。不会自动重新提交。';
        $('elapsed').textContent='';lastCompleted=null;$('followup').disabled=true;
      }
    } else if(s.state==='ready'&&!result) {
      $('progress').textContent='服务已就绪。写下你的问题即可开始。';$('progress-hint').textContent='勾选两项许可可体验完整问答；不勾选则只进行本地检索。';
    } else if(s.state==='unavailable') {
      $('progress').textContent='服务初始化未完成'+(s.initialization_error?'（'+s.initialization_error+'）':'');
      $('progress-hint').textContent='请检查本地模型与服务。此状态与文件有没有答案无关。';
    }
    if(submitError)$('progress').textContent=submitError;
  } catch(error) {
    $('service').textContent='连接中断';$('service').dataset.state='unavailable';$('submit').disabled=true;
    $('progress').textContent=error.code==='service_restarted'?'服务已重启，请刷新页面后继续。':'暂时连不上本地服务。';
    $('progress-hint').textContent=error.code==='service_restarted'?'旧页面会话已失效，刷新即可连接新服务；问题不会自动重发。':'页面会检查连接恢复情况，但不会自动重新发送你的问题。请保持服务启动窗口运行。';
  } finally {polling=false;renderHistory();}
}
$('question').addEventListener('input',()=>{$('question-count').textContent=$('question').value.length+' / 2000';});
$('answer-consent').addEventListener('change',buttonCopy);
$('followup').addEventListener('change',()=>{
  if($('followup').checked&&lastCompleted) {
    const entry=history.get(lastCompleted);
    if(entry) {selected=entry.id;editingNew=false;show(entry.value);renderHistory();}
    $('followup-help').textContent='将继续最近一题：'+(entry?.value.user_message||entry?.value.question||'最近完成的问题')+'。仅发送上一轮用户条件和本次问题；15 分钟过期，不发送助手旧答案。';
  } else $('followup-help').textContent='追问会发送上一轮用户条件和本次问题，不发送助手旧答案。条件 15 分钟有效；换话题请点「新问题」。';
});
$('new-question').addEventListener('click',()=>{
  editingNew=true;selected=null;submitError=null;submittedAt=null;
  $('question').value='';$('question-count').textContent='0 / 2000';$('followup').checked=false;
  $('followup-help').textContent='追问会发送上一轮用户条件和本次问题，不发送助手旧答案。条件 15 分钟有效；换话题请点「新问题」。';
  $('cloud').checked=false;$('answer-consent').checked=false;
  resetView();renderHistory();buttonCopy();$('result-title').textContent='开始一个新问题';
  $('progress').textContent='已切换到新问题，不会沿用上一题条件。';$('progress-hint').textContent='填写新问题，并选择本次允许的处理方式。';
  $('question').focus();
});
$('submit').addEventListener('click',async()=>{
  const question=$('question').value.trim();
  if(!question){$('progress').textContent='请先输入问题。';$('question').focus();return;}
  if(submitting||running)return;
  if($('followup').checked&&(!lastCompleted||active!==lastCompleted)) {$('progress').textContent='上一题无法继续，请点「新问题」并完整输入条件。';return;}
  submitting=true;submitError=null;submittedAt=Date.now();editingNew=false;selected=null;
  $('submit').disabled=true;resetView();renderHistory();buttonCopy();$('result-title').textContent='正在处理你的问题';
  const payload={question,cloud_consent:$('cloud').checked,answer_consent:$('answer-consent').checked,...($('followup').checked?{base_id:lastCompleted,context_consent:true}:{})};
  try {
    const response=await api('/api/search',payload);active=response.id;latestObserved=response.id;
    $('cloud').checked=false;$('answer-consent').checked=false;$('followup').checked=false;
    $('progress').textContent='问题已收到，开始处理。';
  } catch(error) {
    submitError=error.name==='AbortError'?'提交状态暂未确认。请等待页面检查，不要重复发送。':error.message;
    $('progress').textContent=submitError;
  } finally {submitting=false;await poll();}
});
$('download').addEventListener('click',()=>{
  if(!result)return;$('export-json').value=JSON.stringify(result,null,2);$('export-box').hidden=false;
  $('export-json').focus();$('export-json').select();
});
$('save-json').addEventListener('click',()=>{
  if(!result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));
  const link=document.createElement('a');link.href=url;link.download='v5-answer-'+(selected||'current')+'.json';link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
});
poll();setInterval(poll,1500);
}
