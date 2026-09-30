"""Offline browser fixture. Replaces every external sheet/media/model dependency."""
import copy
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests import upload_browser_fixture as base
from tests.test_articles import RESULT
from hooks import media

m=base.m
root=Path(os.environ.get('ARTICLE_FIXTURE_DIR','/tmp/article-browser-fixture'))
root.mkdir(exist_ok=True)
m.app.config.update(ARTICLES_DATA_DIR=str(root/'articles'),HOOKS_DATA_DIR=str(root/'hooks'))
m.HELPER_FILE=root/'helper.json'
video=root/'fixture.mp4'
media.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=green:s=320x576:d=4:r=25',
    '-vf','drawbox=x=0:y=0:w=320:h=100:color=red:t=fill,drawbox=x=0:y=476:w=320:h=100:color=blue:t=fill',
    '-c:v','libx264','-pix_fmt','yuv420p','-y',str(video)])
media.download=lambda *a,**kw:video
media.remote_info=lambda *a,**kw:{'duration':4}
media.remote_subtitle=lambda *a,**kw:(b'1\n00:00:00,000 --> 00:00:01,500\nOil in pan\n\n2\n00:00:01,500 --> 00:00:03,900\nStir rice\n','srt','youtube_manual')
base.records['item-0']['status']='✅ 업로드 완료'
base.records['item-2'].update(title='완성된 레시피',desc='밥 1공기를 볶는다',youtubeUrl='https://youtu.be/abcdefghijk')

def call(action,**kw):
    item_id=kw['itemId']
    if action=='article_get':return {**base.result(item_id),'article_version':1}
    if action=='article_fill_missing':
        if kw['requestId'] in base.receipts:return {**base.result(item_id,kw['requestId']),'article_version':1}
        if kw['revision']!=base.revision(base.records[item_id]):raise m.SheetCallError('현황판 변경',409,'conflict')
        before=copy.deepcopy(base.records[item_id]);base.records[item_id].update(kw['fields']);base.receipts[kw['requestId']]=item_id;base.control['posts']+=1
        return {**base.result(item_id,kw['requestId']),'before':before,'article_version':1}
    raise AssertionError('unexpected external action '+action)
m.sheet_call=call

def llm(system,prompt,schema,*args):
    import json
    base.control['generations']+=1
    if 'matches' in schema['properties']:
        steps=json.loads(prompt)['steps']
        return {'matches':[{'step_id':s['id'],'start_id':min(i+1,2),'end_id':min(i+1,2)} for i,s in enumerate(steps)]}
    return copy.deepcopy(RESULT)
m.llm_structured=llm
if __name__=='__main__':m.app.run(host='127.0.0.1',port=int(os.environ.get('ARTICLE_FIXTURE_PORT','8883')),threaded=True)
