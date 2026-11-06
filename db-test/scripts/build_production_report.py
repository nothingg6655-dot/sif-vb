"""Create a self-contained production test report from the recorded test runs."""
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import math
import re
import statistics

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'production-tests'
CANVAS = Path(r'C:\Users\shris\.cursor\projects\c-Users-shris-Aikyam-Projects-Mutual-Fund-Guru-Vinayak-Bhosale-SIF-Project\canvases\production-backend-test-report.canvas.tsx')


def tap(path):
    text = path.read_text(encoding='utf-8')
    matches = list(re.finditer(r'^(not ok|ok) (\d+) - (.+)$', text, re.M))
    rows = []
    for i, match in enumerate(matches):
        block = text[match.end():matches[i+1].start() if i+1 < len(matches) else len(text)]
        duration = re.search(r'duration_ms: ([\d.]+)', block)
        name = match.group(3)
        status = 'FAIL' if match.group(1) == 'not ok' else 'SKIP' if '# SKIP' in name else 'PASS'
        rows.append({'name': name, 'status': status, 'category': 'Source regression',
                     'detail': block.strip() if status == 'FAIL' else '',
                     'elapsedMs': float(duration.group(1)) if duration else None})
    summary = {key: int(value) for key, value in re.findall(r'^# (tests|pass|fail|skipped|cancelled) (\d+)$', text, re.M)}
    duration = re.search(r'^# duration_ms ([\d.]+)', text, re.M)
    if duration:
        summary['elapsedMs'] = float(duration.group(1))
    return {'summary': summary, 'tests': rows, 'complete': 'tests' in summary}


def p95(values):
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered)*.95)-1] if ordered else 0


def main():
    live = json.loads((OUT / 'live-results.json').read_text(encoding='utf-8'))
    retests = json.loads((OUT / 'targeted-retests.json').read_text(encoding='utf-8'))
    local = tap(ROOT / 'data' / 'production-regression.tap')
    production = tap(OUT / 'production-db-regression.tap')
    latency = [r['elapsedMs'] for r in live['requests']]
    by_category = defaultdict(list)
    for item in live['requests']:
        by_category[item['category']].append(item['elapsedMs'])
    category_timings = [{'category': category, 'count': len(values),
        'mean': round(statistics.mean(values),1), 'p95': p95(values), 'max': max(values)}
        for category, values in sorted(by_category.items())]
    initial_errors = [r for r in live['requests'] if not r['ok']]
    recovered = all(all(a.get('passed') for a in r['attempts']) and r['payloadAgreement'] and r['peerStatus']==200 for r in retests)
    report = {'generatedAt': datetime.now(ZoneInfo('Asia/Kolkata')).isoformat(),
        'live': live, 'retests': retests, 'localRegression': local, 'productionRegression': production,
        'latency': {'mean': round(statistics.mean(latency),1), 'median': statistics.median(latency),
            'p95': p95(latency), 'max': max(latency), 'categories': category_timings},
        'initialTransportFailures': len(initial_errors), 'allTargetedRetestsPassed': recovered,
        'http5xx': sum(1 for r in live['requests'] if r['httpStatus'] and r['httpStatus']>=500),
        'calculationChecks': dict(Counter(c['status'] for c in live['checks'] if c['category']=='Calculation audit')),
        'scope': ['Live requests target the deployed Render URL; concurrency is limited to two.',
            'Source regression tests use current workspace code against production Supabase, with read-only credentials.',
            'The mapping-mutation test ran only against the preserved local database, inside a rolled-back transaction.',
            'Timing includes client network and TLS; this is a functional acceptance run, not a load/capacity test.',
            'The deployed Git commit is not exposed by the public API, so source-code regression is distinct from live deployment verification.'],
        'findings': [
            {'severity':'Test failure','title':'Historical reconciliation assertion is too strict for this snapshot',
             'detail':'reconciliation.test.ts:83 expects the best fixed-observation method to match fewer funds. The 30-day calendar method and 19-observation method both match 34/34; the 90-day method matches 34/34 versus 32/34 for the best fixed 61-observation method. Independent live NAV recalculation passes all 170 returns and 170 volatility cells.',
             'action':'Use a deterministic distinguishing fixture for the method comparison. Preserve the documented calendar policy; do not change financial calculations merely to satisfy this assertion.'},
            {'severity':'Recovered transport errors' if recovered else 'Transport errors',
             'title':f'{len(initial_errors)} first-pass connection errors',
             'detail':'Affected Arudha sector allocation, Arthaya sector allocation, and Arudha risk-band requests. Each endpoint was retried twice; counterpart payloads were compared. The original failures remain in the report.',
             'action':'Monitor for recurrence. The runner cannot determine whether the resets originated in the client network, Render edge, or the service.'},
            {'severity':'Data coverage','title':'Five schemes lack verified published net AUM',
             'detail':'; '.join(live['summary']['missingAum']),
             'action':'Keep missing net AUM explicit; do not substitute quarterly AAUM into the net AUM total.'},
        ]}
    (OUT / 'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    initial = live['summary']['checks']
    lines = ['# Production backend test report', '', f"Target: {live['baseUrl']}",
        f"Live run: {live['startedAt']} to {live['finishedAt']} (Asia/Kolkata)", '',
        f"- Live HTTP requests: {len(live['requests'])}; expected responses: {len(live['requests'])-len(initial_errors)}; connection errors: {len(initial_errors)}; HTTP 5xx: {report['http5xx']}.",
        f"- Live assertions: {initial.get('PASS',0)} passed, {initial.get('FAIL',0)} failed on the first pass. Three transport errors also caused three dependent equality assertions to fail.",
        f"- Targeted retests: {'all passed' if recovered else 'unresolved failures'}; each affected endpoint tested twice and matched its counterpart.",
        f"- Independent production NAV audit: {report['calculationChecks']}.",
        f"- Production-database source regression: {production['summary']}.",
        f"- Complete local regression: {local['summary']}.",
        f"- First-pass response times: median {report['latency']['median']:.1f} ms; p95 {report['latency']['p95']:.1f} ms; maximum {report['latency']['max']:.1f} ms.", '',
        f"Coverage: {live['summary']['funds']} Regular Growth schemes, {live['summary']['registeredStrategies']} registered strategies, four allocation families, both risk-band aliases, NAV history, return/volatility calculations, all three benchmarks, all heatmap/rolling ranges, validation errors, admin authentication, and browser CORS.", '',
        '## Findings', '']
    for finding in report['findings']:
        lines.extend([f"### {finding['title']}", '', finding['detail'], '', 'Next action: '+finding['action'], ''])
    lines.extend(['## Scope and limitations','', *['- '+s for s in report['scope']], '',
        'Raw live results: live-results.json', 'Targeted retests: targeted-retests.json',
        'Production database regression log: production-db-regression.tap',
        'Complete local regression log: ../production-regression.tap'])
    (OUT / 'report.md').write_text('\n'.join(lines),encoding='utf-8')
    template = r'''import { useState, useMemo, useHostTheme, Stack, Row, Grid, H1, H2, Text, Stat, Table, Pill, TextInput, Select, Button, Callout, BarChart, Divider, Link } from "cursor/canvas";
const R: any = JSON.parse(__REPORT__);
export default function ProductionReport() {
  const t=useHostTheme();
  const [tab,setTab]=useState('Findings'); const [status,setStatus]=useState('All');
  const [search,setSearch]=useState(''); const [page,setPage]=useState(0);
  const initial=R.live.summary.checks;
  const liveRows=R.live.checks;
  const sourceRows=R.productionRegression.tests;
  const requestRows=R.live.requests.map((r:any)=>({name:r.method+' '+r.path,category:r.category,status:r.ok?'PASS':'FAIL',detail:'HTTP '+r.httpStatus+' | '+r.elapsedMs+' ms',elapsedMs:r.elapsedMs}));
  const pool=tab==='Live assertions'?liveRows:tab==='HTTP requests'?requestRows:sourceRows;
  const rows=useMemo(()=>pool.filter((r:any)=>(status==='All'||r.status===status)&&((r.name+' '+r.category+' '+r.detail).toLowerCase().includes(search.toLowerCase()))),[pool,status,search]);
  const pages=Math.max(1,Math.ceil(rows.length/80)); const selected=rows.slice(page*80,(page+1)*80);
  return <Stack gap={20} style={{padding:24,maxWidth:1250,margin:'0 auto',color:t.text.primary}}>
    <Row justify="space-between" wrap><H1>Production backend test report</H1><Pill>Completed with findings</Pill></Row>
    <Text tone="secondary"><Link href={R.live.baseUrl}>{R.live.baseUrl}</Link> · {R.live.startedAt} to {R.live.finishedAt} · Asia/Kolkata</Text>
    <Callout tone="warning" title="Functional checks passed after targeted retries; one regression assertion fails">
      Initial live run: {initial.PASS} passed assertions and {initial.FAIL} failures caused by {R.initialTransportFailures} connection errors. All affected endpoints passed two targeted retries and payload agreement checks. The historical source regression assertion remains a failure.
    </Callout>
    <Grid columns={4} gap={20}>
      <Stat value={R.live.requests.length} label="Initial live HTTP requests"/>
      <Stat value={R.calculationChecks.PASS} label="Independent financial checks passed"/>
      <Stat value={R.http5xx} label="HTTP 5xx responses observed"/>
      <Stat value={R.latency.p95+' ms'} label="First-pass p95 response time"/>
    </Grid>
    <Row gap={8} wrap>{['Findings','Live assertions','HTTP requests','Source regression','Data coverage','Scope'].map(name=><Pill key={name} active={tab===name} onClick={()=>{setTab(name);setPage(0);setStatus('All');setSearch('');}}>{name}</Pill>)}</Row>
    {tab==='Findings'&&<Stack gap={24}>
      {R.findings.map((f:any)=><Stack key={f.title} gap={7}><Row gap={12}><H2>{f.title}</H2><Pill size="sm">{f.severity}</Pill></Row><Text>{f.detail}</Text><Text tone="secondary">Next action: {f.action}</Text></Stack>)}
      <Divider/>
      <H2>Response latency by API family</H2>
      <Text tone="secondary">Vertical axis: API family. Horizontal axis: response time in milliseconds. Series: p95 (nearest rank). Source: {R.live.requests.length} first-pass HTTPS requests, concurrency ≤2; includes client network and TLS.</Text>
      <BarChart horizontal categories={R.latency.categories.map((x:any)=>x.category)} series={[{name:'P95 response time (ms)',data:R.latency.categories.map((x:any)=>x.p95),tone:'neutral'}]} valueSuffix=" ms" height={480} showValues/>
      <Table headers={['Regression run','Passed','Failed','Skipped','Scope']} rows={[
        ['Production database',R.productionRegression.summary.pass,R.productionRegression.summary.fail,R.productionRegression.summary.skipped,'Current workspace code; live Supabase; read-only role'],
        ['Complete local suite',R.localRegression.summary.pass,R.localRegression.summary.fail,R.localRegression.summary.skipped,'Preserved local database; includes rollback-only mutation test'],
      ]}/>
    </Stack>}
    {['Live assertions','HTTP requests','Source regression'].includes(tab)&&<Stack gap={12}>
      <Row gap={12} wrap><TextInput value={search} onChange={v=>{setSearch(v);setPage(0);}} placeholder="Search scheme, path, category, or result" style={{minWidth:320}}/><Select value={status} onChange={v=>{setStatus(v);setPage(0);}} options={['All','PASS','FAIL','SKIP'].map(v=>({value:v,label:v}))}/><Text tone="secondary">{rows.length} matching results</Text></Row>
      {selected.length>0&&<Table stickyHeader striped headers={['Result','Category','Test / endpoint','Details']} rows={selected.map((r:any)=>[r.status,r.category,r.name,<span style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{r.detail}</span>])} rowTone={selected.map((r:any)=>r.status==='FAIL'?'danger':undefined)}/>}
      <Row justify="space-between"><Text tone="secondary">Page {page+1} of {pages} · 80 results per page</Text><Row><Button disabled={page===0} onClick={()=>setPage(page-1)}>Previous</Button><Button disabled={page+1>=pages} onClick={()=>setPage(page+1)}>Next</Button></Row></Row>
    </Stack>}
    {tab==='Data coverage'&&<Stack gap={18}>
      <Grid columns={4}><Stat value={R.live.summary.funds} label="Regular Growth schemes"/><Stat value={R.live.summary.registeredStrategies} label="Registered strategies tested"/><Stat value={R.live.summary.publishedAumSchemes} label="Schemes reporting net AUM"/><Stat value={'₹'+Math.round(R.live.summary.totalAumCr).toLocaleString('en-IN')+' Cr'} label="Published net AUM"/></Grid>
      <Text tone="secondary">NAV observations through {R.live.summary.navDates.join(', ')}. Monthly benchmark returns are partial month-to-date, not completed-month returns.</Text>
      <H2>Current-month benchmark returns</H2><Table headers={['Benchmark','Month-to-date return','Through date']} rows={R.live.summary.benchmarkMonthToDate.map((b:any)=>[b.name,b.return+'%',b.endDate])}/>
      <H2>Missing published net AUM</H2><Table headers={['Scheme']} rows={R.live.summary.missingAum.map((name:string)=>[name])}/>
      <H2>Explicitly unavailable allocation disclosures</H2><Table headers={['Scheme','Metric','Backend reason']} rows={R.live.warnings.map((w:any)=>[w.scheme,w.metric,w.reason])}/>
    </Stack>}
    {tab==='Scope'&&<Stack gap={12}><H2>What was tested</H2>{R.scope.map((s:string)=><Text key={s}>{s}</Text>)}<Text tone="secondary">This snapshot does not run destructive tests or ingestion on production, exercise every network failure mode, measure capacity, or prove that the deployed binary matches unexposed source commit metadata.</Text></Stack>}
  </Stack>;
}
'''
    content = template.replace('__REPORT__', json.dumps(json.dumps(report, separators=(',',':')), ensure_ascii=True))
    CANVAS.write_text(content,encoding='utf-8')
    print(json.dumps({'canvas':str(CANVAS),'markdown':str(OUT/'report.md'),'liveRequests':len(live['requests']),
        'liveAssertions':initial,'productionRegression':production['summary'],'localRegression':local['summary'],
        'retestsPassed':recovered,'p95Ms':report['latency']['p95']},indent=2))


if __name__=='__main__':
    main()
