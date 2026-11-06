# -*- coding: utf-8 -*-
"""
OK影视(base.spider) Python 版 - 片库网爬虫
站点: https://4k01.pianku.online

关键增强:
  - 滑块 Cookie 持久化: 首次过盾后的 cookie 保存到本地文件, 后续搜索直接复用
    (站点逻辑: 首次搜索需滑块, 之后凭 cookie 免验证)
  - init 时自动加载已保存 cookie
  - 滑块成功后立即保存, 搜索成功后再保存一次(防 search_complete 下发新 cookie)
"""
import sys
import os
import re
import json
import tempfile
import html as html_lib
import requests
from urllib.parse import quote
from http.cookiejar import MozillaCookieJar

requests.packages.urllib3.disable_warnings()

sys.path.append("..")
from base.spider import Spider


HOST = "https://4k01.pianku.online"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
PARSE_API_URL = "https://svip.qlplayer.cyou/?url="

HEADERS = {"User-Agent": UA}

VOD_ITEM_RE = re.compile(
    r'<div class="vod-item">[\s\S]*?'
    r'<a href="/voddetail/(\d+)\.html" title="([^"]*)"[\s\S]*?'
    r'<img src="([^"]*)"[\s\S]*?'
    r'<span class="remarks">([\s\S]*?)</span>'
)
CLEAN_TAG_RE = re.compile(r'<[^>]+>')
DIRECT_URL_RE = re.compile(r'\.(m3u8|mp4|flv)', re.I)
API_TOKEN_RE = re.compile(r'apiToken\s*:\s*["\']([^"\']+)["\']')

CAPTCHA_MARKERS = ('SlideCaptcha.js', '请完成验证', 'slideCaptcha')


class Spider(Spider):

    def getName(self):
        return '片库网'

    def init(self, extend=""):
        try:
            super().init(extend)
        except Exception:
            pass
        self.sess = requests.Session()
        self.sess.verify = False
        self.sess.headers.update(HEADERS)
        self.headers = dict(HEADERS)
        # 加载已保存的滑块 cookie
        self._load_cookies()

    def isVideoFormat(self, url):
        pass

    def manualVideoCheck(self):
        pass

    def action(self, action):
        pass

    def destroy(self):
        try:
            self._save_cookies()
        except Exception:
            pass
        try:
            self.sess.close()
        except Exception:
            pass

    # ==================== Cookie 持久化 ====================

    def _cookie_path(self):
        """选一个可写目录存 cookie"""
        candidates = [
            os.path.join(tempfile.gettempdir(), '_pianku_cookies.txt'),
            '/tmp/_pianku_cookies.txt',
            os.path.join(os.path.dirname(os.path.abspath(__file__)), '_pianku_cookies.txt'),
            '_pianku_cookies.txt',
        ]
        for p in candidates:
            try:
                d = os.path.dirname(os.path.abspath(p))
                if os.path.isdir(d) and os.access(d, os.W_OK):
                    return p
            except Exception:
                pass
        return candidates[0]

    def _load_cookies(self):
        try:
            p = self._cookie_path()
            if not os.path.exists(p):
                print('[片库网] 无历史 cookie')
                return
            jar = MozillaCookieJar(p)
            jar.load(ignore_discard=True, ignore_expires=True)
            if len(jar) == 0:
                return
            self.sess.cookies.update(jar)
            names = [c.name for c in jar]
            print('[片库网] 已加载 %d 个 cookie: %s' % (len(jar), ','.join(names)))
        except Exception as e:
            print('[片库网] 加载 cookie 失败:', str(e)[:80])

    def _save_cookies(self):
        try:
            p = self._cookie_path()
            jar = MozillaCookieJar(p)
            for c in self.sess.cookies:
                jar.set_cookie(c)
            jar.save(ignore_discard=True, ignore_expires=True)
            print('[片库网] 已保存 %d 个 cookie' % len(jar))
        except Exception as e:
            print('[片库网] 保存 cookie 失败:', str(e)[:80])

    # ==================== 通用工具 ====================

    def _get(self, url, headers=None):
        try:
            r = self.sess.get(url, headers=headers or self.headers, timeout=15)
            if r.status_code != 200:
                print('[片库网] HTTP %d: %s' % (r.status_code, url))
                return ''
            if not r.encoding or r.encoding.lower() in ('iso-8859-1', 'ascii'):
                r.encoding = 'utf-8'
            return r.text or ''
        except Exception as e:
            print('[片库网] 请求失败: %s %s' % (url, str(e)[:80]))
            return ''

    def _get_resp(self, url, headers=None):
        try:
            r = self.sess.get(url, headers=headers or self.headers, timeout=15)
            return r.status_code, r.headers.get('Content-Type', ''), r.content
        except Exception as e:
            print('[片库网] 请求失败: %s %s' % (url, str(e)[:80]))
            return 0, '', None

    @staticmethod
    def _build_url(path):
        if not path:
            return ''
        if path.startswith('http'):
            return path
        return HOST + ('' if path.startswith('/') else '/') + path

    @staticmethod
    def _clean_text(s):
        if not s:
            return ''
        return CLEAN_TAG_RE.sub('', str(s)).strip()

    @staticmethod
    def _extract_brace_block(s, start):
        depth = 0
        in_str = False
        esc = False
        quote_ch = '"'
        for i in range(start, len(s)):
            c = s[i]
            if in_str:
                if esc:
                    esc = False
                    continue
                if c == '\\':
                    esc = True
                    continue
                if c == quote_ch:
                    in_str = False
                continue
            if c in ('"', "'"):
                in_str = True
                quote_ch = c
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return s[start:i + 1]
        return None

    @staticmethod
    def _extract_js_string(js, var):
        if not js:
            return ''
        m = re.search(r'var\s+%s\s*=\s*"((?:[^"\\]|\\.)*)"' % re.escape(var), js)
        if not m:
            m = re.search(r'%s\s*[:=]\s*"((?:[^"\\]|\\.)*)"' % re.escape(var), js)
        if not m:
            return ''
        return m.group(1).replace('\\/', '/').replace('\\"', '"')

    def _get_vod_list(self, html):
        if not html:
            return []
        out = []
        for m in VOD_ITEM_RE.finditer(html):
            out.append({
                'vod_id': m.group(1),
                'vod_name': self._clean_text(m.group(2)),
                'vod_pic': self._build_url(m.group(3)),
                'vod_remarks': self._clean_text(m.group(4)),
            })
        print('[片库网] 共提取到 %d 条数据' % len(out))
        return out

    @staticmethod
    def _format_url(url):
        if not url:
            return ''
        url = url.replace('\\', '')
        return re.sub(r'^(https?):/([^/])', r'\1://\2', url, flags=re.I)

    def _extract_media_url(self, text):
        if not text:
            return ''
        m = re.search(
            r'https?://[^\s"\'<>\\]+?\.(?:m3u8|mp4|flv)(?:\?[^\s"\'<>\\]*)?',
            text, re.I
        )
        if m:
            return m.group(0).replace('\\/', '/')
        for pat in (
            r'"(?:url|play_url|video_url|m3u8|src)"\s*:\s*"([^"]+)"',
        ):
            m = re.search(pat, text, re.I)
            if m and m.group(1).startswith('http'):
                return m.group(1).replace('\\/', '/')
        return ''

    # ==================== 首页 / 分类 ====================

    def homeContent(self, filter):
        classes = [
            {'type_id': '20', 'type_name': '电影'},
            {'type_id': '37', 'type_name': '剧集'},
            {'type_id': '43', 'type_name': '动漫'},
            {'type_id': '45', 'type_name': '综艺'},
        ]
        filters = {
            '20': [{
                'key': 'tid', 'name': '分类', 'value': [
                    {'n': '全部', 'v': '20'},
                    {'n': '动作片', 'v': '21'}, {'n': '喜剧片', 'v': '22'},
                    {'n': '爱情片', 'v': '23'}, {'n': '科幻片', 'v': '24'},
                    {'n': '恐怖片', 'v': '25'}, {'n': '剧情片', 'v': '26'},
                    {'n': '战争片', 'v': '27'}, {'n': '惊悚片', 'v': '28'},
                    {'n': '犯罪片', 'v': '29'}, {'n': '冒险篇', 'v': '30'},
                    {'n': '动画片', 'v': '31'}, {'n': '悬疑片', 'v': '32'},
                    {'n': '武侠片', 'v': '33'}, {'n': '奇幻片', 'v': '34'},
                    {'n': '纪录片', 'v': '35'}, {'n': '其他片', 'v': '36'},
                ]
            }]
        }
        html = self._get(HOST)
        return {
            'class': classes,
            'filters': filters if filter else {},
            'list': self._get_vod_list(html),
        }

    def homeVideoContent(self):
        html = self._get(HOST)
        return {'list': self._get_vod_list(html)}

    def categoryContent(self, tid, pg, filter, extend):
        real_tid = tid
        if isinstance(extend, dict) and extend.get('tid'):
            real_tid = extend.get('tid')
        page = str(pg or '1')
        url = (f'{HOST}/vodtype/{real_tid}.html' if page == '1'
               else f'{HOST}/vodtype/{real_tid}-{page}.html')
        print('[片库网] 请求分类 URL:', url)

        html = self._get(url)
        try:
            cur = int(page)
        except Exception:
            cur = 1

        pagecount = cur + 1
        total = 0
        m = re.search(r'尾页[\s\S]*?href="[^"]*?-(\d+)\.html"', html)
        if m:
            pagecount = int(m.group(1))
            total = pagecount * 24

        return {
            'list': self._get_vod_list(html),
            'page': cur,
            'pagecount': pagecount,
            'limit': 24,
            'total': total,
        }

    # ==================== 详情 ====================

    def detailContent(self, ids):
        vid = ids[0] if ids else ''
        url = f'{HOST}/voddetail/{vid}.html'
        print('[片库网] 获取详情页 URL:', url)

        html = self._get(url)
        if not html:
            return {'list': []}

        def get(pat):
            m = re.search(pat, html)
            return m.group(1) if m and m.group(1) is not None else ''

        title = self._clean_text(get(r'<h1[^>]*class="detail-title"[^>]*>([\s\S]*?)(?:<span|</h1>)'))
        pic = get(r'class="detail-poster"[^>]*>[\s\S]*?<img src="([^"]*)"')
        if pic:
            pic = self._build_url(pic)
        remarks = self._clean_text(get(r'class="detail-remarks"[^>]*>([\s\S]*?)</span>'))
        content = self._clean_text(get(r'class="detail-desc"[^>]*>[\s\S]*?<p>([\s\S]*?)</p>'))

        director = actor = area = year = ''
        meta_re = re.compile(
            r'<(?:span|p|div)[^>]*>(导演|主演|地区|年份)[：:]([\s\S]*?)(?:</span>|</p>|</div>)'
        )
        for m in meta_re.finditer(html):
            key, val = m.group(1), self._clean_text(m.group(2))
            if key == '导演':
                director = val
            elif key == '主演':
                actor = val
            elif key == '地区':
                area = val
            elif key == '年份':
                year = val

        play_from = []
        for m in re.finditer(r'class="source-tab-item[^"]*"[^>]*>([\s\S]*?)</span>', html):
            f = self._clean_text(m.group(1))
            if '自营4K60帧' in f or '自营4k60帧' in f:
                f = '⚡%s(注意直连)' % f
            play_from.append(f)

        play_url = []
        panes = html.split('class="source-pane')
        for pane in panes[1:]:
            eps = []
            for m in re.finditer(r'href="(/vodplay/[^"]+)"[^>]*>([\s\S]*?)</a>', pane):
                ep_name = self._clean_text(m.group(2))
                ep_url = self._build_url(m.group(1))
                eps.append('%s$%s' % (ep_name, ep_url))
            if eps:
                play_url.append('#'.join(eps))

        if not play_from and play_url:
            play_from = ['线路%d' % (i + 1) for i in range(len(play_url))]

        vod = {
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': pic,
            'vod_type_name': '',
            'vod_year': year,
            'vod_area': area,
            'vod_remarks': remarks,
            'vod_actor': actor,
            'vod_director': director,
            'vod_content': content,
            'vod_play_from': '$$$'.join(play_from),
            'vod_play_url': '$$$'.join(play_url),
        }
        print('[片库网] 成功解析视频详情:', title)
        return {'list': [vod]}

    # ==================== 搜索 + SlideCaptcha ====================

    @staticmethod
    def _is_captcha_page(html):
        return bool(html) and any(m in html for m in CAPTCHA_MARKERS)

    def _fetch_captcha_image(self):
        import random
        url = f'{HOST}/SlideCaptcha.php?action=get&t={random.random()}'
        status, ct, data = self._get_resp(url)
        print('[片库网] action=get status=%d ct=%s len=%s' % (
            status, ct, len(data) if data else 0))
        return data, ct

    def _check_captcha(self, x):
        import random
        url = f'{HOST}/SlideCaptcha.php?action=check&x={int(x)}&t={random.random()}'
        txt = self._get(url)
        raw = (txt or '').strip()
        print('[片库网] action=check x=%d -> %r' % (x, raw[:80]))
        if raw == 'ok' or raw.upper() == 'OK' or raw == '1':
            return True
        try:
            j = json.loads(raw)
            if isinstance(j, dict):
                code = j.get('code')
                if code in (1, '1') or j.get('success') is True or j.get('ok') is True:
                    return True
        except Exception:
            pass
        return False

    def _ncc_match(self, tmpl, bg):
        import numpy as np
        g_t = tmpl.mean(axis=2)
        g_b = bg.mean(axis=2)
        mask = g_t >= 66.67
        n = float(mask.sum())
        if n < 50:
            return None, -1.0
        t_mean = (g_t * mask).sum() / n
        t_c = (g_t - t_mean) * mask
        t_norm = float(np.sqrt((t_c ** 2).sum()))
        if t_norm < 1e-6:
            return None, -1.0
        H, W = g_b.shape
        th, tw = g_t.shape
        if th != H:
            th = min(th, H)
            g_t, mask, t_c = g_t[:th], mask[:th], t_c[:th]
            n = float(mask.sum())
            if n < 50:
                return None, -1.0
            t_mean = (g_t * mask).sum() / n
            t_c = (g_t - t_mean) * mask
            t_norm = float(np.sqrt((t_c ** 2).sum()))
            if t_norm < 1e-6:
                return None, -1.0
        best, bx = -2.0, -1
        for dx in range(0, W - tw + 1):
            win = g_b[:th, dx:dx + tw]
            w_mean = (win * mask).sum() / n
            w_c = (win - w_mean) * mask
            w_norm = float(np.sqrt((w_c ** 2).sum()))
            if w_norm < 1e-6:
                continue
            sc = float((t_c * w_c).sum() / (t_norm * w_norm))
            if sc > best:
                best, bx = sc, dx
        if best < 0.5:
            return None, best
        return int(bx), best

    def _dark_gap_detect(self, bg, mark_w):
        import numpy as np
        g = bg.mean(axis=2)
        H, W = g.shape
        cols = g.mean(axis=0)
        best, bx = 1e18, 0
        for dx in range(0, W - mark_w + 1):
            s = float(cols[dx:dx + mark_w].mean())
            if s < best:
                best, bx = s, dx
        if best < cols.mean() * 0.88:
            return int(bx)
        return None

    def _detect_gap_slidecaptcha(self, png):
        if not png:
            return None
        try:
            import io
            import numpy as np
            from PIL import Image
        except Exception:
            print('[片库网] 无 PIL/numpy, 无法识别缺口')
            return None
        try:
            img = np.array(Image.open(io.BytesIO(png)).convert('RGB')).astype(float)
            H, W = img.shape[:2]
            seg_h = H // 3
            if seg_h < 40:
                print('[片库网] 图片高度异常 H=%d' % H)
                return None
            mark_w = max(20, int(round(W * 50.0 / 240.0)))
            print('[片库网] 验证码图片 %dx%d, 段高=%d, mark_w=%d' % (W, H, seg_h, mark_w))

            if H >= seg_h * 3:
                puzzle = img[seg_h:seg_h * 2, :mark_w, :]
                fullbg = img[seg_h * 2:seg_h * 3, :, :]
                x, sc = self._ncc_match(puzzle, fullbg)
                print('[片库网] 段2匹配 x=%s score=%.3f' % (x, sc))
                if x is not None:
                    return x

            puzzle = img[seg_h:seg_h * 2, :mark_w, :]
            bg0 = img[0:seg_h, :, :]
            x, sc = self._ncc_match(puzzle, bg0)
            print('[片库网] 段0匹配 x=%s score=%.3f' % (x, sc))
            if x is not None:
                return x

            x = self._dark_gap_detect(bg0, mark_w)
            print('[片库网] 暗色缺口 x=%s' % x)
            return x
        except Exception as e:
            print('[片库网] 缺口识别异常:', str(e)[:120])
            return None

    def _solve_search_captcha(self, html):
        target = self._extract_js_string(html, 'target')
        verify_url = self._extract_js_string(html, 'verifyUrl')
        print('[片库网] 验证页 target=%s' % (target[:80] if target else '(空)'))
        print('[片库网] 验证页 verifyUrl=%s' % (verify_url or '(空)'))
        if not target or not verify_url:
            return False, ''

        for attempt in range(5):
            png, ct = self._fetch_captcha_image()
            if not png:
                print('[片库网] 第%d次 获取图片失败' % (attempt + 1))
                continue
            if not re.search(r'image|png|jpe?g|gif', (ct or '').lower()):
                head = png[:200].decode('utf-8', 'ignore')
                print('[片库网] 第%d次 get 返回非图片: %s' % (attempt + 1, head))
                continue
            x = self._detect_gap_slidecaptcha(png)
            if x is None:
                print('[片库网] 第%d次 缺口识别失败' % (attempt + 1))
                continue
            if self._check_captcha(x):
                print('[片库网] 第%d次 滑块校验通过 x=%d' % (attempt + 1, x))
                try:
                    r = self.sess.post(
                        self._build_url(verify_url),
                        data='source=search',
                        timeout=15,
                        headers={
                            'X-Requested-With': 'XMLHttpRequest',
                            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                        }
                    )
                    print('[片库网] search_complete status=%d body=%s' % (
                        r.status_code, (r.text or '')[:200]))
                except Exception as e:
                    print('[片库网] search_complete 异常:', str(e)[:80])
                # 关键: 过盾后立即保存 cookie
                self._save_cookies()
                return True, target
            print('[片库网] 第%d次 校验失败 x=%d' % (attempt + 1, x))

        print('[片库网] 滑块破解失败(5次)')
        return False, ''

    def searchContent(self, key, quick, pg='1'):
        url = f'{HOST}/vodsearch/-------------.html?wd={quote(key, safe="")}'
        print('[片库网] 搜索 key=%s' % key)
        print('[片库网] 当前 cookie 数=%d' % len(self.sess.cookies))

        html = self._get(url)
        print('[片库网] 搜索返回长度=%d, 是否验证页=%s' % (
            len(html or ''), self._is_captcha_page(html)))

        if self._is_captcha_page(html):
            print('[片库网] 触发滑块(首次或 cookie 失效), 开始破解')
            ok, target = self._solve_search_captcha(html)
            if not ok:
                return {'list': [], 'page': 1, 'pagecount': 1, 'limit': 20, 'total': 0}
            # 用验证页的 target 重放
            replay_url = target or url
            print('[片库网] 重放搜索: %s' % replay_url)
            html = self._get(replay_url)
            if self._is_captcha_page(html):
                print('[片库网] 重放仍被拦截')
                return {'list': [], 'page': 1, 'pagecount': 1, 'limit': 20, 'total': 0}

        lst = self._get_vod_list(html)
        # 搜索成功, 保存 cookie(可能被服务端刷新了)
        self._save_cookies()
        return {'list': lst, 'page': 1, 'pagecount': 1, 'limit': 20, 'total': len(lst)}

    # ==================== 播放解析 ====================

    def _parse_player_page(self, html):
        player = None
        anchor = html.find('player_aaaa')
        if anchor >= 0:
            brace = html.find('{', anchor)
            if brace >= 0:
                js = self._extract_brace_block(html, brace)
                if js:
                    try:
                        player = json.loads(js)
                    except Exception as e:
                        print('[片库网] player_aaaa 解析失败:', str(e)[:80])

        parsers = []
        m = re.search(r'data-parsers="([^"]*)"', html)
        if m:
            raw = html_lib.unescape(m.group(1))
            try:
                parsers = json.loads(raw)
            except Exception:
                parsers = []

        lxart = ''
        m = re.search(r'data-lxart-player="([^"]+)"', html)
        if m:
            lxart = html_lib.unescape(m.group(1))

        return player, parsers, lxart

    def _parse_video_url(self, url):
        try:
            resolve_url = PARSE_API_URL + url
            print('[片库网] svip 解析地址:', resolve_url)
            html1 = self._get(resolve_url)
            m = API_TOKEN_RE.search(html1 or '')
            if not m:
                return ''
            token_url = ('https://svip.qlplayer.cyou/api/resolve.php?token='
                         + quote(m.group(1), safe=''))
            print('[片库网] parseTokenUrl:', token_url)
            txt = self._get(token_url)
            data = json.loads(txt)
            final_url = self._format_url(data.get('url', ''))
            print('[片库网] svip finalUrl:', final_url)
            return final_url
        except Exception as e:
            print('[片库网] svip 解析失败:', str(e)[:80])
            return ''

    def _try_parser(self, parser, video_url, referer, header):
        try:
            sep = '&' if '?' in parser else '?'
            api = parser + sep + 'url=' + quote(video_url, safe='')
            html = self._get(api, headers={**header, 'Referer': referer})
            u = self._extract_media_url(html)
            if u:
                return u
            m = re.search(r'<iframe[^>]+src="([^"]+)"', html)
            if m:
                iframe_url = self._build_url(m.group(1))
                html2 = self._get(iframe_url, headers={**header, 'Referer': api})
                return self._extract_media_url(html2)
        except Exception as e:
            print('[片库网] parser 解析异常:', str(e)[:80])
        return ''

    def _try_lxart(self, path, referer, header):
        try:
            url = self._build_url(path)
            html = self._get(url, headers={**header, 'Referer': referer})
            u = self._extract_media_url(html)
            if u:
                return u
            m = re.search(r'<iframe[^>]+src="([^"]+)"', html)
            if m:
                iframe_url = self._build_url(m.group(1))
                html2 = self._get(iframe_url, headers={**header, 'Referer': url})
                return self._extract_media_url(html2)
        except Exception as e:
            print('[片库网] lxart 解析异常:', str(e)[:80])
        return ''

    def _try_other_sources(self, play_url, header):
        m = re.search(r'/vodplay/(\d+)-(\d+)-(\d+)\.html', play_url)
        if not m:
            return ''
        vid, sid, nid = m.group(1), int(m.group(2)), m.group(3)
        for nsid in (1, 2, 3):
            if nsid == sid:
                continue
            alt = f'{HOST}/vodplay/{vid}-{nsid}-{nid}.html'
            html = self._get(alt, headers=header)
            p2, parsers2, lxart2 = self._parse_player_page(html)
            if not p2 or not p2.get('url'):
                continue
            target = p2['url']
            if DIRECT_URL_RE.search(target):
                return target
            for parser in parsers2:
                u = self._try_parser(parser, target, alt, header)
                if u:
                    return u
            if lxart2:
                u = self._try_lxart(lxart2, alt, header)
                if u:
                    return u
        return ''

    def playerContent(self, flag, id, vipFlags):
        play_url = self._build_url(id)
        print('[片库网] 开始获取播放地址:', play_url)
        header = {'User-Agent': UA, 'Referer': HOST + '/'}

        html = self._get(play_url, headers=header)
        player, parsers, lxart = self._parse_player_page(html)

        if player and player.get('url'):
            target = player['url']
            if DIRECT_URL_RE.search(target):
                print('[片库网] 直链:', target)
                return {'parse': 0, 'url': target, 'header': header}

            for parser in parsers:
                u = self._try_parser(parser, target, play_url, header)
                if u:
                    print('[片库网] parser 解析成功:', u)
                    return {'parse': 0, 'url': u, 'header': header}

            if lxart:
                u = self._try_lxart(lxart, play_url, header)
                if u:
                    print('[片库网] lxart 解析成功:', u)
                    return {'parse': 0, 'url': u, 'header': header}

            u = self._parse_video_url(target)
            if u:
                return {'parse': 0, 'url': u, 'header': header}

            u = self._try_other_sources(play_url, header)
            if u:
                print('[片库网] 换线路成功:', u)
                return {'parse': 0, 'url': u, 'header': header}

        print('[片库网] 未拿到直链, 降级网页播放')
        return {'parse': 1, 'url': play_url, 'header': header}