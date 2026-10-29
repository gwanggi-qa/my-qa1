// Explicit live smoke check. Run only on the matching TOEIC card screen.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const fs=require('fs');
const mode=process.argv[2];
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  await page.goto('http://localhost:5000/');
  await page.waitForTimeout(500);
  const call=async(path,body)=>page.evaluate(async({path,body})=>{
   const r=await fetch('/api/tc-workflow/'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   const data=await r.json();if(!r.ok)throw Error(JSON.stringify(data));return data;
  },{path,body});
  if(mode==='upload'){
   const data=fs.readFileSync('qa_results/library-smoke.xlsx').toString('base64');
   console.log(await page.evaluate(async(data)=>{
    const f=new FormData();f.append('file',new Blob([Uint8Array.from(atob(data),c=>c.charCodeAt(0))]),'library-smoke.xlsx');
    const r=await fetch('/api/testcase',{method:'POST',body:f});const j=await r.json();return {status:r.status,metadata:j.metadata,count:j.steps?.length,error:j.error};
   },data));
  } else if(mode==='prepare'){
   const data=await page.evaluate(async()=>fetch('/api/testcase').then(r=>r.json()));
   console.log(await call('prepare',{upload_id:data.metadata.upload_id,file_hash:data.metadata.file_hash,indices:[1,2],ai_mode:true,consent:true}));
  } else if(mode==='save'){
   const {workflow:w}=await call('current');console.log(await call(w.id+'/save',{approved:true,plan_digest:w.plan_digest,name:'TOEIC card flip smoke'}));
  } else if(mode==='load'){
   const {versions}=await call('library');console.log(await call('library/'+versions[0].version+'/load',{consent:true}));
  } else if(mode==='start'){
   const {workflow:w}=await call('current');console.log(await call(w.id+'/start',{approved:true,plan_digest:w.plan_digest}));
  } else if(mode==='export'){
   const {workflow:w}=await call('current');
   const bytes=await page.evaluate(async(id)=>{const r=await fetch('/api/tc-workflow/'+id+'/export');if(!r.ok)throw Error('Export failed');return Array.from(new Uint8Array(await r.arrayBuffer()));},w.id);
   const path='qa_results/library-results-'+w.results.run_id+'.xlsx';fs.writeFileSync(path,Buffer.from(bytes));console.log(path);
  } else {
   const {workflow:w}=await call('current');console.log(JSON.stringify({id:w?.id,state:w?.state,message:w?.message,ai:w?.ai,counts:w?.results?.counts,results:w?.results?.results.map(c=>({id:c.source.tc_id,status:c.status,reason:c.reason,inputs:c.input_count,note:c.note}))}));
   await page.screenshot({path:'qa_results/library-ui.png'});
  }
 } finally {await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
