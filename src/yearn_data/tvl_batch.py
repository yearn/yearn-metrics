"""Batched archive reads and persisted exact-day prices shared by refresh and backfill."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import time

import requests
from web3 import Web3

from . import tvl_sources as sources
from .multicall import MULTICALL3, MULTICALL3_ABI
from .pricing_clients import (fetch_yearn_price, fetch_yearn_prices_batch,
    YearnPricesAuthenticationError, YearnPricesConfigurationError)
from .fee_valuation import endpoint_identity
from .pricing_policy import PRICE_EVIDENCE_POLICY


def call_data(signature, arg=None):
    data = '0x' + bytes(Web3.keccak(text=signature)[:4]).hex()
    return data if arg is None else data + arg.lower().removeprefix('0x').rjust(64, '0')


class Prices:
    def __init__(self, conn, *, retry_missing=False, retry_statuses=None, fetch_batch=None, fetch_exact=None):
        self.conn = conn
        self.cache = {}
        self.retry_missing = retry_missing
        self.retry_statuses = retry_statuses
        self.fetch_batch = fetch_batch or fetch_yearn_prices_batch
        self.fetch_exact = fetch_exact or fetch_yearn_price
        self.source = 'yearn-tvl-prices:' + PRICE_EVIDENCE_POLICY + ':' + endpoint_identity()

    def get(self, chain, token, timestamp):
        key = (chain, token.lower(), timestamp)
        if key not in self.cache:
            self.prime([key])
        return self.cache[key]

    def prime(self, keys):
        pending = []
        keys = sorted(set(keys) - self.cache.keys())
        cached = {}
        for offset in range(0, len(keys), 500):
            chunk = keys[offset:offset+500]
            marks = ','.join('(?,?,?)' for _ in chunk)
            rows = self.conn.execute(
                'SELECT * FROM tvl_prices WHERE source=? AND (chain_id,token_address,timestamp) IN (VALUES '
                + marks + ')', (self.source, *(value for key in chunk for value in key)))
            cached.update(((r['chain_id'], r['token_address'], r['timestamp']), r) for r in rows)
        for key in keys:
            row = cached.get(key)
            retry = (row and self.retry_missing and row['status'] != 'ok'
                     and (self.retry_statuses is None or row['status'] in self.retry_statuses))
            if row and not retry:
                self.cache[key] = (row['price_usd'], row['status'], json.loads(row['raw_json']))
            else:
                pending.append(key)
        results = {}
        def batch(chunk):
            try:
                return self.fetch_batch(chunk, timeout=30)
            except (YearnPricesAuthenticationError, YearnPricesConfigurationError):
                raise
            except Exception:
                return {}  # Exact lookup still verifies every omitted target.
        with ThreadPoolExecutor(max_workers=4) as pool:
            for found in pool.map(batch, [pending[i:i+50] for i in range(0,len(pending),50)]):
                results.update(found)
            absent = [key for key in pending if key not in results]
            def exact(key):
                try:
                    return key, self.fetch_exact(*key, timeout=30)
                except (YearnPricesAuthenticationError, YearnPricesConfigurationError):
                    raise
                except Exception as exc:
                    return key, (None, 'unavailable', {'failure_class':type(exc).__name__,
                        'failure_reason':str(exc) if isinstance(exc,ValueError) else type(exc).__name__})
            results.update(pool.map(exact, absent))
        with self.conn:
            self.conn.executemany("""
                INSERT INTO tvl_prices
                (chain_id,token_address,timestamp,source,price_usd,status,raw_json)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(chain_id,token_address,timestamp,source) DO UPDATE SET
                    price_usd=excluded.price_usd,
                    status=excluded.status,
                    raw_json=excluded.raw_json
            """, ((*key,self.source,price,status,json.dumps(evidence))
                  for key,(price,status,evidence) in results.items()))
        self.cache.update(results)


class BatchedReader(sources.ArchiveReader):
    def __init__(self, chain, conn, head, vaults, edges, births, prices):
        super().__init__(chain)
        if int(self.w3.eth.chain_id) != chain:
            raise ValueError('RPC chain mismatch')
        if head is not None:
            pinned = self.w3.eth.get_block(head['head_block'])
            if bytes(pinned['hash']).hex() != head['head_hash']:
                raise ValueError('pinned finalized block hash changed')
            self.head = pinned
        self.chain, self.conn = chain, conn
        self.vaults, self.edges, self.births, self.prices = vaults, edges, births, prices
        self.prepared = None
        self.multicall = self.w3.eth.contract(address=Web3.to_checksum_address(MULTICALL3), abi=MULTICALL3_ABI)

    def price(self, chain, token, timestamp):
        return self.prices.get(chain, token, timestamp)

    def exists(self, address, block):
        birth = self.births.get(address.lower())
        if birth is not None:
            return block >= birth
        return super().exists(address, block)

    def words(self, address, signature, block, arg=None):
        key = (address.lower(),call_data(signature,arg),block)
        result = self.cache.get(key)
        if isinstance(result, Exception):
            raise result
        return super().words(address,signature,block,arg)

    def transport(self, calls, block, use_multicall):
        if use_multicall:
            payload = [(Web3.to_checksum_address(a),True,bytes.fromhex(d[2:])) for a,d in calls]
            try:
                rows = self.multicall.functions.aggregate3(payload).call(block_identifier=block)
                if len(rows) != len(calls):
                    raise ValueError('multicall response length mismatch')
                # Use direct calls for failures, preserving the native call context.
                bad = [c for c,(ok,raw) in zip(calls,rows) if not ok or not raw]
                fallback = self.transport(bad,block,False) if bad else {}
                return {c:(bytes(raw) if ok and raw else fallback[c]) for c,(ok,raw) in zip(calls,rows)}
            except Exception:
                pass
        payload = [{'jsonrpc':'2.0','id':i,'method':'eth_call','params':[{'to':a,'data':d},hex(block)]} for i,(a,d) in enumerate(calls)]
        for attempt in range(3):
            try:
                response = requests.post(self.w3.provider.endpoint_uri,json=payload,timeout=60)
                response.raise_for_status()
                rows = response.json()
                if not isinstance(rows,list) or len(rows)!=len(calls):
                    raise ValueError('RPC batch response length mismatch')
                if any(not isinstance(r, dict) or type(r.get('id')) is not int for r in rows):
                    raise ValueError('RPC batch response IDs malformed')
                indexed = {r['id']:r for r in rows}
                if set(indexed)!=set(range(len(calls))):
                    raise ValueError('RPC batch response IDs mismatch')
                return {c:(bytes.fromhex(indexed[i]['result'].removeprefix('0x')) if 'result' in indexed[i]
                           else ValueError('historical RPC call failed')) for i,c in enumerate(calls)}
            except Exception as exc:
                if attempt==2:
                    return {c:ValueError(type(exc).__name__) for c in calls}
                time.sleep(attempt+1)

    def prefetch_code(self, addresses, block):
        """Batch code probes; malformed/failed probes retain the direct fallback."""
        def fetch(chunk):
            payload = [dict(jsonrpc='2.0', id=i, method='eth_getCode', params=[address, hex(block)])
                       for i, address in enumerate(chunk)]
            try:
                response = requests.post(self.w3.provider.endpoint_uri, json=payload, timeout=30)
                response.raise_for_status()
                rows = response.json()
                if (not isinstance(rows, list) or len(rows) != len(chunk)
                        or any(not isinstance(r, dict) or type(r.get('id')) is not int for r in rows)):
                    return {}
                indexed = {r['id']:r for r in rows}
                if set(indexed) != set(range(len(chunk))):
                    return {}
                found = {}
                for i, address in enumerate(chunk):
                    value = indexed[i].get('result')
                    if isinstance(value, str) and value.startswith('0x'):
                        found[('code', address.lower(), block)] = bool(bytes.fromhex(value[2:]))
                return found
            except Exception:
                return {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for found in pool.map(fetch, [addresses[i:i+150] for i in range(0, len(addresses), 150)]):
                self.cache.update(found)

    def block_at(self, timestamp):
        if self.prepared == timestamp:
            return self.blocks[timestamp]
        if timestamp > int(self.head['timestamp']):
            raise ValueError('requested timestamp newer than pinned finalized head')
        row = self.conn.execute('SELECT block_number FROM tvl_backfill_blocks WHERE chain_id=? AND timestamp=?',(self.chain,timestamp)).fetchone()
        block = row['block_number'] if row else super().block_at(timestamp)
        if not row:
            with self.conn:
                self.conn.execute('INSERT INTO tvl_backfill_blocks VALUES (?,?,?)',(self.chain,timestamp,block))
        self.blocks[timestamp] = block
        self.cache.clear()
        if hasattr(self.prices, 'cache'):
            self.prices.cache.clear()
        calls = set()
        self.prefetch_code([Web3.to_checksum_address(v['address']) for v in self.vaults
                            if v['address'].lower() not in self.births], block)
        active = {v['address'].lower():v for v in self.vaults if self.exists(v['address'],block)}
        for address,v in active.items():
            calls.add((address,call_data('totalSupply()')))
            calls.add((address,call_data('getPricePerFullShare()' if v['version']=='v1' else 'totalAssets()')))
        for e in self.edges:
            if e['parent'] not in active:
                continue
            v = active[e['parent']]
            if e['method'] not in ('vault-asset','vault-held-shares'):
                if v['version'] in ('morpho-v2','curation'):
                    calls.add((e['parent'],call_data('isAdapter(address)',e['strategy'])))
                    calls.add((e['strategy'],call_data('realAssets()')))
                elif v['version'] in ('v2','v3'):
                    calls.add((e['parent'],call_data('strategies(address)',e['strategy'])))
            if e['method'] == 'vault-asset' and e['child'] in active:
                calls.add((e['child'],call_data('balanceOf(address)',e['parent'])))
            elif e['method'] not in ('direct-strategy','strategy-allocation','cross-chain-shares') and e['child'] in active:
                calls.add((e['child'],call_data('balanceOf(address)',e['strategy'])))
            if e.get('vault_getter'):
                calls.add((e['strategy'],call_data(e['vault_getter'])))
        use_multicall = bool(self.w3.eth.get_code(Web3.to_checksum_address(MULTICALL3),block_identifier=block))
        ordered = sorted(calls)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self.transport,ordered[i:i+150],block,use_multicall) for i in range(0,len(ordered),150)]
            for f in as_completed(futures):
                for (a,d),raw in f.result().items():
                    if isinstance(raw,bytes) and raw and len(raw)%32==0:
                        self.cache[a,d,block] = [int.from_bytes(raw[i:i+32],'big') for i in range(0,len(raw),32)]
                    else:
                        self.cache[a,d,block] = ValueError('invalid historical RPC result')
        keys = []
        for a,v in active.items():
            try:
                raw = self.uint(a,'getPricePerFullShare()' if v['version']=='v1' else 'totalAssets()',block)
            except Exception:
                continue
            if raw>0 and v['asset']:
                keys.append((self.chain,v['asset'].lower(),timestamp))
        self.prices.prime(keys)
        self.prepared = timestamp
        return block



def reader(conn, chain, *, retry_missing=False):
    vaults = [dict(v) for v in conn.execute('SELECT * FROM tvl_vaults WHERE chain_id=?', (chain,))]
    return BatchedReader(chain, conn, None, vaults, sources.candidate_edges(conn, vaults), {},
                         Prices(conn, retry_missing=retry_missing))
