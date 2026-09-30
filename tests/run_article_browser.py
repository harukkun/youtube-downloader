"""Run the isolated browser fixture and always stop it; NODE_PATH must expose Playwright."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='article-browser-') as directory:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env={**os.environ,'ARTICLE_FIXTURE_DIR':directory,'ARTICLE_FIXTURE_PORT':str(port),'ARTICLE_BASE':f'http://127.0.0.1:{port}'}
    with open(Path(directory)/'server.log','w+') as log:
        server=subprocess.Popen([sys.executable,str(root/'tests/article_browser_fixture.py')],cwd=root,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:
                    with urlopen(env['ARTICLE_BASE']+'/_test/control',timeout=1):break
                except OSError:
                    if server.poll() is not None:raise RuntimeError('Fixture server stopped')
                    time.sleep(.1)
            result=subprocess.run(['node',str(root/'tests/test_article_browser.js')],cwd=root,env=env,timeout=180)
            if result.returncode:
                log.seek(0);print(log.read()[-10000:])
            sys.exit(result.returncode)
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:server.kill();server.wait()
