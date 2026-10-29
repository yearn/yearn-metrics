"""Verified Envio curation events, with RPC retained as the acquisition fallback."""
from __future__ import annotations

import json
import re
from pathlib import Path
from web3 import Web3
from . import envio

SCOPE = json.loads((Path(__file__).parent/'inventories/envio-curation-scope.json').read_text())


class CurationUnavailable(ValueError):
    """Public-scope diagnostic; never includes a provider URL or response."""


class CurationEvents:
    def __init__(self, chain_id, block, gql=None, anchor_block=None):
        self.chain_id = chain_id
        self.block = block
        self.anchor_block = anchor_block or block
        self.gql = gql or envio._gql
        self.state = None
        self.unavailable = False
        self.anchor = None
        self.sources = {'envio': 0, 'rpc': 0}
        self.failures = {}

    def query(self, query, variables):
        # Range splitting belongs to the RPC fallback; don't stall each weekly
        # discovery range for the legacy five-attempt HTTP backoff.
        return self.gql(query, variables, timeout=10, split_timeouts=True, max_attempts=1)

    def eligible(self, address, name, start, end):
        if name not in {'CreateMetaMorpho','CreateVaultV2','AddAdapter'}:
            raise CurationUnavailable('event family not indexed')
        if self.unavailable:
            raise CurationUnavailable('curation indexing coverage unavailable')
        if self.state is None:
            try:
                data = self.query('''query($chain:Int!,$id:String!){
                    CurationIndexingState_by_pk(id:$id){chainId scopeHash fromBlock throughBlock}
                    chain_metadata(where:{chain_id:{_eq:$chain}}){latest_processed_block}}''',
                    {'chain':self.chain_id,'id':str(self.chain_id)})
                state = data.get('CurationIndexingState_by_pk')
                heads = data.get('chain_metadata') or []
                if (not state or state['chainId']!=self.chain_id or state['scopeHash']!=SCOPE['scopeHash']
                        or len(heads)!=1 or int(heads[0]['latest_processed_block'])<int(state['throughBlock'])):
                    raise CurationUnavailable('curation indexing coverage unavailable')
                self.state = state
            except Exception:
                self.unavailable = True
                raise CurationUnavailable('curation indexing coverage unavailable') from None
        if int(self.state['fromBlock'])>start or int(self.state['throughBlock'])<end:
            raise CurationUnavailable('curation indexing range incomplete')
        address = Web3.to_checksum_address(address)
        family = {'CreateMetaMorpho':'morpho-v1-factory','CreateVaultV2':'morpho-v2-factory','AddAdapter':'morpho-v2'}[name]
        entry = next((c for c in SCOPE['contracts'] if c['chainId']==self.chain_id
                      and c['address'].lower()==address.lower() and c['family']==family),None)
        if entry:
            if entry['startBlock']>start:
                raise CurationUnavailable('contract indexing starts after requested range')
        elif family=='morpho-v2':
            data=self.query('''query($id:String!){CurationVault_by_pk(id:$id){chainId address vaultVersion createdBlock}}''',
                            {'id':f'{self.chain_id}:{address.lower()}'})
            row=data.get('CurationVault_by_pk')
            if (not row or row['chainId']!=self.chain_id or row['address'].lower()!=address.lower()
                    or row['vaultVersion']!=2 or int(row['createdBlock'])>start):
                raise CurationUnavailable('contract indexing coverage unavailable')
        else:
            raise CurationUnavailable('factory indexing coverage unavailable')
        # Check an indexed canonical anchor at or beyond this range. A chain
        # height alone cannot distinguish stale indexer data after a reorg.
        if self.anchor is None or self.anchor['block_number']<end:
            data=self.query('''query($chain:Int!,$end:Int!,$through:Int!){raw_events(limit:1,
                order_by:{block_number:desc},where:{chain_id:{_eq:$chain},
                block_number:{_gte:$end,_lte:$through}}){chain_id block_number block_hash}}''',
                {'chain':self.chain_id,'end':end,'through':int(self.state['throughBlock'])})
            anchors=data.get('raw_events') or []
            if len(anchors)!=1:
                raise CurationUnavailable('canonical indexed anchor unavailable')
            anchor=anchors[0]
            self.anchor=anchor
        anchor=self.anchor
        if (anchor['chain_id']!=self.chain_id or not end<=int(anchor['block_number'])<=int(self.state['throughBlock'])
                or Web3.to_hex(self.anchor_block(int(anchor['block_number']))['hash']).lower()!=anchor['block_hash'].lower()):
            raise CurationUnavailable('indexed anchor is not canonical')
        return address

    def logs(self, address, abi, start, end):
        address = self.eligible(address,abi['name'],start,end)
        factory=abi['name']!='AddAdapter'
        entity='CurationFactoryEvent' if factory else 'MorphoGovernanceEvent'
        address_field='factoryAddress' if factory else 'vaultAddress'
        version=1 if abi['name']=='CreateMetaMorpho' else 2
        page_size=1000
        cursor=''
        rows=[]
        seen=set()
        while True:
            fields=f'chainId {address_field} vaultVersion blockNumber blockTimestamp blockHash transactionHash logIndex argsJson id'
            if not factory:fields+=' eventName'
            event_filter='' if factory else ',eventName:{_eq:"AddAdapter"}'
            query=f'''query($chain:Int!,$addresses:[String!]!,$lo:Int!,$hi:Int!,$cursor:String!,$limit:Int!){{
                {entity}(limit:$limit,order_by:{{id:asc}},where:{{chainId:{{_eq:$chain}},
                {address_field}:{{_in:$addresses}},vaultVersion:{{_eq:{version}}},
                blockNumber:{{_gte:$lo,_lte:$hi}},id:{{_gt:$cursor}}{event_filter}}}){{{fields}}}}}'''
            data=self.query(query,{'chain':self.chain_id,'addresses':list(dict.fromkeys([address,address.lower()])),
                'lo':start,'hi':end,'cursor':cursor,'limit':page_size})
            page=data.get(entity)
            if not isinstance(page,list) or len(page)>page_size:
                raise CurationUnavailable('invalid indexed event page')
            if not page:break
            for row in page:
                if (not isinstance(row['id'],str) or row['id']<=cursor or row['chainId']!=self.chain_id
                        or row[address_field].lower()!=address.lower() or row['vaultVersion']!=version
                        or not start<=int(row['blockNumber'])<=end
                        or (not factory and row['eventName']!='AddAdapter')):
                    raise CurationUnavailable('indexed event scope mismatch')
                header=self.block(int(row['blockNumber']))
                if (Web3.to_hex(header['hash']).lower()!=row['blockHash'].lower()
                        or int(header['timestamp'])!=int(row['blockTimestamp'])):
                    raise CurationUnavailable('indexed event is not canonical')
                args=json.loads(row['argsJson'])
                # Validate required ABI values, including zero and large uints.
                for field in abi['inputs']:
                    value=args[field['name']]
                    if field['type']=='string' and not isinstance(value,str):
                        raise CurationUnavailable('invalid indexed event string')
                    if field['type']=='address' and not Web3.is_address(value):
                        raise CurationUnavailable('invalid indexed event address')
                    if field['type'].startswith('uint'):
                        if isinstance(value,bool) or not str(value).isdigit() or not 0<=int(value)<2**int(field['type'][4:] or 256):
                            raise CurationUnavailable('invalid indexed event integer')
                        args[field['name']]=int(value)
                    if field['type']=='bytes32' and (not isinstance(value,str) or len(value)!=66 or not re.fullmatch(r"0x[0-9a-fA-F]{64}",value)):
                        raise CurationUnavailable('invalid indexed event bytes')
                tx=row['transactionHash']
                if not isinstance(tx,str) or len(tx)!=66 or not re.fullmatch(r"0x[0-9a-fA-F]{64}",tx) or isinstance(row['logIndex'],bool) or int(row['logIndex'])<0:
                    raise CurationUnavailable('invalid indexed log identity')
                key=(tx.lower(),int(row['logIndex']))
                if key in seen:raise CurationUnavailable('duplicate indexed log identity')
                seen.add(key)
                rows.append({'address':address,'args':args,'blockNumber':int(row['blockNumber']),
                    'blockHash':bytes.fromhex(row['blockHash'][2:]),'transactionHash':bytes.fromhex(tx[2:]),
                    'logIndex':int(row['logIndex'])})
                cursor=row['id']
        self.sources['envio']+=1
        return rows

    def failed(self, exc):
        reason=str(exc) if isinstance(exc,CurationUnavailable) else type(exc).__name__
        self.failures[reason]=self.failures.get(reason,0)+1
        self.sources['rpc']+=1
