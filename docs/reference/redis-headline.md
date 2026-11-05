# Optional Redis headline

A ticking estimate needs bounded analyses, not just `run lifetime-yield`, which
produces unbounded totals. After indexing and pricing, analyze through an explicit
closed UTC midnight and publish:

```bash
CUTOFF=$(date -u -d '00:00 today' +%s)
yearn-data --db staging analyze lifetime-yield --before-timestamp "$CUTOFF"
REDIS_URL='rediss://...' yearn-data --db staging publish lifetime-yield --ttl 259200
```

The publisher atomically replaces `lifetime_yield:headline` with one JSON blob containing `net_yield_usd`, `as_of_ms`, `rate_usd_per_sec`, previous anchor fields, and `run_id`. Use `--dry-run` to inspect without writing Redis. With a Redis URL configured,
it reads the existing anchor and makes the same extrapolation decision as a
publish. Without Redis it reports `anchor-not-read` rather than predicting a
reset. Unbounded analyses always report `unbounded-analysis`.

The first bounded publication seeds a zero-rate anchor. Repeat at a later cutoff
to derive a rate from a comparable covered interval. `net_yield_usd` is the stored
value through `coverage_through_timestamp`, while `as_of_ms` is publication time.
For a `coverage-verified` estimate, consumers tick from the coverage timestamp:
`net_yield_usd + rate_usd_per_sec * max(0, now_ms / 1000 - coverage_through_timestamp)`.
This includes any delay between the close and publication; it remains an estimate.
Do not tick from `as_of_ms`. On resets or unbounded runs the rate is zero.
The `prev_*` fields retain the actual previous publication even when no rate can
be inferred; they do not fabricate a replacement anchor on resets.

Publishing requires explicit complete valuation counts and retained report rows.
Incomplete or legacy summaries with unknown completeness fail before replacing
Redis, including in dry-run mode. Rejection leaves an existing anchor unchanged,
but does not extend its TTL: it remains available only until its existing expiry.
With the example TTL above, the key can disappear after 72 hours without a
successful publication. A
complete genuine zero remains publishable. Payloads add completeness and hashed
coverage/policy metadata. Extrapolation continues only when the previous anchor
is verified complete and its historical report rows match the current run.
For analyses with an explicit `--before-timestamp`, the rate divides new yield
by the advance in covered chain time, independent of publication frequency.
Cutoff-less analyses (including `run lifetime-yield`) publish complete totals
with a zero rate, `coverage_through_timestamp: null` and
`extrapolation_status: "unbounded-analysis"`; analysis start time never stands in
for closed chain coverage. Bounded anchors report `coverage-verified` or
`coverage-reset` according to the historical comparability check. Retained coverage is checked in
one bounded-memory streaming pass; unbounded runs validate rows without sorting
or computing unusable coverage digests. New reports after that history can contribute yield; backfills, changed historical
valuations, source-policy changes and legacy anchors reset the rate to zero.
Only the publication timestamp `as_of_ms` remains monotonic across a reset.
Selecting an earlier bounded analysis can reduce `coverage_through_timestamp`
and the covered value; selecting an unbounded analysis sets coverage to `null`.
Consumers must read the new coverage and status rather than assuming forward
financial coverage across resets. Existing Redis publications
are not changed until this command is explicitly invoked.
This optional publisher is separate from hosted release selection and the nightly
wrapper. Keep the Redis URL server-side and explicitly select the analysis database.

[Documentation index](../README.md).
