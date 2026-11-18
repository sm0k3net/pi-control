#!/usr/bin/env python3
import argparse
import hashlib
import json
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

source = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(source))
from maintenance_bundle import RELEASE_FILES

parser = argparse.ArgumentParser()
parser.add_argument('output', type=Path)
parser.add_argument('--version', required=True)
args = parser.parse_args()
payloads = {name: (source / name).read_bytes() for name in RELEASE_FILES}
manifest = {'format': 1, 'version': args.version,
            'files': {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}}
with ZipFile(args.output, 'w', compression=ZIP_DEFLATED) as archive:
    archive.writestr('manifest.json', json.dumps(manifest))
    for name, data in payloads.items():
        archive.writestr(name, data)
