import sys
import re
import json
import time
import html as html_lib
from urllib.parse import quote, unquote

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
requests.packages.urllib3.disable_warnings()

try:
    from base.spider import Spider as _SpiderBase
except Exception:
    class _SpiderBase(object):
        def init(self, extend=""):
            self.extend = extend

class Spider(_SpiderBase):
    def getName(self):
        return "麦田影院"

    def init(self, extend=""):
        super().init(extend)

        self.site_url = "https://mtyy7.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.site_url + "/",
            "Accept-Language": "zh-CN,zh;q=0.9"
        }
        self.sess = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        self.sess.mount("https://", HTTPAdapter(max_retries=retry))
        self.sess.mount("http://", HTTPAdapter(max_retries=retry))

        self.page_size = 42
        self.total = 9999
        self._cache = {}
        self._verified = set()
        self._captcha = None
        self._verify_failed = False
        self._search_cache = {}
        self._fallback_used = False

        cfg = self._parse_extend(extend)
        self._cfg = cfg
        self._manual_code = str(cfg.get("code") or "").strip()
        self._retry = int(cfg.get("retry") or 4)
        self._search_gap = float(cfg.get("search_gap") or 6)
        self._last_search = 0.0
        phpsessid = str(cfg.get("phpsessid") or "").strip()
        if phpsessid:
            self.sess.cookies.set("PHPSESSID", phpsessid, domain="mtyy7.com", path="/")
            self._verified = {"search", "show"}

        self._ocr = None
        try:
            import ddddocr
            self._ocr = ddddocr.DdddOcr(show_ad=False)
        except Exception:
            self._ocr = None

        self._fast_mode = (self._ocr is None and not phpsessid and not self._manual_code)
        if str(cfg.get("force_verify") or "") == "1":
            self._fast_mode = False

        def _split(v):
            return {x.strip().lower() for x in
                    re.split(r"[,，;；\s]+", str(v or "")) if x.strip()}

        self._block_from = {"mt源", "mtm3u8"} | _split(cfg.get("block_from"))
        self._block_from -= _split(cfg.get("keep_from"))

    def _from_blocked(self, name, form=""):
        n = re.sub(r"\s+", "", str(name or "")).lower()
        f = re.sub(r"\s+", "", str(form or "")).lower()
        return any(k and (k in n or k in f) for k in self._block_from)

    @staticmethod
    def _parse_extend(extend):
        if not extend:
            return {}
        if isinstance(extend, dict):
            return dict(extend)
        try:
            obj = json.loads(extend)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        out = {}
        for kv in str(extend).split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def fetch(self, url, timeout=12):
        self._verify_failed = False
        try:
            res = self.sess.get(url, headers=self.headers, timeout=timeout, verify=False)
        except Exception:
            return None
        if not res or not res.ok:
            return res

        if self._is_captcha_page(res.text):
            if self._fast_mode:
                self._verify_failed = True
                return res
            vtype = "search" if "/vodsearch/" in url else "show"
            if self._ensure_verified(vtype):
                try:
                    res = self.sess.get(url, headers=self.headers, timeout=timeout, verify=False)
                except Exception:
                    return None
                if self._is_captcha_page(res.text):
                    self._verified.discard(vtype)
                    self._verify_failed = True
            else:
                self._verify_failed = True
        return res

    @staticmethod
    def _is_captcha_page(text):
        return ('name="verify"' in text and "请输入验证码" in text) or "verify-submit" in text

    def _ensure_verified(self, vtype):
        if vtype in self._verified:
            return True
        for i in range(max(1, self._retry)):
            code = self._read_captcha(i)
            if not code:
                continue
            try:
                r = self.sess.post(
                    "{}/index.php/ajax/verify_check?type={}&verify={}".format(
                        self.site_url, vtype, code),
                    headers=dict(self.headers, **{"X-Requested-With": "XMLHttpRequest"}),
                    timeout=10, verify=False
                )
                ok = False
                try:
                    ok = int(json.loads(r.text).get("code", 0)) == 1
                except Exception:
                    ok = re.search(r'"code"\s*:\s*1\s*[,}]', r.text) is not None
                if ok:
                    self._verified.add(vtype)
                    self._verify_failed = False
                    return True
            except Exception:
                pass
        return False

    def _read_captcha(self, round_no):
        if self._manual_code and round_no == 0:
            c = re.sub(r"\D", "", self._manual_code)
            return c if len(c) == 4 else None
        try:
            r = self.sess.get(self.site_url + "/verify/index.html",
                              headers=self.headers, timeout=10, verify=False)
            if not r or not r.ok or not r.content:
                return None
            try:
                import base64
                self._captcha = base64.b64encode(r.content).decode("ascii")
            except Exception:
                pass
            if self._ocr is None:
                return None
            raw = self._ocr.classification(r.content)
            code = re.sub(r"\D", "", str(raw))[:4]
            return code if len(code) == 4 else None
        except Exception:
            return None

    def _abs(self, url):
        if not url:
            return ""
        url = html_lib.unescape(url.strip())
        if url.startswith("//"):
            return "https:" + url
        if url.startswith(("http://", "https://")):
            return url
        return self.site_url + (url if url.startswith("/") else "/" + url)

    @staticmethod
    def _clean(text):
        out = html_lib.unescape(re.sub(r"<[^>]+>", " ", text or ""))
        return re.sub(r"\s+", " ", out.replace("\xa0", " ")).strip()

    def _parse_list(self, page, pg):
        videos = []
        pattern = r'<a\b(?=[^>]*\bclass="public-list-exp")([^>]*)>(.*?)</a>'
        for m in re.finditer(pattern, page, re.S):
            attrs, inner = m.group(1), m.group(2)
            href = re.search(r'href=["\']([^"\']+)["\']', attrs)
            name = re.search(r'title=["\']([^"\']*)["\']', attrs)
            if not href or not name:
                continue
            vid = self._abs(href.group(1))
            title = self._clean(name.group(1))
            if not title or "/voddetail/" not in vid:
                continue
            pic = (re.search(r'data-src=["\']([^"\']+)["\']', inner)
                   or re.search(r'\bsrc=["\']([^"\']+)["\']', inner))
            pic_url = self._abs(pic.group(1)) if pic else ""
            if pic_url.startswith("data:"):
                pic_url = ""
            remark = re.search(r'class="public-list-prb[^"]*"[^>]*>(.*?)</span>', inner, re.S)
            year = re.search(r'class="public-prt[^"]*"[^>]*>(.*?)</span>', inner, re.S)
            remarks = self._clean(remark.group(1)) if remark else ""
            if year and self._clean(year.group(1)):
                remarks = (self._clean(year.group(1)) + " " + remarks).strip()
            videos.append({
                "vod_id": vid,
                "vod_name": title,
                "vod_pic": pic_url,
                "vod_remarks": remarks
            })
            self._cache[vid] = {
                "vod_name": title, "vod_pic": pic_url, "vod_remarks": remarks
            }
        return videos

    def _throttle_search(self):
        gap = self._search_gap - (time.time() - self._last_search)
        if gap > 0:
            time.sleep(min(gap, 8))
        self._last_search = time.time()

    def _pagecount(self, page, pg, count):
        nums = [int(x) for x in re.findall(r'-{6,}(\d+)-{3}\.html', page)]
        if count:
            return max(max(nums) if nums else 0, pg + 1)
        return max(nums) if nums else pg

    def _api_search(self, key):
        if key in self._search_cache:
            return self._search_cache[key]
        try:
            r = self.sess.get(self.site_url + "/index.php/ajax/suggest",
                              params={"wd": key, "mid": 1, "limit": 500},
                              headers=self.headers, timeout=12, verify=False)
            data = r.json()
            if int(data.get("code", 0)) != 1:
                return None
            items = data.get("list") or []
        except Exception:
            return None
        videos = []
        for it in items:
            try:
                vid = int(it.get("id"))
            except Exception:
                continue
            url = "{}/voddetail/{}.html".format(self.site_url, vid)
            name = self._clean(str(it.get("name") or ""))
            if not name:
                continue
            pic = str(it.get("pic") or "")
            videos.append({
                "vod_id": url, "vod_name": name,
                "vod_pic": pic, "vod_remarks": ""
            })
            self._cache[url] = {
                "vod_name": name, "vod_pic": pic, "vod_remarks": ""
            }
        self._search_cache[key] = videos
        return videos

    def _vodtype_all(self, tid):
        res = self.fetch(self.site_url + "/vodtype/{}.html".format(tid))
        if not res or not res.ok:
            return []
        return self._parse_list(res.text, 1)

    def _local_page(self, videos, pg):
        start = (pg - 1) * self.page_size
        pagecount = max(1, (len(videos) + self.page_size - 1) // self.page_size)
        return {
            "list": videos[start:start + self.page_size], "page": pg,
            "pagecount": pagecount,
            "limit": self.page_size, "total": len(videos)
        }

    def _empty_hint(self, why, pg):
        return {
            "list": [{
                "vod_id": self.site_url + "/",
                "vod_name": "暂无数据，请稍后重试",
                "vod_pic": "",
                "vod_remarks": "检查网络或插件扩展参数"
            }],
            "page": pg, "pagecount": pg, "limit": self.page_size, "total": 1
        }

    def homeContent(self, filter):
        cate_list = [
            {"type_name": "首页推荐", "type_id": "index"},
            {"type_name": "电影",   "type_id": "1"},
            {"type_name": "电视剧", "type_id": "2"},
            {"type_name": "动漫",   "type_id": "4"},
            {"type_name": "综艺",   "type_id": "3"},
            {"type_name": "短剧",   "type_id": "26"},
            {"type_name": "AI漫剧", "type_id": "33"},
            {"type_name": "少儿",   "type_id": "25"},
        ]
        year_v = [{"n": "全部", "v": ""}] + [
            {"n": str(y), "v": str(y)} for y in range(2026, 2009, -1)
        ]
        area_v = [{"n": "全部", "v": ""}] + [
            {"n": n, "v": n} for n in
            ["中国大陆", "中国香港", "中国台湾", "美国", "日本", "韩国", "英国", "泰国", "印度", "法国"]
        ]
        cls_v = [{"n": "全部", "v": ""}] + [
            {"n": n, "v": n} for n in
            ["剧情", "喜剧", "动作", "爱情", "科幻", "悬疑", "惊悚", "恐怖", "犯罪",
             "古装", "武侠", "奇幻", "动画", "纪录片", "历史", "战争", "家庭"]
        ]
        one = [
            {"key": "class", "name": "类型", "value": cls_v},
            {"key": "area",  "name": "地区", "value": area_v},
            {"key": "year",  "name": "年份", "value": year_v},
        ]
        filters = {c["type_id"]: one for c in cate_list if c["type_id"] != "index"}
        return {"class": cate_list, "filters": filters}

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg) if str(pg).isdigit() else 1
        self._verify_failed = False

        ext = self._parse_extend(extend)
        has_filter = any(str(ext.get(k) or "").strip()
                         for k in ("area", "class", "year"))

        if str(tid) == "index":
            res = self.fetch(self.site_url + "/")
            videos = self._parse_list(res.text, pg) if res and res.ok else []
            return self._local_page(videos, pg)

        if self._fast_mode and not has_filter:
            self._fallback_used = True
            return self._local_page(self._vodtype_all(tid), pg)

        segs = [""] * 12
        segs[0] = "/vodshow/{}".format(tid)
        segs[1] = quote(str(ext.get("area") or ""))
        segs[3] = quote(str(ext.get("class") or ""))
        segs[8] = str(pg) if pg > 1 else ""
        segs[11] = str(ext.get("year") or "")
        list_url = self.site_url + "-".join(segs) + ".html"

        res = self.fetch(list_url)
        page = res.text if res and res.ok else ""
        videos = self._parse_list(page, pg)
        if not videos and self._verify_failed:
            self._fallback_used = True
            return self._local_page(self._vodtype_all(tid), pg)
        return {
            "list": videos, "page": pg,
            "pagecount": self._pagecount(page, pg, len(videos)),
            "limit": self.page_size, "total": self.total
        }

    def detailContent(self, ids):
        vod_id = ids[0] if ids else ""
        if not vod_id:
            return {"list": [{"vod_name": "视频ID为空"}]}
        raw_id = str(vod_id)
        pm = re.search(r'/vodplay/(\d+)-\d+-\d+\.html', raw_id)
        if pm:
            detail_url = "{}/voddetail/{}.html".format(self.site_url, pm.group(1))
        elif raw_id.isdigit():
            detail_url = "{}/voddetail/{}.html".format(self.site_url, raw_id)
        elif raw_id.startswith("http"):
            detail_url = raw_id
        else:
            detail_url = self._abs(raw_id)
        cached = self._cache.get(detail_url, {})

        res = self.fetch(detail_url)
        if not res or not res.ok:
            return {"list": [{"vod_id": detail_url, "vod_name": "视频详情解析失败"}]}
        page = res.text

        def meta(name):
            m = re.search(r'<meta\s+name=["\']' + name + r'["\']\s+content=["\']([^"\']*)', page, re.I)
            return self._clean(m.group(1)) if m else ""

        desc = meta("description")
        content = re.sub(r'^.*?剧情介绍[：:]\s*', "", desc).strip()
        kw = meta("keywords")
        title_m = re.search(r"<title>(.*?)</title>", page, re.S)
        name = (cached.get("vod_name") or
                (kw.split(",")[0] if kw else "") or
                (self._clean(title_m.group(1)).split("电影")[0] if title_m else "") or "未知影片")
        pic = cached.get("vod_pic") or ""
        if not pic:
            pm = re.search(r'<img[^>]+alt=["\'][^"\']*封面图["\'][^>]+data-src=["\']([^"\']+)', page)
            if pm:
                pic = self._abs(pm.group(1))

        def field(k):
            m = re.search(r'<li[^>]*>.*?' + k + r'\s*[：:]\s*(?:</em>)?(.*?)</li>', page, re.S)
            return self._clean(m.group(1)) if m else ""

        real_name = field("片名")
        if real_name:
            name = real_name
        def cut(v):
            return re.split(r"[，,]|演员[:：]|描述[:：]", v)[0].strip()

        actor = cut(field("主演"))
        director = cut(field("导演"))
        area = cut(field("地区"))
        cls = field("类型")
        year_f = field("年份")
        year = re.search(r"(19|20)\d{2}", year_f)
        year = year.group(0) if year else ""
        cur = re.search(r'<a[^>]+class="[^"]*current[^"]*"[^>]*>(.*?)</a>', page, re.S)
        type_name = self._clean(cur.group(1)) if cur else cls

        tab_m = re.search(r'<div class="anthology-tab.*?</div>\s*</div>', page, re.S)
        tabs, tab_forms = [], []
        form_names = {
            "NBY": "NB源", "dyttm3u8": "TT源", "ffm3u8": "FF源",
            "lzm3u8": "LZ源", "bfzym3u8": "BF源", "1080zyk": "YZ源",
            "modum3u8": "MD源", "mtm3u8": "MT源"
        }
        if tab_m:
            seg = tab_m.group(0)
            for attrs, t in re.findall(
                    r'<a\b(?=[^>]*\bclass="[^"]*swiper-slide[^"]*")([^>]*)>(.*?)</a>',
                    seg, re.S):
                fm = re.search(r'data-form="([^"]+)"', attrs)
                tab_forms.append(fm.group(1) if fm else "")
                clean = self._clean(t)
                clean = re.sub(r"[\ue000-\uf8ff]", "", clean)
                clean = re.sub(r"\s*\d+\s*$", "", clean).strip()
                tabs.append(clean or (form_names.get(fm.group(1), fm.group(1)) if fm else "默认线路"))
            if not tabs:
                tab_forms = re.findall(r'<a data-form="([^"]+)"', seg)
                tabs = [form_names.get(f, f) for f in tab_forms]
        tail = page[tab_m.end():] if tab_m else page
        blocks = re.split(r'<div class="anthology-list-box[^"]*">', tail)[1:]
        if not tabs:
            tabs = ["线路{}".format(i + 1) for i in range(len(blocks))] or ["默认线路"]

        froms, urls, blocked = [], [], []
        for i, block in enumerate(blocks):
            ul = re.search(r'<ul class="anthology-list-play.*?</ul>', block, re.S)
            scope = ul.group(0) if ul else block
            eps = []
            for a in re.finditer(r'<a[^>]+href=["\'](/vodplay/[^"\']+)["\'][^>]*>(.*?)</a>', scope, re.S):
                ep_name = self._clean(a.group(2)).replace("$", "").replace("#", "").strip() or str(len(eps) + 1)
                eps.append("{}${}".format(ep_name, self._abs(a.group(1))))
            if eps:
                fname = tabs[i] if i < len(tabs) else "线路{}".format(i + 1)
                fform = tab_forms[i] if i < len(tab_forms) else ""
                entry = (fname, "#".join(eps))
                if self._from_blocked(fname, fform):
                    blocked.append(entry)
                    continue
                froms.append(fname)
                urls.append(entry[1])
        if not urls and blocked:
            froms = [x[0] for x in blocked]
            urls = [x[1] for x in blocked]

        if not urls:
            return {"list": [{"vod_id": detail_url, "vod_name": name,
                              "vod_content": content, "vod_pic": pic}]}

        info = {
            "vod_id": detail_url,
            "vod_name": name,
            "vod_pic": pic,
            "vod_remarks": cached.get("vod_remarks") or "",
            "type_name": type_name,
            "vod_year": year,
            "vod_area": area,
            "vod_actor": actor,
            "vod_director": director,
            "vod_content": content,
            "vod_play_from": "$$$".join(froms),
            "vod_play_url": "$$$".join(urls)
        }
        return {"list": [info]}

    def searchContent(self, key, quick, pg=1):
        pg = int(pg) if str(pg).isdigit() else 1
        self._verify_failed = False
        key = (key or "").strip()
        if not key:
            return {"list": [], "page": pg, "pagecount": pg,
                    "limit": self.page_size, "total": 0}

        all_videos = self._api_search(key)
        if all_videos is not None:
            self._fallback_used = True
            return self._local_page(all_videos, pg)

        if pg <= 1:
            search_url = "{}/vodsearch/-------------.html?wd={}".format(
                self.site_url, quote(key))
        else:
            segs = [""] * 14
            segs[0] = "/vodsearch/{}".format(quote(key))
            segs[10] = str(pg)
            search_url = self.site_url + "-".join(segs) + ".html"

        self._throttle_search()
        page, videos = "", []
        for attempt in range(3):
            res = self.fetch(search_url)
            page = res.text if res and res.ok else ""
            videos = self._parse_list(page, pg)
            if videos or self._verify_failed or self._is_captcha_page(page):
                break
            if attempt < 2:
                time.sleep(min(self._search_gap + attempt * 2, 10))
                self._last_search = time.time()
        if not videos and self._verify_failed:
            return self._empty_hint("搜索服务暂不可用", pg)
        return {
            "list": videos, "page": pg,
            "pagecount": self._pagecount(page, pg, len(videos)),
            "limit": self.page_size,
            "total": len(videos) if len(videos) < self.page_size else self.total
        }

    def _decrypt_play_url(self, raw, play_page):
        base = self.site_url + "/static/player/art.php"
        hdr = dict(self.headers, **{
            "Referer": play_page,
            "X-Requested-With": "XMLHttpRequest"
        })
        try:
            r1 = self.sess.get(base + "?get_signed_url=1&url=" + quote(raw, safe=""),
                               headers=hdr, timeout=12, verify=False)
            su = (r1.json() or {}).get("signed_url") or ""
            if not su:
                return ""
            u2 = (base + su) if su.startswith("?") else (
                su if su.startswith("http") else base + "/" + su.lstrip("/"))
            r2 = self.sess.get(u2, headers=hdr, timeout=12, verify=False)
            data = r2.json() or {}
            return (data.get("jmurl") or data.get("url") or "").replace("\\/", "/")
        except Exception:
            return ""

    def _fallback_play(self, play_page):
        m = re.search(r'/vodplay/(\d+)-(\d+)-(\d+)\.html', play_page)
        if not m:
            return ""
        vid, sid, nid = m.groups()
        try:
            d = self.fetch("{}/voddetail/{}.html".format(self.site_url, vid))
            if not d or not d.ok:
                return ""
            links = dict.fromkeys(
                re.findall(r'/vodplay/{}-(\d+)-(\d+)\.html'.format(vid), d.text))
            cand = [(0 if n2 == nid else 1, int(s2), int(n2), s2, n2)
                    for s2, n2 in links if not (s2 == sid and n2 == nid)]
            cand.sort()
            tried = 0
            for _, _, _, s2, n2 in cand[:10]:
                alt = "{}/vodplay/{}-{}-{}.html".format(self.site_url, vid, s2, n2)
                r = self.fetch(alt)
                tried += 1
                if not r or not r.ok:
                    continue
                mm = re.search(r'var\s+player_data\s*=\s*', r.text)
                if not mm:
                    continue
                try:
                    obj, _ = json.JSONDecoder().raw_decode(r.text[mm.end():])
                    if self._from_blocked("", obj.get("from") or ""):
                        continue
                    u = (obj.get("url") or "").replace("\\/", "/")
                except Exception:
                    continue
                if u.startswith(("http://", "https://")):
                    return u
                if u:
                    u = self._decrypt_play_url(u, alt)
                    if u.startswith(("http://", "https://")):
                        return u
                if tried >= 6:
                    break
        except Exception:
            pass
        return ""

    def playerContent(self, flag, id, vipFlags):
        play_page = id.split("$")[-1] if "$" in str(id) else id
        play_page = self._abs(str(play_page))
        if not play_page:
            return {"parse": 0, "url": "", "header": self.headers}

        res = self.fetch(play_page)
        if not res or not res.ok:
            return {"parse": 0, "url": "", "header": self.headers}
        page = res.text

        m = re.search(r'var\s+player_data\s*=\s*', page)
        final_url, play_from = "", ""
        if m:
            try:
                obj, _ = json.JSONDecoder().raw_decode(page[m.end():])
                final_url = (obj.get("url") or "").replace("\\/", "/")
                play_from = obj.get("from") or ""
                enc = obj.get("encrypt")
                if enc in (1, "1") and final_url:
                    final_url = unquote(final_url)
                elif enc in (2, "2") and final_url:
                    try:
                        import base64
                        final_url = unquote(base64.b64decode(final_url).decode("utf-8", "ignore"))
                    except Exception:
                        pass
            except Exception:
                pass

        if self._from_blocked("", play_from):
            final_url = self._fallback_play(play_page)
        else:
            if final_url and not final_url.startswith(("http://", "https://")):
                final_url = self._decrypt_play_url(final_url, play_page)
            if not final_url:
                mm = re.search(r'["\'](https?[^"\']*?\.m3u8[^"\']*)["\']', page)
                if mm:
                    final_url = mm.group(1).replace("\\/", "/")
            if not final_url.startswith(("http://", "https://")):
                final_url = self._fallback_play(play_page) or final_url

        play_header = {
            "User-Agent": self.headers["User-Agent"],
            "Referer": self.site_url + "/",
            "Accept-Language": "zh-CN,zh;q=0.9"
        }
        return {"parse": 0, "url": final_url, "header": play_header}

def _gen_session():
    try:
        import ddddocr
    except ImportError:
        print("缺少依赖，请先执行：pip3 install requests ddddocr")
        return 1
    sess = requests.Session()
    ocr = ddddocr.DdddOcr(show_ad=False)
    base = "https://mtyy7.com"
    hdr = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": base + "/",
        "Accept-Language": "zh-CN,zh;q=0.9"
    }

    def solve(vtype, page):
        sess.get(page, headers=hdr, timeout=15, verify=False)
        for _ in range(8):
            img = sess.get(base + "/verify/index.html", headers=hdr,
                           timeout=15, verify=False).content
            code = re.sub(r"\D", "", ocr.classification(img))[:4]
            if len(code) != 4:
                continue
            r = sess.post(base + "/index.php/ajax/verify_check?type=" + vtype + "&verify=" + code,
                          headers=dict(hdr, **{"X-Requested-With": "XMLHttpRequest"}),
                          timeout=15, verify=False)
            try:
                if int(json.loads(r.text).get("code", 0)) == 1:
                    return True
            except Exception:
                pass
        return False

    checks = [
        ("search", base + "/vodsearch/-------------.html?wd=%E5%87%A1%E4%BA%BA"),
        ("show", base + "/vodshow/1-----------.html"),
    ]
    for vtype, page in checks:
        ok = solve(vtype, page)
        print("验证码 %s : %s" % (vtype, "通过" if ok else "失败"))
        if not ok:
            print("识别失败，可重跑一次（验证码有随机性）")
            return 1
    for vtype, page in checks:
        r = sess.get(page, headers=hdr, timeout=15, verify=False)
        print("复核 %s : %s" % (vtype, "正常" if "请输入验证码" not in r.text else "仍被拦截"))
    print("\n将下面这行填入插件扩展参数：")
    print(json.dumps({"phpsessid": sess.cookies.get("PHPSESSID")}, ensure_ascii=False))
    return 0

def _self_test():
    try:
        import ddddocr
        has_ocr = True
    except ImportError:
        has_ocr = False
    print("OCR 可用: %s（缺失时跳过「完整通道」增强用例）" % has_ocr)
    results, snap = [], {}
    state = {"s": None}

    def check(name, fn):
        t0 = time.time()
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, "%s: %s" % (type(e).__name__, str(e)[:150])
        cost = time.time() - t0
        results.append((name, bool(ok), detail, cost))
        print("[%s] %-22s %5.1fs  %s" % ("PASS" if ok else "FAIL", name, cost, detail))
        sys.stdout.flush()

    def need(cond, msg):
        if not cond:
            raise AssertionError(msg)

    def valid_item(it):
        return (isinstance(it, dict)
                and str(it.get("vod_id", "")).startswith("http")
                and bool(it.get("vod_name"))
                and str(it.get("vod_pic", "")).startswith("http"))

    def c_env():
        saved = sys.modules.get("ddddocr")
        sys.modules["ddddocr"] = None
        try:
            s = Spider()
            s.init("")
        finally:
            if saved is None:
                sys.modules.pop("ddddocr", None)
            else:
                sys.modules["ddddocr"] = saved
        need(s.getName() == "麦田影院", "插件名称异常")
        need(s._ocr is None and s._fast_mode, "未进入免验证码快速模式")
        state["s"] = s
        return True, "Python %s / 手机端零配置模式 / %s" % (
            sys.version.split()[0], s.site_url)

    def c_home():
        h = state["s"].homeContent(True)
        cls = h.get("class", [])
        need(len(cls) >= 5, "分类过少")
        need(len(h.get("filters", {})) >= 5, "筛选配置缺失")
        return True, "%d 个分类 / %d 组筛选" % (len(cls), len(h["filters"]))

    def c_cat1():
        r = state["s"].categoryContent("1", 1, True, {})
        v = r.get("list", [])
        need(len(v) >= 20, "第1页条数异常 %d" % len(v))
        need(all(valid_item(x) for x in v[:5]), "卡片字段不完整")
        need(r["pagecount"] > 1, "未识别分页")
        snap["cat1"] = v[0]["vod_id"]
        return True, "%d 条 / %s 页 / 首条 %s" % (len(v), r["pagecount"], v[0]["vod_name"])

    def c_cat2():
        r = state["s"].categoryContent("1", 2, True, {})
        v = r.get("list", [])
        need(len(v) >= 20, "第2页条数异常 %d" % len(v))
        need(v[0]["vod_id"] != snap.get("cat1"), "分页未生效")
        return True, "%d 条 / 首条 %s" % (len(v), v[0]["vod_name"])

    def c_filter():
        out = []
        for ext in ({"year": "2020"}, {"class": "科幻"}, {"area": "日本"},
                    {"class": "动作", "year": "2025"}):
            v = state["s"].categoryContent("1", 1, True, ext).get("list", [])
            need(len(v) > 0, "筛选 %s 无结果" % ext)
            out.append("%s=%d" % (list(ext.keys())[0], len(v)))
        return True, " / ".join(out)

    def c_index():
        v = state["s"].categoryContent("index", 1, True, {}).get("list", [])
        need(len(v) >= 20, "首页推荐条数异常 %d" % len(v))
        return True, "%d 条 / 首条 %s" % (len(v), v[0]["vod_name"])

    def c_search1():
        r = state["s"].searchContent("凡人", False, 1)
        v = r.get("list", [])
        need(len(v) >= 5, "搜索第1页条数异常 %d" % len(v))
        need(not state["s"]._verify_failed, "搜索触发了验证码")
        need("search" not in state["s"]._verified, "搜索不应走验证码通道")
        snap["search1"] = v[0]["vod_id"]
        return True, "%d 条 / JSON 免验证码通道 / 首条 %s" % (len(v), v[0]["vod_name"])

    def c_search2():
        r = state["s"].searchContent("凡人", False, 2)
        v = r.get("list", [])
        need(len(v) >= 5, "搜索第2页条数异常 %d" % len(v))
        need(v[0]["vod_id"] != snap.get("search1"), "搜索翻页未生效")
        return True, "%d 条 / 首条 %s" % (len(v), v[0]["vod_name"])

    def c_burst():
        out = []
        for kw in ("斗破苍穹", "遮天"):
            v = state["s"].searchContent(kw, False, 1).get("list", [])
            need(len(v) > 0, "连续搜索「%s」被限流" % kw)
            out.append("%s=%d" % (kw, len(v)))
        return True, "连续搜索均有结果：" + " / ".join(out)

    def c_detail_movie():
        d = state["s"].detailContent(["https://mtyy7.com/voddetail/230035.html"])["list"][0]
        need(d.get("vod_name") == "我的妈耶", "片名错误 %s" % d.get("vod_name"))
        need(bool(d.get("vod_year")) and bool(d.get("type_name")), "年份/分类缺失")
        need(bool(d.get("vod_content")), "简介缺失")
        need(bool(d.get("vod_play_from")) and bool(d.get("vod_play_url")), "播放信息缺失")
        return True, "%s / %s / %s / %s" % (
            d["vod_name"], d["vod_year"], d["type_name"], d["vod_play_from"])

    def c_detail_multi():
        d = state["s"].detailContent(["https://mtyy7.com/voddetail/45867.html"])["list"][0]
        froms = d.get("vod_play_from", "").split("$$$")
        urls = d.get("vod_play_url", "").split("$$$")
        need(len(froms) >= 2, "多线路未解析 %s" % froms)
        need(len(urls) == len(froms), "线路与播放组数量不一致")
        counts = [x.count("#") + 1 for x in urls]
        need(max(counts) >= 50, "集数异常 %s" % counts)
        need(all("/vodplay/" in x for x in urls), "播放地址格式错误")
        snap["p1"] = urls[0].split("#")[0]
        snap["p2"] = urls[-1].split("#")[0] if len(urls) > 1 else ""
        snap["froms"], snap["counts"] = froms, counts
        return True, "%d 条线路 / 集数 %s" % (len(froms), counts)

    def c_player():
        p = state["s"].playerContent("", snap["p1"], [])
        u = str(p.get("url") or "")
        need(u.startswith("http") and ".m3u8" in u, "播放地址无效 %s" % u[:100])
        need(p.get("parse") == 0, "parse 异常")
        need("User-Agent" in p.get("header", {}), "缺少播放请求头")
        snap["m3u8"] = u
        try:
            rr = requests.get(u, headers=p["header"], timeout=6, verify=False)
            extra = "CDN %s / %d bytes" % (rr.status_code, len(rr.content))
        except Exception as e:
            extra = "CDN 直连未测通（%s），不影响解析" % type(e).__name__
        return True, "%s | %s" % (u[:80], extra)

    def c_player2():
        if not snap.get("p2"):
            return True, "跳过（仅一条线路）"
        u = str(state["s"].playerContent("", snap["p2"], []).get("url") or "")
        need(u.startswith("http"), "第二线路地址无效")
        need(u != snap.get("m3u8"), "不同线路返回同一地址")
        return True, u[:80]

    def c_session():
        s = state["s"]
        need(not s._verified, "免验证码模式不应触发任何验证码")
        need(s._fallback_used, "未使用免验证码通道")
        for u in ("/", "/vodtype/1.html",
                  "/index.php/ajax/suggest?wd=a&mid=1&limit=5"):
            r = s.sess.get(s.site_url + u, headers=s.headers,
                           timeout=12, verify=False)
            need(not s._is_captcha_page(r.text), "路径需要验证码: %s" % u)
        return True, "首页 / 分类 / 搜索JSON 三条路径均无需验证码"

    def c_phone_filter():
        s = state["s"]
        for ext in ({"year": "2020"}, {"class": "科幻"}, {"area": "日本"}):
            v = s.categoryContent("1", 1, True, ext).get("list", [])
            need(len(v) >= 20, "筛选 %s 降级后无数据" % ext)
            need(all("验证码未通过" not in str(x.get("vod_name", ""))
                     for x in v), "筛选 %s 出现验证码占位卡" % ext)
        return True, "3 组筛选均自动降级且无占位卡"

    def c_phone_chain():
        s = state["s"]
        q = s.searchContent("庆余年", False, 1)
        need(len(q["list"]) > 0, "搜索无结果")
        ok_names, total = [], 0
        for it in q["list"][:4]:
            d = s.detailContent([it["vod_id"]])["list"][0]
            need(bool(d.get("vod_name")) and bool(d.get("vod_play_url")),
                 "搜索结果的详情页不可用：%s" % it["vod_name"])
            groups = d["vod_play_url"].split("$$$")
            first_ep = groups[0].split("#")[0] if groups and groups[0] else ""
            total += 1
            p = s.playerContent("", first_ep, []) if first_ep else {}
            if str(p.get("url", "")).startswith("http"):
                ok_names.append(d.get("vod_name"))
        need(ok_names, "前 %d 部均无法取得播放地址" % total)
        need(not s._verify_failed, "链路中触发了验证码")
        return True, "搜索→详情→播放：%d/%d 部可播（%s）" % (
            len(ok_names), total, "、".join(x[:10] for x in ok_names[:2]))

    def c_lines():
        s = state["s"]
        d = s.detailContent(["210495"])["list"][0]
        froms = d.get("vod_play_from", "").split("$$$")
        urls = d.get("vod_play_url", "").split("$$$")
        r = s.sess.get(s.site_url + "/vodplay/210495-2-1.html",
                       headers=s.headers, timeout=12, verify=False)
        seg = re.search(r'<div class="anthology-tab.*?</div>\s*</div>', r.text, re.S)
        seg = seg.group(0) if seg else ""
        site = []
        for t in re.findall(
                r'<a\b(?=[^>]*\bclass="[^"]*swiper-slide[^"]*")[^>]*>(.*?)</a>',
                seg, re.S):
            c = re.sub(r"[\ue000-\uf8ff]", "", s._clean(t))
            site.append(re.sub(r"\s*\d+\s*$", "", c).strip())
        need(bool(site), "未能读取网站线路")
        need(froms == site, "线路与网站不一致：插件=%s 网站=%s" % (froms, site))
        need("TT源" in froms, "TT源 丢失")
        need(len(urls) == len(froms), "线路与播放组数量不一致")
        d2 = s.detailContent(["https://mtyy7.com/vodplay/210495-2-1.html"])["list"][0]
        need(d2.get("vod_play_from", "").split("$$$") == froms,
             "播放页入口线路丢失")
        ok = 0
        for f, u in zip(froms, urls):
            p = s.playerContent(f, u.split("#")[0], [])
            if str(p.get("url", "")).startswith("http"):
                ok += 1
        need(ok == len(froms), "可播线路仅 %d/%d" % (ok, len(froms)))
        return True, "线路 %s / 全部可播 / 播放页入口一致" % "/".join(froms)

    def c_block_mt():
        s = state["s"]
        d = s.detailContent(["230035"])["list"][0]
        froms = d.get("vod_play_from", "").split("$$$")
        urls = d.get("vod_play_url", "").split("$$$")
        need(bool(froms) and froms != [""], "线路为空")
        need(all("mt源" not in f.lower() and "mtm3u8" not in f.lower()
                 for f in froms), "MT源 未被剔除：%s" % froms)
        need(len(urls) == len(froms), "线路与播放组数量不一致")
        p = s.playerContent(
            "", "第1集$https://mtyy7.com/vodplay/230035-4-1.html", [])
        u = str(p.get("url") or "")
        need(u.startswith("http"), "MT源 换线路兜底失败：%s" % u[:90])
        need("vodcnd03.myqqdd.com" not in u,
             "仍返回 MT源(广告)地址：%s" % u[:90])
        k = Spider()
        k.init(json.dumps({"keep_from": "MT源,mtm3u8"}))
        dk = k.detailContent(["230035"])["list"][0]
        need("MT源" in dk.get("vod_play_from", ""), "keep_from 未恢复 MT源")
        return True, "线路 %s / MT源已剔除 / 兜底 %s" % (
            "/".join(froms), u[:52])

    def c_full_channel():
        if not has_ocr:
            return True, "跳过（本机无 ddddocr，完整通道需 OCR/phpsessid）"
        f = Spider()
        f.init("")
        need(not f._fast_mode, "未启用完整通道")
        r = f.categoryContent("1", 1, True, {"year": "2020", "class": "动作"})
        need(len(r["list"]) > 0, "完整通道筛选无结果")
        need("show" in f._verified, "show 验证码未自动通过")
        return True, "完整通道筛选 %d 条 / 已放行 %s" % (
            len(r["list"]), sorted(f._verified))

    def c_no_ocr():
        saved = sys.modules.get("ddddocr")
        sys.modules["ddddocr"] = None
        try:
            s2 = Spider()
            s2.init(json.dumps({"phpsessid": "expired_session_for_test",
                                "search_gap": "8"}))
            need(s2._ocr is None, "未能模拟无 OCR 环境")
            c = s2.categoryContent("2", 1, True, {})
            need(len(c["list"]) >= 20, "会话失效时分类不可用")
            need(all("验证码未通过" not in str(x.get("vod_name", ""))
                     for x in c["list"]), "出现验证码占位卡")
            q = s2.searchContent("庆余年", False, 1)
            need(len(q["list"]) > 0, "会话失效时搜索不可用")
            return True, "失效会话自动降级：分类 %d 条 / 搜索 %d 条" % (
                len(c["list"]), len(q["list"]))
        finally:
            if saved is None:
                sys.modules.pop("ddddocr", None)
            else:
                sys.modules["ddddocr"] = saved

    cases = [
        ("环境与插件加载", c_env),
        ("首页分类", c_home),
        ("分类列表 第1页", c_cat1),
        ("分类列表 第2页分页", c_cat2),
        ("分类筛选 自动降级", c_filter),
        ("首页推荐 index", c_index),
        ("搜索 第1页", c_search1),
        ("搜索 第2页分页", c_search2),
        ("连续搜索 无速率限制", c_burst),
        ("详情 单线路电影", c_detail_movie),
        ("详情 多线路多集剧", c_detail_multi),
        ("播放解析 m3u8", c_player),
        ("播放解析 第二线路", c_player2),
        ("免验证码路径复核", c_session),
        ("手机端筛选不出验卡", c_phone_filter),
        ("手机端 搜索→详情→播放", c_phone_chain),
        ("线路与网站一致 TT源", c_lines),
        ("MT源广告线路剔除", c_block_mt),
        ("完整通道 验证码放行", c_full_channel),
        ("失效会话自动降级", c_no_ocr),
    ]

    print("=" * 76)
    print("麦田影院插件 | 本地全自动端到端验证 | %s" %
          time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 76)
    t0 = time.time()
    for name, fn in cases:
        check(name, fn)
    passed = sum(1 for _, ok, _, _ in results if ok)
    failed = len(results) - passed
    print("-" * 76)
    print("多集剧：%d 条线路 %s | m3u8：%s" % (
        len(snap.get("froms", [])), snap.get("counts", []), snap.get("m3u8", "")[:70]))
    print("汇总：%d 项，通过 %d，失败 %d，耗时 %.1fs" % (
        len(results), passed, failed, time.time() - t0))
    print("=" * 76)
    return 1 if failed else 0

if __name__ == "__main__":
    _cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "test"
    sys.exit(_gen_session() if _cmd == "session" else _self_test())
