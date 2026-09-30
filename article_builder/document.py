"""Validated article data and deliberately small, safe Markdown renderer."""
import html
import math
import re
from pathlib import Path
from urllib.parse import urlsplit
from hooks.media import youtube_url
from hooks.store import uid, valid_id

DEFAULT_TEMPLATE = (Path(__file__).parent / 'templates/article-template.md').read_text(encoding='utf-8')
SLOTS = {'title', 'intro', 'ingredients', 'steps', 'closing', 'youtube_url'}
DEFAULT_INSTRUCTIONS = '한국어 정보 중심 레시피 글. 간결하고 친근한 존댓말. 실제 경험이나 원본에 없는 재료·분량·시간은 만들지 마세요.'


def text(value, limit=30000, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()) or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', value):
        raise ValueError(f'텍스트는 {limit:,}자 이내로 입력해주세요.' + (' 필수 항목입니다.' if required else ''))
    return value.strip()


def template(value):
    value = text(value, required=True)
    slots = re.findall(r'{{\s*([^{}]+?)\s*}}', value)
    if any(s not in SLOTS for s in slots) or any(slots.count(s) != 1 for s in ('steps', 'youtube_url')):
        raise ValueError('지원하는 슬롯만 사용하고 {{steps}}, {{youtube_url}}을 각각 한 번 포함해주세요.')
    return value


def settings(raw, base):
    if not isinstance(raw, dict):
        raise ValueError('아티클 설정 형식이 잘못되었습니다.')
    out = dict(base)
    if 'template' in raw:
        out['template'] = template(raw['template'])
    if 'instructions' in raw:
        out['instructions'] = text(raw['instructions'], 5000)
    for key in ('backend', 'model'):
        if key in raw:
            out[key] = text(raw[key], 100, True)
    return out


def source_fields(item):
    return dict(title=item.get('video', {}).get('title', ''),
                description=item.get('video', {}).get('description', ''),
                youtube_url=item.get('platforms', {}).get('youtube', {}).get('url', ''))


def source(raw):
    if not isinstance(raw, dict):
        raise ValueError('영상 정보가 필요합니다.')
    out = {k: text(raw.get(k), n, True) for k, n in [('title', 500), ('description', 30000), ('youtube_url', 2000)]}
    out['youtube_url'] = youtube_url(out['youtube_url'])
    return out


def article(raw, new_ids=False):
    if not isinstance(raw, dict):
        raise ValueError('아티클 형식이 잘못되었습니다.')
    out = {k: text(raw.get(k, ''), 20000) for k in ('title', 'intro', 'ingredients', 'closing')}
    steps = raw.get('steps')
    if not isinstance(steps, list) or len(steps) > 100:
        raise ValueError('조리 단계는 최대 100개입니다.')
    out['steps'] = []
    seen = set()
    for s in steps:
        if not isinstance(s, dict):
            raise ValueError('조리 단계 형식이 잘못되었습니다.')
        ident = uid() if new_ids else valid_id(s.get('id'))
        if ident in seen:
            raise ValueError('조리 단계 ID가 중복되었습니다.')
        seen.add(ident)
        out['steps'].append(dict(id=ident, title=text(s.get('title', ''), 500), body=text(s.get('body', ''), 10000)))
    return out


def number(value):
    if isinstance(value, bool):
        raise ValueError('유효한 숫자를 입력해주세요.')
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError('유효한 숫자를 입력해주세요.')
    if not math.isfinite(n):
        raise ValueError('유효한 숫자를 입력해주세요.')
    return n


def interval(raw, duration):
    if not isinstance(raw, list) or len(raw) != 2:
        raise ValueError('시작·끝 시간을 입력해주세요.')
    start, end = map(number, raw)
    if not 0 <= start < end <= duration:
        raise ValueError('구간은 영상 범위 안에서 시작 < 끝이어야 합니다.')
    return [start, end]


def crop(raw, width, height, excluded):
    if not isinstance(raw, dict):
        raise ValueError('크롭 영역이 필요합니다.')
    x, y, size = [number(raw.get(k)) for k in ('x', 'y', 'size')]
    top, bottom = excluded
    # Coordinates are fractions of displayed width/height; size is in width units.
    x, y, size = round(x * width), round(y * height), round(size * width)
    if size < 2 or x < 0 or y < math.ceil(top * height) or x + size > width or y + size > math.floor((1-bottom) * height):
        raise ValueError('정사각형을 상하 제외 영역 안으로 이동해주세요.')
    return x, y, size


def _literal(value):
    return re.sub(r'([\\`*_{}\[\]<>#!])', r'\\\1', value)


def markdown(state, image_urls=None, plain=False):
    a = state['article']
    blocks = []
    for i, step in enumerate(a['steps'], 1):
        blocks.append(f"### {i}. {_literal(step['title'])}\n\n{_literal(step['body'])}")
        link = (image_urls or {}).get(step['id'])
        if link and not plain:
            blocks.append(f'![{i}번 조리 과정]({link})')
        elif plain and state.get('selections', {}).get(step['id'], {}).get('image'):
            blocks.append(f'[{i}번 이미지]')
    values = {k: _literal(a[k]) for k in ('title', 'intro', 'ingredients', 'closing')}
    values.update(steps='\n\n'.join(blocks), youtube_url=state['source']['youtube_url'])
    return re.sub(r'{{\s*(\w+)\s*}}', lambda m: values[m[1]], state['settings']['template'])


def render(md, images=()):
    """Headings, paragraphs, lists, emphasis, HTTPS links; raw HTML stays text.

    Only server-generated image URLs are permitted. Used for preview and exports.
    """
    allowed = set(images)
    def inline(s):
        # Protect literal Markdown escapes before applying the supported syntax.
        literals = []
        def keep(m):
            literals.append(html.escape(m[1])); return '\x00' + str(len(literals)-1) + '\x00'
        s = re.sub(r'\\([\\`*_{}\[\]<>#!])', keep, s)
        s = html.escape(s)
        s = re.sub(r'\[([^\]\n]+)\]\((https://[^\s)]+)\)', r'<a href="\2" rel="noopener noreferrer">\1</a>', s)
        s = re.sub(r'\*\*([^*\n]+)\*\*', r'<strong>\1</strong>', s)
        s = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', s)
        return re.sub(r'\x00(\d+)\x00', lambda m: literals[int(m[1])], s)
    output, para, listing = [], [], False
    def flush():
        if para:
            output.append('<p>' + '<br>'.join(inline(s) for s in para) + '</p>'); para.clear()
    for line in md.splitlines():
        image = re.fullmatch(r'!\[([^\]]*)\]\(([^\s)]+)\)', line)
        heading = re.match(r'^(#{1,6})\s+(.+)', line)
        item = re.match(r'^[-*] (.+)', line)
        if listing and not item:
            output.append('</ul>'); listing = False
        if not line.strip():
            flush()
        elif image and image[2] in allowed:
            flush(); output.append(f'<img src="{html.escape(image[2], quote=True)}" alt="{html.escape(image[1], quote=True)}">')
        elif re.fullmatch(r'https://[^\s<>\"]+', line):
            flush(); safe=html.escape(line, quote=True); output.append(f'<p><a href="{safe}" rel="noopener noreferrer">{safe}</a></p>')
        elif heading:
            flush(); n=len(heading[1]); output.append(f'<h{n}>{inline(heading[2])}</h{n}>')
        elif item:
            flush()
            if not listing:
                output.append('<ul>'); listing=True
            output.append('<li>'+inline(item[1])+'</li>')
        else:
            para.append(line)
    flush()
    if listing: output.append('</ul>')
    return '\n'.join(output)


def html_page(body):
    return '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>레시피 아티클</title><style>body{max-width:720px;margin:40px auto;padding:24px;color:#292722;background:#fffdf8;font:17px/1.85 Georgia,"Apple SD Gothic Neo",serif}h1{font-size:2em;line-height:1.35}h2{margin-top:2em;border-bottom:1px solid #ddd6c8}h3{margin-top:2em}img{display:block;width:100%;max-width:640px;height:auto;margin:24px auto}a{color:#7c4c2b}p{overflow-wrap:anywhere}</style><body>'+body+'</body></html>'
