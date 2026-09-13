import hashlib
import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import app as appmod
from hooks.service import Service as Hooks
from hooks import subtitles, media
from cooking_audio.service import Service
from cooking_audio import analysis
from tests.hooks_fixture import make_video

SRT='''1
00:00:01,000 --> 00:00:02,500
후추 20바퀴 넣어요

2
00:00:02,500 --> 00:00:03,200
에에에??

3
00:00:03,300 --> 00:00:03,600
[웃음]

4
00:00:04,000 --> 00:00:05,500
중불로 1분 끓이세요
'''
MATCH={'start_id':1,'end_id':2,'kind':'ingredient','reaction':True,'check':False}

class CookingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.video=Path(cls.tmp.name)/'clock.mp4';make_video(cls.video)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def setUp(self):
        self.tmpjob=tempfile.TemporaryDirectory();self.addCleanup(self.tmpjob.cleanup)
        appmod.app.config['HOOKS_DATA_DIR']=self.tmpjob.name
        self.client=appmod.app.test_client()
        with appmod.app.app_context():self.hooks=appmod.app.extensions['hooks_service']()
        self.s=Service(self.hooks)
        self.calls=[]
        def llm(*args):self.calls.append(args);return {'candidates':[MATCH]}
        self.s.llm=llm
        self.s.settings=lambda:{'backend':'test','model':'test'}
        asset='a'*32
        directory=self.s.store.directory('assets',asset);directory.mkdir()
        import shutil
        shutil.copy(self.video,directory/'video')
        self.s.store.write('assets',asset,{'id':asset,'name':'clock.mp4','sha256':hashlib.sha256(self.video.read_bytes()).hexdigest(),'info':media.probe(self.video),'updated':time.time()})
        self.state=self.s.create_cooking({'asset_id':asset})
        self.id=self.state['id'];self.base='/api/cooking-audio/jobs/'+self.id
        self.s.add_transcript(self.state,subtitles.parse(SRT,preserve_context=True),'uploaded')
        self.s.store.write('jobs',self.id,self.state)
    def analyze(self):self.s.analyze(self.id);return self.s.get(self.id)
    def test_context_and_schema(self):
        cues=subtitles.parse(SRT,preserve_context=True)
        self.assertTrue(cues[2]['context_only']);self.assertEqual(len(subtitles.parse(SRT)),3)
        for match in (dict(MATCH,start_id=999),dict(MATCH,end_id=0),dict(MATCH,reaction='yes')):
            with self.assertRaises(ValueError):analysis.validate({'candidates':[match]},cues)
        self.assertIn('에에에??',analysis.SYSTEM)
    def test_reanalysis_preserves_edit_delete_and_uses_cache(self):
        state=self.analyze();c=state['candidates'][0];self.assertTrue(c['reaction']);self.assertIn('에에에',c['text'])
        self.s.edit(state,c['id'],{'text':'편집 문구','start':1.2,'end':3,'selected':False});self.s.store.write('jobs',self.id,state)
        state=self.analyze();self.assertEqual(state['candidates'][0]['text'],'편집 문구');self.assertEqual(len(self.calls),1)
        self.s.edit(state,c['id']);self.s.store.write('jobs',self.id,state)
        self.assertEqual(self.analyze()['candidates'],[]);self.assertEqual(len(self.calls),1)
    def test_shared_concurrent_cache(self):
        state=self.s.get(self.id);cues=state['cues']
        args=(cues,state['settings'],'',self.s.store.root/'cache',self.s.llm,lambda *x:None)
        with ThreadPoolExecutor(2) as ex:list(ex.map(lambda _:analysis.analyze(*args),range(2)))
        self.assertEqual(len(self.calls),1)
    def test_offset_split_merge_and_manual(self):
        state=self.analyze();state['offset']=.5;c=state['candidates'][0]
        self.assertAlmostEqual(self.s.timing(state,c)[0],1.4)
        manual=self.s.manual(state,{'start':4,'end':5,'text':'직접'});state['candidates'].append(manual)
        self.assertEqual(self.s.timing(state,manual),(4,5))
        self.s.split(state,c['id'],2);parts=state['candidates'][:2]
        state['offset']=1;self.assertAlmostEqual(self.s.timing(state,parts[0])[0],1.4)
        self.s.merge(state,[c['id'] for c in parts]);self.assertEqual(state['candidates'][0]['coordinate'],'source')
        self.assertIn('parent_ids',state['candidates'][0])
    def test_partial_asr_is_alternative_without_offset(self):
        self.analyze();self.s.store.update(self.id,offset=2)
        cues=[{'id':1,'start_ms':4000,'end_ms':4500,'text':'설탕','original_text':'설탕'}, {'id':2,'start_ms':4500,'end_ms':5000,'text':'넣어요','original_text':'넣어요'}]
        with patch('cooking_audio.asr.transcribe',return_value=(cues,False)):
            self.s.recognize(self.id,4,5)
        state=self.analyze();self.assertEqual(len(state['candidates']),2)
        c=state['candidates'][-1];self.assertTrue(c['alternative']);self.assertFalse(c['selected']);self.assertAlmostEqual(self.s.timing(state,c)[0],4.0)
    def test_export_mp3_duration_and_zip_gate(self):
        state=self.analyze();ids=[c['id'] for c in state['candidates']]
        self.s.export(self.id,ids);batch=self.s.get(self.id)['exports'][0]
        self.assertTrue(batch['complete']);record=batch['clips'][0];self.assertEqual(record['status'],'finished')
        url=self.base+'/exports/'+batch['id']+'/'
        r=self.client.get(url+'dialogues.zip');self.assertEqual(r.status_code,200)
        payload=r.data;r.close()
        with zipfile.ZipFile(io.BytesIO(payload)) as z:self.assertIn(record['filename'],z.namelist());self.assertIn('dialogues.json',z.namelist())
        output=self.s.store.directory('jobs',self.id)/'exports'/batch['id']/record['filename']
        info=json.loads(media.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(output)]))
        self.assertEqual(info['streams'][0]['codec_name'],'mp3');self.assertLess(abs(float(info['format']['duration'])-(record['end']-record['start'])),.1)
        state=self.s.get(self.id);state['exports'][0]['complete']=False;self.s.store.write('jobs',self.id,state)
        self.assertEqual(self.client.get(url+'dialogues.zip').status_code,409)
        response=self.client.get(url+record['filename']);self.assertEqual(response.status_code,200);response.close()
    def test_api_isolation_and_bad_source(self):
        self.assertEqual(self.client.get('/api/hooks/jobs/'+self.id).status_code,404)
        self.assertEqual(self.client.get(self.base).status_code,200)
        self.assertFalse(any(j['id']==self.id for j in self.client.get('/api/hooks/jobs').json['jobs']))
        self.assertEqual(self.client.patch(self.base,json={'offset':'nan'}).status_code,400)
        self.assertEqual(self.client.post(self.base+'/subtitles',data='broken').status_code,400)
        hook=self.hooks.create(asset_id='a'*32)
        self.assertEqual(self.client.get('/api/cooking-audio/jobs/'+hook['id']).status_code,404)
    def test_no_asr_if_captions_and_no_fallback_on_network_error(self):
        with patch('cooking_audio.asr.transcribe') as mocked:self.s.prepare(self.id);mocked.assert_not_called()
        self.s.store.update(self.id,cues=[],url='https://www.youtube.com/watch?v=ylPj5BQw5cs')
        with patch('hooks.media.remote_info',side_effect=ValueError('network')),patch('cooking_audio.asr.transcribe') as mocked:
            with self.assertRaises(ValueError):self.s.prepare(self.id)
            mocked.assert_not_called()
    def test_partial_export_failure_preserves_success(self):
        state=self.analyze()
        state['candidates'].append(self.s.manual(state,{'start':4,'end':5,'text':'두 번째'}))
        self.s.store.write('jobs',self.id,state)
        original=media.run
        def run(args,*rest):
            if args[0]=='ffmpeg' and '두 번째' in str(args[-1]):
                raise ValueError('인코딩 실패')
            return original(args,*rest)
        with patch('hooks.media.run',side_effect=run):
            self.s.export(self.id,[c['id'] for c in state['candidates']])
        batch=self.s.get(self.id)['exports'][0]
        self.assertTrue(batch['complete'])
        self.assertEqual([c['status'] for c in batch['clips']],['finished','error'])
        path=self.s.store.directory('jobs',self.id)/'exports'/batch['id']/'dialogues.zip'
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(len([x for x in archive.namelist() if x.endswith('.mp3')]),1)

    def test_missing_subtitles_runs_asr_and_cancellation_preserves_candidates(self):
        self.s.store.update(self.id,cues=[])
        cues=subtitles.parse(SRT)
        with patch('cooking_audio.asr.transcribe',return_value=(cues,False)) as mocked:
            self.s.prepare(self.id)
            mocked.assert_called_once()
        state=self.analyze()
        self.s.store.update(self.id,cancel_requested=True)
        with self.assertRaises(InterruptedError):self.s.analyze(self.id)
        self.assertEqual(self.s.get(self.id)['candidates'],state['candidates'])

    def test_shared_retention_and_restart(self):
        from hooks.store import Store
        state=self.analyze();state.update(last_media_use=time.time()-8*86400)
        self.s.store.write('jobs',self.id,state)
        asset=self.s.store.read('assets','a'*32);asset['updated']=time.time()-8*86400
        self.s.store.write('assets','a'*32,asset)
        hook=self.hooks.create(asset_id='a'*32)
        self.s.store.cleanup()
        self.assertTrue((self.s.store.directory('assets','a'*32)/'video').exists())
        self.s.store.update(hook['id'],last_media_use=time.time()-8*86400)
        self.s.store.cleanup()
        self.assertFalse((self.s.store.directory('assets','a'*32)/'video').exists())
        self.s.store.update(self.id,busy=True)
        recovered=Store(self.tmpjob.name).read('jobs',self.id)
        self.assertFalse(recovered['busy']);self.assertEqual(recovered['status'],'interrupted')
        self.assertEqual(recovered['candidates'],state['candidates'])

    def test_source_reuse_and_transcript_versions(self):
        hook=self.hooks.create(asset_id='a'*32,srt=SRT)
        state=self.s.create_cooking({'hook_job':hook['id']})
        self.assertEqual(state['asset_id'],hook['asset_id']);self.assertEqual(state['candidates'],[])
        old=state['active_transcript'];self.s.add_transcript(state,subtitles.parse(SRT.replace('후추','소금')),'uploaded')
        self.assertNotEqual(old,state['active_transcript'])

if __name__=='__main__':unittest.main()
