"""Own the mount helper processes for as long as USB volumes are available."""
import logging
import os
import subprocess
import time

import usb_backup as usb


def main():
    os.umask(0o077)
    usb.FILES_STATE.mkdir(mode=0o700, exist_ok=True)
    cache = usb.FILES_STATE / 'mounts.json'
    state = usb.load(cache, {}) or {}
    while True:
        try:
            retry = usb.FILES_RESCAN.exists()
            usb.FILES_RESCAN.unlink(missing_ok=True)
            new = usb.automount_files(usb.devices(), state, retry)
            if state != new or not cache.exists():
                usb.save(cache, new)
            state = new
        except (OSError, ValueError, subprocess.SubprocessError):
            logging.warning('USB mount scan failed; will retry.')
        time.sleep(3)


if __name__ == '__main__':
    main()
