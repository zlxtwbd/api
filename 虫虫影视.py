# -*- coding: utf-8 -*-
"""
虫虫影视 Python Spider — 兼容 FongMi/TV (T3) 与 WebHomeTV / PeekPro (T4)
站点: https://rara01.com/

特性:
  - 7个一级分类 + 完整二级分类（电影26/电视剧9/综艺4/动漫5/AI剧场3/短剧10/影视解说4）
  - 搜索: /film/search=关键词，阿拉伯数字自动转中文重试
  - 播放: 详情页剧集 -> 播放页 data-video-token(base64) 解出 m3u8 直链，零解析
          每集自带多线路 token，自动探测择优 + 故障转移
  - 剧圈圈备用线路: 搜索同名影片于 jqqzx.one，sign 解密出真实 m3u8，CDN 宕机自动可用
  - CDN 故障容灾: DNS 预检快速跳过死域名 + 死域缓存 + 外部解析器兜底
  - 速度: 全链路缓存(首页5min/分类2min/详情10min/播放token 30min/探测10min)
  - 图片代理: 可选，解决Android端防盗链
"""

import sys
import json
import re
import time
import base64
import socket
import hashlib
import html as html_mod

sys.path.append('..')

try:
    from base.spider import Spider
except ImportError:
    import requests as _rq
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass

    class Spider:
        def fetch(self, url, headers=None, **kw):
            timeout = kw.pop('timeout', 15)
            r = _rq.get(url, headers=headers, timeout=timeout, verify=False, **kw)
            r.encoding = 'utf-8'
            return r

try:
    from concurrent.futures import ThreadPoolExecutor
except Exception:
    ThreadPoolExecutor = None

from urllib.parse import quote, unquote


# ============================================================
# 常量
# ============================================================

HOST = "https://rara01.com"
UA = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"

# 一级分类 type_id = URL slug
CLASSES = [
    {"type_name": "电影",     "type_id": "dianyingd"},
    {"type_name": "电视剧",   "type_id": "dianshia"},
    {"type_name": "综艺",     "type_id": "zongyia"},
    {"type_name": "动漫",     "type_id": "dongmana"},
    {"type_name": "AI剧场",   "type_id": "juchanga"},
    {"type_name": "短剧",     "type_id": "duanjua"},
    {"type_name": "影视解说", "type_id": "yingshia"},
]

# 二级分类 {父slug: [(名称, 子slug)]}
SUB_TYPES = {
    "dianyingd": [
        ("全部", ""), ("动作片", "dongzuoa"), ("喜剧片", "xijua"), ("爱情片", "aiqinga"),
        ("科幻片", "kehuana"), ("恐怖片", "kongbua"), ("剧情片", "juqingb"), ("战争片", "zhanzhenga"),
        ("悬疑片", "xuanyia"), ("犯罪片", "fanzuia"), ("惊悚片", "jingsonga"), ("奇幻片", "qihuana"),
        ("冒险片", "maoxiana"), ("灾难片", "zainana"), ("武侠片", "wuxiaa"), ("古装片", "guzhuangb"),
        ("动画电影", "donghuab"), ("纪录片", "jilua"), ("4K电影", "dianyinge"),
        ("Netflix电影", "dianyingf"), ("邵氏电影", "shaoshia"),
    ],
    "dianshia": [
        ("全部", ""), ("国产剧", "guochanb"), ("香港剧", "xiangganga"), ("台湾剧", "taiwana"),
        ("韩国剧", "hanguoc"), ("日本剧", "ribend"), ("欧美剧", "oumeic"), ("泰国剧", "taiguoa"),
        ("海外剧", "haiwaib"), ("Netflix自制剧", "zizhia"),
    ],
    "zongyia": [
        ("全部", ""), ("大陆综艺", "dalua"), ("港台综艺", "gangtaid"),
        ("日韩综艺", "rihana"), ("欧美综艺", "oumeid"),
    ],
    "dongmana": [
        ("全部", ""), ("国产动漫", "guochanc"), ("日本动漫", "ribene"), ("欧美动漫", "oumeie"),
        ("港台动漫", "gangtaie"), ("海外动漫", "haiwaic"),
    ],
    "juchanga": [("全部", ""), ("有声动漫", "youshenga"), ("漫剧", "manjub"), ("AI漫剧", "manjuc")],
    "duanjua": [
        ("全部", ""), ("现代都市", "xiandaia"), ("女频恋爱", "nvpina"), ("反转爽剧", "fanzhuana"),
        ("古装仙侠", "guzhuangc"), ("年代穿越", "niandaia"), ("脑洞悬疑", "naodonga"),
        ("成长逆袭", "chengzhanga"), ("战神", "zhanshena"), ("豪门", "haomena"),
    ],
    "yingshia": [
        ("全部", ""), ("电影解说", "dianyingg"), ("预告解说", "yugaob"),
        ("预告片", "yugaoc"), ("剧情介绍", "juqingc"),
    ],
}

# 筛选器：分类（二级）+ 年份（客户端兜底）
_FILTERS_YEAR = {"key": "year", "name": "年份", "value": [
    {"n": "全部", "v": ""},
    {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"}, {"n": "2024", "v": "2024"},
    {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"}, {"n": "2021", "v": "2021"},
    {"n": "2020", "v": "2020"}, {"n": "2019", "v": "2019"}, {"n": "2018以前", "v": "-2018"},
]}

FILTERS = {}
for c in CLASSES:
    tid = c["type_id"]
    FILTERS[tid] = [
        {"key": "type", "name": "分类", "value": [
            {"n": n, "v": v} for n, v in SUB_TYPES.get(tid, [("全部", "")])
        ]},
        _FILTERS_YEAR,
    ]

_CN_DIGITS = str.maketrans("0123456789", "零一二三四五六七八九")


# ============================================================
# 剧圈圈备用线路 (jqqzx.one) — sign 解密
# ============================================================

JQQ_HOST = "https://www.jqqzx.one"
JQQ_UA = ("Mozilla/5.0 (Linux; Android 12; M2007J22C) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")


def _md5_hex(s):
    if isinstance(s, str):
        try:
            s = s.encode("utf-8")
        except Exception:
            pass
    return hashlib.md5(s).hexdigest()


def _b64decode_str(s):
    try:
        raw = base64.b64decode(s)
        if isinstance(raw, str):
            return raw
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _jqq_custom_str_decode(s):
    """剧圈圈 customStrDecode: b64解码 -> 与 md5('test') 逐字符异或 -> b64解码"""
    key = _md5_hex("test")
    try:
        raw = base64.b64decode(s)
    except Exception:
        return ""
    code = []
    for i in range(len(raw)):
        c = raw[i]
        if isinstance(c, str):
            c = ord(c)
        code.append(chr(c ^ ord(key[i % len(key)])))
    return _b64decode_str("".join(code))


def _jqq_de_string(a, b, c):
    """剧圈圈 deString: 密文中在 b 数组里的字母替换为 b[a.index(字母)]"""
    out = []
    b_set = set(b)
    for ch in c:
        is_alpha = ("a" <= ch <= "z") or ("A" <= ch <= "Z")
        if is_alpha and ch in b_set:
            try:
                idx = a.index(ch)
            except ValueError:
                out.append(ch)
                continue
            out.append(b[idx] if 0 <= idx < len(b) else ch)
        else:
            out.append(ch)
    return "".join(out)


def _jqq_sign_decrypt(s):
    """剧圈圈 sign(): 解密 /jx/api.php 返回的加密 url -> 真实 m3u8"""
    try:
        m = _jqq_custom_str_decode(s)
        if not m:
            return ""
        parts = m.split("/")
        if len(parts) < 3:
            return ""
        a = json.loads(_b64decode_str(parts[1]))
        b = json.loads(_b64decode_str(parts[0]))
        body = "/".join(parts[2:])
        c = _b64decode_str(body)
        if not a or not b or not c:
            return ""
        return _jqq_de_string(a, b, c)
    except Exception:
        return ""


# ============================================================
# Spider 主类
# ============================================================

class Spider(Spider):

    def getName(self):
        return "虫虫影视"

    def init(self, extend=""):
        if isinstance(extend, list):
            self.extend = ""
        else:
            self.extend = extend or ""

        self.header = {
            "User-Agent": UA,
            "Referer": HOST + "/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        # 缓存
        self._home_cache = []           # 首页 5min
        self._home_cache_time = 0
        self._cat_cache = {}            # 分类 2min
        self._cat_cache_ttl = 120
        self._detail_cache = {}         # 详情 10min
        self._detail_cache_ttl = 600
        self._token_cache = {}          # 播放页->m3u8列表 30min（token固定，可久存）
        self._token_cache_ttl = 1800
        self._probe_cache = {}          # 域名探测 10min
        self._probe_ttl = 600

        # --- CDN 故障容灾 ---
        self._dead_domains = {}        # 域名 -> 标记死亡时间戳
        self._dead_domain_ttl = 3600   # 死域 1h 后重试
        self._player_config = None      # /common/player-config 缓存
        self._player_config_time = 0
        self._player_config_ttl = 600   # 解析器配置 10min

        # 图片代理
        self._img_prefix = ""
        try:
            cfg = json.loads(self.extend) if self.extend.strip().startswith("{") else {}
            if isinstance(cfg, dict):
                img = (cfg.get("img") or "").strip()
                if img == "t4":
                    self._img_prefix = "http://127.0.0.1:10079/proxy?do=py&url="
                elif img == "t3":
                    self._img_prefix = "http://127.0.0.1:9978/proxy?do=py&url="
                elif img in ("off", "0", "false"):
                    self._img_prefix = ""
                elif img.startswith("http"):
                    self._img_prefix = img
        except Exception:
            pass
        self._img_cache = {}
        self._img_cache_ttl = 1800

        # --- 剧圈圈备用线路 ---
        self._jqq_header = {
            "User-Agent": JQQ_UA,
            "Referer": JQQ_HOST + "/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        self._jqq_search_cache = {}       # 片名 -> jqq vid, 10min
        self._jqq_search_cache_ttl = 600
        self._jqq_detail_cache = {}      # jqq vid -> episodes, 10min
        self._jqq_detail_cache_ttl = 600
        self._jqq_token_cache = {}       # jqq play_path -> real m3u8, 30min
        self._jqq_token_cache_ttl = 1800

    # ===== 网络工具 =====
    def _rsp_text(self, rsp):
        try:
            return rsp.text
        except Exception:
            try:
                return rsp.content.decode('utf-8', 'ignore')
            except Exception:
                return ""

    def _get_html(self, url, timeout=5):
        try:
            rsp = self.fetch(url, headers=self.header, timeout=timeout)
            return self._rsp_text(rsp)
        except Exception:
            return ""

    # ===== POST 请求（剧圈圈 /jx/api.php 用）=====
    def _http_post(self, url, data, headers=None, timeout=10):
        """POST 请求: urllib 标准库 -> 框架 fetch 兜底"""
        hdr = headers or self._jqq_header
        try:
            from urllib.request import Request as _Req, urlopen as _Uo
            from urllib.parse import urlencode as _Ue
        except ImportError:
            from urllib2 import Request as _Req, urlopen as _Uo
            from urllib import urlencode as _Ue
        try:
            body = _Ue(data)
            try:
                body = body.encode("utf-8")
            except Exception:
                pass
            req = _Req(url, data=body, headers=hdr)
            try:
                import ssl as _ssl
                ctx = _ssl._create_unverified_context()
                rsp = _Uo(req, timeout=timeout, context=ctx)
            except Exception:
                rsp = _Uo(req, timeout=timeout)
            raw = rsp.read()
            if not isinstance(raw, str):
                raw = raw.decode("utf-8", errors="replace")
            if raw:
                return raw
        except Exception:
            pass
        try:
            rsp = self.fetch(url, headers=hdr, timeout=timeout, data=data)
            if getattr(rsp, "status_code", 0) == 200:
                return self._rsp_text(rsp)
        except Exception:
            pass
        return None

    def _pic(self, url):
        url = (url or "").strip()
        if not url or 'no-cover' in url:
            return ""
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = HOST + url
        elif not url.startswith("http"):
            url = HOST + "/" + url
        if self._img_prefix:
            return self._img_prefix + quote(url, safe="")
        return url

    @staticmethod
    def _strip_tags(s):
        return re.sub(r'<[^>]+>', '', s or '').strip()

    # ===== DNS 预检（快速跳过死域名）=====
    def _dns_ok(self, url):
        """DNS 预检: 域名能否解析。死域缓存 1h, 避免重复超时等待。"""
        try:
            domain = url.split("://")[1].split("/")[0]
        except Exception:
            return True

        now = int(time.time())
        dead = self._dead_domains.get(domain)
        if dead and now - dead < self._dead_domain_ttl:
            return False

        try:
            socket.gethostbyname(domain)
            return True
        except Exception:
            self._dead_domains[domain] = now
            if len(self._dead_domains) > 100:
                self._dead_domains.clear()
            return False

    # ===== 卡片解析（首页/分类/搜索通用） =====
    _CARD_RE = re.compile(
        r'<a[^>]+href="(/film/([a-zA-Z0-9]+)\.html)"[^>]*>(.{0,1500}?)</a>', re.DOTALL)

    # 非影片占位 vid (rank 排行榜, list 列表, search 搜索, recommend 推荐等)
    _SKIP_VIDS = frozenset({"rank", "list", "search", "rank.html", "list.html", "recommend", "recommend.html"})

    def _parse_cards(self, html):
        cards = []
        seen = set()
        for m in self._CARD_RE.finditer(html):
            path, vid, block = m.group(1), m.group(2), m.group(3)
            # 过滤非影片占位卡片 (排行榜/列表等)
            if vid in self._SKIP_VIDS or len(vid) <= 5:
                continue
            if vid in seen:
                continue
            # 图片: 优先 data-cover-src (懒加载), 兜底 src
            pic = ""
            pm = re.search(r'data-cover-src="([^"]+)"', block) or re.search(r'src="([^"]+)"', block)
            if pm:
                pic = pm.group(1)
            # 名称: alt > <h3> > 纯文本
            name = ""
            nm = re.search(r'alt="([^"]+)"', block)
            if nm:
                name = nm.group(1)
            if not name:
                nm = re.search(r'<h[23][^>]*>(.*?)</h[23]>', block, re.DOTALL)
                if nm:
                    name = self._strip_tags(nm.group(1))
            if not name:
                name = self._strip_tags(block)[:40]
            name = html_mod.unescape(name).strip()
            if not name:
                continue
            # 备注: 抓块内年份
            remark = ""
            rm = re.search(r'(19\d{2}|20[0-3]\d)', self._strip_tags(block))
            if rm:
                remark = rm.group(1)
            seen.add(vid)
            cards.append({
                "vod_id": vid,
                "vod_name": name,
                "vod_pic": self._pic(pic),
                "vod_remarks": remark,
            })
        return cards

    # ===== 分页 =====
    @staticmethod
    def _parse_pagecount(html, cur):
        nums = []
        for mm in re.finditer(r'href="(/film/[^"]*(?:\?page=|/page/|-)(\d+)[^"]*)"', html):
            nums.append(int(mm.group(2)))
        if nums:
            return max(nums)
        if re.search(r'<a[^>]*>\s*下一页\s*</a>', html):
            return cur + 1
        return cur

    # ===== 线路探测（含 DNS 预检）=====
    def _probe(self, url, timeout=1.5):
        url = (url or "").strip()
        if not url:
            return False
        try:
            domain = url.split("://")[1].split("/")[0]
        except Exception:
            domain = url
        now = int(time.time())
        hit = self._probe_cache.get(domain)
        if hit and now - hit[0] < self._probe_ttl:
            return hit[1]

        # DNS 预检: 域名无法解析直接判死, 跳过 HTTP 超时等待
        if not self._dns_ok(url):
            if len(self._probe_cache) > 300:
                self._probe_cache.clear()
            self._probe_cache[domain] = (now, False)
            return False

        ok = False
        try:
            headers = {"User-Agent": UA, "Range": "bytes=0-511"}
            rsp = self.fetch(url, headers=headers, timeout=timeout)
            code = getattr(rsp, "status_code", 200) or 200
            text = self._rsp_text(rsp)[:512]
            ok = (int(code) < 400) and ("#EXTM3U" in text or ".m3u8" in text or len(text) > 100)
        except Exception:
            ok = False

        if len(self._probe_cache) > 300:
            self._probe_cache.clear()
        self._probe_cache[domain] = (now, ok)
        return ok

    # ============================================================
    # 首页
    # ============================================================
    def homeContent(self, filter):
        return {"class": CLASSES, "filters": FILTERS}

    def homeVideoContent(self):
        now = int(time.time())
        if self._home_cache and now - self._home_cache_time < 300:
            return {"list": list(self._home_cache)}
        html = self._get_html(HOST + "/", timeout=5)
        if not html:
            return {"list": []}
        cards = self._parse_cards(html)[:60]
        self._home_cache = cards
        self._home_cache_time = now
        return {"list": list(cards)}

    # ============================================================
    # 分类列表
    # ============================================================
    def _fetch_cat(self, base, page):
        """base 形如 /film/dianyingd/dongzuoa；返回 (cards, pagecount)"""
        if page <= 1:
            html = self._get_html(HOST + base, timeout=5)
            if html:
                return self._parse_cards(html), self._parse_pagecount(html, page)
            return [], 1
        # 翻页 URL 模式未知，依次尝试常见形式
        for cand in ("%s?page=%d" % (base, page),
                     "%s/page/%d" % (base, page),
                     "%s-%d" % (base, page)):
            html = self._get_html(HOST + cand, timeout=5)
            if not html:
                continue
            cards = self._parse_cards(html)
            if cards:
                return cards, self._parse_pagecount(html, page)
        return [], page

    # 一级分类页无影片内容时, 用搜索关键词兜底
    _SEARCH_FALLBACK = {
        "juchanga": [("有声动漫", "动漫"), ("漫剧", "漫剧"), ("AI漫剧", "AI漫剧")],
        "yingshia": [("电影解说", "解说"), ("预告解说", "解说"), ("预告片", "预告"), ("剧情介绍", "解说")],
    }

    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = max(1, int(pg or 1))
            ext = {}
            if extend:
                if isinstance(extend, dict):
                    ext = extend
                elif isinstance(extend, str):
                    try:
                        ext = json.loads(extend)
                    except Exception:
                        ext = {}

            sub = (ext.get("type") or "").strip()
            year_kw = (ext.get("year") or "").strip()

            base = "/film/" + str(tid)
            if sub:
                base = base + "/" + sub

            cache_key = "%s|%s|%s" % (base, page, year_kw)
            now = int(time.time())
            hit = self._cat_cache.get(cache_key)
            if hit and now - hit[0] < self._cat_cache_ttl:
                return hit[1]

            cards, pagecount = self._fetch_cat(base, page)

            # --- 空分类搜索兜底 (AI剧场/影视解说等) ---
            if not cards and tid in self._SEARCH_FALLBACK:
                fallback_list = self._SEARCH_FALLBACK[tid]
                kw = ""
                for sub_name, search_kw in fallback_list:
                    if sub == sub_name or not sub:
                        kw = search_kw
                        break
                if not kw and fallback_list:
                    kw = fallback_list[0][1]
                if kw:
                    search_results = self._search_once(kw, 1)
                    if search_results:
                        cards = search_results[:24]
                        pagecount = 1

            # 年份客户端过滤（兜底）
            if year_kw and cards:
                if year_kw.startswith("-"):
                    ylimit = int(year_kw[1:])
                    cards = [c for c in cards
                             if not c["vod_remarks"].isdigit() or int(c["vod_remarks"]) <= ylimit]
                else:
                    cards = [c for c in cards if c["vod_remarks"] == year_kw]

            result = {
                "list": cards, "page": page,
                "pagecount": max(pagecount, page),
                "limit": 24, "total": max(pagecount, page) * 24,
            }
            if len(self._cat_cache) > 60:
                self._cat_cache.clear()
            self._cat_cache[cache_key] = (now, result)
            return result
        except Exception:
            return {"page": 1, "pagecount": 1, "limit": 24, "total": 0, "list": []}

    # ============================================================
    # 剧圈圈备用线路: 搜索 + 详情剧集 + 播放解析
    # ============================================================
    _JQQ_RE_PLAY_EP = re.compile(
        r'<a[^>]+href="(/play/(\d+-\d+-\d+)\.html)"[^>]*>([\s\S]*?)</a>')
    _JQQ_RE_PLAYLIST = re.compile(
        r'<div[^>]*class="[^"]*module-play-list[^"]*"[^>]*>([\s\S]*?)</div>\s*</div>')
    _JQQ_RE_PLAYER_JSON = re.compile(
        r'var\s+player_aaaa\s*=\s*(\{[\s\S]*?\})\s*(?:</script>|;|&)')

    def _jqq_get_html(self, url, timeout=6):
        try:
            rsp = self.fetch(url, headers=self._jqq_header, timeout=timeout)
            return self._rsp_text(rsp)
        except Exception:
            return ""

    def _jqq_search(self, name):
        """搜索剧圈圈, 返回 vid 或空"""
        name = (name or "").strip()
        if not name:
            return ""
        now = int(time.time())
        cached = self._jqq_search_cache.get(name)
        if cached and now - cached[0] < self._jqq_search_cache_ttl:
            return cached[1]
        try:
            url = JQQ_HOST + "/index.php/ajax/suggest?mid=1&wd=" + quote(name) + "&page=1"
            rsp = self.fetch(url, headers=self._jqq_header, timeout=6)
            data = json.loads(self._rsp_text(rsp))
            if data and data.get("code") == 1:
                for item in data.get("list", []):
                    iname = (item.get("name") or "").strip()
                    vid = str(item.get("id", ""))
                    if vid and (iname == name or name in iname or iname in name):
                        if len(self._jqq_search_cache) > 100:
                            self._jqq_search_cache.clear()
                        self._jqq_search_cache[name] = (now, vid)
                        return vid
        except Exception:
            pass
        if len(self._jqq_search_cache) > 100:
            self._jqq_search_cache.clear()
        self._jqq_search_cache[name] = (now, "")
        return ""

    def _jqq_get_episodes(self, vid):
        """获取剧圈圈详情页剧集列表, 返回 [(ep_name, play_path), ...] 或 []"""
        vid = str(vid)
        now = int(time.time())
        cached = self._jqq_detail_cache.get(vid)
        if cached and now - cached[0] < self._jqq_detail_cache_ttl:
            return cached[1]
        url = JQQ_HOST + "/vod/" + vid + ".html"
        html = self._jqq_get_html(url, timeout=8)
        episodes = []
        if html:
            blocks = self._JQQ_RE_PLAYLIST.findall(html)
            for chunk in blocks:
                seen = set()
                raw = []
                for em in self._JQQ_RE_PLAY_EP.finditer(chunk):
                    path = em.group(1)
                    if path in seen:
                        continue
                    seen.add(path)
                    ep_name = re.sub(r"<[^>]*>", "", em.group(3)).strip() or "正片"
                    nm = re.search(r"-(\d+)\.html$", path)
                    nid = int(nm.group(1)) if nm else 0
                    raw.append((nid, ep_name, path))
                raw.sort(key=lambda x: x[0])
                if raw:
                    episodes = [(name, path) for _, name, path in raw]
                    break
        if len(self._jqq_detail_cache) > 100:
            self._jqq_detail_cache.clear()
        self._jqq_detail_cache[vid] = (now, episodes)
        return episodes

    def _jqq_resolve_play(self, play_path):
        """剧圈圈播放页 -> sign 解密 -> 真实 m3u8, 30min 缓存"""
        play_path = str(play_path).strip()
        if play_path.startswith("/"):
            url = JQQ_HOST + play_path
        elif play_path.startswith("http"):
            url = play_path
        else:
            url = JQQ_HOST + "/" + play_path
        now = int(time.time())
        cached = self._jqq_token_cache.get(url)
        if cached and now - cached[0] < self._jqq_token_cache_ttl:
            return cached[1]
        real_url = ""
        html = self._jqq_get_html(url, timeout=8)
        if html:
            player = self._jqq_extract_player_aaaa(html)
            if player:
                url_enc = player.get("url", "")
                if url_enc:
                    api_rsp = self._http_post(
                        JQQ_HOST + "/jx/api.php",
                        data={"vid": url_enc},
                        timeout=10,
                    )
                    if api_rsp:
                        try:
                            data = json.loads(api_rsp)
                            if data.get("code") == 200:
                                enc_url = (data.get("data") or {}).get("url", "")
                                if enc_url:
                                    real_url = _jqq_sign_decrypt(enc_url)
                        except Exception:
                            pass
        if len(self._jqq_token_cache) > 300:
            self._jqq_token_cache.clear()
        self._jqq_token_cache[url] = (now, real_url)
        return real_url

    def _jqq_extract_player_aaaa(self, html):
        """提取 player_aaaa JSON"""
        if not html:
            return None
        m = self._JQQ_RE_PLAYER_JSON.search(html)
        if m:
            try:
                json_str = m.group(1)
                brace = 0
                end = 0
                for i, c in enumerate(json_str):
                    if c == '{':
                        brace += 1
                    elif c == '}':
                        brace -= 1
                        if brace == 0:
                            end = i + 1
                            break
                return json.loads(json_str[:end])
            except Exception:
                pass
        try:
            idx = html.find("player_aaaa")
            if idx < 0:
                return None
            brace_start = html.find("{", idx)
            if brace_start < 0:
                return None
            depth = 0
            for i in range(brace_start, len(html)):
                c = html[i]
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        return json.loads(html[brace_start:i + 1])
        except Exception:
            pass
        return None

    # ============================================================
    # 详情页
    # ============================================================
    def _parse_detail(self, html):
        if not html:
            return None
        title_m = re.search(r'<h1[^>]*itemprop="name"[^>]*>(.*?)</h1>', html, re.DOTALL) \
                  or re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
        if not title_m:
            return None
        vod_name = html_mod.unescape(self._strip_tags(title_m.group(1)))

        # meta 行: "动作 犯罪　/　1973　/　中国香港　/　汉语普通话" / 导演： / 主演：
        metas = re.findall(r'<p class="wu-detail-meta">(.*?)</p>', html, re.DOTALL)
        vod_year = vod_area = vod_lang = vod_type = vod_actor = vod_director = ""
        if metas:
            first = html_mod.unescape(self._strip_tags(metas[0]))
            parts = [p.strip() for p in re.split(r'[/／]', first) if p.strip()]
            if len(parts) >= 1:
                vod_type = parts[0].split()[0] if parts[0] else ""
            if len(parts) >= 2 and re.match(r'^(19|20)\d{2}$', parts[1]):
                vod_year = parts[1]
            if len(parts) >= 3:
                vod_area = parts[2]
            if len(parts) >= 4:
                vod_lang = parts[3]
            for line in metas[1:]:
                txt = html_mod.unescape(self._strip_tags(line))
                if txt.startswith("导演"):
                    vod_director = txt.split("：", 1)[-1].split(":", 1)[-1].strip()
                elif txt.startswith("主演") or txt.startswith("演员"):
                    vod_actor = txt.split("：", 1)[-1].split(":", 1)[-1].strip()

        summary_m = re.search(r'<p class="wu-detail-summary"[^>]*>(.*?)</p>', html, re.DOTALL)
        vod_content = html_mod.unescape(self._strip_tags(summary_m.group(1)))[:800] if summary_m else ""

        # 封面
        pic_m = re.search(r'itemprop="image"[^>]*src="([^"]+)"', html) \
                or re.search(r'data-cover-src="([^"]+)"', html) \
                or re.search(r'itemprop="image"[^>]*content="([^"]+)"', html)
        vod_pic = pic_m.group(1) if pic_m else ""

        # 剧集面板（每个面板=一条线路）
        play_from, play_url = [], []
        panels = re.findall(r'<section class="wu-episode-panel.*?</section>', html, re.DOTALL)
        for idx, p in enumerate(panels):
            name_m = re.search(r'<h2>\s*选集播放\s*</h2>\s*<span>(.*?)</span>', p, re.DOTALL)
            line_name = html_mod.unescape(self._strip_tags(name_m.group(1))) if name_m else "线路%d" % (idx + 1)
            eps = []
            seen_ep = set()
            for href, text in re.findall(r'<a[^>]+href="(/play/[^"]+)"[^>]*>(.*?)</a>', p, re.DOTALL):
                ep_name = html_mod.unescape(self._strip_tags(text))
                if not ep_name or href in seen_ep:
                    continue
                seen_ep.add(href)
                eps.append("%s$%s" % (ep_name, href))
            if eps:
                play_from.append(line_name)
                play_url.append("#".join(eps))

        # 单 panel 都没有时，兜底抓"立即播放"链接
        if not play_url:
            pm = re.search(r'<a[^>]+href="(/play/[^"]+)"[^>]*>\s*立即播放\s*</a>', html)
            if pm:
                play_from = ["在线播放"]
                play_url = ["正片$%s" % pm.group(1)]

        if not play_url:
            return None
        return {
            "vod_name": vod_name, "vod_pic": self._pic(vod_pic),
            "vod_year": vod_year, "vod_area": vod_area, "vod_lang": vod_lang,
            "vod_type": vod_type, "vod_actor": vod_actor, "vod_director": vod_director,
            "vod_content": vod_content,
            "play_from": play_from, "play_url": play_url,
        }

    def detailContent(self, ids):
        if isinstance(ids, str):
            ids = [ids]
        vod_id = str(ids[0])
        now = int(time.time())
        hit = self._detail_cache.get(vod_id)
        if hit and now - hit[0] < self._detail_cache_ttl:
            return hit[1]

        url = "%s/film/%s.html" % (HOST, vod_id)
        html = ""
        for _ in range(2):
            html = self._get_html(url, timeout=5)
            if html and 'wu-detail' in html:
                break
            time.sleep(0.1)
        info = self._parse_detail(html) if html else None
        if not info:
            return {"list": []}

        vod = {
            "vod_id": vod_id,
            "vod_name": info["vod_name"],
            "vod_pic": info["vod_pic"],
            "type_name": info["vod_type"],
            "vod_year": info["vod_year"],
            "vod_area": info["vod_area"],
            "vod_lang": info["vod_lang"],
            "vod_actor": info["vod_actor"],
            "vod_director": info["vod_director"],
            "vod_content": info["vod_content"],
            "vod_remarks": info["vod_year"] or "HD",
            "vod_play_from": "$$$".join(info["play_from"]),
            "vod_play_url": "$$$".join(info["play_url"]),
        }

        # --- 剧圈圈备用线路: 搜索同名影片, 添加为额外线路 ---
        try:
            jqq_vid = self._jqq_search(info["vod_name"])
            if jqq_vid:
                jqq_eps = self._jqq_get_episodes(jqq_vid)
                if jqq_eps:
                    jqq_eps_str = "#".join(
                        "%s$jqq:%s" % (ep_name, path) for ep_name, path in jqq_eps
                    )
                    extra_from = info["play_from"] + ["剧圈圈"]
                    extra_url = info["play_url"] + [jqq_eps_str]
                    vod["vod_play_from"] = "$$$".join(extra_from)
                    vod["vod_play_url"] = "$$$".join(extra_url)
        except Exception:
            pass

        result = {"list": [vod]}
        if len(self._detail_cache) > 200:
            self._detail_cache.clear()
        self._detail_cache[vod_id] = (now, result)
        return result

    # ============================================================
    # 搜索
    # ============================================================
    def _search_once(self, key, page):
        if page <= 1:
            url = "%s/film/search=%s" % (HOST, quote(key))
        else:
            url = "%s/film/search=%s?page=%d" % (HOST, quote(key), page)
        html = self._get_html(url, timeout=5)
        if html and '/film/' in html:
            return self._parse_cards(html)
        return None

    def searchContent(self, key, quick, pg="1"):
        try:
            page = max(1, int(pg or 1))
            key = (key or "").strip()
            if re.search(r'%[0-9A-Fa-f]{2}', key):
                try:
                    key = unquote(key)
                except Exception:
                    pass

            vods = self._search_once(key, page) or self._search_once(key, page)

            # 无结果且含数字 -> 中文数字重试
            if not vods and re.search(r'\d', key):
                alt = re.sub(r'\d', lambda m: "零一二三四五六七八九"[int(m.group(0))], key)
                if alt != key:
                    vods = self._search_once(alt, page)

            return {"list": vods or []}
        except Exception:
            return {"list": []}

    # ============================================================
    # 播放解析
    # ============================================================
    @staticmethod
    def _b64url(token):
        try:
            token = token.strip()
            raw = base64.b64decode(token + "=" * (-len(token) % 4))
            return raw.decode('utf-8', 'ignore').strip()
        except Exception:
            return ""

    def _get_play_urls(self, play_path):
        """播放页 -> [m3u8直链,...]（多线路token全解出），30分钟缓存"""
        play_path = str(play_path).replace("\\/", "/").strip()
        if play_path.startswith("/"):
            url = HOST + play_path
        elif play_path.startswith("http"):
            url = play_path
        else:
            url = HOST + "/" + play_path

        now = int(time.time())
        cached = self._token_cache.get(url)
        if cached and now - cached[0] < self._token_cache_ttl:
            return cached[1]

        urls = []
        html = self._get_html(url, timeout=5)
        if html:
            # 方式1: data-video-token (base64) —— 已验证
            for tk in re.findall(r'data-video-token="([^"]+)"', html):
                u = self._b64url(tk)
                if u and u.startswith("http") and u not in urls:
                    urls.append(u)
            # 方式2: 页面内直接出现的媒体地址
            if not urls:
                for u in re.findall(r'(https?://[^\s"\'<>]+\.(?:m3u8|mp4|flv)[^\s"\'<>]*)', html):
                    u = u.replace("\\/", "/")
                    if u not in urls:
                        urls.append(u)

        if len(self._token_cache) > 500:
            self._token_cache.clear()
        self._token_cache[url] = (now, urls)
        return urls

    def _direct_result(self, url):
        is_m3u8 = ".m3u8" in url.lower()
        header = {"User-Agent": UA}
        return {
            "parse": 0, "playUrl": "", "url": url,
            "header": header,
            "format": "application/x-mpegURL" if is_m3u8 else "",
            "contentType": "application/x-mpegURL" if is_m3u8 else "",
        }

    # ===== 外部解析器配置（CDN 故障兜底）=====
    def _get_player_config(self):
        """获取 /common/player-config, 缓存 10min。返回外部解析器列表。"""
        now = int(time.time())
        if self._player_config and now - self._player_config_time < self._player_config_ttl:
            return self._player_config
        try:
            rsp = self.fetch(HOST + "/common/player-config",
                             headers=self.header, timeout=5)
            cfg = json.loads(self._rsp_text(rsp))
            if isinstance(cfg, dict):
                self._player_config = cfg
                self._player_config_time = now
                return cfg
        except Exception:
            pass
        return {"players": []}

    @staticmethod
    def _build_parser_url(parse_url, raw_url):
        """根据 parse_url 模板构建外部解析器 URL。"""
        parse_url = (parse_url or "").strip()
        raw_url = (raw_url or "").strip()
        if not parse_url or not raw_url:
            return ""
        if "{url}" in parse_url:
            return parse_url.replace("{url}", quote(raw_url, safe=""))
        if "{raw_url}" in parse_url:
            return parse_url.replace("{raw_url}", raw_url)
        return parse_url + quote(raw_url, safe="")

    def playerContent(self, flag, id, vipFlags):
        if not id:
            return {"parse": 0, "playUrl": "", "url": ""}

        # --- 剧圈圈线路: jqq: 前缀 -> sign 解密直链 ---
        if str(id).startswith("jqq:"):
            play_path = str(id)[4:]
            real_url = self._jqq_resolve_play(play_path)
            if real_url and real_url.startswith("http"):
                if self._probe(real_url, timeout=2):
                    return self._direct_result(real_url)
                return self._direct_result(real_url)
            return {"parse": 0, "playUrl": "", "url": ""}

        urls = self._get_play_urls(id)
        if not urls:
            return {"parse": 0, "playUrl": "", "url": ""}

        # --- 阶段1: DNS 预检过滤, 快速剔除死域名 URL ---
        alive_urls = [u for u in urls if self._dns_ok(u)]
        if alive_urls:
            probe_pool = alive_urls
        else:
            # 全部 DNS 死亡, 仍尝试探测 (万一 DNS 缓存过期)
            probe_pool = urls

        # --- 阶段2: 并发探测择优（同集多线路token），失败自动换源 ---
        best = probe_pool[0]
        if len(probe_pool) > 1 and ThreadPoolExecutor is not None:
            try:
                with ThreadPoolExecutor(max_workers=min(4, len(probe_pool))) as ex:
                    results = list(ex.map(lambda u: self._probe(u, timeout=1.5), probe_pool))
                for u, ok in zip(probe_pool, results):
                    if ok:
                        best = u
                        break
            except Exception:
                if not self._probe(probe_pool[0], timeout=1.5) and self._probe(probe_pool[-1], timeout=1.5):
                    best = probe_pool[-1]
        else:
            for u in probe_pool:
                if self._probe(u, timeout=1.5):
                    best = u
                    break

        # --- 阶段3: http/https 互翻兜底 ---
        if not self._probe(best, timeout=1.2):
            flipped = ""
            if best.startswith("http://"):
                flipped = "https://" + best[7:]
            elif best.startswith("https://"):
                flipped = "http://" + best[8:]
            if flipped and self._probe(flipped, timeout=1.2):
                best = flipped

        # --- 阶段4: 直链存活 -> 直接播放 ---
        if self._probe(best, timeout=1.2):
            return self._direct_result(best)

        # --- 阶段5: 直链全死 -> 外部解析器兜底 ---
        # 获取站点配置的外部解析器, 用首个可用 URL 构建解析地址
        # 返回 parse=1 让客户端走 webview 解析
        raw_url = urls[0]
        cfg = self._get_player_config()
        players = cfg.get("players", []) if isinstance(cfg, dict) else []
        for p in players:
            if not p.get("enabled"):
                continue
            parse_url = (p.get("parse_url") or "").strip()
            if not parse_url or parse_url == "本地解析":
                continue
            parser_url = self._build_parser_url(parse_url, raw_url)
            if parser_url:
                return {
                    "parse": 1, "playUrl": "", "url": parser_url,
                    "header": {"User-Agent": UA},
                }

        # 所有兜底失败, 返回首个 URL (让客户端自行尝试)
        return self._direct_result(best)

    # ============================================================
    # 本地图片代理
    # ============================================================
    _PLACEHOLDER_GIF = (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff"
        b"\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
        b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    )

    def localProxy(self, param):
        try:
            url = ""
            if isinstance(param, dict):
                url = unquote(param.get("url", "") or "")
            elif isinstance(param, str):
                m = re.search(r'[?&]url=([^&]+)', param)
                if m:
                    url = unquote(m.group(1))
            if not url:
                return [404, "text/plain", b"missing url", ""]

            now = int(time.time())
            cached = self._img_cache.get(url)
            if cached and now - cached[0] < self._img_cache_ttl:
                return [cached[1], cached[2], cached[3], ""]

            headers = {
                "User-Agent": UA,
                "Referer": HOST + "/",
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            }
            rsp = self.fetch(url, headers=headers, timeout=4)
            content = rsp.content
            code = int(getattr(rsp, "status_code", 200) or 200)
            ctype = "image/jpeg"
            try:
                ctype = rsp.headers.get("Content-Type", "").split(";")[0].strip() or ctype
            except Exception:
                pass

            is_image = "image" in ctype.lower() or \
                (len(content) > 200 and (content[:3] == b"\xff\xd8\xff" or
                 content[:4] in (b"GIF8", b"\x89PNG", b"RIFF", b"BM\x00\x00") or
                 (content[:4] == b"RIFF" and b"WEBP" in content[:16])))
            if not is_image or code >= 400 or len(content) < 100:
                return [200, "image/gif", self._PLACEHOLDER_GIF, ""]

            if content[:3] == b"\xff\xd8\xff":
                ctype = "image/jpeg"
            elif content[:4] == b"\x89PNG":
                ctype = "image/png"
            elif content[:4] == b"GIF8":
                ctype = "image/gif"

            if len(self._img_cache) >= 200:
                old = sorted(self._img_cache, key=lambda k: self._img_cache[k][0])[:100]
                for k in old:
                    del self._img_cache[k]
            self._img_cache[url] = (now, code, ctype, content)
            return [code, ctype, content, ""]
        except Exception:
            return [200, "image/gif", self._PLACEHOLDER_GIF, ""]

    def destroy(self):
        pass

    def close(self):
        self.destroy()
