"""Format shared by the server release packager and the updater."""
FILES = (
    'cloud-usb-ui.js', 'cloud_postgres.py', 'dashboard_platform.py',
    'filemanager.js', 'files6.py', 'platform-ui.css', 'platform-ui.js',
    'platform_events.py', 'usb_backup.py', 'usb_mount_monitor.py',
    'maintenance-ui.js', 'maintenance-ui.css',
)
ADDITIONS = ('node_features.py', 'remote_desktop.py')
RELEASE_FILES = FILES + ADDITIONS
ASSET = 'pi-control-server.zip'
