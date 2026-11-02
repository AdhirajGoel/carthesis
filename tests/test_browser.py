#!/usr/bin/env python3
"""Isolated browser verification using synthetic data only."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--screenshots', type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as folder, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1450, 'height': 1000}, device_scale_factor=1)
        page = context.new_page()
        failures = []
        page.on('pageerror', lambda error: failures.append(str(error)))
        page.goto((ROOT / 'web/index.html').as_uri())
        page.get_by_role('heading', name='What changes the decision').wait_for()
        assert page.locator('#error').is_hidden()
        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(args.screenshots / 'ownership-overview.png'), full_page=True)
        page.get_by_role('button', name='Local price index', exact=True).click()
        assert '3.70%' in page.locator('#view').inner_text()
        if args.screenshots:
            page.screenshot(path=str(args.screenshots / 'local-price-index.png'), full_page=True)
        for tab in ['Ownership costs', 'Gas vs hybrid', 'Loan & prepay', 'Assumptions']:
            page.get_by_role('button', name=tab, exact=True).click()
            assert page.locator('#error').is_hidden(), page.locator('#error').inner_text()
        page.get_by_role('button', name='Edit', exact=True).first.click()
        page.get_by_label('Purchase price', exact=True).fill('31000')
        page.get_by_role('button', name='Save vehicle', exact=True).click()
        page.locator('dialog').wait_for(state='detached')
        with page.expect_download() as download:
            page.get_by_role('button', name='Save comparison', exact=True).click()
        target = Path(folder) / 'comparison.json'
        download.value.save_as(target)
        saved = json.loads(target.read_text())
        assert saved['vehicles'][0]['price'] == 31000
        saved['vehicles'][0]['name'] = '<img src=x onerror="window.pwned=1">'
        target.write_text(json.dumps(saved))
        page.locator('#import').set_input_files(target)
        page.wait_for_function('document.getElementById("import").value === ""')
        assert page.get_by_text('<img src=x onerror="window.pwned=1">', exact=True).count() >= 1
        assert page.evaluate('window.pwned') is None
        assert page.locator('#view img').count() == 0
        invalid = Path(folder) / 'invalid.json'
        saved['vehicles'][0]['mpg'] = 0
        invalid.write_text(json.dumps(saved))
        page.locator('#import').set_input_files(invalid)
        page.locator('#error').wait_for(state='visible')
        assert 'MPG' in page.locator('#error').inner_text()
        page.get_by_role('button', name='Reset synthetic demo').click()
        page.get_by_role('button', name='Overview', exact=True).click()
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert not failures, failures
        context.close()
        browser.close()
    print('All six views, edit/save, safe imports, invalid inputs, and mobile layout passed')


if __name__ == '__main__':
    main()
