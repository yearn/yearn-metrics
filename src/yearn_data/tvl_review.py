"""Offline TVL review from captured JSON evidence, using the shared accounting."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .tvl_api import TvlDataset, encoded


class ReviewDataset(TvlDataset):
    def __init__(self, manifest, timestamp, rows, positions):
        manifest = deepcopy(manifest)
        manifest.pop('diagnostics', None)
        manifest.pop('datasetId', None)
        manifest.update(asOfTimestamp=timestamp)
        self.evidence = {'manifest': manifest, 'rows': deepcopy(rows), 'positions': deepcopy(positions)}
        identity = hashlib.sha256(encoded(self.evidence).encode()).hexdigest()
        super().__init__(manifest | {'datasetId': identity})
        points = deepcopy(rows)
        for row in points:
            # Dated migration output adds a target even when the observation
            # had none. Preserve unmarked targets acquired as raw evidence.
            if row.get('bridge_migration_timestamp') is not None:
                row.pop('bridge_target_chain', None)
            for field in ('overlap_usd','external_tvl_usd','bridge_excluded_usd','bridge_migration_timestamp'):
                row.pop(field, None)
        points = [self.validate_price(row) for row in points]
        positions = deepcopy(positions)
        for position in positions:
            position.pop('accounting_excluded', None)
            if position.get('reason') in ('curation_separate_product','bridge_parent_excluded','endpoint_outside_selection'):
                position.pop('reason')
        self.frame = self.account_frame(timestamp, points, positions)

    def dates(self):
        return (self.as_of(),)

    def history(self, group='chain', mode='external', interval='weekly', start=None, end=None, chain_id=None, constant=False):
        raise ValueError('review evidence contains a single captured close; use graph() or summary()')

    def history_prepared(self):
        return False

    def iter_frames(self, timestamps, chain_id=None, include_curation=False, include_retired=True):
        if include_curation:
            raise ValueError('review frames retain the captured publication accounting policy')
        for timestamp in timestamps:
            if timestamp != self.as_of():
                raise ValueError('review evidence contains only the captured close')
            rows, positions, diagnostics = self.frame
            rows = [r for r in rows if (chain_id is None or r['chain_id'] == chain_id)
                    and (include_retired or self.catalog.get((r['chain_id'],r['vault'].lower()),{}).get('active',1))]
            yield timestamp, rows, positions, diagnostics

    def save(self, out):
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        (out/'frame.json').write_text(encoded(self.evidence)+'\n')

    @classmethod
    def load(cls, path):
        evidence = json.loads(Path(path).read_text())
        manifest = dict(evidence['manifest'])
        manifest.pop('datasetId', None)
        return cls(manifest, manifest['asOfTimestamp'], evidence['rows'], evidence['positions'])
