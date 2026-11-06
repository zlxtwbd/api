
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
            timeout=timeout or Q360_TI
