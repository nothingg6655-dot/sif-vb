"""Export Sapphire's stored allocation and holdings to a self-contained report."""
import html
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'data/factsheets/latest/extraction/Sapphire Equity Long-Short Fund.json'
OUT=ROOT/'docs/reports'

def main():
    fund=json.loads(SOURCE.read_text(encoding='utf-8'))['Sapphire Equity Long-Short Fund']
    columns=['Instrument','Percent to Net Assets','Derivatives Percent to Net Assets','Quantity','Market Value (INR Lakhs)','Source Page']
    data={'Scheme':'Sapphire Equity Long-Short Fund','As Of':fund['Portfolio Date'],
        'Asset Allocation (%)':fund['Asset Allocation (%)'],'Sector Allocation (%)':fund['Sector Allocation (%)'],
        'Holdings':[{key:row.get(key) for key in columns} for row in fund['Portfolio']],
        'Source':{'JSON':str(SOURCE),'PDF':fund['Factsheet Source']['Local PDF'],'PDF Page':4,
                  'PDF SHA256':fund['Factsheet Source']['SHA256']}}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'sapphire-allocations-and-holdings.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    payload=json.dumps(data,ensure_ascii=False).replace('<','\\u003c')
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sapphire allocations and holdings | 30 September 2026</title>
<style>
:root{color-scheme:light dark;--bg:#fff;--text:#17222f;--muted:#5a6775;--line:#dce1e6;--accent:#28547c;--surface:#f5f7f9}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}main{max-width:1250px;margin:auto;padding:32px 24px}h1{font-size:26px;margin:0 0 5px}h2{font-size:20px;margin:0 0 12px}p{margin:5px 0 18px}.muted{color:var(--muted);font-size:13px}.layout{display:grid;grid-template-columns:minmax(300px,1fr) minmax(350px,1.6fr);gap:32px;align-items:start}.asset{padding:16px 0;border-top:1px solid var(--line)}.asset div{display:flex;justify-content:space-between;gap:16px;margin:12px 0}.asset strong{font-size:20px}.links{display:flex;gap:18px;flex-wrap:wrap;margin:20px 0}a{color:var(--accent)}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{position:sticky;top:0;background:var(--surface);font-weight:600}td.num,th.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}tr:hover td{background:var(--surface)}.scroll{overflow:auto;max-height:690px;border:1px solid var(--line)}.controls{display:flex;gap:12px;margin:12px 0;flex-wrap:wrap;align-items:center}input,select,button{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:3px;background:var(--bg);color:var(--text)}input{flex:1;min-width:220px}button{cursor:pointer}button:hover{border-color:var(--accent)}.sectorName{min-width:200px}.bar{height:3px;margin-top:5px;background:var(--accent)}section.holdings{margin-top:32px}footer{border-top:1px solid var(--line);padding-top:15px;margin-top:26px}.summary{padding:16px 0 5px;border-top:1px solid var(--line)}.summary b{font-size:24px;color:var(--accent)}
@media(max-width:850px){.layout{grid-template-columns:1fr}main{padding:22px 14px}.scroll{max-height:560px}}@media(prefers-color-scheme:dark){:root{--bg:#161b21;--text:#e3e8ed;--muted:#a5b0bd;--line:#343e49;--accent:#8cb7df;--surface:#202831}}@media print{.scroll{max-height:none;overflow:visible}input,select,button{display:none}.layout{display:block}th{position:static}body{font-size:11px}}
</style><main>
<h1>Sapphire Equity Long-Short Fund</h1><p class="muted">As of 30 September 2026 · Franklin Templeton · Factsheet page 4</p>
<div class="links"><a href="../../data/factsheets/latest/extraction/Sapphire%20Equity%20Long-Short%20Fund.json">Original scheme JSON</a><a href="sapphire-allocations-and-holdings.json" download>Download allocation and holdings JSON</a><a href="../../data/factsheets/latest/Franklin%20Templeton%20Mutual%20Fund.pdf#page=4">Source PDF, page 4</a></div>
<div class="layout"><section><h2>Asset allocation</h2><div id="assets" class="asset"></div><p class="muted">Percent of net assets. Published holdings, margin and cash totals are separate components.</p><div class="summary"><b>35</b> industry-chart rows · <b>118</b> portfolio rows</div><p class="muted">The industry chart combines derivative margin and cash into 6.25%. Its rounded labels total 100.01%; original percentages are retained.</p></section>
<section><h2>Industry allocation — equity assets</h2><p class="muted">Published chart labels · % of net assets · 30 September 2026</p><div class="scroll"><table><thead><tr><th>Industry / allocation category</th><th class="num">Allocation (%)</th></tr></thead><tbody id="sectors"></tbody></table></div></section></div>
<section class="holdings"><h2>All holdings and exposure rows</h2><p class="muted">Percentages are of net assets. Holdings and derivative exposure remain in separate columns. A dash indicates a missing extracted value. Values are shown as stored in the source JSON.</p>
<div class="controls"><input id="search" type="search" aria-label="Search instrument" placeholder="Search instrument"><select id="filter" aria-label="Filter records"><option value="all">All portfolio rows</option><option value="holdings">Positive holdings</option><option value="derivatives">Rows with derivative exposure</option></select><select id="sort" aria-label="Sort records"><option value="source">Source order</option><option value="weight">Largest holdings first</option><option value="name">Instrument A–Z</option></select><button id="csv">Download displayed CSV</button><span id="count" class="muted"></span></div>
<div class="scroll"><table><thead><tr><th>Instrument</th><th class="num">Holding (%)</th><th class="num">Derivative exposure (%)</th><th class="num">Quantity</th><th class="num">Market value (INR lakhs)</th><th class="num">PDF page</th></tr></thead><tbody id="holdings"></tbody></table></div></section>
<footer class="muted">Source: the locally extracted September 2026 official factsheet. Chart totals and rounded holdings totals can differ. These files contain the existing extracted data; this report does not infer weights or rebase percentages.</footer>
</main><script>
const data=PAYLOAD;
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=v=>v==null?'—':Number(v).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
document.querySelector('#assets').innerHTML=Object.entries(data['Asset Allocation (%)']).map(([k,v])=>`<div><span>${esc(k)}</span><strong>${num(v)}%</strong></div>`).join('');
document.querySelector('#sectors').innerHTML=Object.entries(data['Sector Allocation (%)']).map(([k,v])=>`<tr><td class="sectorName">${esc(k)}<div class="bar" style="width:${v/17.47*100}%"></div></td><td class="num">${num(v)}%</td></tr>`).join('');
let displayed=[];function render(){const q=document.querySelector('#search').value.toLowerCase();const f=document.querySelector('#filter').value;const sort=document.querySelector('#sort').value;displayed=data.Holdings.filter(r=>r.Instrument.toLowerCase().includes(q)&&(f==='all'||f==='holdings'&&r['Percent to Net Assets']>0||f==='derivatives'&&r['Derivatives Percent to Net Assets']!=null));if(sort==='weight')displayed.sort((a,b)=>(b['Percent to Net Assets']??-Infinity)-(a['Percent to Net Assets']??-Infinity));if(sort==='name')displayed.sort((a,b)=>a.Instrument.localeCompare(b.Instrument));document.querySelector('#holdings').innerHTML=displayed.map(r=>`<tr><td>${esc(r.Instrument)}</td><td class="num">${num(r['Percent to Net Assets'])}</td><td class="num">${num(r['Derivatives Percent to Net Assets'])}</td><td class="num">${r.Quantity==null?'—':Number(r.Quantity).toLocaleString('en-IN')}</td><td class="num">${num(r['Market Value (INR Lakhs)'])}</td><td class="num">${esc(r['Source Page'])}</td></tr>`).join('');document.querySelector('#count').textContent=displayed.length+' / '+data.Holdings.length+' rows';}
['search','filter','sort'].forEach(id=>document.getElementById(id).addEventListener('input',render));
document.querySelector('#csv').onclick=()=>{const keys=Object.keys(data.Holdings[0]);const quote=v=>'"'+String(v??'').replace(/"/g,'""')+'"';const csv=[keys,...displayed.map(r=>keys.map(k=>r[k]))].map(r=>r.map(quote).join(',')).join('\\r\\n');const url=URL.createObjectURL(new Blob(['\\ufeff'+csv],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='sapphire-holdings-2026-09-30.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};render();
</script></html>'''.replace('PAYLOAD',payload)
    path=OUT/'sapphire-allocations-and-holdings.html';path.write_text(page,encoding='utf-8')
    print(path)
    print('Allocation rows:',len(data['Sector Allocation (%)']),'Portfolio rows:',len(data['Holdings']))

if __name__=='__main__':main()
