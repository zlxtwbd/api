
import json
import time
import uuid
import re
import os
import gzip
import base64
import hashlib
import hmac
import threading
from collections import OrderedDict
from concurrent import futures as _futures
from urllib.parse import urljoin, urlparse

import requests

try:
    from Crypto.Cipher import AES as _AES
except ImportError:
    _AES = None

try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider(object):
        pass


def _unquote_html(s):
    return re.sub(r"<[^>]+>", "", s or "")










MT_BASE = "https://mtyy7.com"
MT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Referer": MT_BASE + "/",
}
MT_DEAD_PIC_HOSTS = ("cdn.g3ejjm8m.com",)
MT_BAD_PIC_HOSTS = ("doubanio.com",)
MT_TIMEOUT = 4            # 单请求超时(非首屏路径)
MT_FAST_TIMEOUT = 3       # 首屏单请求超时(更短, 保证快速返回)
MT_BUDGET = 6.0           # 非首屏补全预算(两源并行竞速)
MT_FAST_BUDGET = 3.5      # 首屏/列表补全预算(两源并行竞速, 保证海报覆盖)
MT_BG_BUDGET = 8.0        # 后台线程补全预算
MT_WORKERS = 12           # 编排池: 同时处理的片名数(翻页一屏约20~24条)
MT_CACHE_MAX = 3000

_mt_tls = threading.local()
_mt_cache = OrderedDict()
_mt_lock = threading.Lock()
_mt_ex = None
_mt_ex_lock = threading.Lock()
_mt_bg_running = False
_mt_bg_lock = threading.Lock()


def _mt_executor():
    """全局复用线程池: 避免每次 _mt_repair 都新建/销毁线程池的额外开销"""
    global _mt_ex
    if _mt_ex is None or getattr(_mt_ex, "_shutdown", False):
        with _mt_ex_lock:
            if _mt_ex is None or getattr(_mt_ex, "_shutdown", False):
                _mt_ex = _futures.ThreadPoolExecutor(max_workers=MT_WORKERS)
    return _mt_ex


def _mt_shutdown():
    """插件销毁时释放全局线程池"""
    global _mt_ex
    if _mt_ex is not None:
        try:
            _mt_ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            _mt_ex.shutdown(wait=False)
        _mt_ex = None


def _mt_session():
    """线程本地会话(免验证码接口无需 cookie, 复用连接降低延迟)"""
    s = getattr(_mt_tls, "s", None)
    if s is None:
        s = requests.Session()
        s.verify = False
        s.headers.update(MT_HEADERS)
        _mt_tls.s = s
    return s


def _mt_norm(s):
    """片名归一化: 去空白/标点, 全角半角统一由 lower 覆盖常见场景"""
    return re.sub(r"[\s:：·,，。.\-—_()（）\[\]【】'\"!！?？~～*]+", "",
                  str(s or "")).lower()


def _mt_pic_dead(pic):
    """判定海报是否需要修补: 空值/非法URL/死CDN/防盗链图床(豆瓣等, App 无法直接显示)"""
    p = (pic or "").strip()
    if not p or p.lower() in ("null", "none", "undefined"):
        return True
    if not p.lower().startswith(("http://", "https://")):
        return True
    try:
        host = urlparse(p).netloc.lower()
    except Exception:
        return True
    for d in MT_DEAD_PIC_HOSTS + MT_BAD_PIC_HOSTS:
        if host == d or host.endswith("." + d):
            return True
    return False


def _mt_num2cn(n):
    """1-99 -> 中文数字(季数用), 超范围返回空"""
    d = "零一二三四五六七八九"
    if n <= 0 or n >= 100:
        return ""
    if n < 10:
        return d[n]
    tens, ones = divmod(n, 10)
    s = "十" if tens == 1 else (d[tens] + "十")
    if ones:
        s += d[ones]
    return s


def _mt_variants(title):
    """片名降级查询变体(仅原查询无结果时使用):
    第N季->第中文N季 / 去季段 / 尾数字->插季名或去除 / 尾中文数字->阿拉伯 /
    去Part后缀 / 去年份后缀"""
    t = str(title or "").strip()
    vs = []
    m = re.search(r"第\s*(\d{1,2})\s*([季期部])", t)
    if m:
        cn = _mt_num2cn(int(m.group(1)))
        if cn:
            vs.append(t[:m.start()] + "第%s%s" % (cn, m.group(2)) + t[m.end():])
        vs.append((t[:m.start()] + t[m.end():]).strip())
    m = re.search(r"(\D)(\d{1,2})$", t)
    if m:
        cn = _mt_num2cn(int(m.group(2)))
        if cn:
            vs.append(t[:m.start() + 1] + "第%s季" % cn)
        vs.append(t[:m.start() + 1])
    m = re.search(r"([一二三四五六七八九十])$", t)
    if m:
        num = "一二三四五六七八九十".index(m.group(1)) + 1
        if num < 10:
            vs.append(t[:m.start()] + str(num))
        vs.append(t[:m.start()])
    m = re.search(r"[.\s]*[Pp]art\.?\s*\d{1,2}$", t)
    if m:
        vs.append(t[:m.start()].strip())
    y = re.search(r"[\s.:：·\-]*(?:19|20)\d{2}\s*$", t)
    if y:
        vs.append(t[:y.start()].strip())
    seen, out = set(), []
    for v in vs:
        v = v.strip()
        if v and v != t and v not in seen:
            seen.add(v)
            out.append(v)
    return out[:4]


def _mt_pic_bad(pic):
    """App 无法直接显示的图源(防盗链图床)"""
    try:
        host = urlparse(str(pic or "")).netloc.lower()
    except Exception:
        return True
    for d in MT_BAD_PIC_HOSTS:
        if host == d or host.endswith("." + d):
            return True
    return False


def _mt_match(items, t, t2=None):
    """同名优先(原名/变体名), 次选互相包含且词长>=3(防短名错配); 跳过豆瓣等防盗链图源"""
    usable = [it for it in items if not _mt_pic_bad(it.get("pic"))]
    if not usable:
        return None
    for tg in (t, t2):
        if not tg:
            continue
        for it in usable:
            if _mt_norm(it.get("name")) == tg:
                return it
    for tg in (t, t2):
        if not tg:
            continue
        for it in usable:
            n = _mt_norm(it.get("name"))
            if len(n) >= 3 and len(tg) >= 3 and (n in tg or tg in n):
                return it
    return None


def _mt_fetch(q, timeout=None):
    """麦田 suggest 单查询: 命中同名返回海报地址, 否则空串。
    变体/多源并发由上层 _mt_lookup 统筹, 这里只做一次查询。"""
    if not q:
        return ""
    try:
        r = _mt_session().get(
            MT_BASE + "/index.php/ajax/suggest",
            params={"wd": q, "mid": 1},
            timeout=timeout or MT_TIMEOUT)
        j = r.json()
    except Exception:
        return ""
    if not isinstance(j, dict) or j.get("code") != 1:
        return ""
    pick = _mt_match(j.get("list") or [], _mt_norm(q))
    return str(pick.get("pic") or "").strip() if pick else ""





Q360_BASE = "https://api.so.360kan.com"
Q360_HOST = "https://www.360kan.com"
Q360_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Referer": Q360_HOST + "/",
}
Q360_TIMEOUT = 6

_q360_tls = threading.local()


def _q360_session():
    """线程本地会话(搜索接口无需 cookie, 复用连接降低延迟)"""
    s = getattr(_q360_tls, "s", None)
    if s is None:
        s = requests.Session()
        s.verify = False
        s.headers.update(Q360_HEADERS)
        _q360_tls.s = s
    return s


def _q360_fixpic(pic):
    """360 官方海报 URL 规范化(同 cat_官360.js fixPic): 补协议头/去制表符"""
    p = (pic or "").strip()
    if not p:
        return ""
    if p.startswith("//"):
        p = "https:" + p
    tab = p.find("\t")
    if tab != -1:
        p = p[:tab]
    return p


def _q360_fetch(q, timeout=None):
    """360官方搜索单查询: 命中同名返回海报地址, 否则空串。"""
    if not q:
        return ""
    try:
        r = _q360_session().get(
            Q360_BASE + "/index",
            params={"force_v": "1", "kw": q, "from": "",
                    "pageno": "1", "v_ap": "1", "tab": "all"},
            timeout=timeout or Q360_TIMEOUT)
        j = r.json()
    except Exception:
        return ""
    if not isinstance(j, dict) or j.get("code") != 1:
        return ""
    rows = (((j or {}).get("data") or {}).get("longData") or {}).get("rows") or []
    items = [{"name": (x.get("titleTxt") or x.get("titlealias")),
              "pic": _q360_fixpic(x.get("cover"))} for x in rows]
    items = [it for it in items if it.get("name") and it.get("pic")]
    pick = _mt_match(items, _mt_norm(q))
    return str(pick.get("pic") or "").strip() if pick else ""


def _mt_race_once(q, timeout, deadline):
    """单查询两源并行竞速: 麦田与 360 同时请求, 谁先给出有效海报就用谁。
    并行可避免"麦田未命中再串行回落 360"的累加延迟, 同时保证两源都能兜底
    (翻页/新上架条目麦田多半没有, 靠 360 补图)。"""
    box = {}

    def _run(key, fn):
        try:
            box[key] = fn(q, timeout)
        except Exception:
            box[key] = ""

    ths = (threading.Thread(target=_run, args=("mt", _mt_fetch), daemon=True),
           threading.Thread(target=_run, args=("q360", _q360_fetch), daemon=True))
    for x in ths:
        x.start()
    limit = min(deadline, time.time() + timeout + 0.5)
    pic = ""
    while time.time() < limit:
        pic = box.get("mt") or box.get("q360") or ""
        if pic or len(box) >= 2:
            break
        time.sleep(0.02)
    for x in ths:
        x.join(0.05)
    return pic or box.get("mt") or box.get("q360") or ""


def _mt_lookup(title, fast=False):
    """带 LRU 缓存的查询, 返回海报地址(查不到为空串)。
    两源并行竞速(麦田+360), 兼顾首屏速度与海报覆盖率;
    仅缓存有效结果, 避免空结果污染缓存、阻断后续重试与落盘预热。"""
    t = _mt_norm(title)
    if not t:
        return ""
    with _mt_lock:
        if t in _mt_cache:
            _mt_cache.move_to_end(t)
            return _mt_cache[t]
    budget = MT_FAST_BUDGET if fast else MT_BUDGET
    timeout = MT_FAST_TIMEOUT if fast else MT_TIMEOUT
    deadline = time.time() + budget
    queries = [str(title or "")] + _mt_variants(title)
    if fast:
        queries = queries[:2]      # 首屏少查变体, 控制请求量
    pic = ""
    for q in queries:
        if not q:
            continue
        pic = _mt_race_once(q, timeout, deadline)
        if pic or time.time() >= deadline:
            break
    if pic:
        with _mt_lock:
            _mt_cache[t] = pic
            _mt_cache.move_to_end(t)
            while len(_mt_cache) > MT_CACHE_MAX:
                _mt_cache.popitem(last=False)
    return pic


def _mt_repair(vods, budget=None, fast=False):
    """海报修补: 两源并行竞速(麦田+360)补全死海报。
    - fast=True(首屏/列表): 限时(budget)补全, 尽快返回; 仍未补全的交给后台线程继续补并写缓存。
    - fast=False(详情): 预算更宽, 单个条目完整补全。
    仅缓存有效结果, 超出预算的查询后续请求会自然重试。"""
    global _mt_bg_running
    try:
        need, seen = [], set()
        for v in vods:
            if not isinstance(v, dict) or not _mt_pic_dead(v.get("vod_pic")):
                continue
            t = _mt_norm(v.get("vod_name"))
            if not t or t in seen:
                continue
            seen.add(t)
            need.append(v)
        if not need:
            return vods
        if budget is None:
            budget = MT_FAST_BUDGET if fast else MT_BUDGET
        deadline = time.time() + budget
        if len(need) == 1:
            pic = _mt_lookup(need[0].get("vod_name"), fast=fast)
            if pic:
                need[0]["vod_pic"] = pic
            need = [] if pic else need
        else:
            ex = _mt_executor()
            futs = dict((ex.submit(_mt_lookup, v.get("vod_name"), fast), v)
                       for v in need)
            try:
                for fu in _futures.as_completed(futs, timeout=budget):
                    try:
                        pic = fu.result()
                    except Exception:
                        continue
                    if pic:
                        futs[fu]["vod_pic"] = pic
                    if time.time() > deadline:
                        break
            except _futures.TimeoutError:
                pass
        # 首屏限时后仍有未补全的死海报 -> 后台线程继续(麦田+360), 写缓存供后续请求命中
        if fast and need and not _mt_bg_running:
            with _mt_bg_lock:
                if not _mt_bg_running:
                    _mt_bg_running = True
                    threading.Thread(target=_mt_warm, args=(need,),
                                    daemon=True).start()
        return vods
    except Exception as _e:
        print("[SA视频] 海报修补异常: %s" % _e)
        return vods


def _mt_warm(dead_vods):
    """后台线程: 对首屏未补全的死海报用麦田+360 继续查询, 结果写入 LRU 缓存, 供后续导航/刷新直接命中。"""
    global _mt_bg_running
    try:
        names, seen = [], set()
        for v in dead_vods:
            if not _mt_pic_dead(v.get("vod_pic")):
                continue
            t = _mt_norm(v.get("vod_name"))
            if not t or t in seen:
                continue
            seen.add(t)
            names.append(v.get("vod_name"))
        if not names:
            return
        ex = _mt_executor()
        futs = [ex.submit(_mt_lookup, n, False) for n in names]
        try:
            for fu in _futures.as_completed(futs, timeout=MT_BG_BUDGET):
                try:
                    fu.result()
                except Exception:
                    pass
        except _futures.TimeoutError:
            pass
    finally:
        _mt_bg_running = False


def _mt_cache_path():
    """海报缓存落盘路径: 优先脚本同目录(可写则用之), 失败回退系统临时目录。"""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "SA_poster_cache.json")
        with open(p, "a"):
            pass
        return p
    except Exception:
        try:
            import tempfile
            return os.path.join(tempfile.gettempdir(), "SA_poster_cache.json")
        except Exception:
            return None


def _mt_load_cache():
    """启动时预热 LRU 缓存: 让非首次启动的冷加载直接命中, 无需联网。"""
    p = _mt_cache_path()
    if not p or not os.path.exists(p):
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            with _mt_lock:
                for k, v in data.items():
                    if k and v:
                        _mt_cache[k] = v
                        _mt_cache.move_to_end(k)
                while len(_mt_cache) > MT_CACHE_MAX:
                    _mt_cache.popitem(last=False)
    except Exception:
        pass


def _mt_save_cache():
    """销毁时落盘: 已查到的海报地址保留, 下次启动冷加载即时可用。"""
    p = _mt_cache_path()
    if not p:
        return
    try:
        with _mt_lock:
            data = {k: v for k, v in _mt_cache.items() if v}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


class Spider(_BaseSpider):
    name = "SA视频"
    base_url = "https://www.lsjys11.com"
    site_url = "https://www.lsjys11.com"
    api_url = "https://www.lsjys11.com/api"
    KEY = b"x3t8rvtaescfe38s"
    EP_FILTER = "/movie/filter"
    EP_SEARCH = "/search/movie"
    EP_DETAIL = "/movie/detail"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.lsjys11.com/",
        "Origin": "https://www.lsjys11.com",
    }
    timeout = 15
    page_size = "24"
    class_name = ["电影", "连续剧", "综艺", "短剧", "动漫", "纪录片"]
    class_url = ["13", "12", "11", "16", "14", "15"]
    _device_id = ("oct" + uuid.uuid4().hex)[:20]
    _session = None
    _filter_data = None
    _filter_ts = 0.0
    _home_cache = None
    _home_ts = 0.0
    _detail_cache = None
    _detail_key = ""
    _detail_ts = 0.0
    CACHE_TTL = 60.0

    def getName(self):
        return self.name

    def init(self, extend=""):
        self._extend = extend or ""
        ext = {}
        if isinstance(extend, str) and extend.strip():
            try:
                ext = json.loads(extend)
            except Exception:
                ext = {}
        elif isinstance(extend, dict):
            ext = dict(extend)
        if isinstance(ext, dict):
            k = str(ext.get("key") or "")
            if k:
                self.KEY = k.encode()
            h = str(ext.get("host") or "").rstrip("/")
            if h.startswith("http"):
                self.base_url = h
                self.site_url = h
                self.api_url = h + "/api"
                self.headers["Referer"] = h + "/"
                self.headers["Origin"] = h
        _mt_load_cache()
        return ""

    def getHeaders(self):
        return self.headers

    def _get_session(self):
        if self._session is None:
            from requests.adapters import HTTPAdapter as _HTTPAdapter
            s = _PowSession()
            s.trust_env = False
            s.verify = False
            s.headers.update(self.headers)
            ad = _HTTPAdapter(pool_connections=5, pool_maxsize=10,
                              max_retries=0)
            s.mount("http://", ad)
            s.mount("https://", ad)
            self._session = s
        return self._session

    def _gen_key(self, rid):
        return hmac.new(self.KEY, bytes.fromhex(rid.replace("-", "")),
                        hashlib.sha256).digest()

    def _api(self, path, data, retries=1):
        if _AES is None:
            print(f"[{self.name}] 缺少 pycryptodome，无法使用加密接口: "
                  f"pip install pycryptodome")
            return None
        sess = self._get_session()
        for i in range(retries + 1):
            rid = str(uuid.uuid4())
            body = {"data": data, "token": "", "deviceId": self._device_id}
            raw = gzip.compress(json.dumps(body, separators=(",", ":")).encode())
            pad = 16 - len(raw) % 16
            raw += bytes([pad]) * pad
            iv = os.urandom(16)
            try:
                payload = base64.b64encode(
                    iv + _AES.new(self._gen_key(rid), _AES.MODE_CBC,
                                  iv).encrypt(raw)).decode()
                r = sess.post(
                    self.api_url + path, data=payload,
                    headers={"Content-Type": "text/plain",
                             "time": str(int(time.time() * 1000))[:11],
                             "version": "1.0.0", "deviceType": "web",
                             "requestId": rid, "language": "zh-cn",
                             "Accept": "application/json"},
                    timeout=(6, 20))
                if r.status_code != 200 or not r.text:
                    if 400 <= r.status_code < 500:
                        print(f"[{self.name}] 接口拒绝({i + 1}/{retries + 1}) "
                              f"{path}: http {r.status_code}")
                        return None
                    raise ValueError("http %s" % r.status_code)
                er = base64.b64decode(r.text.strip())
                pt = _AES.new(self._gen_key(rid), _AES.MODE_CBC,
                              er[:16]).decrypt(er[16:])
                pt = pt[:-pt[-1]]
                return json.loads(gzip.decompress(pt).decode())
            except Exception as e:
                if i < retries:
                    print(f"[{self.name}] 接口异常({i + 1}/{retries + 1}) "
                          f"{path}: {e}")
                    time.sleep(0.3)
                else:
                    print(f"[{self.name}] 接口异常 {path}: {e}")
                    return None
        return None

    def _load_filters(self, force=False):
        now = time.time()
        if self._filter_data is not None and not force and now - self._filter_ts < 1800:
            return self._filter_data
        r = self._api(self.EP_FILTER, {})
        data = {}
        if isinstance(r, dict) and r.get("status") == "y" and isinstance(r.get("data"), dict):
            d = r["data"]
            cats = (d.get("category") or {})
            cat_items = [x for x in (cats.get("items") or []) if str(x.get("value") or "")]

            def block(field, items, name):
                vals = [{"n": _unquote_html(x.get("name")), "v": str(x.get("value") or "")}
                        for x in (items or [])]
                return {"key": field, "name": name, "value": vals} if vals else None

            for c in cat_items:
                tid = str(c.get("value") or "")
                blocks = []
                tags = ((c.get("filters") or {}).get("items")) or []
                b = block("tag_id", [t for t in tags if str(t.get("value") or "")], "标签")
                if b:
                    blocks.append(b)
                for field, fname in (("area", "地区"), ("language", "语言"),
                                     ("year", "年份"), ("series_status", "状态"),
                                     ("update_status", "状态"), ("sort", "排序")):
                    dim = d.get(field) or {}
                    b = block(dim.get("field") or field,
                              [x for x in (dim.get("items") or [])
                               if str(x.get("value") or "")],
                              fname)
                    if b:
                        blocks.append(b)
                data[tid] = blocks
        if data:
            self._filter_data = data
            self._filter_ts = now
        return self._filter_data or {}

    def homeContent(self, filter=False):
        now = time.time()
        if (self._home_cache is not None
                and now - self._home_ts < self.CACHE_TTL):
            result = dict(self._home_cache)
            if filter and not result.get("filters"):
                result["filters"] = self._load_filters()
            return result
        result = {"class": [], "filters": {}, "list": []}
        classes = []
        r = self._api(self.EP_FILTER, {})
        if isinstance(r, dict) and isinstance(r.get("data"), dict):
            for c in ((r["data"].get("category") or {}).get("items") or []):
                tid = str(c.get("value") or "")
                if tid:
                    classes.append({"type_id": tid,
                                    "type_name": _unquote_html(c.get("name"))})
        if not classes and self.class_name:
            classes = [{"type_id": u, "type_name": n}
                       for n, u in zip(self.class_name, self.class_url)]
        result["class"] = classes
        if filter:
            result["filters"] = self._load_filters()
        r2 = self._api(self.EP_SEARCH, {"page": 1, "page_size": self.page_size,
                                        "order_direction": "desc",
                                        "position": "movie"})
        if isinstance(r2, dict) and isinstance((r2.get("data") or {}).get("data"), list):
            result["list"] = [self._brief(x) for x in r2["data"]["data"][:30]]
        result["list"] = _mt_repair(result["list"], fast=True)
        self._home_cache = result
        self._home_ts = now
        return result

    def homeVideoContent(self):
        r = self._api(self.EP_SEARCH, {"page": 1, "page_size": "12",
                                       "order_direction": "desc",
                                       "position": "movie"})
        if isinstance(r, dict) and isinstance((r.get("data") or {}).get("data"), list):
            items = r["data"]["data"]
            if items:
                b = self._brief(items[0])
                b["vod_content"] = _unquote_html(str(items[0].get("description") or ""))
                return _mt_repair([b])[0]
        return {}

    def _normalize_pic(self, pic):
        """海报 URL 规范化：补全协议头/相对路径，空值与异常值容错，
        避免播放器因 vod_pic 非完整 URL 或空图而无法显示海报。"""
        pic = (pic or "").strip()
        if not pic or pic.lower() in ("null", "none", "undefined"):
            return ""
        if pic.startswith("//"):
            return "https:" + pic
        if pic.startswith("/"):
            return urljoin(self.base_url.rstrip("/") + "/", pic)
        if not pic.lower().startswith(("http://", "https://")):
            return urljoin(self.base_url.rstrip("/") + "/", pic)
        return pic

    def _brief(self, item):
        return {
            "vod_id": str(item.get("id") or ""),
            "vod_name": _unquote_html(str(item.get("name") or "")),
            "vod_pic": self._normalize_pic(item.get("img")),
            "vod_remarks": str(item.get("duration") or ""),
            "vod_year": str(item.get("issue_year") or ""),
        }

    def categoryContent(self, tid, pg, filter=False, extend=None):
        page = int(pg) if str(pg).isdigit() and int(pg) > 0 else 1
        result = {"list": [], "page": page, "pagecount": 1,
                  "limit": int(self.page_size), "total": 0}
        ext = {}
        if isinstance(extend, str) and extend.strip():
            try:
                ext = json.loads(extend)
            except Exception:
                ext = {}
        if isinstance(extend, dict):
            ext = dict(extend)
        body = {"page": page, "page_size": self.page_size,
                "order_direction": "desc", "position": "movie"}
        if str(tid):
            body["cat_id"] = str(tid)
        for k in ("tag_id", "area", "language", "year", "update_status",
                  "series_status", "order"):
            v = str(ext.get(k) or "")
            if v:
                body[k] = v
        r = self._api(self.EP_SEARCH, body)
        if isinstance(r, dict) and isinstance(r.get("data"), dict):
            d = r["data"]
            items = d.get("data") or []
            result["list"] = _mt_repair([self._brief(x) for x in items], fast=True)
            try:
                result["total"] = int(d.get("total") or 0)
                result["pagecount"] = int(d.get("last_page") or 1)
            except Exception:
                pass
        return result

    def _get_detail(self, vid, lid=None):
        key = str(vid)
        now = time.time()
        if (self._detail_cache is not None
                and self._detail_key == key
                and now - self._detail_ts < self.CACHE_TTL):
            return self._detail_cache
        body = {"id": key}
        if lid:
            body["lid"] = str(lid)
        r = self._api(self.EP_DETAIL, body)
        d = ((r or {}).get("data") or {}).get("data") or {}
        if d:
            self._detail_cache = d
            self._detail_key = key
            self._detail_ts = now
        return d

    def detailContent(self, ids):
        result = {"list": []}
        try:
            vid = str(ids[0])
            d = self._get_detail(vid)
            vod_name = _unquote_html(str(d.get("name") or ""))
            tags = [str(t.get("name") or "") for t in (d.get("tags") or [])
                    if t.get("name")]
            play_links = d.get("play_links") or []



            play_links = [x for x in play_links
                          if str(x.get("code") or "") != "line3"]
            if not play_links:
                play_links = d.get("play_links") or []
            line_names = [str(x.get("name") or x.get("code") or "线路")
                          for x in play_links] or ["默认线路"]
            eps_groups = d.get("links") or []
            line_urls = []
            for _ln in line_names:
                eps = []
                for g in eps_groups:
                    for it in (g.get("items") or []):
                        lid = str(it.get("id") or "")
                        if lid:
                            eps.append("%s$%s|%s" % (
                                _unquote_html(str(it.get("name") or "")), vid, lid))
                if not eps:
                    eps.append("正片$%s|%s" % (vid, str(d.get("lid") or "")))
                line_urls.append("#".join(eps))
            vod = {
                "vod_id": vid,
                "vod_name": vod_name,
                "vod_pic": self._normalize_pic(d.get("img")),
                "vod_year": str(d.get("issue_year") or ""),
                "vod_area": str(d.get("area") or ""),
                "vod_actor": _unquote_html(str(d.get("actor") or "")),
                "vod_director": _unquote_html(str(d.get("director") or "")),
                "vod_type": ",".join(tags),
                "vod_remarks": str(d.get("links_tips") or ""),
                "vod_content": _unquote_html(str(d.get("description") or "")),
                "vod_play_from": "$$$".join(line_names),
                "vod_play_url": "$$$".join(line_urls),
            }
            result["list"] = _mt_repair([vod], budget=4.0)
        except Exception as e:
            print(f"[{self.name}] 详情异常: {e}")
        return result

    def searchContent(self, key, quick=None, pg="1"):
        page = int(pg) if str(pg).isdigit() and int(pg) > 0 else 1
        result = {"list": [], "page": page, "pagecount": 1,
                  "limit": int(self.page_size), "total": 0}
        if not key:
            return result
        r = self._api(self.EP_SEARCH, {"page": page, "page_size": self.page_size,
                                       "keywords": str(key),
                                       "order_direction": "desc"})
        if isinstance(r, dict) and isinstance(r.get("data"), dict):
            d = r["data"]
            items = d.get("data") or []
            result["list"] = _mt_repair([self._brief(x) for x in items], fast=True)
            try:
                result["total"] = int(d.get("total") or 0)
                result["pagecount"] = int(d.get("last_page") or 1)
            except Exception:
                pass
        return result

    def playerContent(self, flag, id, vipFlags=None):
        try:
            vid, _, lid = str(id).partition("|")
            line = str(flag or "")
            d = self._get_detail(vid, lid or None)
            play_links = [x for x in (d.get("play_links") or [])
                          if str(x.get("code") or "") != "line3"]
            url = ""
            for x in play_links:
                if (str(x.get("name") or "") == line
                        or str(x.get("code") or "") == line or not line):
                    url = str(x.get("m3u8_url") or "")
                    if url:
                        break
            if not url and play_links:
                url = str(play_links[0].get("m3u8_url") or "")
            if url and not url.startswith("http"):
                url = urljoin(self.base_url + "/", url)
            if url:
                return {"parse": 0, "url": url,
                        "header": {"User-Agent": self.headers["User-Agent"],
                                   "Referer": self.base_url + "/"}}
            return {"parse": 1, "url": self.base_url + "/", "header": {}}
        except Exception as e:
            print(f"[{self.name}] 播放解析异常: {e}")
            return {"parse": 1, "url": self.base_url + "/", "header": {}}

    def isVideoFormat(self, url):
        return any(t in url for t in (".m3u8", ".mp4", ".flv", ".mkv"))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        try:
            if self._session is not None:
                self._session.close()
        except Exception:
            pass
        try:
            _mt_shutdown()
        except Exception:
            pass
        try:
            _mt_save_cache()
        except Exception:
            pass

    def localProxy(self, param):
        return None


import requests as _rq
from urllib.parse import urlparse as _up


def _pow_params(text):
    if not text:
        return None
    if not ("正在验证您的浏览器" in text or "__cdn_pow" in text):
        return None
    import re as _re
    m = _re.search(r'var\s+TS\s*=\s*"([^"]+)"\s*,\s*SIG\s*=\s*"([^"]+)"\s*,'
                   r'\s*DIFF\s*=\s*"([^"]+)"\s*(?:,\s*MODE\s*=\s*"([^"]*)")?',
                   text)
    if not m:
        return None
    mc = _re.search(r'var\s+POW\s*=\s*"([^"]+)"', text)
    return {"ts": m.group(1), "sig": m.group(2),
            "diff": m.group(3) or "0000", "mode": m.group(4) or "auto",
            "cookie": mc.group(1) if mc else "__cdn_pow"}


def _pow_solve(sig, diff, mx=4000000):
    import hashlib as _hl
    import time as _t
    t0 = _t.time()
    n = 0
    while n < mx:
        if _hl.sha256((sig + str(n)).encode()).hexdigest().startswith(diff):
            return n
        n += 1
        if n % 50000 == 0 and _t.time() - t0 > 8:
            return None
    return None


try:
    class _PowSession(_rq.Session):

        def request(self, method, url, *a, **kw):
            r = _rq.Session.request(self, method, url, *a, **kw)
            try:
                txt = r.text or ""
            except Exception:
                return r
            for _i in range(2):
                p = _pow_params(txt)
                if not p:
                    break
                n = _pow_solve(p["sig"], p["diff"])
                if n is None:
                    break
                self.cookies.set(p["cookie"],
                                 "%s_%s_%s_%s" % (p["ts"], p["mode"], n, p["sig"]),
                                 domain=_up(url).netloc, path="/")
                r = _rq.Session.request(self, method, url, *a, **kw)
                try:
                    txt = r.text or ""
                except Exception:
                    break
            return r
except Exception:
    _PowSession = _rq.Session
