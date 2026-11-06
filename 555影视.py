# -*- coding: utf-8 -*-
"""
555影视 · TVBox Python Spider 插件
移植自 555影视-dr3.js
"""
import sys
import re
import json
import urllib.parse
try:
    from base.spider import Spider as BaseSpider
except ImportError:
    class BaseSpider:
        def fetch(self, url, headers=None, timeout=10): raise NotImplementedError
        def log(self, msg): pass

HOST = 'https://555dy3.com'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
HEADERS = {
    'User-Agent': UA,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': HOST,
}
PLAY_HEADERS = {'User-Agent': UA, 'Referer': HOST + '/'}
TIMEOUT = 25
PLAY_TIMEOUT = 12

MEDIA_RE = re.compile(r'\.(m3u8|mp4|mkv|flv|ts|webm)(\?|#|$)', re.I)
BARE_MEDIA_RE = re.compile(r'(?:https?:)?\/\/[^\s"\'<>]+?\.(?:m3u8|mp4|flv)(?:\?[^\s"\'<>]*)?', re.I)
CF_SIGNS = ['just a moment', 'checking your browser', 'attention required! | cloudflare']

CLASSES = [
    {'type_id': '1', 'type_name': '电影'},
    {'type_id': '2', 'type_name': '连续剧'},
    {'type_id': '126', 'type_name': '擦边短剧'},
    {'type_id': '4', 'type_name': '动漫'},
    {'type_id': '3', 'type_name': '综艺纪录'},
]

SUB_FILTERS = {
    '1': ['动作片', '喜剧片', '爱情片', '科幻片', '恐怖片', '剧情片', '战争片', '纪录片', '动画片'],
    '2': ['国产剧', '香港剧', '台湾剧', '日本剧', '韩国剧', '欧美剧', '海外剧', '泰国剧'],
    '3': ['内地综艺', '港台综艺', '日韩综艺', '欧美综艺'],
    '4': ['国产动漫', '港台动漫', '日韩动漫', '欧美动漫'],
}

YEARS = [2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018, 2017, 2016, 2015]
SORT_OPTS = [{'n': '最新', 'v': 'time'}, {'n': '最热', 'v': 'hits'}, {'n': '评分', 'v': 'score'}]

def get_filters():
    f = {}
    for cid, subs in SUB_FILTERS.items():
        f[cid] = [
            {'key': 'class', 'name': '类型', 'value': [{'n': '全部', 'v': ''}] + [{'n': s, 'v': s} for s in subs]},
            {'key': 'year', 'name': '年份', 'value': [{'n': '全部', 'v': ''}] + [{'n': str(y), 'v': str(y)} for y in YEARS]},
            {'key': 'by', 'name': '排序', 'value': SORT_OPTS}
        ]
    f['126'] = [
        {'key': 'year', 'name': '年份', 'value': [{'n': '全部', 'v': ''}] + [{'n': str(y), 'v': str(y)} for y in [2026, 2025, 2024, 2023, 2022]]},
        {'key': 'by', 'name': '排序', 'value': SORT_OPTS[:2]}
    ]
    return f

class Spider(BaseSpider):
    def getName(self):
        return '555影视'

    def init(self, extend=''):
        pass

    def isVideoFormat(self, url):
        return bool(MEDIA_RE.search(url))
        
    def manualVideoCheck(self):
        pass
        
    def localProxy(self, param):
        return None
        
    def liveContent(self, url):
        return ''

    def _get(self, url, referer=None, timeout=TIMEOUT):
        headers = HEADERS.copy()
        if referer:
            headers['Referer'] = referer
        for _ in range(2):
            try:
                r = self.fetch(url, headers=headers, timeout=timeout)
                if r and r.text:
                    text = r.text
                    lower_text = text.lower()
                    if any(sign in lower_text for sign in CF_SIGNS):
                        self.log('555 CF拦截 ' + url[:56])
                        return ''
                    return text
            except Exception as e:
                self.log(f'555 {url[:56]} 失败: {str(e)}')
        return ''

    def _fix_url(self, url):
        if not url:
            return ''
        v = str(url).strip().replace('\\/', '/')
        if v.startswith('//'):
            return 'https:' + v
        if not v.startswith('http'):
            return urllib.parse.urljoin(HOST + '/', v)
        return v

    def _strip_tags(self, s):
        if not s:
            return ''
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s)).strip()

    def _parse_cards(self, html):
        lst = []
        if not html:
            return lst
        seen = set()
        # 模拟 JS 的 exec loop
        re_card = re.compile(r'<a\b[^>]*href=["\']([^"\']*\/voddetail\/(\d+)\.html[^"\']*)["\'][^>]*>([\s\S]*?)<\/a>', re.I)
        for m in re_card.finditer(html):
            vid = m.group(2)
            if vid in seen:
                continue
            tag = m.group(0)
            inner = m.group(3)
            
            title_m = re.search(r'title=["\']([^"\']+)["\']', tag)
            name = title_m.group(1) if title_m else self._strip_tags(inner)
            if not name:
                continue
            seen.add(vid)
            
            card = {'vod_id': vid, 'vod_name': name}
            
            # 找图
            pic_m = re.search(r'data-original=["\']([^"\']+)["\']', tag) or \
                    re.search(r'data-original=["\']([^"\']+)["\']', inner) or \
                    re.search(r'src=["\']([^"\']+)["\']', inner)
            if pic_m:
                card['vod_pic'] = self._fix_url(pic_m.group(1))
            else:
                pwin = html[m.end():m.end()+800]
                pm = re.search(r'data-original=["\']([^"\']+)["\']', pwin) or \
                     re.search(r'src=["\'](https?:[^"\']+\.(?:jpg|png|webp)[^"\']*)["\']', pwin)
                if pm:
                    card['vod_pic'] = self._fix_url(pm.group(1))
                    
            # 找备注
            rm_inner = re.search(r'class=["\'][^"\']*(?:pic-text|module-item-note|vod-remarks|remarks)["\'][^>]*>([\s\S]{1,60}?)<\//', inner)
            remark = self._strip_tags(rm_inner.group(1)) if rm_inner else ''
            if not remark:
                rwin = html[m.end():m.end()+600]
                next_link_idx = html.find('/voddetail/', m.end())
                rm_win = re.search(r'class=["\'][^"\']*(?:module-item-note|module-item-text|vod-remarks|remarks|desc)["\'][^>]*>([\s\S]{1,60}?)<\//', rwin)
                if rm_win:
                    match_pos = m.end() + rm_win.start()
                    if next_link_idx < 0 or match_pos < next_link_idx:
                        remark = self._strip_tags(rm_win.group(1))
            if remark:
                card['vod_remarks'] = remark
                
            lst.append(card)
        return lst

    def _parse_pagecount(self, html, page):
        if not html:
            return page
        max_p = page
        tail_re = re.compile(r'href=["\'][^"\']*(?:-(\d{1,4})|\/page\/(\d{1,4}))\.html[^"\']*["\'][^>]*>\s*(?:尾页|末页|末頁)', re.I)
        for m in tail_re.finditer(html):
            p = int(m.group(1) or m.group(2))
            if p > max_p:
                max_p = p
        if max_p > page:
            return max_p
            
        seg = re.search(r'class=["\'][^"\']*(?:page|pagination)["\'][^>]*>([\s\S]{0,2000}?)(?:<\/div>|<\/ul>)', html, re.I)
        if seg:
            for m in re.finditer(r'>(\d{1,4})<', seg.group(1)):
                p = int(m.group(1))
                if p > max_p:
                    max_p = p
        return max_p

    def _extract_plays(self, source):
        groups = {}
        order = []
        re_play = re.compile(r'<a\b[^>]*href=["\']([^"\']*\/vodplay\/(\d+)-(\d+)-(\d+)\.html[^"\']*)["\'][^>]*>([\s\S]*?)<\/a>', re.I)
        
        for m in re_play.finditer(source):
            sid = m.group(3)
            href = self._fix_url(m.group(1))
            name = self._strip_tags(m.group(5))
            
            if sid not in groups:
                groups[sid] = {}
                order.append(sid)
            if href not in groups[sid] or groups[sid][href] == '播放' or '第' in name:
                groups[sid][href] = name if name else f'第{len(groups[sid])+1}集'
                
        if not order:
            return '', ''
            
        names = []
        for m in re.finditer(r'class=["\'][^"\']*(?:play-title|line-name|playlist-title)["\'][^>]*>([^<]{1,30})<', source, re.I):
            n = self._strip_tags(m.group(1))
            if n:
                names.append(n)
                
        try:
            order.sort(key=lambda x: int(x) if x.isdigit() else 1)
        except Exception:
            pass
            
        play_froms = []
        play_urls = []
        
        for i, sid in enumerate(order):
            eps = [f'{name}${url}' for url, name in groups[sid].items()]
            if eps:
                play_froms.append(names[i] if i < len(names) else f'线路{i+1}')
                play_urls.append('#'.join(eps))
                
        return '$$$'.join(play_froms), '$$$'.join(play_urls)

    def _parse_detail(self, html, vod_id):
        vod = {'vod_id': vod_id}
        
        title = re.search(r'<h1[^>]*>([\s\S]*?)<\/h1>', html, re.I) or \
                re.search(r'class=["\'][^"\']*vod-title[^"\']*["\'][^>]*>([\s\S]*?)<', html, re.I) or \
                re.search(r'<title>([\s\S]*?)<\/title>', html, re.I)
        if title:
            vod['vod_name'] = re.sub(r'\s*[-|–—]\s*.*$', '', self._strip_tags(title.group(1)))
        else:
            vod['vod_name'] = vod_id
            
        pic = re.search(r'class=["\'][^"\']*(?:vod-pic|detail-pic|cover|lazyload)[^"\']*["\'][^>]*data-original=["\']([^"\']+)["\']', html, re.I) or \
              re.search(r'data-original=["\']([^"\']+\.(?:jpg|png|webp)[^"\']*)["\']', html, re.I) or \
              re.search(r'<img[^>]*src=["\'](https?:[^"\']+\.(?:jpg|png|webp)[^"\']*)["\']', html, re.I)
        if pic:
            vod['vod_pic'] = self._fix_url(pic.group(1).replace("'", '').replace('"', ''))
            
        desc = re.search(r'class=["\'][^"\']*(?:vod-content|detail-content|desc)[^"\']*["\'][^>]*>([\s\S]*?)<\/div>', html, re.I) or \
               re.search(r'name=["\']description["\'][^>]*content=["\']([^"\']+)', html, re.I)
        if desc:
            vod['vod_content'] = self._strip_tags(desc.group(1))
            
        plain = self._strip_tags(html)
        actor = re.search(r'主演[：:]\s*([^，。；|]{1,120})', plain)
        if actor:
            vod['vod_actor'] = actor.group(1).strip()
        director = re.search(r'导演[：:]\s*([^，。；|]{1,60})', plain)
        if director:
            vod['vod_director'] = director.group(1).strip()
        year = re.search(r'\b(19\d{2}|20\d{2})\b', plain)
        if year:
            vod['vod_year'] = year.group(1)
            
        play_from, play_url = self._extract_plays(html)
        vod['vod_play_from'] = play_from
        vod['vod_play_url'] = play_url
        return vod

    def _extract_play_config(self, html):
        vars = ['player_aaaa', 'player_data', 'MacPlayer']
        for v in vars:
            head = re.search(r'(?:var\s+|let\s+|const\s+|window\.)?' + v + r'\s*=\s*', html, re.I)
            if not head:
                continue
            
            start = html.find('{', head.end())
            if start < 0:
                continue
                
            depth = 0
            in_str = False
            esc = False
            q = ''
            
            for i in range(start, min(len(html), start + 150000)):
                c = html[i]
                if in_str:
                    if esc:
                        esc = False
                        continue
                    if c == '\\':
                        esc = True
                        continue
                    if c == q:
                        in_str = False
                    continue
                if c in ('"', "'"):
                    in_str = True
                    q = c
                    continue
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        block = html[start:i+1]
                        try:
                            obj = json.loads(block)
                            if isinstance(obj, dict):
                                return obj
                        except:
                            pass
                        try:
                            loose = re.sub(r'([{,]\s*)([A-Za-z_]\w*)\s*:', r'\1"\2":', block)
                            loose = re.sub(r"'([^'\\]*)'", r'"\1"', loose)
                            obj = json.loads(loose)
                            if isinstance(obj, dict):
                                return obj
                        except:
                            pass
                        break
        return None

    def _resolve_play(self, start_url):
        visited = set()
        cur = start_url
        
        for _ in range(5):
            if cur in visited:
                break
            visited.add(cur)
            
            if MEDIA_RE.search(cur):
                return cur, 0
                
            html = self._get(cur, HOST + '/', PLAY_TIMEOUT)
            if not html:
                break
                
            cfg = self._extract_play_config(html)
            if cfg:
                link = self._fix_url(cfg.get('link', ''))
                if link and link != cur and link not in visited:
                    cur = link
                    continue
                u = str(cfg.get('url', ''))
                if u:
                    if MEDIA_RE.search(u):
                        return self._fix_url(u), 0
                    nu = self._fix_url(u)
                    if nu and nu != cur and nu not in visited:
                        cur = nu
                        continue
                        
            iframe = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
            if iframe:
                iu = self._fix_url(iframe.group(1))
                if iu and iu != cur and iu not in visited:
                    cur = iu
                    continue
                    
            vsrc = re.search(r'<video[^>]+src=["\']([^"\']+)["\']', html, re.I) or \
                   re.search(r'<source[^>]+src=["\']([^"\']+)["\']', html, re.I)
            if vsrc:
                vu = self._fix_url(vsrc.group(1))
                if MEDIA_RE.search(vu):
                    return vu, 0
                if vu and vu != cur and vu not in visited:
                    cur = vu
                    continue
                    
            bare = BARE_MEDIA_RE.search(html)
            if bare:
                return self._fix_url(bare.group(0)), 0
                
            for p in [r'var\s+playurl\s*=\s*["\']([^"\']+)["\']', r'var\s+url\s*=\s*["\']([^"\']+)["\']',
                      r'var\s+video\s*=\s*["\']([^"\']+)["\']', r'var\s+src\s*=\s*["\']([^"\']+)["\']']:
                m = re.search(p, html, re.I)
                if m and MEDIA_RE.search(m.group(1)):
                    return self._fix_url(m.group(1)), 0
                    
            jumped = False
            for m in re.finditer(r'href=["\']([^"\']*\/vodplay\/[^"\']+)["\']', html, re.I):
                nu = self._fix_url(m.group(1))
                if nu and nu != cur and nu not in visited:
                    cur = nu
                    jumped = True
                    break
            if jumped:
                continue
            break
            
        return cur, 1

    # ------------------ TVBox 接口实现 ------------------

    def homeContent(self, filter):
        return {
            'class': CLASSES,
            'filters': get_filters() if filter else {}
        }

    def homeVideoContent(self):
        html = self._get(HOST + '/')
        lst = self._parse_cards(html)[:30]
        if len(lst) < 8:
            html2 = self._get(HOST + '/vodtype/1.html')
            lst.extend(self._parse_cards(html2))
            lst = lst[:30]
        return {'list': lst}

    def categoryContent(self, tid, pg, filter, extend):
        page = max(1, int(pg))
        cid = str(tid or '1')
        ex = extend or {}
        
        def has(v): return v and v != '全部'
        
        seg = []
        if has(ex.get('class')): seg.append('class/' + ex['class'])
        if has(ex.get('year')): seg.append('year/' + ex['year'])
        if has(ex.get('by')): seg.append('by/' + ex['by'])
        
        qs = []
        if has(ex.get('class')): qs.append('class=' + urllib.parse.quote(ex['class']))
        if has(ex.get('year')): qs.append('year=' + urllib.parse.quote(ex['year']))
        if has(ex.get('by')): qs.append('by=' + ex['by'])
        
        q = '?' + '&'.join(qs) if qs else ''
        
        urls = []
        if seg:
            urls = [
                f'{HOST}/vodshow/{cid}/{"/".join(seg)}/page/{page}.html',
                f'{HOST}/vodshow/{cid}-{page}.html{q}',
                f'{HOST}/vodtype/{cid}-{page}.html{q}',
                f'{HOST}/vodtype/{cid}-{page}.html'
            ]
        elif page == 1:
            urls = [f'{HOST}/vodtype/{cid}.html', f'{HOST}/vodshow/{cid}.html', f'{HOST}/vodlist/{cid}.html']
        else:
            urls = [
                f'{HOST}/vodtype/{cid}-{page}.html',
                f'{HOST}/vodtype/{cid}.html?page={page}',
                f'{HOST}/vodshow/{cid}-{page}.html'
            ]
            
        html = ''
        for u in urls:
            html = self._get(u)
            if html:
                break
                
        if not html:
            return {'list': [], 'page': page, 'pagecount': 1, 'limit': 24, 'total': 0}
            
        lst = self._parse_cards(html)
        pagecount = self._parse_pagecount(html, page)
        
        return {'list': lst, 'page': page, 'pagecount': pagecount, 'limit': 24, 'total': len(lst)*pagecount}

    def searchContent(self, key, quick, pg='1'):
        page = max(1, int(pg))
        kw = str(key or '').strip()
        if not kw:
            return {'list': [], 'page': page, 'pagecount': 1, 'limit': 0, 'total': 0}
            
        url = f'{HOST}/vodsearch/-------------.html?wd={urllib.parse.quote(kw)}'
        if page > 1:
            url += f'&page={page}'
            
        html = self._get(url)
        lst = self._parse_cards(html)
        
        return {'list': lst, 'page': page, 'pagecount': 1, 'limit': len(lst), 'total': len(lst)}

    def detailContent(self, ids):
        vod_id = re.sub(r'[^0-9]', '', str(ids[0]))
        if not vod_id:
            return {'list': []}
            
        html = self._get(f'{HOST}/voddetail/{vod_id}.html')
        if not html:
            return {'list': []}
            
        vod = self._parse_detail(html, vod_id)
        if not vod.get('vod_play_url'):
            vod['vod_play_from'] = '默认源'
            vod['vod_play_url'] = f'播放${HOST}/vodplay/{vod_id}-1-1.html'
            
        return {'list': [vod]}

    def playerContent(self, flag, id, vipFlags):
        raw = str(id or '').strip()
        if not raw:
            return {'parse': 0, 'url': ''}
            
        play_url = raw[raw.rfind('$')+1:] if '$' in raw else raw
        play_url = self._fix_url(play_url)
        
        if MEDIA_RE.search(play_url):
            return {'parse': 0, 'url': play_url, 'header': PLAY_HEADERS}
            
        final_url, parse_flag = self._resolve_play(play_url)
        return {'parse': parse_flag, 'url': final_url, 'header': PLAY_HEADERS}