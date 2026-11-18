#!/usr/bin/env python3
"""Build the fixed-format bundle consumed by the dashboard updater."""
import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1] / 'src'
parser = argparse.ArgumentParser()
parser.add_argument('output', type=Path)
output = parser.parse_args().output
catalog = json.loads((root / 'product-en.json').read_text())
if not isinstance(catalog, dict):
    raise SystemExit('Translation catalog must be an object.')
with ZipFile(output, 'w', compression=ZIP_DEFLATED) as bundle:
    for name in ('product.js', 'product.css', 'product-en.json'):
        bundle.write(root / name, name)
