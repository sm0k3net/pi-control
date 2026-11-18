#!/usr/bin/env python3
"""Install the verified browser client for direct Mac sessions."""
import argparse
import hashlib
import io
import os
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath

COMMIT = 'a8dfd6a3ea3c74244f5ebdaa5a7f1023007a7820'
SHA256 = 'ebb8eabcd3670ce1a02e4025f03976826c289121cf631183647bd8325b003403'
URL = 'https://codeload.github.com/novnc/noVNC/tar.gz/' + COMMIT


def install(root):
    install_path = root / 'novnc-client'
    if install_path.exists() or install_path.is_symlink():
        raise ValueError('novnc-client already exists; use a separate directory to stage upgrades.')
    with urllib.request.urlopen(URL,timeout=22) as response:
        payload = response.read(2*1024**2)
    if hashlib.sha256(payload).hexdigest() != SHA256:
        raise ValueError('noVNC archive checksum mismatch.')
    with tempfile.TemporaryDirectory(dir=root) as tmp, tarfile.open(
            fileobj=io.BytesIO(payload),mode='r:gz') as archive:
        staging = Path(tmp) / 'novnc-client'
        staging.mkdir(mode=0o755)
        for member in archive:
            rel = PurePosixPath(*PurePosixPath(member.name).parts[1:])
            if not member.isfile() or '..' in rel.parts or not rel.parts:
                continue
            allowed = rel.parts[0] in ('core','vendor') and (rel.suffix=='.js' or rel.name=='LICENSE')
            if not allowed and rel.as_posix() != 'LICENSE.txt':
                continue
            if member.size > 512*1024:
                raise ValueError('Unexpected noVNC asset size.')
            target = staging.joinpath(*rel.parts)
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
            target.chmod(0o644)
        if not (staging/'core/rfb.js').is_file():
            raise ValueError('The noVNC archive is incomplete.')
        os.replace(staging,install_path)
    return install_path


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('/opt/pi-dashboard'))
    args=parser.parse_args()
    print(install(args.root))
