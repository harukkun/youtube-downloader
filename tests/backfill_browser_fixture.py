"""Local-only UI fixture: no Google, Drive, AI, or user-setting writes."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import upload_browser_fixture as fixture

m = fixture.m
records = fixture.records
for item in records.values():
    item['status'] = '✅ 업로드 완료'
records['item-0']['desc'] = '김치 100g과 밥 1공기를 볶는다.'
records['item-1']['desc'] = ''
records['item-2']['cookVersion'] = '1'
original_call = m.sheet_call


def call(action, **kwargs):
    if action == 'cook_backfill_list':
        return {'ok': True, 'items': [{'itemId': key, 'title': item['dish'], 'revision': fixture.revision(item)}
                                    for key, item in records.items() if not item.get('cookVersion')],
                'skipped': sum(bool(item.get('cookVersion')) for item in records.values()), 'errors': [], 'nextCursor': None}
    if action in ('cook_backfill_get', 'cook_backfill_save'):
        item = records[kwargs['itemId']]
        if item.get('cookVersion'):
            return {'ok': True, 'skipped': True}
        if fixture.revision(item) != kwargs['revision']:
            raise m.SheetCallError('설명글이 변경되었습니다. 대상을 다시 확인해 주세요.', 409, 'conflict')
        if not item['desc']:
            raise m.SheetCallError('현황판에 재료와 만드는 순서가 포함된 설명글을 채워 주세요.', 400)
        if action == 'cook_backfill_get':
            return {'ok': True, 'item': {'title': item['dish'], 'description': item['desc'], 'sourceChannel': item['srcChannel']}}
        item['cookVersion'] = '1'
        fixture.control['backfill_saves'] = fixture.control.get('backfill_saves', 0) + 1
        return {'ok': True, 'saved': True}
    return original_call(action, **kwargs)


m.sheet_call = call
if __name__ == '__main__':
    m.app.run(host='127.0.0.1', port=int(os.environ.get('BACKFILL_FIXTURE_PORT', '8882')), threaded=True)
