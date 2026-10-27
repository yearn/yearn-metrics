"""Contract valuation adapters using accepted underlying quotes; no fixed parity guesses.

fetch_underlying_price is the public quote dependency for contract-derived valuations.
It uses provider clients; adapters never read or write analysis/price tables.
"""
from __future__ import annotations
from decimal import Decimal
from functools import lru_cache
from typing import Any
import requests
from eth_abi import decode as abi_decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from .config import CHAINS, get_rpc_url
from . import pricing_clients as clients
from .pricing_policy import CONTRACT_SOURCE_NAMES

ETHEREUM_WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


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

CURVE_LEGACY_POOLS = {
    # Curve sUSD LP token -> pre-registry pool.
    "0xc25a3a3b969415c80451098fa907ec722572917f": "0xA5407eAE9Ba41422680e2e00537571bcC53efBfD",
}

BALANCER_VAULT = "0xBA12222222228d8Ba445958a75a0704d566BF2C8"

BALANCER_CHAINS = {1, 137, 8453, 42161}

ONCHAIN_ADAPTER_SOURCES = CONTRACT_SOURCE_NAMES


def _selector(signature: str) -> str:
    return "0x" + function_signature_to_4byte_selector(signature).hex()


def _eth_call(chain_id: int, address: str, data: str, block_number: int, timeout: int = 30, *, strict: bool = False) -> str | None:
    chain = next(c.key for c in CHAINS.values() if c.chain_id == int(chain_id))
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": Web3.to_checksum_address(address), "data": data}, hex(int(block_number))],
    }
    response = requests.post(get_rpc_url(chain), json=payload, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    result = body.get("result")
    if strict and body.get('error'):
        error = body['error']
        if isinstance(error, dict) and (error.get('code') == 3 or
                'execution reverted' in str(error.get('message', '')).lower()):
            return None  # Unsupported signature or a completed array getter.
        raise ValueError('RPC call failed')
    if strict and (not isinstance(result, str) or not result.startswith('0x')):
        raise ValueError('invalid RPC call result')
    if not isinstance(result, str) or result == "0x":
        return None
    return result


def _call_uint256(chain_id: int, address: str, signature: str, block_number: int, args_hex: str = "") -> int | None:
    try:
        result = _eth_call(chain_id, address, _selector(signature) + args_hex, block_number)
        if not result:
            return None
        return int(abi_decode(["uint256"], bytes.fromhex(result[2:]))[0])
    except Exception:
        return None


def _call_address(chain_id: int, address: str, signature: str, block_number: int, args_hex: str = "", *, strict: bool = False) -> str | None:
    try:
        result = (_eth_call(chain_id, address, _selector(signature) + args_hex, block_number, strict=True)
                  if strict else _eth_call(chain_id, address, _selector(signature) + args_hex, block_number))
        if not result:
            return None
        decoded = abi_decode(["address"], bytes.fromhex(result[2:]))[0]
        if decoded == ZERO_ADDRESS:
            return None
        return Web3.to_checksum_address(decoded)
    except Exception:
        if strict:
            raise ValueError('reserve address unavailable') from None
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
    underlying_price, underlying_payload = fetch_underlying_price(chain_id, underlying, timestamp, block_number)
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
    """Value a Yearn V1 yToken from its underlying and historical share price."""

    underlying = _call_address(chain_id, token_address, "token()", block_number)
    if not underlying:
        return None, {"source": "yearn_v1_vault_fallback", "status": "missing_underlying"}
    price_per_share = _call_uint256(
        chain_id,
        token_address,
        "getPricePerFullShare()",
        block_number,
    )
    if price_per_share is None:
        return None, {
            "source": "yearn_v1_vault_fallback",
            "status": "missing_price_per_share",
            "underlying": underlying,
        }
    underlying_price, underlying_payload = fetch_underlying_price(
        chain_id,
        underlying,
        timestamp,
        block_number,
    )
    if underlying_price is None:
        return None, {
            "source": "yearn_v1_vault_fallback",
            "status": "missing_underlying_price",
            "underlying": underlying,
            "underlying_payload": underlying_payload,
        }
    return (
        float((Decimal(price_per_share) / Decimal(10**18)) * Decimal(str(underlying_price))),
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
    underlying_price, underlying_payload = fetch_underlying_price(chain_id, underlying, timestamp, block_number)
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
def fetch_underlying_price(chain_id: int, token_address: str, timestamp: int, block_number: int) -> tuple[float | None, dict[str, Any]]:
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
        yearn_v1_price, yearn_v1_payload = _yearn_v1_vault_price(
            chain_id,
            token_address,
            timestamp,
            block_number,
        )
        if yearn_v1_price is not None:
            return yearn_v1_price, yearn_v1_payload
    return None, {"source": "missing_underlying", "payload": payload}


@lru_cache(maxsize=200_000)
def _curve_lp_price(
    chain_id: int,
    token_address: str,
    timestamp: int,
    block_number: int,
    depth: int = 3,
) -> tuple[float | None, dict[str, Any]]:
    """Value a Curve LP from historical reserves, failing closed on gaps."""

    if int(chain_id) not in CURVE_LP_CHAINS or depth < 0:
        return None, {"source": "curve_lp_fallback", "status": "unsupported"}

    token = Web3.to_checksum_address(token_address)
    pool = (
        _call_address(chain_id, token, "minter()", block_number)
        or CURVE_LEGACY_POOLS.get(token_address.lower())
        or _curve_pool_from_registry(chain_id, token, block_number)
        or token
    )
    token_decimals = _call_uint256(chain_id, token, "decimals()", block_number)
    total_supply = _call_uint256(chain_id, token, "totalSupply()", block_number)
    if token_decimals is None or not total_supply:
        return None, {"source": "curve_lp_fallback", "status": "missing_supply", "pool": pool}

    coins: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for idx in range(8):
        try:
            coin = (
                _call_address(chain_id, pool, "coins(uint256)", block_number, _uint_arg(idx), strict=True)
                or _call_address(chain_id, pool, "coins(int128)", block_number, _uint_arg(idx), strict=True)
            )
        except ValueError:
            return None, {'source':'curve_lp_fallback','status':'missing_reserve_address',
                          'pool':pool,'reserve_index':idx}
        if not coin:
            break
        balance = _call_uint256(chain_id, pool, "balances(uint256)", block_number, _uint_arg(idx))
        if balance is None:
            balance = _call_uint256(chain_id, pool, "balances(int128)", block_number, _uint_arg(idx))
        if balance is None:
            missing.append({"coin": coin, "balance": None, "reason": "missing_balance"})
            continue
        if balance == 0:
            continue
        decimals = _call_uint256(chain_id, coin, "decimals()", block_number)
        if decimals is None:
            missing.append({"coin": coin, "balance": str(balance), "reason": "missing_decimals"})
            continue
        price, price_payload = _resolve_reserve_price(
            chain_id,
            coin,
            timestamp,
            block_number,
            depth - 1,
        )
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
    supply = Decimal(total_supply) / (Decimal(10) ** int(token_decimals))
    if supply <= 0 or tvl <= 0:
        return None, {"source": "curve_lp_fallback", "status": "zero_valued_pool", "pool": pool}
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
    depth: int = 3,
) -> tuple[float | None, dict[str, Any]]:
    """Value an ERC-4626-style share token from assets per whole share."""

    if depth < 0:
        return None, {"source": "erc4626_wrapper_fallback", "status": "unsupported"}
    token = Web3.to_checksum_address(token_address)
    if _call_address(chain_id, token, "SY()", block_number):
        return None, {
            "source": "erc4626_wrapper_fallback",
            "status": "pendle_principal_token_excluded",
        }
    token_decimals = _call_uint256(chain_id, token, "decimals()", block_number)
    underlying = _call_address(chain_id, token, "asset()", block_number)
    if token_decimals is None or not underlying or underlying.lower() == token_address.lower():
        return None, {"source": "erc4626_wrapper_fallback", "status": "missing_asset"}
    underlying_decimals = _call_uint256(chain_id, underlying, "decimals()", block_number)
    if underlying_decimals is None:
        return None, {
            "source": "erc4626_wrapper_fallback",
            "status": "missing_underlying_decimals",
            "underlying": underlying,
        }
    one_share = 10 ** int(token_decimals)
    assets = _call_uint256(
        chain_id,
        token,
        "convertToAssets(uint256)",
        block_number,
        _uint_arg(one_share),
    )
    rate_method = "convertToAssets(uint256)"
    rate_scale = Decimal(10) ** int(underlying_decimals)
    if assets is None and int(token_decimals) == 18:
        assets = _call_uint256(chain_id, token, "getRate()", block_number)
        rate_method = "getRate()"
        rate_scale = Decimal(10**18)
    if assets is None:
        return None, {
            "source": "erc4626_wrapper_fallback",
            "status": "missing_rate",
            "underlying": underlying,
        }
    underlying_price, underlying_payload = _resolve_reserve_price(
        chain_id,
        underlying,
        timestamp,
        block_number,
        depth - 1,
    )
    if underlying_price is None:
        return None, {
            "source": "erc4626_wrapper_fallback",
            "status": "missing_underlying_price",
            "underlying": underlying,
            "underlying_payload": underlying_payload,
        }
    assets_per_share = Decimal(assets) / rate_scale
    return (
        float(assets_per_share * Decimal(str(underlying_price))),
        {
            "source": "erc4626_wrapper_fallback",
            "underlying": underlying,
            "rate_method": rate_method,
            "assets": str(assets),
            "assets_per_share": format(assets_per_share, "f"),
            "token_decimals": int(token_decimals),
            "underlying_decimals": int(underlying_decimals),
            "underlying_payload": underlying_payload,
        },
    )


def _balancer_pool_supply(
    chain_id: int,
    token: str,
    block_number: int,
    total_supply: int,
    phantom_balance: int | None,
) -> tuple[int | None, str]:
    """Return circulating BPT supply for regular and preminted pools."""

    if phantom_balance is None:
        return total_supply, "totalSupply()"
    actual_supply = _call_uint256(chain_id, token, "getActualSupply()", block_number)
    if actual_supply:
        return actual_supply, "getActualSupply()"
    virtual_supply = _call_uint256(chain_id, token, "getVirtualSupply()", block_number)
    if virtual_supply:
        return virtual_supply, "getVirtualSupply()"
    circulating = total_supply - phantom_balance
    return (circulating if circulating > 0 else None), "totalSupply()-phantomBalance"


def _balancer_bpt_price(
    chain_id: int,
    token_address: str,
    timestamp: int,
    block_number: int,
    depth: int = 3,
) -> tuple[float | None, dict[str, Any]]:
    """Value a Balancer BPT from Vault reserves and circulating supply."""

    if int(chain_id) not in BALANCER_CHAINS or depth < 0:
        return None, {"source": "balancer_bpt_fallback", "status": "unsupported"}
    token = Web3.to_checksum_address(token_address)
    try:
        pool_id = _eth_call(chain_id, token, _selector("getPoolId()"), block_number)
        if not pool_id:
            return None, {"source": "balancer_bpt_fallback", "status": "missing_pool_id"}
        result = _eth_call(
            chain_id,
            BALANCER_VAULT,
            _selector("getPoolTokens(bytes32)") + pool_id[2:].zfill(64),
            block_number,
        )
        if not result:
            return None, {"source": "balancer_bpt_fallback", "status": "missing_pool_tokens"}
        reserves, balances, _ = abi_decode(
            ["address[]", "uint256[]", "uint256"],
            bytes.fromhex(result[2:]),
        )
    except Exception as exc:
        return None, {"source": "balancer_bpt_fallback", "status": "error", "error": str(exc)}

    token_decimals = _call_uint256(chain_id, token, "decimals()", block_number)
    total_supply = _call_uint256(chain_id, token, "totalSupply()", block_number)
    if token_decimals is None or not total_supply:
        return None, {"source": "balancer_bpt_fallback", "status": "missing_supply", "pool_id": pool_id}

    phantom_balance = next(
        (int(balance) for reserve, balance in zip(reserves, balances) if reserve.lower() == token_address.lower()),
        None,
    )
    supply_raw, supply_method = _balancer_pool_supply(
        chain_id,
        token,
        block_number,
        total_supply,
        phantom_balance,
    )
    if not supply_raw:
        return None, {"source": "balancer_bpt_fallback", "status": "missing_circulating_supply", "pool_id": pool_id}

    value = Decimal(0)
    priced_reserves: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for reserve, balance in zip(reserves, balances):
        if reserve.lower() == token_address.lower():
            priced_reserves.append({"coin": reserve, "balance": str(balance), "skipped": "phantom_bpt"})
            continue
        if balance == 0:
            continue
        price, price_payload = _resolve_reserve_price(
            chain_id,
            reserve,
            timestamp,
            block_number,
            depth - 1,
        )
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
        decimals = _call_uint256(chain_id, reserve, "decimals()", block_number)
        if decimals is None:
            missing.append({"coin": reserve, "balance": str(balance), "reason": "missing_decimals"})
            continue
        amount = Decimal(balance) / (Decimal(10) ** int(decimals))
        reserve_value = amount * Decimal(str(price))
        value += reserve_value
        priced_reserves.append(
            {
                "coin": reserve,
                "balance": str(balance),
                "decimals": int(decimals),
                "price": float(price),
                "value_usd": str(reserve_value),
                "price_payload": price_payload,
            }
        )
    if missing:
        return None, {
            "source": "balancer_bpt_fallback",
            "status": "missing_reserve_price",
            "pool_id": pool_id,
            "missing_reserves": missing,
            "priced_reserves": priced_reserves,
        }
    supply = Decimal(supply_raw) / (Decimal(10) ** int(token_decimals))
    if supply <= 0 or value <= 0:
        return None, {"source": "balancer_bpt_fallback", "status": "zero_valued_pool", "pool_id": pool_id}
    return (
        float(value / supply),
        {
            "source": "balancer_bpt_fallback",
            "pool_id": pool_id,
            "token": token,
            "supply": str(supply_raw),
            "supply_method": supply_method,
            "token_decimals": int(token_decimals),
            "reserves": priced_reserves,
        },
    )


def _resolve_reserve_price(
    chain_id: int,
    token_address: str,
    timestamp: int,
    block_number: int,
    depth: int,
) -> tuple[float | None, dict[str, Any]]:
    """Resolve direct and nested reserve prices with bounded recursion."""

    if depth < 0:
        return None, {"source": "reserve_resolver", "status": "depth_exhausted"}
    price, payload = fetch_underlying_price(chain_id, token_address, timestamp, block_number)
    if price is not None:
        return price, payload
    attempts: list[dict[str, Any]] = []
    for adapter in (_erc4626_wrapper_price, _curve_lp_price, _balancer_bpt_price):
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
    base_price, base_payload = fetch_underlying_price(chain_id, base_token, timestamp, block_number)
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
    base_price, base_payload = fetch_underlying_price(chain_id, base_token, timestamp, block_number)
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
    if address in CRV_BASED_CURVE_LPS:
        virtual_price = _call_uint256(chain_id, token_address, "get_virtual_price()", block_number)
        crv_price, crv_payload = fetch_underlying_price(chain_id, CRV, timestamp, block_number)
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
    price, payload = _crv_derivative_price(chain_id, token_address, timestamp, block_number)
    if price is not None:
        return price, "ok", payload
    attempts: list[dict[str, Any]] = []
    for adapter in (_erc4626_wrapper_price, _curve_lp_price, _balancer_bpt_price):
        try:
            price, payload = adapter(chain_id, token_address, timestamp, block_number)
        except Exception as exc:
            attempts.append({"adapter": adapter.__name__, "error": str(exc)})
            continue
        if price is not None:
            return price, "ok", payload
        attempts.append(payload)
    return None, "missing", {"source": "row_fallback", "status": "unresolved", "attempts": attempts}


def fetch_fallback_price(
    chain_id: int,
    token_address: str,
    timestamp: int,
    block_number: int,
) -> tuple[float | None, str, dict[str, Any]]:
    try:
        result = _row_level_fallback_price(chain_id, token_address, timestamp, block_number)
        return clients.price_result(*result, provider='local-adapter', requested_timestamp=timestamp)
    except Exception as exc:
        return clients.price_result(None, "missing", {
            "source": "onchain_fallback",
            "status": "error",
            "error": str(exc),
        }, provider='local-adapter', requested_timestamp=timestamp)


def fetch_defillama_prices_batch(requests_, timeout=30):
    return clients.fetch_defillama_prices_batch(requests_, timeout=timeout)


def fetch_defillama_price(chain_id, token_address, timestamp, timeout=30):
    target = (int(chain_id), token_address, int(timestamp))
    return fetch_defillama_prices_batch([target], timeout=timeout)[target]
