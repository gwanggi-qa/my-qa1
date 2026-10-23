// Synthetic responses only, against the disposable 5015 fixture server.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const started=Date.now()-155000;
  let status='running',delayed=false,stopping=false;
  const draft=()=>({id:'timer-fixture',created_at:new Date(started).toISOString(),
    finished_at:status==='running'?null:new Date(started+170000).toISOString(),status,
    elapsed_seconds:status==='running'?(Date.now()-started)/1000:170,timeout_seconds:600,
    stopping,error_code:status==='failed'?'timeout':null,
    error:status==='failed'?'시간 초과: 생성 개수나 참고자료 범위를 줄인 뒤 다시 시도하세요.':null,
    metrics:{planner_calls:1,usage:{}},context:{evidence:[]},original:null,cases:null});
  await page.route('**/api/app-analysis/builds/*/drafts**',async route=>{
    const url=new URL(route.request().url());
    if(url.pathname.endsWith('/stop')){stopping=true;return route.fulfill({json:{status:'stopping'}});}
    if(url.pathname.endsWith('/drafts'))return route.fulfill({json:{drafts:[draft()]}});
    if(delayed)return route.fulfill({status:503,json:{error:'synthetic disconnected'}});
    return route.fulfill({json:{draft:draft()}});
  });
  try{
    await page.goto('http://localhost:5015/app-analysis',{waitUntil:'networkidle'});
    await page.waitForFunction(()=>document.querySelector('#draft-clock').textContent.includes('제한 10:00'));
    const before=await page.locator('#draft-clock').innerText();
    await page.waitForTimeout(1200);
    assert.notEqual(await page.locator('#draft-clock').innerText(),before);
    await page.reload({waitUntil:'networkidle'});
    await page.waitForFunction(()=>document.querySelector('#draft-clock').textContent.includes('경과 02:'));
    delayed=true;
    await page.waitForFunction(()=>document.querySelector('#draft-message').textContent.includes('상태 확인 지연'));
    const delayTime=await page.locator('#draft-clock').innerText();
    await page.waitForTimeout(1200);
    assert.notEqual(await page.locator('#draft-clock').innerText(),delayTime);
    delayed=false;
    await page.waitForFunction(()=>!document.querySelector('#draft-message').textContent.includes('상태 확인 지연'),null,{timeout:10000});
    await page.locator('#draft-stop').click();
    await page.waitForFunction(()=>document.querySelector('#draft-message').textContent.includes('중단 처리 중'));
    assert.ok(await page.locator('#draft-stop').isDisabled());
    status='stopped';
    await page.waitForFunction(()=>document.querySelector('#draft-clock').textContent==='총 소요 02:50');
    await page.waitForTimeout(1200);
    assert.equal(await page.locator('#draft-clock').innerText(),'총 소요 02:50');
    assert.match(await page.locator('#draft-message').innerText(),/사용량 확인 불가/);
    status='failed';stopping=false;
    await page.reload({waitUntil:'networkidle'});
    await page.waitForFunction(()=>document.querySelector('#draft-message').textContent.includes('[시간 초과]'));
    await page.locator('#draft-message').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.resolve('qa_results/draft-ui/timer-desktop.png')});
    await page.setViewportSize({width:390,height:844});
    await page.locator('#draft-message').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.resolve('qa_results/draft-ui/timer-mobile.png')});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent('qa-build-selected',{detail:{sha:''}})));
    assert.ok(await page.locator('#draft-clock').isHidden());
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({timer:true,reload:true,delayed:true,stop:true,error:true,mobile:true,errors}));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
