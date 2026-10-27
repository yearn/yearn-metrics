"""Provider HTTP clients and validated evidence; no database or local valuation.

Batch request failures raise provider exceptions; sparse Yearn batch omissions are
explicit unresolved targets in PriceBatch.target_results and need exact lookup.
"""
from __future__ import annotations
from collections import defaultdict
from math import isfinite
from typing import Any, NamedTuple
import json
import os
import re
import time
from urllib.parse import quote
import requests
from .config import CHAINS

DEFILLAMA_BASE = "https://coins.llama.fi"

DEFILLAMA_SEARCH_WIDTH_SECONDS = 12 * 60 * 60

YEARN_PRICES_DEFAULT_BASE = "https://prices.yearn.dev"

YEARN_PRICES_MAX_TOKEN_KEYS = 50

YEARN_PRICES_MAX_TIMESTAMPS_PER_TOKEN = 90

TOKEN_ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")

DEFILLAMA_RETRY_ATTEMPTS = 7

DEFILLAMA_RETRY_MAX_SECONDS = 30.0


class PriceResult(NamedTuple):
    value: float | None
    status: str
    provenance: dict[str, Any]

    @property
    def provider(self) -> str | None:
        return self.provenance.get('provider')

    @property
    def reason(self) -> str:
        return self.provenance.get('failure_reason') or self.provenance.get('failure_class') or self.status

    @property
    def requested_timestamp(self) -> int | None:
        return self.provenance.get('requested_timestamp')

    @property
    def evidence_timestamp(self) -> int | None:
        return self.provenance.get('evidence_timestamp')


def price_result(value, status, provenance, *, provider, requested_timestamp) -> PriceResult:
    evidence = dict(provenance)
    evidence['provider'] = provider
    evidence['requested_timestamp'] = requested_timestamp
    matched = evidence.get('matched') or {}
    evidence['evidence_timestamp'] = (evidence.get('normalized_timestamp') if provider == 'yearn-prices'
        else matched.get('timestamp')) if status == 'ok' else None
    return PriceResult(value, status, evidence)


class PriceBatch(dict):
    """Accepted/supplied results remain mapping compatible; all targets are explicit."""
    target_results: dict[tuple[int, str, int], PriceResult]

    def __init__(self, results, targets, *, provider):
        super().__init__(results)
        self.target_results = {target: self.get(target) or price_result(None, 'unresolved',
            {'failure_class': 'unaccepted-batch-target'}, provider=provider, requested_timestamp=target[2])
            for target in targets}


def defillama_coin_id(chain_id: int, token_address: str) -> str:
    cfg = next(c for c in CHAINS.values() if c.chain_id == int(chain_id))
    return f"{cfg.defillama_slug}:{token_address.lower()}"


def normalize_yearn_price_timestamp(timestamp: int) -> int:
    """Normalize an event timestamp to the canonical Yearn Prices UTC day-end."""
    timestamp = int(timestamp)
    if timestamp < 0:
        raise ValueError("price timestamp must be non-negative")
    return timestamp // 86_400 * 86_400 + 86_399


def yearn_prices_token_key(chain_id: int, token_address: str) -> str:
    if not TOKEN_ADDRESS_PATTERN.fullmatch(token_address):
        raise ValueError(f"invalid token address {token_address!r}")
    # Canonical Yearn Prices namespaces for catalog chains outside fee defaults.
    additional_chains = {100: 'gnosis', 146: 'sonic', 999: 'hyperliquid', 80094: 'berachain', 4663: 'robinhood'}
    if int(chain_id) in additional_chains:
        return f"{additional_chains[int(chain_id)]}:{token_address.lower()}"
    try:
        cfg = next(c for c in CHAINS.values() if c.chain_id == int(chain_id))
    except StopIteration as error:
        raise ValueError(f"Yearn Prices does not support configured chain {chain_id}") from error
    return f"{cfg.defillama_slug}:{token_address.lower()}"


class YearnPricesConfigurationError(ValueError):
    """Operator configuration prevents requesting usable price evidence."""


def yearn_prices_config() -> tuple[str, str]:
    api_key = os.environ.get("YEARN_PRICE_PROD_KEY", "").strip()
    if not api_key:
        raise YearnPricesConfigurationError("missing Yearn Prices API key; set YEARN_PRICE_PROD_KEY")
    base_url = os.environ.get("YEARN_PRICE_PROD_BASE_URL", YEARN_PRICES_DEFAULT_BASE).strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise YearnPricesConfigurationError("YEARN_PRICE_PROD_BASE_URL must be an absolute HTTP(S) URL")
    return base_url, api_key


class YearnPricesAuthenticationError(RuntimeError):
    pass


class YearnPricesRequestError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class YearnPricesResponseError(ValueError):
    """Unusable upstream price evidence, distinct from local configuration errors."""


def _yearn_prices_get(
    path: str,
    *,
    params: dict[str, str] | None = None,
    timeout: int = 10,
    allow_not_found: bool = False,
) -> dict[str, Any] | None:
    base_url, api_key = yearn_prices_config()
    try:
        attempts = max(1, int(os.environ.get("YEARN_PRICE_HTTP_ATTEMPTS", "3")))
        retry_base = max(0.0, float(os.environ.get("YEARN_PRICE_HTTP_RETRY_BASE_SECONDS", "0.25")))
    except ValueError as error:
        raise YearnPricesConfigurationError("invalid Yearn Prices HTTP retry configuration") from error
    last_error: Exception | None = None
    last_retryable = True

    for attempt in range(attempts):
        response = None
        try:
            response = requests.get(
                f"{base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                timeout=timeout,
            )
            if response.status_code in {401, 403}:
                raise YearnPricesAuthenticationError(
                    f"Yearn Prices authentication failed (HTTP {response.status_code})"
                )
            if allow_not_found and response.status_code == 404:
                return None
            if response.status_code == 408 or response.status_code == 429 or response.status_code >= 500:
                raise requests.HTTPError(f"Yearn Prices returned HTTP {response.status_code}", response=response)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise YearnPricesResponseError("Yearn Prices returned a non-object JSON response")
            return payload
        except YearnPricesAuthenticationError:
            raise
        except (requests.RequestException, ValueError) as error:
            last_error = error
            retryable = response is None or response.status_code in {408, 429} or response.status_code >= 500
            last_retryable = retryable
            if not retryable or attempt == attempts - 1:
                break
            retry_after = response.headers.get("retry-after") if response is not None else None
            try:
                delay = min(float(retry_after), 30.0) if retry_after is not None else retry_base * (2**attempt)
            except ValueError:
                delay = retry_base * (2**attempt)
            time.sleep(max(0.0, delay))

    raise YearnPricesRequestError(
        f"Yearn Prices request failed after {attempts} attempt(s)",
        retryable=last_retryable,
    ) from last_error


def _parse_yearn_price_point(
    raw_point: Any,
    *,
    expected_timestamp: int,
    symbol: Any,
    adapter: str,
) -> tuple[float, str, dict[str, Any]] | None:
    if not isinstance(raw_point, dict):
        raise YearnPricesResponseError("Yearn Prices returned a malformed price point")
    price = raw_point.get("price")
    if price == 0 and not isinstance(price, bool):
        return None
    if (
        raw_point.get("timestamp") != expected_timestamp
        or not isinstance(price, (int, float))
        or isinstance(price, bool)
        or not isfinite(float(price))
        or float(price) <= 0
        or not isinstance(raw_point.get("source"), str)
        or not raw_point["source"]
    ):
        raise YearnPricesResponseError("Yearn Prices returned a malformed price point")
    confidence = raw_point.get("confidence")
    if confidence is not None and (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not isfinite(float(confidence))
    ):
        raise YearnPricesResponseError("Yearn Prices returned malformed confidence metadata")
    payload = {
        "adapter": adapter,
        "confidence": confidence,
        "normalized_timestamp": expected_timestamp,
        "provider": "yearn-prices",
        "symbol": symbol if isinstance(symbol, str) else None,
        "upstream_source": raw_point["source"],
    }
    return float(price), "ok", payload


def fetch_yearn_prices_batch(
    targets: list[tuple[int, str, int]],
    timeout: int = 10,
) -> PriceBatch:
    """Fetch normalized EOD targets from the Yearn Prices batch endpoint.

    Omitted targets are intentionally absent from the result. The caller verifies
    each omission against the exact endpoint before recording it as missing.
    """
    coins: dict[str, list[int]] = defaultdict(list)
    target_by_key: dict[tuple[str, int], tuple[int, str, int]] = {}
    for chain_id, token_address, timestamp in targets:
        normalized = normalize_yearn_price_timestamp(timestamp)
        token_key = yearn_prices_token_key(chain_id, token_address)
        key = (token_key, normalized)
        if key not in target_by_key:
            coins[token_key].append(normalized)
            target_by_key[key] = (int(chain_id), token_address.lower(), normalized)
    if len(coins) > YEARN_PRICES_MAX_TOKEN_KEYS or any(
        len(timestamps) > YEARN_PRICES_MAX_TIMESTAMPS_PER_TOKEN for timestamps in coins.values()
    ):
        raise ValueError("Yearn Prices batch exceeds the documented API limits")

    payload = _yearn_prices_get(
        "/api/prices/batchHistorical",
        params={"coins": json.dumps(coins, separators=(",", ":"), sort_keys=True)},
        timeout=timeout,
    )
    raw_coins = payload.get("coins") if payload is not None else None
    if not isinstance(raw_coins, dict):
        raise YearnPricesResponseError("Yearn Prices returned a malformed batch response")

    output: dict[tuple[int, str, int], tuple[float, str, dict[str, Any]]] = {}
    seen_targets: set[tuple[int, str, int]] = set()
    for raw_token_key, raw_coin in raw_coins.items():
        if not isinstance(raw_token_key, str) or not isinstance(raw_coin, dict):
            continue
        token_key = raw_token_key.lower()
        prices = raw_coin.get("prices")
        if not isinstance(prices, list):
            continue
        for raw_point in prices:
            if not isinstance(raw_point, dict) or not isinstance(raw_point.get("timestamp"), int):
                continue
            lookup = (token_key, raw_point["timestamp"])
            target = target_by_key.get(lookup)
            if target is None:
                continue
            if target in seen_targets:
                # Neither copy is authoritative, even if one is a valid price.
                output.pop(target, None)
                continue
            seen_targets.add(target)
            try:
                parsed = _parse_yearn_price_point(
                    raw_point,
                    expected_timestamp=target[2],
                    symbol=raw_coin.get("symbol"),
                    adapter="batchHistorical",
                )
            except YearnPricesResponseError:
                continue
            if parsed is not None:
                output[target] = parsed
    return PriceBatch({target: price_result(*result, provider='yearn-prices', requested_timestamp=target[2])
        for target, result in output.items()}, target_by_key.values(), provider='yearn-prices')


def _fetch_yearn_price(
    chain_id: int,
    token_address: str,
    timestamp: int,
    timeout: int = 10,
) -> tuple[float | None, str, dict[str, Any]]:
    normalized = normalize_yearn_price_timestamp(timestamp)
    token_key = yearn_prices_token_key(chain_id, token_address)
    try:
        payload = _yearn_prices_get(
            f"/api/prices/historical/{normalized}/{quote(token_key, safe=':')}",
            timeout=timeout,
            allow_not_found=True,
        )
        parsed = None
        if payload is not None:
            raw_coins = payload.get("coins")
            if not isinstance(raw_coins, dict) or len(raw_coins) != 1:
                raise YearnPricesResponseError("Yearn Prices returned a malformed exact response")
            raw_key, raw_point = next(iter(raw_coins.items()))
            if not isinstance(raw_key, str) or raw_key.lower() != token_key:
                raise YearnPricesResponseError("Yearn Prices returned an unexpected exact token key")
            parsed = _parse_yearn_price_point(
                raw_point,
                expected_timestamp=normalized,
                symbol=raw_point.get("symbol") if isinstance(raw_point, dict) else None,
                adapter="historical",
            )
    except (YearnPricesRequestError, YearnPricesResponseError) as error:
        failure_class = (
            "retryable" if isinstance(error, YearnPricesRequestError) and error.retryable else "invalid"
        )
        return None, failure_class, {
            "adapter": "historical",
            "failure_class": failure_class,
            "failure_reason": str(error),
            "normalized_timestamp": normalized,
            "provider": "yearn-prices",
        }
    if parsed is None:
        return None, "missing", {
            "adapter": "historical",
            "failure_class": "not-found",
            "normalized_timestamp": normalized,
            "provider": "yearn-prices",
        }
    return parsed


def fetch_yearn_price(chain_id: int, token_address: str, timestamp: int, timeout: int = 10) -> PriceResult:
    return price_result(*_fetch_yearn_price(chain_id, token_address, timestamp, timeout),
        provider='yearn-prices', requested_timestamp=normalize_yearn_price_timestamp(timestamp))


def match_defillama_samples(
    requested_timestamps: list[int],
    samples: list[Any],
) -> dict[int, dict[str, Any]]:
    """Match each request to its nearest valid observation within twelve hours."""
    requested = sorted(set(int(timestamp) for timestamp in requested_timestamps))
    valid_samples = [
        sample
        for sample in samples
        if isinstance(sample, dict)
        and isinstance(sample.get("timestamp"), (int, float))
        and not isinstance(sample.get("timestamp"), bool)
        and isfinite(float(sample["timestamp"]))
        and isinstance(sample.get("price"), (int, float))
        and not isinstance(sample.get("price"), bool)
        and isfinite(float(sample["price"]))
        and float(sample["price"]) > 0
    ]
    matched = {}
    for requested_timestamp in requested:
        candidates = [
            sample
            for sample in valid_samples
            if abs(int(sample["timestamp"]) - requested_timestamp) <= DEFILLAMA_SEARCH_WIDTH_SECONDS
        ]
        if candidates:
            matched[requested_timestamp] = min(
                candidates,
                key=lambda sample: abs(int(sample["timestamp"]) - requested_timestamp),
            )
    return matched


def defillama_get(url: str, *, timeout: int, params: dict[str, str] | None = None):
    """Retry rate-limited DefiLlama requests with bounded backoff."""

    for attempt in range(DEFILLAMA_RETRY_ATTEMPTS):
        response = requests.get(url, params=params, timeout=timeout)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        if attempt == DEFILLAMA_RETRY_ATTEMPTS - 1:
            response.raise_for_status()
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after is not None else 2**attempt
        except ValueError:
            delay = 2**attempt
        time.sleep(min(max(delay, 0.0), DEFILLAMA_RETRY_MAX_SECONDS))

    raise RuntimeError("unreachable DefiLlama retry state")


def fetch_defillama_price(chain_id: int, token_address: str, timestamp: int, timeout: int = 30) -> PriceResult:
    key = (int(chain_id), token_address, int(timestamp))
    return fetch_defillama_prices_batch([key], timeout=timeout).get(
        key,
        price_result(None, "missing",
            {"coin": defillama_coin_id(chain_id, token_address), "timestamp": int(timestamp)},
            provider='defillama', requested_timestamp=int(timestamp)),
    )


def fetch_defillama_prices_batch(
    requests_: list[tuple[int, str, int]],
    timeout: int = 30,
) -> PriceBatch:
    coins: dict[str, list[int]] = {}
    key_by_coin: dict[str, tuple[int, str]] = {}
    for chain_id, token_address, timestamp in requests_:
        coin = defillama_coin_id(chain_id, token_address)
        coins.setdefault(coin, []).append(int(timestamp))
        key_by_coin[coin] = (int(chain_id), token_address)
    response = defillama_get(
        f"{DEFILLAMA_BASE}/batchHistorical",
        params={"coins": json.dumps(coins, separators=(",", ":"))},
        timeout=timeout,
    )
    payload = response.json()
    output: dict[tuple[int, str, int], tuple[float | None, str, dict[str, Any]]] = {}
    missing_keys: list[tuple[int, str, int, str]] = []
    for coin, timestamps in coins.items():
        chain_id, token_address = key_by_coin[coin]
        prices = payload.get("coins", {}).get(coin, {}).get("prices") or []
        matched_prices = match_defillama_samples(timestamps, prices)
        for requested_ts in timestamps:
            key = (chain_id, token_address, int(requested_ts))
            matched = matched_prices.get(int(requested_ts))
            if matched is None:
                missing_keys.append((chain_id, token_address, int(requested_ts), coin))
                continue
            output[key] = (
                float(matched["price"]),
                "ok",
                {
                    "coin": coin,
                    "timestamp": int(requested_ts),
                    "matched": matched,
                    "matched_offset_seconds": int(matched["timestamp"]) - int(requested_ts),
                    "maximum_accepted_offset_seconds": DEFILLAMA_SEARCH_WIDTH_SECONDS,
                },
            )
    for chain_id, token_address, timestamp, coin in missing_keys:
        output[(chain_id, token_address, timestamp)] = (None, 'missing', {'coin': coin, 'timestamp': timestamp})
    return PriceBatch({target: price_result(*result, provider='defillama', requested_timestamp=target[2])
        for target, result in output.items()}, requests_, provider='defillama')


def yearn_prices_batches(
    targets: list[tuple[int, str, int]],
    max_tokens: int | None = None,
) -> list[list[tuple[int, str, int]]]:
    if max_tokens is None:
        max_tokens = int(os.environ.get("YEARN_PRICE_BATCH_TOKENS", "20"))
    if max_tokens < 1 or max_tokens > YEARN_PRICES_MAX_TOKEN_KEYS:
        raise ValueError(
            f"YEARN_PRICE_BATCH_TOKENS must be between 1 and {YEARN_PRICES_MAX_TOKEN_KEYS}"
        )

    timestamps_by_token: dict[tuple[int, str], set[int]] = defaultdict(set)
    for chain_id, token_address, timestamp in targets:
        yearn_prices_token_key(chain_id, token_address)
        timestamps_by_token[(int(chain_id), token_address.lower())].add(
            normalize_yearn_price_timestamp(timestamp)
        )

    segments: list[list[tuple[int, str, int]]] = []
    for (chain_id, token_address), timestamps in sorted(timestamps_by_token.items()):
        sorted_timestamps = sorted(timestamps)
        for offset in range(0, len(sorted_timestamps), YEARN_PRICES_MAX_TIMESTAMPS_PER_TOKEN):
            chunk = sorted_timestamps[offset:offset+YEARN_PRICES_MAX_TIMESTAMPS_PER_TOKEN]
            segments.append([(chain_id, token_address, timestamp) for timestamp in chunk])

    batches: list[list[tuple[int, str, int]]] = []
    current: list[tuple[int, str, int]] = []
    current_tokens: set[tuple[int, str]] = set()
    for segment in segments:
        token = (segment[0][0], segment[0][1])
        if current and (token in current_tokens or len(current_tokens) >= max_tokens):
            batches.append(current)
            current = []
            current_tokens = set()
        current.extend(segment)
        current_tokens.add(token)
    if current:
        batches.append(current)
    return batches
