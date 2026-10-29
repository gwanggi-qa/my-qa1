(() => {
'use strict';
const $=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
const list=v=>Array.isArray(v)?v:[],record=v=>v&&typeof v==='object'&&!Array.isArray(v)?v:{};
const text=v=>v===undefined||v===null||v===''?'미기록':typeof v==='object'?JSON.stringify(v,null,2):String(v);
const style=$('link');style.rel='stylesheet';style.href='/static/execution-dashboard.css';document.head.append(style);
const statusNames={passed:'Pass',failed:'Fail',blocked:'Block',not_run:'미실행',running:'실행 중',stopped:'중단',unverified:'검증 미확인'};
const methodNames={automated_device:'자동 기기 실행',manual_observation:'수동 관찰',software_test:'소프트웨어 테스트',legacy_unknown:'과거 출처 불명'};
const causeNames={product_mismatch:'제품 · 기대조건 불일치',mapping:'자동화 · 매핑 확인',environment:'환경 · 확인 필요',internal:'자동화 · 실행 오류',cancelled:'운영 · 중단',incomplete_verification:'검증 · 근거 부족',legacy_evidence:'과거 기록 · 근거 확인'};
const stateClass=s=>Object.hasOwn(statusNames,s)?s:'unknown';
const badge=s=>$('span',statusNames[s]||('알 수 없는 판정 · '+text(s)),'ex-badge ex-'+stateClass(s));
const method=m=>methodNames[m]||('출처 미확인 · '+text(m));
const panel=$('section');panel.id='execution-panel';panel.hidden=true;panel.setAttribute('aria-label','실행 결과 대시보드');panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby','execution-open');
const open=document.getElementById('execution-open'),testTab=document.getElementById('test-execution-tab'),testPanel=document.getElementById('test-execution-panel');document.body.append(panel);
const content=$('div',undefined,'ex-content');panel.append(content);
const top=$('div',undefined,'ex-top'),heading=$('div'),title=$('h2','실행 결과 대시보드'),close=$('button','테스트 실행으로'),refresh=$('button','새로고침'),stop=$('button','현재 실행 중단');
close.id='execution-close';refresh.id='execution-refresh';stop.id='execution-stop';stop.disabled=true;
heading.append($('p','TEST RESULTS','ex-eyebrow'),title,$('p','실행 이력과 원문, 판정 근거를 함께 확인합니다.','ex-subtitle'));
const topActions=$('div',undefined,'ex-top-actions');topActions.append(refresh,close);top.append(heading,topActions);content.append(top);
const error=$('div',undefined,'ex-error');error.id='execution-error';error.hidden=true;error.setAttribute('role','alert');content.append(error);
const context=$('section',undefined,'ex-card ex-context'),contextTop=$('div',undefined,'ex-context-top'),runInfo=$('div'),runTitle=$('h3','실행 이력 확인 중','ex-context-title'),runSource=$('span',undefined,'ex-source'),runId=$('p',undefined,'ex-run-id');
runInfo.append(runTitle,runSource,runId);
const history=$('label',undefined,'ex-history');history.append($('span','실행 이력','ex-meta-label'));
const runs=$('select',undefined,'ex-history-select');runs.id='execution-runs';runs.setAttribute('aria-label','실행 이력');history.append(runs);contextTop.append(runInfo,history);context.append(contextTop);
const status=$('div',undefined,'ex-run-state ex-state-ready');status.id='execution-status';status.setAttribute('role','status');
const current=$('p',undefined,'ex-note');current.id='execution-current';const stateRow=$('div',undefined,'ex-bar');stateRow.style.marginTop='16px';stateRow.append(status,stop);context.append(stateRow,current);
const meta=$('div',undefined,'ex-meta');context.append(meta);content.append(context);
const progress=$('div',undefined,'ex-progress'),progressTop=$('div',undefined,'ex-progress-heading'),progressLabel=$('strong','결과 등록'),progressCount=$('span');progressCount.id='execution-progress-count';progressTop.append(progressLabel,progressCount);
const meter=$('div',undefined,'ex-progress-meter');meter.id='execution-progress-meter';const progressNote=$('p',undefined,'ex-note');progress.append(progressTop,meter,progressNote);content.append(progress);
const stats=$('div',undefined,'ex-stats');stats.id='execution-stats';const statNumbers={},statButtons={};
for(const [key,icon] of [['passed','✓'],['failed','×'],['blocked','!'],['not_run','–']]){const b=$('button',undefined,'ex-stat ex-'+key),n=$('span','0','ex-stat-number');b.type='button';b.dataset.status=key;b.setAttribute('aria-pressed','false');b.append($('span',icon,'ex-stat-icon'),$('span',statusNames[key],'ex-stat-name'),n);b.onclick=()=>setFilter(key);stats.append(b);statNumbers[key]=n;statButtons[key]=b;}content.append(stats);
const warnings=$('div',undefined,'ex-warning');warnings.id='execution-warnings';warnings.hidden=true;content.append(warnings);
const layout=$('div',undefined,'ex-layout'),resultList=$('section',undefined,'ex-card ex-list');layout.id='execution-layout';content.append(layout);layout.append(resultList);
const listHeader=$('div',undefined,'ex-list-header'),listTitle=$('div',undefined,'ex-list-title'),count=$('span',undefined,'ex-count');count.id='execution-count';listTitle.append($('h3','테스트 케이스'),count);listHeader.append(listTitle);
const chips=$('div',undefined,'ex-filter-row');chips.id='execution-quick-filters';listHeader.append(chips);
const searchRow=$('div',undefined,'ex-search'),search=$('input'),reset=$('button','필터 초기화');search.type='search';search.id='execution-search';search.placeholder='TC ID · 제목 · 실제 결과 검색';search.setAttribute('aria-label','TC ID와 결과 검색');reset.id='execution-filter-reset';searchRow.append(search,reset);listHeader.append(searchRow);
const selectors=$('div',undefined,'ex-selects'),statuses=$('select'),methods=$('select');statuses.id='execution-filter';statuses.setAttribute('aria-label','판정 필터');methods.id='execution-method';methods.setAttribute('aria-label','출처 필터');
function option(select,value,label){const o=$('option',label);o.value=value;select.append(o);}
option(statuses,'','모든 판정');option(statuses,'attention','우선 확인');for(const [v,n] of Object.entries(statusNames))option(statuses,v,n);option(statuses,'unknown','알 수 없는 판정');
option(methods,'','모든 출처');for(const [v,n] of Object.entries(methodNames))option(methods,v,n);
selectors.append(statuses,methods);listHeader.append(selectors);resultList.append(listHeader);
const table=$('table');table.id='execution-table';const head=$('thead'),hr=$('tr');for(const label of ['TC ID / 제목','판정','실제 결과 · 확인할 사항']){const th=$('th',label);th.scope='col';hr.append(th);}head.append(hr);table.append(head);
const body=$('tbody');table.append(body);const tableWrap=$('div',undefined,'ex-table-wrap');tableWrap.id='execution-table-wrap';tableWrap.append(table);resultList.append(tableWrap,$('p','판정은 저장된 기록을 그대로 표시합니다. Block·중단·미확인은 Pass가 아닙니다.','ex-list-footer'));
const detail=$('section',undefined,'ex-card');detail.id='execution-detail';detail.hidden=true;detail.setAttribute('aria-label','TC 상세 근거');layout.append(detail);
const advanced=$('div',undefined,'ex-advanced'),mapOpen=$('button','기존 검토 매핑 실행 도구');mapOpen.id='execution-mapping-open';mapOpen.setAttribute('aria-expanded','false');advanced.append(mapOpen);content.append(advanced);
const mapping=$('section',undefined,'ex-card');mapping.id='execution-mapping';mapping.hidden=true;advanced.append(mapping);
mapping.append($('h3','원문에 고정된 검토 매핑'),$('p','기존 실행 도구입니다. 실기기에서 확인한 도움말·환경설정·제품정보 매핑만 지원하며, 실행은 아래 별도 버튼에서 시작합니다.','ex-note'));
const sources=$('button','업로드 원문·버전 확인'),sourceText=$('pre'),mapText=$('textarea'),confirm=$('input'),confirmLabel=$('label'),execute=$('button','검토 매핑으로 실행');mapText.id='execution-mapping-json';mapText.setAttribute('aria-label','검토된 매핑 JSON');mapText.placeholder='upload_id, file_hash, selection, mappings가 있는 JSON';confirm.type='checkbox';confirm.id='execution-mapping-confirm';confirmLabel.append(confirm,document.createTextNode(' 원문 전체와 실기기 매핑을 검토했습니다.'));execute.id='execution-mapping-start';mapping.append(sources,sourceText,mapText,confirmLabel,execute);
let data={current:null,history:[]},selected='',detailIndex=null,detailSignature='',requestSerial=0,controller=null,timer=null,loaded=false,loadError='';
const allRuns=()=>[...(data.current?[data.current]:[]),...list(data.history)].filter(r=>r&&typeof r.run_id==='string'&&Array.isArray(r.results));
const projectFilter=$('select');projectFilter.setAttribute('aria-label','프로젝트 필터');topActions.prepend(projectFilter);option(projectFilter,'','전체 프로젝트');
projectFilter.onchange=()=>{const choices=allRuns().filter(r=>!projectFilter.value||(r.project_id||'__unassigned')===projectFilter.value);selected=choices[0]?.run_id||'';runs.replaceChildren();for(const r of choices)option(runs,r.run_id,(r.project_id||'프로젝트 미지정')+' · '+formatTime(r.started_at)+' · '+r.run_id);runs.value=selected;closeDetail(false);render();};
const currentRun=()=>allRuns().find(r=>r.run_id===selected);
function showError(message){loadError=message;error.hidden=!message;error.textContent=message;}
function metrics(run){const c=Object.fromEntries([...Object.keys(statusNames),'unknown'].map(k=>[k,0]));for(const r of list(run?.results))c[stateClass(r.status)]++;return c;}
function formatTime(value){if(!value)return '시각 미기록';const d=new Date(value);return Number.isNaN(d.getTime())?text(value):d.toLocaleString('ko-KR',{hour12:false});}
function runState(run){if(!run)return {kind:loaded?'empty':'ready',label:loaded?'실행 기록 없음':'기록 확인 중'};
 if(run.persistence_error||run.error)return {kind:'error',label:'실행 오류 · 기록 확인 필요'};
 if(run.state==='running')return {kind:'running',label:run.current_tc?'실행 중':'실행 준비 중'};
 if(run.state==='stopped')return {kind:'stopped',label:'중단됨'};
 if(['error','failed'].includes(run.state))return {kind:'error',label:'실행 오류'};
 if(run.state==='finished'&&list(run.results).some(r=>r.error_kind==='internal'))return {kind:'error',label:'실행 오류 · 결과 확인'};
 if(['preparing','ready','queued'].includes(run.state))return {kind:'ready',label:'실행 준비 중'};
 if(run.state==='finished'){const c=metrics(run);if(c.stopped)return {kind:'stopped',label:'종료 · 중단 기록 있음'};if(!run.results.length)return {kind:'empty',label:'종료 · 결과 없음'};if(c.running)return {kind:'unknown',label:'종료 상태와 TC 기록 불일치'};if(c.passed===run.results.length)return {kind:'finished',label:'완료 · 전체 Pass'};if(c.failed||c.blocked)return {kind:'attention',label:'실행 종료 · 실패/차단 확인'};return {kind:'unknown',label:'실행 종료 · 검증 미완료'};}
 return {kind:'unknown',label:'실행 상태 미확인 · '+text(run.state)};}
function addMeta(label,value){const box=$('div');box.append($('span',label,'ex-meta-label'),$('span',text(value),'ex-meta-value'));meta.append(box);}
function filterMatches(r){const value=statuses.value,s=stateClass(r.status);return !value||value===s||value==='attention'&&['failed','blocked','stopped','unverified','unknown'].includes(s);}
function rowsFor(run){const query=search.value.trim().toLocaleLowerCase();return list(run?.results).map((r,index)=>({r,index})).filter(({r})=>(!methods.value||run.method===methods.value)&&filterMatches(r)&&(!query||[r.source?.tc_id,r.source?.title,r.actual,r.reason,r.source?.expected].map(v=>text(v)).join(' ').toLocaleLowerCase().includes(query)));}
function setFilter(value){statuses.value=value;closeDetail(false);render();}
function closeDetail(restore=true){const index=detailIndex;detailIndex=null;detailSignature='';detail.hidden=true;layout.classList.remove('has-detail');for(const b of body.querySelectorAll('.ex-case-link')){b.setAttribute('aria-expanded','false');b.closest('tr').classList.remove('ex-selected');if(restore&&Number(b.dataset.index)===index)b.focus({preventScroll:true});}}
function field(parent,label,value){const line=$('div',undefined,'ex-field');line.append($('span',label,'ex-field-label'),$('span',text(value)));parent.append(line);}
function rawDisclosure(parent,label,value){if(value===undefined||value===null)return;const box=$('details',undefined,'ex-raw');box.append($('summary',label),$('pre',text(value)));parent.append(box);}
function redmineLinks(parent,reviews){
 const linked=list(reviews).filter(r=>r.issue?.issue_id);if(!linked.length)return;
 parent.append($('div','Redmine 등록 이슈','ex-mini-heading'));
 const seen=new Set();for(const review of linked){const issue=review.issue,key=String(issue.issue_id);if(seen.has(key))continue;seen.add(key);
  let url;try{const parsed=new URL(issue.issue_url);if(['http:','https:'].includes(parsed.protocol)&&!parsed.username&&!parsed.password)url=parsed.href;}catch{}
  const entry=$(url?'a':'span','Redmine #'+key);if(url){entry.href=url;entry.target='_blank';entry.rel='noopener noreferrer';}parent.append(entry);
  if(review.latest_retest)field(parent,'최근 재시험',text(review.latest_retest.status)+' · '+formatTime(review.latest_retest.at));
 }
}
function evidenceUrl(e){if(typeof e.url!=='string')return null;const match=/^\/api\/results\/screenshot\/([A-Za-z0-9_.-]+\.png)$/.exec(e.url);return match&&!match[1].startsWith('.')?e.url:null;}
function addEvidence(parent,entries){const grid=$('div',undefined,'ex-evidence-list'),seen=new Set();for(const raw of list(entries)){const e=record(raw),url=evidenceUrl(e);if(!url||seen.has(url))continue;seen.add(url);const a=$('a',undefined,'ex-evidence');a.href=url;a.target='_blank';a.rel='noopener noreferrer';const img=$('img');img.src=url;img.loading='lazy';img.alt='저장된 관찰 근거 '+text(e.file);img.onerror=()=>{img.hidden=true;label.textContent='근거 파일을 불러올 수 없습니다. '+text(e.file);};const label=$('span',text(e.file));a.append(img,label);if(e.captured_at)a.append($('span',formatTime(e.captured_at)));grid.append(a);}if(!grid.children.length)parent.append($('p','저장된 이미지 근거가 없습니다.','ex-note'));else parent.append(grid);}
function conditions(parent,label,items,verified){if(!list(items).length)return;const group=$('div',undefined,'ex-conditions');group.append($('strong',label+(verified===true?' · 검증 완료':verified===false?' · 미확인':'')));for(const item of items){const c=record(item);group.append($('p',text(c.source||item)+(typeof c.present==='boolean'?' · '+(c.present?'표시 기대':'부재 기대'):'')));}parent.append(group);}
function showDetail(run,r,index,focus=true){detailIndex=index;detailSignature=JSON.stringify([run.run_id,r]);detail.replaceChildren();detail.hidden=false;layout.classList.add('has-detail');const bar=$('div',undefined,'ex-detail-header'),h=$('div'),b=$('button','×','ex-detail-close');b.id='execution-detail-close';b.setAttribute('aria-label','상세 닫기');b.onclick=()=>closeDetail(true);h.append($('p',text(r.source?.tc_id)+' · 상세 근거','ex-eyebrow'),$('h3',text(r.source?.title)));bar.append(h,b);detail.append(bar);
 const inner=$('div',undefined,'ex-detail-body'),badges=$('div',undefined,'ex-detail-status');badges.append(badge(r.status),$('span',method(run.method),'ex-source'));inner.append(badges);
 const observed=$('div',undefined,'ex-observation ex-'+stateClass(r.status));observed.append($('strong',r.status==='failed'?'기대와 다른 점':r.status==='blocked'?'검증하지 못한 이유':'실제 관찰 · 미수행 사유'),document.createTextNode(text(r.actual||r.reason)));inner.append(observed);
 if(r.reason&&r.actual)field(inner,'사유',r.reason);if(r.error_kind)field(inner,'원인 종류',causeNames[r.error_kind]||r.error_kind);
 if(r.review_error)field(inner,'검토 기록 오류',r.review_error);
 redmineLinks(inner,r.redmine_reviews);
 field(inner,'실제 입력 횟수',r.input_count??'미기록');field(inner,'검증 범위',list(r.stages).filter(s=>s.expectations_verified===true).length+' / '+list(r.stages).length+' 단계 기대결과 확인');
 const same=c=>JSON.stringify([c.source?.tc_id,c.source?.procedure,c.source?.expected,c.source?.precondition]);
 const prior=allRuns().filter(x=>x.run_id!==run.run_id&&new Date(x.started_at)<new Date(run.started_at)).sort((a,b)=>new Date(b.started_at)-new Date(a.started_at)).map(x=>({run:x,result:list(x.results).find(c=>c.source?.tc_id&&same(c)===same(r))})).find(x=>x.result);
 field(inner,'이전 동일 TC',prior?formatTime(prior.run.started_at)+' · '+text(prior.result.status)+' → '+text(r.status)+' · '+text(prior.result.actual||prior.result.reason):'동일 사전조건·절차·기대결과의 이전 기록 없음');
 inner.append($('div','원문 절차 · 예상 / 실제','ex-mini-heading'));field(inner,'사전조건',r.source?.precondition);field(inner,'절차',r.source?.procedure);field(inner,'기대결과',r.source?.expected);
 inner.append($('div','단계별 검증 기록','ex-mini-heading'));const stages=list(r.stages);
 if(!stages.length)inner.append($('p','저장된 단계별 기록이 없습니다. 절차나 검증 결과를 추정하지 않습니다.','ex-note'));
 for(let i=0;i<stages.length;i++){const s=record(stages[i]),step=$('section',undefined,'ex-step'),top=$('div',undefined,'ex-step-top');top.append($('span',(s.number??i+1)+'. '+text(s.source||s.action)),badge(s.status));step.append(top);conditions(step,'사전조건',s.preconditions,s.preconditions_verified);conditions(step,'기대조건',s.expected||s.checks,s.expectations_verified);field(step,'실제',s.actual||s.message||s.reason);if(s.error_kind)field(step,'원인',causeNames[s.error_kind]||s.error_kind);if(s.started_at||s.finished_at)field(step,'시각',formatTime(s.started_at)+' → '+formatTime(s.finished_at));if(list(s.evidence).length)addEvidence(step,s.evidence);rawDisclosure(step,'저장된 단계 원문',s);inner.append(step);}
 inner.append($('div','저장된 증거','ex-mini-heading'));addEvidence(inner,r.evidence);
 if(r.legacy_status)field(inner,'과거 판정',text(r.legacy_status)+' · 현재 검증을 뜻하지 않음');
 rawDisclosure(inner,'앱 · 기기 및 기록 정보',run.environment);rawDisclosure(inner,'원본 기록',r.legacy_record);
 const provenance=$('div',undefined,'ex-provenance');provenance.append($('p',text(r.source?.sheet)+' · '+text(r.source?.row)+'행 / 실행 '+run.run_id),$('p','원본 버전 '+text(r.source?.version)), $('p','시작 '+formatTime(r.started_at)+' / 종료 '+formatTime(r.finished_at)));inner.append(provenance);detail.append(inner);
 for(const link of body.querySelectorAll('.ex-case-link')){const selectedRow=Number(link.dataset.index)===index;link.setAttribute('aria-expanded',String(selectedRow));link.closest('tr').classList.toggle('ex-selected',selectedRow);}if(focus){b.focus({preventScroll:true});if(matchMedia('(max-width:900px)').matches)detail.scrollIntoView({block:'start'});}}
function render(){const run=currentRun(),c=metrics(run),total=list(run?.results).length,summary=runState(run),active=data.current?.state==='running';stop.disabled=!active;
 let exportButton=document.getElementById('execution-export');if(!exportButton){exportButton=$('button','회차 Excel');exportButton.id='execution-export';topActions.append(exportButton);}exportButton.disabled=!run||!['finished','stopped'].includes(run.state);exportButton.onclick=()=>{if(run)location.href='/api/execution/results/'+encodeURIComponent(run.run_id)+'/export';};
 runTitle.textContent=run?text(run.filename||run.results[0]?.source?.filename||'실행 결과'):'아직 저장된 실행이 없습니다';runSource.textContent=run?method(run.method):'실제 기록을 기다리고 있습니다';runId.textContent=run?'실행 ID '+run.run_id:'';
 status.textContent=summary.label;status.className='ex-run-state ex-state-'+summary.kind;
 const priorProject=projectFilter.value;projectFilter.replaceChildren();option(projectFilter,'','전체 프로젝트');for(const p of [...new Set(allRuns().map(r=>r.project_id||'__unassigned'))])option(projectFilter,p,p==='__unassigned'?'프로젝트 미지정':p);projectFilter.value=priorProject;
 status.title=run?'파일 '+text(run.filename)+' · 원본 버전 '+text(run.source_version)+' · 실행 대상 '+total+'개 · 실제 입력 '+text(run.input_count)+'회':'';
 current.textContent=run?.state==='running'?'현재 TC: '+(run.current_tc||'준비 중')+' · 단계 '+text(run.current_step):run?'시작 '+formatTime(run.started_at)+' · 종료 '+formatTime(run.finished_at):'테스트를 실행하면 원문과 함께 결과가 여기에 표시됩니다.';
 if(active&&data.current.run_id!==run?.run_id)current.textContent+=' · 별도 실행 진행 중: '+text(data.current.current_tc||'준비 중');
 meta.replaceChildren();addMeta('앱 · 기기',run?.environment?.device||run?.environment?.reason);addMeta('선택 범위',run?total+'건':'미기록');addMeta('원본 버전',run?.source_version?run.source_version.slice(0,16):null);addMeta('업로드',run?(run.upload_id&&data.upload_id&&run.upload_id===data.upload_id?'현재 업로드':'이전 업로드 또는 버전 미기록'):null);
 const registered=c.passed+c.failed+c.blocked;progressCount.textContent=registered+' / '+total+'건 · 판정 등록';meter.replaceChildren();for(const key of ['passed','failed','blocked','running','stopped','unverified','unknown']){if(!c[key])continue;const segment=$('span',undefined,'ex-progress-segment ex-'+key);segment.style.width=(total?c[key]/total*100:0)+'%';meter.append(segment);}meter.setAttribute('aria-label',Object.entries(c).map(([k,n])=>(statusNames[k]||'알 수 없는 판정')+' '+n+'건').join(', '));progressNote.textContent='판정 등록 = Pass + Fail + Block · 중단 '+c.stopped+' · 검증 미확인 '+c.unverified+' · 실행 중 '+c.running+' · 미실행 '+c.not_run+(c.unknown?' · 알 수 없는 판정 '+c.unknown:'')+'. Block은 수행 성공을 의미하지 않습니다.';
 for(const key of Object.keys(statNumbers)){statNumbers[key].replaceChildren(document.createTextNode(String(c[key])),$('small','건'));statButtons[key].setAttribute('aria-pressed',String(statuses.value===key));statButtons[key].setAttribute('aria-label',statusNames[key]+' '+c[key]+'건으로 필터');}
 const notes=[...list(data.warnings).map(text)];if(run?.method==='legacy_unknown')notes.push('과거 출처 불명 기록입니다. 이전 Pass를 현재 검증된 성공으로 사용하지 않습니다.');if(run?.persistence_error)notes.push('기록 저장 오류: '+text(run.persistence_error));warnings.textContent=notes.join('\n');warnings.hidden=!notes.length;
 chips.replaceChildren();for(const [value,label,n] of [['','전체',total],['attention','우선 확인',c.failed+c.blocked+c.stopped+c.unverified+c.unknown],['running','실행 중',c.running],['stopped','중단',c.stopped],['unverified','미확인',c.unverified]]){const b=$('button',label+' '+n,'ex-chip');b.dataset.filter=value;b.setAttribute('aria-pressed',String(statuses.value===value));b.onclick=()=>setFilter(value);chips.append(b);}
 body.replaceChildren();const rows=rowsFor(run);count.textContent=rows.length+'건 표시 / 전체 '+total+'건';for(const {r,index} of rows){const tr=$('tr'),id=$('td'),link=$('button',undefined,'ex-case-link');link.type='button';link.dataset.index=index;link.setAttribute('aria-controls','execution-detail');link.setAttribute('aria-expanded',String(detailIndex===index));link.append($('span',text(r.source?.tc_id||r.index),'ex-case-id'),$('span',text(r.source?.title),'ex-case-title'));link.onclick=()=>showDetail(run,r,index);id.append(link,$('p',text(r.source?.sheet)+' · '+text(r.source?.row)+'행','ex-case-source'));const state=$('td');state.append(badge(r.status));const actual=$('td',undefined,'ex-reason');actual.append(document.createTextNode(text(r.actual||r.reason)));if(r.error_kind)actual.append($('span',causeNames[r.error_kind]||r.error_kind,'ex-cause'));tr.append(id,state,actual);tr.classList.toggle('ex-selected',detailIndex===index);body.append(tr);}
 if(!rows.length){const tr=$('tr'),td=$('td',undefined,'ex-empty');td.colSpan=3;td.append($('strong',run?'조건에 맞는 TC가 없습니다':'실행 기록이 없습니다'),$('p',run?'검색어나 판정·출처 필터를 바꿔 확인하세요.':'저장된 실제 기록만 표시합니다. 예시 결과나 증거를 만들지 않습니다.'));tr.append(td);body.append(tr);}
 if(detailIndex!==null){const r=run?.results[detailIndex];if(!r)closeDetail(false);else if(detailSignature!==JSON.stringify([run.run_id,r]))showDetail(run,r,detailIndex,false);}}
async function load(){const serial=++requestSerial;if(controller)controller.abort();controller=new AbortController();try{const response=await fetch('/api/execution/results',{signal:controller.signal,cache:'no-store'});if(!response.ok)throw new Error('결과 조회 실패: '+response.status);const incoming=await response.json();if(serial!==requestSerial)return;if(!incoming||!Array.isArray(incoming.history))throw new Error('결과 데이터 형식을 확인할 수 없습니다.');data=incoming;loaded=true;runs.replaceChildren();for(const r of allRuns())option(runs,r.run_id,method(r.method)+' · '+formatTime(r.started_at)+' · '+r.run_id.slice(0,10));if(!allRuns().some(r=>r.run_id===selected)){selected=allRuns()[0]?.run_id||'';closeDetail(false);}if(!allRuns().length)option(runs,'','실행 기록 없음');runs.disabled=!allRuns().length;runs.value=selected;showError('');render();}catch(e){if(e.name!=='AbortError'&&serial===requestSerial){showError(e.message+(loaded?' · 마지막으로 확인한 기록을 표시합니다.':''));if(!loaded){status.textContent='결과 조회 오류';status.className='ex-run-state ex-state-error';}}}}
function activateView(dashboard){panel.hidden=!dashboard;testPanel.hidden=dashboard;open.setAttribute('aria-selected',String(dashboard));testTab.setAttribute('aria-selected',String(!dashboard));open.tabIndex=dashboard?0:-1;testTab.tabIndex=dashboard?-1:0;if(dashboard){load();if(!timer)timer=setInterval(()=>{if(!panel.hidden)load();},2000);}else{clearInterval(timer);timer=null;if(controller)controller.abort();requestSerial++;requestAnimationFrame(()=>window.dispatchEvent(new Event('resize')));}}
open.onclick=()=>activateView(true);testTab.onclick=close.onclick=()=>activateView(false);
for(const tab of [testTab,open])tab.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const destination=event.key==='Home'?testTab:event.key==='End'?open:tab===testTab?open:testTab;activateView(destination===open);destination.focus();});
detail.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();closeDetail(true);}});
refresh.onclick=load;runs.onchange=()=>{selected=runs.value;closeDetail(false);render();};statuses.onchange=methods.onchange=()=>{closeDetail(false);render();};search.oninput=()=>{closeDetail(false);render();};reset.onclick=()=>{search.value='';methods.value='';setFilter('');};
stop.onclick=async()=>{stop.disabled=true;try{const r=await fetch('/api/run/stop',{method:'POST'});if(!r.ok)throw new Error('중단 요청 실패');await load();}catch(e){showError(e.message);}};
mapOpen.onclick=()=>{mapping.hidden=!mapping.hidden;mapOpen.setAttribute('aria-expanded',String(!mapping.hidden));};
sources.onclick=async()=>{try{const r=await fetch('/api/execution/sources');if(!r.ok)throw new Error('원문 조회 실패');sourceText.textContent=JSON.stringify(await r.json(),null,2);}catch(e){showError(e.message);}};
execute.onclick=async()=>{if(!confirm.checked){showError('원문과 실기기 매핑 검토 확인이 필요합니다.');return;}execute.disabled=true;try{const payload=JSON.parse(mapText.value);const r=await window.startTestWithReview('/api/execution/run',payload);const v=await r.json();if(!r.ok)throw new Error(v.error||'실행 거부');selected=v.run_id;closeDetail(false);await load();}catch(e){showError(e.message);}finally{execute.disabled=false;}};
render();activateView(false);
const runtimeStatus=document.getElementById('view-runtime-status'),stopControls=document.getElementById('view-stop-controls');
let runtime=null;
const haltActions=[
 ['TC 중단','test_running','/api/run/stop','btn-test-stop'],
 ['Random 중단','random_running','/api/random/stop','btn-random-stop'],
 ['재생 중단','record_play_running','/api/recordings/play/stop','btn-record-play-stop'],
 ['녹화 중지',null,null,'btn-record-stop']
];
for(const [label,key,url,originalId] of haltActions){
 const button=$('button',label);button.type='button';button.id='view-'+originalId;button.disabled=true;
 button.onclick=async()=>{
   if(button.disabled)return;
   if(!url){document.getElementById(originalId)?.click();syncRuntime();return;}
   button.disabled=true;
   try{const response=await fetch(url,{method:'POST'});if(!response.ok)throw new Error(label+' 요청 실패');await refreshRuntime();if(!panel.hidden)load();}
   catch(e){runtimeStatus.textContent=e.message;button.disabled=false;}
 };
 stopControls.append(button);
}
function syncRuntime(){
 const recording=document.getElementById('btn-record-stop')?.disabled===false;
 const connection=document.getElementById('device-state')?.textContent||'확인 중';
 runtimeStatus.textContent='기기: '+connection+' · '+(runtime?
   'TC '+(runtime.test_running?'실행 중':'대기')+' · Random '+(runtime.random_running?'실행 중':'대기')+
   ' · 재생 '+(runtime.record_play_running?'실행 중':'대기')+' · 미러링 '+(runtime.streaming?'켜짐':'꺼짐'):
   '서버 상태 확인 중')+' · 녹화 '+(recording?'진행 중':'대기');
 for(const [,key,,originalId] of haltActions){
   const original=document.getElementById(originalId);
   const active=key&&runtime?runtime[key]:original?.disabled===false;
   document.getElementById('view-'+originalId).disabled=!active;
 }
}
async function refreshRuntime(){
 try{const response=await fetch('/api/runtime/status',{cache:'no-store'});if(!response.ok)throw new Error('상태 조회 실패');
   runtime=await response.json();syncRuntime();
 }catch(e){runtimeStatus.textContent='서버 상태 확인 불가 · 기존 화면 상태를 확인하세요';}
}
for(const id of ['device-state','btn-record-stop','btn-test-stop','btn-random-stop','btn-record-play-stop']){
 const element=document.getElementById(id);if(element)new MutationObserver(syncRuntime).observe(element,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['disabled']});
}
refreshRuntime();setInterval(refreshRuntime,2000);

})();
