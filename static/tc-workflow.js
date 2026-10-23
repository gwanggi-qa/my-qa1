(() => {
  'use strict';
  const $=id=>document.getElementById('tc-flow-'+id);
  let metadata={},steps=[],flow=null,timer=null,version=0,busy=false;
  const api=async(path,body)=>{const url='/api/tc-workflow/'+path;const response=path.endsWith('/start')?await window.startTestWithReview(url,body):await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.error||'실행 요청 실패');return data;};
  function message(text){$('message').textContent=text;}
  function canStart(){return flow?.state==='ready'&&(flow.executable_count??flow.cases?.filter(c=>c.plan&&!c.plan.blocked_reason).length??0)>0;}
  const planLabels={pending:'분석 대기',retrying:'재계획 중',ready:'준비 완료',repaired:'재계획 완료',fallback:'일반 실행 전환',blocked:'실행 불가'};
  function render(){const state=flow?.state||'selection';
    document.querySelectorAll('#tc-flow-steps [data-phase]').forEach(el=>{const active=el.dataset.phase===state;el.style.color=active?'#53d8c0':'';el.style.fontWeight=active?'700':'';el.setAttribute('aria-current',active?'step':'false');});
    const active=['preparing','running'].includes(state);
    if(['finished','stopped'].includes(state)&&flow?.results&&window.setWorkbookExport){
      window.setWorkbookExport({workflow_id:flow.id,run_id:flow.results.run_id,filename:'testcase-results.xlsx'});
    }
    $('start').disabled=busy||!canStart();$('stop').disabled=busy||!['preparing','ready','running'].includes(state);
    $('export').disabled=busy||!['finished','stopped'].includes(state)||!flow?.results;$('consent').disabled=busy||active;
    document.querySelectorAll('.tc-flow-selection').forEach(e=>e.disabled=busy||active);
    if(flow){message(flow.message);$('plan').hidden=false;$('cases').replaceChildren();
      for(const c of flow.cases){const row=document.createElement('section');row.style.cssText='padding:8px 0;border-bottom:1px solid #3b414a;white-space:pre-wrap;overflow-wrap:anywhere';
        const actions=c.plan?.actions?.map((a,i)=>`${i+1}. ${a.basis==='expected'?'기대결과 보완':'절차 '+a.procedure}: ${a.action} ${a.target}${a.source?'\n근거: '+a.source:''}`).join('\n')||'';
        const checkpoints=c.plan?.checks?.map(c=>`동작 ${c.after_action} 직후 기대결과 ${c.expectation} 검증`).join('\n')||'';
        const diagnostic=(c.scope_note?`\n검증 범위: ${c.scope_note}`:'')+(c.planning?`\n계획 상태: ${planLabels[c.planning.status]||c.planning.status} · 재계획 ${c.planning.retry_count}/1\n${(c.planning.errors||[]).map(e=>`${e.tc_id} · ${e.code} · ${e.field}: ${e.message}\n${e.hint}`).join('\n')}`:'');
        row.textContent=`${c.case_no} · ${c.title}\n${c.priority||'-'} · ${c.test_type||'-'} · ${(c.depths||[]).filter(Boolean).join(' > ')||'-'}\n목적: ${c.purpose||'-'}\n사전조건: ${c.precondition||'(원문 공란)'}\n${c.procedures.join('\n')}\n기대결과: ${c.expected||'(없음)'}\n${c.plan?.blocked_reason?'Block: '+c.plan.blocked_reason:actions||'계획 준비 중'}${diagnostic}${checkpoints?'\n안전 보완: 사전조건 준비·기존 항목 선택, TC당 최대 5회\n'+checkpoints:''}`;$('cases').append(row);}
    }
  }
  async function poll(){const serial=version;clearTimeout(timer);try{const data=await api('current');if(serial!==version)return;
    if(data.workflow&&data.workflow.file_hash===metadata.file_hash&&data.workflow.upload_id===metadata.upload_id){flow=data.workflow;render();if(['preparing','running'].includes(flow.state))timer=setTimeout(poll,1500);}
  }catch(e){if(serial===version){message('상태 확인 지연: '+e.message);timer=setTimeout(poll,5000);}}}
  function uploaded(data){const changed=metadata.upload_id!==data.metadata?.upload_id;metadata=data.metadata||{};steps=data.steps||[];
    if(changed){++version;clearTimeout(timer);flow=null;$('consent').checked=false;$('plan').hidden=true;message(`${steps.length}개 TC · 기대결과를 판정 기준으로 사용 · 실행결과는 별도 저장`);render();poll();}}
  window.addEventListener('tc-uploaded',e=>uploaded(e.detail));
  window.tcWorkflow={prepare:async aiMode=>{if(busy||['preparing','running'].includes(flow?.state))return;
    const indices=[...document.querySelectorAll('.tc-flow-selection:checked')].map(e=>Number(e.value));
    if(!indices.length||indices.length>20){message('목록에서 원본 순서대로 1~20개 TC를 선택하세요.');return;}
    if(aiMode&&!$('consent').checked){message('AI TC RUN은 선택 TC와 현재 화면을 분석합니다. 아래 AI 분석 동의를 확인하세요.');return;}
    busy=true;render();message(aiMode?'AI 절차 준비 요청 중':'일반 실행 절차 준비 중');const serial=++version;
    try{const data=await api('prepare',{upload_id:metadata.upload_id,file_hash:metadata.file_hash,indices,ai_mode:aiMode,consent:$('consent').checked});if(serial!==version)return;flow=data.workflow;$('plan').open=true;render();poll();}
    catch(e){message(e.message);}finally{busy=false;$('start').disabled=!canStart();$('stop').disabled=!['preparing','ready','running'].includes(flow?.state);$('consent').disabled=['preparing','running'].includes(flow?.state);document.querySelectorAll('.tc-flow-selection').forEach(e=>e.disabled=['preparing','running'].includes(flow?.state));}
  }};
  $('start').onclick=async()=>{if(!flow||busy)return;busy=true;render();let error='';try{const data=await api(flow.id+'/start',{plan_digest:flow.plan_digest,approved:true});flow=data.workflow;render();poll();}catch(e){error=e.message;}finally{busy=false;render();if(error)message(error);}};
  $('stop').onclick=async()=>{if(!flow||busy)return;busy=true;render();let error='';try{await api(flow.id+'/stop',{});message('중단 처리 중');poll();}catch(e){error=e.message;}finally{busy=false;render();message(error||'중단 처리 중');}};
  $('export').onclick=async()=>{if(!flow||busy)return;busy=true;render();let error='';try{const res=await fetch('/api/tc-workflow/'+flow.id+'/export');if(!res.ok)throw Error((await res.json()).error);const url=URL.createObjectURL(await res.blob());const a=document.createElement('a');a.href=url;a.download='testcase-results.xlsx';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}catch(e){error=e.message;}finally{busy=false;render();if(error)message(error);}};
  window.addEventListener('pagehide',()=>{++version;clearTimeout(timer);});
  fetch('/api/testcase').then(r=>r.json()).then(uploaded).catch(e=>message(e.message));
})();
