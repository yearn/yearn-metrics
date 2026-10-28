"""Redis headline publishers for analysis outputs."""

from __future__ import annotations

import json
import hashlib
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .storage import from_json


LIFETIME_YIELD_HEADLINE_KEY = "lifetime_yield:headline"


def _decimal_text(value: Decimal | str | int | float) -> str:
    return format(Decimal(str(value)), "f")


def _iso_run_id(timestamp: int | None) -> str:
    if timestamp is None:
        return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def latest_lifetime_yield_anchor(conn, analysis_run_id: int | None = None) -> tuple[Decimal, str]:
    """Return a fully valued completed lifetime-yield total and default run id."""
    row, summary = _complete_lifetime_yield_run(conn, analysis_run_id)
    run_id = f"analysis-run-{row['id']}:{_iso_run_id(row['completed_at'] or row['started_at'])}"
    return Decimal(str(summary['net_yield_usd'])), run_id


def _complete_lifetime_yield_run(conn, analysis_run_id):
    params: tuple[Any, ...]
    if analysis_run_id is None:
        where = "r.name='lifetime-yield' AND r.status='complete'"
        params = ()
    else:
        where = "r.id=? AND r.name='lifetime-yield' AND r.status='complete'"
        params = (analysis_run_id,)

    row = conn.execute(
        f"""
        SELECT r.id, r.completed_at, r.started_at, r.params_json, o.row_json
        FROM analysis_runs r
        JOIN analysis_outputs o
          ON o.run_id = r.id
         AND o.name = 'total_yield_summary'
        WHERE {where}
        ORDER BY r.id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if row is None:
        if analysis_run_id is None:
            raise ValueError("no completed lifetime-yield analysis output found; run `yearn-data analyze lifetime-yield` first")
        raise ValueError(f"completed lifetime-yield analysis run {analysis_run_id} was not found")

    summary = from_json(row["row_json"])
    if "net_yield_usd" not in summary or summary["net_yield_usd"] is None:
        raise ValueError(f"lifetime-yield analysis run {row['id']} has no net_yield_usd")
    counts = [summary.get(field) for field in ('reports', 'priced_reports', 'unpriced_reports')]
    if (any(type(count) is not int or count < 0 for count in counts)
            or counts[0] != counts[1] or counts[2] != 0):
        raise ValueError(f"lifetime-yield analysis run {row['id']} has incomplete valuation")
    return row, summary


def _coverage_digest(reports):
    # Canonical row JSON includes event identity, raw/valued amounts and provenance.
    rows = sorted(json.dumps(row, sort_keys=True, separators=(',', ':')) for row in reports)
    return hashlib.sha256('\n'.join(rows).encode()).hexdigest()


def _comparable_previous(previous, historical_digest, policy):
    if not previous or previous.get('valuation_complete') is not True:
        return None
    cutoff = previous.get('coverage_through_timestamp')
    if type(cutoff) is not int or previous.get('valuation_policy') != policy:
        return None
    if historical_digest != previous.get('coverage_digest'):
        return None
    return previous


def _retained_coverage(conn, run_id, expected_count, previous_cutoff, *, include_digests=True):
    # Only bounded publications can seed a reusable digest. Unbounded runs still
    # validate every row, without a database sort or JSON reserialization.
    current, historical = hashlib.sha256(), hashlib.sha256()
    statement = "SELECT row_json FROM analysis_outputs WHERE run_id=? AND name='reports'"
    if include_digests:
        statement += ' ORDER BY row_json COLLATE "C"'
    count, historical_count, latest = 0, 0, 0
    with conn.raw.transaction():
        for output in conn.stream(statement, (run_id,)):
            report = from_json(output['row_json'])
            timestamp = report.get('block_timestamp')
            if type(timestamp) is not int:
                raise ValueError(f"lifetime-yield analysis run {run_id} has incomplete report coverage")
            if include_digests:
                encoded = json.dumps(report, sort_keys=True, separators=(',', ':')).encode()
                if count:
                    current.update(b'\n')
                current.update(encoded)
                if type(previous_cutoff) is int and timestamp <= previous_cutoff:
                    if historical_count:
                        historical.update(b'\n')
                    historical.update(encoded)
                    historical_count += 1
            count += 1
            latest = max(latest, timestamp)
    if count != expected_count:
        raise ValueError(f"lifetime-yield analysis run {run_id} has incomplete report coverage")
    return (current.hexdigest() if include_digests else None,
            historical.hexdigest() if include_digests else None, latest)


def build_lifetime_yield_headline(
    net_yield_usd: Decimal | str | int | float,
    previous: dict[str, Any] | None,
    *,
    now_ms: int | None = None,
    run_id: str,
    coverage_through_timestamp: int | None = None,
) -> dict[str, Any]:
    """Build the Redis payload while preserving monotonic timestamps and non-negative rate."""

    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    new_value = Decimal(str(net_yield_usd))
    previous = previous or None
    if previous:
        prev_as_of_ms = int(previous["as_of_ms"])
        now_ms = max(now_ms, prev_as_of_ms)
        prev_value = Decimal(str(previous["net_yield_usd"]))
        if coverage_through_timestamp is not None:
            dt_seconds = Decimal(coverage_through_timestamp - previous['coverage_through_timestamp'])
        else:
            dt_seconds = Decimal(now_ms - prev_as_of_ms) / Decimal(1000)
        if dt_seconds > 0:
            rate = max(0.0, float((new_value - prev_value) / dt_seconds))
        else:
            rate = 0.0
    else:
        prev_value = new_value
        prev_as_of_ms = now_ms
        rate = 0.0

    return {
        "net_yield_usd": new_value,
        "as_of_ms": now_ms,
        "rate_usd_per_sec": rate,
        "prev_net_yield_usd": prev_value,
        "prev_as_of_ms": prev_as_of_ms,
        "run_id": run_id,
    }


def lifetime_yield_headline_json(payload: dict[str, Any]) -> str:
    """Serialize the headline payload with Decimal values as JSON numbers, not strings."""

    fields = [
        f'"net_yield_usd": {_decimal_text(payload["net_yield_usd"])}',
        f'"as_of_ms": {int(payload["as_of_ms"])}',
        f'"rate_usd_per_sec": {float(payload["rate_usd_per_sec"])}',
        f'"prev_net_yield_usd": {_decimal_text(payload["prev_net_yield_usd"])}',
        f'"prev_as_of_ms": {int(payload["prev_as_of_ms"])}',
        f'"run_id": {json.dumps(str(payload["run_id"]))}',
    ]
    for key in ('valuation_complete', 'coverage_through_timestamp', 'coverage_digest', 'valuation_policy', 'extrapolation_status'):
        if key in payload:
            fields.append(f'{json.dumps(key)}: {json.dumps(payload[key])}')
    return "{" + ",".join(fields) + "}"


def parse_previous_headline(raw: bytes | str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


def publish_lifetime_yield_headline(
    conn,
    *,
    redis_url: str | None = None,
    redis_client: Any | None = None,
    key: str = LIFETIME_YIELD_HEADLINE_KEY,
    ttl: int | None = None,
    analysis_run_id: int | None = None,
    run_id: str | None = None,
    now_ms: int | None = None,
    dry_run: bool = False,
) -> tuple[dict[str, Any], str]:
    """Publish or dry-run the latest lifetime-yield headline Redis blob."""

    row, summary = _complete_lifetime_yield_run(conn, analysis_run_id)
    net_yield_usd = Decimal(str(summary['net_yield_usd']))
    default_run_id = f"analysis-run-{row['id']}:{_iso_run_id(row['completed_at'] or row['started_at'])}"
    run_id = run_id or default_run_id
    params = from_json(row['params_json'])
    closed_cutoff = params.get('before_timestamp')
    coverage_through = closed_cutoff-1 if type(closed_cutoff) is int else None
    # Cutoff growth is allowed; all other source/configuration changes reset the rate.
    policy = hashlib.sha256(json.dumps({key: value for key, value in params.items()
        if key != 'before_timestamp'}, sort_keys=True).encode()).hexdigest()

    previous = None
    if redis_client is None and redis_url:
        from redis import Redis
        redis_client = Redis.from_url(redis_url)
    if redis_client is not None:
        previous = parse_previous_headline(redis_client.get(key))
    elif not dry_run:
        raise ValueError("redis_url is required unless dry_run=True or redis_client is provided")
    published_previous = previous

    if previous:
        now_ms = max(int(time.time()*1000) if now_ms is None else int(now_ms), int(previous['as_of_ms']))
    digest, historical_digest, latest = _retained_coverage(
        conn, row['id'], summary['reports'],
        previous.get('coverage_through_timestamp') if previous else None,
        include_digests=coverage_through is not None,
    )
    if coverage_through is None:
        # Wall-clock analysis time is not evidence of closed chain coverage.
        previous = None
        extrapolation_status = 'unbounded-analysis'
    else:
        coverage_through = max(coverage_through, latest)
        previous = _comparable_previous(previous, historical_digest, policy)
        extrapolation_status = ('anchor-not-read' if redis_client is None else
                                'coverage-verified' if previous else 'coverage-reset')
    payload = build_lifetime_yield_headline(
        net_yield_usd, previous, now_ms=now_ms, run_id=run_id,
        coverage_through_timestamp=coverage_through,
    )
    if published_previous:
        payload.update(prev_net_yield_usd=Decimal(str(published_previous['net_yield_usd'])),
                       prev_as_of_ms=int(published_previous['as_of_ms']))
    # net_yield_usd is a covered-time snapshot; as_of_ms records publication.
    # Consumers extrapolate from coverage_through_timestamp, not as_of_ms.
    payload.update(valuation_complete=True, coverage_through_timestamp=coverage_through,
                   coverage_digest=digest, valuation_policy=policy, extrapolation_status=extrapolation_status)
    payload_json = lifetime_yield_headline_json(payload)
    if not dry_run:
        kwargs = {"ex": ttl} if ttl else {}
        redis_client.set(key, payload_json, **kwargs)
    return payload, payload_json
