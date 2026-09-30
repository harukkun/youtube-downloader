"""Offline article tests: no paid model calls and no Google/YouTube requests."""
import copy
import io
import json
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import app as appmod
from article_builder import analysis, document as doc
from article_builder.service import Service, Conflict
from hooks import media

URL='https://www.youtube.com/watch?v=abcdefghijk'
ITEM={'item_id':'dish-1','status':'uploaded','dish_title':'볶음밥','video':{'title':'볶음밥','description':'밥 1공기, 기름 1T. 기름에 밥을 3분 볶는다.'},'platforms':{'youtube':{'url':URL}},'source':{'url':'https://youtu.be/lmnopqrstuv'}}
CFG=dict(template=doc.DEFAULT_TEMPLATE,instructions=doc.DEFAULT_INSTRUCTIONS,backend='codex',model=appmod.DEFAULT_CODEX_MODEL)
RESULT=dict(status='complete',questions=[],title='볶음밥 만들기',intro='간단한 조리법입니다.',ingredients='밥 1공기\n기름 1T',closing='영상도 참고하세요.',steps=[dict(title='기름 두르기',body='기름 1T를 둘러주세요.'),dict(title='밥 볶기',body='밥을 넣고 3분 볶습니다.')])


class ArticleTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.llm=Mock(return_value=copy.deepcopy(RESULT))
        self.s=Service(Path(self.temp.name),self.llm)
        self.job=self.s.create(ITEM,'connection',CFG)

    def apply(self):
        result=self.s.perform(self.job,'generate',{},lambda **kw:None)
        self.job.update(result);self.s.write(self.job)
        self.job=self.s.apply_article(self.job['id'],self.job['version'])
        return self.job

    def wait(self):
        deadline=time.time()+15
        while time.time()<deadline:
            self.job=self.s.get(self.job['id'])
            if not self.job['busy']:return self.job
            time.sleep(.02)
        self.fail('worker timeout')

    def test_snapshot_and_version_conflict(self):
        self.assertEqual(self.job['source']['youtube_url'],URL)
        old=self.job['version'];content=copy.deepcopy(self.job['article']);content['intro']='수정'
        self.job=self.s.patch(self.job['id'],{'version':old,'article':content})
        with self.assertRaises(Conflict):self.s.patch(self.job['id'],{'version':old,'article':content})
        self.assertEqual(self.s.get(self.job['id'])['article']['intro'],'수정')
        self.assertEqual(self.llm.call_count,0)

    def test_text_only_export_without_video_or_images(self):
        self.apply()
        self.assertFalse(self.s.missing(self.job))
        result = self.s.perform(self.job, 'export', {}, lambda **kw: None)
        with zipfile.ZipFile(self.s.file(self.job['id'], 'exports', result['exports'][-1]['file'])) as z:
            self.assertEqual(set(z.namelist()), {'article.md', 'article.html'})
            md = z.read('article.md').decode()
            self.assertIn('### 2.', md)
            self.assertNotIn('번 이미지]', md)
            self.assertNotIn('![', md)
        self.job['article']['steps'][0]['body'] = ''
        self.assertTrue(self.s.missing(self.job))

    def test_generation_cache_force_and_pending_candidate(self):
        initial=self.job['article']
        self.apply();self.assertEqual(self.llm.call_count,1)
        self.assertNotEqual(initial,self.job['article'])
        self.s.perform(self.job,'generate',{},lambda **kw:None)
        self.assertEqual(self.llm.call_count,1)
        self.s.perform(self.job,'generate',{'force':True},lambda **kw:None)
        self.assertEqual(self.llm.call_count,2)
        changed=copy.deepcopy(self.job);changed['settings']['instructions']='차분하게'
        self.s.perform(changed,'generate',{},lambda **kw:None)
        self.assertEqual(self.llm.call_count,3)

    def test_questions_and_answers_are_cache_inputs(self):
        self.llm.return_value={'status':'needs_input','questions':['기름은 얼마나 넣나요?']}
        result=self.s.perform(self.job,'generate',{},lambda **kw:None)
        self.assertEqual(len(result['questions']),1);self.assertIsNone(result['pending_article'])
        self.job['answers']=[dict(question='기름은?',answer='1T')]
        self.llm.return_value=RESULT
        result=self.s.perform(self.job,'generate',{},lambda **kw:None)
        self.assertIsNotNone(result['pending_article']);self.assertEqual(self.llm.call_count,2)

    def test_usage_unknown_and_failed_calls_are_not_retried(self):
        runner=analysis.Runner(self.s.store.root/'cache',self.llm,CFG)
        analysis.generate(self.job,runner)
        self.assertEqual(runner.usage['calls'],1);self.assertIsNone(runner.usage['input_tokens'])
        self.llm.return_value={**RESULT,'_usage':dict(input_tokens=120,output_tokens=40)}
        runner=analysis.Runner(self.s.store.root/'cache',self.llm,CFG,force=True)
        analysis.generate(self.job,runner);self.assertEqual(runner.usage['input_tokens'],120)
        self.llm.side_effect=RuntimeError('offline')
        runner=analysis.Runner(self.s.store.root/'cache',self.llm,CFG,force=True)
        with self.assertRaises(RuntimeError):analysis.generate(self.job,runner)
        self.assertEqual(runner.usage['calls'],1)

    def test_matching_accepts_overlap_reverse_and_multiple_occurrences(self):
        self.apply();a,b=[s['id'] for s in self.job['article']['steps']]
        self.job.update(info={'duration':10},cues=[{'id':n,'start_ms':n*1000,'end_ms':n*1000+900,'text':'조리법'} for n in range(1,6)])
        self.llm.return_value={'matches':[{'step_id':a,'start_id':4,'end_id':5},{'step_id':a,'start_id':1,'end_id':1},{'step_id':b,'start_id':1,'end_id':2}]}
        result=self.s.perform(self.job,'suggest',{},lambda **kw:None)['suggestions']
        self.assertEqual(result[a],[[1,1.9],[4,5.9]]);self.assertEqual(result[b],[[1,2.9]])
        self.llm.return_value={'matches':[{'step_id':a,'start_id':999,'end_id':999}]}
        with self.assertRaises(ValueError):self.s.perform(self.job,'suggest',{'force':True},lambda **kw:None)

    def test_stale_async_result_never_overwrites_newer_draft(self):
        entered,release=threading.Event(),threading.Event()
        def slow(*a):entered.set();release.wait(3);return RESULT
        self.llm.side_effect=slow
        self.s.start(self.job['id'],self.job['version'],'generate')
        self.assertTrue(entered.wait(2))
        content=copy.deepcopy(self.job['article']);content['intro']='새로운 편집'
        self.s.patch(self.job['id'],{'version':self.job['version'],'article':content});release.set()
        result=self.wait();self.assertEqual(result['status'],'stale');self.assertEqual(result['article']['intro'],'새로운 편집');self.assertIsNone(result['pending_article'])

    def test_reordering_preserves_images_edits_require_review(self):
        self.apply();ids=[s['id'] for s in self.job['article']['steps']]
        self.job['selections']={k:{'image':'saved.jpg','confirmed':True,'review':False} for k in ids};self.s.write(self.job)
        a=copy.deepcopy(self.job['article']);a['steps'].reverse()
        self.job=self.s.patch(self.job['id'],dict(version=self.job['version'],article=a))
        self.assertTrue(self.job['selections'][ids[0]]['confirmed'])
        a['steps'][0]['body']='다르게 볶기'
        self.job=self.s.patch(self.job['id'],dict(version=self.job['version'],article=a))
        self.assertFalse(self.job['selections'][ids[1]]['confirmed']);self.assertTrue(self.job['selections'][ids[0]]['confirmed'])

    def test_template_and_html_safety(self):
        with self.assertRaises(ValueError):doc.template('{{steps}}')
        with self.assertRaises(ValueError):doc.template('{{steps}} {{steps}} {{youtube_url}}')
        with self.assertRaises(ValueError):doc.template('{{steps}} {{youtube_url}} {{unknown}}')
        with self.assertRaises(ValueError):doc.text('bad\x00')
        self.apply();self.job['article']['title']='<script>alert(1)</script>'
        self.job['settings']['template']='{{title}}\n\n[bad](javascript:alert(1))\n\n<img src=x onerror=alert(1)>\n\n{{steps}}\n\n{{youtube_url}}'
        output=self.s.preview_document(self.job)
        self.assertNotIn('<script>',output);self.assertNotIn('<img src=x',output);self.assertNotIn('href="javascript:',output)
        self.assertIn('1. 기름 두르기',output)
        with self.assertRaises(FileNotFoundError):self.s.file(self.job['id'],'images','../../secret')

    def test_restart_retains_confirmed_artifacts(self):
        self.job.update(busy=True,status='working');self.s.write(self.job)
        restarted=Service(Path(self.temp.name),self.llm)
        self.assertEqual(restarted.get(self.job['id'])['status'],'interrupted')


class ArticleMediaTest(unittest.TestCase):
    setUp = ArticleTest.setUp
    apply = ArticleTest.apply
    @classmethod
    def setUpClass(cls):
        cls.fixture=tempfile.TemporaryDirectory();cls.video=Path(cls.fixture.name)/'color.mp4'
        media.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=green:s=160x288:d=2:r=10','-vf','drawbox=x=0:y=0:w=160:h=64:color=red:t=fill,drawbox=x=0:y=224:w=160:h=64:color=blue:t=fill','-c:v','libx264','-pix_fmt','yuv420p','-y',str(cls.video)])
    @classmethod
    def tearDownClass(cls):cls.fixture.cleanup()

    def media_job(self):
        self.apply();self.job=self.s.replace_asset(self.job['id'],self.job['version'],self.video,'video.mp4')
        self.job.update(self.s.perform(self.job,'prepare',{},lambda **kw:None));self.s.write(self.job)
        self.job=self.s.patch(self.job['id'],dict(version=self.job['version'],excluded=[.25,.25]))
        return self.job

    def test_still_crop_confirmation_zip_and_expired_source(self):
        self.media_job();self.assertEqual(self.job['info']['width'],160)
        for step in self.job['article']['steps']:
            sid=step['id'];self.job=self.s.step(self.job['id'],sid,dict(version=self.job['version'],range=[0,2],time=.5))
            updates=self.s.perform(self.job,'capture',{'step_id':sid},lambda **kw:None);self.job.update(updates);self.s.write(self.job)
            self.job=self.s.step(self.job['id'],sid,dict(version=self.job['version'],confirm=True))
            image=self.s.file(self.job['id'],'images',self.job['selections'][sid]['image'])
            raw=media.run(['ffmpeg','-v','error','-i',str(image),'-f','rawvideo','-pix_fmt','rgb24','-'])
            # Every pixel is green, including edges: excluded title/source bars are absent.
            self.assertEqual(len(raw),144*144*3)
            self.assertTrue(all(raw[i+1]>80 and raw[i]<40 and raw[i+2]<40 for i in range(0,len(raw),3)))
        self.assertFalse(self.s.missing(self.job));calls=self.llm.call_count
        self.job['last_media_use']=0;self.s.write(self.job);self.s.store.cleanup()
        self.assertFalse(self.s.preview_path(self.job).exists())
        output=self.s.perform(self.job,'export',{},lambda **kw:None)
        self.assertEqual(self.llm.call_count,calls)
        with zipfile.ZipFile(self.s.file(self.job['id'],'exports',output['exports'][-1]['file'])) as z:
            self.assertEqual(set(z.namelist()),{'article.md','article.html','images/step-01.jpg','images/step-02.jpg'})
            self.assertIn('images/step-01.jpg',z.read('article.md').decode())
            self.assertIn('<img src="images/step-01.jpg"',z.read('article.html').decode())

    def test_ranges_exclusion_and_local_no_remote_subtitle(self):
        self.media_job();sid=self.job['article']['steps'][0]['id']
        with self.assertRaises(ValueError):self.s.step(self.job['id'],sid,dict(version=self.job['version'],range=[1,3]))
        with self.assertRaises(ValueError):self.s.step(self.job['id'],sid,dict(version=self.job['version'],range=[0,1],time=1.5))
        self.job=self.s.step(self.job['id'],sid,dict(version=self.job['version'],range=[0,2],time=1.5))
        self.job=self.s.step(self.job['id'],sid,dict(version=self.job['version'],range=[0,1]))
        self.assertIsNone(self.job['selections'][sid]['time'])
        with self.assertRaises(ValueError):doc.crop({'x':0,'y':0,'size':1},160,288,[.25,.25])
        with patch.object(media,'remote_info',side_effect=AssertionError('must not fetch remote subtitles')):
            result=self.s.perform(self.job,'transcribe',{},lambda **kw:None)
        self.assertEqual(result['transcript_status'],'unavailable')

    def test_rotated_video_preview_and_extraction_agree(self):
        rotated=Path(self.temp.name)/'rotated.mp4'
        media.run(['ffmpeg','-v','error','-display_rotation:v:0','90','-i',str(self.video),'-c','copy','-y',str(rotated)])
        self.apply();self.job=self.s.replace_asset(self.job['id'],self.job['version'],rotated,'rotated.mp4')
        self.job.update(self.s.perform(self.job,'prepare',{},lambda **kw:None));self.s.write(self.job)
        self.assertEqual((self.job['info']['width'],self.job['info']['height']),(288,160))
        sid=self.job['article']['steps'][0]['id'];self.job=self.s.step(self.job['id'],sid,dict(version=self.job['version'],range=[0,2],time=.5))
        result=self.s.perform(self.job,'capture',{'step_id':sid},lambda **kw:None)
        path=self.s.file(self.job['id'],'images',result['selections'][sid]['image'])
        raw=media.run(['ffmpeg','-v','error','-i',str(path),'-f','rawvideo','-pix_fmt','rgb24','-'])
        self.assertEqual(len(raw),160*160*3)
        self.assertTrue(all(raw[i+1]>80 and raw[i]<40 and raw[i+2]<40 for i in range(0,len(raw),3)))


class ArticleRoutesTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.old=appmod.app.config.get('ARTICLES_DATA_DIR');appmod.app.config['ARTICLES_DATA_DIR']=self.temp.name
        self.addCleanup(lambda:appmod.app.config.update(ARTICLES_DATA_DIR=self.old))
        self.client=appmod.app.test_client()
        self.cells={'itemId':'dish-1','status':'✅ 업로드 완료','dish':'볶음밥','title':'볶음밥','desc':'밥 1공기를 볶기','youtubeUrl':URL}
        self.result={'ok':True,'article_version':1,'row':3,'cells':self.cells,'revision':'a'*64}
        for name,value in [('get_sheet_setting',{'sheet_id':'sheet','gid':'0'}),('get_upload_setting',{'url':'url','token':'token'}),('upload_connection','connection'),('get_article_settings',CFG)]:
            p=patch.object(appmod,name,return_value=value);p.start();self.addCleanup(p.stop)
        self.sheet=patch.object(appmod,'sheet_call',return_value=self.result).start();self.addCleanup(patch.stopall)

    def test_page_settings_and_creation(self):
        self.assertIn('ab-panel',self.client.get('/helper').text)
        self.assertIn('article',self.client.get('/api/helper/settings').json)
        response=self.client.post('/api/articles/jobs',json={'connection':'connection','item_id':'dish-1'})
        self.assertEqual(response.status_code,201);self.assertEqual(response.json['source']['youtube_url'],URL)
        ident=response.json['id']
        self.assertEqual(self.client.patch('/api/articles/jobs/'+ident,json={'version':0,'article':response.json['article']}).status_code,409)
        self.assertEqual(self.client.get('/api/articles/jobs/'+ident+'/preview').status_code,200)
        self.assertEqual(self.client.get('/api/articles/jobs/'+ident+'/files/images/not-a-file').status_code,404)

    def test_status_connection_and_legacy_script(self):
        self.assertEqual(self.client.post('/api/articles/jobs',json={'connection':'other','item_id':'dish-1'}).status_code,409)
        self.cells['status']='촬영 중';self.assertEqual(self.client.get('/api/articles/items/dish-1').status_code,409)
        self.sheet.side_effect=appmod.SheetCallError('old',409,'unknown_action')
        response=self.client.get('/api/articles/items/dish-1');self.assertEqual(response.status_code,409);self.assertIn('15',response.json['error'])

    def test_supplement_maps_only_required_fields_and_request_id(self):
        response=self.client.patch('/api/articles/items/dish-1',json={'connection':'connection','revision':'a'*64,'request_id':'r'*20,'fields':{'youtube_url':'https://youtu.be/abcdefghijk'}})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.sheet.call_args.args[0],'article_fill_missing')
        self.assertEqual(self.sheet.call_args.kwargs['fields'],{'youtubeUrl':URL})
        response=self.client.patch('/api/articles/items/dish-1',json={'connection':'connection','revision':'a'*64,'request_id':'r'*20,'fields':{'status':'uploaded'}})
        self.assertEqual(response.status_code,400)
        response=self.client.post('/api/articles/jobs',headers={'Origin':'https://evil.test'},json={})
        self.assertEqual(response.status_code,403)


if __name__=='__main__':unittest.main()
