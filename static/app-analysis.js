(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const node = (tag, text) => { const e=document.createElement(tag); e.textContent=text; return e; };
  const names = {running:'분석 중',completed:'정적 분석 완료',failed:'분석 실패',stopped:'중단됨',interrupted:'재시작으로 중단'};
  let selected='', build=null, ready=false, tab='components', timer=null, requestId=0, busy=false;
  let referenceBuild='',reference=null,referenceBusy=false,referenceSerial=0,imageUrl='';
  const kindNames={requirements:'기획서',design:'디자인',testcase:'기존 TC'};
  const referenceStates={ready:'미리보기 준비됨',partial:'부분 추출 · 검토 필요',visual_only:'이미지 · 텍스트 미추출'};
  const warningNames={
    'Image registered; OCR and AI interpretation have not been performed.':'이미지 등록 완료 · OCR·AI 해석 미수행',
    'Some pages have no extractable text; visual review/OCR is required.':'텍스트를 읽을 수 없는 페이지가 있습니다. 이미지 검토 또는 OCR 필요',
    'PDF text preview truncated.':'PDF 텍스트 일부만 미리보기에 포함되었습니다.',
    'Only the first 100 PDF pages are previewed.':'PDF 앞 100페이지만 미리보기에 포함되었습니다.',
    'Only the first 20 sheets are previewed.':'앞 20개 시트만 미리보기에 포함되었습니다.',
    'Text preview limited to 100,000 characters.':'텍스트 앞 100,000자만 미리보기에 포함되었습니다.',
    'Cell/text preview truncated.':'셀 내용 또는 전체 텍스트 일부가 미리보기에서 생략되었습니다.',
    'Formulas are shown literally, never evaluated. Merged values remain at their original cells.':'수식 미계산 · 병합 셀 값은 원래 위치에 표시'
  };
  function message(text,error=false){$('message').textContent=text;$('message').classList.toggle('error',error);}
  async function api(path,options){const r=await fetch('/api/app-analysis/'+path,options);const data=await r.json();if(!r.ok)throw Error(data.error||'요청 실패');return data;}
  function controls(){const running=build?.job?.status==='running';$('analyze').disabled=!build||!ready||running||busy;$('stop').disabled=!running||busy;$('upload').disabled=busy;referenceControls();}
  function referenceControls(){for(const id of ['reference-upload','reference-file','reference-kind','reference-source'])$(id).disabled=!selected||referenceBusy;$('reference-delete').disabled=!reference||referenceBusy;}
  function referenceMessage(text,error=false){$('reference-message').textContent=text;$('reference-message').classList.toggle('error',error);}
  function clearReference(){++referenceSerial;reference=null;if(imageUrl)URL.revokeObjectURL(imageUrl);imageUrl='';$('reference-name').textContent='선택된 자료 없음';$('reference-meta').replaceChildren();$('reference-content').replaceChildren();$('reference-warning').textContent='';$('reference-unit-label').hidden=true;referenceControls();}
  function referenceUnit(){
    const p=reference?.preview,unit=p?.units?.[Number($('reference-unit').value)];$('reference-content').replaceChildren();if(!unit)return;
    if(p.format==='workbook'){
      const table=node('table',''),body=node('tbody','');
      for(const row of unit.rows)for(const cell of row.cells){const tr=node('tr','');tr.append(node('td',cell.cell),node('td',cell.value+(cell.formula?' [수식 · 미계산]':'')));body.append(tr);}
      table.append(body);$('reference-content').append(table);
      if(unit.sheet_state!=='visible')$('reference-content').prepend(node('p','숨김 시트: '+unit.sheet_state));
      if(unit.merged_ranges.length)$('reference-content').prepend(node('p','병합 셀: '+unit.merged_ranges.join(', ')));
    }else $('reference-content').append(node('pre',p.format==='text'?unit.lines.map(l=>l.line+' | '+l.text).join('\n'):unit.text||'추출 가능한 텍스트 없음'));
  }
  async function chooseReference(id){
    const sha=selected,serial=++referenceSerial;
    try{const data=await api('builds/'+sha+'/references/'+id);if(sha!==selected||serial!==referenceSerial)return;
      reference=data.reference;const p=reference.preview;$('reference-name').textContent=reference.filename;
      metadata('reference-meta',[['유형',kindNames[reference.kind]],['상태',referenceStates[p.status]],['SHA-256',reference.sha256],['등록 시각',new Date(reference.created_at).toLocaleString('ko-KR')],['원본 주소',reference.source_url||'-']]);
      $('reference-warning').textContent=[...(p.warnings||[]),...(p.notes||[])].map(w=>warningNames[w]||w.replace(': preview limited to 200 rows / 30 columns / 200 merges.',': 앞 200행·30열·병합 범위 200개만 미리보기')).join('\n');
      $('reference-unit').replaceChildren();for(const [i,u]of (p.units||[]).entries()){const option=node('option',u.label);option.value=i;$('reference-unit').append(option);}
      $('reference-unit-label').hidden=!(p.units?.length);referenceUnit();
      if(imageUrl)URL.revokeObjectURL(imageUrl);imageUrl='';
      if(p.format==='image'||p.image_available){
        const r=await fetch('/api/app-analysis/builds/'+sha+'/references/'+id+'/image');if(!r.ok)throw Error('이미지 미리보기 조회 실패');const blob=await r.blob();
        if(sha!==selected||serial!==referenceSerial)return;imageUrl=URL.createObjectURL(blob);const img=document.createElement('img');img.src=imageUrl;img.alt=reference.filename+' 미리보기';$('reference-content').prepend(img);
      }
      document.querySelectorAll('#reference-list button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.id===id)));referenceControls();
    }catch(e){if(sha===selected&&serial===referenceSerial)referenceMessage(e.message,true);}
  }
  async function loadReferences(sha,preferred=''){
    try{const data=await api('builds/'+sha+'/references');if(sha!==selected)return;
      $('reference-list').replaceChildren();$('reference-count').textContent=data.references.length+'개';
      window.dispatchEvent(new CustomEvent('qa-references-updated',{detail:{sha,references:data.references}}));
      for(const r of data.references){const button=node('button','');button.type='button';button.dataset.id=r.id;button.append(node('strong',r.filename),node('small',kindNames[r.kind]+' · '+referenceStates[r.status]));button.onclick=()=>chooseReference(r.id);$('reference-list').append(button);}
      if(!data.references.length){$('reference-list').append(node('p','등록된 참고자료 없음'));clearReference();}
      else await chooseReference(data.references.some(r=>r.id===preferred)?preferred:data.references[0].id);
    }catch(e){if(sha===selected)referenceMessage(e.message,true);}
  }
  function metadata(id,values){const target=$(id);target.replaceChildren();for(const [k,v]of values){target.append(node('dt',k),node('dd',v??'미확인'));}}
  function inventory(){
    const result=build?.job?.result,head=$('table-head'),body=$('table-body');head.replaceChildren();body.replaceChildren();
    const row=node('tr','');for(const label of tab==='components'?['유형','이름','Exported']:['권한 이름'])row.append(node('th',label));head.append(row);
    const query=$('search').value.trim().toLowerCase();
    const entries=(result?.[tab]||[]).filter(v=>JSON.stringify(v).toLowerCase().includes(query));
    for(const v of entries){const r=node('tr','');for(const value of tab==='components'?[v.type,v.name,v.exported]:[v])r.append(node('td',value));body.append(r);}
    if(!entries.length){const r=node('tr',''),c=node('td','표시할 선언 없음');c.colSpan=tab==='components'?3:1;r.append(c);body.append(r);}
  }
  function render(){
    $('build-name').textContent=build?.filename||'선택된 빌드 없음';
    const job=build?.job,status=job?.status;
    $('job-status').textContent=names[status]||'분석 전';$('job-status').className='badge '+(status||'');
    metadata('build-meta',build?[['SHA-256',build.sha256],['파일 크기',(build.size_bytes/1024/1024).toFixed(2)+' MB'],['등록 시각',new Date(build.created_at).toLocaleString('ko-KR')],['분석 시각',job?new Date(job.started_at).toLocaleString('ko-KR'):'미실행']]:[]);
    const r=job?.result;$('analysis-result').hidden=!r;
    if(r){
      $('metrics').replaceChildren();
      for(const [name,value]of [['클래스',r.class_count],['리소스',r.resource_count],['컴포넌트',r.components.length]]){const box=node('div','');box.className='metric';box.append(node('strong',value??'미확인'),node('span',name));$('metrics').append(box);}
      metadata('package-meta',[['패키지',r.package_name],['버전',r.version_name+' ('+r.version_code+')'],['SDK','min '+r.min_sdk+' / target '+r.target_sdk],['분석 엔진','REA '+r.rea_version+' / JADX '+(r.engine?.version||'미확인')],['근거','AndroidManifest.xml · '+r.manifest_status],['서명 검증','미수행']]);
    }
    inventory();controls();
  }
  async function select(sha){
    if(referenceBuild!==sha){referenceBuild=sha;clearReference();$('reference-list').replaceChildren();$('reference-count').textContent='0개';$('reference-file').value='';$('reference-source').value='';referenceMessage('자료 등록: AI 미사용');}
    selected=sha;const serial=++requestId;clearTimeout(timer);
    window.dispatchEvent(new CustomEvent('qa-build-selected',{detail:{sha}}));
    try{const data=await api('builds/'+sha);if(serial!==requestId)return;const previous=build?.job?.status;build=data.build;render();
      $('reference-build').textContent=build.filename+' · '+sha.slice(0,12);
      if(!$('reference-list').children.length)await loadReferences(sha);
      if(serial!==requestId)return;
      document.querySelectorAll('.build').forEach(b=>{b.classList.toggle('selected',b.dataset.sha===sha);b.setAttribute('aria-pressed',String(b.dataset.sha===sha));});
      if(build.job?.error)message(build.job.error,true);
      else if(previous==='running'&&build.job?.status==='completed')message('정적 분석 완료 · 실행 검증은 미수행');
      if(build.job?.status==='running')timer=setTimeout(()=>select(sha),1200);
    }catch(e){if(serial===requestId){message(e.message,true);timer=setTimeout(()=>select(sha),5000);}}
  }
  async function refresh(){
    const [env,data]=await Promise.all([api('environment'),api('builds')]);ready=env.ready;
    $('environment').replaceChildren();
    for(const [name,value,ok]of [['REA',env.rea_version,!!env.rea_version],['JADX',env.jadx_present?'설치됨':'미설치',env.jadx_present],['Java',env.java_version,env.java_ready],['Appium',env.appium_version,!!env.appium_version],['UiAutomator2',env.uiautomator2_version,!!env.uiautomator2_version]]){
      const e=node('span',''),dot=node('i','');dot.className='dot'+(ok?' ready':'');e.append(dot,document.createTextNode(name+': '+(value||'미확인')));$('environment').append(e);
    }
    $('build-list').replaceChildren();$('build-count').textContent=data.builds.length;
    for(const b of data.builds){const button=node('button','');button.type='button';button.className='build';button.dataset.sha=b.sha256;button.append(node('strong',b.filename),node('small',b.sha256.slice(0,12)+' · '+(b.size_bytes/1024/1024).toFixed(2)+' MB'));button.onclick=()=>{message('');select(b.sha256);};$('build-list').append(button);}
    if(!data.builds.length)$('build-list').append(node('p','등록된 APK 없음'));
    if(data.builds.length)await select(data.builds.some(b=>b.sha256===selected)?selected:data.builds[0].sha256);else controls();
  }
  $('upload-form').onsubmit=async e=>{e.preventDefault();if(busy)return;const file=$('apk-file').files[0];if(!file)return;
    if(!file.name.toLowerCase().endsWith('.apk')||file.size>200*1024*1024){message('200 MB 이하의 단일 APK를 선택해 주세요.',true);return;}
    busy=true;controls();message('APK 등록 중');try{const form=new FormData();form.append('file',file);const data=await api('builds',{method:'POST',body:form});selected=data.build.sha256;await refresh();message(data.duplicate?'동일한 APK가 이미 등록되어 있습니다.':'빌드 등록 완료');}catch(e){message(e.message,true);}finally{busy=false;controls();}
  };
  $('analyze').onclick=async()=>{if(!build||busy)return;busy=true;controls();message('정적 분석 시작 중');try{await api('builds/'+selected+'/analyze',{method:'POST'});await select(selected);message('정적 분석 진행 중 · 기기 입력 없음');}catch(e){message(e.message,true);}finally{busy=false;controls();}};
  $('stop').onclick=async()=>{if(!build?.job||busy)return;busy=true;controls();try{await api('jobs/'+build.job.id+'/stop',{method:'POST'});message('분석 중단 요청됨');await select(selected);}catch(e){message(e.message,true);}finally{busy=false;controls();}};
  $('refresh').onclick=async()=>{try{await refresh();if(selected)await loadReferences(selected,reference?.id);}catch(e){message(e.message,true);}};
  for(const name of ['components','permissions'])$(name+'-tab').onclick=()=>{tab=name;for(const n of ['components','permissions'])$(n+'-tab').setAttribute('aria-selected',String(n===name));$('inventory').setAttribute('aria-labelledby',name+'-tab');inventory();};
  $('search').oninput=inventory;
  $('reference-unit').onchange=referenceUnit;
  window.addEventListener('qa-refresh-references',e=>{if(selected)loadReferences(selected,e.detail?.id||'');});
  $('reference-form').onsubmit=async e=>{e.preventDefault();if(!selected||referenceBusy)return;const file=$('reference-file').files[0];if(!file)return;
    if(file.size>20*1024*1024){referenceMessage('20 MB 이하 파일을 선택하세요.',true);return;}
    const sha=selected;referenceBusy=true;referenceControls();referenceMessage('참고자료 읽는 중');
    try{const form=new FormData();form.append('file',file);form.append('kind',$('reference-kind').value);form.append('source_url',$('reference-source').value);
      const data=await api('builds/'+sha+'/references',{method:'POST',body:form});
      if(sha===selected){await loadReferences(sha,data.reference.id);$('reference-file').value='';referenceMessage(data.duplicate?'동일한 참고자료가 이미 등록되어 있습니다.':'참고자료 등록 완료 · AI 미사용');}
    }catch(e){if(sha===selected)referenceMessage(e.message,true);}finally{referenceBusy=false;referenceControls();}
  };
  $('reference-delete').onclick=async()=>{if(!reference||referenceBusy||!confirm('선택한 참고자료를 삭제할까요? 원본 파일은 변경되지 않습니다.'))return;
    const sha=selected,id=reference.id;referenceBusy=true;referenceControls();
    try{await api('builds/'+sha+'/references/'+id,{method:'DELETE'});if(sha===selected){clearReference();await loadReferences(sha);referenceMessage('참고자료 삭제 완료');}}
    catch(e){if(sha===selected)referenceMessage(e.message,true);}finally{referenceBusy=false;referenceControls();}
  };
  window.addEventListener('pagehide',()=>{clearTimeout(timer);++requestId;});
  refresh().catch(e=>message(e.message,true));
})();
