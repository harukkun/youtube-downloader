"""Opt-in acceptance check with user-provided source; never part of discovery."""
import sys
sys.path.insert(0,str(__import__("pathlib").Path(__file__).resolve().parents[1]))
import hashlib
import json
import os
import time
import unicodedata
import urllib.request
from pathlib import Path

BASE='http://127.0.0.1:8879/api/hooks'

def api(path, method='GET', data=None, raw=None, headers=None):
    h=headers or {}
    if data is not None:
        raw=json.dumps(data).encode();h={**h,'Content-Type':'application/json'}
    with urllib.request.urlopen(urllib.request.Request(BASE+path,data=raw,method=method,headers=h),timeout=120) as r:
        return json.load(r)

def wait(ident):
    last=None
    for _ in range(360):
        state=api('/jobs/'+ident)
        if state['message']!=last:print(state['status'],state['message'],flush=True);last=state['message']
        if not state['busy']:
            if state['status']=='error':raise RuntimeError(state['message'])
            return state
        time.sleep(1)
    raise RuntimeError('timeout')

if __name__=='__main__':
    source=next(Path('/Users/user/Downloads')/n for n in os.listdir('/Users/user/Downloads') if unicodedata.normalize('NFC',n)=='원본영상.mp4')
    ident=os.environ.get('HOOKS_RESUME_ID')
    if ident:
        job=api('/jobs/'+ident)
        asset=job['asset_id']
        api('/jobs/'+ident+'/source','POST',{'url':'https://www.youtube.com/watch?v=ylPj5BQw5cs'})
    else:
        registered=api('/uploads','POST',{'name':source.name,'size':source.stat().st_size})
        with source.open('rb') as f:
            index=0
            while data:=f.read(registered['chunk_size']):
                headers={'Content-Type':'application/octet-stream','X-Chunk-SHA256':hashlib.sha256(data).hexdigest()}
                api(f'/uploads/{registered["id"]}/chunks/{index}','PUT',raw=data,headers=headers)
                if index==0:api(f'/uploads/{registered["id"]}/chunks/{index}','PUT',raw=data,headers=headers)
                index+=1
        asset=api(f'/uploads/{registered["id"]}/complete','POST',{})['asset_id']
        job=api('/jobs','POST',{'asset_id':asset,'url':'https://www.youtube.com/watch?v=ylPj5BQw5cs'})
        ident=job['id']
    print('JOB',ident,flush=True)
    api('/jobs/'+ident+'/prepare','POST',{});state=wait(ident)
    assert state['subtitle_source']=='youtube_auto' and state['cue_count']>0,state
    print('CAPTIONS',state['cue_count'],'DURATION',state['info']['duration'],flush=True)
    api('/jobs/'+ident+'/analyze','POST',{});state=wait(ident)
    print('CANDIDATES',len(state['candidates']),'USAGE',state['usage'],flush=True)
    assert state['candidates'],state
    api('/jobs/'+ident+'/preview','POST',{});state=wait(ident)
    # Do not falsely attest that a human has checked the hybrid source's speech.
    # Verify the source-only/manual path using the actual candidate times.
    original_candidate=state['candidates'][0]
    api('/jobs/'+ident+'/analyze','POST',{});state=wait(ident)
    assert state['usage']['calls']==0,state['usage']
    assert not (Path('/private/tmp/hooks-live-validation/jobs')/ident/'source').exists()
    local=api('/jobs','POST',{'asset_id':asset})['id']
    from tests.hooks_fixture import SRT
    api('/jobs/'+local+'/subtitles','POST',raw=SRT.encode(),headers={'Content-Type':'application/octet-stream'})
    c=api('/jobs/'+local+'/candidates','POST',{'text':original_candidate['text'],'start':original_candidate['start'],'end':original_candidate['end']})['candidates'][0]
    api('/jobs/'+local+'/export','POST',{'candidate_ids':[c['id']]});out=wait(local)
    assert out['exports'][-1]['clips'][0]['status']=='finished',out
    print('LIVE_CHECK_PASSED',ident,json.dumps(out['exports'][-1]['clips'][0],ensure_ascii=False),flush=True)
