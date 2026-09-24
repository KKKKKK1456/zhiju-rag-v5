"""Offline tests for the delivery UI, without importing model/runtime code."""
import pathlib
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.checkboxes = []
        self.asset_urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if attrs.get('type') == 'checkbox':
            self.checkboxes.append(attrs)
        if tag in ('script', 'link'):
            self.asset_urls.append(attrs.get('src', attrs.get('href', '')))


class DeliveryUITests(unittest.TestCase):
    def test_unique_ids_and_unchecked_consent(self):
        html = (ROOT / 'preview/index.html').read_text()
        page = Elements()
        page.feed(html)
        self.assertEqual(len(page.ids), len(set(page.ids)))
        self.assertTrue(all('checked' not in x for x in page.checkboxes))
        self.assertTrue(all(x.startswith('/') for x in page.asset_urls))
        self.assertIn('12000', html)
        self.assertIn('20 条', html)
        self.assertIn('v5 体验版', html)

    def test_dom_contract_and_no_unsafe_or_persistent_storage(self):
        html = (ROOT / 'preview/index.html').read_text()
        js = (ROOT / 'preview/preview.js').read_text()
        page = Elements()
        page.feed(html)
        for element in re.findall(r"\$\('([^']+)'\)", js):
            self.assertIn(element, page.ids)
        for prohibited in ('innerHTML', 'localStorage', 'sessionStorage', 'eval('):
            self.assertNotIn(prohibited, js)
        self.assertIn("'X-V5-Session':token", js)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for browser-independent UI fixtures')
    def test_fixture_interactions(self):
        result = subprocess.run(['node', str(ROOT / 'tests/preview_delivery_harness.cjs')], cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for JS syntax validation')
    def test_javascript_syntax(self):
        result = subprocess.run(['node', '--check', str(ROOT / 'preview/preview.js')], cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
