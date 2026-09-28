# -*- coding: utf-8 -*-
import sys
import re
import json
import time
from urllib.parse import quote

sys.path.append('..')
try:
    from base.spider import Spider
except ImportError:
    class Spider:
        def fetch(self, url, headers=None, **kw):
            import requests as rq
            kw.pop('timeout', None)
            r = rq.get(url, headers=headers, timeout=15, **kw)
            r.encoding = 'utf-8'
            return r

HOST = "https://api.520vd.com:3001"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# 源站已下线"狂飙短剧"(kuangb/87wyhry4p)模块,分类接口与模块列表均无此入口,已移除
CATEGORIES = {
    "movie": "电影",
    "s": "短剧",
    "d": "动画",
    "zongyi1": "综艺",
    "lianxuju": "连续剧",
    "home-hot": "热门推荐",
}

ALIAS = {
    "uxx999gfo": "movie",
    "9mcnm4msx": "s",
    "yrw7an9tc": "d",
    "zy": "zongyi1",
    "7d4hmw2rp": "lianxuju",
}

HOME_MODULES = ["home-hot"]

PAGE_SIZE = 60

class Spider(Spider):
    def __init__(self):
        self._cache = {}

    def init(self, extend=""):
        global HOST
        if extend and extend.startswith("http"):
            HOST = extend.rstrip("/")

    def homeContent(self, filter=False):
        r = {"class": [], "list": []}
        for k, v in CATEGORIES.items():
            r["class"].append({"type_id": k, "type_name": v})
        return r

    def homeVideoContent(self):
        try:
            seen, items = set(), []
            for slug in HOME_MODULES:
                try:
                    data = self._fetch(f"/api/v1/public/content/modules/{slug}/vods?page=1&page_size={PAGE_SIZE}&locale=zh")
                except:
                    continue
                for v in (data.get("data") or {}).get("items") or []:
                    vid = v.get("slug") or v.get("id") or ""
                    if vid and vid not in seen:
                        seen.add(vid)
                        items.append(self._item(v))
            return {"list": items}
        except:
            try:
                data = self._cache.get("/api/v1/public/content/modules/home-hot/vods?page=1&page_size=60&locale=zh")
                if data and data[0]:
                    return {"list": self._items((data[0].get("data") or {}).get("items") or [])}
            except:
                pass
            return {"list": []}

    def categoryContent(self, tid, pg=1, filter=False, extend=""):
        pn = 1
        try:
            pn = max(int(str(pg)), 1)
        except:
            pass
        slug = str(tid)
        slug = ALIAS.get(slug, slug)
        if slug not in CATEGORIES:
            slug = "movie"
        try:
            if slug == "home-hot":
                data = self._fetch(f"/api/v1/public/content/modules/{slug}/vods?page={pn}&page_size={PAGE_SIZE}&locale=zh", ttl=120, retry=0)
            else:
                try:
                    data = self._fetch(f"/api/v1/public/categories/{slug}/vods?page={pn}&page_size={PAGE_SIZE}&locale=zh", ttl=120, retry=0)
                    if data.get("code") != "SUCCESS":
                        raise Exception("fallback")
                except:
                    data = self._fetch(f"/api/v1/public/content/modules/{slug}/vods?page={pn}&page_size={PAGE_SIZE}&locale=zh", ttl=120, retry=0)
            dd = data.get("data") or {}
            items = self._items(dd.get("items") or [])
            pag = dd.get("pagination") or {}
            total = int(pag.get("total") or len(items))
            pc = max(total // PAGE_SIZE + (1 if total % PAGE_SIZE else 0), 1)
            if not items and pn > pc:
                pn = pc
            return {
                "page": pn,
                "pagecount": pc,
                "limit": min(PAGE_SIZE, max(total, 1)),
                "total": total,
                "list": items
            }
        except:
            try:
                data = self._fetch("/api/v1/public/content/modules/home-hot/vods?page=1&page_size=24&locale=zh", ttl=120, retry=0)
                items = self._items((data.get("data") or {}).get("items") or [])
                if items:
                    return {"page": pn, "pagecount": 1, "limit": 24, "total": len(items), "list": items}
            except:
                pass
            return {"page": pn, "pagecount": 1, "limit": 24, "total": 0, "list": []}

    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, list) and ids else str(ids or "")
        if not vid:
            return {"list": []}
        try:
            data = self._fetch(f"/api/v1/public/vods/{quote(vid)}?locale=zh", ttl=600)
        except:
            return {"list": []}
        v = data.get("data") or {}
        all_eps = v.get("episodes") or []
        eps = [e for e in all_eps if not e.get("locked")]
        if not eps:
            eps = all_eps
        d = {
            "vod_id": v.get("slug") or vid,
            "vod_name": v.get("title") or "",
            "vod_pic": v.get("poster_url") or "",
            "vod_year": str(v.get("release_year") or ""),
            "vod_area": "",
            "vod_class": (v.get("category") or {}).get("name") or "",
            "vod_director": "",
            "vod_actor": "",
            "vod_content": v.get("description") or "",
            "vod_remarks": f"可播{len(eps)}/{len(all_eps)}集" if all_eps else "",
            "vod_play_from": "520ju",
            "vod_play_url": "#".join([f'{e.get("title") or f"第{i+1}集"}{"🔒" if e.get("locked") else ""}${v.get("slug")}|{e.get("id")}|{e.get("episode_no") or i+1}' for i, e in enumerate(eps)])
        }
        return {"list": [d]}

    def searchContent(self, key, quick=False, pg="1"):
        try:
            pn = max(int(str(pg)), 1)
        except:
            pn = 1
        try:
            data = self._fetch(f"/api/v1/public/search?q={quote(key)}&page={pn}&page_size=24&locale=zh")
            return {"list": self._items((data.get("data") or {}).get("items") or []), "page": pn}
        except:
            return {"list": []}

    def playerContent(self, flag, id, vipFlags=None):
        try:
            slug, ep, epno = str(id).split("|", 2)
        except:
            try:
                slug, ep = str(id).split("|", 1)
                epno = ""
            except:
                return {"url": ""}
        url = self._try_play(slug, ep)
        if url:
            return {"parse": 0, "url": url}
        if epno:
            try:
                data = self._fetch(f"/api/v1/public/vods/{quote(slug)}?locale=zh", ttl=600)
                title = (data.get("data") or {}).get("title") or ""
                if title:
                    sr = self._fetch(f"/api/v1/public/search?q={quote(title)}&page=1&page_size=10&locale=zh", ttl=300, retry=0)
                    for item in (sr.get("data") or {}).get("items") or []:
                        if item.get("slug") == slug:
                            continue
                        try:
                            od = self._fetch(f"/api/v1/public/vods/{quote(item.get('slug') or '')}?locale=zh", ttl=600, retry=0)
                        except:
                            continue
                        for e in (od.get("data") or {}).get("episodes") or []:
                            if not e.get("locked") and str(e.get("episode_no")) == str(epno):
                                url = self._try_play(item.get("slug"), e.get("id"))
                                if url:
                                    return {"parse": 0, "url": url}
            except:
                pass
        return {"url": ""}

    def _try_play(self, slug, ep):
        try:
            data = self._fetch(f"/api/v1/public/play/{quote(slug)}/{quote(ep)}?locale=zh", ttl=300)
        except:
            return ""
        srcs = (data.get("data") or {}).get("sources") or []
        for s in srcs:
            u = s.get("url")
            if u and (".mp4" in u or "/direct/" in u or "/dc/mirror/" in u):
                return u
        for s in srcs:
            u = s.get("url")
            if u:
                return u
        return ""

    def localProxy(self, param):
        pass

    def _pagecount(self, data, current_page=1):
        try:
            pag = data.get("pagination") or {}
            total = int(pag.get("total") or 0)
            size = int(pag.get("page_size") or 24)
            return max(total // size + (1 if total % size else 0), 1)
        except:
            return current_page + 1

    def _item(self, v):
        return {
            "vod_id": v.get("slug") or v.get("id") or "",
            "vod_name": (v.get("title") or "")[:50],
            "vod_pic": v.get("poster_url") or "",
            "vod_remarks": f'{v.get("release_year") or ""} {"⭐" + str(v.get("display_score")) if v.get("display_score") else ""}'.strip(),
        }

    def _items(self, items):
        return [self._item(v) for v in items or []]

    def _fetch(self, path, ttl=0, retry=1):
        now = time.time()
        if ttl and path in self._cache:
            data, ts = self._cache[path]
            if now - ts < ttl:
                return data
        err = None
        for i in range(retry + 1):
            try:
                r = self.fetch(HOST + path, headers={"User-Agent": UA}, timeout=4000)
                data = json.loads(r.text if hasattr(r, 'text') else str(r))
                if ttl:
                    self._cache[path] = (data, now)
                    if len(self._cache) > 100:
                        for k in list(self._cache):
                            if now - self._cache[k][1] > 600:
                                del self._cache[k]
                return data
            except Exception as e:
                err = e
                time.sleep(0.3)
        raise err