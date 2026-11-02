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
        assert page.title() == 'CarThesis'
        assert page.get_by_role('heading', name='CarThesis', exact=True).is_visible()
        page.get_by_role('heading', name='What changes the decision').wait_for()
        assert page.locator('#error').is_hidden()
        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
            page.locator('#view').screenshot(path=str(args.screenshots / 'ownership-overview.png'))
        page.get_by_role('button', name='Local price index', exact=True).click()
        assert '3.70%' in page.locator('#view').inner_text()
        if args.screenshots:
            page.locator('#view .card').screenshot(path=str(args.screenshots / 'local-price-index.png'))
        for tab in ['Ownership costs', 'Gas vs hybrid', 'Loan & prepay', 'APR crossover', 'Assumptions']:
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
        check_regressions(page, Path(folder))
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert not failures, failures
        context.close()
        browser.close()
    print('Seven views, safe imports, saved settings, loan rollback, stale-score removal, and mobile layout passed')


def save_comparison(page, path):
    with page.expect_download() as download:
        page.get_by_role('button', name='Save comparison', exact=True).click()
    download.value.save_as(path)
    return json.loads(path.read_text())


def load_comparison(page, path):
    page.locator('#import').set_input_files(path)
    page.wait_for_function('document.getElementById("import").value === ""')
    assert page.locator('#error').is_hidden(), page.locator('#error').inner_text()


def check_regressions(page, folder):
    def tab(name):
        page.get_by_role('button', name=name, exact=True).click()

    def change(label, value):
        field = page.get_by_label(label, exact=True)
        field.fill(str(value))
        field.press('Tab')

    def reset():
        page.get_by_role('button', name='Reset synthetic demo').click()
        tab('Overview')

    # A blur commit must not replace the sidebar input the buyer moves to next.
    reset()
    daily = page.get_by_label('Miles per day', exact=True)
    years = page.get_by_label('Ownership years', exact=True)
    for miles, horizon, move in [(42, 4, 'keyboard'), (43, 6, 'mouse')]:
        daily.fill(str(miles))
        if move == 'keyboard':
            daily.press('Tab')
        else:
            years.click()
        assert years.evaluate('el => el === document.activeElement'), move
        page.keyboard.press('ControlOrMeta+A')
        page.keyboard.insert_text(str(horizon))
        page.keyboard.press('Tab')
        saved = save_comparison(page, folder / f'sequential-{move}.json')
        assert saved['assumptions']['daily'] == miles
        assert saved['assumptions']['years'] == horizon

    # Both settings must survive reload and replace another session's settings.
    reset()
    page.get_by_role('button', name='resale', exact=True).click()
    assert '$52,533' in page.locator('#view .kpis').inner_text()
    tab('APR crossover')
    change('Extra savings required to choose used ($)', 2000)
    expected_cross = page.locator('#view table').first.inner_text()
    target = folder / 'settings.json'
    saved = save_comparison(page, target)
    assert 'resale' not in saved['enabled'] and saved['preferencePremium'] == 2000
    page.reload()
    load_comparison(page, target)
    for _ in range(2):
        tab('Overview')
        assert '$52,533' in page.locator('#view .kpis').inner_text()
        assert page.get_by_role('button', name='resale', exact=True).get_attribute('aria-pressed') == 'false'
        tab('APR crossover')
        assert page.get_by_label('Extra savings required to choose used ($)', exact=True).input_value() == '2000'
        assert page.locator('#view table').first.inner_text() == expected_cross
        page.get_by_role('button', name='resale', exact=True).click()
        change('Extra savings required to choose used ($)', 3500)
        load_comparison(page, target)
    # Version 1 files without the new settings also replace session state.
    load_comparison(page, ROOT / 'examples/comparison.json')
    assert page.get_by_label('Extra savings required to choose used ($)', exact=True).input_value() == '0'
    tab('Overview')
    assert '$33,933' in page.locator('#view .kpis').inner_text()
    assert page.get_by_role('button', name='resale', exact=True).get_attribute('aria-pressed') == 'true'

    # Reject an invalid common term before it can break a different view or export.
    tab('Loan & prepay')
    used = saved['vehicles'][2]['id']
    page.get_by_label('Loan vehicle', exact=True).select_option(used)
    change('Monthly payment ($)', 600)
    change('Loan term (months)', 36)
    assert 'contractual minimum' in page.locator('#error').inner_text()
    assert page.get_by_label('Loan term (months)', exact=True).input_value() == '60'
    change('Monthly payment ($)', 1)
    assert 'contractual minimum' in page.locator('#error').inner_text()
    change('Monthly payment ($)', 650)
    recovered = save_comparison(page, folder / 'recovered-payment.json')
    assert next(v for v in recovered['vehicles'] if v['id'] == used)['payment'] == 650
    change('Monthly payment ($)', 600)
    tab('Overview')
    assert page.locator('#error').is_hidden()
    assert page.get_by_role('heading', name='What changes the decision').is_visible()
    valid = folder / 'valid-after-rejection.json'
    result = save_comparison(page, valid)
    assert result['assumptions']['term'] == 60
    assert next(v for v in result['vehicles'] if v['id'] == used)['payment'] == 600
    load_comparison(page, valid)
    # Rollback uses the most recent valid value after successive sidebar edits.
    change('Miles per day', 40)
    change('Miles per day', -1)
    assert page.get_by_label('Miles per day', exact=True).input_value() == '40'

    reset()
    tab('Assumptions')
    page.get_by_role('button', name='Edit', exact=True).nth(2).click()
    page.get_by_label('Body', exact=True).fill('SUV')
    page.get_by_role('button', name='Save vehicle', exact=True).click()
    page.locator('dialog').wait_for(state='detached')
    tab('Local price index')
    assert '3.70%' not in page.locator('#view').inner_text()
    result = save_comparison(page, folder / 'edited-body.json')
    car = next(v for v in result['vehicles'] if v['id'] == used)
    assert car['body'] == 'SUV' and 'dealEdge' not in car and 'marketSource' not in car
    reset()


if __name__ == '__main__':
    main()
