#!/usr/bin/env python3
"""Local setup and the bridge from candidate workbooks to ownership comparisons."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def initialize() -> None:
    (ROOT / 'data').mkdir(exist_ok=True)
    (ROOT / 'results').mkdir(exist_ok=True)
    for name in ('reference.json', 'comparison.json'):
        target = ROOT / 'data' / name
        if not target.exists():
            shutil.copyfile(ROOT / 'examples' / name, target)
    print('Local data directory ready. Existing files were preserved.')
    print('Open web/index.html for the synthetic demo. Research data/reference.json before live searches.')


def export_comparison(workbook: Path, assumptions: Path, output: Path) -> None:
    from openpyxl import load_workbook
    from add_deal_edge import validate_scoring_inputs
    from search import atomic_text
    state = json.loads(assumptions.read_text())
    if not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('vehicles'), list):
        raise ValueError('Use the version 1 comparison format in examples/comparison.json')
    book = load_workbook(workbook, read_only=True, data_only=True)
    records = {}
    try:
        validate_scoring_inputs(book)
        # Brand sheets contain actual shortlist values; All may contain uncached formulas.
        sheets = [s for s in book.worksheets if s.title != 'All'] or book.worksheets
        for sheet in sheets:
            rows = sheet.iter_rows(values_only=True)
            headers = next(rows, ())
            if 'vin' not in headers or 'price' not in headers:
                continue
            for row in rows:
                record = dict(zip(headers, row))
                vin = str(record.get('vin') or '').upper()
                if vin:
                    if vin in records and records[vin].get('price') != record.get('price'):
                        raise ValueError(f'Conflicting rows for VIN {vin}')
                    records[vin] = record
    finally:
        book.close()
    matched = 0
    for vehicle in state['vehicles']:
        if not isinstance(vehicle, dict):
            raise ValueError('Each comparison vehicle must be an object')
        vin = vehicle.get('vin')
        if not vin:
            continue  # New-car quotes or manually entered comparisons do not require a listing VIN.
        row = records.get(str(vin).upper())
        if row is None:
            raise ValueError(f'Comparison VIN {vin} is absent from the candidate workbook')
        price = row.get('price')
        if not isinstance(price, (int, float)) or price <= 0:
            raise ValueError(f'Invalid listing price for VIN {vin}')
        vehicle['price'] = price
        vehicle['dealEdge'] = row.get('deal_edge_pct')
        vehicle['marketSource'] = 'Local candidate workbook; seller-balanced five-peer comparison'
        matched += 1
    if not matched:
        raise ValueError('Set a comparison vehicle’s vin to a candidate VIN before exporting')
    payload = json.dumps(state, indent=2, allow_nan=False) + '\n'
    # Use the browser's model so a refreshed price cannot save an invalid loan.
    validation = subprocess.run([
        'node', '-e',
        "try { const M=require(process.argv[1]); "
        "const s=M.validate(JSON.parse(require('node:fs').readFileSync(0,'utf8'))); "
        "s.vehicles.forEach(v=>M.compute(v,s.assumptions,s.enabled)); "
        "} catch(error) { process.stderr.write(error.message); process.exitCode=1; }",
        str(ROOT / 'web/model.js'),
    ], input=payload, text=True, capture_output=True)
    if validation.returncode:
        raise ValueError(f'Comparison rejected: {validation.stderr.strip()}')
    atomic_text(output, payload)
    print(f'Updated {matched} vehicle(s); open {output.name} using Open comparison JSON in the app.')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init', help='Create local example configuration without making API calls')
    export = sub.add_parser('compare', help='Attach listing prices and deal scores to researched TCO assumptions')
    export.add_argument('workbook', type=Path)
    export.add_argument('--assumptions', type=Path, default=ROOT / 'data' / 'comparison.json')
    export.add_argument('--output', type=Path, default=ROOT / 'data' / 'comparison.json')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            initialize()
        else:
            export_comparison(args.workbook, args.assumptions, args.output)
    except (OSError, ValueError, KeyError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
