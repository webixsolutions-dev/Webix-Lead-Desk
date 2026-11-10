// Offline fixtures only: no production cookies, credentials, provider calls or live sends.
import { createServer } from 'node:http';
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises';
import { resolve, extname } from 'node:path';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import AxeBuilder from '@axe-core/playwright';

const root = resolve('../screenshots/ui-fixtures');
const output = resolve('../screenshots/ui-checks');
await mkdir(output, {recursive:true});
const routes = JSON.parse(await readFile(resolve(root,'routes.json'),'utf8'));
const submissions=[];
const types = {'.css':'text/css','.js':'text/javascript','.png':'image/png','.svg':'image/svg+xml','.html':'text/html'};
const server = createServer(async (req,res) => {
  const url=new URL(req.url,'http://localhost');
  let alias = url.pathname==='/' ? 'today-normal.html' : url.pathname===new URL(routes.editor,'http://localhost').pathname ? 'editor-normal.html' : null;
  if(req.method==='POST' && url.pathname===routes.approval) {
    let body='';for await(const chunk of req) body+=chunk;
    const data=new URLSearchParams(body);
    if(data.get('reviewed')!=='true'||!data.get('csrf_token')||!data.get('digest')) {res.writeHead(422,{'Content-Type':'application/json'});res.end(JSON.stringify({detail:'Exact review required'}));return;}
    submissions.push(Object.fromEntries(data));
    alias='editor-normal.html';
  } else if(req.method!=='GET') {res.writeHead(404);res.end();return;}
  const path=alias ? resolve(root,alias) : resolve(root,'.'+url.pathname);
  if(!path.startsWith(root+'/')) {res.writeHead(404);res.end();return;}

  try { const body=await readFile(path);res.writeHead(200,{'Content-Type':types[extname(path)]||'application/octet-stream'});res.end(body); }
  catch { res.writeHead(404);res.end(); }
});
await new Promise(done => server.listen(0, '127.0.0.1', done));
const base = `http://127.0.0.1:${server.address().port}`;
let browser;
const failures=[];
try {
  browser = await chromium.launch();
  for (const viewport of [{width:1366,height:900},{width:390,height:844}]) {
    const context = await browser.newContext({viewport});
    await context.route('**/*', route => route.request().url().startsWith(base+'/') ? route.continue() : route.abort());
    const page=await context.newPage();
    for (const file of (await readdir(root)).filter(f=>f.endsWith('.html'))) {
      await page.goto(`${base}/${file}`);
      await page.evaluate(()=>document.querySelectorAll('details').forEach(d=>d.open=true));
      await page.screenshot({path:`${output}/${viewport.width}-${file}.png`,fullPage:true});
      if (file==='meeting-pack-normal.html' && viewport.width===1366) {
        await page.pdf({path:`${output}/meeting-pack.pdf`,format:'Letter',printBackground:true,margin:{top:'0.5in',right:'0.5in',bottom:'0.5in',left:'0.5in'}});
      }
      const result=await new AxeBuilder({page}).withRules(['color-contrast','color-contrast-enhanced']).analyze();
      // Enhanced contrast applies to ordinary body copy; all text must meet AA.
      const violations=result.violations.filter(v=>v.id==='color-contrast');
      const small=await page.locator('body *').evaluateAll(elements=>elements.filter(el=>{
        const style=getComputedStyle(el);
        return el.getClientRects().length && [...el.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()) && parseFloat(style.fontSize)<12;
      }).map(el=>({tag:el.tagName,text:el.textContent.slice(0,70),size:getComputedStyle(el).fontSize})));
      const bodyContrast=await page.evaluate(()=>{
        const luminance=color=>{
          const values=color.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
          return values.reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
        };
        const style=getComputedStyle(document.body);
        const a=luminance(style.color),b=luminance(style.backgroundColor);
        return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
      });
      const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
      const statusBounds=await page.locator('.workspace-chip>div').first().boundingBox();
      if(statusBounds) assert(statusBounds.x>=0 && statusBounds.x+statusBounds.width<=viewport.width+1, `Workspace status must fit ${file}`);
      if(violations.length||small.length||overflow||bodyContrast<7) failures.push({file,width:viewport.width,violations,small,overflow,bodyContrast});
    }
    await page.goto(`${base}/today-normal.html`);
    const first=page.locator('main [data-review-item]').first();
    const bounds=await first.boundingBox();
    if(viewport.width===1366) assert(bounds && bounds.y+bounds.height<=900,'First action must be above the fold');
    const before=submissions.length;
    await page.locator('#start-day').click();
    await page.locator('#review-panel form[action$="/approve"]').waitFor();
    await page.keyboard.press('a');
    assert.equal(submissions.length,before,'Shortcut cannot bypass the exact-review checkbox');
    const checkbox=page.locator('#review-panel [name="reviewed"]');
    await checkbox.check();
    await page.locator('#review-panel-title').focus();
    await page.keyboard.press('a');
    await page.getByText('Saved. Review the updated record below.',{exact:true}).waitFor();
    assert.equal(submissions.length,before+1);
    assert.equal(submissions.at(-1).reviewed,'true');
    await page.locator('#review-panel [name="body"]').fill('Typed a reply with the letter a.');
    await page.keyboard.press('a');
    assert.equal(submissions.length,before+1,'Typing inside an editor must not approve');
    await page.locator('#review-panel-title').focus();
    await page.keyboard.press('s');
    assert.equal(await page.locator('#review-panel form[action$="/reject"] [name="reason"]').evaluate(n=>document.activeElement===n),true);
    await page.screenshot({path:`${output}/${viewport.width}-review-panel.png`,fullPage:true});
    const panelAxe=await new AxeBuilder({page}).withRules(['color-contrast']).analyze();
    assert.equal(panelAxe.violations.length,0,JSON.stringify(panelAxe.violations));
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#review-panel').evaluate(n=>n.open),false);
    await context.close();
  }
  await writeFile(`${output}/report.json`,JSON.stringify(failures,null,2));
  assert.equal(failures.length,0,JSON.stringify(failures.map(f=>({file:f.file,width:f.width,small:f.small,overflow:f.overflow,bodyContrast:f.bodyContrast,contrast:f.violations.flatMap(v=>v.nodes.map(n=>({target:n.target,summary:n.failureSummary})))})),null,2));
} finally { await browser?.close();server.close(); }
