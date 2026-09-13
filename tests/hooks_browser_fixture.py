"""Local Waitress harness. Deterministic fixtures unless HOOKS_LIVE_CHECK=1."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as appmod
from hooks import media
from tests.hooks_fixture import SRT, make_video
from flask import send_file
from waitress import serve

root=Path(os.environ.get('HOOKS_DATA_DIR') or tempfile.mkdtemp(prefix='hooks-browser-'))
root.mkdir(parents=True,exist_ok=True)
appmod.app.config['HOOKS_DATA_DIR']=str(root)
if not os.environ.get('HOOKS_LIVE_CHECK'):
    appmod.llm_structured=lambda *args:{'candidates':[{'start_id':1,'end_id':2,'kind':'taste'}]}
    media.remote_info=lambda url:{'duration':8,'title':'후킹 테스트'}
    media.remote_subtitle=lambda info:(SRT.encode(),'srt','youtube_auto')
fixture=root/'clock.mp4'
if not fixture.exists():make_video(fixture)
@appmod.app.get('/_test/video')
def video():return send_file(fixture,mimetype='video/mp4')
print('Hook test server ready',str(root),flush=True)
serve(appmod.app,host='127.0.0.1',port=int(os.environ.get('HOOKS_TEST_PORT','8878')),threads=8,max_request_body_size=12*1024*1024)
