"""Explicit retirement of local parity guesses; no acquisition or database writes."""
import json
import re

PRICE_EVIDENCE_POLICY = 'observed-or-contract-v1'
RETIRED_PARITY_SOURCES = (
    'canonical_polygon_stable_fallback', 'canonical_arbitrum_stable_fallback',
    'canonical_ethereum_stable_fallback', 'canonical_katana_usdc_wrapper_fallback',
    'canonical_polygon_weth_fallback', 'canonical_katana_eth_wrapper_fallback',
    'canonical_eth_equivalent_fallback', 'canonical_btc_equivalent_fallback',
    'canonical_slink_link_fallback', 'canonical_saave_aave_fallback', 'ycrv_crv_par_fallback',
)
CONTRACT_SOURCE_NAMES = (
    'exchange_rate_wrapper_fallback', 'yearn_v1_vault_fallback', 'aave_atoken_fallback',
    'curve_lp_fallback', 'curve_lp_balance_fallback', 'erc4626_wrapper_fallback',
    'balancer_bpt_fallback', 'uniswap_v2_pair_fallback', 'curve_get_dy_fallback',
    'crv_derivative_curve_lp_fallback',
)


def quote_classification(evidence):
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except (TypeError, ValueError):
            evidence = {}
    pending = [evidence]
    derived = False
    observed = False
    provider_quote = False
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if item.get('source') in RETIRED_PARITY_SOURCES:
                return 'estimated'
            derived |= item.get('source') in CONTRACT_SOURCE_NAMES or item.get('provider') == 'local-adapter'
            observed |= bool(item.get('matched'))
            provider_quote |= item.get('provider') in ('defillama', 'yearn-prices')
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return ('derived' if derived else 'observed' if observed else
            'provider-quote' if provider_quote else 'legacy-unclassified')


def retired_estimate_predicate(column):
    """Match actual JSON source labels at any depth, not escaped audit JSON strings.

    Source labels and SQL identifiers are controlled by the application. A text
    predicate also tolerates legacy malformed metadata without a JSON cast error.
    """
    if not re.fullmatch(r'[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*', column):
        raise ValueError('expected an internal table.column identifier')
    labels = '|'.join(RETIRED_PARITY_SOURCES)
    pattern = '"source"[[:space:]]*:[[:space:]]*"('+labels+')"'
    return f"COALESCE({column}, '') ~ '{pattern}'"
