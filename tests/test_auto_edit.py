import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from auto_edit import analysis, capcut
from auto_edit.service import Service


class IntervalTests(unittest.TestCase):
    def test_eof_and_union(self):
        self.assertEqual(analysis.parse_log('silence_start: 0\nsilence_end: 2\nsilence_start: 5', 'silence', 8), [[0,2],[5,8]])
        self.assertEqual(analysis.union([[1,4],[3,8],[10,12]]), [[1,8],[10,12]])
        self.assertEqual(analysis.complement([[1,4],[3,8]], 10), [[0,1],[8,10]])
        self.assertEqual(analysis.intersect([[1,4]], [[3,9]]), [[3,4]])

    def test_bad_options(self):
        for raw in ({'noise_db':float('nan')}, {'padding':-1}, {'silence':False,'freeze':False}, {'silence':'yes'}):
            with self.assertRaises(ValueError): analysis.options(raw)

    def test_ripple_preserves_audio_text_sync_and_fixed_speed(self):
        def seg(start, duration, source=None):
            return dict(id='seg', material_id='mat', target_timerange=dict(start=start*1000000,duration=duration*1000000),source_timerange=source,volume=.5,clip={'scale':2})
        data = {'id':'original','duration':10000000,'tracks':[
            {'type':'video','segments':[seg(0,10,{'start':4000000,'duration':20000000})]},
            {'type':'audio','segments':[seg(1,8,{'start':0,'duration':8000000})]},
            {'type':'text','segments':[seg(3,4)]}]}
        before = copy.deepcopy(data)
        result = capcut.ripple(data, [[2,4],[6,7]])
        self.assertEqual(data,before)
        self.assertEqual(result['duration'],7000000)
        video=result['tracks'][0]['segments']
        self.assertEqual([s['target_timerange']['start'] for s in video],[0,2000000,4000000])
        self.assertEqual([s['source_timerange']['start'] for s in video],[4000000,12000000,18000000])
        self.assertEqual(result['tracks'][2]['segments'][0]['target_timerange'],{'start':2000000,'duration':2000000})
        self.assertEqual(video[0]['clip'],{'scale':2})
        self.assertEqual(len({s['id'] for t in result['tracks'] for s in t['segments']}),7)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.video = cls.root/'speech.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=30:duration=6',
            '-f','lavfi','-i','sine=frequency=440:duration=6','-af',r'volume=enable=between(t\,2\,4):volume=0',
            '-c:v','libx264','-c:a','aac','-y',str(cls.video)],check=True)
        cls.still = cls.root/'still.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:size=160x90:rate=30:duration=3',
            '-c:v','libx264','-y',str(cls.still)],check=True)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_real_detectors(self):
        result=analysis.detect(self.video,6,True,analysis.options({}))
        self.assertEqual(len(result['silence']),1)
        self.assertAlmostEqual(result['silence'][0][0],2,delta=.06)
        self.assertAlmostEqual(result['silence'][0][1],4,delta=.08)
        self.assertEqual(result['freeze'],[])
        still=analysis.detect(self.still,3,False,analysis.options({}))
        self.assertEqual(still['silence'],[])
        self.assertEqual(still['freeze'],[[0,3]])

    def test_durable_multi_source_export_and_template_import(self):
        from hooks.media import probe
        from hooks.store import Store
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); hooks=Store(root/'hooks')
            for ident,name,path in [('a'*32,'speech.mp4',self.video),('b'*32,'still.mp4',self.still)]:
                hooks.write('assets',ident,dict(id=ident,name=name,info=probe(path),updated=time.time()))
                import shutil
                shutil.copy2(path,hooks.directory('assets',ident)/'video')
            service=Service(root/'edits',root/'hooks')
            job=service.create({'asset_ids':['a'*32,'b'*32], 'options':{'freeze':False},'name':'테스트'})
            state=self.wait(service,job['id'])
            self.assertEqual(state['status'],'finished',state['message'])
            self.assertAlmostEqual(state['result']['removed_duration'],1.76,delta=.1)
            folder=service.store.directory('jobs',job['id'])
            result=capcut.read_draft(folder/'draft/draft_content.json')
            self.assertEqual(len(result['tracks'][0]['segments']),3)
            self.assertTrue(all(Path(m['path']).is_file() for m in result['materials']['videos']))
            self.assertTrue(state['warnings'])
            loaded=capcut.pycapcut().ScriptFile.load_template(str(folder/'draft/draft_content.json'))
            self.assertEqual(loaded.duration,result['duration'])
            # Import existing pyCapCut draft, preserving its original file.
            existing=copy.deepcopy(result)
            next_job=service.create({'draft':existing,'options':{'freeze':False}})
            next_state=self.wait(service,next_job['id'])
            self.assertEqual(next_state['status'],'finished',next_state['message'])
            self.assertEqual(existing,result)

    def wait(self,service,ident):
        for _ in range(200):
            state=service.store.read('jobs',ident)
            if not state['busy']: return state
            time.sleep(.05)
        self.fail('job timeout')


class RouteTests(unittest.TestCase):
    def setUp(self):
        from app import app
        self.temp=tempfile.TemporaryDirectory()
        self.app=app
        self.app.config.update(TESTING=True,AUTO_EDIT_DATA_DIR=self.temp.name,HOOKS_DATA_DIR=self.temp.name+'/hooks')
        self.client=app.test_client()

    def tearDown(self): self.temp.cleanup()

    def test_page_empty_invalid_and_csrf(self):
        self.assertEqual(self.client.get('/auto-edit').status_code,200)
        self.assertEqual(self.client.get('/api/auto-edit/jobs').json,{'jobs':[]})
        self.assertEqual(self.client.post('/api/auto-edit/jobs',json={}).status_code,400)
        self.assertEqual(self.client.post('/api/auto-edit/jobs',json={},headers={'Origin':'https://evil.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/auto-edit/jobs/not-an-id').status_code,400)
        self.assertEqual(self.client.get('/api/auto-edit/jobs/'+'a'*32).status_code,404)

    def test_open_targets_only_completed_owned_draft(self):
        root=Path(self.temp.name)/'jobs'/('a'*32);root.mkdir(parents=True)
        state=dict(id='a'*32,busy=False,status='finished',name='테스트',created=1)
        (root/'state.json').write_text(json.dumps(state))
        with patch('auto_edit.capcut.launch', return_value={'opened':False,'message':'프로젝트 선택 필요'}) as launch:
            self.assertEqual(self.client.post('/api/auto-edit/jobs/'+'a'*32+'/open',json={}).status_code,200)
            launch.assert_called_once_with(root/'draft')
        state['status']='failed';(root/'state.json').write_text(json.dumps(state))
        self.assertEqual(self.client.post('/api/auto-edit/jobs/'+'a'*32+'/open',json={}).status_code,400)

class NativeDraftTests(unittest.TestCase):
    def test_install_is_separate_idempotent_and_prefers_mac_timeline(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/'source'; source.mkdir()
            draft={'id':'12345678-1234-1234-1234-123456789ABC','name':'요리 / 편집',
                   'tracks':[],'materials':{}}
            (source/'draft_content.json').write_text(json.dumps(draft))
            (source/'draft_info.json').write_text(json.dumps(draft))
            (source/'draft_meta_info.json').write_text(json.dumps({'draft_id':draft['id']}))
            with patch('auto_edit.capcut.library_root',return_value=root/'library'):
                destination=capcut.install_result(source)
                self.assertEqual(destination.parent,root/'library')
                self.assertEqual(json.loads((destination/'draft_meta_info.json').read_text())['draft_fold_path'],str(destination))
                edited=draft|{'name':'캡컷에서 수정한 이름'}
                (destination/'draft_info.json').write_text(json.dumps(edited))
                self.assertEqual(capcut.install_result(source),destination)
                self.assertEqual(json.loads((destination/'draft_info.json').read_text()),edited)
                entry=next(iter(capcut.projects().values()))
                self.assertEqual(entry['path'],destination/'draft_info.json')
                self.assertEqual(json.loads((source/'draft_info.json').read_text()),draft)

    def test_reject_unsupported_or_malformed_project(self):
        base={'tracks':[], 'materials':{}, 'canvas_config':{'width':1080,'height':1920}}
        for d in (base|{'materials':{'transitions':[{}]}},base|{'keyframes':{'video':[{}]}},
                  base|{'tracks':[None]},base|{'materials':{'videos':[None]}},base|{'canvas_config':{}}):
            with self.assertRaises(ValueError): capcut.validate_draft(d)


if __name__=='__main__': unittest.main()
