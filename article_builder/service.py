"""Versioned article jobs. Slow operations publish only against their input version."""
import copy
import hashlib
import json
import math
import os
import shutil
import threading
import time
import zipfile
from pathlib import Path
from hooks import media, subtitles
from hooks.store import Store, uid, valid_id
from cooking_audio import asr
from . import analysis, document as doc

NORMALIZE = 'scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1'


class Conflict(ValueError):
    pass


class Service:
    def __init__(self, root, llm):
        self.store, self.llm = Store(root), llm
        self.last_cleanup = 0

    def get(self, ident):
        return self.store.read('jobs', ident)

    def write(self, state):
        self.store.write('jobs', state['id'], state)
        return state

    def check(self, state, version):
        if type(version) is not int or version != state['version']:
            raise Conflict('다른 탭 또는 작업에서 초안이 변경되었습니다. 현재 입력을 복사한 뒤 최신 초안을 다시 열어주세요.')

    def create(self, item, connection, cfg):
        fields = doc.source(doc.source_fields(item))
        ident = uid()
        return self.write(dict(id=ident, version=1, connection=connection, item_id=item['item_id'],
            dish=item.get('dish_title', ''), source=fields, settings=copy.deepcopy(cfg),
            article=dict(title=fields['title'], intro='', ingredients='', closing='', steps=[]),
            answers=[], questions=[], pending_article=None, selections={}, suggestions={}, excluded=[0, 0],
            info=None, local=False, cues=[], transcript_status='not_prepared', exports=[], usage={},
            busy=False, status='draft', message='레시피로 아티클을 생성하거나 직접 작성하세요.',
            created=time.time(), last_media_use=time.time()))

    def public(self, state):
        result = copy.deepcopy(state)
        result.pop('cues', None)
        result['cue_count'] = len(state.get('cues', []))
        result['media_ready'] = self.preview_path(state).is_file()
        result['can_export'] = not self.missing(state)
        result['missing'] = self.missing(state)
        return result

    def default_crop(self, state):
        w, h = state['info']['width'], state['info']['height']
        top, bottom = state['excluded']
        lo, hi = math.ceil(top*h), math.floor((1-bottom)*h)
        size = min(w, hi-lo)
        return dict(x=(w-size)/2/w, y=(lo+(hi-lo-size)/2)/h, size=size/w)

    def patch(self, ident, data):
        with self.store.lock:
            s = self.get(ident); self.check(s, data.get('version'))
            if set(data) - {'version', 'article', 'settings', 'answers', 'excluded', 'source'}:
                raise ValueError('지원하지 않는 초안 변경입니다.')
            if 'source' in data:
                fields = doc.source(data['source'])
                if s['busy']:
                    raise Conflict('영상 준비가 끝난 뒤 원본 정보를 변경해주세요.')
                if fields['youtube_url'] != s['source']['youtube_url']:
                    s.update(info=None, local=False, local_name=None, cues=[], suggestions={}, selections={}, transcript_status='not_prepared')
                    for name in ('source', 'preview'):
                        shutil.rmtree(self.store.directory('jobs', ident)/name, ignore_errors=True)
                elif fields != s['source']:
                    for selected in s['selections'].values(): selected['review'] = True; selected['confirmed'] = False
                s['source'] = fields
            if 'article' in data:
                new = doc.article(data['article'])
                old = {x['id']: x for x in s['article']['steps']}
                selections, suggestions = {}, {}
                for step in new['steps']:
                    sid = step['id']
                    selected = copy.deepcopy(s['selections'].get(sid, {}))
                    if step != old.get(sid):
                        selected.update(review=True, confirmed=False)
                    selections[sid] = selected
                    suggestions[sid] = s['suggestions'].get(sid, []) if step == old.get(sid) else []
                s.update(article=new, selections=selections, suggestions=suggestions)
            if 'settings' in data:
                s['settings'] = doc.settings(data['settings'], s['settings'])
            if 'answers' in data:
                answers = data['answers']
                if not isinstance(answers, list) or len(answers) > 30:
                    raise ValueError('추가 답변은 최대 30개입니다.')
                s['answers'] = [dict(question=doc.text(a.get('question'), 1000, True), answer=doc.text(a.get('answer'), 5000, True)) for a in answers]
            if 'excluded' in data:
                if not s['info']:
                    raise ValueError('영상을 먼저 준비해주세요.')
                values = data['excluded']
                if not isinstance(values, list) or len(values) != 2:
                    raise ValueError('상하 제외 영역이 잘못되었습니다.')
                values = list(map(doc.number, values))
                if any(n < 0 or n >= 1 for n in values) or sum(values) >= .98:
                    raise ValueError('상하 제외 영역을 줄여주세요.')
                if values != s['excluded']:
                    s['excluded'] = values
                    for selection in s['selections'].values():
                        selection.update(crop=self.default_crop(s), confirmed=False, review=True, dirty=True)
            s['version'] += 1
            return self.write(s)

    def apply_article(self, ident, version):
        with self.store.lock:
            s = self.get(ident); self.check(s, version)
            if not s.get('pending_article'):
                raise ValueError('적용할 생성 결과가 없습니다.')
            s.update(article=s['pending_article'], pending_article=None, selections={}, suggestions={}, version=s['version']+1)
            return self.write(s)

    def step(self, ident, sid, data):
        with self.store.lock:
            s = self.get(ident); self.check(s, data.get('version'))
            if not s['info'] or sid not in {x['id'] for x in s['article']['steps']}:
                raise ValueError('영상과 조리 단계를 먼저 준비해주세요.')
            selected = s['selections'].setdefault(sid, {})
            if data.get('remove_image') is True:
                s['selections'][sid] = {}
                s['version'] += 1
                return self.write(s)
            if 'range' in data:
                span = doc.interval(data['range'], s['info']['duration'])
                if span != selected.get('range'):
                    selected['candidates'] = []
                selected['range'] = span
                if selected.get('time') is not None and not selected['range'][0] <= selected['time'] < selected['range'][1]:
                    selected.update(confirmed=False, review=True, dirty=True, time=None)
            if 'time' in data:
                value = doc.number(data['time'])
                span = selected.get('range')
                if not span or not span[0] <= value < span[1]:
                    raise ValueError('선택 시점은 지정한 구간 안에 있어야 합니다.')
                if value != selected.get('time'):
                    selected.update(time=value, confirmed=False, dirty=True, review=True)
            if 'crop' in data:
                doc.crop(data['crop'], s['info']['width'], s['info']['height'], s['excluded'])
                if data['crop'] != selected.get('crop'):
                    selected.update(crop=data['crop'], confirmed=False, dirty=True, review=True)
            selected.setdefault('crop', self.default_crop(s))
            if data.get('confirm'):
                if not selected.get('image') or selected.get('dirty') or selected.get('time') is None:
                    raise ValueError('먼저 현재 장면의 이미지를 추출해주세요.')
                if not self.file(ident, 'images', selected['image']).is_file():
                    raise ValueError('확정할 이미지 파일이 없습니다.')
                selected.update(confirmed=True, review=False)
            s['version'] += 1
            return self.write(s)

    def replace_asset(self, ident, version, asset_path, asset_name):
        with self.store.lock:
            s = self.get(ident); self.check(s, version)
            if s['busy']: raise Conflict('진행 중인 작업이 끝난 뒤 영상을 교체해주세요.')
            # Probe before replacing anything. Copy into this job so shared upload cleanup is safe.
            media.probe(asset_path)
            directory = self.store.directory('jobs', ident)/'source'
            directory.mkdir(exist_ok=True)
            dest = directory/('local-'+uid()+'.mp4')
            try: os.link(asset_path, dest)
            except OSError: shutil.copyfile(asset_path, dest)
            for path in directory.iterdir():
                if path != dest: path.unlink()
            shutil.rmtree(self.store.directory('jobs', ident)/'preview', ignore_errors=True)
            s.update(local=True, local_name=dest.name, video_name=asset_name, info=None, cues=[], suggestions={}, selections={},
                     transcript_status='not_prepared', version=s['version']+1, last_media_use=time.time())
            return self.write(s)

    def start(self, ident, version, operation, params=None):
        with self.store.lock:
            s = self.get(ident); self.check(s, version)
            if s['busy']: raise Conflict('이미 작업 중입니다. 완료 후 다시 시도해주세요.')
            if operation not in ('generate', 'prepare', 'transcribe', 'suggest', 'capture', 'candidates', 'export'):
                raise ValueError('지원하지 않는 작업입니다.')
            token = uid()
            s.update(busy=True, operation=operation, operation_id=token, status='working', message='작업을 시작합니다.', usage={} if operation in ('generate','suggest') else s['usage'])
            self.write(s)
            snapshot = copy.deepcopy(s)
        def notify(**changes):
            with self.store.lock:
                live = self.get(ident)
                if live.get('operation_id') == token:
                    live.update(changes); self.write(live)
        def worker():
            try:
                updates = self.perform(snapshot, operation, params or {}, notify)
                with self.store.lock:
                    live = self.get(ident)
                    if live['version'] == snapshot['version']:
                        live.update(updates); live.update(version=live['version']+1, status='ready', message=updates.get('message', '작업 완료'))
                    else:
                        live.update(status='stale', message='실행 중 초안이 변경되어 결과를 적용하지 않았습니다. 최신 내용으로 다시 실행해주세요.')
                    live['busy'] = False; self.write(live)
            except Exception as e:
                notify(busy=False, status='error', message=str(e)[:1500])
        threading.Thread(target=worker, daemon=True).start()
        return self.public(s)

    def source_path(self, state):
        directory = self.store.directory('jobs', state['id'])/'source'
        if state['local']:
            path = directory/state['local_name']
            if not path.is_file(): raise ValueError('로컬 영상이 정리되었습니다. 동일한 파일을 다시 선택해주세요.')
            return path
        return media.download(state['source']['youtube_url'], directory, lambda _: None)

    def preview_path(self, state):
        return self.store.directory('jobs', state['id'])/'preview'/'video.mp4'

    def file(self, ident, kind, name):
        if kind not in ('images', 'exports', 'candidates') or not isinstance(name, str) or not __import__('re').fullmatch(r'[a-f0-9]{32}\.(jpg|zip)', name):
            raise FileNotFoundError('파일을 찾지 못했습니다.')
        return self.store.directory('jobs', ident)/kind/name

    def frame(self, state, stamp, output, crop=None):
        if not self.preview_path(state).is_file():
            raise ValueError('영상을 다시 준비해주세요.')
        source = self.source_path(state)
        filters = NORMALIZE
        if crop:
            x,y,size = doc.crop(crop, state['info']['width'], state['info']['height'], state['excluded'])
            side = min(1080, size)
            filters += f',crop={size}:{size}:{x}:{y}:exact=1,scale={side}:{side}'
        else:
            filters += ',scale=320:-2'
        output.parent.mkdir(exist_ok=True)
        with media.ENCODING:
            media.run(['ffmpeg','-v','error','-nostdin','-y','-ss',str(stamp),'-i',str(source),'-map','0:v:0','-frames:v','1','-vf',filters,'-q:v','2',str(output)])
        if not output.is_file() or not output.stat().st_size:
            raise ValueError('이 시점에서 프레임을 읽지 못했습니다. 조금 앞 장면을 선택해주세요.')

    def perform(self, s, operation, params, notify):
        ident = s['id']
        if operation in ('generate', 'suggest'):
            runner = analysis.Runner(self.store.root/'cache', self.llm, s['settings'], lambda u: notify(usage=u), force=params.get('force') is True)
            return (analysis.generate(s, runner) if operation == 'generate' else analysis.suggest(s, runner))
        if operation == 'prepare':
            notify(message='영상 다운로드·재생 준비 중 · LLM 사용 없음')
            source = self.source_path(s)
            preview = self.preview_path(s); preview.parent.mkdir(exist_ok=True)
            if not preview.is_file():
                temp = preview.with_name(uid()+'.mp4')
                try:
                    with media.ENCODING:
                        media.run(['ffmpeg','-v','error','-nostdin','-y','-i',str(source),'-map','0:v:0','-map','0:a:0?', '-vf', NORMALIZE,
                            '-c:v','libx264','-crf','18','-preset','fast','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart','-map_metadata','-1',str(temp)], 14400)
                    os.replace(temp, preview)
                finally: temp.unlink(missing_ok=True)
            info = media.probe(preview)
            return dict(info=info, last_media_use=time.time(), message='영상 준비 완료. 구간을 직접 지정하거나 자막을 준비하세요.')
        if operation == 'transcribe':
            if not s['info'] or not self.preview_path(s).is_file():
                raise ValueError('영상을 먼저 준비해주세요.')
            if s.get('cues') and not params.get('force'):
                return dict(message='준비된 자막을 재사용합니다.')
            cues, origin = None, None
            notify(message='자막 확인 중 · LLM 사용 없음')
            source = self.source_path(s)
            if s['local']:
                info = media.probe(source)
                if info['subtitles']:
                    cues = subtitles.parse(media.embedded(source, info['subtitles'][0]), preserve_context=True); origin='embedded'
            else:
                result = media.remote_subtitle(media.remote_info(s['source']['youtube_url']))
                if result:
                    raw, fmt, origin = result
                    cues = subtitles.parse(raw, fmt, preserve_context=True)
            if not cues:
                if not s['info'].get('audio_codec'):
                    return dict(cues=[], transcript_status='unavailable', message='음성이 없습니다. 구간을 직접 지정해주세요.')
                if not asr.config()['available']:
                    return dict(cues=[], transcript_status='unavailable', message='자막이 없고 로컬 음성 인식이 설치되지 않았습니다. 구간을 직접 지정하거나 scripts/setup-cooking-asr.sh로 설치해주세요.')
                cues, _ = asr.transcribe(source, 0, s['info']['duration'], self.store.root/'cache', lambda m: notify(message=m), lambda: False)
                origin='asr'
            return dict(cues=cues, transcript_status=origin, message='자막 준비 완료. 구간 자동 제안은 LLM을 사용합니다.', last_media_use=time.time())
        if operation in ('capture', 'candidates'):
            if not s['info']: raise ValueError('영상을 먼저 준비해주세요.')
            sid = params.get('step_id')
            ids = [sid] if sid else [k for k,v in s['selections'].items() if v.get('time') is not None and v.get('image')]
            selections = copy.deepcopy(s['selections'])
            for sid in ids:
                if sid not in {step['id'] for step in s['article']['steps']}:
                    raise ValueError('조리 단계를 찾지 못했습니다.')
                selected = selections.get(sid, {})
                span = doc.interval(selected.get('range'), s['info']['duration'])
                if operation == 'candidates':
                    selected['candidates'] = []
                    for fraction in (.1,.3,.5,.7,.9):
                        stamp = span[0] + (span[1]-span[0])*fraction
                        name = uid()+'.jpg'
                        self.frame(s, stamp, self.file(ident, 'candidates', name))
                        selected['candidates'].append(dict(time=stamp, image=name))
                else:
                    stamp = doc.number(selected.get('time'))
                    if not span[0] <= stamp < span[1]: raise ValueError('선택 시점이 구간 밖입니다.')
                    name = uid()+'.jpg'
                    self.frame(s, stamp, self.file(ident, 'images', name), selected.get('crop') or self.default_crop(s))
                    selected.update(image=name, dirty=False, confirmed=False, review=True)
                selections[sid] = selected
            return dict(selections=selections, last_media_use=time.time())
        if operation == 'export':
            missing = self.missing(s)
            if missing: raise ValueError('내보내기 전에 확인해주세요: ' + ', '.join(missing))
            directory = self.store.directory('jobs', ident)/'exports'; directory.mkdir(exist_ok=True)
            name = uid()+'.zip'; path=directory/name
            links = {step['id']: f'images/step-{n:02d}.jpg' for n,step in enumerate(s['article']['steps'],1)
                     if s['selections'].get(step['id'], {}).get('image')}
            md = doc.markdown(s, links)
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
                z.writestr('article.md', md)
                z.writestr('article.html', doc.html_page(doc.render(md, links.values())))
                for sid, target in links.items(): z.write(self.file(ident,'images',s['selections'][sid]['image']), target)
            return dict(exports=s['exports']+[dict(file=name, created=time.time(), version=s['version'], title=s['article']['title'])])
        raise ValueError('지원하지 않는 작업입니다.')

    def missing(self, state):
        a = state['article']; problems=[]
        if not a['title'].strip(): problems.append('제목')
        if not a['ingredients'].strip(): problems.append('재료')
        if not a['steps']: problems.append('조리 단계')
        for n, step in enumerate(a['steps'],1):
            v = state['selections'].get(step['id'], {})
            if not step['body'].strip():
                problems.append(f'{n}번 단계 본문')
            if not v.get('image'):
                continue
            ok = bool(step['body'].strip() and v.get('confirmed') and not v.get('review') and not v.get('dirty') and v.get('image'))
            try:
                span=doc.interval(v.get('range'), state['info']['duration'])
                ok = ok and span[0] <= doc.number(v.get('time')) < span[1] and self.file(state['id'],'images',v['image']).is_file()
                if ok: doc.crop(v['crop'], state['info']['width'],state['info']['height'],state['excluded'])
            except (ValueError, TypeError, KeyError): ok=False
            if not ok: problems.append(f'{n}번 단계 본문·구간·이미지 확정')
        return problems

    def preview_document(self, s):
        links = {step['id']: '/api/articles/jobs/'+s['id']+'/files/images/'+s['selections'][step['id']]['image']
                 for step in s['article']['steps'] if s['selections'].get(step['id'],{}).get('image')}
        return doc.html_page(doc.render(doc.markdown(s, links), links.values()))
