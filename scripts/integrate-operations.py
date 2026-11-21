#!/usr/bin/env python3
"""Install the successful-action hook without replacing the deployed server."""
import argparse
import ast
import os
import tempfile
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root',type=Path,default=Path('/opt/pi-dashboard'))
options=parser.parse_args()
server=options.root/'server.py'
text=server.read_text()
hook="dashboard_platform.record_service_state(key,verb,SERVICES)"
if hook not in text:
    if 'import dashboard_platform' not in text:
        raise SystemExit('Install the dashboard platform integration first.')
    marker="   msg='Готово' if p.returncode==0"
    if text.count(marker)!=1:
        raise SystemExit('The deployed action handler has an unknown layout.')
    text=text.replace(marker,"   if p.returncode==0:\n    try:"+hook+
        "\n    except (ValueError,OSError,subprocess.SubprocessError):p.returncode=1\n"+marker,1)
    ast.parse(text)
    with tempfile.NamedTemporaryFile(dir=options.root,delete=False) as new:
        new.write(text.encode())
        os.fchmod(new.fileno(),server.stat().st_mode & 0o777)
        staged=new.name
    os.replace(staged,server)
print('Boot-service preferences hook integrated.')
