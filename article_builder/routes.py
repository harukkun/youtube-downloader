"""Article HTTP boundary: same-origin writes, revision checks and opaque file IDs."""
import hashlib
import json
import os
import re
import shutil
import threading
import time
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit
from flask import Blueprint, current_app, jsonify, request, send_file, Response
from .service import Service, Conflict
from . import document as doc


def install(app, host):
    bp = Blueprint('articles', __name__, url_prefix='/api/articles')
    services, lock = {}, threading.Lock()

    def service():
        root = str(current_app.config.get('ARTICLES_DATA_DIR') or os.environ.get('ARTICLES_DATA_DIR') or Path.home()/'.youtube-downloader/articles')
        with lock:
            if root not in services: services[root] = Service(root, lambda *a: host['llm_structured'](*a))
            s = services[root]
        if time.time()-s.last_cleanup > 3600:
            s.last_cleanup=time.time(); s.store.cleanup()
        return s

    def connection():
        cfg = host['get_sheet_setting']()
        if not cfg: raise ValueError('먼저 쇼츠 현황판에 구글 시트를 연결해주세요.')
        return host['upload_connection']() or hashlib.sha256(json.dumps([cfg['sheet_id'],cfg.get('gid')]).encode()).hexdigest()

    def body():
        data=request.get_json(silent=True)
        if not isinstance(data, dict): raise ValueError('JSON 객체가 필요합니다.')
        return data

    def same_connection(value):
        if value != connection(): raise Conflict('연결된 시트가 변경되었습니다. 항목을 다시 선택해주세요.')

    def read_item(item_id):
        doc.text(item_id,200,True)
        if host['get_upload_setting']():
            try:
                raw=host['sheet_call']('article_get',itemId=item_id)
            except host['SheetCallError'] as e:
                if e.code in ('unknown_action', 'upgrade_required'):
                    raise Conflict('아티클 빌더를 사용하려면 Apps Script 버전 15 이상을 새로 배포해주세요.') from e
                raise
            if raw.get('article_version') != 1:
                raise Conflict('아티클 빌더용 Apps Script 새 버전을 배포해주세요.')
            result=host['upload_result'](raw,host['get_sheet_setting'](),expected_id=item_id)
            result['connection']=connection(); result['can_write']=True
        else:
            cfg=host['get_sheet_setting']()
            if not cfg: raise ValueError('쇼츠 현황판의 시트 연결이 필요합니다.')
            items,_=host['load_sheet_items'](cfg['sheet_id'],cfg.get('gid'))
            matches=[i for i in items if i['item_id']==item_id]
            if len(matches)!=1: raise Conflict('항목 ID가 없거나 중복되었습니다. 현황판을 확인해주세요.')
            result=dict(item=matches[0],connection=connection(),can_write=False,revision=None)
        if result['item']['status'] != 'uploaded': raise Conflict('업로드 완료된 항목만 사용할 수 있습니다.')
        return result

    def guarded(fn):
        @wraps(fn)
        def wrapped(*a, **kw):
            try: return fn(*a, **kw)
            except host['SheetCallError'] as e:
                return host['upload_error'](e)
            except Conflict as e: return jsonify(error=str(e),code='conflict'),409
            except FileNotFoundError as e: return jsonify(error=str(e)),404
            except (ValueError,TypeError,KeyError) as e: return jsonify(error=str(e)),400
            except (RuntimeError,OSError) as e: return jsonify(error=str(e)),502
            except Exception:
                current_app.logger.exception('article builder request failed')
                return jsonify(error='아티클 빌더 서버 처리 중 오류가 발생했습니다. 서버 로그를 확인해주세요.',code='article_internal_error'),500
        return wrapped

    @bp.before_request
    def origin():
        if request.method not in ('GET','HEAD','OPTIONS'):
            value=request.headers.get('Origin')
            if (value and urlsplit(value).netloc != request.host) or request.headers.get('Sec-Fetch-Site')=='cross-site':
                return jsonify(error='앱 페이지에서 다시 시도해주세요.'),403

    @bp.get('/items/<item_id>')
    @guarded
    def item(item_id):
        if request.args.get('connection'): same_connection(request.args['connection'])
        return jsonify(read_item(item_id))

    @bp.patch('/items/<item_id>')
    @guarded
    @host['board_write_serialized']
    def fill(item_id):
        data=body(); same_connection(data.get('connection'))
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',data.get('request_id','')) or not re.fullmatch(r'[a-f0-9]{64}',data.get('revision','')):
            raise ValueError('저장 요청 번호와 최신 정보 버전이 필요합니다.')
        fields=data.get('fields')
        if not isinstance(fields,dict) or not fields or set(fields)-{'title','description','youtube_url'}:
            raise ValueError('누락된 제목·설명·유튜브 링크만 보완할 수 있습니다.')
        clean={}
        for k,v in fields.items():
            clean[{'title':'title','description':'desc','youtube_url':'youtubeUrl'}[k]]=doc.text(v,{'title':500,'description':30000,'youtube_url':2000}[k],True)
            if k=='youtube_url': clean['youtubeUrl']=doc.youtube_url(v)
        raw=host['sheet_call']('article_fill_missing',itemId=item_id,revision=data['revision'],requestId=data['request_id'],fields=clean)
        if raw.get('article_version') != 1: raise Conflict('아티클 빌더용 Apps Script 새 버전을 배포해주세요.')
        result=host['upload_result'](raw,host['get_sheet_setting'](),remember=True,expected_id=item_id)
        result.update(connection=connection(),can_write=True)
        return jsonify(result)

    @bp.route('/jobs',methods=['GET','POST'])
    @guarded
    def jobs():
        s=service()
        if request.method=='GET':
            with s.store.lock:
                jobs=[s.get(p.parent.name) for p in (s.store.root/'jobs').glob('*/state.json')]
            return jsonify(jobs=[{k:j.get(k) for k in ('id','item_id','connection','dish','source','updated','busy','status')} for j in sorted(jobs,key=lambda j:j['updated'],reverse=True)])
        data=body(); same_connection(data.get('connection'))
        selected=read_item(data.get('item_id',''))
        return jsonify(s.public(s.create(selected['item'],selected['connection'],host['get_article_settings']()))),201

    @bp.route('/jobs/<ident>',methods=['GET','PATCH','DELETE'])
    @guarded
    def job(ident):
        s=service()
        if request.method=='GET': return jsonify(s.public(s.get(ident)))
        data=body()
        if request.method=='DELETE':
            with s.store.lock:
                state=s.get(ident); s.check(state,data.get('version'))
                if state['busy']: raise Conflict('진행 중인 작업이 끝난 뒤 삭제해주세요.')
                shutil.rmtree(s.store.directory('jobs',ident))
            return jsonify(ok=True)
        if 'settings' in data:
            cfg=doc.settings(data['settings'],s.get(ident)['settings'])
            host['normalize_recipe_settings']({k:cfg[k] for k in ('backend','model')})
        return jsonify(s.public(s.patch(ident,data)))

    @bp.post('/jobs/<ident>/apply')
    @guarded
    def apply(ident):
        s=service(); return jsonify(s.public(s.apply_article(ident,body().get('version'))))

    @bp.patch('/jobs/<ident>/steps/<sid>')
    @guarded
    def step(ident,sid):
        s=service(); return jsonify(s.public(s.step(ident,sid,body())))

    @bp.post('/jobs/<ident>/asset')
    @guarded
    def asset(ident):
        s=service(); data=body(); shared=current_app.extensions['hooks_service']()
        with shared.store.lock:
            record=shared.store.read('assets',data.get('asset_id'))
            path=shared.store.directory('assets',record['id'])/'video'
            return jsonify(s.public(s.replace_asset(ident,data.get('version'),path,record['name'])))

    @bp.post('/jobs/<ident>/operations/<operation>')
    @guarded
    def operation(ident,operation):
        data=body()
        return jsonify(service().start(ident,data.get('version'),operation,data)),202

    @bp.get('/jobs/<ident>/video')
    @guarded
    def video(ident):
        s=service()
        with s.store.lock:
            state=s.get(ident); s.store.update(ident,last_media_use=time.time()); path=s.preview_path(state)
            if not path.is_file(): raise FileNotFoundError('영상을 다시 준비해주세요.')
        return send_file(path,mimetype='video/mp4',conditional=True)

    @bp.get('/jobs/<ident>/preview')
    @guarded
    def preview(ident):
        s=service(); response=Response(s.preview_document(s.get(ident)),mimetype='text/html')
        response.headers['Content-Security-Policy']="default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
        return response

    @bp.get('/jobs/<ident>/text')
    @guarded
    def copy_text(ident):
        state=service().get(ident)
        md=doc.markdown(state,plain=True)
        # Clipboard text retains numbering and image positions, without Markdown escapes.
        return jsonify(text=re.sub(r'\\([\\`*_{}\[\]<>#!])',r'\1',re.sub(r'^#{1,6}\s+','',md,flags=re.M)))

    @bp.get('/jobs/<ident>/files/<kind>/<name>')
    @guarded
    def file(ident,kind,name):
        s=service(); state=s.get(ident)
        path=s.file(ident,kind,name)
        if kind=='exports' and not any(e['file']==name for e in state['exports']): raise FileNotFoundError('완료된 내보내기만 다운로드할 수 있습니다.')
        return send_file(path,as_attachment=kind=='exports',download_name='article.zip' if kind=='exports' else name)

    app.register_blueprint(bp)
    app.extensions['articles_service']=service
