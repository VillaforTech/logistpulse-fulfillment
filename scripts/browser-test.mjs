import {chromium} from 'playwright';
import fs from 'node:fs';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {inspectLiveFrame} from './grafana-live-protocol.mjs';
const exec=promisify(execFile),base=process.env.BASE_URL||'http://localhost:28080';
const env=Object.fromEntries(fs.readFileSync('.env','utf8').split('\n').filter(x=>x&&!x.startsWith('#')).map(x=>{const i=x.indexOf('=');return [x.slice(0,i),x.slice(i+1)]}));
const out='artifacts/streaming';
// Preserve each previous run instead of mixing an old failure image into a new result.
if(fs.existsSync(out+'/measurements.json'))fs.renameSync(out,out+'-previous-'+Date.now());
fs.mkdirSync(out,{recursive:true});
const result={fixtureRunId:`render-${crypto.randomUUID()}`,requested:100,samples:[],errors:[],transport:{url:null,frames:0,channels:[],messageShapes:[],invalidLines:0},visualChecks:{},measurement:'browser performance.now API invocation -> all three Grafana KPI cards with matching identity/revision/FRESH/expected values, revalidated after two animation frames',thresholdP95Ms:1000};
result.testedSha=(await exec('git',['rev-parse','HEAD'])).stdout.trim();
result.environment={platform:process.platform,arch:process.arch,node:process.version};
result.startedAt=new Date().toISOString();
try{result.environment.docker=JSON.parse((await exec('docker',['info','--format','{"cpus":{{.NCPU}},"memoryBytes":{{.MemTotal}}}'])).stdout)}catch{}
const browser=await chromium.launch({headless:true});
result.environment.browser=browser.version();
const context=await browser.newContext({locale:'en-US',viewport:{width:1600,height:1100}}),page=await context.newPage();
result.environment.locale='en-US';
result.pageErrors=[];result.httpFailures=[];result.consoleErrors=[];result.requestFailures=[];
page.on('pageerror',e=>result.pageErrors.push(String(e)));
page.on('console',e=>{if(e.type()==='error')result.consoleErrors.push(e.text())});
page.on('requestfailed',r=>result.requestFailures.push({url:r.url(),error:r.failure()?.errorText}));
page.on('response',r=>{if(r.status()>=400)result.httpFailures.push({url:r.url(),status:r.status()})});
const stopped=new Set();
async function compose(...args){await exec('bash',['scripts/compose.sh',...args],{timeout:90000,maxBuffer:1024*1024})}
async function stop(service){stopped.add(service);await compose('stop',service)}
async function start(service){await compose('start',service);stopped.delete(service)}
async function waitReady(id,timeout=30000){
 const deadline=Date.now()+timeout;
 while(Date.now()<deadline){
  const order=await page.evaluate(async id=>{const r=await fetch('/api/fulfillment/orders/'+id);if(!r.ok)throw Error(r.status);return r.json()},id);
  if(order.status==='READY')return order;
  await new Promise(resolve=>setTimeout(resolve,200));
 }
 throw new Error('Order did not become READY within bounded wait: '+id);
}
function save(){fs.writeFileSync(out+'/measurements.json',JSON.stringify(result,null,2))}
page.on('websocket',ws=>{
 if(ws.url().includes('/api/live/ws')){
  result.transport.url=ws.url();
  ws.on('framesent',e=>{
   const observed=inspectLiveFrame(e.payload);
   result.transport.channels.push(...observed.subscriptions);
   result.transport.invalidLines+=observed.invalidLines;
   for(const shape of observed.shapes)if(!result.transport.messageShapes.includes(shape))result.transport.messageShapes.push(shape);
  });
  ws.on('framereceived',()=>result.transport.frames++);
 }
});
// Instrumentation reads actual DOM and computes a separate business oracle; it does not write panel values.
await context.addInitScript(()=>{
 window.readLogistCards=()=>[...document.querySelectorAll('[data-logistpulse-panel]')].map(x=>({...x.dataset,text:x.innerText,display:x.querySelector('[data-logistpulse-value]')?.innerText}));
 window.logistEpoch=value=>typeof value==='number'?value:Date.parse(value)/1000+(Number(value.match(/\.(\d{6})Z$/)?.[1]||0)%1000)/1000000;
 window.expectedLogist=(orders,computedAt)=>{
  const now=window.logistEpoch(computedAt);
  const due=orders.filter(o=>window.logistEpoch(o.createdAt)>now-900&&window.logistEpoch(o.createdAt)<=now-15);
  const breached=due.filter(o=>o.readyAt==null||window.logistEpoch(o.readyAt)>window.logistEpoch(o.createdAt)+15);
  const overdue=orders.filter(o=>o.status!=='READY'&&window.logistEpoch(o.createdAt)+15<=now);
  return {lk1:due.length?100*breached.length/due.length:-2,lk2:overdue.reduce((v,o)=>v+Math.round(Number(o.totalExact??o.total)*100),0)/100,lk3:overdue.reduce((v,o)=>v+Math.max(0,now-window.logistEpoch(o.createdAt)-15),0),sample:due.length};
 };
 window.matchLogist=(cards,orders,identity)=>{
  if(cards.length!==3||cards.some(x=>x.quality!=='FRESH'))return null;
  if([...new Set(cards.map(x=>x.logistpulsePanel))].sort().join(',')!=='lk1,lk2,lk3')return null;
  const first=cards[0];
  if(!first.eventId||!Number.isInteger(Number(first.revision))||Number(first.revision)<1)return null;
  if(cards.some(x=>x.revision!==first.revision||x.eventId!==first.eventId||x.computedAt!==first.computedAt))return null;
  if(identity&&cards.some(x=>x.correlationId!==identity.correlation||x.aggregateId!==identity.orderId||Number(x.aggregateVersion)<identity.version))return null;
  const expected=window.expectedLogist(orders,first.computedAt);
  if(cards.some(x=>!Number.isFinite(Number(x.value))||!Number.isFinite(expected[x.logistpulsePanel])))return null;
  if(cards.some(x=>Math.abs(Number(x.value)-expected[x.logistpulsePanel])>.01||Number(x.sample)!==expected.sample))return null;
  if(cards.some(x=>x.display!==(Number(x.value)===-2?'SIN MUESTRA':Number(x.value).toLocaleString('es-EC',{maximumFractionDigits:2}))))return null;
  return {cards,expected};
 };
});
try{
 await page.goto(base+'/grafana/login');
 result.environment.navigatorLanguage=await page.evaluate(()=>navigator.language);
 await page.locator('input[name="user"]').waitFor({state:'visible',timeout:30000});
 if(await page.locator('input[name="user"]').count()){
  await page.locator('input[name="user"]').fill(env.GF_SECURITY_ADMIN_USER||'admin');
  await page.locator('input[name="password"]').fill(env.GF_SECURITY_ADMIN_PASSWORD);
  await page.getByRole('button',{name:/Log in|Sign in/i}).click();
  await page.waitForURL(url=>!url.pathname.endsWith('/login'),{timeout:20000});
 }
 await page.goto(base+'/grafana/d/logistpulse-business/logistpulse-business?kiosk');
 await page.waitForFunction(()=>window.readLogistCards().length===3&&window.readLogistCards().every(c=>c.quality==='FRESH'),null,{timeout:30000});
 let known=await page.evaluate(async()=>{const r=await fetch('/api/fulfillment/snapshot');if(!r.ok)throw Error(r.status);return(await r.json()).orders});
 result.baselineAggregateCount=known.length;
 for(let i=0;i<100;i++){
  const correlation=`${result.fixtureRunId}-${i}`;
  const sample=await page.evaluate(async({correlation,fixture,index,known})=>{
   const start=performance.now();let created;
   try{
    const response=await fetch('/api/fulfillment/orders',{method:'POST',headers:{'Content-Type':'application/json','X-Correlation-ID':correlation},body:JSON.stringify({total:'25.50',channel:'LIVE-BENCHMARK',fixtureRunId:fixture})});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    created=await response.json();const received=performance.now();
    const orders=[...known,created],identity={correlation,orderId:created.orderId,version:created.aggregateVersion};
    const rendered=await new Promise((resolve,reject)=>{
     const expiry=performance.now()+5000;
     function inspect(){
      const match=window.matchLogist(window.readLogistCards(),orders,identity);
      if(match){
       requestAnimationFrame(()=>requestAnimationFrame(()=>{
        const again=window.matchLogist(window.readLogistCards(),orders,identity);
        if(again&&again.cards[0].revision===match.cards[0].revision)resolve({at:performance.now(),...again});else inspect();
       }));return;
      }
      if(performance.now()>expiry){reject(new Error('No coherent FRESH KPI render with expected values within 5s'));return;}
      requestAnimationFrame(inspect);
     }inspect();
    });
    return {index,correlation,orderId:created.orderId,eventId:created.eventId,aggregateVersion:created.aggregateVersion,apiMs:received-start,latencyMs:rendered.at-start,rendered:true,cards:rendered.cards,expected:rendered.expected};
   }catch(error){return {index,correlation,orderId:created?.orderId,rendered:false,error:String(error)}}
  },{correlation,fixture:result.fixtureRunId,index:i,known});
  result.samples.push(sample);save();
  if(!sample.rendered)throw new Error(sample.error||'Expected render was lost');
  if(sample.orderId){
   known.push(await waitReady(sample.orderId));
  }
  if(i%10===0)console.log(`Coherent Grafana KPI renders measured: ${i+1}/100`);
 }
 const values=result.samples.filter(x=>x.rendered).map(x=>x.latencyMs).sort((a,b)=>a-b);
 result.observed=values.length;result.lost=100-values.length;
 const percentile=p=>values.length?values[Math.ceil(p*values.length)-1]:null;
 result.p50Ms=percentile(.5);result.p95Ms=percentile(.95);result.maxMs=values.at(-1)??null;
 await page.waitForFunction(orders=>window.matchLogist(window.readLogistCards(),orders),known,{timeout:10000});
 await page.screenshot({path:out+'/grafana-100-renders.png',fullPage:true});
 if(result.lost)throw new Error(`${result.lost}/100 expected renders lost; not excluded`);
 if(!result.transport.url||!result.transport.channels.length)throw new Error('No observed native Grafana Live channel subscription');
 if(result.p95Ms>=1000)throw new Error(`p95 ${result.p95Ms.toFixed(1)}ms fails <1000ms`);
 // Separate causal check: a new order crosses its real 15-second deadline with no further facts.
 await stop('fulfillment-worker');
 const due=await page.evaluate(async fixture=>{const r=await fetch('/api/fulfillment/orders',{method:'POST',headers:{'Content-Type':'application/json','X-Correlation-ID':fixture},body:JSON.stringify({total:'25.50',fixtureRunId:fixture,channel:'TIMER-VISUAL'})});if(!r.ok)throw Error(r.status);return r.json()},result.fixtureRunId+'-deadline');
 const source=await page.evaluate(async()=>(await(await fetch('/api/fulfillment/snapshot')).json()).orders);
 result.visualChecks.deadline=await page.evaluate(async({source,due})=>{
  const deadlineMs=window.logistEpoch(due.createdAt)*1000+15000;
  const identity={correlation:due.correlationId,orderId:due.orderId,version:due.version};
  const end=performance.now()+25000;
  while(performance.now()<end){
   const match=window.matchLogist(window.readLogistCards(),source,identity);
   if(match&&window.logistEpoch(match.cards[0].computedAt)*1000>=deadlineMs&&match.expected.lk2>=25.5&&match.expected.lk3>0){
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const again=window.matchLogist(window.readLogistCards(),source,identity);
    if(again&&again.cards[0].revision===match.cards[0].revision){
     const renderEpochMs=performance.timeOrigin+performance.now();
     const snapshotDelayMs=window.logistEpoch(again.cards[0].computedAt)*1000-deadlineMs;
     const renderDelayMs=renderEpochMs-deadlineMs;
     return {passed:snapshotDelayMs>=0&&snapshotDelayMs<=1000&&renderDelayMs>=0&&renderDelayMs<=1000,
      ...again,order:due,deadlineUtc:new Date(deadlineMs).toISOString(),deadlineEpochMs:deadlineMs,
      firstObservedComputedAt:again.cards[0].computedAt,renderedAtUtc:new Date(renderEpochMs).toISOString(),renderEpochMs,
      snapshotDelayMs,renderDelayMs,thresholdMs:1000,clock:'Browser performance.timeOrigin + performance.now against source UTC; services and browser share one host clock'};
    }
   }
   await new Promise(resolve=>requestAnimationFrame(resolve));
  }
  return {passed:false,order:due,deadlineEpochMs:deadlineMs,thresholdMs:1000,error:'No coherent breached KPI render within 25-second diagnostic wait'};
 },{source,due});
 save();
 await page.screenshot({path:out+'/grafana-real-deadline.png',fullPage:true});
 if(!result.visualChecks.deadline.passed)throw new Error('Deadline KPI detection/render failed <=1000ms: '+JSON.stringify(result.visualChecks.deadline));
 await start('fulfillment-worker');
 await waitReady(due.orderId);
 // No page reload: a live client must first invalidate, then receive a newer coherent revision.
 async function invalidateAndRecover(name,disconnect,reconnect){
  const before=await page.evaluate(()=>window.readLogistCards());
  await disconnect();
  await page.waitForFunction(()=>window.readLogistCards().length===3&&window.readLogistCards().every(c=>c.quality==='STALE'),null,{timeout:12000});
  const stale=await page.evaluate(()=>window.readLogistCards());
  await page.screenshot({path:out+`/grafana-${name}-stale.png`,fullPage:true});
  await reconnect();
  const full=await page.evaluate(async()=>(await(await fetch('/api/fulfillment/snapshot')).json()).orders);
  await page.waitForFunction(({full,revision})=>{const m=window.matchLogist(window.readLogistCards(),full);return m&&Number(m.cards[0].revision)>revision},{full,revision:Number(before[0].revision)},{timeout:60000,polling:100});
  const recovered=await page.evaluate(()=>window.readLogistCards());
  result.visualChecks[name]={passed:true,before,stale,recovered,reload:false};save();
 }
 await invalidateAndRecover('browser',()=>context.setOffline(true),()=>context.setOffline(false));
 await invalidateAndRecover('adapter',()=>stop('grafana-live-adapter'),()=>start('grafana-live-adapter'));
 await invalidateAndRecover('grafana',()=>stop('grafana'),()=>start('grafana'));
 await page.screenshot({path:out+'/grafana-reconnected.png',fullPage:true});
 result.passed=true;
}catch(error){result.passed=false;result.errors.push(String(error));await page.screenshot({path:out+'/failure.png',fullPage:true}).catch(()=>{});process.exitCode=1}
finally{
 await context.setOffline(false).catch(()=>{});
 for(const service of stopped)await compose('start',service).catch(()=>{});
 result.completedAt=new Date().toISOString();result.observed=result.samples.filter(x=>x.rendered).length;result.lost=result.requested-result.observed;save();await browser.close();
 console.log(JSON.stringify({passed:result.passed,observed:result.observed,lost:result.lost,p50Ms:result.p50Ms,p95Ms:result.p95Ms,maxMs:result.maxMs,errors:result.errors}));
}
