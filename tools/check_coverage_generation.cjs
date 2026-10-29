// Explicit one-requirement smoke run. Invokes the configured AI; no device input.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const fs=require('fs');
const sha='dbe5419619b5df89aa73437c0cb8215a80b66af4dfe214b119867c6894cfbb8a';
const statePath='qa_results/coverage-smoke-state.json';
(async()=>{
 const browser=await chromium.launch();
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://localhost:5000/app-analysis');
  const call=async(path,body)=>page.evaluate(async({path,body})=>{
   const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   const data=await r.json();if(!r.ok)throw Error(JSON.stringify(data));return data;
  },{path,body});
  const base='/api/app-analysis/builds/'+sha;
  let id;
  if(process.argv[2]==='start'){
   const reference=await page.evaluate(async(base)=>{
    const form=new FormData();form.append('file',new Blob(['합성 검증 자료: 설정 화면에서 그랭 서비스 이용약관을 누르면 이용약관 제목과 본문이 표시된다. 이 단일 기능에 대해 TC 한 개만 생성한다. 계정·구매·삭제·기기설정 변경은 범위 밖이다.']), 'coverage-smoke-requirement.txt');form.append('kind','requirements');
    const r=await fetch(base+'/references',{method:'POST',body:form});const j=await r.json();if(!r.ok)throw Error(JSON.stringify(j));return j.reference;
   },base);
   const opts={reference_ids:[reference.id],count:0,include_images:false,scope:'통합 점검용 합성 자료의 단일 이용약관 진입 기능만 검증 범위 1개와 TC 1개로 생성. 다른 기능이나 변형 시나리오는 이번 범위에서 제외.'};
   const preview=await call(base+'/draft-context',opts);
   const data=await call(base+'/drafts',{...opts,context_hash:preview.context_hash,consent:true});id=data.draft.id;
   fs.writeFileSync(statePath,JSON.stringify({id,sha}));
  }else{id=JSON.parse(fs.readFileSync(statePath)).id;}
  const {draft:d}=await call(base+'/drafts/'+id);
  console.log(JSON.stringify({id,status:d.status,progress:d.progress,error:d.error,cases:d.cases?.map(c=>({id:c.id,title:c.title}))}));
  if(d.status==='completed'){
   const bytes=await page.evaluate(async(url)=>{const r=await fetch(url);if(!r.ok)throw Error('export');return [...new Uint8Array(await r.arrayBuffer())];},base+'/drafts/'+id+'/export');
   fs.writeFileSync('qa_results/coverage-smoke-generated.xlsx',Buffer.from(bytes));
  }
  await page.locator('.build[data-sha="'+sha+'"]').click();
  await page.waitForFunction(()=>document.querySelector('#draft-history')?.options.length>0);
  await page.locator('#draft-history').selectOption(id);
  await page.waitForTimeout(1200);
  await page.locator('#draft-message').scrollIntoViewIfNeeded();
  console.log(JSON.stringify({uiStatus:await page.locator('#draft-message').innerText(),
    timer:await page.locator('#draft-clock').innerText(),errors}));
  await page.screenshot({path:'qa_results/coverage-smoke-ui.png'});
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
