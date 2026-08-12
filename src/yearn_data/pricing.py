"""Historical pricing adapters."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any
import os
import json
from collections import defaultdict
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from eth_abi import decode as abi_decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from .config import CHAINS
from .config import get_rpc_urls
from .storage import to_json
from .multicall import aggregate3_raw, decode_first


DEFILLAMA_BASE = "https://coins.llama.fi"
SUPPORTED_SOURCES = {"defillama"}
ETHEREUM_WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
POLYGON_STABLE_ALIASES = {
    "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",  # bridged USDC
    "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",  # native USDC
    "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",  # bridged USDT
    "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063",  # DAI
}
POLYGON_WETH = "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619"
ARBITRUM_STABLE_ALIASES = {
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",  # bridged USDT / USDt0
}
ETHEREUM_STABLE_ALIASES = {
    "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
    "0x57ab1ec28d129707052df4df418d58a2d46d5f51",  # sUSD
    "0x85e30b8b263bc64d94b827ed450f2edfee8579da",  # USDaf
}
KATANA_USDC_WRAPPERS = {
    "0x203a662b0bd271a6ed5a60edfbd04bfce608fd36",  # vbUSDC
}
KATANA_ETH_WRAPPERS = {
    "0xee7d8bcfb72bc1880d0cf19822eb0a2e6577ab62",  # vbETH
}
ETH_EQUIVALENTS = {
    ETHEREUM_WETH.lower(),
    "0xae7ab96520de3a18e5e111b5eaab095312d7fe84",  # stETH
    "0x5e74c9036fb86bd7ecdcb084a0673efc32ea31cb",  # sETH
    "0x9559aaa82d9649c7a7b220e7c461d2e74c9a3593",  # rETH
    "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
}
BTC_EQUIVALENTS = {
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",  # WBTC
    "0x0316eb71485b0ab14103307bf65a021042c6d380",  # HBTC
    "0x8751d4196027d4e6da63716fa7786b5174f04c15",  # Curve sBTC
}
WBTC = "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
LINK = "0x514910771AF9Ca656af840dff83E8264EcF986CA"
AAVE = "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9"
TOKEN_PRICE_ALIASES = {
    "0xbbc455cb4f1b9e4bfc4b73970d360c8f032efee6": (LINK, "canonical_slink_link_fallback"),  # sLINK
    "0x4da9b813057d04baef4e5800e36083717b4a0341": (AAVE, "canonical_saave_aave_fallback"),  # stkAAVE / saAAVE wrappers
}
CRV = "0xD533a949740bb3306d119CC777fa900bA034cd52"
YVECRV = "0xc5bddf9843308380375a611c18b50fb9341f502a"
YCRV = "0xfcc5c47be19d06bf83eb04298b026f81069ff65b"
SUSHI_WETH_YVECRV_PAIR = "0x10b47177e92ef9d5c6059055d92ddf6290848991"
OLD_CRV_YCRV_POOL = "0x453D92C7d4263201C69aACfaf589Ed14202d83a4"
CURVE_ADDRESS_PROVIDER = "0x0000000022D53366457F9d5E68Ec105046FC4383"
CRV_BASED_CURVE_LPS = {
    "0x99f5acc8ec2da2bc0771c32814eff52b712de1e5",  # yCRV-f
    "0xca0253a98d16e9c1e3614cafda19318ee69772d0",  # sdCRVlp-f
    "0x9d0464996170c6b9e75eed71c68b99ddedf279e8",  # cvxcrv-f
}
CURVE_LP_CHAINS = {1, 137, 8453, 42161}
BALANCER_VAULT = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"
BALANCER_CHAINS = {1, 137, 42161, 8453}


def defillama_coin_id(chain_id: int, token_address: str) -> str:
    cfg = next(c for c in CHAINS.values() if c.chain_id == int(chain_id))
    return f"{cfg.defillama_slug}:{token_address.lower()}"


def fetch_defillama_price(chain_id: int, token_address: str, timestamp: int, timeout: int = 30) -> tuple[float | None, str, dict[str, Any]]:
    coin = defillama_coin_id(chain_id, token_address)
    request_timestamp = int(timestamp)
    for attempt in range(5):
        response = requests.get(
            f"{DEFILLAMA_BASE}/batchHistorical",
            params={"coins": json.dumps({coin: [request_timestamp]}, separators=(",", ":"))},
            timeout=timeout,
        )
        if response.status_code != 429:
            response.raise_for_status()
            break
        if attempt == 4:
            response.raise_for_status()
        time.sleep(2**attempt)
    payload = response.json()
    prices = payload.get("coins", {}).get(coin, {}).get("prices") or []
    if prices:
        matched = min(prices, key=lambda item: abs(int(item["timestamp"]) - request_timestamp))
        if matched.get("price") is not None:
            return float(matched["price"]), "ok", {"coin": coin, "timestamp": request_timestamp, "matched": matched}
    fallback = _canonical_defillama_fallback(chain_id, token_address, request_timestamp, timeout=timeout)
    if fallback is not None:
        return fallback
    return None, "missing", payload


def fetch_defillama_prices_batch(
    requests_: list[tuple[int, str, int]],
    timeout: int = 30,
) -> dict[tuple[int, str, int], tuple[float | None, str, dict[str, Any]]]:
    coins: dict[str, list[int]] = {}
    key_by_coin: dict[str, tuple[int, str]] = {}
    for chain_id, token_address, timestamp in requests_:
        coin = defillama_coin_id(chain_id, token_address)
        coins.setdefault(coin, []).append(int(timestamp))
        key_by_coin[coin] = (int(chain_id), token_address)
    response = requests.get(
        f"{DEFILLAMA_BASE}/batchHistorical",
        params={"coins": json.dumps(coins, separators=(",", ":"))},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    output: dict[tuple[int, str, int], tuple[float | None, str, dict[str, Any]]] = {}
    missing_keys: list[tuple[int, str, int, str]] = []
    for coin, timestamps in coins.items():
        chain_id, token_address = key_by_coin[coin]
        prices = payload.get("coins", {}).get(coin, {}).get("prices") or []
        for requested_ts in timestamps:
            key = (chain_id, token_address, int(requested_ts))
            if not prices:
                missing_keys.append((chain_id, token_address, int(requested_ts), coin))
                continue
            matched = min(prices, key=lambda item: abs(int(item["timestamp"]) - int(requested_ts)))
            price = matched.get("price")
            if price is None:
                missing_keys.append((chain_id, token_address, int(requested_ts), coin))
            else:
                output[key] = (float(price), "ok", {"coin": coin, "timestamp": int(requested_ts), "matched": matched})
    output.update(_canonical_defillama_fallbacks_batch(missing_keys, timeout=timeout))
    return output


def _canonical_defillama_fallback(
    chain_id: int,
    token_address: str,
    timestamp: int,
    timeout: int = 30,
) -> tuple[float | None, str, dict[str, Any]] | None:
    address = token_address.lower()
    coin = defillama_coin_id(chain_id, token_address)
    stable_source = _canonical_stable_source(chain_id, address)
    if stable_source:
        return 1.0, "ok", {"coin": coin, "timestamp": int(timestamp), "source": stable_source}
    eth_source = _canonical_eth_source(chain_id, address)
    if eth_source:
        eth_coin = defillama_coin_id(1, ETHEREUM_WETH)
        url = f"{DEFILLAMA_BASE}/prices/historical/{int(timestamp)}/{eth_coin}"
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        entry = payload.get("coins", {}).get(eth_coin)
        if entry and entry.get("price") is not None:
            return (
                float(entry["price"]),
                "ok",
                {
                    "coin": coin,
                    "timestamp": int(timestamp),
                    "source": eth_source,
                    "fallback_coin": eth_coin,
                    "fallback_payload": payload,
                },
            )
    return None


def _canonical_defillama_fallbacks_batch(
    missing_keys: list[tuple[int, str, int, str]],
    timeout: int = 30,
) -> dict[tuple[int, str, int], tuple[float | None, str, dict[str, Any]]]:
    output: dict[tuple[int, str, int], tuple[float | None, str, dict[str, Any]]] = {}
    weth_timestamps: list[int] = []
    eth_keys: list[tuple[int, str, int, str, str]] = []
    for chain_id, token_address, timestamp, coin in missing_keys:
        key = (chain_id, token_address, timestamp)
        address = token_address.lower()
        stable_source = _canonical_stable_source(chain_id, address)
        eth_source = _canonical_eth_source(chain_id, address)
        if stable_source:
            output[key] = (1.0, "ok", {"coin": coin, "timestamp": timestamp, "source": stable_source})
        elif eth_source:
            weth_timestamps.append(timestamp)
            eth_keys.append((chain_id, token_address, timestamp, coin, eth_source))
        else:
            output[key] = (None, "missing", {"coin": coin, "timestamp": timestamp})

    if weth_timestamps:
        eth_coin = defillama_coin_id(1, ETHEREUM_WETH)
        response = requests.get(
            f"{DEFILLAMA_BASE}/batchHistorical",
            params={"coins": json.dumps({eth_coin: sorted(set(weth_timestamps))}, separators=(",", ":"))},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        prices = payload.get("coins", {}).get(eth_coin, {}).get("prices") or []
        for chain_id, token_address, timestamp, coin, eth_source in eth_keys:
            key = (chain_id, token_address, timestamp)
            if not prices:
                output[key] = (None, "missing", {"coin": coin, "timestamp": timestamp, "fallback_coin": eth_coin})
                continue
            matched = min(prices, key=lambda item: abs(int(item["timestamp"]) - int(timestamp)))
            price = matched.get("price")
            if price is None:
                output[key] = (None, "missing", {"coin": coin, "timestamp": timestamp, "fallback_coin": eth_coin, "matched": matched})
            else:
                output[key] = (
                    float(price),
                    "ok",
                    {
                        "coin": coin,
                        "timestamp": timestamp,
                        "source": eth_source,
                        "fallback_coin": eth_coin,
                        "matched": matched,
                    },
                )
    return output


def _canonical_stable_source(chain_id: int, address: str) -> str | None:
    chain_id = int(chain_id)
    if chain_id == 137 and address in POLYGON_STABLE_ALIASES:
        return "canonical_polygon_stable_fallback"
    if chain_id == 42161 and address in ARBITRUM_STABLE_ALIASES:
        return "canonical_arbitrum_stable_fallback"
    if chain_id == 1 and address in ETHEREUM_STABLE_ALIASES:
        return "canonical_ethereum_stable_fallback"
    if chain_id == 747474 and address in KATANA_USDC_WRAPPERS:
        return "canonical_katana_usdc_wrapper_fallback"
    return None


def _canonical_eth_source(chain_id: int, address: str) -> str | None:
    chain_id = int(chain_id)
    if chain_id == 137 and address == POLYGON_WETH:
        return "canonical_polygon_weth_fallback"
    if chain_id == 747474 and address in KATANA_ETH_WRAPPERS:
        return "canonical_katana_eth_wrapper_fallback"
    return None


def report_amount(raw_value: str | int, decimals: int | None) -> Decimal:
    scale = Decimal(10) ** int(decimals or 18)
    return Decimal(int(raw_value)) / scale


def _selector(signature: str) -> str:
    return "0x" + function_signature_to_4byte_selector(signature).hex()


def _eth_call(chain_id: int, address: str, data: str, block_number: int, timeout: int = 30) -> str | None:
    chain = next(c.key for c in CHAINS.values() if c.chain_id == int(chain_id))
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": Web3.to_checksum_address(address), "data": data}, hex(int(block_number))],
    }
    urls = get_rpc_urls(chain)
    last_error: Exception | None = None
    for attempt in range(4):
        if attempt:
            time.sleep(min(0.75 * attempt, 3.0))
        url = urls[min(attempt, len(urls) - 1)]
        try:
            response = requests.post(url, json=payload, timeout=timeout)
            if response.status_code in (408, 429) or response.status_code >= 500:
                last_error = requests.HTTPError(f"HTTP {response.status_code} from {url}")
                continue
            response.raise_for_status()
            body = response.json()
            result = body.get("result")
            if not isinstance(result, str) or result == "0x":
                return None
            return result
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            continue
    raise last_error if last_error else RuntimeError(f"eth_call failed for {address}")


NATIVE_ETH_SENTINEL = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"


def _native_eth_balance(chain_id: int, address: str, block_number: int) -> int | None:
    """Native ETH balance of a contract at a block, for Curve native-ETH pools."""
    chain = _chain_key(chain_id)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getBalance",
        "params": [Web3.to_checksum_address(address), hex(int(block_number))],
    }
    urls = get_rpc_urls(chain)
    for url in urls:
        try:
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            result = response.json().get("result")
            if isinstance(result, str) and result.startswith("0x"):
                return int(result, 16)
        except (requests.RequestException, ValueError):
            continue
    return None


def _call_uint256(chain_id: int, address: str, signature: str, block_number: int, args_hex: str = "") -> int | None:
    try:
        result = _eth_call(chain_id, address, _selector(signature) + args_hex, block_number)
        if not result:
            return None
        return int(abi_decode(["uint256"], bytes.fromhex(result[2:]))[0])
    except Exception:
        return None


def _call_address(chain_id: int, address: str, signature: str, block_number: int, args_hex: str = "") -> str | None:
    try:
        result = _eth_call(chain_id, address, _selector(signature) + args_hex, block_number)
        if not result:
            return None
        decoded = abi_decode(["address"], bytes.fromhex(result[2:]))[0]
        if decoded == ZERO_ADDRESS:
            return None
        return Web3.to_checksum_address(decoded)
    except Exception:
        return None


def _uint_arg(value: int) -> str:
    return int(value).to_bytes(32, byteorder="big").hex()


@lru_cache(maxsize=100_000)
def _curve_pool_from_registry(chain_id: int, lp_token: str, block_number: int) -> str | None:
    if int(chain_id) != 1:
        return None
    lp = Web3.to_checksum_address(lp_token)
    for registry_id in range(10):
        registry = _call_address(chain_id, CURVE_ADDRESS_PROVIDER, "get_address(uint256)", block_number, _uint_arg(registry_id))
        if not registry:
            continue
        pool = _call_address(chain_id, registry, "get_pool_from_lp_token(address)", block_number, lp[2:].rjust(64, "0"))
        if pool:
            return pool
    return None


def _exchange_rate_wrapped_price(
    chain_id: int,
    token_address: str,
    timestamp: int,
    block_number: int,
) -> tuple[float | None, dict[str, Any]]:
    underlying = _call_address(chain_id, token_address, "underlying()", block_number)
    if not underlying:
        return None, {"source": "exchange_rate_wrapper_fallback", "status": "missing_underlying"}
    exchange_rate = _call_uint256(chain_id, token_address, "exchangeRateStored()", block_number)
    if exchange_rate is None:
        return None, {"source": "exchange_rate_wrapper_fallback", "status": "missing_exchange_rate", "underlying": underlying}
    token_decimals = _call_uint256(chain_id, token_address, "decimals()", block_number)
    underlying_decimals = _call_uint256(chain_id, underlying, "decimals()", block_number)
    if token_decimals is None or underlying_decimals is None:
        return None, {"source": "exchange_rate_wrapper_fallback", "status": "missing_decimals", "underlying": underlying}
    underlying_price, underlying_payload = _direct_or_alias_price(chain_id, underlying, timestamp, block_number)
    if underlying_price is None:
        return None, {"source": "exchange_rate_wrapper_fallback", "status": "missing_underlying_price", "underlying": underlying}
    scale = Decimal(10) ** (18 + int(underlying_decimals) - int(token_decimals))
    return (
        float((Decimal(int(exchange_rate)) / scale) * Decimal(str(underlying_price))),
        {
            "source": "exchange_rate_wrapper_fallback",
            "underlying": underlying,
            "exchange_rate": str(exchange_rate),
            "token_decimals": int(token_decimals),
            "underlying_decimals": int(underlying_decimals),
            "underlying_payload": underlying_payload,
        },
    )

def _yearn_v1_vault_price(
    chain_id: int,
    token_address: str,
    timestamp: int,
    block_number: int,
) -> tuple[float | None, dict[str, Any]]:
    """Value a V1 yToken from its historical underlying and share price."""
    underlying = _call_address(chain_id, token_address, "token()", block_number)
    if not underlying:
        return None, {"source": "yearn_v1_vault_fallback", "status": "missing_underlying"}
    price_per_share = _call_uint256(chain_id, token_address, "getPricePerFullShare()", block_number)
    if price_per_share is None:
        return None, {
            "source": "yearn_v1_vault_fallback",
            "status": "missing_price_per_share",
            "underlying": underlying,
        }
    underlying_price, underlying_payload = _direct_or_alias_price(chain_id, underlying, timestamp, block_number)
    if underlying_price is None:
        return None, {
            "source": "yearn_v1_vault_fallback",
            "status": "missing_underlying_price",
            "underlying": underlying,
        }
    return (
        float((Decimal(int(price_per_share)) / Decimal(10**18)) * Decimal(str(underlying_price))),
        {
            "source": "yearn_v1_vault_fallback",
            "underlying": underlying,
            "price_per_share": str(price_per_share),
            "underlying_payload": underlying_payload,
        },
    )




def _aave_atoken_price(
    chain_id: int,
    token_address: str,
    timestamp: int,
    block_number: int,
) -> tuple[float | None, dict[str, Any]]:
    underlying = _call_address(chain_id, token_address, "UNDERLYING_ASSET_ADDRESS()", block_number)
    if not underlying:
        return None, {"source": "aave_atoken_fallback", "status": "missing_underlying"}
    underlying_price, underlying_payload = _direct_or_alias_price(chain_id, underlying, timestamp, block_number)
    if underlying_price is None:
        return None, {"source": "aave_atoken_fallback", "status": "missing_underlying_price", "underlying": underlying}
    return (
        underlying_price,
        {
            "source": "aave_atoken_fallback",
            "underlying": underlying,
            "underlying_payload": underlying_payload,
        },
    )


@lru_cache(maxsize=200_000)
def _direct_or_alias_price(chain_id: int, token_address: str, timestamp: int, block_number: int) -> tuple[float | None, dict[str, Any]]:
    address = token_address.lower()
    stable_source = _canonical_stable_source(chain_id, address)
    if stable_source:
        return 1.0, {"source": stable_source}
    if int(chain_id) == 1 and address in TOKEN_PRICE_ALIASES:
        base_token, alias_source = TOKEN_PRICE_ALIASES[address]
        price, payload = _direct_or_alias_price(chain_id, base_token, timestamp, block_number)
        return price, {"source": alias_source, "base_token": base_token, "base_payload": payload}
    eth_source = _canonical_eth_source(chain_id, address)
    if eth_source or (int(chain_id) == 1 and address in ETH_EQUIVALENTS):
        price, status, payload = fetch_defillama_price(1, ETHEREUM_WETH, timestamp)
        return (price if status == "ok" else None), {"source": eth_source or "canonical_eth_equivalent_fallback", "payload": payload}
    if int(chain_id) == 1 and address in BTC_EQUIVALENTS:
        price, status, payload = fetch_defillama_price(1, WBTC, timestamp)
        return (price if status == "ok" else None), {"source": "canonical_btc_equivalent_fallback", "payload": payload}
    price, status, payload = fetch_defillama_price(chain_id, token_address, timestamp)
    if status == "ok":
        return price, {"source": "defillama_underlying", "payload": payload}
    if int(chain_id) == 1:
        wrapped_price, wrapped_payload = _exchange_rate_wrapped_price(chain_id, token_address, timestamp, block_number)
        if wrapped_price is not None:
            return wrapped_price, wrapped_payload
        atoken_price, atoken_payload = _aave_atoken_price(chain_id, token_address, timestamp, block_number)
        if atoken_price is not None:
            return atoken_price, atoken_payload
        yearn_v1_price, yearn_v1_payload = _yearn_v1_vault_price(chain_id, token_address, timestamp, block_number)
        if yearn_v1_price is not None:
            return yearn_v1_price, yearn_v1_payload
    return None, {"source": "missing_underlying", "payload": payload}


def _chain_key(chain_id: int) -> str:
    return next(c.key for c in CHAINS.values() if c.chain_id == int(chain_id))


@lru_cache(maxsize=100_000)
def _curve_pool_state_multicall(
    chain_id: int,
    lp_token: str,
    block_number: int,
) -> tuple[str, int, int, tuple[tuple[str, int, int], ...]] | None:
    """Fetch Curve LP + pool state in a few Multicall3 round-trips.

    Returns (pool, token_decimals, total_supply, coins) where coins carries
    (address, balance, decimals) per non-empty pool slot, or None when the
    chain/block predates Multicall3 or the endpoint fails (callers fall back
    to sequential eth_calls).
    """
    chain = _chain_key(chain_id)
    lp = Web3.to_checksum_address(lp_token)
    try:
        head = aggregate3_raw(
            chain,
            [
                (lp, _selector("minter()")),
                (lp, _selector("decimals()")),
                (lp, _selector("totalSupply()")),
            ],
            block_number=block_number,
        )
    except Exception:
        return None
    minter = decode_first("address", head[0])
    token_decimals = decode_first("uint256", head[1])
    total_supply = decode_first("uint256", head[2])
    if token_decimals is None or not total_supply:
        return None
    pool = minter or _curve_pool_from_registry(chain_id, lp_token, block_number) or lp

    try:
        pool_calls: list[tuple[str, str]] = []
        for idx in range(8):
            pool_calls.append((pool, _selector("coins(uint256)") + _uint_arg(idx)))
        for idx in range(8):
            pool_calls.append((pool, _selector("coins(int128)") + _uint_arg(idx)))
        for idx in range(8):
            pool_calls.append((pool, _selector("balances(uint256)") + _uint_arg(idx)))
        for idx in range(8):
            pool_calls.append((pool, _selector("balances(int128)") + _uint_arg(idx)))
        pool_res = aggregate3_raw(chain, pool_calls, block_number=block_number)
    except Exception:
        return None

    coins: list[tuple[str, int]] = []
    for idx in range(8):
        coin = decode_first("address", pool_res[idx]) or decode_first("address", pool_res[8 + idx])
        if not coin:
            break
        balance = _decode_uint(pool_res[16 + idx]) or _decode_uint(pool_res[24 + idx])
        coins.append((Web3.to_checksum_address(coin), int(balance or 0)))
    if not coins:
        return None

    try:
        decimal_res = aggregate3_raw(
            chain,
            [(coin, _selector("decimals()")) for coin, _balance in coins],
            block_number=block_number,
        )
    except Exception:
        return None
    out: list[tuple[str, int, int]] = []
    for (coin, balance), dec_result in zip(coins, decimal_res):
        decimals = _decode_uint(dec_result)
        out.append((coin, balance, int(decimals if decimals is not None else 18)))
    return pool, int(token_decimals), int(total_supply), tuple(out)


def _decode_uint(result: tuple[bool, bytes]) -> int | None:
    return decode_first("uint256", result)

@lru_cache(maxsize=200_000)
def _curve_lp_price(
    chain_id: int,
    token_address: str,
    timestamp: int,
    block_number: int,
    depth: int = 2,
) -> tuple[float | None, dict[str, Any]]:
    """Value a Curve LP token from its exact historical reserves.

    Fails closed: if any non-zero pool reserve cannot be priced, the LP stays
    unpriced and the payload records the missing reserve, so partial sums can
    never silently undervalue the token.
    """
    if int(chain_id) not in CURVE_LP_CHAINS or depth < 0:
        return None, {"source": "curve_lp_fallback", "status": "unsupported"}

    token = Web3.to_checksum_address(token_address)
    state = _curve_pool_state_multicall(chain_id, token_address, block_number)
    if state is not None:
        pool, token_decimals, total_supply, pool_coins = state
        coin_rows: list[tuple[str, int | None, int]] = [
            (coin, balance, decimals) for coin, balance, decimals in pool_coins
        ]
    else:
        pool = (
            _call_address(chain_id, token, "minter()", block_number)
            or _curve_pool_from_registry(chain_id, token, block_number)
            or token
        )
        token_decimals = _call_uint256(chain_id, token, "decimals()", block_number)
        total_supply = _call_uint256(chain_id, token, "totalSupply()", block_number)
        if token_decimals is None or not total_supply:
            return None, {"source": "curve_lp_fallback", "status": "missing_supply", "pool": pool}
        coin_rows = []
        for idx in range(8):
            coin = (
                _call_address(chain_id, pool, "coins(uint256)", block_number, _uint_arg(idx))
                or _call_address(chain_id, pool, "coins(int128)", block_number, _uint_arg(idx))
            )
            if not coin:
                break
            balance = _call_uint256(chain_id, pool, "balances(uint256)", block_number, _uint_arg(idx))
            if balance is None:
                balance = _call_uint256(chain_id, pool, "balances(int128)", block_number, _uint_arg(idx))
            decimals = _call_uint256(chain_id, coin, "decimals()", block_number) or 18
            coin_rows.append((coin, balance, int(decimals)))

    coins: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for coin, balance, decimals in coin_rows:
        if coin.lower() == NATIVE_ETH_SENTINEL and not balance:
            balance = _native_eth_balance(chain_id, pool, block_number)
        if balance is None:
            missing.append({"coin": coin, "balance": None, "reason": "missing_balance"})
            continue
        if balance == 0:
            continue
        price, price_payload = _resolve_reserve_price(chain_id, coin, timestamp, block_number, depth - 1)
        if price is None:
            missing.append(
                {
                    "coin": coin,
                    "balance": str(balance),
                    "decimals": int(decimals),
                    "reason": "missing_price",
                    "price_payload": price_payload,
                }
            )
            continue
        amount = Decimal(int(balance)) / (Decimal(10) ** int(decimals))
        coins.append(
            {
                "coin": coin,
                "balance": str(balance),
                "decimals": int(decimals),
                "price": float(price),
                "value_usd": str(amount * Decimal(str(price))),
                "price_payload": price_payload,
            }
        )

    if missing:
        return None, {
            "source": "curve_lp_fallback",
            "status": "missing_reserve_price",
            "pool": pool,
            "block_number": int(block_number),
            "missing_reserves": missing,
            "priced_coins": coins,
        }
    if not coins:
        return None, {"source": "curve_lp_fallback", "status": "no_valued_reserves", "pool": pool}

    tvl = sum(Decimal(item["value_usd"]) for item in coins)
    supply = Decimal(int(total_supply)) / (Decimal(10) ** int(token_decimals))
    if supply > 0 and tvl > 0:
        return (
            float(tvl / supply),
            {
                "source": "curve_lp_balance_fallback",
                "pool": pool,
                "token": token,
                "total_supply": str(total_supply),
                "token_decimals": int(token_decimals),
                "coins": coins,
            },
        )

def _erc4626_wrapper_price(
    chain_id: int,
    token_address: str,
    timestamp: int,
    block_number: int,
    depth: int = 2,
) -> tuple[float | None, dict[str, Any]]:
    """Value share/wrapper tokens from their historical assets-per-share rate.

    Covers ERC-4626 vaults (asset()/convertToAssets) and fixed-rate wrappers
    such as Ether.fi weETH (getRate) on any chain. Pendle PTs are excluded:
    their redemption rate is a maturity value, not a market price.
    """
    if depth < 0:
        return None, {"source": "erc4626_wrapper_fallback", "status": "unsupported"}
    token = Web3.to_checksum_address(token_address)
    if _call_address(chain_id, token, "SY()", block_number):
        return None, {"source": "erc4626_wrapper_fallback", "status": "pendle_principal_token_excluded"}
    token_decimals = _call_uint256(chain_id, token, "decimals()", block_number)
    if token_decimals is None:
        return None, {"source": "erc4626_wrapper_fallback", "status": "missing_decimals"}
    underlying = (
        _call_address(chain_id, token, "asset()", block_number)
        or _call_address(chain_id, token, "underlying()", block_number)
        or _call_address(chain_id, token, "token()", block_number)
    )
    if not underlying or underlying.lower() == token_address.lower():
        return None, {"source": "erc4626_wrapper_fallback", "status": "missing_underlying"}
    underlying_decimals = _call_uint256(chain_id, underlying, "decimals()", block_number)
    if underlying_decimals is None:
        return None, {
            "source": "erc4626_wrapper_fallback",
            "status": "missing_underlying_decimals",
            "underlying": underlying,
        }
    rate: int | None = None
    rate_scale: Decimal | None = None
    rate_method: str | None = None
    one_share = 10 ** int(token_decimals)
    assets = _call_uint256(chain_id, token, "convertToAssets(uint256)", block_number, _uint_arg(one_share))
    if assets is not None:
        rate, rate_scale, rate_method = assets, Decimal(10) ** int(underlying_decimals), "convertToAssets(uint256)"
    if rate is None:
        raw_rate = _call_uint256(chain_id, token, "getRate()", block_number)
        if raw_rate is not None and int(token_decimals) == 18 and int(underlying_decimals) == 18:
            rate, rate_scale, rate_method = raw_rate, Decimal(10**18), "getRate()"
    if rate is None:
        return None, {
            "source": "erc4626_wrapper_fallback",
            "status": "missing_rate",
            "underlying": underlying,
        }
    underlying_price, underlying_payload = _resolve_reserve_price(
        chain_id, underlying, timestamp, block_number, depth - 1
    )
    if underlying_price is None:
        return None, {
            "source": "erc4626_wrapper_fallback",
            "status": "missing_underlying_price",
            "underlying": underlying,
            "underlying_payload": underlying_payload,
        }
    assets_per_share = Decimal(int(rate)) / rate_scale
    return (
        float(assets_per_share * Decimal(str(underlying_price))),
        {
            "source": "erc4626_wrapper_fallback",
            "underlying": underlying,
            "rate_method": rate_method,
            "rate": str(rate),
            "assets_per_share": format(assets_per_share, "f"),
            "token_decimals": int(token_decimals),
            "underlying_decimals": int(underlying_decimals),
            "underlying_payload": underlying_payload,
        },
    )


def _balancer_bpt_price(
    chain_id: int,
    token_address: str,
    timestamp: int,
    block_number: int,
    depth: int = 2,
) -> tuple[float | None, dict[str, Any]]:
    """Value a Balancer BPT from Vault reserves / BPT supply at the block.

    Fails closed when any non-zero reserve (other than the pool's own phantom
    BPT balance) cannot be priced.
    """
    if int(chain_id) not in BALANCER_CHAINS or depth < 0:
        return None, {"source": "balancer_bpt_fallback", "status": "unsupported"}
    token = Web3.to_checksum_address(token_address)
    try:
        pool_id_hex = _eth_call(chain_id, token, _selector("getPoolId()"), block_number)
    except Exception as exc:
        return None, {"source": "balancer_bpt_fallback", "status": "error", "error": str(exc)}
    if not pool_id_hex:
        return None, {"source": "balancer_bpt_fallback", "status": "missing_pool_id"}
    try:
        result = _eth_call(
            chain_id,
            BALANCER_VAULT,
            _selector("getPoolTokens(bytes32)") + pool_id_hex[2:].zfill(64),
            block_number,
        )
        if not result:
            return None, {"source": "balancer_bpt_fallback", "status": "missing_pool_tokens"}
        tokens_, balances_, _last_changed = abi_decode(
            ["address[]", "uint256[]", "uint256"], bytes.fromhex(result[2:])
        )
    except Exception as exc:
        return None, {"source": "balancer_bpt_fallback", "status": "error", "error": str(exc)}
    token_decimals = _call_uint256(chain_id, token, "decimals()", block_number)
    total_supply = _call_uint256(chain_id, token, "totalSupply()", block_number)
    if token_decimals is None or not total_supply:
        return None, {"source": "balancer_bpt_fallback", "status": "missing_supply", "pool_id": pool_id_hex}

    value = Decimal(0)
    reserves: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for reserve, balance in zip(tokens_, balances_):
        if balance == 0:
            continue
        if reserve.lower() == token_address.lower():
            reserves.append({"coin": reserve, "balance": str(balance), "skipped": "phantom_bpt"})
            continue
        price, price_payload = _resolve_reserve_price(chain_id, reserve, timestamp, block_number, depth - 1)
        if price is None:
            missing.append(
                {
                    "coin": reserve,
                    "balance": str(balance),
                    "reason": "missing_price",
                    "price_payload": price_payload,
                }
            )
            continue
        decimals = _call_uint256(chain_id, reserve, "decimals()", block_number) or 18
        amount = Decimal(int(balance)) / (Decimal(10) ** int(decimals))
        value += amount * Decimal(str(price))
        reserves.append(
            {
                "coin": reserve,
                "balance": str(balance),
                "decimals": int(decimals),
                "price": float(price),
                "value_usd": str(amount * Decimal(str(price))),
                "price_payload": price_payload,
            }
        )
    if missing:
        return None, {
            "source": "balancer_bpt_fallback",
            "status": "missing_reserve_price",
            "pool_id": pool_id_hex,
            "block_number": int(block_number),
            "missing_reserves": missing,
            "priced_reserves": reserves,
        }
    if not reserves:
        return None, {"source": "balancer_bpt_fallback", "status": "no_valued_reserves", "pool_id": pool_id_hex}
    supply = Decimal(int(total_supply)) / (Decimal(10) ** int(token_decimals))
    if supply > 0 and value > 0:
        return (
            float(value / supply),
            {
                "source": "balancer_bpt_fallback",
                "pool_id": pool_id_hex,
                "token": token,
                "total_supply": str(total_supply),
                "token_decimals": int(token_decimals),
                "reserves": reserves,
            },
        )
    return None, {"source": "balancer_bpt_fallback", "status": "zero_valued_pool", "pool_id": pool_id_hex}


def _amm_pair_lp_price(
    chain_id: int,
    pair_address: str,
    timestamp: int,
    block_number: int,
    depth: int = 2,
) -> tuple[float | None, dict[str, Any]]:
    """Value a constant-product pair LP (Uniswap-V2 forks, Aerodrome) from
    historical reserves / LP supply at the block. Fails closed when either
    non-zero side cannot be priced."""
    if depth < 0:
        return None, {"source": "amm_pair_lp_fallback", "status": "unsupported"}
    pair = Web3.to_checksum_address(pair_address)
    token0 = _call_address(chain_id, pair, "token0()", block_number)
    token1 = _call_address(chain_id, pair, "token1()", block_number)
    if not token0 or not token1 or token0.lower() == token1.lower():
        return None, {"source": "amm_pair_lp_fallback", "status": "missing_tokens", "pair": pair}
    try:
        result = _eth_call(chain_id, pair, _selector("getReserves()"), block_number)
        if not result:
            return None, {"source": "amm_pair_lp_fallback", "status": "missing_reserves", "pair": pair}
        reserve0, reserve1, _block_ts = abi_decode(["uint112", "uint112", "uint32"], bytes.fromhex(result[2:]))
    except Exception as exc:
        return None, {"source": "amm_pair_lp_fallback", "status": "error", "pair": pair, "error": str(exc)}
    pair_decimals = _call_uint256(chain_id, pair, "decimals()", block_number)
    total_supply = _call_uint256(chain_id, pair, "totalSupply()", block_number)
    if pair_decimals is None or not total_supply:
        return None, {"source": "amm_pair_lp_fallback", "status": "missing_supply", "pair": pair}

    value = Decimal(0)
    reserves: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for token, reserve in ((token0, reserve0), (token1, reserve1)):
        if reserve == 0:
            continue
        price, price_payload = _resolve_reserve_price(chain_id, token, timestamp, block_number, depth - 1)
        if price is None:
            missing.append(
                {
                    "coin": token,
                    "balance": str(reserve),
                    "reason": "missing_price",
                    "price_payload": price_payload,
                }
            )
            continue
        decimals = _call_uint256(chain_id, token, "decimals()", block_number) or 18
        amount = Decimal(int(reserve)) / (Decimal(10) ** int(decimals))
        value += amount * Decimal(str(price))
        reserves.append(
            {
                "coin": token,
                "balance": str(reserve),
                "decimals": int(decimals),
                "price": float(price),
                "value_usd": str(amount * Decimal(str(price))),
                "price_payload": price_payload,
            }
        )
    if missing:
        return None, {
            "source": "amm_pair_lp_fallback",
            "status": "missing_reserve_price",
            "pair": pair,
            "block_number": int(block_number),
            "missing_reserves": missing,
            "priced_reserves": reserves,
        }
    if not reserves:
        return None, {"source": "amm_pair_lp_fallback", "status": "no_valued_reserves", "pair": pair}
    supply = Decimal(int(total_supply)) / (Decimal(10) ** int(pair_decimals))
    if supply > 0 and value > 0:
        return (
            float(value / supply),
            {
                "source": "amm_pair_lp_fallback",
                "pair": pair,
                "token0": token0,
                "token1": token1,
                "total_supply": str(total_supply),
                "token_decimals": int(pair_decimals),
                "reserves": reserves,
            },
        )
    return None, {"source": "amm_pair_lp_fallback", "status": "zero_valued_pool", "pair": pair}


def _resolve_reserve_price(
    chain_id: int,
    token_address: str,
    timestamp: int,
    block_number: int,
    depth: int,
) -> tuple[float | None, dict[str, Any]]:
    """Resolve a reserve token price: direct DefiLlama/alias paths first, then
    the on-chain adapters with a depth budget so nested LP/wrapper structures
    terminate and cycles cannot recurse."""
    if depth < 0:
        return None, {"source": "reserve_resolver", "status": "depth_exhausted", "token": token_address}
    price, payload = _direct_or_alias_price(chain_id, token_address, timestamp, block_number)
    if price is not None:
        return price, payload
    attempts: list[Any] = []
    for adapter in (_erc4626_wrapper_price, _curve_lp_price, _balancer_bpt_price, _amm_pair_lp_price):
        try:
            price, payload = adapter(chain_id, token_address, timestamp, block_number, depth)
        except Exception as exc:
            attempts.append({"adapter": adapter.__name__, "error": str(exc)})
            continue
        if price is not None:
            return price, payload
        attempts.append(payload)
    return None, {"source": "reserve_resolver", "status": "unresolved", "attempts": attempts}


def _uniswap_v2_pair_price(
    chain_id: int,
    pair: str,
    token: str,
    base_token: str,
    timestamp: int,
    block_number: int,
) -> tuple[float | None, dict[str, Any]]:
    token0 = _call_address(chain_id, pair, "token0()", block_number)
    token1 = _call_address(chain_id, pair, "token1()", block_number)
    try:
        result = _eth_call(chain_id, pair, _selector("getReserves()"), block_number)
        if not result:
            return None, {"source": "uniswap_v2_pair_fallback", "status": "missing_reserves", "pair": pair}
        reserve0, reserve1, _ = abi_decode(["uint112", "uint112", "uint32"], bytes.fromhex(result[2:]))
    except Exception as exc:
        return None, {"source": "uniswap_v2_pair_fallback", "status": "error", "pair": pair, "error": str(exc)}
    if not token0 or not token1 or not reserve0 or not reserve1:
        return None, {"source": "uniswap_v2_pair_fallback", "status": "empty_reserves", "pair": pair}
    base_price, base_payload = _direct_or_alias_price(chain_id, base_token, timestamp, block_number)
    if base_price is None:
        return None, {"source": "uniswap_v2_pair_fallback", "status": "missing_base_price", "pair": pair}
    if token0.lower() == base_token.lower() and token1.lower() == token.lower():
        ratio = Decimal(int(reserve0)) / Decimal(int(reserve1))
    elif token1.lower() == base_token.lower() and token0.lower() == token.lower():
        ratio = Decimal(int(reserve1)) / Decimal(int(reserve0))
    else:
        return None, {"source": "uniswap_v2_pair_fallback", "status": "pair_mismatch", "pair": pair}
    return (
        float(ratio * Decimal(str(base_price))),
        {
            "source": "uniswap_v2_pair_fallback",
            "pair": pair,
            "base_token": base_token,
            "base_payload": base_payload,
            "reserve0": str(reserve0),
            "reserve1": str(reserve1),
            "token0": token0,
            "token1": token1,
        },
    )


def _curve_get_dy_price(
    chain_id: int,
    pool: str,
    token_index: int,
    base_index: int,
    base_token: str,
    timestamp: int,
    block_number: int,
) -> tuple[float | None, dict[str, Any]]:
    args = _uint_arg(token_index) + _uint_arg(base_index) + _uint_arg(10**18)
    amount = _call_uint256(chain_id, pool, "get_dy(int128,int128,uint256)", block_number, args)
    if amount is None:
        amount = _call_uint256(chain_id, pool, "get_dy(uint256,uint256,uint256)", block_number, args)
    if amount is None:
        return None, {"source": "curve_get_dy_fallback", "status": "missing_get_dy", "pool": pool}
    base_price, base_payload = _direct_or_alias_price(chain_id, base_token, timestamp, block_number)
    if base_price is None:
        return None, {"source": "curve_get_dy_fallback", "status": "missing_base_price", "pool": pool}
    price = (Decimal(int(amount)) / Decimal(10**18)) * Decimal(str(base_price))
    return (
        float(price),
        {
            "source": "curve_get_dy_fallback",
            "pool": pool,
            "amount_out": str(amount),
            "base_token": base_token,
            "base_payload": base_payload,
        },
    )


def _crv_derivative_price(chain_id: int, token_address: str, timestamp: int, block_number: int) -> tuple[float | None, dict[str, Any]]:
    address = token_address.lower()
    if int(chain_id) != 1:
        return None, {"source": "crv_derivative_fallback", "status": "unsupported_chain"}
    if address == YVECRV:
        return _uniswap_v2_pair_price(chain_id, SUSHI_WETH_YVECRV_PAIR, token_address, ETHEREUM_WETH, timestamp, block_number)
    if address == YCRV:
        price, payload = _curve_get_dy_price(chain_id, OLD_CRV_YCRV_POOL, 1, 0, CRV, timestamp, block_number)
        if price is not None:
            return price, payload
        crv_price, crv_payload = _direct_or_alias_price(chain_id, CRV, timestamp, block_number)
        if crv_price is not None:
            return (
                crv_price,
                {
                    "source": "ycrv_crv_par_fallback",
                    "reason": "CRV/yCRV pool unavailable at block",
                    "crv_payload": crv_payload,
                    "pool_payload": payload,
                },
            )
    if address in CRV_BASED_CURVE_LPS:
        virtual_price = _call_uint256(chain_id, token_address, "get_virtual_price()", block_number)
        crv_price, crv_payload = _direct_or_alias_price(chain_id, CRV, timestamp, block_number)
        if virtual_price and crv_price is not None:
            return (
                float((Decimal(int(virtual_price)) / Decimal(10**18)) * Decimal(str(crv_price))),
                {
                    "source": "crv_derivative_curve_lp_fallback",
                    "virtual_price": str(virtual_price),
                    "base_token": CRV,
                    "base_payload": crv_payload,
                },
            )
    return None, {"source": "crv_derivative_fallback", "status": "missing"}


def _row_level_fallback_price(chain_id: int, token_address: str, timestamp: int, block_number: int) -> tuple[float | None, str, dict[str, Any]]:
    canonical = _canonical_defillama_fallback(chain_id, token_address, timestamp)
    if canonical is not None:
        price, status, payload = canonical
        return price, status, payload
    price, payload = _yearn_v1_vault_price(chain_id, token_address, timestamp, block_number)
    if price is not None:
        return price, "ok", payload
    price, payload = _crv_derivative_price(chain_id, token_address, timestamp, block_number)
    if price is not None:
        return price, "ok", payload
    attempts: list[Any] = []
    for adapter in (_erc4626_wrapper_price, _curve_lp_price, _balancer_bpt_price, _amm_pair_lp_price):
        try:
            price, payload = adapter(chain_id, token_address, timestamp, block_number)
        except Exception as exc:
            attempts.append({"adapter": adapter.__name__, "error": str(exc)})
            continue
        if price is not None:
            return price, "ok", payload
        attempts.append(payload)
    return None, "missing", {"source": "row_fallback", "status": "unresolved", "attempts": attempts}

def _safe_row_level_fallback_price(
    chain_id: int,
    token_address: str,
    timestamp: int,
    block_number: int,
) -> tuple[float | None, str, dict[str, Any]]:
    try:
        return _row_level_fallback_price(chain_id, token_address, timestamp, block_number)
    except requests.RequestException as exc:
        return None, "missing", {"source": "onchain_fallback", "status": "request_error", "error": str(exc)}
    except Exception as exc:
        return None, "missing", {"source": "onchain_fallback", "status": "error", "error": str(exc)}




def _fetch_price(source: str, chain_id: int, token_address: str, timestamp: int, block_number: int):
    if source == "defillama":
        return fetch_defillama_price(chain_id, token_address, timestamp)
    raise ValueError(f"unsupported price source {source!r}; expected {sorted(SUPPORTED_SOURCES)}")


def price_unpriced_reports(
    conn,
    limit: int | None = None,
    source: str = "defillama",
    fallback: str | None = None,
    retry_missing: bool = False,
    chain_ids: set[int] | None = None,
    onchain_fallbacks: bool = True,
) -> int:
    if source not in SUPPORTED_SOURCES:
        raise ValueError(f"unsupported price source {source!r}")
    if fallback is not None and fallback not in SUPPORTED_SOURCES:
        raise ValueError(f"unsupported fallback source {fallback!r}")
    chain_filter = ""
    chain_params: list[int] = []
    if chain_ids:
        chain_filter = f"AND r.chain_id IN ({','.join('?' for _ in chain_ids)})"
        chain_params = sorted(chain_ids)
    price_filter = "AND p.token_address IS NULL"
    if retry_missing:
        price_filter = "AND (p.token_address IS NULL OR p.status != 'ok')"
    sql = f"""
    SELECT
        r.chain_id,
        r.asset,
        r.block_timestamp,
        MIN(r.block_number) AS block_number
    FROM strategy_reports r
    LEFT JOIN prices p
      ON p.chain_id = r.chain_id
     AND p.token_address = r.asset
     AND p.timestamp = r.block_timestamp
     AND p.source = ?
    WHERE r.asset IS NOT NULL
      {chain_filter}
      {price_filter}
    GROUP BY r.chain_id, r.asset, r.block_timestamp
    ORDER BY r.block_timestamp
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, (source, *chain_params)).fetchall()
    count = 0
    if source == "defillama":
        count += _price_unpriced_reports_defillama_batched(conn, rows, fallback, onchain_fallbacks=onchain_fallbacks)
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
                INSERT OR REPLACE INTO prices (
                    chain_id, token_address, timestamp, block_number,
                    source, price_usd, status, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
    chain_ids: set[int] | None = None,
    onchain_fallbacks: bool = True,
) -> int:
    if source not in SUPPORTED_SOURCES:
        raise ValueError(f"unsupported price source {source!r}")
    if fallback is not None and fallback not in SUPPORTED_SOURCES:
        raise ValueError(f"unsupported fallback source {fallback!r}")
    rows = _unpriced_volume_rows(conn, source, limit, retry_missing=retry_missing, chain_ids=chain_ids)
    count = 0
    if source == "defillama":
        count += _price_unpriced_reports_defillama_batched(conn, rows, fallback, onchain_fallbacks=onchain_fallbacks)
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
                INSERT OR REPLACE INTO prices (
                    chain_id, token_address, timestamp, block_number,
                    source, price_usd, status, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
    chain_ids: set[int] | None = None,
) -> list[dict[str, int | str]]:
    rows_by_key: dict[tuple[int, str, int], dict[str, int | str]] = {}
    chain_filter = ""
    chain_params: list[int] = []
    if chain_ids:
        chain_filter = f"AND f.chain_id IN ({','.join('?' for _ in chain_ids)})"
        chain_params = sorted(chain_ids)
    price_filter = "AND p.token_address IS NULL"
    if retry_missing:
        price_filter = "AND (p.token_address IS NULL OR p.status != 'ok')"
    for table in ("vault_flows", "strategy_debt_flows"):
        table_rows = conn.execute(
            f"""
            SELECT
                f.chain_id,
                f.asset,
                f.block_timestamp,
                MIN(f.block_number) AS block_number
            FROM {table} f
            LEFT JOIN prices p
              ON p.chain_id = f.chain_id
             AND p.token_address = f.asset
             AND p.timestamp = f.block_timestamp
             AND p.source = ?
            WHERE f.asset IS NOT NULL
              {chain_filter}
              {price_filter}
            GROUP BY f.chain_id, f.asset, f.block_timestamp
            ORDER BY f.block_timestamp
            """,
            (source, *chain_params),
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
        total = sum(len(batch_) for batch_ in batches)
        for future in as_completed(futures):
            batch, results = future.result()
            row_by_key = {
                (int(row["chain_id"]), row["asset"], int(row["block_timestamp"])): row for row in batch
            }
            fallback_by_key: dict[tuple[int, str, int], Any] = {}
            future_to_key: dict[Any, tuple[int, str, int]] = {}
            if onchain_fallbacks:
                for row in batch:
                    key = (int(row["chain_id"]), row["asset"], int(row["block_timestamp"]))
                    if (
                        results.get(key, (None, "missing", {}))[1] != "ok"
                        and key not in fallback_by_key
                    ):
                        fallback_by_key[key] = fallback_executor.submit(
                            _safe_row_level_fallback_price,
                            int(row["chain_id"]),
                            row["asset"],
                            int(row["block_timestamp"]),
                            int(row["block_number"]),
                        )
                future_to_key = {future: key for key, future in fallback_by_key.items()}
            for row in batch:
                key = (int(row["chain_id"]), row["asset"], int(row["block_timestamp"]))
                if key in fallback_by_key and onchain_fallbacks:
                    continue
                count += _write_defillama_price_row(
                    conn,
                    row,
                    results,
                    fallback,
                    onchain_fallbacks=onchain_fallbacks,
                    fallback_result=None,
                )
            conn.commit()
            for fb_future in as_completed(future_to_key):
                key = future_to_key[fb_future]
                count += _write_defillama_price_row(
                    conn,
                    row_by_key[key],
                    results,
                    fallback,
                    onchain_fallbacks=onchain_fallbacks,
                    fallback_result=fb_future.result(),
                )
                if count % 100 == 0:
                    print(f"progress: priced {count}/{total} rows", flush=True)
                conn.commit()
    conn.commit()
    return count


def _fetch_defillama_batch(batch):
    requests_ = [
        (int(row["chain_id"]), row["asset"], int(row["block_timestamp"]))
        for row in batch
    ]
    try:
        return batch, fetch_defillama_prices_batch(requests_)
    except Exception:
        results = {}
        for chain_id, token_address, timestamp in requests_:
            price, status, payload = fetch_defillama_price(chain_id, token_address, timestamp)
            results[(chain_id, token_address, timestamp)] = (price, status, payload)
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
    if status != "ok" and onchain_fallbacks:
        if fallback_result is None:
            fallback_result = _row_level_fallback_price(chain_id, token_address, timestamp, block_number)
        fallback_price, fallback_status, fallback_payload = fallback_result
        if fallback_status == "ok":
            price, status, payload = fallback_price, fallback_status, fallback_payload
        else:
            payload = {
                "source": "defillama",
                "status": status,
                "coin": payload.get("coin") if isinstance(payload, dict) else None,
                "timestamp": timestamp,
                "fallback": fallback_payload,
            }
    conn.execute(
        """
        INSERT OR REPLACE INTO prices (
            chain_id, token_address, timestamp, block_number,
            source, price_usd, status, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
    return 1


def _defillama_coin_batches(rows, max_timestamps: int = 400):
    rows_by_coin: dict[tuple[int, str], list] = defaultdict(list)
    for row in rows:
        rows_by_coin[(int(row["chain_id"]), row["asset"])].append(row)
    for coin_rows in rows_by_coin.values():
        coin_rows.sort(key=lambda row: int(row["block_timestamp"]))
        yield from _chunks(coin_rows, max_timestamps)
