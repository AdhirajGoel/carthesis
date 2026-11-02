#!/usr/bin/env python3
"""Offline checks; fixtures and outputs stay in temporary directories."""
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vehicle_reference import key
from add_deal_edge import calculate_scores
from search import write_workbook, spreadsheet_value, find_master_workbook, append_new_cars
from lotlogic import export_comparison


def main():
    assert spreadsheet_value('=HYPERLINK("https://example.test")').startswith("'")
    assert spreadsheet_value(-200) == -200
    env = dict(os.environ, LOTLOGIC_REFERENCE=str(ROOT / 'examples/reference.json'))
    env.pop('MARKETCHECK_API_KEY', None)
    for name in ['search', 'search_private', 'vehicle_reference', 'CarPlay_filter', 'add_deal_edge', 'update_shortlist', 'vin_search', 'vin_tools', 'history_check']:
        subprocess.run([sys.executable, str(ROOT / (name + '.py')), '--self-test'], env=env, check=True)
    data = json.loads((ROOT / 'examples/local-listings.json').read_text())
    scores = calculate_scores([{key(k): v for k, v in row.items()} for row in data['listings']], tuple(data['catalog']), data['run_year'])
    assert scores[data['listings'][0]['vin']] == 3.7
    with TemporaryDirectory() as folder:
        folder = Path(folder)
        master = find_master_workbook(folder / 'fresh')
        candidate = dict(data['listings'][0], make='Honda', model='Civic')
        assert len(append_new_cars(master, [candidate])) == 1
        assert append_new_cars(master, [candidate]) == []
        workbook = folder / 'candidates.xlsx'
        for row in data['listings']:
            row['deal_edge_pct'] = scores[row['vin']]
        data['listings'][0]['seller'] = '=1+1'
        write_workbook(workbook, data['listings'], ('Example',), extra_fields=('deal_edge_pct',))
        from openpyxl import load_workbook
        check = load_workbook(workbook, data_only=False)
        col = [c.value for c in check['Example'][1]].index('seller') + 1
        assert check['Example'].cell(2, col).data_type == 's'
        check.close()
        export_comparison(workbook, ROOT / 'examples/comparison.json', folder / 'comparison.json')
        result = json.loads((folder / 'comparison.json').read_text())
        assert result['vehicles'][2]['price'] == 26000
        assert result['vehicles'][2]['dealEdge'] == 3.7
    subprocess.run([sys.executable, str(ROOT / 'tests/test_regressions.py')], env=env, check=True)
    subprocess.run(['node', str(ROOT / 'tests/test_model.js')], check=True)
    print('All offline checks passed')


if __name__ == '__main__':
    main()
