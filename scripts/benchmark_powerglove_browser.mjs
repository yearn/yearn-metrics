/** Opt-in real-browser comparison. URLs remain environment-only; outputs redact origins. */
import fs from 'node:fs'
import { createHash } from 'node:crypto'
const targets = { before: process.env.POWERGLOVE_BEFORE_URL, after: process.env.POWERGLOVE_AFTER_URL }
if (!targets.before || !targets.after) throw Error('Set POWERGLOVE_BEFORE_URL and POWERGLOVE_AFTER_URL')
let chromium
try {
 const playwright = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
 chromium = playwright.chromium
} catch (error) {
 if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error
 console.error('Playwright is unavailable. Install playwright in a location Node can resolve from this script, or set PLAYWRIGHT_MODULE to an installed Playwright ESM entry point (absolute path or file URL).')
 process.exit(1)
}
const hash = v => createHash('sha256').update(typeof v === 'string' ? v : JSON.stringify(v)).digest('hex')
const safe = value => value.replace(/https?:\/\/[^/\s"']+/g, '<origin>')
const output = process.env.POWERGLOVE_BENCHMARK_OUTPUT || '/tmp/cumulative-powerglove-browser.json'
const results = { methodology: { browser: 'Chromium, fresh process and context each navigation', viewport: [1440,1000], browserCache: 'fresh', serverCache: 'uncontrolled warm OS/database/process caches; response headers retained', readiness: 'fee-critical UI: successful fee summaries, chain/type chart groups and actual monthly table with TVL chart and final selected-range history, no fee error or loading placeholder; stable successful state for 1 second', timeoutMs: 120000, repeatPairs: Number(process.env.POWERGLOVE_REPEATS || 5), origins: 'redacted; actual private URLs supplied only through environment' }, runs: [] }
for (let pair = Number(process.env.POWERGLOVE_PAIR_OFFSET || 0); pair < Number(process.env.POWERGLOVE_PAIR_OFFSET || 0)+results.methodology.repeatPairs; pair++) {
 for (const version of pair % 2 ? ['after','before'] : ['before','after']) {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
  const context = await browser.newContext({ viewport: { width:1440,height:1000 }, ignoreHTTPSErrors: true })
  const page = await context.newPage()
  const run = { pair:pair+1, version, requests:[], failures:[], milestones:{}, errors:[] }
  const pending = new Set()
  const inFlightFees = new Set()
  let finalHistory = null
  const payloads=[]
  page.on('request', request => { if(new URL(request.url()).pathname.match(/^\/api\/(fees|tvl)/)) inFlightFees.add(request) })
  page.on('requestfinished', request => inFlightFees.delete(request))
  page.on('requestfailed', request => inFlightFees.delete(request))
  page.on('pageerror', error => run.errors.push(safe(error.message)))
  page.on('requestfailed', request => run.failures.push({ path:new URL(request.url()).pathname, error:request.failure()?.errorText }))
  page.on('requestfinished', request => {
   if (!request.url().includes('/api/')) return
   const task = (async () => {
    const response = await request.response(); const url = new URL(request.url()); const headers = await response.allHeaders()
    const row = { path:url.pathname, query:url.search, status:response.status(), timing:request.timing(), sizes:await request.sizes().catch(()=>({})), headers:Object.fromEntries(Object.entries(headers).filter(([key])=> /cache|age|server-timing|content-encoding|content-length/.test(key))) }
    try { const body=await response.body(); row.bodyBytes=body.length; row.sha256=hash(body.toString()); const parsed=JSON.parse(body); payloads.push({path:row.path,query:row.query,body:parsed}); if(row.path==='/api/fees/history'&&row.status===200&&!new URLSearchParams(row.query).has('chainId')&&!new URLSearchParams(row.query).has('vaultAddress')) finalHistory=new URLSearchParams(row.query); row.publication=Object.fromEntries(Object.entries(parsed).filter(([key])=> /datasetId|publication|aggregationScope|cutoff|generatedAt|startTimestamp|endTimestamp/.test(key))) } catch {}
    run.requests.push(row)
   })(); pending.add(task); task.finally(()=>pending.delete(task))
  })
  try {
   await page.goto(targets[version], { waitUntil:'domcontentloaded',timeout:60000 })
   let stableSince=null
   const deadline=Date.now()+120000
   while(Date.now()<deadline) {
    const state=await page.evaluate(()=>{
     const panel=document.querySelector('.fees-panel'); const summary=[...document.querySelectorAll('.fee-summary-stat dd')].map(e=>e.textContent.trim())
     const monthly=[...document.querySelectorAll('.monthly-vault-fees')].find(e=>e.querySelector('h2')?.textContent.includes('Monthly Vault'))
     const groups=['Earnings and fees by chain','Earnings and fees by vault type'].map(label=>document.querySelector(`[aria-label="${label}"]`))
     return { now:performance.now(), summary:summary.length===8&&!summary.some(v=>v==='…'||v==='—'||v===''), monthly:!!monthly?.querySelector('table:not(.monthly-fee-skeleton) tbody tr a'), charts:groups.every(e=>e&&e.getAttribute('aria-busy')==='false'&&e.querySelector('svg path.recharts-curve,svg .recharts-bar-rectangle')), tvl:!!document.querySelector('.tvl-history-chart svg path.recharts-curve, .tvl-history-chart svg .recharts-bar-rectangle'), range:[...document.querySelectorAll('.fee-date-thumb')].map(e=>({day:e.getAttribute('aria-valuenow'),label:e.getAttribute('aria-valuetext')})), busy:!panel||panel.getAttribute('aria-busy')==='true', errors:panel?[...panel.querySelectorAll('.error-retry')].map(e=>e.textContent.trim()):[] }
    })
    for(const key of ['summary','monthly','charts','tvl']) if(state[key]&&!run.milestones[key]) run.milestones[key]=state.now
    run.lastState=state
    const rangeMatches = finalHistory && state.range.length===2 && Number(finalHistory.get('since'))===Number(state.range[0].day)*86400 && Number(finalHistory.get('until'))===(Number(state.range[1].day)+1)*86400
    run.lastState.rangeMatches=!!rangeMatches
    if(rangeMatches&&state.summary&&state.monthly&&state.charts&&state.tvl&&state.range.length===2&&!state.busy&&!state.errors.length&&inFlightFees.size===0) { stableSince ??= Date.now(); if(Date.now()-stableSince>=1000){run.loadedMs=state.now-1000;run.status='loaded';break} } else stableSince=null
    await page.waitForTimeout(100)
   }
   run.status ??='timeout'
   run.rendered=await page.evaluate(()=>({ summary:[...document.querySelectorAll('.fee-summary-stat')].map(e=>e.innerText), monthly:[...document.querySelectorAll('.monthly-vault-fees')].find(e=>e.querySelector('h2')?.textContent.includes('Monthly Vault'))?.innerText, chartTitles:[...document.querySelectorAll('.fees-panel h2')].map(e=>e.textContent) }))
   run.renderedSha256=hash(run.rendered)
   fs.writeFileSync(output,JSON.stringify({...results,inProgress:run},null,2)+'\n')
   if(process.env.POWERGLOVE_SCREENSHOT_DIR) await page.screenshot({path:`${process.env.POWERGLOVE_SCREENSHOT_DIR}/${version}-${pair+1}.png`,fullPage:true})
  } catch(error) {run.status='failed';run.errors.push(safe(error.message))}
  await Promise.race([Promise.allSettled([...pending]),new Promise(resolve=>setTimeout(resolve,10000))]); await browser.close()
  if(process.env.POWERGLOVE_PAYLOAD_DIR) fs.writeFileSync(`${process.env.POWERGLOVE_PAYLOAD_DIR}/${version}-${pair+1}.json`,JSON.stringify(payloads))
  run.requests.sort((a,b)=>a.timing.startTime-b.timing.startTime)
  run.apiCount=run.requests.length;run.apiBodyBytes=run.requests.reduce((sum,r)=>sum+(r.bodyBytes||0),0)
  results.runs.push(run); fs.writeFileSync(output,JSON.stringify(results,null,2)+'\n')
  console.log(JSON.stringify({pair:run.pair,version,status:run.status,loadedMs:run.loadedMs,milestones:run.milestones,apiCount:run.apiCount,apiBodyBytes:run.apiBodyBytes,errors:run.errors,lastState:run.lastState}))
  if(run.status!=='loaded') process.exitCode=1
  if(run.status!=='loaded') throw Error('Unsuccessful sample retained; stop to investigate before additional database requests')
 }
}
