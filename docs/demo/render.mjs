import { chromium } from 'playwright';
import fs from 'fs';
const b = await chromium.launch({ executablePath:'/opt/pw-browsers/chromium' });
const p = await b.newPage({ viewport:{width:1920,height:1080}, deviceScaleFactor:1 });
await p.goto('file:///tmp/vid/film.html', { waitUntil:'domcontentloaded' });
await p.waitForTimeout(1500);
const plan = await p.evaluate(()=>[...document.querySelectorAll('.scene')]
  .map((s,i)=>({i, t:+s.dataset.t, d:+s.dataset.d})));
fs.mkdirSync('/tmp/vid/frames',{recursive:true});
for (const s of plan) {
  await p.evaluate(t=>window.__paint(t), s.t + 0.5);
  await p.waitForTimeout(650);
  await p.screenshot({ path:`/tmp/vid/frames/scene-${String(s.i).padStart(2,'0')}.png` });
}
fs.writeFileSync('/tmp/vid/plan.json', JSON.stringify(plan,null,2));
console.log('rendered', plan.length, 'scenes; total', Math.max(...plan.map(s=>s.t+s.d)), 's');
await b.close();
