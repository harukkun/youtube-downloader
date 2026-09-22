"""Local-only browser fixture. All sheet/LLM calls are replaced; never accesses Google."""
import copy
import hashlib
import os
import struct
import sys
import zlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.pop('APP_PASSWORD',None)
os.environ.pop('APP_PUBLIC_ORIGIN',None)
import tempfile
import threading
import app as m
from flask import request, Response, send_file
from youtube_upload import service as svc
import googleapiclient.errors
from tests.hooks_fixture import make_video

cfg={'sheet_id':'fixture','gid':'0','url':'https://docs.google.com/spreadsheets/d/fixture/edit'}
m.get_sheet_setting=lambda:cfg
m.get_upload_setting=lambda:{'url':'https://script.google.com/macros/s/fixture/exec','token':'fixture'}
m.llm_environment=lambda:{'codex_available':True,'cli_available':False,'api_key_set':False}
m.get_recipe_settings=m.recipe_defaults
m._write_json_atomic=lambda *args:None
records={}
receipts={}
control={'mode':'','posts':0,'generations':0,'inserts':0,'yt_fail':False,'thumbnails':0}
# YouTube: shared hooks store in a temp dir, faked Google client, in-memory settings/credentials.
data_dir=tempfile.mkdtemp(prefix='upload-fixture-')
m.app.config['HOOKS_DATA_DIR']=data_dir
video_path=Path(data_dir)/'fixture.mp4';make_video(video_path)
ycfg=m.app.extensions['youtube_upload_config']
ycfg.credentials_file=Path(data_dir)/'creds.json'
settings={}
ycfg.load_settings=lambda:dict(settings);ycfg.save_settings=lambda d:settings.update(d);ycfg.settings_lock=threading.Lock()
ycfg.script_version=lambda:15
class FakeHttpError(Exception):
    def __init__(self,status,reason):
        super().__init__('<HttpError %s>'%status);self.resp=type('R',(),{'status':status})();self.error_details=[{'reason':reason}];self.content=b'{}'
googleapiclient.errors.HttpError=FakeHttpError
class FakeYouTube:
    def videos(self):return self
    def thumbnails(self):return self
    def channels(self):return self
    def list(self,**kw):return type('Q',(),{'execute':lambda s,**k:{'items':[{'id':'UC1','snippet':{'title':'픽스처 채널'}}]}})()
    def insert(self,**kw):
        control['inserts']+=1;calls={'n':0}
        class Req:
            def next_chunk(self,num_retries=0):
                calls['n']+=1
                if control['yt_fail']:raise FakeHttpError(403,'quotaExceeded')
                if calls['n']==1:return type('P',(),{'progress':lambda s:.5})(),None
                return None,{'id':'fixture%04d'%control['inserts']}
        return Req()
    def set(self,**kw):
        control['thumbnails']+=1
        return type('Q',(),{'execute':lambda s,**k:{}})()
svc.build_client=lambda creds:FakeYouTube()
def youtube(connected=True,audit=True):
    svc.write_credentials(ycfg,{'client_id':'fixture.apps.googleusercontent.com','client_secret':'s',**({'refresh_token':'r','channel_title':'픽스처 채널'} if connected else {})})
    settings['youtube_audit_passed']=audit

def seed():
    records.clear();receipts.clear();control.update(mode='',posts=0,generations=0,inserts=0,yt_fail=False,thumbnails=0)
    youtube()
    import shutil
    for kind in ('jobs','assets','uploads'):   # forget previous scenarios' YouTube jobs and video assets
        shutil.rmtree(Path(data_dir)/kind,ignore_errors=True);(Path(data_dir)/kind).mkdir(parents=True,exist_ok=True)
    m._recent_edits.clear();m._recent_thumbs.clear();m._sheet_cache['at']=0
    for i in range(3):records['item-'+str(i)]={'itemId':'item-'+str(i),'status':'✅ 업로드 완료' if i==2 else '✂️ 편집 중',
        'dish':['주말 김치볶음밥','토마토 달걀 볶음','완료된 영상'][i],'title':'','desc':'','memo':'',
        'srcUrl':'https://youtu.be/abcdefghijk','refUrls':'https://youtu.be/lmnopqrstuv','thumbUrl':'/_test/image' if i==1 else ''}
seed()
def revision(c):return hashlib.sha256(str(c).encode()).hexdigest()
def result(item_id,request_id=''):
    return {'ok':True,'row':list(records).index(item_id)+3,'cells':copy.deepcopy(records[item_id]),'revision':revision(records[item_id]),
            'submitted':request_id in receipts,'requestId':request_id}
def call(action,**kw):
    item_id=kw['itemId']
    if item_id not in records:raise m.SheetCallError('항목이 삭제되었습니다.',409,'row_mismatch')
    if action=='upload_get':return result(item_id,kw.get('requestId',''))
    control['posts']+=1
    if kw['requestId'] in receipts:return result(item_id,kw['requestId'])
    if control['mode']=='conflict':raise m.SheetCallError('작업 중 내용이 변경되었습니다.',409,'conflict')
    if control['mode']=='unknown':raise m.SheetCallError('저장 결과를 확인해 주세요.',503,'commit_unknown')
    if kw['revision']!=revision(records[item_id]):raise m.SheetCallError('변경되었습니다.',409,'conflict')
    before=copy.deepcopy(records[item_id]);records[item_id].update(kw['fields']);records[item_id]['status']='✅ 업로드 완료'
    if kw.get('youtubeUrl'):records[item_id]['youtubeOn']=True;records[item_id]['youtubeUrl']=kw['youtubeUrl']
    if kw['thumbnailMode']=='new':records[item_id]['thumbUrl']='/_test/image'
    receipts[kw['requestId']]=item_id
    if control['mode']=='lost':raise m.SheetCallError('응답을 받지 못했습니다.',503,'commit_unknown')
    return {**result(item_id,kw['requestId']),'before':before}
m.sheet_call=call
# Block legacy helper/board write endpoints as well; this server must never contact Google.
m.apps_script_post=lambda *args,**kwargs: {'ok':False,'error':'fixture_only'}
m.sheet_items_cached=lambda *args,**kw:([m.item_from_script(i+3,c) for i,c in enumerate(records.values())],False,None)
def llm(*args):
    control['generations']+=1
    if control['mode']=='questions' and control['generations']==1:return {'status':'needs_input','questions':['기름의 양은 얼마인가요?'],'notes':['분량 확인'],'instagram':'','youtube':'','tiktok':''}
    return {'status':'complete','instagram':'인스타그램 레시피 '+('재료와 조리 과정. '*25),'youtube':'유튜브 레시피 '+('김치와 밥을 볶아 완성하세요. '*20),'tiktok':'틱톡 레시피 '+('맛있게 만드는 방법. '*25),'notes':['분량을 확인하세요.']}
m.llm_structured=llm
@m.app.get('/_test/image')
def image():
    w,h=108,192
    def chunk(t,d):return struct.pack('!I',len(d))+t+d+struct.pack('!I',zlib.crc32(t+d)&0xffffffff)
    raw=b''.join(b'\x00'+bytes([190,90+y//3,50])*w for y in range(h))
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')
    return Response(png,mimetype='image/png')
@m.app.get('/_test/video')
def video():
    return send_file(video_path,mimetype='video/mp4')
@m.app.route('/_test/control',methods=['GET','POST'])
def test_control():
    if request.method=='POST':
        data=request.get_json()
        if data.get('reset'):seed()
        if 'mode' in data:control['mode']=data['mode']
        if 'yt_fail' in data:control['yt_fail']=bool(data['yt_fail'])
        if 'youtube' in data:youtube(connected=data['youtube']!='disconnected',audit=data.get('audit',True))
        if data.get('change'):records['item-0']['memo']='팀원이 바꾼 메모'
        if data.get('delete'):records.pop('item-0',None)
    return {**control,'records':{k:{'youtubeUrl':v.get('youtubeUrl',''),'youtubeOn':v.get('youtubeOn',False),'status':v['status']} for k,v in records.items()}}
if __name__=='__main__':m.app.run(host='127.0.0.1',port=8877,threaded=True)
