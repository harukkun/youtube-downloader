import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as appmod
from tests.test_cooking_audio import SRT, MATCH
from tests.hooks_fixture import make_video
from flask import send_file
from waitress import serve
root=Path(tempfile.mkdtemp(prefix='cooking-browser-'))
appmod.app.config['HOOKS_DATA_DIR']=str(root)
appmod.llm_structured=lambda *args:{'candidates':[MATCH]}
video=root/'clock.mp4';make_video(video)
@appmod.app.get('/_test/video')
def fixture():return send_file(video,mimetype='video/mp4')
@appmod.app.get('/_test/srt')
def srt():return SRT
print(str(root),flush=True)
serve(appmod.app,host='127.0.0.1',port=8881,threads=8,max_request_body_size=12*1024*1024)
