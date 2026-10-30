"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .analysis import run_lifetime_yield, run_vault_fees, run_vault_volume
from .config import CHAINS, DEFAULT_CHAINS, get_event_source, load_environment, normalize_chain_key
from .fees import index_canonical_fees, run_canonical_fees
from .discovery import discover
from .envio import discover_from_envio, import_reports_from_envio
from .exports import export_analysis
from .headline import LIFETIME_YIELD_HEADLINE_KEY, publish_lifetime_yield_headline
from .indexing import index_all_reports, index_all_volume, index_v2_fee_mints_from_reports
from .pricing import (
    DEFAULT_FALLBACK_PRICE_SOURCE,
    DEFAULT_PRICE_SOURCE,
    SUPPORTED_SOURCES,
    price_unpriced_reports,
    price_unpriced_volume,
    validate_report_pricing_policy,
)
from .storage import DEFAULT_DATABASE, connect, init_db, seed_chains


def progress(message: str) -> None:
    print(message, flush=True)


def _chains(values: list[str] | None) -> list[str]:
    if not values:
        return list(DEFAULT_CHAINS)
    return [normalize_chain_key(value) for value in values]


def _job_price_source(job: str, requested_source: str | None) -> str:
    if requested_source is not None:
        return requested_source
    return DEFAULT_PRICE_SOURCE if job == "lifetime-yield" else "defillama"


def _fallback_source(price_source: str) -> str | None:
    return DEFAULT_FALLBACK_PRICE_SOURCE if price_source == DEFAULT_PRICE_SOURCE else None


def _run_analysis(
    conn,
    job: str,
    price_source: str | None = None,
    provider_fallback: bool = True,
    before_timestamp: int | None = None,
    deferred_manifest: Path | None = None,
) -> int:
    if job == "fee-usd":
        if price_source not in (None, "yearn-prices"):
            raise ValueError("fee-usd supports Yearn Prices only")
        from .fee_valuation import run_fee_usd
        return run_fee_usd(conn, before_timestamp=before_timestamp, deferred_manifest=deferred_manifest)
    if deferred_manifest is not None:
        raise ValueError("deferred manifests are supported only for fee-usd")
    if before_timestamp is not None and job != "lifetime-yield":
        raise ValueError("valuation cutoff is supported only for fee-usd and lifetime-yield")
    if job == "canonical-fees":
        if price_source is not None:
            raise ValueError("canonical-fees currently exports raw asset amounts without USD pricing")
        return run_canonical_fees(conn)
    price_source = _job_price_source(job, price_source)
    if job == "lifetime-yield":
        return run_lifetime_yield(
            conn,
            price_source=price_source,
            fallback_price_source=_fallback_source(price_source) if provider_fallback else None,
            before_timestamp=before_timestamp,
        )
    if price_source != "defillama":
        raise ValueError("--price-source yearn-prices is currently supported only for lifetime-yield")
    if job == "vault-volume":
        return run_vault_volume(conn)
    if job == "vault-fees":
        return run_vault_fees(conn)
    raise ValueError(f"unsupported analysis job {job!r}")


def open_db(path: str | Path):
    conn = connect(path)
    init_db(conn)
    seed_chains(conn, CHAINS)
    return conn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yearn-data")
    parser.add_argument("--db", help="Postgres: neon or staging; defaults to YEARN_DATA_DB or neon")
    parser.add_argument("--env", action="append", default=[], help="Extra .env file to load before defaults")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db")

    canonical_p = sub.add_parser('consolidate-history', help='Keep one canonical history, verifying every historical key')
    canonical_p.add_argument('--apply', action='store_true', help='Apply atomically; the default only reports the plan')
    canonical_p.add_argument('--receipt', type=Path, help='Write key/value preservation counts and selected revision')

    tvl_p = sub.add_parser("tvl", help="Historical vault TVL and nested positions")
    tvl_sub = tvl_p.add_subparsers(dest="tvl_command", required=True)
    tvl_prepare=tvl_sub.add_parser('prepare-history',help='Prepare immutable TVL chart rows for fast reads')
    tvl_prepare.add_argument('--publication',required=True,type=Path)
    tvl_prepare.add_argument('--previous-dataset-id',help='Copy this prepared dataset and recompute only newly rejected quote dates')
    tvl_prepare.add_argument('--full-rebuild',action='store_true',help='Recalculate every date of a new immutable dataset')
    tvl_prepare.add_argument('--backfill-from',type=int,help='Force recalculation from this inclusive captured timestamp')
    tvl_prepare.add_argument('--backfill-to',type=int,help='Force recalculation through this inclusive captured timestamp')
    tvl_prepare.add_argument('--current-bridge-policy',choices=['none','retired-registry'],default='retired-registry')
    tvl_discover = tvl_sub.add_parser("discover", help="Create or refresh the TVL catalog independently")
    tvl_discover.add_argument("--sources", nargs="+", choices=["kong", "v1", "curation"], help="Default: all sources")
    tvl_discover.add_argument("--chain-ids", nargs="+", type=int)
    tvl_discover.add_argument("--from-block", type=int, help="Optional lower bound for factory and adapter scans")
    tvl_discover.add_argument("--to-block", type=int, help="Optional finalized upper bound for historical scans")
    tvl_discover.add_argument("--chunk-size", type=int, default=50_000)
    tvl_sub.add_parser("refresh-kong", help="Refresh paginated V2/V3 vault and strategy inventory")
    tvl_collect = tvl_sub.add_parser("collect", help="Store archive-state TVL at explicit timestamps")
    tvl_collect.add_argument("--from-timestamp", required=True, type=int)
    tvl_collect.add_argument("--to-timestamp", required=True, type=int)
    tvl_collect.add_argument("--interval", type=int, default=86400, help="Seconds between samples; default daily")
    tvl_collect.add_argument("--chain-ids", nargs="+", type=int)
    tvl_collect.add_argument("--vaults", nargs="+", help="Optional bounded vault-address selection")
    tvl_collect.add_argument("--price-source", choices=["yearn-prices", "defillama"], default="yearn-prices")
    tvl_map = tvl_sub.add_parser("map-holders", help="Scan strategy balances for nested child vaults at a date")
    tvl_map.add_argument("--timestamp", required=True, type=int)
    tvl_map.add_argument("--chain-ids", nargs="+", type=int)
    tvl_export = tvl_sub.add_parser("export", help="Export vaults, positions, and aggregate history")
    tvl_export.add_argument("--out", required=True)
    tvl_export.add_argument("--run-id", type=int)
    tvl_export.add_argument("--include-curation", action=argparse.BooleanOptionalAction, default=True, help="Deduct curated child ownership (default: enabled; --no-include-curation uses the separate-product convention)")

    pairing_p = sub.add_parser("select-pairing", help="Select completed earnings/fees for Powerglove without changing source data")
    pairing_p.add_argument("--earnings-run-id", type=int, required=True)
    pairing_p.add_argument("--fees-run-id", type=int, required=True)
    pairing_p.add_argument("--out", type=Path, default=Path("data/powerglove"))

    hosted_p = sub.add_parser('publish-hosted', help='Validate and atomically select database-backed API publications')
    hosted_p.add_argument('--publication', type=Path, required=True)
    hosted_p.add_argument('--tvl-publication', type=Path, required=True)
    hosted_choice = hosted_p.add_mutually_exclusive_group()
    hosted_choice.add_argument('--analytics-publication-id')
    hosted_choice.add_argument('--prepare-analytics', action='store_true', help='Prepare matching analytics; successful consolidation also updates the legacy selection')
    restore_p = sub.add_parser('select-hosted-release', help='Select a still-retained release; successful consolidation retires older releases')
    restore_p.add_argument('--release-id', required=True)

    sub.add_parser('collect-analytics', help='Collect owned DefiLlama references, membership and declared fee configurations')
    prepare_p = sub.add_parser('prepare-analytics', help='Publish remaining Powerglove analytics in the selected database')
    prepare_p.add_argument('--publication', type=Path, required=True)
    prepare_p.add_argument('--tvl-publication', type=Path, required=True)

    serve_p = sub.add_parser("serve-pairing", help="Serve read-only selected Powerglove views")
    serve_p.add_argument("--publication", type=Path, default=Path("data/powerglove"))
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=3490)
    serve_p.add_argument("--cors-origin", help="Optional production frontend origin")
    serve_p.add_argument("--tvl-publication", type=Path, help="Automatically publish accumulated finished TVL results too")
    serve_p.add_argument("--published-tvl-only", action="store_true", help="Serve only the updater's selected TVL publication")
    serve_p.add_argument("--current-bridge-policy", choices=["none", "retired-registry"], default="retired-registry")
    serve_p.add_argument("--analytics", action="store_true", help="Serve prepared owned analytics and comparison without legacy services")

    refresh_p=sub.add_parser('refresh',help='Resume bounded multi-chain acquisition/preparation; publishing is explicit')
    refresh_p.add_argument('--chains',nargs='+')
    refresh_p.add_argument('--cutoff',type=int,help='Exclusive closed UTC midnight')
    refresh_p.add_argument('--publication-root',type=Path)
    refresh_p.add_argument('--mode',choices=['acquire','prepare','publish'],default='prepare')
    refresh_p.add_argument('--resume',help='Resume this job with the same scope, bounds and configuration')
    refresh_p.add_argument('--inspect',help='Read a job receipt without acquiring the writer lock')
    refresh_p.add_argument('--from-block',action='append',default=[],metavar='CHAIN:BLOCK')
    refresh_p.add_argument('--confirmations',action='append',default=[],metavar='CHAIN:COUNT')
    refresh_p.add_argument('--first-close',type=int,help='Initial inclusive UTC day end when retained TVL scope is absent')
    refresh_p.add_argument('--source',choices=['auto','rpc','envio'],default='auto')
    refresh_p.add_argument('--chunk-size',type=int,help='Override report chunk defaults (Envio: 1000000; RPC: 50000)')
    refresh_p.add_argument('--chain-chunk-size',action='append',default=[],metavar='CHAIN:BLOCKS')
    refresh_p.add_argument('--report-workers',type=int,default=4)
    refresh_p.add_argument('--window-days',type=int,default=7,help='Recent acquisition window without explicit bounds')
    refresh_p.add_argument('--since',help='Continue acquisition after a publish-ready prepared/selected refresh receipt')
    refresh_p.add_argument('--max-targets',type=int,default=10_000,help='Total fee/pricing/chunk targets per invocation')
    refresh_p.add_argument('--max-dates',type=int,default=30,help='Total TVL chain/date targets per invocation')
    refresh_p.add_argument('--repair-history',action='store_true',help='Reacquire every close in the retained/explicit TVL window')
    refresh_p.add_argument('--deferred-manifest',type=Path)
    refresh_p.add_argument('--receipt',type=Path)

    catchup_p = sub.add_parser("catch-up", help="Acquire one bounded chain window; no pricing or analysis")
    catchup_p.add_argument("--chain", required=True, choices=sorted(CHAINS))
    catchup_p.add_argument("--from-block", type=int, required=True)
    catchup_p.add_argument("--to-block", type=int, required=True)
    catchup_p.add_argument("--chunk-size", type=int, default=50_000)
    catchup_p.add_argument("--source", choices=["auto", "rpc", "envio"], default="auto")
    catchup_p.add_argument("--confirmations", type=int, help="Explicit alternative to RPC finalized")
    catchup_p.add_argument("--discover", action="store_true", help="Also scan configured inventory sources in this window")
    catchup_p.add_argument("--include-experimental-v2", action="store_true")
    catchup_p.add_argument("--revalidate-rpc-coverage", action="store_true",
                          help="Recheck Optimism RPC coverage through Envio within the requested window")

    discover_p = sub.add_parser("discover")
    discover_p.add_argument("--chains", nargs="+", help="Chains to discover")
    discover_p.add_argument(
        "--find-deployment",
        action="store_true",
        help="Find deployment blocks now. Slower, but reduces later scan ranges.",
    )
    discover_p.add_argument(
        "--include-non-yearn",
        action="store_true",
        help="Also discover public V3 VaultFactory vaults not managed by Yearn role managers.",
    )
    discover_p.add_argument(
        "--skip-v2",
        action="store_true",
        help="Skip V2 registry discovery when refreshing V3-only vault sets.",
    )
    discover_p.add_argument("--include-retired", action="store_true", help="Retain historical V3 role-manager members without marking them active")
    discover_p.add_argument("--to-block", type=int, help="Stop Envio inventory discovery at this block")
    discover_p.add_argument(
        "--include-experimental-v2",
        action="store_true",
        help="Include V2 experimental registry deployments (excluded by default for RPC parity).",
    )

    index_p = sub.add_parser("index-events")
    index_p.add_argument("--chains", nargs="+", help="Chains to index")
    index_p.add_argument("--versions", nargs="+", choices=["v2", "v3"], help="Vault versions to index")
    index_p.add_argument("--to-block", type=int, help="Stop block for every selected chain")
    index_p.add_argument("--include-inactive", action="store_true", help="Include inactive vaults in bounded imports; forward imports always include them")
    index_p.add_argument("--from-block", type=int, help="Bounded Envio historical import; requires --to-block and preserves forward cursors")
    index_p.add_argument("--vaults", nargs="+", help="Limit bounded Envio historical import to these addresses")
    index_p.add_argument("--chunk-size", type=int, default=50_000)

    volume_index_p = sub.add_parser("index-volume")
    volume_index_p.add_argument("--chains", nargs="+", help="Chains to index")
    volume_index_p.add_argument("--versions", nargs="+", choices=["v2", "v3"], help="Vault versions to index")
    volume_index_p.add_argument("--to-block", type=int, help="Stop block for every selected chain")
    volume_index_p.add_argument("--chunk-size", type=int, default=50_000)

    fee_index_p = sub.add_parser("index-fees")
    fee_index_p.add_argument("--chains", nargs="+", help="Chains to index")

    fee_index_p.add_argument("--canonical", action="store_true")
    fee_index_p.add_argument("--limit", type=int)
    fee_index_p.add_argument("--retry-unresolved", action="store_true")
    fee_index_p.add_argument("--version", choices=["v2", "v3"])

    fee_index_p.add_argument("--verify-selective", action="store_true", help="Trace supported reconstructed fees when later vault logs are detected")
    fee_index_p.add_argument("--trace-limit", type=int, help="Maximum new trace requests per run (default 10)")
    fee_index_p.add_argument("--reconstruct-v2", action="store_true")
    fee_index_p.add_argument("--filtered-evidence-only", action="store_true", help="Project retained direct V2 logs offline")
    fee_index_p.add_argument("--refresh-evidence", action="store_true")
    fee_index_p.add_argument("--verify-execution", action="store_true", help="Verify executed fee operands for explicitly selected reports")
    fee_index_p.add_argument("--report-keys", type=Path, help="JSON array of [chain_id, tx_hash, report_log_index] triples")

    fee_recompute_p = sub.add_parser("recompute-fees", help="Recompute retained evidence without RPC")
    fee_recompute_p.add_argument("mode", choices=["policy", "formulas"])
    fee_recompute_p.add_argument("--chains", nargs="+")
    fee_recompute_p.add_argument("--limit", type=int)
    fee_recompute_p.add_argument("--version", choices=["v2", "v3"])

    tokenized_p = sub.add_parser("index-tokenized-fees", help="Import bounded Tokenized Strategy fee ranges")
    tokenized_p.add_argument("--inventory", type=Path, help="Classified inventory JSON; defaults to packaged Yearn inventory")
    tokenized_p.add_argument("--chain", required=True, choices=sorted(CHAINS))
    tokenized_p.add_argument("--from-block", required=True, type=int)
    tokenized_p.add_argument("--to-block", required=True, type=int)
    tokenized_p.add_argument("--before-timestamp", required=True, type=int)
    tokenized_p.add_argument("--chunk-size", type=int, default=2000)
    tokenized_p.add_argument("--max-vaults", type=int, default=10)
    tokenized_p.add_argument("--confirmations", type=int, help="Explicit latest-minus-N finality policy instead of finalized")

    fee_price_p = sub.add_parser("price-fees", help="Cache Yearn Prices for accepted closed-day fee amounts")
    fee_price_p.add_argument("--before-timestamp", type=int, help="Exclusive closed UTC midnight; defaults to today")
    fee_price_p.add_argument("--limit", type=int, default=500, help="Maximum distinct asset-days per invocation")
    fee_price_p.add_argument("--retry-missing", action="store_true")
    fee_price_p.add_argument("--refresh", action="store_true", help="Refresh selected cached prices")

    price_p = sub.add_parser("price")
    price_p.add_argument("--limit", type=int, help="Maximum distinct token/timestamp prices to fetch")
    price_p.add_argument("--source", choices=sorted(SUPPORTED_SOURCES), default=DEFAULT_PRICE_SOURCE)
    price_p.add_argument("--retry-missing", action="store_true", help="Retry existing non-ok price rows")
    price_p.add_argument("--no-provider-fallback", action="store_true", help="Do not use DefiLlama when Yearn Prices is unavailable")
    price_p.add_argument("--refresh-onchain-fallbacks", action="store_true", help="Recompute prices produced by local fallback adapters")
    price_p.add_argument("--chains", nargs="+", help="Restrict report pricing to chains")
    price_p.add_argument("--no-onchain-fallbacks", action="store_true", help="Use only the selected offchain price source")

    volume_price_p = sub.add_parser("price-volume")
    volume_price_p.add_argument("--limit", type=int, help="Maximum distinct token/timestamp prices to fetch")
    volume_price_p.add_argument("--source", choices=["defillama"], default="defillama")
    volume_price_p.add_argument("--retry-missing", action="store_true", help="Retry existing non-ok price rows")
    volume_price_p.add_argument("--refresh-onchain-fallbacks", action="store_true", help="Recompute prices produced by local fallback adapters")
    volume_price_p.add_argument("--chains", nargs="+", help="Restrict volume pricing to chains")
    volume_price_p.add_argument("--no-onchain-fallbacks", action="store_true", help="Use only the selected offchain price source")

    analyze_p = sub.add_parser("analyze")
    analyze_p.add_argument("job", choices=["lifetime-yield", "vault-volume", "vault-fees", "canonical-fees", "fee-usd"])

    analyze_p.add_argument("--before-timestamp", type=int, help="Exclusive closed UTC midnight for fee-usd or lifetime-yield")
    analyze_p.add_argument("--deferred-manifest", type=Path, help="Optional validated accounting exclusions for fee-usd")
    analyze_p.add_argument("--price-source", choices=sorted(SUPPORTED_SOURCES))
    analyze_p.add_argument("--no-provider-fallback", action="store_true")

    export_p = sub.add_parser("export")
    export_p.add_argument("job", choices=["lifetime-yield", "vault-volume", "vault-fees", "canonical-fees", "fee-usd"])
    export_p.add_argument("--out", default="exports")
    export_p.add_argument("--run-id", type=int, help="Export this completed run; defaults to latest")

    publish_p = sub.add_parser("publish")
    publish_sub = publish_p.add_subparsers(dest="publish_job", required=True)
    headline_p = publish_sub.add_parser("lifetime-yield")
    headline_p.add_argument("--redis-url", help="Redis URL. Defaults to REDIS_URL from the environment")
    headline_p.add_argument("--key", default=LIFETIME_YIELD_HEADLINE_KEY)
    headline_p.add_argument("--ttl", type=int, help="Optional Redis EX TTL in seconds")
    headline_p.add_argument("--analysis-run-id", type=int, help="Specific completed lifetime-yield analysis run to publish")
    headline_p.add_argument("--run-id", help="Override payload run_id")
    headline_p.add_argument("--dry-run", action="store_true", help="Print the JSON payload without writing Redis")

    run_p = sub.add_parser("run")
    run_p.add_argument("job", choices=["lifetime-yield", "vault-volume"])
    run_p.add_argument("--chains", nargs="+", help="Chains to discover/index")
    run_p.add_argument("--to-block", type=int)
    run_p.add_argument("--chunk-size", type=int, default=50_000)
    run_p.add_argument("--price-limit", type=int)
    run_p.add_argument("--price-source", choices=sorted(SUPPORTED_SOURCES))
    run_p.add_argument("--no-provider-fallback", action="store_true")
    run_p.add_argument("--no-onchain-fallbacks", action="store_true", help="Use only the selected offchain price source")
    run_p.add_argument("--find-deployment", action="store_true")
    run_p.add_argument("--out", default="exports")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command=='refresh' and args.db is None:
        raise ValueError('refresh requires an explicit --db neon or --db staging')
    load_environment(args.env)
    args.db = args.db or os.environ.get('YEARN_DATA_DB', str(DEFAULT_DATABASE))
    if args.command=='refresh':
        if args.db not in ('neon','staging'):
            raise ValueError('refresh requires a named Postgres database: neon or staging')
        from .refresh import run_refresh
        from .refresh_scope import RefreshOptions
        from .refresh_state import read_receipt,CheckpointRejected,RefreshBusy
        if args.inspect:
            try:result=read_receipt(args.db,args.inspect)
            except Exception as exc:
                result={'status':'rejected','failureClass':type(exc).__name__}
                if isinstance(exc,(CheckpointRejected,RefreshBusy)):result['reason']=str(exc)
        else:
            if not args.chains or args.cutoff is None or args.publication_root is None:
                raise ValueError('refresh requires --chains, --cutoff and --publication-root')
            def mapping(values):
                mapped={}
                for value in values:
                    chain,number=value.split(':',1)
                    key=normalize_chain_key(chain)
                    if key in mapped:raise ValueError('duplicate explicit chain bound')
                    mapped[key]=int(number)
                return mapped
            try:
                options=RefreshOptions(args.db,tuple(args.chains),args.cutoff,args.publication_root,
                    from_blocks=mapping(args.from_block),confirmations=mapping(args.confirmations),
                    chain_chunk_sizes=mapping(args.chain_chunk_size),report_workers=args.report_workers,
                    window_days=args.window_days,baseline_job=args.since,
                    first_close=args.first_close,source=args.source,chunk_size=args.chunk_size,
                    max_targets=args.max_targets,max_dates=args.max_dates,repair_history=args.repair_history,
                    deferred_manifest=args.deferred_manifest)
                result=run_refresh(options,mode=args.mode,resume=args.resume,
                    progress=lambda event:print(json.dumps(event,sort_keys=True),file=sys.stderr,flush=True))
            except Exception as exc:
                result={'status':'rejected','failureClass':type(exc).__name__}
                if isinstance(exc,(CheckpointRejected,RefreshBusy)):result['reason']=str(exc)
        body=json.dumps(result,sort_keys=True,indent=2)
        if args.receipt:
            args.receipt.parent.mkdir(parents=True,exist_ok=True)
            temporary=args.receipt.with_suffix(args.receipt.suffix+'.tmp')
            temporary.write_text(body+'\n');temporary.replace(args.receipt)
        print(body)
        return (3 if result['status']=='work-deferred' else 4 if result['status']=='selected-cleanup-failed'
                else 2 if result['status'] in ('failed','rejected') else 0)
    if args.command == 'consolidate-history':
        from .canonical_history import consolidate
        result = consolidate(args.db, apply=args.apply, progress=progress)
        body = json.dumps(result, sort_keys=True, indent=2)
        if args.receipt:
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(body+'\n')
        print(body)
        return 0
    if args.command == 'select-hosted-release':
        from .hosted_publications import select_release
        print(json.dumps({'releaseId':select_release(args.db, args.release_id)}))
        return 0
    if args.command == 'publish-hosted':
        from .hosted_publications import publish, PublicationCleanupError
        try:
            release = publish(args.db, args.publication, args.tvl_publication,
                              args.analytics_publication_id, prepare_analytics=args.prepare_analytics)
        except PublicationCleanupError as exc:
            print(json.dumps({'releaseId': exc.release_id, 'published': True,
                              'consolidated': False, 'error': str(exc)}))
            return 1
        print(json.dumps({'releaseId': release}))
        return 0
    if args.command == 'collect-analytics':
        from .analytics_collect import collect
        print(json.dumps(collect(args.db), sort_keys=True))
        return 0
    if args.command == 'prepare-analytics':
        from .analytics import prepare
        from .pairing import PairingStore
        from .tvl_api import TvlStore
        financial = PairingStore(args.publication).get()
        from .storage import database_reference
        if database_reference(financial.database) != database_reference(args.db):
            raise ValueError('financial publication database must match --db')
        tvl = TvlStore(args.db, args.tvl_publication, current_bridge_policy='retired-registry').get()
        print(json.dumps({'publicationId':prepare(args.db,financial,tvl)}))
        return 0
    if args.command == "select-pairing":
        from .pairing import select_pairing
        manifest = select_pairing(args.db, args.earnings_run_id, args.fees_run_id, args.out)
        print(json.dumps({'datasetId': manifest['datasetId'], 'publication': str(args.out)}, sort_keys=True))
        return 0
    if args.command == "serve-pairing":
        from .pairing import serve_pairing
        serve_pairing(args.publication, host=args.host, port=args.port, cors_origin=args.cors_origin,
                      tvl_publication=args.tvl_publication, current_bridge_policy=args.current_bridge_policy,
                      published_tvl_only=args.published_tvl_only,
                      analytics=args.analytics)
        return 0
    if args.command=='tvl' and args.tvl_command=='prepare-history':
        from .tvl_api import TvlStore
        from .tvl_history_cache import prepare, prepare_price_repair
        if (args.backfill_from is None)!=(args.backfill_to is None):
            raise ValueError('provide both --backfill-from and --backfill-to')
        backfill=None if args.backfill_from is None else (args.backfill_from,args.backfill_to)
        if args.previous_dataset_id and (args.full_rebuild or backfill):
            raise ValueError('choose price-only repair or general/full history preparation')
        store=TvlStore(args.db,args.publication,current_bridge_policy=args.current_bridge_policy)
        dataset=store.get()
        result=(prepare_price_repair(dataset,store.get(args.previous_dataset_id))
                if args.previous_dataset_id else prepare(dataset,progress=progress,
                    full_rebuild=args.full_rebuild,backfill=backfill))
        print(json.dumps(result,sort_keys=True))
        return 0
    conn = open_db(args.db)
    event_source = get_event_source()

    if args.command == "tvl":
        from .tvl import export_tvl
        from .tvl_sources import refresh_kong_catalog, collect_tvl, scan_holders
        if args.tvl_command == "discover":
            from .tvl_discovery import discover_catalog
            try:
                result = discover_catalog(conn, sources=args.sources, chain_ids=args.chain_ids,
                    from_block=args.from_block, to_block=args.to_block, chunk_size=args.chunk_size, progress=progress)
                print(json.dumps(result, sort_keys=True))
                return 0 if result["status"] == "complete" else 2
            finally:
                conn.close()
        elif args.tvl_command == "refresh-kong":
            result = {"vaults": refresh_kong_catalog(conn)}
        elif args.tvl_command == "map-holders":
            result = scan_holders(conn, args.timestamp, chain_ids=args.chain_ids, progress=progress)
        elif args.tvl_command == "collect":
            result = {"run_id": collect_tvl(conn, args.from_timestamp, args.to_timestamp,
                interval=args.interval, chain_ids=args.chain_ids, addresses=args.vaults,
                price_source=args.price_source, progress=progress)}
        else:
            result = {"out": str(export_tvl(conn, args.out, args.run_id, include_curation=args.include_curation))}
        print(json.dumps(result, sort_keys=True))
        conn.close()
        return 0

    if args.command == "init-db":
        from .storage import database_reference
        print(f"initialized {database_reference(args.db)}")
        return 0

    if args.command == "catch-up":
        from .catchup import catch_up
        result = catch_up(conn, args.chain, args.from_block, args.to_block,
                          source=args.source, confirmations=args.confirmations,
                          chunk_size=args.chunk_size, discover=args.discover,
                          experimental=args.include_experimental_v2,
                          revalidate_rpc_coverage=args.revalidate_rpc_coverage,
                          progress=lambda message: print(message, file=sys.stderr, flush=True))
        print(json.dumps(result, sort_keys=True))
        return 0

    if args.command == "discover":
        if event_source == "envio":
            if args.find_deployment:
                raise ValueError("--find-deployment requires YEARN_DATA_EVENT_SOURCE=rpc")
            if args.include_non_yearn:
                raise ValueError("--include-non-yearn is only available with YEARN_DATA_EVENT_SOURCE=rpc")
            count = discover_from_envio(
                conn,
                _chains(args.chains),
                skip_v2=args.skip_v2,
                include_experimental_v2=args.include_experimental_v2,
                include_retired=args.include_retired,
                to_block=args.to_block,
                progress=progress,
            )
        else:
            if args.to_block is not None:
                raise ValueError("discover --to-block requires YEARN_DATA_EVENT_SOURCE=envio")
            if args.include_retired:
                raise ValueError("--include-retired requires Envio discovery")
            if args.include_experimental_v2:
                raise ValueError("--include-experimental-v2 is only available with YEARN_DATA_EVENT_SOURCE=envio")
            count = discover(
                conn,
                _chains(args.chains),
                find_deployment=args.find_deployment,
                include_non_yearn=args.include_non_yearn,
                skip_v2=args.skip_v2,
            )
        print(f"discovered/upserted {count} vault rows")
        return 0

    if args.command == "index-events":
        if event_source == "envio":
            count = import_reports_from_envio(
                conn,
                _chains(args.chains),
                versions=args.versions,
                to_block=args.to_block,
                progress=progress,
                include_inactive=args.include_inactive or args.from_block is None,
                from_block=args.from_block,
                vault_addresses=args.vaults,
            )
        else:
            if args.include_inactive or args.from_block is not None or args.vaults:
                raise ValueError("historical selection flags require Envio import")
            count = index_all_reports(
                conn,
                _chains(args.chains),
                versions=args.versions,
                to_block=args.to_block,
                chunk_size=args.chunk_size,
                progress=progress,
            )
        print(f"indexed {count} strategy report logs")
        return 0

    if args.command == "index-volume":
        count = index_all_volume(
            conn,
            _chains(args.chains),
            versions=args.versions,
            to_block=args.to_block,
            chunk_size=args.chunk_size,
            progress=progress,
        )
        print(f"indexed {count} volume logs/rows")
        return 0

    if args.command == "index-tokenized-fees":
        from .tokenized_fees import index_tokenized_fees, load_inventory
        inventory = json.loads(args.inventory.read_text()) if args.inventory else load_inventory()
        result = index_tokenized_fees(conn, inventory, args.chain, args.from_block,
            args.to_block, args.before_timestamp, args.chunk_size, args.max_vaults,
            confirmations=args.confirmations)
        print(result)
        return 0

    if args.command == "recompute-fees":
        from .fee_recompute import recompute_canonical_fees
        result = recompute_canonical_fees(conn,args.mode,_chains(args.chains) if args.chains else None,args.limit,version=args.version)
        print(result)
        return 0

    if args.command == "index-fees":
        if args.trace_limit is not None and not args.verify_selective:
            raise ValueError("--trace-limit requires --verify-selective")
        report_keys = None
        if args.report_keys:
            report_keys = json.loads(args.report_keys.read_text())
            import re
            if not isinstance(report_keys, list) or any(
                not isinstance(key, list) or len(key) != 3 or
                type(key[0]) is not int or key[0] <= 0 or
                not isinstance(key[1], str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', key[1]) or
                type(key[2]) is not int or key[2] < 0 for key in report_keys):
                raise ValueError('report keys must be [chain_id, tx_hash, report_log_index] triples')
            report_keys = [(key[0], key[1].lower(), key[2]) for key in report_keys]
        if args.canonical:
            count = index_canonical_fees(conn, _chains(args.chains) if args.chains else None,
                                         limit=args.limit, retry_unresolved=args.retry_unresolved, reconstruct_v2=args.reconstruct_v2,
                                         verify_selective=args.verify_selective, trace_limit=args.trace_limit if args.trace_limit is not None else 10, refresh_evidence=args.refresh_evidence, filtered_evidence_only=args.filtered_evidence_only, version=args.version, report_keys=report_keys, verify_execution=args.verify_execution)
            print(f"processed {count} canonical fee reports")
            return 0
        if args.limit is not None or args.retry_unresolved or args.reconstruct_v2 or args.verify_selective or args.refresh_evidence or args.filtered_evidence_only or args.version or args.report_keys or args.verify_execution:
            raise ValueError("Canonical fee options, including --version, require --canonical")
        count = index_v2_fee_mints_from_reports(conn, _chains(args.chains), progress=progress)
        print(f"indexed {count} fee events")
        return 0

    if args.command == "price-fees":
        from .fee_valuation import price_fees
        print(json.dumps(price_fees(conn, before_timestamp=args.before_timestamp, limit=args.limit,
                                   retry_missing=args.retry_missing, refresh=args.refresh)))
        return 0

    if args.command == "price":
        chain_ids = {CHAINS[chain].chain_id for chain in _chains(args.chains)} if args.chains else None
        count = price_unpriced_reports(
            conn,
            limit=args.limit,
            source=args.source,
            fallback=None if args.no_provider_fallback else _fallback_source(args.source),
            retry_missing=args.retry_missing,
            refresh_onchain_fallbacks=args.refresh_onchain_fallbacks,
            chain_ids=chain_ids,
            onchain_fallbacks=not args.no_onchain_fallbacks,
        )
        print(f"priced/recorded {count} token timestamp rows")
        return 0

    if args.command == "price-volume":
        chain_ids = {CHAINS[chain].chain_id for chain in _chains(args.chains)} if args.chains else None
        count = price_unpriced_volume(
            conn,
            limit=args.limit,
            source=args.source,
            fallback=None,
            retry_missing=args.retry_missing,
            refresh_onchain_fallbacks=args.refresh_onchain_fallbacks,
            chain_ids=chain_ids,
            onchain_fallbacks=not args.no_onchain_fallbacks,
        )
        print(f"priced/recorded {count} volume token timestamp rows")
        return 0

    if args.command == "analyze":
        run_id = _run_analysis(conn, args.job, args.price_source,
                               provider_fallback=not args.no_provider_fallback,
                               before_timestamp=args.before_timestamp,
                               deferred_manifest=args.deferred_manifest)
        print(f"analysis run {run_id} complete")
        return 0

    if args.command == "export":
        paths = export_analysis(conn, args.job, args.out, run_id=args.run_id)
        for path in paths:
            print(path)
        return 0

    if args.command == "publish":
        if args.publish_job == "lifetime-yield":
            _, payload_json = publish_lifetime_yield_headline(
                conn,
                redis_url=args.redis_url or os.environ.get("REDIS_URL"),
                key=args.key,
                ttl=args.ttl,
                analysis_run_id=args.analysis_run_id,
                run_id=args.run_id,
                dry_run=args.dry_run,
            )
            print(payload_json)
            return 0
        raise AssertionError(args.publish_job)

    if args.command == "run":
        price_source = _job_price_source(args.job, args.price_source)
        if args.job != "lifetime-yield" and price_source != "defillama":
            raise ValueError("--price-source yearn-prices is currently supported only for lifetime-yield")
        if args.job == "lifetime-yield":
            if event_source == "envio" and args.find_deployment:
                raise ValueError("--find-deployment requires YEARN_DATA_EVENT_SOURCE=rpc")
            validate_report_pricing_policy(
                price_source,
                None if args.no_provider_fallback else _fallback_source(price_source),
                not args.no_onchain_fallbacks,
                require_credentials=True,
            )
        chains = _chains(args.chains)
        if args.job == "lifetime-yield" and event_source == "envio":
            count = discover_from_envio(
                conn,
                chains,
                include_retired=True,
                to_block=args.to_block,
                progress=progress,
            )
        else:
            count = discover(conn, chains, find_deployment=args.find_deployment)
        print(f"discovered/upserted {count} vault rows")
        if args.job == "lifetime-yield":
            if event_source == "envio":
                count = import_reports_from_envio(
                    conn,
                    chains,
                    include_inactive=True,
                    to_block=args.to_block,
                    progress=progress,
                )
            else:
                count = index_all_reports(
                    conn,
                    chains,
                    to_block=args.to_block,
                    chunk_size=args.chunk_size,
                    progress=progress,
                )
            print(f"indexed {count} strategy report logs")
        else:
            count = index_all_volume(conn, chains, to_block=args.to_block, chunk_size=args.chunk_size, progress=progress)
            print(f"indexed {count} volume logs/rows")
        if args.job == "lifetime-yield":
            count = price_unpriced_reports(
                conn,
                limit=args.price_limit,
                source=price_source,
                fallback=None if args.no_provider_fallback else _fallback_source(price_source),
                onchain_fallbacks=not args.no_onchain_fallbacks,
            )
        else:
            count = price_unpriced_volume(
                conn,
                limit=args.price_limit,
                source=price_source,
                fallback=None,
                onchain_fallbacks=not args.no_onchain_fallbacks,
            )
        print(f"priced/recorded {count} token timestamp rows")
        run_id = _run_analysis(conn, args.job, args.price_source, provider_fallback=not args.no_provider_fallback)
        print(f"analysis run {run_id} complete")
        for path in export_analysis(conn, args.job, args.out, run_id=run_id):
            print(path)
        return 0

    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
