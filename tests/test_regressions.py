#!/usr/bin/env python3
"""Offline checks for report identity and shared credential loading."""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import history_check as history
import vin_tools


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


if __name__ == '__main__':
    unittest.main()
