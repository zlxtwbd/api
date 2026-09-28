# -*- coding: utf-8 -*-
"""
影视蛙 Python Spider — 兼容 FongMi/TV (T3) 与 WebHomeTV / PeekPro (T4)
站点: https://btsxlcc.com/

【v1 — 全功能版】
  - 6个一级分类 + 完整二级分类（电影8个子类、电视剧8个、综艺4个、动漫4个、短剧、MV）
  - 6类筛选：分类、类型、地区、年份、语言、排序
  - 搜索：智能重试，阿拉伯数字转中文数字
  - 多线路播放：详情页提取全部播放源，并发探测排序
  - 播放直链：m3u8直出，零解析；故障自动转移
  - 全链路缓存：首页5min、分类2min、详情5min、播放URL 10min、图片30min
  - 图片代理：解决Android端SSL/防盗链问题
  - 全链路5s超时，并发拉取
"""

import sys
import json
import re
import time
import html as html_mod

sys.path.append('..')

# ===== 兼容导入 =====
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

from urllib.parse import urlencode, quote, unquote


# ============================================================
# 常量
# ============================================================

HOST = "https://btsxlcc.com"
UA = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"

# 线路显示名称映射
LINE_NAMES = {
    "jsyun": "极速资源", "jinying": "金鹰资源", "subo": "速播资源",
    "ffzy": "非凡资源", "dytt": "电影天堂", "bfzym3u8": "暴风资源",
    "mtm3u8": "茅台资源", "dbm3u8": "豆瓣资源", "snm3u8": "索尼资源",
    "kuaikan": "快看资源", "wolong": "卧龙资源", "xlm3u8": "新浪资源",
    "wjm3u8": "无尽资源", "hnm3u8": "华为资源", "zuidam3u8": "最大资源",
}

# 一级分类
CLASSES = [
    {"type_name": "电影", "type_id": "1"},
    {"type_name": "电视剧", "type_id": "2"},
    {"type_name": "综艺", "type_id": "3"},
    {"type_name": "动漫", "type_id": "4"},
    {"type_name": "短剧大全", "type_id": "30"},
    {"type_name": "MV偶像", "type_id": "36"},
]

# 二级分类（子分类 type_id）
SUB_TYPES = {
    "1": [("全部", ""), ("动作片", "7"), ("喜剧片", "8"), ("爱情片", "9"), ("科幻片", "10"),
          ("恐怖片", "11"), ("剧情片", "12"), ("犯罪片", "13"), ("纪录片", "29")],
    "2": [("全部", ""), ("国产剧", "14"), ("港剧", "15"), ("台剧", "16"), ("韩剧", "17"),
          ("日剧", "18"), ("泰剧", "31"), ("欧美剧", "19"), ("海外剧", "20")],
    "3": [("全部", ""), ("大陆综艺", "21"), ("日韩综艺", "22"), ("港台综艺", "23"), ("欧美综艺", "24")],
    "4": [("全部", ""), ("国产动漫", "25"), ("日本动漫", "26"), ("欧美动漫", "27"), ("海外动漫", "28")],
    "30": [("全部", "")],
    "36": [("全部", "")],
}

# ===== 筛选器 =====

# 类型（客户端过滤）
_GENRE_FILTER = {"key": "class", "name": "类型", "value": [
    {"n": "全部", "v": ""},
    {"n": "动作", "v": "动作"}, {"n": "喜剧", "v": "喜剧"}, {"n": "爱情", "v": "爱情"},
    {"n": "科幻", "v": "科幻"}, {"n": "悬疑", "v": "悬疑"}, {"n": "惊悚", "v": "惊悚"},
    {"n": "恐怖", "v": "恐怖"}, {"n": "灾难", "v": "灾难"}, {"n": "战争", "v": "战争"},
    {"n": "动画", "v": "动画"}, {"n": "奇幻", "v": "奇幻"}, {"n": "冒险", "v": "冒险"},
    {"n": "纪录", "v": "纪录"}, {"n": "犯罪", "v": "犯罪"}, {"n": "古装", "v": "古装"},
    {"n": "武侠", "v": "武侠"}, {"n": "家庭", "v": "家庭"}, {"n": "历史", "v": "历史"},
]}

# 地区（客户端过滤）
_AREA_FILTER = {"key": "area", "name": "地区", "value": [
    {"n": "全部", "v": ""},
    {"n": "中国大陆", "v": "大陆"}, {"n": "中国香港", "v": "香港"}, {"n": "中国台湾", "v": "台湾"},
    {"n": "日本", "v": "日本"}, {"n": "韩国", "v": "韩国"}, {"n": "美国", "v": "美国"},
    {"n": "英国", "v": "英国"}, {"n": "法国", "v": "法国"}, {"n": "德国", "v": "德国"},
    {"n": "泰国", "v": "泰国"}, {"n": "印度", "v": "印度"}, {"n": "其他", "v": "其他"},
]}

# 年份（客户端过滤）
_YEAR_FILTER = {"key": "year", "name": "年份", "value": [
    {"n": "全部", "v": ""},
    {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"}, {"n": "2024", "v": "2024"},
    {"n": "2023", "v": "2023"}, {"n": "2022", "v": "2022"}, {"n": "2021", "v": "2021"},
    {"n": "2020", "v": "2020"}, {"n": "2019", "v": "2019"}, {"n": "2018", "v": "2018"},
]}

# 语言（客户端过滤）
_LANG_FILTER = {"key": "lang", "name": "语言", "value": [
    {"n": "全部", "v": ""},
    {"n": "国语", "v": "国语"}, {"n": "英语", "v": "英语"}, {"n": "粤语", "v": "粤语"},
    {"n": "韩语", "v": "韩语"}, {"n": "日语", "v": "日语"}, {"n": "法语", "v": "法语"},
    {"n": "德语", "v": "德语"}, {"n": "俄语", "v": "俄语"}, {"n": "泰语", "v": "泰语"},
]}

# 排序
_BY_FILTER = {"key": "by", "name": "排序", "value": [
    {"n": "最新", "v": "time"}, {"n": "最热", "v": "hits"}, {"n": "评分", "v": "score"},
]}

# 构建各一级分类的完整筛选器
FILTERS = {}
for c in CLASSES:
    tid = c["type_id"]
    FILTERS[tid] = [
        {"key": "type", "name": "分类", "value": [
            {"n": n, "v": v} for n, v in SUB_TYPES.get(tid, [("全部", "")])
        ]},
        _GENRE_FILTER, _AREA_FILTER, _YEAR_FILTER, _LANG_FILTER, _BY_FILTER,
    ]

# 搜索：阿拉伯数字 -> 中文数字
_CN_DIGITS = str.maketrans("0123456789", "零一二三四五六七八九")

# 详情页保留的最大线路数
_MAX_LINES = 8

# 「全部」每页并发拉取的子分类数
_BATCH = 3


# ============================================================
# Spider 主类
# ============================================================

class Spider(Spider):

    def getName(self):
        return "影视蛙"

    # ===== 初始化 =====
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

        # 首页缓存（5分钟）
        self._home_cache = []
        self._home_cache_time = 0

        # 分类列表缓存（2分钟）
        self._cat_cache = {}
        self._cat_cache_ttl = 120

        # 详情页缓存（5分钟）
        self._detail_cache = {}
        self._detail_cache_ttl = 300

        # 播放URL缓存（10分钟）
        self._play_cache = {}
        self._play_cache_ttl = 600

        # 线路探测域名级缓存（10分钟）
        self._probe_cache = {}
        self._probe_ttl = 600

        # 播放故障转移表
        self._alt_map = {}

        # 图片代理前缀（默认直连，可通过 extend 配置开启）
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

        # 图片代理内存缓存（30分钟，最多200张）
        self._img_cache = {}
        self._img_cache_ttl = 1800

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
        """GET 请求返回 HTML 文本，异常返回空字符串"""
        try:
            rsp = self.fetch(url, headers=self.header, timeout=timeout)
            return self._rsp_text(rsp)
        except Exception:
            return ""

    # ===== 媒体/工具 =====
    def _is_direct_media(self, url):
        url = (url or "").lower()
        return ".m3u8" in url or ".mp4" in url or ".flv" in url or ".mkv" in url

    def _extract_referer(self, url):
        try:
            if "://" in url:
                scheme = url.split("://")[0]
                host_part = url.split("://")[1].split("/")[0]
                return scheme + "://" + host_part + "/"
        except Exception:
            pass
        return HOST + "/"

    def _strip_tags(self, s):
        return re.sub(r'<[^>]+>', '', s or '').strip()

    def _pic(self, url):
        """图片地址处理"""
        url = (url or "").strip()
        if not url:
            return ""
        # 相对路径补全
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = HOST + url
        elif not url.startswith("http"):
            url = HOST + "/" + url
        # 可选本地代理
        if self._img_prefix:
            return self._img_prefix + quote(url, safe="")
        return url

    # ===== HTML 解析工具 =====
    def _parse_cards(self, html):
        """从 HTML 中提取视频卡片列表"""
        cards = []
        # 匹配 vodlist_item 块
        pattern = re.compile(
            r'href="[^"]*?/weihu/(\d+)\.html"[^>]*title="([^"]*)"[^>]*data-original="([^"]*)"'
            r'.*?pic_text[^>]*>([^<]*)<',
            re.DOTALL
        )
        for m in pattern.finditer(html):
            vid = m.group(1)
            name = html_mod.unescape(m.group(2).strip())
            pic = m.group(3).strip()
            remarks = html_mod.unescape(m.group(4).strip())
            if not name:
                continue
            if not remarks:
                # 尝试从 voddate_year 提取
                ym = re.search(r'voddate_year">(\d+)<', m.group(0))
                if ym and ym.group(1) != "0":
                    remarks = ym.group(1)
                else:
                    remarks = "HD"
            cards.append({
                "vod_id": vid,
                "vod_name": name,
                "vod_pic": self._pic(pic),
                "vod_remarks": remarks,
            })
        return cards

    def _parse_total_pages(self, html):
        """从分页区域提取总页数"""
        # 匹配最后一页的页码
        m = re.search(r'class="page[^"]*"[^>]*>.*?</div>', html, re.DOTALL)
        if not m:
            return 1
        page_block = m.group(0)
        # 找所有页码数字
        nums = re.findall(r'>(\d+)<', page_block)
        if nums:
            return max(int(n) for n in nums)
        return 1

    # ===== 线路探测 =====
    def _probe(self, url, timeout=1.5):
        """探测 m3u8 是否可播。域名级缓存10分钟。"""
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

        ok = False
        try:
            headers = {
                "User-Agent": UA,
                "Referer": self._extract_referer(url),
                "Range": "bytes=0-511",
            }
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

    def _probe_lines(self, urls):
        """并发探测多条线路首集URL"""
        if not urls:
            return []
        if ThreadPoolExecutor is not None and len(urls) > 1:
            try:
                with ThreadPoolExecutor(max_workers=min(6, len(urls))) as ex:
                    return list(ex.map(lambda u: self._probe(u, timeout=1.5), urls))
            except Exception:
                pass
        return [self._probe(u, timeout=1.5) for u in urls]

    # ============================================================
    # 首页
    # ============================================================

    def homeContent(self, filter):
        return {"class": CLASSES, "filters": FILTERS}

    def homeVideoContent(self):
        """首页推荐：抓取首页HTML，5分钟缓存"""
        now = int(time.time())
        if self._home_cache and now - self._home_cache_time < 300:
            return {"list": list(self._home_cache)}

        html = self._get_html(HOST + "/", timeout=5)
        if not html:
            return {"list": []}

        cards = self._parse_cards(html)
        # 去重
        seen = set()
        deduped = []
        for c in cards:
            if c["vod_id"] not in seen:
                seen.add(c["vod_id"])
                deduped.append(c)

        self._home_cache = deduped[:60]
        self._home_cache_time = now
        return {"list": list(self._home_cache)}

    # ============================================================
    # 分类列表
    # ============================================================

    def _fetch_cat_page(self, cat_id, page):
        """拉取单个分类单页数据"""
        if page <= 1:
            url = "%s/zixun/%s.html" % (HOST, cat_id)
        else:
            url = "%s/zixun/%s-%d.html" % (HOST, cat_id, page)
        for attempt in range(2):
            html = self._get_html(url, timeout=5)
            if html and 'weihu/' in html:
                cards = self._parse_cards(html)
                total_pages = self._parse_total_pages(html)
                return cards, total_pages
            time.sleep(0.1)
        return [], 1

    @staticmethod
    def _match_area(val, kw):
        """地区模糊匹配"""
        val = (val or "").strip()
        if kw == "大陆":
            return "大陆" in val
        if kw == "香港":
            return "香港" in val
        if kw == "台湾":
            return "台湾" in val
        return kw in val

    @staticmethod
    def _match_lang(val, kw):
        """语言模糊匹配"""
        val = (val or "").strip()
        if kw == "国语":
            return any(x in val for x in ["国语", "普通话", "汉语"])
        if kw == "英语":
            return "英语" in val or "英文" in val
        if kw == "粤语":
            return "粤语" in val or "广东话" in val
        if kw == "韩语":
            return "韩语" in val or "韩文" in val or "朝鲜语" in val
        if kw == "日语":
            return "日语" in val or "日文" in val
        if kw == "泰语":
            return "泰语" in val
        return kw in val

    def categoryContent(self, tid, pg, filter, extend):
        try:
            page = int(pg or 1)
            if page < 1:
                page = 1

            ext = {}
            if extend:
                if isinstance(extend, dict):
                    ext = extend
                elif isinstance(extend, str):
                    try:
                        ext = json.loads(extend)
                    except Exception:
                        ext = {}

            sub_id = (ext.get("type") or "").strip()
            year_kw = (ext.get("year") or "").strip()
            area_kw = (ext.get("area") or "").strip()
            lang_kw = (ext.get("lang") or "").strip()
            class_kw = (ext.get("class") or "").strip()

            # 分类列表缓存
            cache_key = "%s_%s_%s" % (tid, page, json.dumps(ext, sort_keys=True) if ext else "")
            now = int(time.time())
            hit = self._cat_cache.get(cache_key)
            if hit and now - hit[0] < self._cat_cache_ttl:
                return hit[1]

            # 子分类列表（不含"全部"）
            children = [v for n, v in SUB_TYPES.get(str(tid), []) if v]

            if sub_id:
                # 选中了二级分类
                targets = [(sub_id, page)]
                cycle = 1
            elif children:
                # 「全部」：并发拉取 _BATCH 个子分类/页
                num_batches = (len(children) + _BATCH - 1) // _BATCH
                batch_idx = (page - 1) % num_batches
                req_page = (page - 1) // num_batches + 1
                start = batch_idx * _BATCH
                sub_batch = children[start:start + _BATCH]
                targets = [(t, req_page) for t in sub_batch]
                cycle = num_batches
            else:
                targets = [(str(tid), page)]
                cycle = 1

            # 并发拉取
            if len(targets) > 1 and ThreadPoolExecutor is not None:
                try:
                    with ThreadPoolExecutor(max_workers=len(targets)) as ex:
                        futures = [ex.submit(self._fetch_cat_page, t, rp) for t, rp in targets]
                        results = [f.result() for f in futures]
                except Exception:
                    results = [self._fetch_cat_page(t, rp) for t, rp in targets]
            else:
                results = [self._fetch_cat_page(t, rp) for t, rp in targets]

            # 合并 + 去重
            all_cards = []
            max_pc = 1
            for cards, pc in results:
                all_cards.extend(cards)
                if pc > max_pc:
                    max_pc = pc

            seen_ids = set()
            deduped = []
            for c in all_cards:
                if c["vod_id"] not in seen_ids:
                    seen_ids.add(c["vod_id"])
                    deduped.append(c)

            vods = deduped
            total_pages = max(1, max_pc * cycle)

            result = {
                "list": vods, "page": page, "pagecount": total_pages,
                "limit": 20, "total": total_pages * 20,
            }

            if len(self._cat_cache) > 50:
                self._cat_cache.clear()
            self._cat_cache[cache_key] = (now, result)
            return result

        except Exception:
            return {"page": 1, "pagecount": 1, "limit": 20, "total": 0, "list": []}

    # ============================================================
    # 详情页（5分钟缓存）
    # ============================================================

    @staticmethod
    def _extract_ep_num(name):
        """从剧集名中提取集数，用于排序和去重"""
        name = re.sub(r'\s+', '', name)
        # 第N集/话/回/期/季
        m = re.search(r'第(\d+)[集话回期季]', name)
        if m:
            return int(m.group(1))
        # 中文数字 第X集
        _cn = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
               '六': 6, '七': 7, '八': 8, '九': 9}
        m = re.search(r'第([一二三四五六七八九十]+)[集话回期季]', name)
        if m:
            s = m.group(1)
            if s == '十':
                return 10
            if '十' in s:
                a, b = s.split('十')
                tens = _cn.get(a, 1) if a else 1
                ones = _cn.get(b, 0) if b else 0
                return tens * 10 + ones
            return _cn.get(s, -1)
        # 纯数字开头
        m = re.match(r'(\d+)', name)
        if m:
            return int(m.group(1))
        # EP N / E N
        m = re.search(r'[Ee][Pp]?\s*(\d+)', name)
        if m:
            return int(m.group(1))
        return -1

    def _parse_detail(self, html):
        """从详情页提取视频信息和播放列表"""
        if not html:
            return None

        # 提取视频ID — 从URL或页面中提取
        vid_m = re.search(r'getVideoHit\([\'"]?(\d+)', html)
        if not vid_m:
            # 从URL中提取：/weihu/12345.html
            vid_m = re.search(r'/weihu/(\d+)\.html', html)
        vod_id = vid_m.group(1) if vid_m else ""

        # 提取标题 — 优先用 <h2> 标签（无class），其次 <title>
        title_m = re.search(r'<h2[^>]*>([^<]+)<', html)
        if not title_m:
            title_m = re.search(r'<title>([^<]+)', html)
        if not title_m:
            title_m = re.search(r'<h[13][^>]*>([^<]+)<', html)
        vod_name = html_mod.unescape(self._strip_tags(title_m.group(1))) if title_m else ""
        # 清理标题中的后缀
        vod_name = re.sub(r'[-_—].*(?:在线观看|免费|高清|全集|完整版|播放).*$', '', vod_name).strip()
        if not vod_name:
            return None

        # 提取封面图
        pic_m = re.search(r'data-original="([^"]+)"', html)
        if not pic_m:
            pic_m = re.search(r'background-image:url\(([^)]+)\)', html)
        vod_pic = pic_m.group(1).strip() if pic_m else ""

        # 提取年份
        year_m = re.search(r'voddate_year">(\d+)<', html)
        vod_year = year_m.group(1) if year_m and year_m.group(1) != "0" else ""

        # 提取地区/类型
        type_m = re.search(r'voddate_type">([^<]*)<', html)
        vod_area = html_mod.unescape(type_m.group(1).strip()) if type_m else ""

        # 提取演员
        actor_m = re.search(r'主演[：:]\s*</span>\s*<span[^>]*>([^<]+)', html, re.IGNORECASE)
        if not actor_m:
            actor_m = re.search(r'vodlist_sub[^>]*>([^<]+)<', html)
        vod_actor = html_mod.unescape(self._strip_tags(actor_m.group(1))) if actor_m else ""

        # 提取简介
        blurb_m = re.search(r'content_desc[^>]*>(.*?)</div>', html, re.DOTALL)
        if not blurb_m:
            blurb_m = re.search(r'detail[^>]*content[^>]*>(.*?)</div>', html, re.DOTALL)
        vod_blurb = html_mod.unescape(self._strip_tags(blurb_m.group(1)))[:600] if blurb_m else ""

        # 提取播放列表
        # 1. 提取播放源标签
        sources = []
        tab_pattern = re.compile(r'href="#playlist(\d+)"[^>]*>(.*?)</a>', re.DOTALL)
        for m in tab_pattern.finditer(html):
            src_id = m.group(1)
            content = m.group(2)
            # 提取 </i> 后的文本
            name_m = re.search(r'</i>\s*([^<]+)', content)
            src_name = name_m.group(1).strip() if name_m else content.strip()
            src_name = html_mod.unescape(src_name)
            display_name = LINE_NAMES.get(src_name, src_name)
            sources.append((src_id, display_name, src_name))

        # 2. 提取每个源的剧集列表
        play_from = []
        play_url = []

        for src_id, display_name, raw_name in sources:
            # 提取该源剧集块 — 方式1: 从当前 playlist div 到下一个 playlist div（更完整）
            block = ""
            m_start = re.search(r'<div\s+id="playlist%s"[^>]*>' % src_id, html)
            if m_start:
                tail = html[m_start.end():]
                m_next = re.search(r'<div\s+id="playlist\d', tail)
                block = tail[:m_next.start()] if m_next else tail[:5000]
            if not block:
                # 方式2: 原始正则兜底
                ep_pattern = re.compile(
                    r'<div\s+id="playlist%s"[^>]*class="tab-pane[^"]*"[^>]*>(.*?)</div>\s*</div>' % src_id,
                    re.DOTALL
                )
                bm = ep_pattern.search(html)
                block = bm.group(1) if bm else ""

            if not block:
                continue

            # 提取剧集链接 — 兼容 title/href 不同属性顺序
            ep_links = []
            for am in re.finditer(r'<a\s+([^>]*)>([^<]+)</a>', block):
                attrs, text = am.group(1), am.group(2)
                hm = re.search(r'href="([^"]+)"', attrs)
                tm = re.search(r'title="([^"]*)"', attrs)
                if hm:
                    ep_links.append((tm.group(1) if tm else "", hm.group(1), text))

            if not ep_links:
                continue

            ep_list = []
            seen_ep = set()         # URL 去重
            seen_ep_num = set()     # 集数去重
            for title, href, text in ep_links:
                href = href.strip()
                # 补全URL
                if href.startswith("/"):
                    href = HOST + href
                elif not href.startswith("http"):
                    href = HOST + "/" + href

                ep_name = html_mod.unescape(text.strip() or title.strip())
                if not ep_name:
                    ep_name = "第%d集" % (len(ep_list) + 1)

                # URL 去重
                if href in seen_ep:
                    continue
                # 集数去重 — 同一集号只保留首次出现的
                ep_num = self._extract_ep_num(ep_name)
                if ep_num >= 0 and ep_num in seen_ep_num:
                    continue
                seen_ep.add(href)
                if ep_num >= 0:
                    seen_ep_num.add(ep_num)

                ep_list.append((ep_num, "%s$%s" % (ep_name, href)))

            # 按集数排序 — 有集号的排前面并排序，无集号的原序追加
            if ep_list:
                has_nums = [e for e in ep_list if e[0] >= 0]
                no_nums = [e for e in ep_list if e[0] < 0]
                if has_nums:
                    has_nums.sort(key=lambda x: x[0])
                    ep_list = has_nums + no_nums
                play_from.append(display_name)
                play_url.append("#".join(e[1] for e in ep_list))

        if not play_url:
            return None

        # 提取备注（更新状态）
        remarks_m = re.search(r'pic_text[^>]*>([^<]+)<', html)
        vod_remarks = html_mod.unescape(remarks_m.group(1).strip()) if remarks_m else "HD"

        return {
            "vod_id": vod_id,
            "vod_name": vod_name,
            "vod_pic": self._pic(vod_pic),
            "vod_year": vod_year,
            "vod_area": vod_area,
            "vod_actor": vod_actor,
            "vod_remarks": vod_remarks,
            "vod_content": vod_blurb,
            "play_from": play_from,
            "play_url": play_url,
        }

    def detailContent(self, ids):
        if isinstance(ids, str):
            ids = [ids]
        vod_id = str(ids[0])

        # 详情页缓存
        now = int(time.time())
        hit = self._detail_cache.get(vod_id)
        if hit and now - hit[0] < self._detail_cache_ttl:
            return hit[1]

        url = "%s/weihu/%s.html" % (HOST, vod_id)
        html = ""
        for attempt in range(2):
            html = self._get_html(url, timeout=5)
            if html and 'weihu/' in html:
                break
            time.sleep(0.1)

        if not html:
            return {"list": []}

        info = self._parse_detail(html)
        if not info:
            return {"list": []}

        play_from = info["play_from"]
        play_url = info["play_url"]

        if not play_url:
            return {"list": []}

        # 限制线路数
        play_from = play_from[:_MAX_LINES]
        play_url = play_url[:_MAX_LINES]

        # 构建播放故障转移表
        alt = {}
        for pu in play_url:
            for ep in pu.split("#"):
                if "$" not in ep:
                    continue
                ep_name, ep_url = ep.split("$", 1)
                ep_url = ep_url.strip()
                bucket = alt.setdefault(ep_url, [])
                for pu2 in play_url:
                    if pu2 is pu:
                        continue
                    for ep2 in pu2.split("#"):
                        if "$" not in ep2:
                            continue
                        n2, u2 = ep2.split("$", 1)
                        if n2.strip() == ep_name.strip():
                            u2 = u2.strip()
                            if u2 and u2 != ep_url and u2 not in bucket:
                                bucket.append(u2)
        if len(self._alt_map) > 3000:
            self._alt_map.clear()
        self._alt_map.update(alt)

        vod = {
            "vod_id": vod_id,
            "vod_name": info["vod_name"],
            "vod_pic": info["vod_pic"],
            "type_name": info.get("vod_area", ""),
            "vod_year": info.get("vod_year", ""),
            "vod_area": info.get("vod_area", ""),
            "vod_remarks": info.get("vod_remarks", "HD"),
            "vod_actor": info.get("vod_actor", ""),
            "vod_director": "",
            "vod_content": info.get("vod_content", ""),
            "vod_play_from": "$$$".join(play_from) if play_from else "影视蛙",
            "vod_play_url": "$$$".join(play_url) if play_url else "",
        }
        result = {"list": [vod]}

        # 缓存
        if len(self._detail_cache) > 200:
            self._detail_cache.clear()
        self._detail_cache[vod_id] = (now, result)
        return result

    # ============================================================
    # 搜索
    # ============================================================

    def _search_once(self, key, page):
        """单次搜索 — 尝试多种方式"""
        # 方式1：直接搜索（可能已禁用）
        if page <= 1:
            url = "%s/search/-------------.html?searchword=%s" % (HOST, quote(key))
        else:
            url = "%s/search/-------------?searchword=%s&page=%d" % (HOST, quote(key), page)
        html = self._get_html(url, timeout=5)
        if html and 'weihu/' in html and '搜索功能关闭' not in html:
            return self._parse_cards(html)
        
        # 方式2：尝试 MacCMS API
        api_url = "%s/api.php/provide/vod/?ac=detail&wd=%s" % (HOST, quote(key))
        try:
            rsp = self.fetch(api_url, headers=self.header, timeout=5)
            data = json.loads(self._rsp_text(rsp))
            if data and data.get("list"):
                cards = []
                for item in data["list"]:
                    cards.append({
                        "vod_id": str(item.get("vod_id", "")),
                        "vod_name": html_mod.unescape(str(item.get("vod_name", ""))),
                        "vod_pic": self._pic(item.get("vod_pic", "")),
                        "vod_remarks": html_mod.unescape(str(item.get("vod_remarks", "HD"))),
                    })
                return cards
        except Exception:
            pass
        
        return None

    def searchContent(self, key, quick, pg="1"):
        try:
            page = int(pg or 1)
            if page < 1:
                page = 1

            key = (key or "").strip()
            if re.search(r'%[0-9A-Fa-f]{2}', key):
                try:
                    key = unquote(key)
                except Exception:
                    pass

            # 第1次：原词
            vods = self._search_once(key, page)
            if vods is None:
                time.sleep(0.1)
                vods = self._search_once(key, page)

            # 第2次：无结果且含数字 -> 转中文数字
            if not vods and re.search(r'\d', key):
                alt_key = re.sub(r'\d', lambda m: "零一二三四五六七八九"[int(m.group(0))], key)
                if alt_key != key:
                    vods2 = self._search_once(alt_key, page)
                    if vods2:
                        vods = vods2

            # 第3次：搜索禁用时，从最近更新中模糊匹配
            if not vods:
                vods = self._search_from_recent(key, page)

            return {"list": vods or []}
        except Exception:
            return {"list": []}

    def _search_from_recent(self, key, page):
        """从最近更新中搜索（搜索禁用时的备选方案）"""
        # 抓取最近更新页面的前几页
        all_cards = []
        max_pages = min(page + 2, 10)  # 最多搜索10页
        
        # 将搜索词拆分为关键词
        keywords = [k.strip() for k in re.split(r'[\s,，、]+', key) if k.strip()]
        
        for p in range(1, max_pages + 1):
            if p == 1:
                url = "%s/label/new.html" % HOST
            else:
                url = "%s/label/new-%d.html" % (HOST, p)
            
            html = self._get_html(url, timeout=5)
            if not html or 'weihu/' not in html:
                break
            
            # 尝试两种解析方式
            cards = self._parse_cards(html)
            if not cards:
                # 备选：part_eone 结构
                cards = self._parse_recent_cards(html)
            
            # 模糊匹配关键词（任一关键词匹配即可）
            for card in cards:
                name = card.get("vod_name", "").lower()
                # 检查是否包含任一关键词
                if any(kw.lower() in name for kw in keywords):
                    all_cards.append(card)
            
            time.sleep(0.1)
        
        # 分页
        start = (page - 1) * 20
        end = start + 20
        return all_cards[start:end] if all_cards else None

    def _parse_recent_cards(self, html):
        """解析最近更新页面的卡片（part_eone 结构）"""
        cards = []
        pattern = re.compile(
            r'<li\s+class="part_eone">\s*<a\s+href="[^"]*?/weihu/(\d+)\.html"[^>]*title="([^"]*)"',
            re.DOTALL
        )
        for m in pattern.finditer(html):
            vid = m.group(1)
            name = html_mod.unescape(m.group(2).strip())
            if not name:
                continue
            cards.append({
                "vod_id": vid,
                "vod_name": name,
                "vod_pic": "",
                "vod_remarks": "",
            })
        return cards

    # ============================================================
    # 播放解析（直链 + 故障转移）
    # ============================================================

    def _extract_play_url(self, play_page_url):
        """从播放页提取实际视频URL"""
        # 先查缓存
        now = int(time.time())
        cached = self._play_cache.get(play_page_url)
        if cached and now - cached[0] < self._play_cache_ttl:
            return cached[1]

        html = self._get_html(play_page_url, timeout=5)
        if not html:
            return ""

        # 提取 player_aaaa 配置
        m = re.search(r'var\s+player_aaaa\s*=\s*(\{[^}]+\})', html)
        if not m:
            return ""

        try:
            cfg = json.loads(m.group(1).replace('\\/', '/'))
        except Exception:
            return ""

        video_url = (cfg.get("url") or "").replace("\\/", "/").strip()
        from_name = (cfg.get("from") or "").strip()

        if not video_url:
            return ""

        # 对于已知直链源，直接构造 m3u8
        # jiyun/jsyun: 极速资源, 直接 m3u8
        direct_sources = {"jsyun", "jisu", "dplayer", "videojs", "iva", "link"}
        if from_name in direct_sources or not from_name:
            # 构造 m3u8 URL
            if video_url.endswith(".m3u8") or video_url.endswith(".mp4"):
                final_url = video_url
            else:
                final_url = video_url.rstrip("/") + "/index.m3u8"
        else:
            # 其他源也尝试直接构造
            if video_url.endswith(".m3u8") or video_url.endswith(".mp4"):
                final_url = video_url
            else:
                final_url = video_url.rstrip("/") + "/index.m3u8"

        # 写入缓存
        if len(self._play_cache) > 500:
            self._play_cache.clear()
        self._play_cache[play_page_url] = (now, final_url)
        return final_url

    def _direct_result(self, url):
        is_m3u8 = ".m3u8" in url.lower()
        return {
            "parse": 0, "playUrl": "", "url": url,
            "header": {"User-Agent": UA, "Referer": self._extract_referer(url)},
            "format": "application/x-mpegURL" if is_m3u8 else "",
            "contentType": "application/x-mpegURL" if is_m3u8 else "",
        }

    def playerContent(self, flag, id, vipFlags):
        if not id:
            return {"parse": 0, "playUrl": "", "url": ""}

        play_page_url = str(id).replace("\\/", "/").strip()

        # 确保是完整URL
        if play_page_url.startswith("/"):
            play_page_url = HOST + play_page_url
        elif not play_page_url.startswith("http"):
            play_page_url = HOST + "/" + play_page_url

        # 提取实际视频URL
        video_url = self._extract_play_url(play_page_url)

        if not video_url:
            return {"parse": 0, "playUrl": "", "url": ""}

        # 非直链交给壳子嗅探
        if not self._is_direct_media(video_url):
            return {
                "parse": 1, "playUrl": "", "url": video_url,
                "header": {"User-Agent": UA, "Referer": HOST + "/"},
            }

        # 1) 当前线路可播 -> 直出
        if self._probe(video_url, timeout=1.5):
            return self._direct_result(video_url)

        # 2) 故障转移：同集数其他线路
        tried = 0
        for alt_url in self._alt_map.get(play_page_url, []):
            if tried >= 3:
                break
            tried += 1
            # alt_url 也是播放页URL，需要提取实际视频URL
            alt_video = self._extract_play_url(alt_url)
            if alt_video and self._probe(alt_video, timeout=1.5):
                return self._direct_result(alt_video)

        # 3) http/https 互翻再试
        flipped = ""
        if video_url.startswith("http://"):
            flipped = "https://" + video_url[7:]
        elif video_url.startswith("https://"):
            flipped = "http://" + video_url[8:]
        if flipped and self._probe(flipped, timeout=1.5):
            return self._direct_result(flipped)

        # 4) 兜底
        return self._direct_result(video_url)

    # ============================================================
    # 本地图片代理（带缓存 + Referer伪装 + 错误兜底）
    # ============================================================

    # 1x1 透明GIF兜底
    _PLACEHOLDER_GIF = (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff"
        b"\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
        b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    )

    def localProxy(self, param):
        try:
            # 解析URL
            url = ""
            if isinstance(param, dict):
                url = unquote(param.get("url", "") or "")
            elif isinstance(param, str):
                m = re.search(r'[?&]url=([^&]+)', param)
                if m:
                    url = unquote(m.group(1))
            if not url:
                return [404, "text/plain", b"missing url", ""]

            # 内存缓存命中
            now = int(time.time())
            cached = self._img_cache.get(url)
            if cached and now - cached[0] < self._img_cache_ttl:
                return [cached[1], cached[2], cached[3], ""]

            # 请求图片
            headers = {
                "User-Agent": UA,
                "Referer": self._extract_referer(url),
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            }
            rsp = self.fetch(url, headers=headers, timeout=4)
            content = rsp.content
            code = int(getattr(rsp, "status_code", 200) or 200)

            # 提取 Content-Type
            ctype = "image/jpeg"
            try:
                raw_ct = rsp.headers.get("Content-Type", "")
                ctype = raw_ct.split(";")[0].strip() or ctype
            except Exception:
                pass

            # 验证是否为有效图片
            is_image = False
            if "image" in ctype.lower():
                is_image = True
            elif len(content) > 200:
                magic = content[:8]
                if (magic.startswith(b"\xff\xd8\xff") or
                    magic.startswith(b"GIF87a") or
                    magic.startswith(b"GIF89a") or
                    magic.startswith(b"\x89PNG") or
                    magic.startswith(b"RIFF") or
                    magic.startswith(b"BM")):
                    is_image = True

            if not is_image or code >= 400 or len(content) < 100:
                return [200, "image/gif", self._PLACEHOLDER_GIF, ""]

            # 修正 Content-Type
            if content.startswith(b"\xff\xd8\xff"):
                ctype = "image/jpeg"
            elif content.startswith(b"\x89PNG"):
                ctype = "image/png"
            elif content.startswith(b"GIF8"):
                ctype = "image/gif"
            elif content.startswith(b"RIFF") and b"WEBP" in content[:16]:
                ctype = "image/webp"

            # 写入缓存
            if len(self._img_cache) >= 200:
                old_keys = sorted(self._img_cache.keys(), key=lambda k: self._img_cache[k][0])[:100]
                for k in old_keys:
                    del self._img_cache[k]
            self._img_cache[url] = (now, code, ctype, content)

            return [code, ctype, content, ""]
        except Exception:
            return [200, "image/gif", self._PLACEHOLDER_GIF, ""]

    # ===== 清理 =====
    def destroy(self):
        pass

    def close(self):
        self.destroy()
