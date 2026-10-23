// Local sample references only; does not start APK analysis or device input.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..','qa_results/reference-ui');
(async()=>{
  const browser=await chromium.launch({headless:true,channel:'msedge'});
  const context=await browser.newContext({viewport:{width:1440,height:1100}});
  const page=await context.newPage();
  const errors=[],created=[];
  let base='',token='';
  page.on('pageerror',e=>errors.push(e.message));
  try{
    await page.goto('http://localhost:5000/app-analysis',{waitUntil:'networkidle'});
    const sample=page.locator('.build').filter({hasText:'settings_apk-debug.apk'});
    await sample.click();
    const sha=await sample.getAttribute('data-sha');
    base='http://localhost:5000/api/app-analysis/builds/'+sha+'/references';
    await page.waitForFunction(()=>!document.querySelector('#reference-upload').disabled);
    token=await page.locator('meta[name="qa-csrf-token"]').getAttribute('content');
    async function upload(name,kind){
      await page.locator('#reference-kind').selectOption(kind);
      await page.locator('#reference-file').setInputFiles(path.join(root,'fixtures',name));
      const response=page.waitForResponse(r=>r.url()===base&&r.request().method()==='POST');
      await page.locator('#reference-upload').click();
      const r=await response,data=await r.json();
      assert.ok(r.ok(),JSON.stringify(data));
      if(r.status()===201)created.push(data.reference.id);
      await page.waitForFunction(()=>!document.querySelector('#reference-upload').disabled);
      await page.locator('#reference-name').getByText(name,{exact:true}).waitFor();
      return data;
    }
    await upload('requirements.md','requirements');
    assert.match(await page.locator('#reference-content').innerText(),/LC and RC/);
    assert.equal(await page.evaluate(()=>window.injected),undefined);
    const duplicate=await upload('requirements.md','requirements');
    assert.equal(duplicate.duplicate,true);
    await upload('cases.xlsx','testcase');
    assert.equal(await page.locator('#reference-unit option').count(),2);
    assert.match(await page.locator('#reference-content').innerText(),/QA-001/);
    await page.locator('#reference-unit').selectOption('1');
    assert.match(await page.locator('#reference-content').innerText(),/Version/);
    await upload('requirements.pdf','requirements');
    assert.match(await page.locator('#reference-content').innerText(),/LC and RC/);
    const image=await upload('design.png','design');
    await page.waitForFunction(()=>document.querySelector('#reference-content img')?.naturalWidth>0);
    const before=await page.locator('#reference-count').innerText();
    await page.reload({waitUntil:'networkidle'});
    await sample.click();
    await page.locator('#reference-list button[data-id="'+image.reference.id+'"]').click();
    await page.waitForFunction(()=>document.querySelector('#reference-content img')?.naturalWidth>0);
    assert.equal(await page.locator('#reference-count').innerText(),before);
    await page.screenshot({path:path.join(root,'desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(root,'mobile.png'),fullPage:true});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    assert.ok(await page.locator('#reference-list button').evaluateAll(buttons=>buttons.every(b=>b.scrollHeight<=b.clientHeight+1)),'Reference labels overlap their buttons');
    const legacy=await page.context().newPage();
    await legacy.goto('http://localhost:5000/',{waitUntil:'domcontentloaded'});
    await legacy.waitForFunction(()=>document.querySelector('#screen')?.naturalWidth>0,null,{timeout:30000});
    const mirroring=await legacy.locator('#screen').evaluate(img=>({width:img.naturalWidth,height:img.naturalHeight}));
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({text:true,xlsx:true,pdf:true,image:true,dedup:true,persistence:true,mobile:true,mirroring,errors}));
  }finally{
    for(const id of created){const r=await page.request.delete(base+'/'+id,{headers:{'X-QA-CSRF-Token':token,'Origin':'http://localhost:5000'},data:{}});assert.ok(r.ok(),'Fixture cleanup failed');}
    await browser.close();
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
