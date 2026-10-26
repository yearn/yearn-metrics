# Cumulative Powerglove serving measurement — October 26, 2026

This compares the actual Powerglove production build before all three serving
passes with the final build, backed by the same actual published Yearn Data in
read-only Neon. It measures browser navigation through the private Tailscale
preview, rather than extrapolating synthetic backend timings.

## Frozen revisions and publication

| Component | Before | After |
| --- | --- | --- |
| Yearn Data | `3f6df4b1095bddce6ddb1762df4a1c2341ca4834` | `a123d1bc58e769c34887688bc34144e31bfb7efd` |
| Powerglove | `df81fdddfb8c70e66e40a89260326ca66bd8bc14` | `94487b84f6ca0fd49c7717c6a4ab313f2220247a` |

The baseline frontend is the parent of the first grouped-history optimization,
not that optimization itself. The after backend includes grouped histories,
ordered summary accumulation and validated bounded JSON row loading; frontend
changes use grouped histories and reuse FeesPanel's selected dataset in the monthly table.
The final row-loading backend is now committed locally as `a123d1b`.

Both complete hosted APIs used the same existing Neon database and `public`
schema, `default_transaction_read_only=on`, GET-only runners and verified TLS.
No publication, consolidation, release selection, CI configuration or remote
branch was changed by this measurement. Production builds were served by Vite
preview through private Tailscale Serve. There was no CDN on this path.

Publication identities were checked before and after navigation:

- Release: `7b10155e5cae005f7884cb9dd8fd223867c6bcc07dee5bc347e04e1daa246b47`
- Analytics: `55986fd711b6f6ba6a03fa16af8b1bda0ac2708914a746e44125784c2da1f4b9`
- Fees: `07edb93a75b7c6a8854bdba80378865f3059e3985c87bc71e1cb8d98bcbd8dc3`
- TVL: `281c3ab6f3ae76683e9c8c2eca2f5f22d7663ba2f4f7776b7c4df4bdc0136461`

## Browser boundary

Each navigation launches a new Chromium process/context with a 1440 × 1000
viewport and empty browser caches. Navigation begins with the application's
normal default range; the timeline then bounds it to available TVL history.
Both final selections were October 25, 2022 through October 21, 2026. A loaded
sample requires all eight fee summary values without unavailable/loading
placeholders, actual chain/type chart paths with completed loading states,
a real monthly vault table with vault links, a rendered TVL chart, no fee error
or loading state, no outstanding fee/TVL request, and the final overall history
request matching both selected calendar dates. This successful state must remain
stable for one second; the recorded ready time subtracts that confirmation second.
This is **fee-critical UI ready with TVL chart**, not whole-page settlement or
completion of every auxiliary analysis/vault chart. Rounded visible values are
checked separately from exact API financial parity.

The first pair started with fresh backend processes whose only prior request was
publication discovery. Database, OS and provider caches were not flushed, and
requests within a navigation can share application caches. Subsequent pairs keep
the same server processes and therefore measure warm-server navigation with a
fresh browser. They alternate version order. Server and browser cache conditions
must not be conflated; the first single pair is not a cold-database distribution.

## Results

The first fresh-process navigation improved from **26.874 s to 13.281 s**
(**50.6% lower**). This is one pair, run before then after, with uncontrolled
DB/OS warmth; it must not be presented as a repeated cold-start median.

Four subsequent fresh-browser, warm-server pairs alternated version order:

| Pair / order | Before ready (s) | After ready (s) |
| --- | ---: | ---: |
| 2 / after then before | 5.361 | 2.825 |
| 3 / before then after | 4.117 | 2.448 |
| 4 / after then before | 4.229 | 4.045 |
| 5 / before then after | 3.758 | 2.740 |
| Median | **4.173** | **2.783** |

The warm-server ready median fell **33.3%**. Ranges were 3.758–5.361 s before
and 2.448–4.045 s after. These four samples are too few for a precise production
speedup. The slowest optimized sample is retained, without outlier removal.

| Observed completed API responses | Before | After |
| --- | ---: | ---: |
| First fresh-process navigation | 93 | 34 |
| Each warm-server navigation | 96 | 37 |
| Warm decoded response bodies | 26,944,332 bytes | 14,660,359 bytes |
| Warm compressed response-body median (Playwright sizes) | 2,633,127.5 bytes | 2,029,543 bytes |

There are **59 fewer completed API responses** in both conditions. Warm decoded
body bytes fell 45.6%; compressed response-body bytes fell 22.9%. These counts
include all observed `/api/` responses, not just fee routes, and exclude canceled
requests. The raw bundle retains cancellation records: some default-range
requests were aborted normally as the TVL-bounded range replaced them. Every
completed response in all ten valid navigations returned HTTP 200; all ten
reached the successful target state, with no browser exceptions or visible fee
errors. No canceled request was silently classified as a successful response.

Warm runs complete three additional responses per version compared with the
first pair because more default-range work finishes before the range changes;
this does not change the 59-response difference. Both versions still issue the
initial default-range summary and the TVL-bounded replacement summary. The
monthly-table discovery summary disappears, reducing the first pair's summary
requests from four to three. Server responses are gzip encoded and advertise
cache policy, but no CDN HIT was involved on this local preview path.

First-pair summary, chain/type-chart and monthly-table milestones respectively
fell from 16.579/26.093/26.849 s to 9.448/12.631/13.251 s. These are local observed
milestones, not isolated backend phase attribution. On warm servers the summary
first-ready milestone was slower after (median 1.506 s versus 0.800 s), while
chain/type charts and the monthly table improved (4.146→2.493 s and
3.806→2.611 s). The overall gain does not imply every individual stage improved.

All request timings, sizes, headers, hashes, valid and invalid samples are in
[the raw measurement bundle](cumulative-powerglove-performance-2026-10-26.json).

## Apparatus failures retained

An initial sandboxed Chromium launch failed before any navigation; authorized
unsandboxed launch worked. An exploratory warm attempt then exposed a harness
bug: it compared the selected range with the last completed per-vault history
rather than the overall history. Its primary UI was ready by 2.749 s, but the
incorrect matcher produced a 120-second timeout. The following baseline sample
was interrupted. Both records remain under `invalidApparatusSamples`; neither
contributes to the reported warm results. The corrected harness was frozen for
all four reported warm pairs and stops on an unsuccessful sample.

The first fresh-process pair predates this matcher correction, but both its
recorded final `rangeMatches` checks were true with the same selected dates and
successful UI state. Its validity does not depend on treating the later false
matcher timeout as an application failure. Optional screenshots and response
collection occur after status serialization; collection has a bounded wait.

## Financial parity

All ten valid navigations' rendered summaries, monthly table text and chart titles match
exactly, SHA-256 `072c7b8026265d4473420c41ebf0ad14c9e9a09317bd00288402df85244889c8`.
That includes the monthly table's explicit partial/unavailable counts, all 12
completed-month columns and currently displayed vault rows. The table reports
857 vaults, a partial annual earnings total of $12,406,374 and 313 incomplete
vaults. A matching visible rounded value alone would not establish exact parity.

The captured responses provide the stronger financial check: the first pair’s 32
shared response instances (29 distinct normalized request keys), including
summaries, fee history and TVL data, are deeply equal. The new compact grouped-history response is an
intentional contract projection, so its whole-response hash cannot match the
original individual vault responses. Instead, all 48 contract-scope and 12
product-scope buckets were matched to original responses using the exact bucket
start/end timestamps, dataset pin and scope. All 14,070 returned vault rows match
in order, identity and every compact financial field, including exact Decimal
strings, null and genuine zero. Compact `lifetimeEarnings` intentionally retains
`netYieldUsd`; unrelated source fields are not asserted to exist in that projection.
The same compact comparisons passed for each of the four warm pairs (35 common response instances per pair), and all ten rendered hashes match. Both grouped responses retain data cutoff timestamp `1792627200`.

- Contract grouped response: `d47014e68bc2cb63672bc551c4b535ceda8d09b9b135f0fa708ab29b2cb62433`
- Product grouped response: `82d8dbb04a9a2668f2a50fba124c48f4741ab065e1457242dc2051a425f2b905`

These checks establish baseline equivalence on this publication, alongside the
existing independent known-result domain tests; they do not independently prove
that every financial policy is correct.

## Reproduction and limits

Use `scripts/benchmark_powerglove_browser.mjs` with private before/after preview
origins in `POWERGLOVE_BEFORE_URL` and `POWERGLOVE_AFTER_URL`. These origins are
supplied only through the environment and never retained in this report or raw
bundle. Playwright is an external prerequisite: install `playwright` where Node
can resolve it from the script, or set `PLAYWRIGHT_MODULE` to an installed
Playwright ESM entry point (absolute path or file URL). Install Chromium for that
Playwright version before running browser measurements (`playwright install chromium`
using that installation's CLI). The script defaults to the ordinary `playwright`
package import. This module-resolution portability follow-up was made after the
frozen run; the raw bundle retains the original measurement script's hash.
`POWERGLOVE_REPEATS` sets pairs, `POWERGLOVE_PAIR_OFFSET` preserves alternating
order across invocations, and `POWERGLOVE_BENCHMARK_OUTPUT` chooses the JSON file.
Optional `POWERGLOVE_PAYLOAD_DIR` retains local response payloads for exact
comparison; compare them without additional database requests using
`scripts/compare_powerglove_browser.cjs before.json after.json output.json`.

The bundle retains request paths/query parameters, response hashes, publication
metadata, HTTP status/timing/size and cache headers. Decoded body bytes are not
wire bytes; request sizes are also recorded. This is one shared host accessing
its own private preview, not external-user geography, production CDN behaviour,
a confidence interval, an isolated attribution of each optimization or a claim
that every page panel completes at the reported time. Heavy local tests/builds
were held during measurement. Small samples still permit network/provider drift.

## Delivery validation

Both frozen frontend production builds passed. Both opt-in measurement scripts
pass Node syntax checks; the ten valid browser navigations and five paired
financial comparisons passed. Independent review found no blocking issues.
The measured backend revision passed 1,355 tests with 7 skipped. No application
code was changed during the timed measurement, and CI remains unchanged.

### Post-measurement Ethereum chart fix

The PR also fixes an Ethereum drilldown that requested a compact top-ten chart
but hit the full-export point limit before compaction. Compact histories now
rank and reduce before enforcing the existing output limits. The frontend uses
`topMetric=constant-price` for adjusted rankings, preserving unavailable prices,
source order, dated reference windows and remainder totals. Compact `pointCount`
now describes emitted chart points; full-export limits remain unchanged.

This follow-up is not part of the frozen cumulative timing comparison above.
Browser checks verified actual, price-adjusted and version Ethereum views, each
with 209 dated rows and the same publication pin. Review additionally covered
unpriced actual capital in remainder-series metadata and the prepared-history
HTTP route/cache path. The backend must be deployed before the paired frontend
because it introduces the grouped-history route and adjusted compact contract.

Final delivery validation after the review fixes: **1,358 backend tests passed,
7 skipped** against disposable Postgres; **481 frontend tests passed**. Focused
lint, Node syntax and diff checks passed. Frontend production build and Ethereum
browser checks passed. CI execution remains unchanged.
