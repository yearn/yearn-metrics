"""Small Multicall3 helper for metadata fan-out."""

from __future__ import annotations

from typing import Any

from eth_abi import decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from .chains import chain_config, web3_for


MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"

MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "target", "type": "address"},
                    {"name": "allowFailure", "type": "bool"},
                    {"name": "callData", "type": "bytes"},
                ],
                "name": "calls",
                "type": "tuple[]",
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"name": "success", "type": "bool"},
                    {"name": "returnData", "type": "bytes"},
                ],
                "name": "returnData",
                "type": "tuple[]",
            }
        ],
        "stateMutability": "payable",
        "type": "function",
    }
]


def selector(signature: str) -> bytes:
    return function_signature_to_4byte_selector(signature)


def multicall_available(chain: str) -> bool:
    return bool(web3_for(chain).eth.get_code(Web3.to_checksum_address(MULTICALL3)))


def aggregate3(chain: str, calls: list[tuple[str, str]]) -> list[tuple[bool, bytes]]:
    """Run `(target, function_signature)` calls with allowFailure=true."""
    return aggregate3_raw(chain, [(target, selector(signature)) for target, signature in calls])


def _call_data_bytes(call_data: str | bytes) -> bytes:
    if isinstance(call_data, bytes):
        return call_data
    value = call_data[2:] if call_data.startswith("0x") else call_data
    return bytes.fromhex(value)


def _aggregate3_raw_once(
    chain: str,
    calls: list[tuple[str, str | bytes]],
    block_number: int | None = None,
) -> list[tuple[bool, bytes]]:
    """Run `(target, calldata)` calls with allowFailure=true."""
    if not calls:
        return []
    w3 = web3_for(chain)
    contract = w3.eth.contract(address=Web3.to_checksum_address(MULTICALL3), abi=MULTICALL3_ABI)
    payload = [
        {
            "target": Web3.to_checksum_address(target),
            "allowFailure": True,
            "callData": _call_data_bytes(call_data),
        }
        for target, call_data in calls
    ]
    if block_number is None:
        raw = contract.functions.aggregate3(payload).call()
    else:
        raw = contract.functions.aggregate3(payload).call(block_identifier=int(block_number))
    return [(bool(success), bytes(data)) for success, data in raw]


def aggregate3_raw(
    chain: str,
    calls: list[tuple[str, str | bytes]],
    batch_size: int = 250,
    block_number: int | None = None,
) -> list[tuple[bool, bytes]]:
    """Run `(target, calldata)` calls with allowFailure=true, chunked for RPC reliability."""
    results: list[tuple[bool, bytes]] = []
    for i in range(0, len(calls), batch_size):
        results.extend(_aggregate3_raw_once(chain, calls[i : i + batch_size], block_number=block_number))
    return results


def decode_first(output_type: str, result: tuple[bool, bytes]) -> Any:
    success, data = result
    if not success or not data:
        return None
    try:
        return decode([output_type], data)[0]
    except Exception:
        return None
