#!/usr/bin/env python3
"""Install checksum-pinned Apple Remote Desktop browser modules."""
import argparse
import hashlib
import io
import os
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

ARCHIVES = (
    ('peetinc/noVNC-ARD','d699293635083c9e0c30c8f9480993865653772a',
     'fce4598a53b3fd63ae7f9efcc2485e975c5c9ca83e311a0f0d60dc64ca358546',
     '','ard'),
    ('novnc/noVNC','8e1ebdffba02e651c399dacef841f8941f6ad6e4',
     '94e34d45222366de86a4e9eae82d558ae008f672f614ce0407d2aba3c068ca91',
     'noVNC','core vendor'),
)
LIMIT = 24 * 1024 * 1024


def install(root):
    destination = root / 'novnc-ard'
    if destination.exists() or destination.is_symlink():
        raise ValueError('novnc-ard already exists.')
    with tempfile.TemporaryDirectory(dir=root) as work:
        stage = Path(work) / 'novnc-ard'
        stage.mkdir()
        for repo, commit, checksum, subdir, dirs in ARCHIVES:
            url = f'https://codeload.github.com/{repo}/tar.gz/{commit}'
            with urllib.request.urlopen(url,timeout=35) as response:
                payload = response.read(LIMIT + 1)
            if len(payload) > LIMIT or hashlib.sha256(payload).hexdigest() != checksum:
                raise ValueError(f'Archive checksum mismatch for {repo}.')
            with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as archive:
                for member in archive:
                    rel = PurePosixPath(*PurePosixPath(member.name).parts[1:])
                    if not member.isfile() or not rel.parts or '..' in rel.parts:
                        continue
                    script = rel.parts[0] in dirs.split() and rel.suffix == '.js' and 'tests' not in rel.parts
                    if not script and rel.as_posix() not in ('LICENSE','LICENSE.txt','README.md'):
                        continue
                    if member.size > 512 * 1024:
                        raise ValueError('Unexpected browser asset size.')
                    path = (stage/subdir).joinpath(*rel.parts)
                    path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_bytes(archive.extractfile(member).read())
                    path.chmod(0o644)
        if not (stage/'ard/ard-patch.js').is_file() or not (stage/'noVNC/core/rfb.js').is_file():
            raise ValueError('Apple-compatible browser assets are incomplete.')
        os.replace(stage,destination)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('/opt/pi-dashboard'))
    print(install(parser.parse_args().root))
