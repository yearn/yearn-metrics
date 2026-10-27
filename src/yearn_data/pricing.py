"""Historical pricing job selection, acquisition policy and database writes."""
from __future__ import annotations
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Any
import os
import time
import requests
from .config import CHAINS
from .storage import to_json
from . import pricing_adapters as adapters
from . import pricing_clients as clients
from .pricing_adapters import fetch_defillama_price as fetch_defillama_price, fetch_defillama_prices_batch as fetch_defillama_prices_batch
from .pricing_adapters import ONCHAIN_ADAPTER_SOURCES
from .pricing_clients import (
    yearn_prices_config as yearn_prices_config,
    yearn_prices_batches as yearn_prices_batches,
    YearnPricesConfigurationError as YearnPricesConfigurationError,
    YearnPricesAuthenticationError as YearnPricesAuthenticationError,
    YearnPricesRequestError as YearnPricesRequestError,
    YearnPricesResponseError as YearnPricesResponseError,
    fetch_yearn_price as fetch_yearn_price,
    fetch_yearn_prices_batch as fetch_yearn_prices_batch,
    normalize_yearn_price_timestamp as normalize_yearn_price_timestamp,
    yearn_prices_token_key as yearn_prices_token_key,
    defillama_coin_id as defillama_coin_id,
    YEARN_PRICES_DEFAULT_BASE as YEARN_PRICES_DEFAULT_BASE,
)

SUPPORTED_SOURCES = {"defillama", "yearn-prices"}

DEFAULT_PRICE_SOURCE = "yearn-prices"

DEFAULT_FALLBACK_PRICE_SOURCE = "defillama"


def report_amount(raw_value: str | int, decimals: int | None) -> Decimal:
    scale = Decimal(10) ** int(decimals or 18)
    return Decimal(int(raw_value)) / scale


def _existing_price_filter(retry_missing: bool) -> str:
    if retry_missing:
        return "p.token_address IS NULL OR p.status != 'ok'"
    return "p.token_address IS NULL"


def _adapter_refresh_sql(enabled: bool, target: str) -> tuple[str, str]:
    if not enabled:
        return '', 'FALSE'
    join = f"""LEFT JOIN prices adapter_price
        ON adapter_price.chain_id={target}.chain_id
        AND adapter_price.token_address={target}.asset
        AND adapter_price.timestamp={target}.block_timestamp
        AND adapter_price.source='defillama'"""
    sources = ','.join("'"+name+"'" for name in ONCHAIN_ADAPTER_SOURCES)
    match = f"adapter_price.status='ok' AND json_extract(adapter_price.raw_json,'$.source') IN ({sources})"
    return join, match


def _refresh_adapter_rows(conn, rows, *, onchain_fallbacks):
    refresh = [row for row in rows if row['refresh_adapter']]
    count = _price_unpriced_reports_defillama_batched(conn, refresh, None, onchain_fallbacks=onchain_fallbacks) if refresh else 0
    return count, [row for row in rows if not row['refresh_adapter']]


def _fetch_price(source: str, chain_id: int, token_address: str, timestamp: int, block_number: int):
    if source == "defillama":
        return adapters.fetch_defillama_price(chain_id, token_address, timestamp)
    if source == "yearn-prices":
        return clients.fetch_yearn_price(chain_id, token_address, timestamp)
    raise ValueError(f"unsupported price source {source!r}; expected {sorted(SUPPORTED_SOURCES)}")


def validate_report_pricing_policy(
    source: str, fallback: str | None, onchain_fallbacks: bool, *, require_credentials: bool = False
) -> None:
    if source not in SUPPORTED_SOURCES:
        raise ValueError(f"unsupported price source {source!r}")
    if fallback is not None and fallback not in SUPPORTED_SOURCES:
        raise ValueError(f"unsupported fallback source {fallback!r}")
    if source == "yearn-prices" and fallback != "defillama" and onchain_fallbacks:
        raise ValueError(
            "onchain fallbacks require DefiLlama provider fallback; "
            "use --no-onchain-fallbacks with --no-provider-fallback"
        )
    if require_credentials and source == "yearn-prices":
        clients.yearn_prices_config()


def price_unpriced_reports(
    conn,
    limit: int | None = None,
    source: str = DEFAULT_PRICE_SOURCE,
    fallback: str | None = DEFAULT_FALLBACK_PRICE_SOURCE,
    retry_missing: bool = False,
    refresh_onchain_fallbacks: bool = False,
    chain_ids: set[int] | None = None,
    onchain_fallbacks: bool = True,
) -> int:
    validate_report_pricing_policy(source, fallback, onchain_fallbacks)
    if refresh_onchain_fallbacks and source == 'yearn-prices' and fallback != 'defillama':
        raise ValueError('refreshing onchain fallbacks requires DefiLlama provider fallback')
    chain_filter = ""
    chain_params: list[int] = []
    if chain_ids:
        chain_filter = f"AND r.chain_id IN ({','.join('?' for _ in chain_ids)})"
        chain_params = sorted(chain_ids)
    price_filter = _existing_price_filter(retry_missing)
    price_params: list[int] = []
    if source == "yearn-prices" and not retry_missing:
        price_filter = "p.token_address IS NULL OR (p.status = 'pending' AND r.block_timestamp < ?)"
        price_params = [int(time.time()) // 86_400 * 86_400]
    refresh_join, refresh_match = _adapter_refresh_sql(refresh_onchain_fallbacks, 'r')
    sql = f"""
    SELECT
        r.chain_id,
        r.asset,
        r.block_timestamp,
        MIN(r.block_number) AS block_number,
        MAX(CASE WHEN ({refresh_match}) THEN 1 ELSE 0 END) AS refresh_adapter
    FROM strategy_reports r
    LEFT JOIN prices p
      ON p.chain_id = r.chain_id
     AND p.token_address = r.asset
     AND p.timestamp = r.block_timestamp
     AND p.source = ?
    {refresh_join}
    WHERE r.asset IS NOT NULL
      {chain_filter}
      AND (({price_filter}) OR ({refresh_match}))
    GROUP BY r.chain_id, r.asset, r.block_timestamp
    ORDER BY r.block_timestamp
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, (source, *chain_params, *price_params)).fetchall()
    count, rows = _refresh_adapter_rows(conn, rows, onchain_fallbacks=onchain_fallbacks)
    if source == "defillama":
        count += _price_unpriced_reports_defillama_batched(conn, rows, fallback, onchain_fallbacks=onchain_fallbacks)
        return count
    if source == "yearn-prices":
        primary_count, unresolved_rows = _price_rows_with_yearn_prices(conn, rows)
        count += primary_count
        if fallback == "defillama":
            count += _price_unpriced_reports_defillama_batched(
                conn,
                unresolved_rows,
                fallback=None,
                onchain_fallbacks=onchain_fallbacks,
            )
        return count

    for row in rows:
        sources = [source]
        if fallback and fallback != source:
            sources.append(fallback)
        for price_source in sources:
            price, status, payload = _fetch_price(
                price_source,
                int(row["chain_id"]),
                row["asset"],
                int(row["block_timestamp"]),
                int(row["block_number"]),
            )
            conn.execute(
                """
                    INSERT INTO prices (
                        chain_id, token_address, timestamp, block_number,
                        source, price_usd, status, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chain_id,token_address,timestamp,source) DO UPDATE SET
                        block_number=excluded.block_number,
                        price_usd=excluded.price_usd,
                        status=excluded.status,
                        raw_json=excluded.raw_json
                """,
                (
                    int(row["chain_id"]),
                    row["asset"],
                    int(row["block_timestamp"]),
                    int(row["block_number"]),
                    price_source,
                    price,
                    status,
                    to_json(payload),
                ),
            )
            count += 1
            if status == "ok":
                break
        if count % 100 == 0:
            conn.commit()
            time.sleep(0.2)
    conn.commit()
    return count


def price_unpriced_volume(
    conn,
    limit: int | None = None,
    source: str = "defillama",
    fallback: str | None = None,
    retry_missing: bool = False,
    refresh_onchain_fallbacks: bool = False,
    chain_ids: set[int] | None = None,
    onchain_fallbacks: bool = True,
) -> int:
    if source not in SUPPORTED_SOURCES:
        raise ValueError(f"unsupported price source {source!r}")
    if fallback is not None and fallback not in SUPPORTED_SOURCES:
        raise ValueError(f"unsupported fallback source {fallback!r}")
    if refresh_onchain_fallbacks and source == 'yearn-prices' and fallback != 'defillama':
        raise ValueError('refreshing onchain fallbacks requires DefiLlama provider fallback')
    rows = _unpriced_volume_rows(
        conn,
        source,
        limit,
        retry_missing=retry_missing,
        refresh_onchain_fallbacks=refresh_onchain_fallbacks,
        chain_ids=chain_ids,
    )
    count, rows = _refresh_adapter_rows(conn, rows, onchain_fallbacks=onchain_fallbacks)
    if source == "defillama":
        count += _price_unpriced_reports_defillama_batched(conn, rows, fallback, onchain_fallbacks=onchain_fallbacks)
        return count
    if source == "yearn-prices":
        primary_count, unresolved_rows = _price_rows_with_yearn_prices(conn, rows)
        count += primary_count
        if fallback == 'defillama':
            count += _price_unpriced_reports_defillama_batched(
                conn, unresolved_rows, fallback=None, onchain_fallbacks=onchain_fallbacks)
        return count

    for row in rows:
        sources = [source]
        if fallback and fallback != source:
            sources.append(fallback)
        for price_source in sources:
            price, status, payload = _fetch_price(
                price_source,
                int(row["chain_id"]),
                row["asset"],
                int(row["block_timestamp"]),
                int(row["block_number"]),
            )
            conn.execute(
                """
                    INSERT INTO prices (
                        chain_id, token_address, timestamp, block_number,
                        source, price_usd, status, raw_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chain_id,token_address,timestamp,source) DO UPDATE SET
                        block_number=excluded.block_number,
                        price_usd=excluded.price_usd,
                        status=excluded.status,
                        raw_json=excluded.raw_json
                """,
                (
                    int(row["chain_id"]),
                    row["asset"],
                    int(row["block_timestamp"]),
                    int(row["block_number"]),
                    price_source,
                    price,
                    status,
                    to_json(payload),
                ),
            )
            count += 1
            if status == "ok":
                break
        if count % 100 == 0:
            conn.commit()
            time.sleep(0.2)
    conn.commit()
    return count


def _unpriced_volume_rows(
    conn,
    source: str,
    limit: int | None = None,
    retry_missing: bool = False,
    refresh_onchain_fallbacks: bool = False,
    chain_ids: set[int] | None = None,
) -> list[dict[str, int | str]]:
    rows_by_key: dict[tuple[int, str, int], dict[str, int | str]] = {}
    chain_filter = ""
    chain_params: list[int] = []
    if chain_ids:
        chain_filter = f"AND f.chain_id IN ({','.join('?' for _ in chain_ids)})"
        chain_params = sorted(chain_ids)
    price_filter = _existing_price_filter(retry_missing)
    price_params = []
    if source == 'yearn-prices' and not retry_missing:
        price_filter = "p.token_address IS NULL OR (p.status = 'pending' AND f.block_timestamp < ?)"
        price_params = [int(time.time()) // 86_400 * 86_400]
    refresh_join, refresh_match = _adapter_refresh_sql(refresh_onchain_fallbacks, 'f')
    for table in ("vault_flows", "strategy_debt_flows"):
        table_rows = conn.execute(
            f"""
            SELECT
                f.chain_id,
                f.asset,
                f.block_timestamp,
                MIN(f.block_number) AS block_number,
                MAX(CASE WHEN ({refresh_match}) THEN 1 ELSE 0 END) AS refresh_adapter
            FROM {table} f
            LEFT JOIN prices p
              ON p.chain_id = f.chain_id
             AND p.token_address = f.asset
             AND p.timestamp = f.block_timestamp
             AND p.source = ?
            {refresh_join}
            WHERE f.asset IS NOT NULL
              {chain_filter}
              AND (({price_filter}) OR ({refresh_match}))
            GROUP BY f.chain_id, f.asset, f.block_timestamp
            ORDER BY f.block_timestamp
            """,
            (source, *chain_params, *price_params),
        ).fetchall()
        for row in table_rows:
            key = (int(row["chain_id"]), row["asset"], int(row["block_timestamp"]))
            existing = rows_by_key.get(key)
            if existing is None or int(row["block_number"]) < int(existing["block_number"]):
                rows_by_key[key] = {
                    "chain_id": int(row["chain_id"]),
                    "asset": row["asset"],
                    "block_timestamp": int(row["block_timestamp"]),
                    "block_number": int(row["block_number"]),
                    "refresh_adapter": row["refresh_adapter"],
                }
    rows = sorted(rows_by_key.values(), key=lambda row: int(row["block_timestamp"]))
    if limit:
        return rows[: int(limit)]
    return rows


def _chunks(rows, size: int):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _price_unpriced_reports_defillama_batched(
    conn,
    rows,
    fallback: str | None,
    onchain_fallbacks: bool = True,
) -> int:
    count = 0
    batches = list(_defillama_coin_batches(rows))
    workers = max(1, int(os.environ.get("YEARN_DATA_PRICE_WORKERS", "8")))
    with (
        ThreadPoolExecutor(max_workers=workers) as fetch_executor,
        ThreadPoolExecutor(max_workers=workers) as fallback_executor,
    ):
        futures = [fetch_executor.submit(_fetch_defillama_batch, batch) for batch in batches]
        for future in as_completed(futures):
            batch, results = future.result()
            fallback_futures = {}
            for row in batch:
                key = (int(row["chain_id"]), row["asset"], int(row["block_timestamp"]))
                result = results.get(key, (None, "missing", {}))
                if (onchain_fallbacks and result[1] != "ok"
                        and result[2].get("failure_class") != "unsupported-chain"):
                    fallback_future = fallback_executor.submit(
                        adapters.fetch_fallback_price,
                        int(row["chain_id"]),
                        row["asset"],
                        int(row["block_timestamp"]),
                        int(row["block_number"]),
                    )
                    fallback_futures[fallback_future] = row
                    continue
                count += _write_defillama_price_row(
                    conn,
                    row,
                    results,
                    fallback,
                    onchain_fallbacks=onchain_fallbacks,
                )
            conn.commit()
            for fallback_future in as_completed(fallback_futures):
                count += _write_defillama_price_row(
                    conn,
                    fallback_futures[fallback_future],
                    results,
                    fallback,
                    onchain_fallbacks=onchain_fallbacks,
                    fallback_result=fallback_future.result(),
                )
                if count % 100 == 0:
                    conn.commit()
            conn.commit()
    conn.commit()
    return count


def _unsupported_chain_price(chain_id: int, source: str) -> tuple[None, str, dict[str, Any]]:
    return None, "missing", {
        "provider": source,
        "failure_class": "unsupported-chain",
        "failure_reason": f"{source} has no configured pricing namespace for chain {chain_id}",
    }


def _fetch_defillama_batch(batch):
    requests_ = [
        (int(row["chain_id"]), row["asset"], int(row["block_timestamp"]))
        for row in batch
    ]
    configured_chains = {cfg.chain_id for cfg in CHAINS.values()}
    results = {target: _unsupported_chain_price(target[0], "defillama")
               for target in requests_ if target[0] not in configured_chains}
    supported_requests = [target for target in requests_ if target[0] in configured_chains]
    if not supported_requests:
        return batch, results
    try:
        results.update(adapters.fetch_defillama_prices_batch(supported_requests))
    except Exception:
        for chain_id, token_address, timestamp in supported_requests:
            try:
                result = adapters.fetch_defillama_price(chain_id, token_address, timestamp)
            except requests.RequestException as error:
                result = (None, "retryable", {
                    "provider": "defillama",
                    "failure_class": "retryable",
                    "failure_reason": type(error).__name__,
                    "timestamp": timestamp,
                })
            results[(chain_id, token_address, timestamp)] = result
    return batch, results


def _write_defillama_price_row(
    conn,
    row,
    results,
    fallback: str | None,
    onchain_fallbacks: bool = True,
    fallback_result: tuple[float | None, str, dict[str, Any]] | None = None,
) -> int:
    chain_id = int(row["chain_id"])
    token_address = row["asset"]
    timestamp = int(row["block_timestamp"])
    block_number = int(row["block_number"])
    price, status, payload = results.get(
        (chain_id, token_address, timestamp),
        (None, "missing", {"timestamp": timestamp}),
    )
    if status != "ok" and onchain_fallbacks and payload.get("failure_class") != "unsupported-chain":
        if fallback_result is None:
            fallback_result = adapters.fetch_fallback_price(
                chain_id,
                token_address,
                timestamp,
                block_number,
            )
        fallback_price, fallback_status, fallback_payload = fallback_result
        if fallback_status == "ok":
            price, status, payload = fallback_price, fallback_status, fallback_payload
        else:
            payload = {
                "source": "defillama",
                "status": status,
                "timestamp": timestamp,
                "fallback": fallback_payload,
            }
    cursor = conn.execute(
        """
        INSERT INTO prices (
            chain_id, token_address, timestamp, block_number,
            source, price_usd, status, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (chain_id, token_address, timestamp, source) DO UPDATE SET
            block_number = excluded.block_number,
            price_usd = excluded.price_usd,
            status = excluded.status,
            raw_json = excluded.raw_json
        WHERE prices.status != 'ok' OR excluded.status = 'ok'
        """,
        (
            chain_id,
            token_address,
            timestamp,
            block_number,
            "defillama",
            price,
            status,
            to_json(payload),
        ),
    )
    return cursor.rowcount


def _defillama_coin_batches(rows, max_timestamps: int = 400):
    rows_by_coin: dict[tuple[int, str], list] = defaultdict(list)
    for row in rows:
        rows_by_coin[(int(row["chain_id"]), row["asset"])].append(row)
    for coin_rows in rows_by_coin.values():
        coin_rows.sort(key=lambda row: int(row["block_timestamp"]))
        yield from _chunks(coin_rows, max_timestamps)


def _price_rows_with_yearn_prices(conn, rows) -> tuple[int, list]:
    if not rows:
        return 0, []

    report_targets = [
        (int(row["chain_id"]), row["asset"].lower(), int(row["block_timestamp"]))
        for row in rows
    ]
    normalized_targets = {
        (chain_id, token_address, clients.normalize_yearn_price_timestamp(timestamp))
        for chain_id, token_address, timestamp in report_targets
    }
    results: dict[tuple[int, str, int], tuple[float | None, str, dict[str, Any]]] = {}
    supported_chains = set()
    for chain_id in {target[0] for target in normalized_targets}:
        try:
            clients.yearn_prices_token_key(chain_id, adapters.ZERO_ADDRESS)
        except ValueError:
            continue
        supported_chains.add(chain_id)
    day_start = int(time.time()) // 86_400 * 86_400
    for target in normalized_targets:
        if target[0] not in supported_chains:
            results[target] = _unsupported_chain_price(target[0], "yearn-prices")
        elif target[2] >= day_start:
            results[target] = (None, "pending", {
                "provider": "yearn-prices",
                "normalized_timestamp": target[2],
                "failure_class": "day-not-closed",
                "failure_reason": "UTC day is still open; an ordinary pricing run will retry after day-end",
            })
    batches = clients.yearn_prices_batches(list(normalized_targets - results.keys()))
    batch_workers = max(1, int(os.environ.get("YEARN_DATA_PRICE_WORKERS", "8")))
    with ThreadPoolExecutor(max_workers=batch_workers) as executor:
        futures = [executor.submit(clients.fetch_yearn_prices_batch, batch) for batch in batches]
        for future in as_completed(futures):
            try:
                results.update(future.result())
            except (YearnPricesRequestError, YearnPricesResponseError):
                # Verify failed or malformed batches through exact requests.
                # Invalid evidence is never accepted as a usable price.
                continue

    missing_targets = sorted(normalized_targets - results.keys())
    exact_workers = max(1, int(os.environ.get("YEARN_PRICE_EXACT_WORKERS", "5")))
    with ThreadPoolExecutor(max_workers=exact_workers) as executor:
        futures = {
            executor.submit(clients.fetch_yearn_price, chain_id, token_address, timestamp): (
                chain_id,
                token_address,
                timestamp,
            )
            for chain_id, token_address, timestamp in missing_targets
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()

    insert_rows = []
    unresolved_rows = []
    for row in rows:
        chain_id = int(row["chain_id"])
        token_address = row["asset"]
        report_timestamp = int(row["block_timestamp"])
        normalized_timestamp = clients.normalize_yearn_price_timestamp(report_timestamp)
        price, status, result_payload = results[(chain_id, token_address.lower(), normalized_timestamp)]
        if status != "ok":
            unresolved_rows.append(row)
        payload = dict(result_payload)
        payload["requested_timestamp"] = report_timestamp
        payload.setdefault("normalized_timestamp", normalized_timestamp)
        insert_rows.append(
            (
                chain_id,
                token_address,
                report_timestamp,
                int(row["block_number"]),
                "yearn-prices",
                price,
                status,
                to_json(payload),
            )
        )
    conn.executemany(
        """
            INSERT INTO prices (
                chain_id, token_address, timestamp, block_number,
                source, price_usd, status, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chain_id,token_address,timestamp,source) DO UPDATE SET
                block_number=excluded.block_number,
                price_usd=excluded.price_usd,
                status=excluded.status,
                raw_json=excluded.raw_json
        """,
        insert_rows,
    )
    conn.commit()
    return len(insert_rows), unresolved_rows
