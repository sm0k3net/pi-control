#!/usr/bin/env python3
"""Register platform backup and notification endpoints in a Pi Control installation."""
import ast
import sys
from pathlib import Path

root = Path(sys.argv[1])
server = root / 'server.py'
html = root / 'index.html'
source = server.read_text()
if 'import dashboard_platform\n' not in source:
    assert 'import product\n' in source
    source = source.replace('import product\n', 'import product\nimport dashboard_platform\n', 1)
    get = ' def do_GET(self):\n'
    post = "  if self.path.startswith('/api/product/'):return product.handle_post(self,data)\n"
    assert get in source and post in source
    source = source.replace(get, get + "  if self.path in dashboard_platform.GET or self.path in ('/platform-ui.js','/platform-ui.css','/cloud-usb-ui.js'):return dashboard_platform.get(self)\n", 1)
    source = source.replace(post, "  if self.path in dashboard_platform.POST:return dashboard_platform.post(self,data)\n" + post, 1)
    ast.parse(source)
    server.write_text(source)

source = html.read_text()
if '/platform-ui.js' not in source:
    assert '</head>' in source and '</body>' in source
    html.write_text(source.replace('</head>', '<link rel="stylesheet" href="/platform-ui.css"></head>', 1)
                    .replace('</body>', '<script src="/platform-ui.js"></script><script src="/cloud-usb-ui.js"></script></body>', 1))
