#!/usr/bin/env python3
"""Validate and extract a downloaded encrypted backup set into a new directory."""
import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('set',type=Path)
parser.add_argument('destination',type=Path)
parser.add_argument('--identity',type=Path,required=True)
parser.add_argument('--dashboard-root',type=Path,default=Path('/opt/pi-dashboard'))
args=parser.parse_args()
sys.path.insert(0,str(args.dashboard_root))
import usb_backup
from maintenance_restore import extract

if args.destination.exists() or args.destination.is_symlink():
    parser.error('The destination must not exist.')
manifest=usb_backup.verify_set(args.set)
target=args.destination
with tempfile.TemporaryDirectory(dir=target.parent,prefix='.pi-restore-') as working:
    root=Path(working)/'contents'
    root.mkdir(mode=0o700)
    limits={'entries':0,'skipped':0,'files':0,
            'remaining':max(0,shutil.disk_usage(root).free-256*1024**2)}
    for archive,parts in zip(usb_backup.FILES,({'home'},{'services','postgres'})):
        extract(args.set/archive,args.identity,root,parts,manifest['home'],limits)
    if not limits['files']:
        parser.error('The selected set contains no extractable files.')
    os.rename(root,target)
print(f"Verified and extracted {limits['files']} files. Skipped {limits['skipped']} links or special entries.")
