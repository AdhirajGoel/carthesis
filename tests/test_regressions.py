#!/usr/bin/env python3
"""Offline regressions for report identity, comparison exports, and workbook safety."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import history_check as history
import vin_tools
import carthesis
import update_shortlist
from add_deal_edge import add_deal_edge, calculate_scores
from openpyxl import load_workbook
from search import write_workbook
from vehicle_reference import key, match_current_vehicle

ROOT = Path(__file__).resolve().parents[1]


class ReleaseRegressions(unittest.TestCase):
    vin = '1TEST000000000012'
    other = '1TEST000000000099'

    def report(self, identity, url_vin=None):
        url = f'https://www.carfax.com/vehiclehistory/{url_vin or self.vin}'
        page = Mock(url=url)
        context = Mock()
        context.new_page.return_value = page
        text = f'CARFAX Vehicle History Report\n{identity}\nOwner 1\nNo Accidents or Damage Reported'
        with patch.object(history, 'page_content', return_value=(text, '')):
            result = history.check_car(context, {'vin': self.vin, 'listing_url': 'https://example.test/car'}, url)
        page.close.assert_called_once()
        return result

    def test_matching_report(self):
        result = self.report(f'VIN: {self.vin}')
        self.assertEqual(result['history_status'], 'available')
        self.assertEqual(result['history_verified_vin'], self.vin)
        self.assertEqual(result['owner_count'], 1)
        self.assertEqual(result['reported_accidents'], 'none reported')

    def test_wrong_report(self):
        result = self.report(f'VIN: {self.other}', self.other)
        self.assertEqual(result['history_status'], 'vin_mismatch')
        self.assertEqual(result['owner_count'], '')
        self.assertEqual(result['reported_accidents'], 'unknown')

    def test_url_alone_does_not_verify_vehicle(self):
        result = self.report('No vehicle identity supplied')
        self.assertEqual(result['history_status'], 'vin_unverified')
        self.assertEqual(result['reported_accidents'], 'unknown')

    def test_ambiguous_or_conflicting_identity(self):
        for text, url in [(f'VIN: {self.vin}\nVIN: {self.other}', self.vin), (f'VIN: {self.vin}', self.other)]:
            with self.subTest(text=text):
                self.assertEqual(self.report(text, url)['history_status'], 'vin_mismatch')

    def test_unverified_old_cache_cannot_supply_facts(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'cache.json'
            record = self.report(f'VIN: {self.vin}')
            history.save_cache(path, {self.vin: record})
            self.assertEqual(history.load_cache(path)[self.vin]['history_status'], 'available')
            del record['history_verified_vin']
            history.save_cache(path, {self.vin: record})
            result = history.load_cache(path)[self.vin]
            self.assertEqual(result['history_status'], 'vin_unverified')
            self.assertEqual(result['reported_accidents'], 'unknown')

    def test_vin_run_uses_shared_key_loader(self):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder / 'cars.csv'
            source.write_text(f'vin\n{self.vin}\n')
            output = folder / 'output.jsonl'
            args = argparse.Namespace(limit=1, output=output, export_only=False, csv=source, refresh=False, tool='basic', yes=True)
            with patch.object(vin_tools, 'RESULTS', folder), patch.object(vin_tools, 'load_api_key', return_value='synthetic-key') as key, patch.object(vin_tools, 'fetch', return_value=({'make': 'Example'}, {})) as fetch:
                self.assertEqual(vin_tools.run(args), 0)
                key.assert_called_once_with()
                fetch.assert_called_once_with('basic', self.vin, 'synthetic-key')
                key.side_effect = RuntimeError('No API key found')
                with self.assertRaisesRegex(RuntimeError, 'No API key found'):
                    vin_tools.run(args)
            self.assertNotIn('synthetic-key', output.read_text())
            self.assertEqual(json.loads(output.read_text())['vin'], self.vin)


class WorkbookRegressions(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.data = json.loads((ROOT / 'examples/local-listings.json').read_text())
        self.state = json.loads((ROOT / 'examples/comparison.json').read_text())
        self.catalog = tuple(self.data['catalog'])

    def workbook(self, name='candidates.xlsx', price=None):
        rows = deepcopy(self.data['listings'])
        if price is not None:
            rows[0]['price'] = price
        path = self.folder / name
        write_workbook(path, rows, ('Example',))
        return path

    def test_invalid_export_preserves_saved_comparison(self):
        workbook = self.workbook(price=28000)
        for index, change, message in [(2, {'payment': 550}, 'contractual minimum'), (0, {'mpg': 0}, 'MPG')]:
            with self.subTest(change=change):
                state = deepcopy(self.state)
                state['vehicles'][index].update(change)
                saved = self.folder / 'comparison.json'
                saved.write_text(json.dumps(state))
                before = saved.read_bytes()
                with self.assertRaisesRegex(ValueError, message):
                    carthesis.export_comparison(workbook, saved, saved)
                self.assertEqual(saved.read_bytes(), before)

    def test_unavailable_validator_preserves_saved_comparison(self):
        workbook = self.workbook(price=28000)
        saved = self.folder / 'comparison.json'
        saved.write_text(json.dumps(self.state))
        before = saved.read_bytes()
        with patch.object(carthesis.subprocess, 'run', side_effect=FileNotFoundError('Node unavailable')):
            with self.assertRaisesRegex(OSError, 'Node unavailable'):
                carthesis.export_comparison(workbook, saved, saved)
        self.assertEqual(saved.read_bytes(), before)

    def test_valid_same_file_export_keeps_buyer_settings(self):
        workbook = self.workbook(price=28000)
        self.state.update(enabled=['purchase', 'fuel'], preferencePremium=2000)
        self.state['vehicles'][2]['payment'] = 600
        saved = self.folder / 'comparison.json'
        saved.write_text(json.dumps(self.state))
        carthesis.export_comparison(workbook, saved, saved)
        result = json.loads(saved.read_text())
        self.assertEqual(result['vehicles'][2]['price'], 28000)
        self.assertEqual(result['vehicles'][2]['payment'], 600)
        self.assertEqual(result['enabled'], ['purchase', 'fuel'])
        self.assertEqual(result['preferencePremium'], 2000)

    def test_conflicting_sheet_inputs_preserve_workbook(self):
        for field, value in [('price', 24000), ('body_type', 'SUV'), ('seller', 'Changed dealer'), ('miles', 41000)]:
            with self.subTest(field=field):
                path = self.workbook()
                book = load_workbook(path)
                sheet = book['Example']
                headers = [cell.value for cell in sheet[1]]
                sheet.cell(2, headers.index(field) + 1).value = value
                book.save(path)
                book.close()
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, 'Conflicting.*VIN'):
                    add_deal_edge(path, catalog=self.catalog, run_year=self.data['run_year'])
                self.assertEqual(path.read_bytes(), before)

    def test_conflicting_scored_workbook_cannot_replace_comparison(self):
        path = self.workbook()
        add_deal_edge(path, catalog=self.catalog, run_year=self.data['run_year'])
        book = load_workbook(path)
        sheet = book['Example']
        headers = [cell.value for cell in sheet[1]]
        sheet.cell(2, headers.index('price') + 1).value = 24000
        book.save(path)
        book.close()
        saved = self.folder / 'comparison.json'
        saved.write_text(json.dumps(self.state))
        before = saved.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Conflicting.*VIN'):
            carthesis.export_comparison(path, saved, saved)
        self.assertEqual(saved.read_bytes(), before)

    def test_shortlist_flags_can_differ_without_invalidating_score(self):
        path = self.workbook()
        book = load_workbook(path)
        book['Example']['A2'] = 1
        book.save(path)
        book.close()
        add_deal_edge(path, catalog=self.catalog, run_year=self.data['run_year'])
        book = load_workbook(path)
        try:
            for name in ('All', 'Example'):
                sheet = book[name]
                headers = [cell.value for cell in sheet[1]]
                self.assertEqual(sheet.cell(2, headers.index('deal_edge_pct') + 1).value, 3.7)
            self.assertEqual(book['Example']['A2'].value, 1)
        finally:
            book.close()

    def shortlist(self):
        car = dict(self.data['listings'][0], make='Honda', shortlist=1)
        write_workbook(self.folder / 'candidates-Master.xlsx', [car], ('Honda',))
        path = self.folder / 'Shortlist.xlsx'
        write_workbook(path, [car], ('Honda',))
        history.apply_to_workbook(path, {car['vin']: history.outcome('not_provided', 'Synthetic saved evidence')})
        return path

    def test_malformed_history_preserves_shortlist(self):
        path = self.shortlist()
        before = path.read_bytes()
        (self.folder / 'history-reports.json').write_text('{')
        with self.assertRaisesRegex(RuntimeError, 'Cannot read'):
            update_shortlist.rebuild(self.folder, path)
        self.assertEqual(path.read_bytes(), before)

    def test_failed_history_enrichment_preserves_shortlist(self):
        path = self.shortlist()
        before = path.read_bytes()
        history.save_cache(self.folder / 'history-reports.json', {})
        with patch.object(history, 'apply_to_workbook', side_effect=OSError('Synthetic save failure')):
            with self.assertRaisesRegex(OSError, 'Synthetic save failure'):
                update_shortlist.rebuild(self.folder, path)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()),
                         ['Shortlist.xlsx', 'candidates-Master.xlsx', 'history-reports.json'])

    def test_incompatible_or_missing_body_cannot_claim_local_score(self):
        for body in ('SUV', 'Hatchback', '', None, 'unknown'):
            with self.subTest(body=body):
                rows = deepcopy(self.data['listings'])
                rows[0]['body_type'] = body
                self.assertIsNone(match_current_vehicle(rows[0], self.catalog))
                scores = calculate_scores([{key(k): v for k, v in row.items()} for row in rows],
                                          self.catalog, self.data['run_year'])
                self.assertIsNone(scores[rows[0]['vin']])

    def test_known_body_evidence_still_matches(self):
        for body in ('Sedan', '4-Door Sedan'):
            car = dict(self.data['listings'][0], body_type=body)
            self.assertEqual(match_current_vehicle(car, self.catalog)['body'], 'Sedan')
        suv = dict(self.catalog[0], body='SUV')
        car = dict(self.data['listings'][0], body_type='SUV')
        self.assertEqual(match_current_vehicle(car, (suv,))['body'], 'SUV')


if __name__ == '__main__':
    unittest.main()
