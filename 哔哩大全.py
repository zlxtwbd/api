# -*- coding: utf-8 -*-
"""哔哩大全 · FongMi TV Python 源

    首页  x/web-interface/popular   全站热门
    分区  search/type               22 个一级分区的 UP 主投稿，一页 50 条
    详情  x/web-interface/view      多分P 一并列出，UP 主合集铺成选集
    播放  DASH 合成 MPD，清晰度铺成多条线路
    弹幕  dm/list.so                raw deflate，proxy 解成 XML 再给播放器

几条动不得的：
    buvid3 要从 finger/spi 取真的，自己拼的会成片 412
    DASH 走 fnval=4048 和 /x/player/playurl，wbi 那条只给到 480P
    清晰度只能在 dash 轨道里数，accept_quality 会谎报 4K
    MPD 的 format 必须是 "application/dash+xml"，CDN 直链必须带 Referer
    搜索接口比取流严得多：一页 50 条、间隔 2 秒、缓存 3 分钟

配置全在文件顶部
    COOKIE   整条浏览器 cookie，留空也能看，只是清晰度上限低一些
    QUALITY  首选清晰度，接口给不到会自动降档
    ext      站点配置里留空即可
"""

import hashlib
import html
import json
import re
import time
import zlib
from urllib.parse import quote, urlencode
from xml.etree import ElementTree as ET

from base.spider import Spider as BaseSpider

# ---------------------------------------------------------------- 配置区

# 整条复制浏览器里的 Cookie，至少要带 SESSDATA。写死在文件里，分享即交出账号。
COOKIE = ""

# 首选清晰度：120=4K，116=1080P60，80=1080P，64=720P。给不到会自动降档
QUALITY = 120

API = "https://api.bilibili.com"
WEB = "https://www.bilibili.com"
SEARCH = "https://search.bilibili.com"
SPACE = "https://space.bilibili.com"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

TIMEOUT = 12000
WBI_TTL = 1800
PAGE_SIZE = 50

# WBI 混排表前 32 位，抄错一位签名就废
MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
]

# 分区：(type_id, 名称, 关键词, tid)。搜索接口硬要求带 keyword，tids 负责锁区
UGC_TYPES = [
    ("knowledge", "知识", "知识", 36),
    ("tech", "科技", "科技", 188),
    ("music", "音乐", "音乐", 3),
    ("dance", "舞蹈", "舞蹈", 129),
    ("game", "游戏", "游戏", 4),
    ("food", "美食", "美食", 211),
    ("animal", "动物", "动物", 217),
    ("life", "生活", "生活", 160),
    ("guichu", "鬼畜", "鬼畜", 119),
    ("movie_talk", "影视", "影视", 181),
    ("documentary", "纪录片", "纪录片", 177),
    ("anime", "动画", "动画", 1),
    ("bangumi", "番剧", "番剧", 13),
    ("guochuang", "国创", "国创", 167),
    ("ent", "娱乐", "娱乐", 5),
    ("fashion", "时尚", "时尚", 155),
    ("news", "资讯", "新闻", 202),
    ("handmake", "手工", "手工", 161),
    ("paint", "绘画", "绘画", 162),
    ("sports", "运动", "运动", 235),
    ("fitness", "健身", "健身", 234),
    ("car", "汽车", "汽车", 223),
]

SORTS = [
    ("totalrank", "综合排序"),
    ("click", "最多播放"),
    ("pubdate", "最新发布"),
    ("dm", "最多弹幕"),
    ("stow", "最多收藏"),
]

DURATIONS = [
    ("", "全部时长"),
    ("1", "10 分钟以下"),
    ("2", "10-30 分钟"),
    ("3", "30-60 分钟"),
    ("4", "60 分钟以上"),
]

# 播放量、时间接口不认，只拿到一页后本地筛，见 _narrow()
PLAYS = [
    ("", "不限播放量"),
    ("1", "1 万以上"),
    ("2", "10 万以上"),
    ("3", "100 万以上"),
]

PLAY_FLOOR = {"1": 10000, "2": 100000, "3": 1000000}

AGES = [
    ("", "不限时间"),
    ("1", "三天内"),
    ("2", "一周内"),
    ("3", "一个月内"),
]

AGE_DAYS = {"1": 3, "2": 7, "3": 30}

# 搜索接口连点几个分类就 412，请求之间隔这么久，结果再缓存几分钟
SEARCH_GAP = 2.0
LIST_TTL = 180

# 画质编号 -> 展示名。只有 dash 里真出现过的编号才会被铺成线路
QUALITY_NAMES = {
    6: "240P", 16: "360P", 32: "480P", 64: "720P", 74: "720P 60帧",
    80: "1080P", 112: "1080P 高码率", 116: "1080P 60帧",
    120: "4K", 125: "HDR 真彩", 126: "杜比视界", 127: "8K",
}

# 探测时直接问最高档，接口按账号实际权限返回
PROBE_QN = 127

TAG = re.compile(r"<[^>]+>")
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
RANGE_RE = re.compile(r"^([0-9]+)-([0-9]+)$")
FRAME_RE = re.compile(r"^[0-9]+(?:\.[0-9]+|/[1-9][0-9]*)?$")


def _clean(text):
    """搜索结果标题里带 <em class="keyword"> 高亮，先去标签再还原实体。"""
    return html.unescape(TAG.sub("", str(text or ""))).strip()


# 投稿简介里，正文后面常挂着求三连、引流的话术。强词命中就整句删；
# 弱词（感谢、分享、支持）正文也用，只在 40 字以内的短句里才算推广
PROMO_HARD = re.compile(
    r"三连|三联|投币|充电|打赏|赞赏|一键|"
    r"关注我|点个关注|加个关注|求关注|"
    r"评论区|留言|私信|私聊|弹幕|公众号|微信|QQ群|qq群|群号|"
    r"课程|下单|购买|优惠|折扣|领券|淘宝|闲鱼|店铺|橱窗|"
    r"商务|合作|恰饭|广告|推广|赞助|催更|追更|下期|主页|订阅")
PROMO_SOFT = re.compile(
    r"感谢|谢谢|支持|点赞|收藏|分享|转发|链接|喜欢这期|多多|记得|欢迎收看|"
    r"讲义|资料|网盘|提取码|报名|咨询")

SENT_RE = re.compile(r"(?<=[。！？!?；;\n])")


def _desc(text, limit=400):
    """洗掉话题标签、@、短链和推广话术，只留正文；洗空了就返回空。"""
    text = _clean(text)
    if not text:
        return ""
    text = re.sub(r"#[^#\n]{1,40}#", "", text)
    text = re.sub(r"@[^\s@：:，,。！？]{1,20}", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"b23\.tv/\S+", "", text)

    kept = []
    for part in SENT_RE.split(text):
        body = part.strip()
        plain = body.strip("。！？!?；;，,、~～… \u3000")
        if len(plain) < 3:
            continue
        if PROMO_HARD.search(body):
            continue
        if len(plain) <= 40 and PROMO_SOFT.search(body):
            continue
        # split 把换行当分隔符吃掉了，补回来才不会几段糊成一坨
        kept.append(body.rstrip("～~") + ("\n" if part.endswith("\n") else ""))

    out = "".join(kept)
    out = re.sub(r"[ \t\u3000]+", " ", out)
    out = re.sub(r"\n[ \u3000]*\n+", "\n", out)
    out = out.strip()
    if len(out) <= limit:
        return out
    cut = max(out.rfind(mark, 0, limit) for mark in "。！？!?\n")
    return out[:cut + 1] if cut > limit // 2 else out[:limit]


def _raw_desc(data):
    """desc_v2 里 type 2 是 @、type 3 是话题，只要 type 1 的正文。"""
    parts = [str(item.get("raw_text") or "").strip()
             for item in (data.get("desc_v2") or [])
             if isinstance(item, dict) and _int(item.get("type")) == 1]
    text = "\n".join(part for part in parts if part)
    return text or str(data.get("desc") or "")


def _human(value):
    """播放量、弹幕数折成「708.6万」，比一串七位数好读。"""
    number = _int(value)
    if number <= 0:
        return ""
    if number >= 100000000:
        return "%.1f亿" % (number / 100000000.0)
    if number >= 10000:
        return "%.1f万" % (number / 10000.0)
    return str(number)


def _narrow(rows, extend):
    """播放量、时间接口不认，拿到一页后本地筛。筛空了就空页，不多打几页凑数。"""
    extend = extend if isinstance(extend, dict) else {}
    floor = PLAY_FLOOR.get(str(extend.get("plays") or ""))
    days = AGE_DAYS.get(str(extend.get("age") or ""))
    if not floor and not days:
        return rows

    now = time.time()
    kept = []
    for row in rows:
        if floor and _int(row.get("play")) < floor:
            continue
        if days:
            stamp = _int(row.get("pubdate"))
            # 时间缺失时放过，别把整页错杀成空
            if stamp and now - stamp > days * 86400:
                continue
        kept.append(row)
    return kept


def _cover(url):
    url = str(url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[7:]
    return url


def _clock(seconds):
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    hour, rest = divmod(seconds, 3600)
    minute, second = divmod(rest, 60)
    if hour:
        return "%d:%02d:%02d" % (hour, minute, second)
    return "%02d:%02d" % (minute, second)


def _seconds(text):
    """搜索结果的时长是 "199:23" 这种串，热门的却是秒数，统一成秒。"""
    if isinstance(text, (int, float)):
        return int(text)
    parts = str(text or "").split(":")
    try:
        values = [int(part) for part in parts]
    except ValueError:
        return 0
    total = 0
    for value in values:
        total = total * 60 + value
    return total


LAST_SECOND = 253402300799    # 9999-12-31 23:59:59


def _stamp(value):
    """折成秒。0、毫秒时间戳、未来值一律当没有（毫秒直接格式化会显示成五万年后）。"""
    seconds = _int(value)
    return seconds if 0 < seconds <= LAST_SECOND else 0


def _date(value):
    """投稿给的是秒级时间戳，影视给的是日期串，两种都认。"""
    text = str(value or "").strip()
    if len(text) >= 10 and text[:4].isdigit() and text[4] in "-/":
        return text[:10].replace("/", "-")
    seconds = _stamp(value)
    return time.strftime("%Y-%m-%d", time.localtime(seconds)) if seconds else ""


def _year(timestamp):
    text = _date(timestamp)
    return text[:4] if text else ""


def _is_drm(data):
    """DRM 片的 dash 里没有可播地址，认出来才能把话说清楚。"""
    if not isinstance(data, dict):
        return False
    return bool(_int(data.get("is_drm"))) or bool(_int((data.get("dash") or {}).get("is_drm")))


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _decimal(value):
    """MPD 时长可以是小数，截成整数会让最后一秒对不上。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number <= 0 or number != number or number == float("inf"):
        return ""
    text = ("%.3f" % number).rstrip("0").rstrip(".")
    return text or ""


def _segment(stream, name):
    base = stream.get("SegmentBase") or stream.get("segment_base") or {}
    return str(base.get(name) or base.get(name.lower()) or "")


def _range_ok(value):
    """Initialization / indexRange 必须形如 "0-915" 且左不大于右，否则拿不到 moov。"""
    match = RANGE_RE.match(str(value or ""))
    return bool(match) and int(match.group(1)) <= int(match.group(2))


def _media_url(stream):
    url = str(stream.get("baseUrl") or stream.get("base_url") or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[7:]
    if not url.startswith("https://") or " " in url:
        return ""
    return url


def _codec_rank(stream):
    """盒子对 H.264 最稳，HEVC 次之，AV1 基本解不动。"""
    codec_id = _int(stream.get("codecid"))
    codecs = str(stream.get("codecs") or "").lower()
    if codec_id == 7 or codecs.startswith("avc"):
        return 0
    if codec_id == 12 or codecs.startswith(("hev1", "hvc1")):
        return 1
    if codec_id == 13 or codecs.startswith("av01"):
        return 2
    return 3


def _usable(stream, kind):
    if not isinstance(stream, dict):
        return False
    if _int(stream.get("id")) <= 0 or _int(stream.get("bandwidth")) <= 0:
        return False
    if not str(stream.get("codecs") or "").strip():
        return False
    if not _range_ok(_segment(stream, "Initialization")):
        return False
    if not _range_ok(_segment(stream, "indexRange")):
        return False
    mime = str(stream.get("mimeType") or stream.get("mime_type") or "")
    if mime and mime != kind + "/mp4":
        return False
    return bool(_media_url(stream))


class Spider(BaseSpider):

    def init(self, extend=""):
        """ext 只当临时覆盖：留空走 COOKIE/QUALITY，JSON 覆盖 cookie 和 qn，
        裸的 SESSDATA=xx 也认。"""
        options = {}
        if isinstance(extend, dict):
            options = dict(extend)
        elif extend is not None:
            text = str(extend).strip()
            if text and text not in ("{}", "null", "None"):
                if text[0] in "{[":
                    try:
                        parsed = json.loads(text)
                    except ValueError:
                        parsed = None
                    if isinstance(parsed, dict):
                        options = parsed
                else:
                    options = {"cookie": text}

        self.cookie = str(options.get("cookie") or COOKIE or "").strip()
        want = _int(options.get("qn")) or _int(QUALITY) or 120
        # 手滑写成 1080 这种（该是 80）接口会直接忽略，回落 4K 更省事
        self.qn = want if want in QUALITY_NAMES else 120
        self._buvid = ""
        self._cookie_ready = False
        self._mixin = ""
        self._mixin_at = 0.0
        self._play_cache = {}
        self._dm_cache = {}
        self._list_cache = {}
        self._last_search = 0.0

    # ---- 首页 ----------------------------------------------------------

    def homeContent(self, filter):
        result = {"class": [{"type_id": tid, "type_name": name}
                            for tid, name, _, _ in UGC_TYPES]}
        if filter:
            result["filters"] = {tid: self._filters() for tid, _, _, _ in UGC_TYPES}
        return result

    @staticmethod
    def _filters():
        return [{"key": "sort", "name": "排序",
                 "value": [{"n": label, "v": value} for value, label in SORTS]},
                {"key": "duration", "name": "时长",
                 "value": [{"n": label, "v": value} for value, label in DURATIONS]},
                {"key": "plays", "name": "播放量",
                 "value": [{"n": label, "v": value} for value, label in PLAYS]},
                {"key": "age", "name": "发布时间",
                 "value": [{"n": label, "v": value} for value, label in AGES]}]

    def homeVideoContent(self):
        data = self._api("/x/web-interface/popular", {"ps": PAGE_SIZE, "pn": 1})
        return {"list": [self._item(entry) for entry in data.get("list") or []]}

    # ---- 列表 ----------------------------------------------------------

    def categoryContent(self, tid, pg, filter, extend):
        extend = extend if isinstance(extend, dict) else {}
        page = max(1, _int(pg) or 1)
        return self._ugc_list(str(tid or ""), page, extend)

    def _ugc_list(self, tid, page, extend):
        keyword, tids = self._category(tid)
        order = str(extend.get("sort") or "totalrank")
        duration = str(extend.get("duration") or "")
        data = self._list(keyword, page, order, duration, tids)
        raw = [entry for entry in data.get("result") or []
               if entry.get("type") == "video"]
        items = [self._item(entry) for entry in _narrow(raw, extend)]
        # 上游真没东西才报「没有这个分类」；筛得太狠导致一页空是筛选的锅
        if not raw and page == 1:
            raise RuntimeError("B 站没有返回「%s」的任何结果" % keyword)
        pages = _int(data.get("numPages")) or 1
        return {"list": items, "page": page, "pagecount": max(1, pages),
                "limit": PAGE_SIZE, "total": _int(data.get("numResults"))}

    def _list(self, keyword, page, order, duration, tids):
        key = "%s|%s|%s|%s|%s" % (keyword, page, order, duration, tids)
        hit = self._list_cache.get(key)
        if hit and time.time() - hit[0] < LIST_TTL:
            return hit[1]
        data = self._search(keyword, page, order, duration, tids)
        if len(self._list_cache) > 60:
            self._list_cache.clear()
        self._list_cache[key] = (time.time(), data)
        return data

    def _category(self, tid):
        for key, _, keyword, tids in UGC_TYPES:
            if key == tid:
                return keyword, tids
        return str(tid or ""), 0

    # ---- 搜索 ----------------------------------------------------------

    def searchContent(self, key, quick, pg="1"):
        keyword = str(key or "").strip()
        page = max(1, _int(pg) or 1)

        # 直接贴 BV 号 / 视频链接，跳过搜索
        found = BV_RE.search(keyword)
        if found:
            return self._one("bv:" + found.group(0), found.group(0), "打开视频")
        if keyword.startswith("av") and keyword[2:].isdigit():
            return self._one("av:" + keyword[2:], keyword, "打开视频")

        data = self._search(keyword, page, "totalrank", "", 0)
        items = [self._item(entry) for entry in data.get("result") or []
                 if entry.get("type") == "video"]
        return {"list": items, "page": page,
                "pagecount": max(1, _int(data.get("numPages")) or 1),
                "limit": PAGE_SIZE, "total": _int(data.get("numResults"))}

    @staticmethod
    def _one(vod_id, name, remark):
        return {"list": [{"vod_id": vod_id, "vod_name": _clean(name),
                          "vod_remarks": remark}], "page": 1,
                "pagecount": 1, "limit": PAGE_SIZE, "total": 1}

    # ---- 详情 ----------------------------------------------------------

    def detailContent(self, ids):
        return {"list": [self._detail(str(vod_id)) for vod_id in (ids or [])]}

    def _detail(self, ident):
        ident = str(ident or "").strip()
        if ident.startswith("bv:"):
            return self._detail_ugc(bvid=ident[3:])
        if ident.startswith("av:"):
            return self._detail_ugc(aid=ident[3:])
        # 播放串里的 aid_cid、手贴的裸 BV 号都从这里进来
        if ident.startswith("BV"):
            return self._detail_ugc(bvid=ident)
        return self._detail_ugc(aid=ident.split("_")[0] or ident)

    def _detail_ugc(self, aid="", bvid=""):
        params = {"bvid": bvid} if bvid else {"aid": aid}
        data = self._api("/x/web-interface/view", params, WEB + "/")
        pages = data.get("pages") or []
        if not pages:
            raise RuntimeError("视频不存在或已下架：%s" % (bvid or aid))

        aid = str(data.get("aid") or aid)
        episodes = []
        for index, page in enumerate(pages, 1):
            name = _clean(page.get("part")) or ("第 %d 集" % index)
            episodes.append("%s$%s_%s" % (_safe(name), aid, page.get("cid")))

        owner = data.get("owner") or {}
        stat = data.get("stat") or {}
        desc = _desc(_raw_desc(data))

        line = []
        if data.get("bvid"):
            line.append(data["bvid"])
        if owner.get("name"):
            line.append(owner["name"])
        views = _human(stat.get("view"))
        if views:
            line.append("%s播放" % views)
        danmaku = _human(stat.get("danmaku"))
        if danmaku:
            line.append("%s弹幕" % danmaku)
        if _date(data.get("pubdate")):
            line.append(_date(data.get("pubdate")))

        flags, lines = ["自动"], ["#".join(episodes)]

        season = data.get("ugc_season") or {}
        tracks = self._season_tracks(season, data) if _int(season.get("id")) else ""
        if tracks:
            total = len(tracks.split("#"))
            name = _clean(season.get("title")) or "未命名合集"
            line.append("合集《%s》%d 集" % (name, total))
            if len(episodes) == 1:
                # 单 P 没有选集可言，合集直接铺进「自动」线路，当前集留首位
                lines[0] = _merge_current(episodes, tracks)
            else:
                line.append("共 %d 个分P" % len(episodes))
                flags.append("合集 %d 集" % total)
                lines.append(tracks)
        elif len(episodes) > 1:
            line.append("共 %d 个分P" % len(episodes))

        content = "\n".join(filter(None, [" · ".join(line), desc]))

        item = {
            "vod_id": "av:" + aid,
            "vod_name": _clean(data.get("title")),
            "vod_pic": _cover(data.get("pic")),
            "vod_year": _year(data.get("pubdate")),
            "vod_remarks": _clock(data.get("duration")),
            "vod_director": owner.get("name") or "",
            "vod_content": content,
        }
        return self._add_qualities(item, flags, lines)

    def _season_tracks(self, season, seed):
        """sections 通常就是全部，够用不再请求；被截断才翻
        seasons_archives_list，四页封顶。"""
        episodes, seen = [], set()
        for section in season.get("sections") or []:
            for entry in section.get("episodes") or []:
                _push_episode(episodes, seen, entry)

        total = _int(season.get("ep_count"))
        mid = _int(season.get("mid")) or _int((seed.get("owner") or {}).get("mid"))
        season_id = _int(season.get("id"))
        if total > len(episodes) and mid and season_id:
            for page in range(1, 5):
                time.sleep(0.2)
                try:
                    data = self._api("/x/polymer/web-space/seasons_archives_list", {
                        "mid": mid, "season_id": season_id,
                        "page_num": page, "page_size": 30,
                        "sort_reverse": "false",
                    }, "%s/%d/favlist" % (SPACE, mid))
                except Exception:
                    break
                rows = data.get("archives") or []
                if not rows:
                    break
                before = len(episodes)
                for entry in rows:
                    _push_episode(episodes, seen, entry)
                # 这一页全是见过的，说明到底了；没到 ep_count 也认了
                if len(episodes) == before or len(episodes) >= total:
                    break
        return "#".join(episodes[:120])

    # ---- 数据 ----------------------------------------------------------

    @staticmethod
    def _item(entry):
        duration = _seconds(entry.get("duration"))
        remark = entry.get("duration_text") or _clock(duration) or str(entry.get("duration") or "")
        return {
            "vod_id": "av:" + str(entry.get("aid") or ""),
            "vod_name": _clean(entry.get("title")),
            "vod_pic": _cover(entry.get("pic") or entry.get("cover")),
            "vod_remarks": remark,
            "vod_year": _year(entry.get("pubdate")),
            "style": {"type": "rect", "ratio": 0.75},
        }

    # ---- 清晰度线路 ----------------------------------------------------

    def _add_qualities(self, item, flags, lines):
        """按 dash 里真实存在的轨道铺线路。accept_quality 会谎报 4K，不能信。"""
        item["vod_play_from"] = "$$$".join(flags)
        item["vod_play_url"] = "$$$".join(lines)

        first = lines[0].split("#")[0].split("$", 1)[-1]
        qualities, preview, reason = [], False, ""
        try:
            dash, preview = self._probe(first)
            qualities = sorted({_int(stream.get("id"))
                                for stream in dash.get("video") or []
                                if _usable(stream, "video")}, reverse=True)
        except Exception as error:
            qualities, preview, reason = [], False, str(error)

        if preview:
            item["vod_content"] = item.get("vod_content", "") + \
                "\n\n本片为会员专享，未登录只能试看片段；把 COOKIE 填进本文件顶部后即可完整播放。"
        elif reason:
            # 探测失败不等于不能播，说清楚，别让线路栏光秃秃一条
            item["vod_content"] = item.get("vod_content", "") + "\n\n" + (
                "本片受 DRM 保护，B 站只允许官方客户端解密播放。"
                if "DRM" in reason else
                "暂时取不到清晰度档位，仍可直接播放；需要手动选画质请稍后重进详情页。")

        if not qualities:
            return item

        # 只给主线路铺档位，否则线路数按 分P × 画质 膨胀，遥控器按不过来
        out_flags = [flags[0]]
        out_lines = [lines[0]]
        for value in qualities:
            name = QUALITY_NAMES.get(value, "清晰度 %d" % value)
            out_flags.append(name)
            out_lines.append("#".join("%s@qn=%d" % (episode, value)
                                      for episode in lines[0].split("#")))

        item["vod_play_from"] = "$$$".join(out_flags + flags[1:])
        item["vod_play_url"] = "$$$".join(out_lines + lines[1:])
        return item

    def _probe(self, play_id):
        """一次拿到轨道表和「是不是试看」。会员片 dash 是空的，durl 里却有一小段。"""
        data = self._play_data(play_id, PROBE_QN)
        if not isinstance(data, dict):
            raise RuntimeError("探测不到可播放轨道")
        if _is_drm(data):
            raise RuntimeError("该片受 DRM 保护，无法获取清晰度档位")
        return (data.get("dash") or {}), bool(data.get("is_preview"))

    # ---- 播放 ----------------------------------------------------------

    def playerContent(self, flag, id, vipFlags):
        play_id, quality = self._split_quality(id)
        danmaku = self._danmaku_url(play_id)
        data = self._play_data(play_id, quality or self.qn)
        if _is_drm(data):
            raise RuntimeError("该片受 DRM 保护，只能在哔哩哔哩官方客户端观看")
        dash = data.get("dash") or {}

        if dash:
            video = self._pick_video(dash.get("video"), quality)
            audio = self._pick_audio(dash.get("audio"))
            if video and audio:
                params = self._proxy_params(play_id, quality)
                return self._with_danmaku({
                    "parse": 0,
                    "url": self.proxyUrl(params),
                    # 必须与 MimeTypes.APPLICATION_MPD 一致，否则走渐进式解析
                    "format": "application/dash+xml",
                    "header": self._player_headers(play_id),
                }, danmaku)

        # DASH 拿不到时退到 durl，单文件带音轨，代价是最高 720P
        plain = self._play_data(play_id, quality or self.qn, durl=True)
        if _is_drm(plain):
            raise RuntimeError("该片受 DRM 保护，只能在哔哩哔哩官方客户端观看")
        parts = (plain or {}).get("durl") or []
        if not parts or not parts[0].get("url"):
            raise RuntimeError(self._no_stream_reason(play_id))
        if len(parts) > 1:
            # 分段存储的老视频，每段独立 mp4，拼不出可拖动的时间轴
            raise RuntimeError("这段视频被 B 站切成 %d 段存储，播放器无法连续播放；"
                               "可在哔哩哔哩官方客户端观看完整版" % len(parts))
        return self._with_danmaku({
            "parse": 0,
            "url": _media_url(parts[0]) or parts[0]["url"],
            "format": "video/mp4",
            "header": self._player_headers(play_id),
        }, danmaku)

    @staticmethod
    def _no_stream_reason(play_id):
        return "B 站没有返回可播放地址（可能需要登录、付费或已下架）"

    @staticmethod
    def _split_quality(ident):
        """剥掉 @qn= 后缀，剩下的才是资源 ID。"""
        text = str(ident or "").strip()
        base, _, suffix = text.partition("@qn=")
        quality = _int(suffix) if suffix else 0
        if suffix and quality <= 0:
            raise RuntimeError("清晰度编号无效：%r" % ident)
        return base, quality

    @staticmethod
    def _player_headers(play_id):
        """播放器只要这三样。CDN 没有 Referer 直接 403，带 Cookie 反而多余。"""
        aid = str(play_id or "").partition("_")[0]
        referer = WEB + "/video/av" + aid if aid.isdigit() else WEB + "/"
        return json.dumps({
            "User-Agent": UA, "Referer": referer, "Origin": WEB,
        })

    @staticmethod
    def _proxy_params(play_id, quality):
        aid, _, cid = play_id.partition("_")
        return {"kind": "mpd", "t": "ugc", "a": aid, "c": cid, "q": str(quality or 0)}

    def _danmaku_url(self, play_id):
        """弹幕地址交给 playerContent 的 danmaku 字段。dm/list.so 回的是
        raw deflate，客户端解不开，统一走 proxy 转成明文 XML。"""
        aid, _, cid = play_id.partition("_")
        if not cid.isdigit():
            return ""
        if cid == "0":
            # 合集翻页来的条目不带 cid，跟取流共用缓存补一次
            try:
                cid = self._first_cid(aid)
            except Exception:
                return ""
        return self.proxyUrl({"kind": "danmaku", "cid": cid}) if cid else ""

    @staticmethod
    def _with_danmaku(result, url):
        """没 cid 就别写这个键，否则弹幕列表里会多一条没名字的空条目。"""
        if url:
            result["danmaku"] = url
        return result

    def _play_data(self, play_id, quality, durl=False):
        """取一条视频的完整播放响应。

        durl=True 对应 fnval=1，那种请求不返回 dash，不能拿来探测画质。
        playerContent 和 proxy 会前后各要一次，留 5 分钟缓存挡掉重复请求。
        """
        key = "%s|%s|%s" % (play_id, quality, "durl" if durl else "dash")
        hit = self._play_cache.get(key)
        if hit and time.time() - hit[0] < 300:
            return hit[1]

        aid, _, cid = play_id.partition("_")
        if not aid.isdigit() or not cid.isdigit():
            raise RuntimeError("播放 ID 格式不对：%r" % play_id)
        if cid == "0":
            cid = self._first_cid(aid)

        data = self._api("/x/player/playurl", {
            "avid": aid, "cid": cid, "qn": quality,
            # fnval=1 只给 durl，4048 才给 dash
            "fnval": "1" if durl else "4048",
            "fnver": "0", "fourk": "1", "try_look": "1",
        }, WEB + "/video/av" + aid)

        if len(self._play_cache) > 24:
            self._play_cache.clear()
        self._play_cache[key] = (time.time(), data)
        return data

    def _dash(self, play_id, quality):
        return (self._play_data(play_id, quality).get("dash") or {})

    def _first_cid(self, aid):
        key = "cid|" + aid
        hit = self._play_cache.get(key)
        if hit and time.time() - hit[0] < 1800:
            return hit[1]
        view = self._api("/x/web-interface/view", {"aid": aid}, WEB + "/video/av" + aid)
        pages = view.get("pages") or []
        if not pages:
            raise RuntimeError("视频不存在或已下架：%s" % aid)
        cid = str(pages[0].get("cid") or "")
        self._play_cache[key] = (time.time(), cid)
        return cid

    @staticmethod
    def _pick_video(streams, quality=0):
        """按用户选的编号卡死，再按 H.264 > HEVC > AV1 挑。
        不能「avc 优先，没有就随便」——挑到 AV1 盒子黑屏还没有任何报错。"""
        pool = [stream for stream in (streams or []) if _usable(stream, "video")]
        if not pool:
            return {}
        if quality:
            exact = [stream for stream in pool if _int(stream.get("id")) == quality]
            if exact:
                pool = exact
            else:
                lower = [stream for stream in pool if _int(stream.get("id")) <= quality]
                pool = lower or pool
        pool.sort(key=lambda stream: (-_int(stream.get("id")),
                                      _codec_rank(stream),
                                      -_int(stream.get("bandwidth"))))
        return pool[0]

    @staticmethod
    def _pick_audio(streams):
        """只挑 AAC，杜比 / Hi-Res 那几条 ExoPlayer 会报解码器缺失。"""
        pool = [stream for stream in (streams or [])
                if _usable(stream, "audio")
                and str(stream.get("codecs") or "").startswith("mp4a")]
        pool.sort(key=lambda stream: (-_int(stream.get("bandwidth")),))
        return pool[0] if pool else {}

    # ---- 代理 ----------------------------------------------------------

    def proxy(self, params):
        params = params if isinstance(params, dict) else {}
        kind = str(params.get("kind") or "")
        if kind == "mpd":
            return self._proxy_mpd(params)
        if kind == "danmaku":
            return self._proxy_danmaku(str(params.get("cid") or ""))
        return [404, "text/plain; charset=utf-8", "未知的代理请求", {}]

    def _proxy_mpd(self, params):
        quality = _int(params.get("q"))
        aid, cid = str(params.get("a") or ""), str(params.get("c") or "")
        if not aid or not cid:
            return [400, "text/plain; charset=utf-8", "缺少 aid 或 cid", {}]

        dash = self._dash("%s_%s" % (aid, cid), quality or self.qn)
        video = self._pick_video(dash.get("video"), quality)
        audio = self._pick_audio(dash.get("audio"))
        if not video or not audio:
            return [502, "text/plain; charset=utf-8", "B 站没有返回 DASH 流", {}]
        text = self._build_mpd(video, audio, dash.get("duration"))
        return [200, "application/dash+xml", text, {"Cache-Control": "no-store"}]

    def _proxy_danmaku(self, cid):
        """dm/list.so 回的是 raw deflate（没有 zlib 头），这里解成明文 XML。
        同一集 5 分钟内重复请求直接吃缓存。"""
        cid = str(cid or "")
        if not cid:
            return [400, "text/plain; charset=utf-8", "缺少 cid", {}]

        hit = self._dm_cache.get(cid)
        if hit and time.time() - hit[0] < 300:
            return [200, "text/xml; charset=utf-8", hit[1],
                    {"Cache-Control": "no-store"}]

        text = ""
        for attempt in (0, 1):
            # -412 是秒级封禁，隔两秒再问就恢复；间隔太短等于没退避
            if attempt:
                time.sleep(2)
            resp = self.net.req("%s/x/v1/dm/list.so?oid=%s" % (API, cid), {
                "headers": self._headers(WEB), "timeout": TIMEOUT,
                "buffer": 3, "callTimeout": TIMEOUT,
            })
            if resp.get("error"):
                continue
            raw = resp.get("content") or b""
            if isinstance(raw, str):
                raw = raw.encode("utf-8", "ignore")
            try:
                raw = zlib.decompress(raw, -15)
            except zlib.error:
                pass
            text = raw.decode("utf-8", "ignore")
            # 被风控时回的是 {"code":-412} 的 JSON，给播放器只会静默无弹幕
            if "<i>" in text:
                break
            text = ""

        if not text:
            return [502, "text/plain; charset=utf-8",
                    "B 站限制了弹幕请求（-412），过一两分钟再进这一集", {}]
        if len(self._dm_cache) > 8:
            self._dm_cache.clear()
        self._dm_cache[cid] = (time.time(), text)
        return [200, "text/xml; charset=utf-8", text, {"Cache-Control": "no-store"}]

    # ---- MPD --------------------------------------------------------

    @staticmethod
    def _build_mpd(video, audio, duration):
        length = _decimal(duration)
        attrs = {
            "xmlns": "urn:mpeg:dash:schema:mpd:2011",
            "profiles": "urn:mpeg:dash:profile:isoff-on-demand:2011",
            "type": "static",
            "minBufferTime": "PT1.5S",
        }
        # 写 PT0S 播放器会认定总长为零，进度条直接废掉
        if length:
            attrs["mediaPresentationDuration"] = "PT%sS" % length
        root = ET.Element("MPD", attrs)

        period_attrs = {"start": "PT0S"}
        if length:
            period_attrs["duration"] = "PT%sS" % length
        period = ET.SubElement(root, "Period", period_attrs)

        for kind, stream in (("video", video), ("audio", audio)):
            adaptation = ET.SubElement(period, "AdaptationSet", {
                "mimeType": kind + "/mp4",
                "contentType": kind,
                "subsegmentAlignment": "true",
                "subsegmentStartsWithSAP": "1",
                "startWithSAP": "1",
            })
            rep_attrs = {
                "id": "%s_%d" % (kind, _int(stream.get("id"))),
                "codecs": str(stream.get("codecs") or ""),
                "bandwidth": str(_int(stream.get("bandwidth"))),
            }
            if kind == "video":
                width = _int(stream.get("width"))
                height = _int(stream.get("height"))
                if width > 0:
                    rep_attrs["width"] = str(width)
                if height > 0:
                    rep_attrs["height"] = str(height)
                rate = str(stream.get("frameRate") or stream.get("frame_rate") or "")
                # frameRate 只认整数、小数或 "30000/1001"，别的会让 MPD 失效
                if FRAME_RE.match(rate):
                    rep_attrs["frameRate"] = rate
            else:
                rate = _int(stream.get("audioSamplingRate") or stream.get("sampleRate"))
                if rate > 0:
                    rep_attrs["audioSamplingRate"] = str(rate)

            representation = ET.SubElement(adaptation, "Representation", rep_attrs)
            # ET 已经会把 & 转成 &amp;，再手动 replace 就是双重转义
            ET.SubElement(representation, "BaseURL").text = _media_url(stream)
            segment = ET.SubElement(representation, "SegmentBase", {
                "indexRange": _segment(stream, "indexRange"),
                "indexRangeExact": "true",
            })
            ET.SubElement(segment, "Initialization", {
                "range": _segment(stream, "Initialization")})
        return ET.tostring(root, encoding="unicode", method="xml")

    # ---- 网络 ----------------------------------------------------------

    def _headers(self, referer=None):
        headers = {
            "User-Agent": UA,
            "Referer": referer or WEB + "/",
            "Origin": WEB,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        cookie = self._cookie()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _cookie(self):
        """buvid3 必须从 finger/spi 取真的，自己拼的后面每个请求都会 412。

        这里不能走 _api()/_headers()——那两个都会回头问 _cookie()，会递归爆栈。
        """
        if self._cookie_ready:
            return self._buvid
        self._cookie_ready = True

        b3 = b4 = ""
        try:
            resp = self.net.req(API + "/x/frontend/finger/spi", {
                "headers": {"User-Agent": UA, "Referer": WEB + "/"},
                "timeout": TIMEOUT,
                "callTimeout": TIMEOUT,
            })
            body = resp.get("content") or ""
            if isinstance(body, bytes):
                body = body.decode("utf-8", "ignore")
            data = (json.loads(body).get("data") or {}) if body else {}
            b3 = str(data.get("b_3") or "")
            b4 = str(data.get("b_4") or "")
        except Exception:
            b3 = b4 = ""

        # 用户那条 cookie 排前面：同名键服务端只认第一个，别被我们补的盖掉
        parts = []
        mine = self.cookie or ""
        if mine:
            parts.append(mine)
        if b3 and "buvid3=" not in mine:
            parts.append("buvid3=" + b3)
        if b4 and "buvid4=" not in mine:
            parts.append("buvid4=" + b4)
        self._buvid = "; ".join(parts)
        return self._buvid

    def _payload(self, path, params=None, referer=None, signed=False):
        """返回原始 JSON：多数接口数据在 data，影视那批在 result，调用方自己取。"""
        query = dict(params or {})
        if signed:
            query = self._sign(query)
        options = {
            "headers": self._headers(referer),
            "timeout": TIMEOUT,
            "callTimeout": TIMEOUT,
            "redirect": 1,
        }
        if query:
            options["params"] = {key: str(value) for key, value in query.items()}

        resp = self.net.req(API + path, options)
        if resp.get("error"):
            raise RuntimeError("%s 请求失败：%s" % (path, resp["error"]))
        code = resp.get("code")
        if code == 412:
            raise RuntimeError("被 B 站风控拦截（412），稍后再试或换一条 COOKIE")
        if code != 200:
            raise RuntimeError("%s 返回 HTTP %s" % (path, code))

        body = resp.get("content") or ""
        if isinstance(body, bytes):
            body = body.decode("utf-8", "ignore")
        try:
            payload = json.loads(body)
        except ValueError:
            raise RuntimeError("%s 返回的不是 JSON，可能被风控" % path) from None

        error = payload.get("code")
        if error != 0:
            if error == -403:
                # 签名过期或表抄错，丢掉缓存的 mixin 让调用方重试
                self._mixin, self._mixin_at = "", 0.0
            raise RuntimeError("B 站 %s 返回 %s：%s"
                               % (path, error, payload.get("message") or "未知错误"))
        return payload

    def _api(self, path, params=None, referer=None, signed=False):
        payload = self._payload(path, params, referer, signed)
        return payload.get("data") or {}

    # ---- WBI --------------------------------------------------------

    def _sign(self, params):
        """wbi 签名：参数按名排序、值里去掉 !'()*、拼上 mixin_key 取 md5。"""
        values = {key: str(value) for key, value in params.items()}
        values["wts"] = str(int(time.time()))
        cleaned = {key: "".join(ch for ch in value if ch not in "!'()*")
                   for key, value in sorted(values.items())}
        query = urlencode(cleaned)
        cleaned["w_rid"] = hashlib.md5((query + self._mixin_key()).encode()).hexdigest()
        return cleaned

    def _mixin_key(self):
        if self._mixin and time.time() < self._mixin_at:
            return self._mixin
        data = self._nav()
        img = str(data.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
        sub = str(data.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
        if not img or not sub or len(img) + len(sub) < 64:
            raise RuntimeError("拿不到 wbi 密钥，B 站接口可能已变")
        self._mixin = "".join((img + sub)[index] for index in MIXIN_TAB)[:32]
        self._mixin_at = time.time() + WBI_TTL
        return self._mixin

    def _nav(self):
        """未登录时 nav 返回 code=-101，但 wbi_img 照样给，所以这里不用 _api 的校验。"""
        options = {
            "headers": self._headers(WEB + "/"),
            "timeout": TIMEOUT,
            "callTimeout": TIMEOUT,
        }
        resp = self.net.req(API + "/x/web-interface/nav", options)
        if resp.get("error"):
            raise RuntimeError("获取 wbi 密钥失败：%s" % resp["error"])
        if resp.get("code") == 412:
            raise RuntimeError("被 B 站风控拦截（412），稍后再试")
        body = resp.get("content") or ""
        if isinstance(body, bytes):
            body = body.decode("utf-8", "ignore")
        try:
            payload = json.loads(body)
        except ValueError:
            raise RuntimeError("获取 wbi 密钥失败：返回的不是 JSON") from None
        return (payload.get("data") or {}).get("wbi_img") or {}

    # ---- 搜索 ----------------------------------------------------------

    def _search(self, keyword, page, order, duration, tids):
        if not keyword:
            raise RuntimeError("关键词为空")
        params = {
            "search_type": "video",
            "keyword": keyword,
            "page": page,
            # 接口默认 20 条，能要到 50：同样翻几页内容翻倍，请求数减半
            "page_size": PAGE_SIZE,
            "order": order or "totalrank",
        }
        if duration:
            params["duration"] = duration
        if tids:
            params["tids"] = tids
        path, referer = "/x/web-interface/search/type", SEARCH + "/all?keyword=" + quote(keyword)
        last = None
        for attempt in (0, 1):
            self._pace()
            try:
                return self._api(path, params, referer, signed=True)
            except RuntimeError as error:
                last = error
                text = str(error)
                if "412" in text:
                    # 秒级封禁，退开几秒就恢复；连着补刀只会封更久
                    time.sleep(4)
                elif "-403" not in text:
                    raise
        raise last

    def _pace(self):
        gap = time.time() - self._last_search
        if gap < SEARCH_GAP:
            time.sleep(SEARCH_GAP - gap)
        self._last_search = time.time()

    # ---- 其他 ----------------------------------------------------------

    def isVideoFormat(self, url):
        text = str(url or "")
        return any(token in text for token in (".mpd", ".m4s", ".mp4"))

    def manualVideoCheck(self):
        return False


def _push_episode(episodes, seen, entry):
    """合集里的一集转成播放串。没有 cid 就写 0，播放时再按 aid 现查。"""
    if not isinstance(entry, dict):
        return
    aid = _int(entry.get("aid"))
    if not aid:
        return
    bvid = str(entry.get("bvid") or "")
    mark = bvid or ("a%d" % aid)
    if mark in seen:
        return
    seen.add(mark)
    cid = _int(entry.get("cid")) or _int((entry.get("page") or {}).get("cid"))
    title = _clean(entry.get("title")) or bvid or str(aid)
    episodes.append("%s$%d_%d" % (_safe(title), aid, cid))


def _merge_current(episodes, tracks):
    """当前这集提到最前，其余按合集顺序接上。合集顺序是 UP 主排的，
    但用户点进来的是某一集，默认播第一集就播错了片。"""
    aid = episodes[0].split("$", 1)[-1].split("_")[0]
    merged = [episodes[0]]
    for entry in tracks.split("#"):
        if entry.split("$", 1)[-1].split("_")[0] == aid:
            continue
        if entry and entry not in merged:
            merged.append(entry)
    return "#".join(merged)


def _safe(name):
    """$ 与 # 是播放串分隔符，出现在分P名里要换掉。"""
    return str(name).replace("$", "＄").replace("#", "＃").strip()
