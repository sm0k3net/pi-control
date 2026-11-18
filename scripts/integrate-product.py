#!/usr/bin/env python3
"""Register the product UI in a Pi Control source tree or installed dashboard."""
import argparse
import ast
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('root', type=Path)
root = parser.parse_args().root
server = root / 'server.py'
page = root / 'index.html'
source = server.read_text()
html = page.read_text()

if 'import product\n' not in source:
    get = ' def do_GET(self):\n'
    post = '  if self.path.startswith(\'/api/vault/\'):\n'
    assert get in source and post in source
    source = source.replace('import vault\n', 'import vault\nimport product\n', 1)
    assert 'import product\n' in source
    source = source.replace(get, get + "  if self.path in ('/product.js','/product.css','/product-en.json','/api/product/preferences','/api/product/update'):return product.handle_get(self)\n", 1)
    source = source.replace(post, "  if self.path.startswith('/api/product/'):return product.handle_post(self,data)\n" + post, 1)
    source = source.replace("self.path=='/api/vault/import' else", "self.path in ('/api/vault/import','/api/product/vault-restore') else")
    ast.parse(source)
    server.write_text(source)

if '/product.js' not in html:
    assert '</head>' in html and '</body>' in html
    html = html.replace('</head>', '<link rel="stylesheet" href="/product.css"></head>', 1)
    html = html.replace('</body>', '<script src="/product.js"></script></body>', 1)
    page.write_text(html)
