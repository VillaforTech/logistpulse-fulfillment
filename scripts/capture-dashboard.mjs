// Diagnostic screenshot only. It never changes a business result or starts a benchmark.
import {chromium} from 'playwright';
import fs from 'node:fs';
const env=Object.fromEntries(fs.readFileSync('.env','utf8').split('\n').filter(x=>x&&!x.startsWith('#')).map(x=>{const i=x.indexOf('=');return [x.slice(0,i),x.slice(i+1)]}));
const out='artifacts/runtime';fs.mkdirSync(out,{recursive:true});
const browser=await chromium.launch({headless:true}),page=await browser.newPage({locale:'en-US',viewport:{width:1600,height:1100}});
const errors=[];page.on('pageerror',e=>errors.push(String(e)));
const environment={locale:'en-US',browser:browser.version(),consoleErrors:[],requestFailures:[]};
page.on('console',e=>{if(e.type()==='error')environment.consoleErrors.push(e.text())});
page.on('requestfailed',r=>environment.requestFailures.push({url:r.url(),error:r.failure()?.errorText}));
try{
 await page.goto('http://localhost:28080/grafana/login');
 environment.navigatorLanguage=await page.evaluate(()=>navigator.language);
 await page.locator('input[name="user"]').fill(env.GF_SECURITY_ADMIN_USER||'admin');
 await page.locator('input[name="password"]').fill(env.GF_SECURITY_ADMIN_PASSWORD);
 await page.getByRole('button',{name:/Log in|Sign in/i}).click();
 await page.waitForURL(url=>!url.pathname.endsWith('/login'));
 await page.goto('http://localhost:28080/grafana/d/logistpulse-business/logistpulse-business?kiosk');
 await page.waitForFunction(()=>document.querySelectorAll('[data-logistpulse-panel]').length===3);
 fs.writeFileSync(out+'/grafana-panel-state.json',JSON.stringify(await page.locator('[data-logistpulse-panel]').evaluateAll(nodes=>nodes.map(x=>({...x.dataset,text:x.innerText}))),null,2));
 await page.screenshot({path:out+'/grafana-diagnostic.png',fullPage:true});
}catch(error){errors.push(String(error));await page.screenshot({path:out+'/grafana-diagnostic-error.png',fullPage:true}).catch(()=>{});process.exitCode=1}
finally{fs.writeFileSync(out+'/grafana-diagnostic-errors.json',JSON.stringify(errors,null,2));fs.writeFileSync(out+'/grafana-environment.json',JSON.stringify(environment,null,2));await browser.close()}
