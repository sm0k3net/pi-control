#!/usr/bin/env python3
import ast
import sys
from pathlib import Path

root = Path(sys.argv[1])
server = root / 'server.py'
html = root / 'index.html'
source = server.read_text()
if 'import maintenance_web\n' not in source:
    assert 'import product\n' in source
    source = source.replace('import product\n','import product\nimport maintenance_web\n',1)
    get = ' def do_GET(self):\n'
    post = "  if self.path.startswith('/api/product/'):return product.handle_post(self,data)\n"
    assert get in source and post in source
    source = source.replace(get,get+'  if self.path in maintenance_web.GET:return maintenance_web.get(self)\n',1)
    source = source.replace(post,'  if self.path in maintenance_web.POST:return maintenance_web.post(self,data)\n'+post,1)
    ast.parse(source)
    server.write_text(source)

source = html.read_text()
if '/maintenance-ui.js' not in source:
    assert '</head>' in source and '</body>' in source
    source = source.replace('</head>','<link rel="stylesheet" href="/maintenance-ui.css"></head>',1)
    html.write_text(source.replace('</body>','<script src="/maintenance-ui.js"></script></body>',1))
