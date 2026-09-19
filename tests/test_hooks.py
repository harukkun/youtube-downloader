import hashlib
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import app as appmod
from hooks import media, subtitles, analysis
from hooks.store import Store
from hooks.service import Service
from tests.hooks_fixture import make_video, SRT


class SubtitleTest(unittest.TestCase):
    def test_overlap_preserved_and_rolling_deduplicated(self):
        raw=SRT+'\n4\n00:00:04,500 --> 00:00:06,000\n설탕을 넣으세요 그리고 볶아요\n\n5\n00:00:06,500 --> 00:00:07,000\n진짜 맛있어요\n'
        cues=subtitles.parse(raw)
        self.assertEqual(len(cues),5)
        self.assertEqual(cues[1]['text'],'꼭 만들어 보세요')
        self.assertEqual(cues[3]['text'],'그리고 볶아요')
        self.assertEqual(cues[4]['text'],'진짜 맛있어요')

    def test_invalid(self):
        for raw in ('garbage', '1\n00:00:04,000 --> 00:00:01,000\n역전', '1\n00:00:01,000 --> 00:00:02,000\n[음악]'):
            with self.assertRaises(ValueError):subtitles.parse(raw)

    def test_chunks_cover_every_cue(self):
        cues=[{'id':i,'text':'가'*30} for i in range(100)]
        chunks=list(subtitles.chunks(cues,limit=200))
        self.assertEqual({c['id'] for chunk in chunks for c in chunk},set(range(100)))
        self.assertGreater(len(chunks),1)

    def test_invalid_ai_ranges(self):
        for value in ({}, {'candidates':[{'start_id':99,'end_id':100,'kind':'taste'}]}, {'candidates':[{'start_id':2,'end_id':1,'kind':'taste'}]}):
            with self.assertRaises(ValueError):analysis.validate(value,subtitles.parse(SRT))

    def test_invalid_ai_retries_once_then_reuses_success(self):
        with tempfile.TemporaryDirectory() as root:
            calls=[]
            def llm(*args):
                calls.append(1)
                return {'candidates':[{'start_id':99,'end_id':99,'kind':'taste'}]} if len(calls)==1 else {'candidates':[]}
            args=(subtitles.parse(SRT),{'model':'m','backend':'b'},Path(root),llm,lambda *args:None)
            _,usage=analysis.analyze(*args)
            self.assertEqual(usage['calls'],2)
            _,usage=analysis.analyze(*args)
            self.assertEqual(usage['calls'],0)
            self.assertEqual(len(calls),2)

    def test_canonical_url(self):
        self.assertEqual(media.youtube_url('https://youtu.be/ylPj5BQw5cs?t=20'),'https://www.youtube.com/watch?v=ylPj5BQw5cs')
        for url in ('http://127.0.0.1/a','https://youtube.com.evil.test/watch?v=ylPj5BQw5cs','https://youtube.com/playlist?list=x'):
            with self.assertRaises(ValueError):media.youtube_url(url)


class HooksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.video=Path(cls.temp.name)/'clock.mp4';make_video(cls.video)
        cls.bytes=cls.video.read_bytes()

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        appmod.app.config['HOOKS_DATA_DIR']=self.tmp.name
        self.client=appmod.app.test_client()
        self.addCleanup(self.tmp.cleanup)

    def upload(self):
        result=self.client.post('/api/hooks/uploads',json={'name':'clock.mp4','size':len(self.bytes)}).get_json()
        ident=result['id'];header={'X-Chunk-SHA256':hashlib.sha256(self.bytes).hexdigest(),'Content-Type':'application/octet-stream'}
        self.assertEqual(self.client.put(f'/api/hooks/uploads/{ident}/chunks/0',data=self.bytes,headers=header).status_code,200)
        self.assertEqual(self.client.put(f'/api/hooks/uploads/{ident}/chunks/0',data=self.bytes,headers=header).status_code,200)
        complete=self.client.post(f'/api/hooks/uploads/{ident}/complete').get_json()
        self.assertEqual(self.client.post(f'/api/hooks/uploads/{ident}/complete').get_json()['asset_id'],complete['asset_id'])
        return complete['asset_id']

    def create(self, srt=True, url=''):
        asset=self.upload()
        response=self.client.post('/api/hooks/jobs',json={'asset_id':asset,'url':url})
        self.assertEqual(response.status_code,201,response.get_data(as_text=True))
        ident=response.get_json()['id']
        if srt:self.assertEqual(self.client.post(f'/api/hooks/jobs/{ident}/subtitles',data=SRT.encode()).status_code,200)
        return ident

    def wait(self, ident):
        for _ in range(200):
            state=self.client.get(f'/api/hooks/jobs/{ident}').get_json()
            if not state['busy']:return state
            time.sleep(.05)
        self.fail('job timeout')

    def test_full_transcript_available_before_analysis_and_after_reload(self):
        ident = self.create(srt=False, url='https://youtu.be/ylPj5BQw5cs')
        endpoint = f'/api/hooks/jobs/{ident}'
        self.assertEqual(self.client.get(endpoint).get_json()['transcript'], '')
        with patch('hooks.media.remote_info', return_value={'duration':8,'title':'test'}), patch('hooks.media.remote_subtitle', return_value=(SRT.encode(), 'srt', 'youtube_auto')):
            self.client.post(endpoint + '/prepare')
            state = self.wait(ident)
        self.assertEqual(state['transcript'], '진짜 맛있어요\n꼭 만들어 보세요\n설탕을 넣으세요')
        self.assertEqual(state['candidates'], [])
        self.assertEqual(self.client.get(endpoint).get_json()['transcript'], state['transcript'])
        changed = self.client.post(endpoint + '/source', json={'url':'https://youtu.be/abcdefghijk'}).get_json()
        self.assertEqual(changed['transcript'], '')

    def test_hook_endpoints_inherit_login_and_origin_protection(self):
        import os
        from flask import Flask
        from access_control import install_access_control
        from hooks.routes import install
        secured=Flask('secured',template_folder=str(Path(appmod.__file__).parent/'templates'))
        with patch.dict(os.environ,{'APP_PASSWORD':'fixture-password','APP_PUBLIC_ORIGIN':''}):
            install_access_control(secured)
        secured.config['HOOKS_DATA_DIR']=str(Path(self.tmp.name)/'secured')
        install(secured,lambda *a:None,lambda:{'backend':'codex','model':'fixture'})
        client=secured.test_client();headers={'Origin':'http://localhost'}
        self.assertEqual(client.post('/api/hooks/uploads',json={'name':'x.mp4','size':3},headers=headers).status_code,401)
        client.post('/login',data={'password':'fixture-password'},headers=headers)
        self.assertEqual(client.post('/api/hooks/uploads',json={'name':'x.mp4','size':3}).status_code,403)
        self.assertEqual(client.post('/api/hooks/uploads',json={'name':'x.mp4','size':3},headers=headers).status_code,200)

    def test_edit_helper_and_legacy_work_links(self):
        response=self.client.get('/edit-helper')
        self.assertEqual(response.status_code,200)
        html=response.get_data(as_text=True)
        self.assertIn('편집 헬퍼',html)
        self.assertIn('id="hooksTool"',html)
        self.assertIn('id="candidates"',html)
        self.assertNotIn('href="/hooks"',html)
        self.assertEqual(self.client.get('/hooks').location,'/edit-helper#hooks')
        self.assertEqual(self.client.get('/hooks?job=abc').location,'/edit-helper?job=abc#hooks')

    def test_upload_limits_checksum_and_cancel(self):
        self.assertEqual(self.client.post('/api/hooks/uploads',json={'name':'x.mp4','size':3*1024**3}).status_code,400)
        u=self.client.post('/api/hooks/uploads',json={'name':'x.mp4','size':3}).get_json()['id']
        self.assertEqual(self.client.put(f'/api/hooks/uploads/{u}/chunks/0',data=b'bad',headers={'X-Chunk-SHA256':'0'*64}).status_code,400)
        self.assertEqual(self.client.post(f'/api/hooks/uploads/{u}/complete').status_code,400)
        self.assertEqual(self.client.delete(f'/api/hooks/uploads/{u}').status_code,200)
        self.assertFalse((Path(self.tmp.name)/'uploads'/u).exists())

    def test_no_subtitles_no_llm_and_bad_upload(self):
        ident=self.create(False)
        with patch.object(appmod,'llm_structured') as llm:
            self.client.post(f'/api/hooks/jobs/{ident}/prepare')
            self.assertEqual(self.wait(ident)['status'],'needs_input')
            self.assertEqual(self.client.post(f'/api/hooks/jobs/{ident}/analyze').status_code,400)
            self.assertEqual(self.client.post(f'/api/hooks/jobs/{ident}/candidates',json={'text':'맛','start':1,'end':2}).status_code,400)
            llm.assert_not_called()
        self.assertEqual(self.client.post(f'/api/hooks/jobs/{ident}/subtitles',data=b'broken').status_code,400)

    def test_remote_no_captions_and_failure_are_distinct(self):
        ident=self.create(False,url='https://youtu.be/ylPj5BQw5cs')
        with patch('hooks.media.remote_info',return_value={'duration':8,'title':'test'}),patch('hooks.media.remote_subtitle',return_value=None):
            self.client.post(f'/api/hooks/jobs/{ident}/prepare');self.assertEqual(self.wait(ident)['status'],'no_subtitles')
        with patch('hooks.media.remote_info',side_effect=ValueError('network failed')):
            self.client.post(f'/api/hooks/jobs/{ident}/prepare');self.assertEqual(self.wait(ident)['status'],'error')

    def test_analysis_cache_manual_offset_and_export(self):
        ident=self.create()
        result={'candidates':[{'start_id':1,'end_id':2,'kind':'taste'}], '_usage':{'input_tokens':40,'output_tokens':10}}
        with patch.object(appmod,'llm_structured',return_value=result) as llm:
            self.client.post(f'/api/hooks/jobs/{ident}/analyze');state=self.wait(ident)
            self.assertEqual(state['status'],'ready',state)
            self.assertEqual(len(state['candidates']),1)
            auto=state['candidates'][0]
            self.assertAlmostEqual(auto['start'],.8)
            self.client.post(f'/api/hooks/jobs/{ident}/analyze');state=self.wait(ident)
            self.assertEqual(state['usage']['calls'],0)
            self.assertEqual(llm.call_count,1)
            state=self.client.post(f'/api/hooks/jobs/{ident}/candidates',json={'text':'직접 발언','start':1.4,'end':3.1}).get_json()
            manual=next(c for c in state['candidates'] if c['origin']=='manual')
            state=self.client.patch(f'/api/hooks/jobs/{ident}',json={'offset':.5}).get_json()
            self.assertEqual(next(c for c in state['candidates'] if c['origin']=='manual')['start'],1.4)
            self.assertAlmostEqual(next(c for c in state['candidates'] if c['origin']=='auto')['start'],1.3)
            self.client.post(f'/api/hooks/jobs/{ident}/export',json={'candidate_ids':[manual['id']]});state=self.wait(ident)
            self.assertEqual(state['exports'][0]['clips'][0]['status'],'finished',state)
            self.assertEqual(llm.call_count,1)
        batch=state['exports'][0];record=batch['clips'][0]
        path=Path(self.tmp.name)/'jobs'/ident/'exports'/batch['id']/record['filename']
        self.assertAlmostEqual(media.probe(path)['duration'],1.7,delta=.1)
        self.assertEqual(media.probe(path)['audio_codec'],'aac')
        # Verify the actual first frame against the source clock, not just duration.
        def first(path,start):return media.run(['ffmpeg','-v','error','-ss',str(start),'-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
        a,b=first(self.video,1.4),first(path,0)
        self.assertEqual(len(a),len(b))
        self.assertLess(sum(abs(x-y) for x,y in zip(a,b))/len(a),5)
        # Compare decoded audio at the same source time, allowing small AAC phase shifts.
        import array, math
        def audio(path,start):
            raw=media.run(['ffmpeg','-v','error','-ss',str(start),'-i',str(path),'-t','0.2','-ac','1','-ar','16000','-f','s16le','-'])
            samples=array.array('h');samples.frombytes(raw);return samples
        x,y=audio(self.video,1.55),audio(path,.15)
        scores=[]
        for lag in range(-160,161,8):
            a=x[160:-160];b=y[160+lag:len(y)-160+lag]
            if len(a)==len(b):scores.append(sum(u*v for u,v in zip(a,b))/math.sqrt(sum(u*u for u in a)*sum(v*v for v in b)))
        self.assertGreater(max(scores),.9)
        uri=f'/api/hooks/jobs/{ident}/exports/{batch["id"]}'
        with self.client.get(uri+'/clips.zip') as response:self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get(uri+'/anything').status_code,404)
        with self.client.get(uri+'/clips.json') as response:saved=response.get_json(force=True)
        self.assertEqual(saved[0]['origin'],'manual');self.assertIsNone(saved[0]['subtitle_start'])

    def test_audio_export_download_and_zip(self):
        import io
        import zipfile
        ident = self.create()
        endpoint = f'/api/hooks/jobs/{ident}'
        state = self.client.post(endpoint + '/candidates', json={'text':'오디오 테스트','start':1.4,'end':3.1}).get_json()
        ids = [state['candidates'][0]['id']]
        for invalid in ('wav', '', None, []):
            self.assertEqual(self.client.post(endpoint + '/export', json={'candidate_ids':ids,'format':invalid}).status_code, 400)
        self.assertEqual(self.client.post(endpoint + '/export', json={'candidate_ids':ids,'format':'audio'}).status_code, 200)
        state = self.wait(ident)
        batch = state['exports'][-1]
        self.assertTrue(batch['complete'])
        self.assertEqual(batch['format'], 'audio')
        record = batch['clips'][0]
        self.assertEqual(record['status'], 'finished', record)
        self.assertTrue(record['filename'].endswith('.mp3'))
        path = Path(self.tmp.name)/'jobs'/ident/'exports'/batch['id']/record['filename']
        info = json.loads(media.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)]))
        self.assertEqual([s['codec_type'] for s in info['streams']], ['audio'])
        self.assertEqual(info['streams'][0]['codec_name'], 'mp3')
        self.assertAlmostEqual(float(info['format']['duration']), 1.7, delta=.15)
        uri = endpoint + '/exports/' + batch['id']
        with self.client.get(uri + '/' + record['filename']) as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, 'audio/mpeg')
            self.assertEqual(response.data, path.read_bytes())
        with self.client.get(uri + '/clips.zip') as response:
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                self.assertEqual(set(archive.namelist()), {record['filename'], 'clips.json', 'clips.txt'})
                self.assertEqual(json.loads(archive.read('clips.json'))[0]['format'], 'audio')

    def test_alignment_requires_preview_and_range_rejection(self):
        ident=self.create(url='https://youtu.be/ylPj5BQw5cs')
        self.assertEqual(self.client.patch(f'/api/hooks/jobs/{ident}',json={'alignment_confirmed':True}).status_code,400)
        self.client.post(f'/api/hooks/jobs/{ident}/preview',json={});self.wait(ident)
        self.assertEqual(self.client.patch(f'/api/hooks/jobs/{ident}',json={'alignment_confirmed':True}).status_code,200)
        with self.client.get(f'/api/hooks/jobs/{ident}/media',headers={'Range':'bytes=0-99'}) as response:self.assertEqual(response.status_code,206)
        self.assertEqual(self.client.post(f'/api/hooks/jobs/{ident}/candidates',json={'text':'bad','start':7,'end':10}).status_code,400)
        self.assertEqual(self.client.patch(f'/api/hooks/jobs/{ident}',json={'offset':'nan'}).status_code,400)

    def test_concurrent_cache_and_restart(self):
        store=Store(Path(self.tmp.name)/'independent')
        calls=[]
        def llm(*args):calls.append(1);time.sleep(.1);return {'candidates':[]}
        args=(subtitles.parse(SRT),{'backend':'x','model':'y'},store.root/'cache',llm,lambda *a:None)
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:analysis.analyze(*args),range(2)))
        self.assertEqual(len(calls),1)
        ident='a'*32;store.write('jobs',ident,{'id':ident,'busy':True,'candidates':[{'text':'saved'}]})
        recovered=Store(store.root).read('jobs',ident)
        self.assertFalse(recovered['busy']);self.assertEqual(recovered['status'],'interrupted')
        self.assertEqual(recovered['candidates'][0]['text'],'saved')

    def test_expiration_retains_results_and_cache(self):
        store=Store(Path(self.tmp.name)/'cleanup')
        ident='b'*32;asset='c'*32
        store.write('jobs',ident,{'id':ident,'busy':False,'asset_id':asset,'last_media_use':0})
        store.write('assets',asset,{'id':asset,'updated':0})
        video=store.directory('assets',asset)/'video';video.write_bytes(b'temporary')
        directory=store.directory('jobs',ident)
        (directory/'preview').mkdir();(directory/'preview'/'preview.mp4').write_bytes(b'temporary')
        (directory/'exports').mkdir();(directory/'exports'/'keep.mp4').write_bytes(b'keep')
        (store.root/'cache'/'keep.json').write_text('{}')
        store.cleanup()
        self.assertFalse(video.exists());self.assertFalse((directory/'preview').exists())
        self.assertTrue((directory/'exports'/'keep.mp4').exists());self.assertTrue((store.root/'cache'/'keep.json').exists())

    def test_same_job_concurrent_start_is_single_work(self):
        ident=self.create();calls=[];entered=threading.Event();release=threading.Event()
        def llm(*args):
            calls.append(1);entered.set();release.wait(2)
            return {'candidates':[]}
        with patch.object(appmod,'llm_structured',side_effect=llm):
            self.client.post(f'/api/hooks/jobs/{ident}/analyze');self.assertTrue(entered.wait(2))
            self.client.post(f'/api/hooks/jobs/{ident}/analyze');release.set()
            self.wait(ident)
        self.assertEqual(len(calls),1)

    def test_zip_blocked_until_last_clip_finishes(self):
        ident=self.create()
        self.client.post(f'/api/hooks/jobs/{ident}/candidates',json={'text':'first','start':1,'end':2})
        state=self.client.post(f'/api/hooks/jobs/{ident}/candidates',json={'text':'second','start':3,'end':4}).get_json()
        entered=threading.Event();release=threading.Event();original=media.encode
        def encode(src,out,start=None,end=None,preview=False):
            if start==3:
                entered.set();release.wait(5)
            return original(src,out,start,end,preview)
        with patch('hooks.media.encode',side_effect=encode):
            self.client.post(f'/api/hooks/jobs/{ident}/export',json={'candidate_ids':[c['id'] for c in state['candidates']]})
            try:
                self.assertTrue(entered.wait(5))
                state=self.client.get(f'/api/hooks/jobs/{ident}').get_json()
                batch=state['exports'][-1]
                self.assertEqual(batch['total'],2);self.assertFalse(batch['complete'])
                self.assertEqual(len(batch['clips']),1)
                uri=f'/api/hooks/jobs/{ident}/exports/{batch["id"]}'
                for name in ('clips.zip','clips.txt','clips.json'):
                    self.assertEqual(self.client.get(uri+'/'+name).status_code,409)
            finally:release.set()
            state=self.wait(ident)
        self.assertTrue(state['exports'][-1]['complete'])
        import io,zipfile
        with self.client.get(uri+'/clips.zip') as response:
            self.assertEqual(response.status_code,200)
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                self.assertEqual(len([n for n in archive.namelist() if n.endswith('.mp4')]),2)

    def test_partial_export_and_recovery(self):
        ident=self.create()
        a=self.client.post(f'/api/hooks/jobs/{ident}/candidates',json={'text':'A','start':1,'end':2}).get_json()['candidates'][0]
        b=self.client.post(f'/api/hooks/jobs/{ident}/candidates',json={'text':'B','start':3,'end':4}).get_json()['candidates'][1]
        original=media.encode
        def encode(src,out,start=None,end=None,preview=False):
            if start==3:raise ValueError('simulated failure')
            return original(src,out,start,end,preview)
        with patch('hooks.media.encode',side_effect=encode):
            self.client.post(f'/api/hooks/jobs/{ident}/export',json={'candidate_ids':[a['id'],b['id']]});state=self.wait(ident)
        self.assertEqual([c['status'] for c in state['exports'][0]['clips']],['finished','error'])
        self.client.post(f'/api/hooks/jobs/{ident}/export',json={'candidate_ids':[b['id']]});state=self.wait(ident)
        self.assertEqual(state['exports'][1]['clips'][0]['status'],'finished')

if __name__=='__main__':unittest.main()
