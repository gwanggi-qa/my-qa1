(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const node=(tag,text)=>{const el=document.createElement(tag);el.textContent=text;return el;};
  const fields=['title','objective','preconditions','steps','expected_results','priority','depth1','depth2','depth3','test_type','category','source_ids','assumptions'];
  const states={running:'생성 중',completed:'초안 생성 완료 · 미실행',failed:'생성 실패',stopped:'생성 중단',interrupted:'서버 재시작으로 중단'};
  const reviews={draft:'생성 완료·미검토',approved:'검토 승인',rejected:'제외'};
  let sha='',epoch=0,prepared=null,batch=null,caseId='',busy=false,figmaBusy=false,timer=null,dirty=false;
  let integrations={figma:{configured:false},ai:{configured:false}},imageUrls=[],sourceKey='';
  let clock=null,elapsedAt=0,elapsedBase=0,stoppingId='',pollDelayed=false,loadVersion=0;
  const errors={timeout:'시간 초과',authentication:'인증 필요',usage_limit:'사용량 한도',connection:'연결 오류',invalid_response:'응답 형식 오류',cli_execution:'CLI 실행 오류',unknown:'기타 오류'};
  const duration=seconds=>{const n=Math.max(0,Math.floor(seconds));return String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0');};
  function clearClock(){clearInterval(clock);clock=null;$('draft-clock').hidden=true;}
  function tick(){if(!batch)return;const running=batch.status==='running';
    const elapsed=elapsedBase+(running?(performance.now()-elapsedAt)/1000:0);
    const estimate=batch.progress?.remaining_seconds;
    $('draft-clock').hidden=false;$('draft-clock').textContent=(running?'경과 ':'총 소요 ')+duration(elapsed)+(running?(batch.progress?(Number.isFinite(estimate)?' · 예상 남은 시간 약 '+duration(estimate)+' (추정)':' · 예상 시간 계산 중'):(batch.timeout_seconds?' / 요청 제한 '+duration(batch.timeout_seconds):'')):'');}
  function syncClock(){clearClock();if(!batch)return;
    elapsedBase=Number.isFinite(batch.elapsed_seconds)?batch.elapsed_seconds:Math.max(0,((batch.finished_at?Date.parse(batch.finished_at):Date.now())-Date.parse(batch.created_at))/1000);
    if(!Number.isFinite(elapsedBase))return;elapsedAt=performance.now();tick();if(batch.status==='running')clock=setInterval(tick,1000);}
  function batchMessage(){if(!batch)return;const running=batch.status==='running',metrics=batch.metrics||{};
    let label=running&&(batch.stopping||stoppingId===batch.id)?'중단 처리 중':(states[batch.status]||batch.status);
    if(batch.status==='failed')label+=' ['+(errors[batch.error_code]||'원인 미분류')+']';
    if(batch.error)label+=' · '+batch.error;
    if(batch.progress){const p=batch.progress;label+=' · '+(p.total===null?'검증 범위 분석 중':p.completed+'/'+p.total+'묶음')+' · '+p.generated+'개 저장';}
    label+=running?' · AI 응답 대기':' · AI 요청 '+(metrics.planner_calls||0)+'회';
    if(!running)label+=metrics.usage&&Object.keys(metrics.usage).length?' · 토큰 '+JSON.stringify(metrics.usage):' · 사용량 확인 불가';
    if(pollDelayed)label+=' · 상태 확인 지연';message(label,batch.status==='failed'||pollDelayed);}
  function message(text,error=false){$('draft-message').textContent=text;$('draft-message').classList.toggle('error',error);}
  async function api(path,options={}){const res=await fetch('/api/app-analysis/'+path,options);const data=await res.json();if(!res.ok)throw Error(data.error||'요청 실패');return data;}
  const post=body=>({method:'POST',body:JSON.stringify(body),headers:{'Content-Type':'application/json'}});
  function controls(){const running=batch?.status==='running';$('figma-import').disabled=!sha||figmaBusy||!integrations.figma.configured;
    for(const id of ['draft-scope','draft-count','draft-images'])$(id).disabled=busy||running;
    document.querySelectorAll('#draft-sources input').forEach(input=>input.disabled=busy||running);
    for(const key of fields)$('case-'+key).disabled=busy;
    $('case-review-status').disabled=busy;
    $('draft-preview').disabled=!sha||busy||running;$('draft-generate').disabled=!prepared||!$('draft-consent').checked||!integrations.ai.configured||busy||running;
    $('draft-stop').disabled=!running||busy||batch?.stopping||stoppingId===batch?.id;$('draft-export').disabled=!batch?.cases?.some(c=>c.review_status!=='rejected')||busy;
    $('draft-resume').disabled=!batch?.progress?.resumable||busy;
    $('case-save').disabled=!caseId||busy||batch?.status!=='completed';$('draft-history').disabled=busy||running;}
  function invalidate(){prepared=null;$('draft-consent').checked=false;$('draft-consent').disabled=true;$('draft-input').hidden=true;imageUrls.forEach(URL.revokeObjectURL);imageUrls=[];$('draft-input-images').replaceChildren();controls();}
  function selection(){return {reference_ids:[...document.querySelectorAll('#draft-sources input:checked')].map(e=>e.value),scope:$('draft-scope').value,count:Number($('draft-count').value),include_images:$('draft-images').checked};}
  async function connection(){try{integrations=await api('integrations');$('figma-status').textContent=integrations.figma.configured?'Figma: 서버 인증 설정됨 · 파일 권한은 가져올 때 확인':'Figma: FIGMA_ACCESS_TOKEN 설정 필요 (file_content:read)';$('draft-provider').textContent='AI: '+(integrations.ai.provider||'미설정')+' / '+(integrations.ai.model||'-')+(integrations.ai.configured?'':' · 연결 필요');controls();}catch(e){message(e.message,true);}}
  function caseOptions(){const current=caseId;$('draft-case').replaceChildren();for(const c of batch?.cases||[]){const op=node('option',c.id+' · '+reviews[c.review_status]+' · '+c.title);op.value=c.id;$('draft-case').append(op);}caseId=batch?.cases?.some(c=>c.id===current)?current:(batch?.cases?.[0]?.id||'');$('draft-case').value=caseId;renderCase();}
  function renderCase(){const c=batch?.cases?.find(c=>c.id===caseId);$('draft-case-form').hidden=!c;if(!c)return;
    for(const key of fields)$('case-'+key).value=Array.isArray(c[key])?c[key].join(key==='source_ids'?', ':'\n'):(c[key]||'');
    $('case-review-status').value=c.review_status;
    $('case-evidence').textContent=batch.context.evidence.filter(e=>c.source_ids.includes(e.id)).map(e=>e.id+' · '+e.locator+'\n'+e.text).join('\n\n');dirty=false;controls();}
  function renderBatch(){const running=batch?.status==='running';$('draft-review').hidden=!batch?.cases?.length;
    syncClock();if(batch){batchMessage();
      $('draft-summary').textContent=batch.original?.summary||'';$('draft-questions').textContent=(batch.original?.questions||[]).join('\n');$('draft-original').textContent=JSON.stringify(batch.original,null,2);if(!running)caseOptions();}
    controls();}
  async function loadBatch(id){const e=epoch,current=sha,version=++loadVersion;clearTimeout(timer);try{const data=await api('builds/'+current+'/drafts/'+id);if(e!==epoch||version!==loadVersion)return;batch=data.draft;pollDelayed=false;if(batch.status!=='running')stoppingId='';renderBatch();if(batch.status==='running')timer=setTimeout(()=>loadBatch(id),1500);}catch(err){if(e===epoch&&version===loadVersion){pollDelayed=true;if(batch?.id===id)batchMessage();else message('상태 확인 지연 · '+err.message,true);timer=setTimeout(()=>loadBatch(id),5000);}}}
  async function history(preferred=''){const e=epoch,current=sha;if(!sha)return;const data=await api('builds/'+sha+'/drafts');if(e!==epoch)return;
    $('draft-history').replaceChildren();for(const d of data.drafts){const op=node('option',new Date(d.created_at).toLocaleString('ko-KR')+' · '+(states[d.status]||d.status));op.value=d.id;$('draft-history').append(op);}
    const id=data.drafts.some(d=>d.id===preferred)?preferred:data.drafts[0]?.id;if(id){$('draft-history').value=id;await loadBatch(id);}else{$('draft-history').append(node('option','생성 이력 없음'));batch=null;renderBatch();}}
  window.addEventListener('qa-build-selected',e=>{if(sha===e.detail.sha)return;sha=e.detail.sha;++epoch;clearTimeout(timer);clearClock();stoppingId='';pollDelayed=false;batch=null;caseId='';dirty=false;sourceKey='';$('draft-sources').replaceChildren();$('draft-review').hidden=true;invalidate();message('초안 검토 대기 · 미실행');history().catch(err=>message(err.message,true));});
  window.addEventListener('qa-references-updated',e=>{if(sha!==e.detail.sha)return;const key=JSON.stringify(e.detail.references.map(r=>[r.id,r.sha256]));if(key===sourceKey)return;sourceKey=key;
    const old=new Set(selection().reference_ids);$('draft-sources').replaceChildren();for(const r of e.detail.references){const label=node('label','');label.className='check';const input=document.createElement('input');input.type='checkbox';input.value=r.id;input.checked=old.has(r.id);input.onchange=invalidate;label.append(input,node('span',r.filename+' · '+r.kind+(r.status==='ready'?'':' · 부분 자료')));$('draft-sources').append(label);}invalidate();});
  $('figma-form').onsubmit=async e=>{e.preventDefault();if(!sha||figmaBusy)return;const current=sha;figmaBusy=true;controls();$('figma-status').textContent='Figma 프레임 읽는 중';
    try{const data=await api('builds/'+current+'/figma',post({url:$('figma-url').value}));if(current===sha){$('figma-status').textContent=data.duplicate?'동일한 버전이 등록되어 있습니다.':'프레임 가져오기 완료 · '+data.reference.preview.version;window.dispatchEvent(new CustomEvent('qa-refresh-references',{detail:{id:data.reference.id}}));}}
    catch(err){$('figma-status').textContent=err.message;}finally{figmaBusy=false;controls();}};
  for(const id of ['draft-scope','draft-count','draft-images'])$(id).addEventListener('input',invalidate);
  $('draft-consent').onchange=controls;
  $('draft-form').onsubmit=async e=>{e.preventDefault();if(busy)return;const serial=epoch,current=sha,settings=selection();invalidate();busy=true;controls();
    try{const data=await api('builds/'+current+'/draft-context',post(settings));if(serial!==epoch)return;
      prepared={...settings,context_hash:data.context_hash};$('draft-context').textContent=JSON.stringify(data.context,null,2);$('draft-input').hidden=false;$('draft-input').open=true;
      for(const img of data.context.images){const evidence=data.context.evidence.find(v=>v.id===img.evidence_id);const res=await fetch('/api/app-analysis/builds/'+current+'/references/'+evidence.reference_id+'/image');if(!res.ok)throw Error('전송 이미지 확인 실패');const blob=await res.blob();if(serial!==epoch)return;const url=URL.createObjectURL(blob);imageUrls.push(url);const preview=document.createElement('img');preview.src=url;preview.alt=img.evidence_id;$('draft-input-images').append(node('span',img.evidence_id),preview);}
      $('draft-consent').disabled=false;message('전송 내용 확인 대기 · '+data.context.evidence.length+'개 근거');
    }catch(err){invalidate();message(err.message,true);}finally{busy=false;controls();}};
  $('draft-generate').onclick=async()=>{if(!prepared||!$('draft-consent').checked||busy)return;const current=sha,serial=epoch;busy=true;controls();try{const data=await api('builds/'+current+'/drafts',post({...prepared,consent:true}));if(serial!==epoch)return;invalidate();batch=data.draft;await history(batch.id);}catch(err){message(err.message,true);}finally{busy=false;controls();}};
  $('draft-stop').onclick=async()=>{if(!batch||busy)return;const id=batch.id,current=sha,serial=epoch;busy=true;stoppingId=id;batchMessage();controls();try{await api('builds/'+current+'/drafts/'+id+'/stop',post({}));if(serial===epoch)await loadBatch(id);}catch(err){if(serial===epoch){stoppingId='';message('중단 요청 실패 · '+err.message,true);}}finally{busy=false;controls();}};
  $('draft-resume').onclick=async()=>{if(!batch||busy||!confirm('저장된 참고자료로 미완료 묶음의 AI 생성을 재개할까요?'))return;const id=batch.id,current=sha,serial=epoch;busy=true;controls();try{await api('builds/'+current+'/drafts/'+id+'/resume',post({consent:true}));if(serial===epoch)await loadBatch(id);}catch(err){message(err.message,true);}finally{busy=false;controls();}};
  $('draft-history').onchange=()=>{if(dirty&&!confirm('저장하지 않은 검토 내용을 버릴까요?')){$('draft-history').value=batch.id;return;}dirty=false;caseId='';loadBatch($('draft-history').value);};
  $('draft-case').onchange=()=>{if(dirty&&!confirm('저장하지 않은 검토 내용을 버릴까요?')){$('draft-case').value=caseId;return;}caseId=$('draft-case').value;renderCase();};
  for(const key of fields)$('case-'+key).oninput=()=>{dirty=true;$('case-review-status').value='draft';};
  $('case-review-status').onchange=()=>{dirty=true;};
  $('draft-case-form').onsubmit=async e=>{e.preventDefault();if(!batch||busy)return;const current=sha,serial=epoch;const payload={revision:batch.revision,review_status:$('case-review-status').value};
    for(const key of fields){const value=$('case-'+key).value;payload[key]=['steps','expected_results','assumptions','source_ids'].includes(key)?value.split(key==='source_ids'?',':'\n').map(s=>s.trim()).filter(Boolean):value;}
    busy=true;controls();try{const data=await api('builds/'+current+'/drafts/'+batch.id+'/cases/'+caseId,{...post(payload),method:'PATCH'});if(serial!==epoch)return;batch=data.draft;renderBatch();message('검토 저장 완료 · 실행은 수행하지 않았습니다.');}catch(err){message(err.message,true);}finally{busy=false;controls();}};
  $('draft-export').onclick=async()=>{if(!batch||busy)return;busy=true;controls();try{const res=await fetch('/api/app-analysis/builds/'+sha+'/drafts/'+batch.id+'/export');if(!res.ok)throw Error((await res.json()).error);const url=URL.createObjectURL(await res.blob()),link=document.createElement('a');link.href=url;link.download='reviewed-testcases.xlsx';link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}catch(err){message(err.message,true);}finally{busy=false;controls();}};
  $('draft-template').onclick=async()=>{const button=$('draft-template');button.disabled=true;try{const res=await fetch('/api/app-analysis/testcase-template');if(!res.ok)throw Error('표준 양식 다운로드 실패');const url=URL.createObjectURL(await res.blob()),a=document.createElement('a');a.href=url;a.download='testcase-template.xlsx';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}catch(e){message(e.message,true);}finally{button.disabled=false;}};
  $('refresh').addEventListener('click',()=>{connection();if(!dirty)history(batch?.id).catch(err=>message(err.message,true));});
  window.addEventListener('pagehide',()=>{++epoch;clearTimeout(timer);clearClock();imageUrls.forEach(URL.revokeObjectURL);});
  connection();
})();
