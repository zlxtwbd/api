# -*- coding: utf-8 -*-
"""
豆瓣导航 Spider（CatVod / TVBox）
==================================================
相对旧版的优化点：
1. [BUG] 分页推进 offset += count 改为按实际返回条数推进，修复漏数据
2. [BUG] 空筛选拼出 ",," / ",,," 脏 tags，现过滤空值
3. [BUG] episodes_info 从 recent_hot 榜单按 start 取、命中率极低 -> 改为缓存式按需补全
4. [BUG] hot_anime 双接口共用 start 导致翻页错位 -> 改为电影优先、剧集补足
5. [BUG] anime 缺少翻页循环 -> 统一走公共分页取数
6. [BUG] hot_anime 默认值与筛选 UI 首项不一致
7. [性能] 缓存加容量上限 + TTL 清理 + 线程锁；请求加重试
8. [功能] 补全 detailContent / searchContent / 首页推荐
9. [健壮] 路径导入、基类遮蔽、异常兜底、筛选 key 中英文兼容
"""
import os
import re
import sys
import json
import time
import threading
from collections import OrderedDict
import ssl
from urllib.parse import urlparse, parse_qs, unquote  # 去广告引擎依赖
from urllib.parse import quote, urlencode

import requests

try:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except Exception:  # pragma: no cover
    HTTPAdapter = None
    Retry = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.abspath(os.path.join(BASE_DIR, '..')) not in sys.path:
    sys.path.append(os.path.abspath(os.path.join(BASE_DIR, '..')))

try:
    from base.spider import Spider as BaseSpider
except Exception:  # 便于脱离框架单独做语法/逻辑测试
    class BaseSpider(object):
        pass


# ================= 常量 =================
DOUBAN_HOST = "https://frodo.douban.com/api/v2"
DOUBAN_API_KEY = "0ac44ae016490db2204ce0a042db2916"
DOUBAN_UA = ("Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/53.0.2785.143 Safari/537.36 "
             "MicroMessenger/7.0.9.501 NetType/WIFI MiniProgramEnv/Windows WindowsWechat")
DOUBAN_REFERER = "https://servicewechat.com/wx2f9b06c1de1ccfca/84/page-frame.html"

PIC_SUFFIX = ('@Referer=https://api.douban.com/'
              '@User-Agent=Mozilla/5.0%20(Windows%20NT%2010.0;%20Win64;%20x64)%20'
              'AppleWebKit/537.36%20(KHTML,%20like%20Gecko)%20Chrome/122.0.0.0%20Safari/537.36')

M_DOUBAN_HOST = "https://m.douban.com/rexxar/api/v2"
M_DOUBAN_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
M_DOUBAN_REFERER_TV = "https://movie.douban.com/tv/"
M_DOUBAN_REFERER_MOVIE = "https://movie.douban.com/explore"
# 去广告引擎: 本地清洗服务不可用/清洗失败时是否回退 file:// 预清洗兜底(False=回原链)
ADFREE_FILE_FALLBACK = False
# ============ ★ wsyzy 播放源(混合源核心) ============
# 豆瓣只管 UI/封面/搜索(海报由 FongMi 壳直连 img1.doubanio.com 加载, 本网络可达显示正常);
# 播放按片名查 wsyzy.cc 取真实 m3u8 直链(无水印资源站点 API)。
WSY_SITE = "https://wsyzy.cc"
WSY_API = WSY_SITE + "/api.php/provide/vod/"
WSY_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
          'AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36')
WSY_TIMEOUT = 8

PAGE_SIZE = 40          # 对外每页条数
API_PAGE_LIMIT = 20     # 豆瓣单页安全上限，超过可能被截断
MAX_LOOP = 8            # 单次取数最多翻几页


# ================= 筛选配置 =================
def _opts(items, label="全部"):
    return [{"n": label, "v": ""}] + [{"n": i, "v": i} for i in items]


_MOVIE_GENRES = ["喜剧", "爱情", "动作", "科幻", "动画", "悬疑", "犯罪", "惊悚",
                 "冒险", "音乐", "历史", "奇幻", "恐怖", "战争", "传记", "歌舞",
                 "武侠", "情色", "灾难", "西部", "纪录片", "短片"]
_MOVIE_REGIONS = ["华语", "欧美", "韩国", "日本", "中国大陆", "美国", "中国香港",
                  "中国台湾", "英国", "法国", "德国", "意大利", "西班牙", "印度",
                  "泰国", "俄罗斯", "加拿大", "澳大利亚", "爱尔兰", "瑞典", "巴西", "丹麦"]
_YEARS = ["2026", "2025", "2024", "2023", "2022", "2021", "2020", "2019",
          "2020年代", "2010年代", "2000年代", "90年代", "80年代", "70年代", "60年代", "更早"]
_TV_GENRES = ["喜剧", "爱情", "悬疑", "动画", "武侠", "古装", "家庭", "犯罪",
              "科幻", "恐怖", "历史", "战争", "动作", "冒险", "传记", "剧情",
              "奇幻", "惊悚", "灾难", "歌舞", "音乐"]
_TV_PLATFORMS = ["腾讯视频", "爱奇艺", "优酷", "湖南卫视", "Netflix", "HBO",
                 "BBC", "NHK", "CBS", "NBC", "tvN"]
_SHOW_GENRES = ["真人秀", "脱口秀", "音乐", "喜剧", "纪实"]
_SHOW_REGIONS = ["中国大陆", "韩国", "港台", "欧美"]
_ANIME_GENRES = ["动画", "日本动画", "国产动画", "欧美动画", "剧场版", "番剧"]
_ANIME_REGIONS = ["日本", "中国大陆", "美国", "欧美"]

_SORT_MOVIE = [{"n": "热度", "v": "U"}, {"n": "评分", "v": "S"}, {"n": "时间", "v": "R"}]
_SORT_SHOW = [{"n": "近期热度", "v": "U"}, {"n": "综合排序", "v": "T"},
              {"n": "首播时间", "v": "R"}, {"n": "高分优先", "v": "S"}]

FILTER_CONFIG = {
    "hot_movie": [
        {"key": "sort", "name": "排序", "value": [
            {"n": "热度", "v": "recommend"},
            {"n": "最新", "v": "time"},
            {"n": "评分", "v": "rank"},
        ]},
        {"key": "area", "name": "地区", "value": [
            {"n": "全部", "v": "全部"},
            {"n": "华语", "v": "华语"},
            {"n": "欧美", "v": "欧美"},
            {"n": "韩国", "v": "韩国"},
            {"n": "日本", "v": "日本"},
        ]},
    ],
    "tv_hot": [
        {"key": "type", "name": "分类", "value": [
            {"n": "综合", "v": "tv_hot"},
            {"n": "国产剧", "v": "tv_domestic"},
            {"n": "欧美剧", "v": "tv_american"},
            {"n": "日剧", "v": "tv_japanese"},
            {"n": "韩剧", "v": "tv_korean"},
            {"n": "动画", "v": "tv_animation"},
        ]},
    ],
    "anime_hot": [
        {"key": "type", "name": "分类", "value": [
            {"n": "全部", "v": "全部"},
            {"n": "国漫", "v": "国漫"},
            {"n": "日漫", "v": "日漫"},
            {"n": "美漫", "v": "美漫"},
            {"n": "韩漫", "v": "韩漫"},
            {"n": "港漫", "v": "港漫"},
            {"n": "台漫", "v": "台漫"},
        ]},
    ],
    "show_hot": [
        {"key": "type", "name": "分类", "value": [
            {"n": "综合", "v": "show_hot"},
            {"n": "国内", "v": "show_domestic"},
            {"n": "国外", "v": "show_foreign"},
        ]},
    ],
    "movie_filter": [
        {"key": "genre", "name": "类型", "value": _opts(_MOVIE_GENRES, "全部类型")},
        {"key": "region", "name": "地区", "value": _opts(_MOVIE_REGIONS, "全部地区")},
        {"key": "year", "name": "年代", "value": _opts(_YEARS, "全部年代")},
        {"key": "sort", "name": "排序", "value": _SORT_MOVIE},
    ],
    "tv_filter": [
        {"key": "genre", "name": "类型", "value": _opts(_TV_GENRES, "全部类型")},
        {"key": "region", "name": "地区", "value": _opts(_MOVIE_REGIONS, "全部地区")},
        {"key": "year", "name": "年代", "value": _opts(_YEARS, "全部年代")},
        {"key": "platform", "name": "平台", "value": _opts(_TV_PLATFORMS, "全部平台")},
        {"key": "sort", "name": "排序", "value": _SORT_MOVIE},
    ],
    "show_filter": [
        {"key": "genre", "name": "类型", "value": _opts(_SHOW_GENRES, "全部类型")},
        {"key": "region", "name": "地区", "value": _opts(_SHOW_REGIONS, "全部地区")},
        {"key": "year", "name": "年代", "value": _opts(_YEARS[:8], "全部年代")},
        {"key": "sort", "name": "排序", "value": _SORT_SHOW},
    ],
    "anime": [
        {"key": "genre", "name": "类型", "value": _opts(_ANIME_GENRES, "全部类型")},
        {"key": "region", "name": "地区", "value": _opts(_ANIME_REGIONS, "全部地区")},
        {"key": "year", "name": "年代", "value": _opts(_YEARS[:8], "全部年代")},
        {"key": "sort", "name": "排序", "value": _SORT_SHOW},
    ],
    "hot_anime": [
        {"key": "slug", "name": "榜单", "value": [
            {"n": "全部动漫", "v": "all"},
            {"n": "最新动漫", "v": "anime_recent"},
            {"n": "近期热门动漫", "v": "anime_hot"},
            {"n": "高分动漫", "v": "anime_best"},
        ]},
    ],
    "top_250": [
        {"key": "genre", "name": "类型", "value": _opts(_MOVIE_GENRES, "全部类型")},
        {"key": "region", "name": "地区", "value": _opts(_MOVIE_REGIONS, "全部地区")},
        {"key": "year", "name": "年代", "value": _opts(_YEARS, "全部年代")},
        {"key": "sort", "name": "排序", "value": [
            {"n": "豆瓣排名", "v": ""}, {"n": "评分", "v": "S"}, {"n": "时间", "v": "R"},
        ]},
    ],
}

# 地区聚合：华语 / 欧美
_WESTERN = {"美国", "英国", "法国", "德国", "意大利", "西班牙", "加拿大",
            "澳大利亚", "爱尔兰", "瑞典", "丹麦", "巴西", "俄罗斯"}
_CHINESE = {"中国大陆", "中国香港", "中国台湾", "华语"}

# 筛选 key 兼容：旧版 show_filter / anime 用的是中文 key
_KEY_ALIAS = {
    'genre': ('genre', '类型'),
    'region': ('region', '地区'),
    'year': ('year', '年代'),
}


class _TTLCache(object):
    """带容量上限 + TTL 的线程安全缓存（避免长期运行内存无限增长）"""

    def __init__(self, max_size=64, ttl=600):
        self.max_size = max_size
        self.ttl = ttl
        self._data = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key):
        with self._lock:
            hit = self._data.get(key)
            if not hit:
                return None
            if time.time() - hit['time'] > self.ttl:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return hit['data']

    def set(self, key, data):
        with self._lock:
            self._data[key] = {'data': data, 'time': time.time()}
            self._data.move_to_end(key)
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)


# ============ ★ 去广告引擎(实时清洗代理, 搬运自无水印版, 逐字节还原) ============
_IMG_CACHE = {}                # 封面中继图缓存

# ============================================================
# 实时清洗代理去广告引擎（自包含，零第三方依赖）
#   ① M3U8Cleaner    去广告引擎：四层判据（时长指纹 / 多帧网格择优 / ts 计数器链 /
#                    广告路径关键词）+ 组扩展(带命中占比门槛) + 广告连播块长吸附
#                    + 截断残片噪声剔除(防误删正片末片) + 15% 保护线
#                    + 分片 / KEY URI 绝对化
#   ② _http_get      抓取通道（纯标准库 urllib，带浏览器 UA）
#   ③ AdFreeCleaner  清洗核心：URL 规范化 + 单条缓存 + 多码率变体下钻 + 结果二次校验
#   ④ AdFreeProxy    实时清洗代理：脚本内自建 127.0.0.1 ThreadingHTTPServer，
#                    播放器取流时才实时拉源站清洗；失败 302 回源原地址
# ============================================================
# 抓取默认 UA（部分源站 WAF 对非浏览器 UA 直接 403）
ADFREE_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
             'AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36')


# ============================================================
# ① 去广告引擎（自包含，独立可复用）
# ============================================================
# 四层判据(并集, 各判据自带高门槛):
#   ① 时长指纹 AD_DUR_FP —— dytt 系广告插播系统固定模板(权威)
#   ② 多帧网格择优 —— 按候选帧率集自动择优, 兼容 23.976/25/29.97/30/60
#   ③ ts 计数器连续性 —— 文件名尾数字按 DISCONTINUITY 组建链, 链外组=广告
#   ④ 广告路径关键词 —— /adjump/ 等固定广告子目录
# 附加: 组扩展(命中组整组删) + 广告连播块长吸附 + 截断残片噪声剔除 + 15% 保护线
class M3U8Cleaner(object):
    # ---------- 广告时长指纹(主判据) ----------
    AD_DUR_FP = (
        (5.567, 2.933, 5.7, 3.333, 1.533),       # FP1 主广告段(5 ts ≈ 19s)
        (6.667, 2.133, 3.233, 3.733),             # FP2 副广告段(4 ts ≈ 15.8s)
        (4.6, 5.533, 0.233),                      # FP3 备用(未观察到)
    )
    FP_TOL = 0.20             # 指纹容差(覆盖 dytt-live FP2 抖动变体, 最大偏差 0.119)
    FRAME_GRID = 0.04         # 25fps 帧网格(仅辅助)
    GRID_TOL = 0.02           # 网格判定容差
    GRID_DISABLE_RATIO = 0.30 # 正片非网格占比超 30% → 非 25fps 源, 停用网格判据
    MAX_REMOVE_RATIO = 0.15   # 保护线: 删除时长占比超 15% 整体放弃
    GROUP_MAX_ITEMS = 12      # 组扩展上限: DISCONTINUITY 组片数不超过此值才整组删
    EXPAND_GROUPS = True      # 组扩展: 命中组整组删(dytt系5片小步切片源, 单片删不净网格对齐碎片)
    GROUP_HIT_RATIO = 0.30    # 组扩展"命中占比"门槛: 组内命中片数占比低于此值不整组删
                              #   防"单片误判"把整组正片拖下水(实测: 西瓜同源某集片尾组)
    BLOCK_TOL = 0.35          # 广告块长吸附容差(连播广告第二支总长≈FP块长±0.35)
    BASE_DUR_WIN = (3.8, 4.2) # 正片基准片窗口(组内含4.0s±0.2片→视为正片组, 拒绝吸附)
    MIN_AD_DUR = 0.5          # 噪声剔除门槛: 组扩展后仍孤立(左右邻居都没命中)的命中片,
                              #   时长不足此值即视为截断残片误判, 不删(实测: 西瓜同源某集
                              #   末片 0.2s=5 帧, 因帧网格判据被当成广告, 属唯一误删点)

    # ---------- 判据2: 多帧网格择优(西瓜同源自适应) ----------
    # 单一 FRAME_GRID=0.04 只适配 25fps; 此处按候选集自动择优, 兼容
    # 23.976 / 29.97 / 59.94 等非整数帧率(用 1001/24000 这类分数表达)
    GRID_CANDIDATES = (0.04, 0.02, 1 / 24.0, 1 / 30.0, 1 / 60.0,
                       1001 / 24000.0, 1001 / 30000.0, 1 / 48.0)
    GRID_FIT_TOL = 0.002      # 单值是否落网格的容差(择优用, 比 GRID_TOL 严)
    GRID_MIN_RATIO = 0.90     # 最佳网格一致性下限, 低于此判为非网格源, 停用网格判据
    # 并列处理: 倍数网格(如 0.04 与 0.02、1/24 与 1/48)天然同等对齐, 会并列居首。
    # 一致性差距在此范围内视为并列, 并列时选"最宽"(周期最大)的网格 —— 宽网格
    # 对随机时长的命中概率更低, 因此最宽的那个才是真实帧率, 也最不易过拟合。
    GRID_TIE = 0.05           # 判定并列的一致性差距
    USE_MULTI_GRID = True     # 多网格择优开关(关则退回单一 FRAME_GRID)

    # ---------- 判据3: ts 计数器连续性(量子同源) ----------
    # 场景: 文件名 = 前缀 + 尾数字(如 0681e378da8000000.ts), 正片编号连续递增,
    #       广告块用另一套基数 → 与主线脱节。对时长无特征的源极有效。
    USE_COUNTER_CHAIN = True  # 计数器判据开关
    COUNTER_MIN_RATIO = 0.50  # 主线(最长连续链)片数占比下限, 低于此放弃(防哈希名误判)

    # ---------- 判据4: 广告路径关键词(暴风同源) ----------
    # 部分源把广告 ts 放固定路径子目录下, 命中即删(零成本, 但站点改路径即失效)
    AD_PATH_KEYWORDS = ('/adjump/', '/ad/', '/ads/', '/gg/')
    USE_PATH_KEYWORD = True   # 路径关键词判据开关

    def __init__(self, debug=False):
        self.debug = debug
        self.last_stats = None  # 最近一次清洗统计(dict), 便于上层记录/排查

    # ==================== 解析 ====================
    @staticmethod
    def _normalize_lines(lines):
        """规范 #EXTINF + #EXT-X-DISCONTINUITY + uri 的相邻顺序(部分源顺序颠倒)"""
        out, i, n = [], 0, len(lines)
        while i < n:
            if (lines[i].startswith('#EXTINF') and i + 2 < n
                    and lines[i + 1] == '#EXT-X-DISCONTINUITY'
                    and not lines[i + 2].startswith('#')):
                out.extend((lines[i + 1], lines[i], lines[i + 2]))
                i += 3
            else:
                out.append(lines[i])
                i += 1
        return out

    def _split_playlist(self, content):
        """m3u8 → (header 头部标签, items 片目, footer 尾部)
        item = {dur 时长, inf EXTINF 行, uri 片地址, disc 前置 DISCONTINUITY, extras 其他标签}"""
        lines = self._normalize_lines(
            [l.strip() for l in content.splitlines() if l.strip()])
        header, i = [], 0
        while i < len(lines) and lines[i].startswith('#EXT') and \
                not lines[i].startswith(('#EXTINF', '#EXT-X-DISCONTINUITY')):
            header.append(lines[i])
            i += 1
        footer, end = [], len(lines)
        for j in range(len(lines) - 1, i - 1, -1):
            if lines[j] == '#EXT-X-ENDLIST':
                footer, end = [lines[j]], j
                break
        items, extras, disc, k = [], [], False, i
        while k < end:
            line = lines[k]
            if line == '#EXT-X-DISCONTINUITY':
                disc = True
            elif line.startswith('#EXTINF') and k + 1 < end and not lines[k + 1].startswith('#'):
                try:
                    dur = float(line.split(':', 1)[1].split(',')[0])
                except (ValueError, IndexError):
                    dur = 0.0
                items.append({'dur': dur, 'inf': line, 'uri': lines[k + 1],
                              'disc': disc, 'extras': extras})
                extras, disc = [], False
                k += 1
            elif line.startswith('#'):
                extras.append(line)
            k += 1
        return header, items, footer

    # ==================== 检测 ====================
    def _detect_duration_fp(self, items):
        """主判据: 广告时长指纹滑窗匹配 → 命中下标集合"""
        durs = [it['dur'] for it in items]
        ads = set()
        for fp in self.AD_DUR_FP:
            n = len(fp)
            for i in range(len(durs) - n + 1):
                if all(abs(durs[i + j] - fp[j]) <= self.FP_TOL for j in range(n)):
                    ads.update(range(i, i + n))
        return ads

    def _grid_is_valid(self, items):
        """网格健康自检: 正片非网格占比过高(非 25fps 源)时停用网格判据
        注: 仅用于 USE_MULTI_GRID=False 的回退路径; 开启多网格时由 _pick_grid 择优。"""
        if not items:
            return False
        non_grid = 0
        for it in items:
            d = it['dur']
            if d > 0:
                q = d / self.FRAME_GRID
                if abs(q - round(q)) > self.GRID_TOL:
                    non_grid += 1
        return non_grid / len(items) <= self.GRID_DISABLE_RATIO

    def _detect_frame_grid(self, items):
        """辅助判据: ts 时长偏离 0.04s 帧网格 → 疑似广告碎片"""
        durs = [it['dur'] for it in items]
        ads = set()
        for i, d in enumerate(durs):
            if d <= 0:
                continue
            q = d / self.FRAME_GRID
            if abs(q - round(q)) > self.GRID_TOL:
                ads.add(i)
        return ads

    # ---------- 判据2: 多帧网格择优 ----------
    def _pick_grid(self, items):
        """在候选帧网格中选真实帧率 → (grid, ratio, ads)
        逻辑: 倍数网格(0.04/0.02、1/24、1/48)天然同等对齐会并列居首,
        并列时取"最宽"者(周期最大 → 对随机时长命中概率最低 → 最可能为真实帧率);
        并列集最佳一致性仍须 ≥ GRID_MIN_RATIO, 否则判为非网格源, 停用该判据。"""
        durs = [it['dur'] for it in items if it['dur'] > 0]
        if len(durs) < 20:                      # 样本过少, 统计不可靠
            return None, 0.0, set()
        ratios = []
        for g in self.GRID_CANDIDATES:
            ok = sum(1 for d in durs if abs(d / g - round(d / g)) * g <= self.GRID_FIT_TOL)
            ratios.append((ok / len(durs), g))
        ratios.sort(reverse=True)
        best_ratio = ratios[0][0]
        if best_ratio < self.GRID_MIN_RATIO:
            return None, best_ratio, set()
        # 并列集(与最高一致性差距在 GRID_TIE 内) → 取最宽网格
        tied = [g for r, g in ratios if best_ratio - r <= self.GRID_TIE]
        best_g = max(tied)
        ads = set()
        for i, it in enumerate(items):
            d = it['dur']
            if d > 0 and abs(d / best_g - round(d / best_g)) * best_g > self.GRID_FIT_TOL:
                ads.add(i)
        return best_g, best_ratio, ads

    # ---------- 判据3: ts 计数器连续性 ----------
    @staticmethod
    def _extract_counter(uri):
        """取文件名尾部连续数字作为内容计数器; 无尾数字返回 None
        ('0681e378da8000123.ts' → 8000123; 'abc.ts' → None)"""
        name = uri.rsplit('/', 1)[-1].strip()
        name = name.split('?', 1)[0]
        if name.lower().endswith('.ts'):
            name = name[:-3]
        m = re.search(r'(\d+)$', name)
        return int(m.group(1)) if m else None

    def _detect_counter_chain(self, items, groups):
        """判据3: 按 DISCONTINUITY 组做"最长递增链"DP, 链外组判为广告。
        逻辑: 正片编号连续(组间 first == 前组 last + 1), 广告块编号与主线脱节。
        安全: 任一组无编号 → 整体放弃; 主线片数占比 < COUNTER_MIN_RATIO → 放弃。
        注意: 本判据只做"强删脱链组", 不用于认定正片(广告编号也连续时会漏, 故不取反)。"""
        if not self.USE_COUNTER_CHAIN or len(groups) < 2:
            return set()
        info = []
        for s, e in groups:
            cnts = [c for c in (self._extract_counter(items[i]['uri'])
                                for i in range(s, e)) if c is not None]
            info.append({'first': cnts[0] if cnts else None,
                         'last': cnts[-1] if cnts else None,
                         'n': e - s, 'has': len(cnts) > 0})
        # 任一组缺编号 → 可能是哈希命名源, 放弃(防误判)
        if not all(x['has'] for x in info):
            return set()
        m = len(info)
        dp, prev = [1] * m, [-1] * m
        for a in range(m):
            for b in range(a):
                if info[a]['first'] == info[b]['last'] + 1 and dp[b] + 1 > dp[a]:
                    dp[a], prev[a] = dp[b] + 1, b
        # 主线 = 最长链(同长取片数最多者)
        best = max(range(m), key=lambda x: (dp[x], info[x]['n']))
        chain = [False] * m
        k = best
        while k != -1:
            chain[k] = True
            k = prev[k]
        total_n = sum(x['n'] for x in info)
        main_n = sum(info[x]['n'] for x in range(m) if chain[x])
        if total_n <= 0 or main_n / total_n < self.COUNTER_MIN_RATIO:
            return set()                        # 主线占比过低, 不可靠
        ads = set()
        for x in range(m):
            if not chain[x]:
                s, e = groups[x]
                ads |= set(range(s, e))
        return ads

    # ---------- 判据4: 广告路径关键词 ----------
    def _detect_path_keyword(self, items):
        """判据4: ts 路径含广告关键词子目录(/adjump/ 等) → 视为广告片。
        零成本, 但对站点路径策略有强依赖, 站点改路径即失效。"""
        if not self.USE_PATH_KEYWORD or not self.AD_PATH_KEYWORDS:
            return set()
        ads = set()
        for i, it in enumerate(items):
            u = it['uri'].lower()
            if any(kw in u for kw in self.AD_PATH_KEYWORDS):
                ads.add(i)
        return ads

    # ==================== 组扩展(可选) ====================
    def _disc_groups(self, items):
        """按 DISCONTINUITY 把片目下标切成连续组 [(start,end), ...]"""
        out, start = [], 0
        for i, it in enumerate(items):
            if it['disc'] and i > start:
                out.append((start, i))
                start = i
        if len(items) > start:
            out.append((start, len(items)))
        return out

    def _expand_to_groups(self, ads, groups):
        """广告命中落在某个小 DISCONTINUITY 组内 → 整组删除(广告常独立成组)

        新增"命中占比"门槛: 组内命中片数 / 组片数 ≥ GROUP_HIT_RATIO 才整组删。
        理由 —— 真广告块在组内通常"成片命中": 时长指纹 4~5 片全中 / 路径关键词整组
        命中 / 30fps 广告插进 25fps 正片组时半数以上偏离网格; 反之"整组只有 1 片命中"
        多为网格判据对"末片截断时长"(如 0.2s 残片) 的误判, 一旦整组删就会误删正片。
        实测: 西瓜同源 v15.wsyzym3u8 某集片尾组 12 片全是 25fps 正片, 仅末片 0.2s
        截断时长偏离网格 → 旧逻辑整组删掉 24s 正片; 加门槛后只删真广告组。"""
        out = set(ads)
        for s, e in groups:
            span = set(range(s, e))
            hits = ads & span
            if not hits or e - s > self.GROUP_MAX_ITEMS:
                continue
            if len(hits) / float(e - s) < self.GROUP_HIT_RATIO:
                continue                      # 命中占比过低 → 视为单片误判, 不整组删
            out |= span
        return out

    @staticmethod
    def _has_base_dur(items, s, e):
        """组内是否含 4.0s±0.2 正片基准片(dytt系正片切片含4.0基准, 广告组不含)"""
        lo, hi = M3U8Cleaner.BASE_DUR_WIN
        return any(lo <= items[i]['dur'] <= hi for i in range(s, e))

    def _absorb_blocks(self, ads, groups, items):
        """广告连播吸附: 命中组链的紧邻组若总时长≈广告块长(FP1/FP2总长±容差)
        且不含正片基准片 → 整组并入(递归至稳定)。
        场景: 重器2026 第7分钟广告=FP1变体块19.07s+无指纹第二块18.84s 连播,
        第二块全网格对齐无任何单片命中, 只能靠块长吸附。"""
        blocks = [sum(fp) for fp in self.AD_DUR_FP[:2]]
        out = set(ads)
        changed, rounds = True, 0
        while changed and rounds < 5:
            changed = False
            rounds += 1
            for idx, (s, e) in enumerate(groups):
                if not (set(range(s, e)) & out):
                    continue
                for j in (idx - 1, idx + 1):
                    if 0 <= j < len(groups):
                        s2, e2 = groups[j]
                        if set(range(s2, e2)) & out or self._has_base_dur(items, s2, e2):
                            continue
                        d2 = sum(items[i]['dur'] for i in range(s2, e2))
                        if any(abs(d2 - bd) <= self.BLOCK_TOL for bd in blocks):
                            out |= set(range(s2, e2))
                            changed = True
        return out

    # ==================== 重建 ====================
    @staticmethod
    def _rewrite_extra(line, base_url):
        """标签内 URI="..." 相对地址 → 绝对地址(KEY/MAP 等标签)"""
        m = re.search(r'URI="([^"]+)"', line) if 'URI="' in line else None
        if m and not m.group(1).startswith(('http://', 'https://')):
            return line.replace('URI="%s"' % m.group(1),
                                'URI="%s"' % urljoin(base_url, m.group(1)))
        return line

    @staticmethod
    def _abs_uri(uri, base_url):
        return uri if uri.startswith(('http://', 'https://')) else urljoin(base_url, uri)

    # ==================== 主清洗 ====================
    def clean_text(self, content, base_url):
        """清洗 m3u8 文本。base_url 为该 m3u8 的目录级地址(用于补全相对 ts 路径)。
        返回清洗后文本; 无片目/全部误判保护时原样返回。"""
        header, items, footer = self._split_playlist(content)
        if not items:
            return content
        total = sum(it['dur'] for it in items)

        # 主判据: 时长指纹
        fp_ads = self._detect_duration_fp(items)

        # 辅助判据: 帧网格 —— 多网格择优(自适应帧率) / 单一网格(回退)
        grid_rate = None
        if self.USE_MULTI_GRID:
            grid_rate, grid_ratio, grid_ads = self._pick_grid(items)
            grid_disabled = not grid_ads and grid_rate is None
        else:
            if self._grid_is_valid(items):
                grid_ads = self._detect_frame_grid(items)
                grid_disabled = False
            else:
                grid_ads = set()
                grid_disabled = True

        # 判据3: ts 计数器连续性(按 DISCONTINUITY 组)
        groups = self._disc_groups(items)
        counter_ads = self._detect_counter_chain(items, groups)

        # 判据4: 广告路径关键词
        path_ads = self._detect_path_keyword(items)

        # 五层判据取并集(各判据自带高门槛, 不取交集)
        ads = fp_ads | grid_ads | counter_ads | path_ads

        # 组扩展 + 广告连播块长吸附
        expanded = False
        absorbed = False
        if ads and self.EXPAND_GROUPS:
            new_ads = self._expand_to_groups(ads, groups)
            if new_ads != ads:
                ads, expanded = new_ads, True
            new_ads = self._absorb_blocks(ads, groups, items)
            if new_ads != ads:
                ads, absorbed = new_ads, True

        # 噪声剔除: 组扩展后仍"孤立"(左右相邻片都没命中) 且时长 < MIN_AD_DUR 的命中,
        # 多为 ts 末片截断残片(如 0.2s) 对帧网格判据的误判; 丢弃以免误删正片残片。
        # 真广告块命中片彼此相邻, 不受影响。
        if ads:
            ads = {i for i in ads
                   if items[i]['dur'] >= self.MIN_AD_DUR
                   or (i - 1) in ads or (i + 1) in ads}

        # 保护线: 删除占比过高则整体放弃
        removed = sum(items[i]['dur'] for i in ads)
        over_limit = False
        if total > 0 and removed / total > self.MAX_REMOVE_RATIO:
            ads, removed, over_limit = set(), 0.0, True

        self.last_stats = {
            'total_items': len(items), 'total_dur': round(total, 1),
            'removed_items': len(ads), 'removed_dur': round(removed, 1),
            'fp_hits': len(fp_ads), 'grid_hits': len(grid_ads),
            'counter_hits': len(counter_ads), 'path_hits': len(path_ads),
            'grid_rate': grid_rate, 'grid_disabled': grid_disabled,
            'group_expanded': expanded, 'block_absorbed': absorbed,
            'over_limit': over_limit,
        }

        # ---- 重建播放列表 ----
        body, maxdur, pending = [], 0.0, False
        for i, it in enumerate(items):
            if i in ads:
                pending = True
                continue
            if (it['disc'] or pending) and body:
                body.append('#EXT-X-DISCONTINUITY')
            pending = False
            for line in it['extras']:
                body.append(self._rewrite_extra(line, base_url))
            maxdur = max(maxdur, it['dur'])
            body.append(it['inf'])
            body.append(self._abs_uri(it['uri'], base_url))
        out = [self._rewrite_extra(l, base_url) for l in header
               if not l.startswith('#EXT-X-TARGETDURATION')]
        if not out or not out[0].startswith('#EXTM3U'):
            out.insert(0, '#EXTM3U')
        for tag, val in (('#EXT-X-VERSION', '#EXT-X-VERSION:3'),
                         ('#EXT-X-PLAYLIST-TYPE', '#EXT-X-PLAYLIST-TYPE:VOD'),
                         ('#EXT-X-MEDIA-SEQUENCE', '#EXT-X-MEDIA-SEQUENCE:0')):
            if not any(l.startswith(tag) for l in out):
                out.append(val)
        out.append('#EXT-X-TARGETDURATION:%d' % max(1, int(maxdur + 0.999)))
        out.extend(body)
        out.extend(footer if footer else ['#EXT-X-ENDLIST'])

        if self.debug:
            st = self.last_stats
            head = ['# ==== m3u8 去广告调试 ====',
                    '# 总片数 %d  总时长 %.1fs  删除 %d 片 / %.1fs (%.2f%%)' % (
                        st['total_items'], st['total_dur'], st['removed_items'],
                        st['removed_dur'],
                        st['removed_dur'] / st['total_dur'] * 100 if st['total_dur'] else 0),
                    '# 指纹 %d  网格 %d  计数器 %d  路径 %d' % (
                        st['fp_hits'], st['grid_hits'],
                        st['counter_hits'], st['path_hits']),
                    '# 帧网格 %s  组扩展 %s  块吸附 %s  保护线 %s' % (
                        ('%.4g' % st['grid_rate']) if st.get('grid_rate') else 'off',
                        st['group_expanded'], st.get('block_absorbed'), st['over_limit']),
                    '# ==== end ====']
            out = head + out
        return '\n'.join(out) + '\n'

    # ==================== 多码率选流 ====================
    @staticmethod
    def pick_variant(content, base_url):
        """主播放列表(#EXT-X-STREAM-INF) → 选最高 BANDWIDTH 子播放列表地址"""
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        best, best_bw = None, -1
        for i, line in enumerate(lines):
            if not line.startswith('#EXT-X-STREAM-INF'):
                continue
            m = re.search(r'BANDWIDTH=(\d+)', line)
            bw = int(m.group(1)) if m else 0
            for j in range(i + 1, len(lines)):
                if not lines[j].startswith('#'):
                    if bw >= best_bw:
                        best, best_bw = urljoin(base_url, lines[j]), bw
                    break
        return best

    # ==================== 抓取 + 清洗 ====================
    def clean_url(self, url, fetch_fn=None, max_hops=3):
        """从 URL 抓取并清洗, 自动跟随多码率变体(最多 max_hops 跳)。
        fetch_fn: 自定义抓取函数 f(url) -> 文本 或 None; 缺省用 _default_fetch。
        返回 (清洗文本 or None, 错误信息 or None)。"""
        if fetch_fn is None:
            fetch_fn = self._default_fetch
        raw = fetch_fn(url)
        if not raw:
            return None, '获取 m3u8 失败'
        if not raw.lstrip().startswith('#EXTM3U'):
            return None, '上游返回非 m3u8(可能 WAF 403/502): %s' % raw[:120]
        base_url = url.rsplit('/', 1)[0] + '/'
        hops = 0
        while '#EXT-X-STREAM-INF' in raw and hops < max_hops:
            variant = self.pick_variant(raw, base_url)
            if not variant:
                break
            sub = fetch_fn(variant)
            if not sub or not sub.lstrip().startswith('#EXTM3U'):
                break
            raw = sub
            base_url = variant.rsplit('/', 1)[0] + '/'
            hops += 1
        return self.clean_text(raw, base_url), None

    @staticmethod
    def _default_fetch(url):
        """默认抓取: 必须带浏览器 UA, 否则部分资源站 WAF 直接 403"""
        return _http_get(url)


# ============================================================
# ② 抓取通道（纯标准库）
# ============================================================
def _http_get(url, timeout=15, headers=None):
    """urllib 直连抓文本（带浏览器 UA）。失败返回 None。"""
    try:
        import urllib.request
        h = {'User-Agent': ADFREE_UA, 'Accept': '*/*'}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if getattr(r, 'status', 200) != 200:
                return None
            return r.read().decode('utf-8', 'ignore')
    except Exception:
        return None


# ============================================================
# ③ 清洗核心：抓取 + 四判据剔除 + URI 绝对化 + 单条缓存
# ============================================================
class AdFreeCleaner(object):
    """去广告清洗核心。
    fetch_fn: 可选注入的抓取器 f(url)->文本/None。站点脚本可传入壳 fetch
              （与播放器同网络栈，最稳）；不传则用 urllib 直连。"""

    def __init__(self, fetch_fn=None, debug=False):
        self.engine = M3U8Cleaner(debug=debug)
        self.fetch_fn = fetch_fn
        # 单条目缓存：同一集反复 seek 免重复抓取清洗
        self._cache = {'url': None, 'body': None, 'removed': 0}

    def _grab(self, url):
        """双通道抓取：注入的 fetch 优先，urllib 兜底。返回文本或 None。"""
        if self.fetch_fn is not None:
            try:
                raw = self.fetch_fn(url)
                if isinstance(raw, str) and raw.lstrip().startswith('#EXTM3U'):
                    return raw
                if raw is not None and hasattr(raw, 'text'):
                    t = getattr(raw, 'text', None) or ''
                    if getattr(raw, 'status_code', 0) == 200 and t.lstrip().startswith('#EXTM3U'):
                        return t
            except Exception:
                pass
        return _http_get(url)

    def clean(self, src):
        """返回 (清洗文本 or None, 剔除广告片数, 错误信息 or None)。"""
        # URL 规范化(去随机参数)提高缓存命中
        try:
            parsed = urlparse(src)
            q = parse_qs(parsed.query)
            for k in ('_', 't', 'timestamp', 'rand', 'v', 'cb', 'callback'):
                q.pop(k, None)
            norm = urlunparse(parsed._replace(query=urlencode(q, doseq=True)))
        except Exception:
            norm = src

        # 缓存命中直接回
        if self._cache['url'] == norm and self._cache['body']:
            return self._cache['body'], self._cache.get('removed', 0), None

        _last_err = ['获取 m3u8 失败']

        def _fetch(u):
            text = self._grab(u)
            if text is None:
                _last_err[0] = '抓取失败: %s' % u[:80]
            return text

        try:
            # 只抓单页, 下钻交给 clean_url 的变体循环(它每跳正确更新 base_url)
            text, err = self.engine.clean_url(src, fetch_fn=_fetch)
        except Exception as e:
            return None, 0, 'clean error: %s' % e
        if err or not text:
            return None, 0, (_last_err[0] if _last_err[0] != '获取 m3u8 失败'
                             else (err or 'clean failed'))
        # 二次校验: 结果必须是分片列表。若仍含 STREAM-INF 说明变体下钻被中断,
        # 也拒绝 —— 绝不让播放器拿到主列表自己请求变体而绕过清洗。
        if '#EXTINF' not in text or '#EXT-X-STREAM-INF' in text:
            return None, 0, '清洗结果异常(非分片列表): %s' % _last_err[0]
        removed = int((getattr(self.engine, 'last_stats', {}) or {}).get('removed_items', 0) or 0)
        self._cache = {'url': norm, 'body': text, 'removed': removed}
        return text, removed, None

    # 兼容旧调用（二元组）
    def clean_text_only(self, src):
        text, _removed, err = self.clean(src)
        return text, err


# ============================================================
# ④ 实时清洗代理：自建 127.0.0.1 ThreadingHTTPServer
# ============================================================
class AdFreeProxy(object):
    """实时清洗代理。惰性启动：首次 url_for() 才建线程绑端口。
    播放器请求 /adfree?url=<原链> 时才实时拉源站 m3u8 清洗并返回；
    抓取/清洗失败 → 302 回源原地址（带广告但至少能播）。
    注：壳自带 /proxy 在 FongMi 3.1.5 / Chaquopy JNI 层不可用，故自建。"""

    def __init__(self, cleaner=None, fetch_fn=None, host='127.0.0.1'):
        self.cleaner = cleaner or AdFreeCleaner(fetch_fn=fetch_fn)
        self.host = host
        self._port = 0
        self._srv = None

    def start(self):
        """启动服务，返回监听端口；起不来返回 0（调用方回退原链/file://）。"""
        if self._port:
            return self._port
        try:
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        except Exception:
            return 0
        proxy = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_GET(self):
                u = urlparse(self.path)
                # ★ 封面中继端点: /img?url=<原链> → 服务器侧取图后回吐, 壳只连本机
                if u.path.rstrip('/') == '/img':
                    target = (parse_qs(u.query).get('url') or [''])[0]
                    if not target.lower().startswith(('http://', 'https://')):
                        return self._out(400, 'text/plain', b'bad url')
                    data, ctype = proxy.fetch_bytes(target)
                    if not data:
                        return self._out(502, 'text/plain', b'fetch fail')
                    try:
                        self.send_response(200)
                        self.send_header('Content-Type', ctype or 'image/jpeg')
                        self.send_header('Content-Length', str(len(data)))
                        self.send_header('Access-Control-Allow-Origin', '*')
                        # 让壳缓存, 避免重复回源
                        self.send_header('Cache-Control', 'public, max-age=86400')
                        self.end_headers()
                        self.wfile.write(data)
                    except Exception:
                        pass
                    return
                if u.path.rstrip('/') != '/adfree':
                    return self._out(404, 'text/plain', b'')
                target = (parse_qs(u.query).get('url') or [''])[0]
                if not target:
                    return self._out(404, 'text/plain', b'')
                text, _removed, err = proxy.cleaner.clean(target)
                if not text or err:
                    # 抓取/清洗失败 → 302 回源原地址(带广告但能播)
                    try:
                        self.send_response(302)
                        self.send_header('Location', target)
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                    except Exception:
                        pass
                    return
                return self._out(200, 'application/vnd.apple.mpegurl',
                                 text.encode('utf-8'))

            def _out(self, code, ctype, body):
                try:
                    self.send_response(code)
                    self.send_header('Content-Type', ctype)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    if body:
                        self.wfile.write(body)
                except Exception:
                    pass

        try:
            srv = ThreadingHTTPServer((self.host, 0), _Handler)
            self._port = srv.server_address[1]
            self._srv = srv
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            return self._port
        except Exception:
            return 0

    def url_for(self, src, probe=True):
        """有广告且本地服务可用 → 返回 http://127.0.0.1:<port>/adfree?url=<原链>；
        否则 None（调用方回退原链）。probe=True 时先同步清洗一次判是否有广告。"""
        if not src:
            return None
        if probe:
            text, removed, err = self.cleaner.clean(src)
            if not text or err or removed <= 0:
                return None
        port = self.start()
        if not port:
            return None
        return 'http://%s:%d/adfree?url=%s' % (self.host, port, quote(src, safe=''))

    # ========== ★ 封面中继：绕开本机对图床(Cloudflare/IPv6)的阻断 ==========
    def fetch_bytes(self, url):
        """服务器侧取图, 返回 (bytes, content_type); 失败返回 (None, None)。
        用脚本自身通道(带浏览器头, 跳证书校验)拉图床, 进程级缓存。"""
        if not url or not url.startswith(('http://', 'https://')):
            return None, None
        if url in _IMG_CACHE:
            return _IMG_CACHE[url]
        data = ctype = None
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 '
                              '(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
                'Referer': 'https://wsyzy.cc/',
            })
            with urllib.request.urlopen(req, timeout=12, context=self._ssl_ctx()) as r:
                data = r.read()
                ctype = r.headers.get('Content-Type', 'image/jpeg')
        except Exception:
            data = None
        if data:
            _IMG_CACHE[url] = (data, ctype)
            return data, ctype
        return None, None

    def _ssl_ctx(self):
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        except Exception:
            return None

    def pic_url(self, src):
        """封面改写: 原链 → http://127.0.0.1:<port>/img?url=<原链>。
        服务器可达时壳只连本机取图, 绕开本机到图床的阻断; 服务起不来返回原链。"""
        u = str(src or '').strip()
        if not u.startswith(('http://', 'https://')):
            return u
        port = self.start()
        if not port:
            return u
        return 'http://%s:%d/img?url=%s' % (self.host, port, quote(u, safe=''))

    def shutdown(self):
        try:
            if self._srv:
                self._srv.shutdown()
        except Exception:
            pass
        self._port = 0
        self._srv = None


# ============================================================
# 便捷：站点脚本 playerContent 可直接用的顶层入口
# ============================================================
def write_temp_m3u8(text, src):
    """★ file:// 兜底：本地清洗服务起不来时使用。把已清洗文本写到 App 缓存目录，
    返回 file:// 地址（分段均为绝对 URL，播放器直连 CDN）。文件名按 URL 哈希命名。"""
    try:
        import tempfile, os, hashlib
        if not text or not text.lstrip().startswith('#EXTM3U'):
            return None
        name = 'ff_%s.m3u8' % hashlib.md5(src.encode('utf-8')).hexdigest()[:16]
        path = None
        for d in (tempfile.gettempdir(), '/data/data/com.fongmi.android.tv/cache',
                  '/data/user/0/com.fongmi.android.tv/cache'):
            if not d:
                continue
            try:
                test = os.path.join(d, '.ff_wtest')
                open(test, 'w').close()
                os.remove(test)
                path = os.path.join(d, name)
                break
            except Exception:
                continue
        if not path:
            return None
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        return 'file://' + path
    except Exception:
        return None


_DEFAULT_PROXY = None


def get_proxy(fetch_fn=None):
    """取（或惰性创建）全局代理实例。整个进程共用一个服务，避免重复占端口。"""
    global _DEFAULT_PROXY
    if _DEFAULT_PROXY is None:
        _DEFAULT_PROXY = AdFreeProxy(fetch_fn=fetch_fn)
    return _DEFAULT_PROXY


def player_url(play_url, proxy=None, fallback_file=True):
    """站点脚本 playerContent 用：决定最终播放地址。
    - m3u8 直链 + 有广告 + 本地服务可用 → 代理地址；
    - 有广告但服务起不来 + fallback_file → file:// 预清洗兜底；
    - 无广告 / 非 m3u8 → 原链。
    返回 (最终地址, 提示信息)。"""
    proxy = proxy or get_proxy()
    if not play_url or not re.match(r'^https?://', play_url, re.I) or '.m3u8' not in play_url:
        return play_url, 'non-m3u8'
    text, removed, err = proxy.cleaner.clean(play_url)
    if not text or err:
        return play_url, 'clean-failed:%s' % (err or '')
    if removed <= 0:
        return play_url, 'no-ad'
    purl = proxy.url_for(play_url, probe=False)
    if purl:
        return purl, 'proxy'
    if fallback_file:
        furl = write_temp_m3u8(text, play_url)
        if furl:
            return furl, 'file-fallback'
    return play_url, 'proxy-unavailable'


class Spider(BaseSpider):
    def init(self, extend=""):
        self.session = requests.Session()
        if HTTPAdapter and Retry:
            retry = Retry(total=2, backoff_factor=0.3,
                          status_forcelist=(429, 500, 502, 503, 504),
                          allowed_methods=frozenset(['GET']))
            adapter = HTTPAdapter(max_retries=retry, pool_maxsize=20)
            self.session.mount('https://', adapter)
            self.session.mount('http://', adapter)

        self.headers = {
            "User-Agent": DOUBAN_UA,
            "Referer": DOUBAN_REFERER,
            "Host": "frodo.douban.com",
            "Accept": "application/json",
            "Connection": "Keep-Alive",
        }
        self.page_cache = _TTLCache(max_size=96, ttl=600)
        self.top250_cache = _TTLCache(max_size=2, ttl=3600)
        self.ep_cache = _TTLCache(max_size=4, ttl=1800)
        # ★ 混合源: wsyzy 取址缓存(豆瓣 sid -> 真实播放串)
        self.wsy_play_cache = _TTLCache(max_size=128, ttl=1800)
        # ★ 混合源: 标记哪些 id 是 wsyzy 的(搜索命中的), 避免被误当豆瓣 sid 查豆瓣
        self.wsy_ids = set()
        # ★ 混合源: wsyzy 搜索结果缓存(存完整 dict, 供 detail/player 复用)
        self.wsy_detail_cache = _TTLCache(max_size=128, ttl=1800)
        # ★ 混合源: 搜索结果缓存
        self.wsy_search_cache = _TTLCache(max_size=32, ttl=900)
        self.debug = False

    # ---------------- 基础工具 ----------------
    def _log(self, msg):
        if self.debug:
            print("[豆瓣导航] %s" % msg)

    @staticmethod
    def _pick(filters, *keys):
        """兼容中英文筛选 key"""
        for k in keys:
            v = filters.get(k)
            if v:
                return str(v).strip()
        return ''

    def _f(self, filters, name):
        return self._pick(filters, *_KEY_ALIAS.get(name, (name,)))

    # ---------------- HTTP ----------------
    def _request_douban(self, url, timeout=15):
        sep = '&' if '?' in url else '?'
        try:
            resp = self.session.get("%s%sapikey=%s" % (url, sep, DOUBAN_API_KEY),
                                    headers=self.headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            self._log("frodo %s -> %s" % (resp.status_code, url[:80]))
        except Exception as e:
            self._log("豆瓣请求失败: %s..., %s" % (url[:80], e))
        return None

    def _request_mdouban(self, url, referer=None):
        headers = {
            "User-Agent": M_DOUBAN_UA,
            "Referer": referer or M_DOUBAN_REFERER_TV,
            "Accept": "*/*",
            "Connection": "keep-alive",
        }
        try:
            resp = self.session.get(url, headers=headers, timeout=12)
            if resp.status_code == 200:
                return resp.json()
            self._log("rexxar %s -> %s" % (resp.status_code, url[:80]))
        except Exception as e:
            self._log("m.douban请求失败: %s, %s" % (url[:80], e))
        return None

    # ---------------- ★ 混合源: 按片名查 wsyzy 取真实播放地址 ----------------
    def _wsy_headers(self):
        return {
            "User-Agent": WSY_UA,
            "Referer": WSY_SITE,
            "Accept": "application/json",
        }

    def _wsy_suggest_ids(self, name):
        """按片名查 suggest 接口, 返回 wsyzy 的 vod_id 列表。"""
        url = WSY_SITE + "/index.php/ajax/suggest"
        try:
            resp = self.session.get(url, params={"mid": "1", "wd": name},
                                    headers=self._wsy_headers(), timeout=WSY_TIMEOUT)
            if resp.status_code != 200:
                self._log("wsyzy suggest %s -> %s" % (resp.status_code, name))
                return []
            data = resp.json()
        except Exception as e:
            self._log("wsyzy suggest 异常 %s: %s" % (name, e))
            return []
        ids = []
        for it in (data.get("list") or []):
            if not isinstance(it, dict):
                continue
            wid = str(it.get("id") or it.get("vod_id") or "").strip()
            if wid:
                ids.append(wid)
        return ids

    def _wsy_detail(self, ids):
        """按 vod_id 批量取 wsyzy 详情, 返回 list[dict]。"""
        if not ids:
            return []
        try:
            resp = self.session.get(WSY_API, params={"ac": "detail", "ids": ",".join(ids)},
                                    headers=self._wsy_headers(), timeout=WSY_TIMEOUT + 4)
            if resp.status_code != 200:
                self._log("wsyzy detail %s -> %s" % (resp.status_code, ids))
                return []
            data = resp.json()
        except Exception as e:
            self._log("wsyzy detail 异常 %s: %s" % (ids, e))
            return []
        out = []
        for d in (data.get("list") or []):
            if isinstance(d, dict) and (d.get("vod_play_url") or d.get("vod_id")):
                out.append(d)
        return out

    def _wsy_parse_play_url(self, raw):
        """解析 wsyzy vod_play_url -> 统一的 '集名$地址' 串(多源合并)。
        格式: 源1$$$源2, 源内各集用 # 分隔, 每集 '集名$http(s)://...'。"""
        if not raw:
            return ""
        groups = []
        for src in str(raw).split("$$$"):
            eps = []
            for seg in src.split("#"):
                seg = seg.strip()
                if "$" in seg:
                    ename, eurl = seg.split("$", 1)
                    ename = ename.strip() or "播放"
                    eurl = eurl.strip()
                    if eurl.lower().startswith("http"):
                        eps.append("%s$%s" % (ename, eurl))
            if eps:
                groups.append("#".join(eps))
        return "$$$".join(groups)

    def _wsy_lookup_by_name(self, sid, name, remarks=""):
        """按片名(+年份备注)查 wsyzy, 命中返回真实播放串(带 vod_play_from), 否则 None。
        结果按豆瓣 sid 缓存, 避免重复查站。"""
        if not name:
            return None
        cached = self.wsy_play_cache.get(sid)
        if cached is not None:
            return cached or None

        result = None
        ids = self._wsy_suggest_ids(name)
        if ids:
            # 优先取首个 id 的详情, 命中即用(片名精确匹配时通常就是目标)
            for d in self._wsy_detail(ids[:1]):
                play = self._wsy_parse_play_url(d.get("vod_play_url"))
                if play:
                    result = play
                    break
            # 首个无播放地址时, 兜底遍历其余 id
            if not result and len(ids) > 1:
                for d in self._wsy_detail(ids[1:]):
                    play = self._wsy_parse_play_url(d.get("vod_play_url"))
                    if play:
                        result = play
                        break
        self.wsy_play_cache.set(sid, result or "")
        if result:
            self._log("wsyzy 命中 [%s] -> %d 源" % (name, len(result.split("$$$"))))
        else:
            self._log("wsyzy 未命中 [%s]" % name)
        return result

    def _wsy_search(self, kw):
        """混合源搜索: 直接走 wsyzy suggest, 返回 [(wid, name, pic)] 列表。"""
        cached = self.wsy_search_cache.get(kw)
        if cached is not None:
            return cached
        try:
            resp = self.session.get(WSY_SITE + "/index.php/ajax/suggest",
                                    params={"mid": "1", "wd": kw},
                                    headers=self._wsy_headers(), timeout=WSY_TIMEOUT)
            data = resp.json() if resp.status_code == 200 else {}
        except Exception as e:
            self._log("wsyzy 搜索异常 [%s]: %s" % (kw, e))
            data = {}
        out = []
        for it in (data.get("list") or []):
            if not isinstance(it, dict):
                continue
            wid = str(it.get("id") or it.get("vod_id") or "").strip()
            name = str(it.get("name") or it.get("vod_name") or "").strip()
            pic = str(it.get("pic") or it.get("vod_pic") or "").strip()
            if wid and name:
                out.append((wid, name, pic))
                # 标记为 wsyzy id + 缓存原始项, 供 detail/player 识别复用
                self.wsy_ids.add(wid)
                self.wsy_detail_cache.set(wid, {"id": wid, "name": name, "pic": pic})
        self.wsy_search_cache.set(kw, out)
        self._log("wsyzy 搜索 [%s] -> %d 条" % (kw, len(out)))
        return out

    def _build_wsyzy_detail(self, wid):
        """按 wsyzy id 构建详情: 取 vod_play_url 真实地址, 返回 vod dict。"""
        base = self.wsy_detail_cache.get(wid)
        name = base.get("name") if base else ""
        if not name:
            return {"list": []}

        # 取 wsyzy detail 补全字段(播放地址/年份/简介等)
        detail = {}
        for d in self._wsy_detail([wid]):
            if str(d.get("vod_id") or "") == wid:
                detail = d
                break

        vname = detail.get("vod_name") or name
        year = str(detail.get("vod_year") or "")
        area = str(detail.get("vod_area") or "")
        actor = str(detail.get("vod_actor") or "")
        director = str(detail.get("vod_director") or "")
        intro = str(detail.get("vod_content") or "")
        remarks = str(detail.get("vod_remarks") or "")
        # 搜索时 suggest 给的 pic 作为封面(suggest 接口通常返回海报)
        pic = (base.get("pic") if base else "") or str(detail.get("vod_pic") or "")

        play = self._wsy_parse_play_url(detail.get("vod_play_url"))
        if play:
            play_from = "豆瓣"
        else:
            play = "wsyzy$%s" % wid
            play_from = "豆瓣"

        return {"list": [{
            "vod_id": wid,
            "vod_name": vname,
            "vod_pic": pic,
            "vod_year": year,
            "vod_area": area,
            "vod_lang": "",
            "vod_actor": actor,
            "vod_director": director,
            "vod_content": intro,
            "type_name": "",
            "vod_remarks": remarks,
            "vod_play_from": play_from,
            "vod_play_url": play,
        }]}

    # ---------------- 解析工具 ----------------
    def _extract_full_date(self, s):
        if not s:
            return ''
        m = re.search(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', str(s))
        return "%s.%s.%s" % (m[1], m[2].zfill(2), m[3].zfill(2)) if m else ''

    def _get_subject_id(self, item):
        if not isinstance(item, dict):
            return ''
        sub = item.get('subject') or {}
        if not isinstance(sub, dict):
            sub = {}
        for c in (sub.get('id'), item.get('id'),
                  item.get('subject_id'), item.get('target_id'), item.get('sid')):
            if c and re.fullmatch(r'\d+', str(c)):
                return str(c).strip()
        return ''

    def _normalize_mdouban_item(self, raw):
        item = dict(raw)
        pic = raw.get('pic')
        if isinstance(pic, dict):
            item['cover'] = {'url': pic.get('large') or pic.get('normal') or '',
                             'normal': pic.get('normal') or ''}
        elif isinstance(pic, str):
            item['cover'] = {'url': pic, 'normal': pic}
        subtitle = raw.get('card_subtitle') or raw.get('subtitle') or ''
        if subtitle:
            ym = re.search(r'^(\d{4})', str(subtitle))
            if ym:
                item['year'] = ym[1]
        return item

    @staticmethod
    def _fmt_score(rating_val):
        try:
            v = float(rating_val)
            return "%.1f" % v if v > 0 else '0'
        except (TypeError, ValueError):
            return '0'

    def _parse_item(self, item):
        if not isinstance(item, dict):
            return None
        sub = item.get('subject') or {}
        if not isinstance(sub, dict):
            sub = {}
        title = item.get('title') or sub.get('title') or '未知'
        raw_id = self._get_subject_id(item)
        if not raw_id:
            return None

        rating = item.get('rating') or sub.get('rating') or {}
        if not isinstance(rating, dict):
            rating = {}
        rating_val = rating.get('value')

        pic_obj = (item.get('cover') or item.get('pic')
                   or sub.get('cover') or sub.get('pic') or {})
        if isinstance(pic_obj, dict):
            pic_url = pic_obj.get('url') or pic_obj.get('normal') or pic_obj.get('large') or ''
        elif isinstance(pic_obj, str):
            pic_url = pic_obj
        else:
            pic_url = ''
        if pic_url:
            pic_url = re.sub(r'img\d+\.doubanio\.com', 'img1.doubanio.com', pic_url)
            pic = pic_url + PIC_SUFFIX
        else:
            pic = ''

        pubdate = ''
        for k in ('pubdate', 'release_date'):
            val = sub.get(k) or item.get(k) or ''
            if val:
                pubdate = val
                break
        year_str = sub.get('year') or item.get('year') or ''
        if not pubdate and year_str:
            pubdate = year_str
        if not pubdate:
            card = item.get('card_subtitle') or sub.get('card_subtitle') or ''
            cm = re.match(r'^(\d{4})', str(card))
            if cm:
                pubdate = cm.group(1)
        if not pubdate and title:
            ym = re.search(r'\((\d{4})\)|\[(\d{4})\]|(\d{4})年', str(title))
            if ym:
                pubdate = ym.group(1) or ym.group(2) or ym.group(3)

        # 旧版缺陷：只有 card_subtitle 里的年份、没有 year 字段时，年份会被丢弃
        fd = self._extract_full_date(pubdate) or self._extract_full_date(year_str)
        if fd:
            date_label = fd
        else:
            ym = re.match(r'^(\d{4})', str(year_str or pubdate))
            date_label = ym.group(1) if ym else str(year_str or pubdate or '').strip()
        score_str = self._fmt_score(rating_val)
        remarks = "%s / %s分" % (date_label, score_str) if date_label else "%s分" % score_str

        ep_info = item.get('episodes_info') or sub.get('episodes_info') or ''
        if ep_info and isinstance(ep_info, str):
            ep_info = re.sub(r'更新至第(\d+)集', r'更新至\1集', ep_info)
            ep_info = re.sub(r'共(\d+)集', r'全\1集', ep_info)
            ep_info = re.sub(r'(\d+)集全', r'全\1集', ep_info).strip()
        else:
            ep_count = sub.get('episodes_count') or item.get('episodes_count') or 0
            cur_ep = sub.get('current_episode') or item.get('current_episode') or 0
            if cur_ep:
                ep_info = "更新至%s集" % cur_ep
            elif ep_count:
                ep_info = "全%s集" % ep_count
            else:
                ep_info = ''

        return {
            'vod_id': raw_id,
            'vod_name': title,
            'vod_pic': pic,
            'vod_remarks': remarks,
            'vod_year': ep_info,  # 沿用旧约定：列表页 vod_year 位展示集数
        }

    # ---------------- 通用分页取数 ----------------
    def _paginate(self, fetcher, start, count):
        """按实际返回条数推进 offset，避免跳页漏数据"""
        result, seen, offset = [], set(), start
        for _ in range(MAX_LOOP):
            want = min(API_PAGE_LIMIT, count - len(result))
            if want <= 0:
                break
            arr = fetcher(offset, want) or []
            if not arr:
                break
            added = 0
            for it in arr:
                sid = self._get_subject_id(it)
                if sid and sid not in seen:
                    seen.add(sid)
                    result.append(it)
                    added += 1
            if not added:
                break
            offset += len(arr)  # 关键：按接口实际返回条数推进
        return result

    def _fetch_rexxar_items(self, base_url, referer, start, count, selected, tags, sort):
        def fetcher(offset, want):
            url = ("%s?refresh=0&start=%s&count=%s"
                   "&selected_categories=%s"
                   "&uncollect=false&score_range=0,10"
                   "&tags=%s&sort=%s" % (base_url, offset, want,
                                         quote(json.dumps(selected, ensure_ascii=False)),
                                         quote(tags), sort))
            data = self._request_mdouban(url, referer)
            return (data or {}).get('items')

        items = self._paginate(fetcher, start, count)
        return [self._normalize_mdouban_item(it) for it in items]

    def _fetch_frodo_items(self, path, params, start, count, key='items'):
        def fetcher(offset, want):
            p = dict(params)
            p['start'], p['count'] = offset, want
            data = self._request_douban("%s%s?%s" % (DOUBAN_HOST, path, urlencode(p)))
            return (data or {}).get(key)

        return self._paginate(fetcher, start, count)

    @staticmethod
    def _join_tags(*parts):
        """过滤空值后再拼 tags，避免全部未选时产生 ',,' """
        return ','.join([p for p in parts if p])

    # ---------------- 各数据源 ----------------
    def _fetch_movie_filter(self, start, count, filters):
        genre = self._f(filters, 'genre')
        region = self._f(filters, 'region')
        year = self._f(filters, 'year')
        sort = self._pick(filters, 'sort') or 'U'
        selected = {}
        if genre:
            selected["类型"] = genre
        if region:
            selected["地区"] = region
        tags = self._join_tags(genre, region, year)
        return self._fetch_rexxar_items(
            "%s/movie/recommend" % M_DOUBAN_HOST, M_DOUBAN_REFERER_MOVIE,
            start, count, selected, tags, sort)

    def _fetch_tv_filter(self, start, count, filters):
        genre = self._f(filters, 'genre')
        region = self._f(filters, 'region')
        year = self._f(filters, 'year')
        platform = self._pick(filters, 'platform')
        sort = self._pick(filters, 'sort') or 'U'
        selected = {"形式": "电视剧"}
        if genre:
            selected["类型"] = genre
        if region:
            selected["地区"] = region
        tags = self._join_tags(genre, region, year, platform)
        return self._fetch_rexxar_items(
            "%s/tv/recommend" % M_DOUBAN_HOST, M_DOUBAN_REFERER_TV,
            start, count, selected, tags, sort)

    def _fetch_show_filter(self, start, count, filters):
        tags = self._join_tags(self._f(filters, 'genre'),
                               self._f(filters, 'region'),
                               self._f(filters, 'year'), '综艺')
        sort = self._pick(filters, 'sort') or 'U'
        return self._fetch_frodo_items("/tv/recommend",
                                       {'tags': tags, 'sort': sort}, start, count)

    def _fetch_anime(self, start, count, filters):
        tags = self._join_tags(self._f(filters, 'genre'),
                               self._f(filters, 'region'),
                               self._f(filters, 'year'), '动画')
        sort = self._pick(filters, 'sort') or 'U'
        return self._fetch_frodo_items("/tv/recommend",
                                       {'tags': tags, 'sort': sort}, start, count)

    def _fetch_hot_anime(self, start, count, filters):
        """动漫电影：电影源优先，不足时用剧集源补足，避免两源共用 start 造成错位"""
        slug = self._pick(filters, 'slug') or 'all'
        sort = {'all': 'U', 'anime_hot': 'U',
                'anime_best': 'S', 'anime_recent': 'R'}.get(slug, 'U')
        params = {'tags': '动画', 'sort': sort}

        items = self._fetch_frodo_items("/movie/recommend", params, start, count)
        if len(items) < count:
            more = self._fetch_frodo_items("/tv/recommend", params, start, count - len(items))
            seen = {self._get_subject_id(i) for i in items}
            for it in more:
                sid = self._get_subject_id(it)
                if sid and sid not in seen:
                    seen.add(sid)
                    items.append(it)
        return items

    # ---------------- 热播/热门入口 ----------------
    def _fetch_hot_movie(self, start, count, filters):
        sort = self._pick(filters, 'sort') or 'recommend'
        area = self._pick(filters, 'area') or '全部'
        return self._fetch_frodo_items(
            "/movie/hot_gaia", {'sort': sort, 'area': area}, start, count)

    def _fetch_collection(self, col, start, count):
        return self._fetch_frodo_items("/subject_collection/%s/items" % col, {},
                                       start, count, key='subject_collection_items')

    def _fetch_tv_hot(self, start, count, filters):
        col = self._pick(filters, 'type') or 'tv_hot'
        return self._fetch_collection(col, start, count)

    def _fetch_show_hot(self, start, count, filters):
        col = self._pick(filters, 'type') or 'show_hot'
        return self._fetch_collection(col, start, count)

    def _fetch_anime_hot(self, start, count, filters):
        choice = self._pick(filters, 'type') or '全部'
        if choice == '全部':
            return self._fetch_collection('tv_animation', start, count)
        tag_map = {
            '国漫': '大陆,动画', '日漫': '日本,动画', '美漫': '欧美,动画',
            '韩漫': '韩国,动画', '港漫': '香港,动画', '台漫': '台湾,动画',
        }
        tags = tag_map.get(choice, '动画')
        return self._fetch_frodo_items("/tv/recommend",
                                       {'tags': tags, 'sort': 'U'}, start, count)

    # ---------------- Top250：全量 + 本地筛选 ----------------
    def _get_top250_all(self):
        cached = self.top250_cache.get('all')
        if cached:
            return cached
        items, seen = [], set()
        ok = False
        for start in range(0, 250, 50):
            data = self._request_douban(
                "%s/subject_collection/movie_top250/items?start=%s&count=50"
                % (DOUBAN_HOST, start))
            if not data:
                break
            arr = data.get('subject_collection_items') or data.get('items') or []
            if not arr:
                break
            ok = True
            for it in arr:
                sid = self._get_subject_id(it)
                if sid and sid not in seen:
                    seen.add(sid)
                    items.append(it)
        # 抓取不完整时不写入长缓存，避免"残缺榜单"被缓存 1 小时
        if items and (ok and len(items) >= 100):
            self.top250_cache.set('all', items)
        return items

    @staticmethod
    def _split_card(item):
        sub = item.get('subject') or {}
        if not isinstance(sub, dict):
            sub = {}
        card = item.get('card_subtitle') or sub.get('card_subtitle') or ''
        parts = [p.strip() for p in str(card).split('/')]
        year = parts[0] if len(parts) > 0 else ''
        regions = parts[1].split() if len(parts) > 1 else []
        genres = parts[2].split() if len(parts) > 2 else []
        return year, regions, genres

    @staticmethod
    def _year_match(item_year, filter_year):
        m = re.match(r'(\d{4})', str(item_year))
        if not m:
            return False
        y = int(m.group(1))
        fy = str(filter_year)
        if fy.isdigit():
            return y == int(fy)
        m = re.match(r'(\d{4})年代', fy)
        if m:
            base = int(m.group(1))
            return base <= y < base + 10
        m = re.match(r'(\d{2})年代', fy)
        if m:
            d = int(m.group(1))
            base = 1900 + d if d >= 30 else 2000 + d
            return base <= y < base + 10
        if fy == '更早':
            return y < 1960
        return True

    def _top250_match(self, item, genre, region, year):
        item_year, regions, genres = self._split_card(item)
        if not item_year:
            sub = item.get('subject') or {}
            item_year = item.get('year') or (sub.get('year') if isinstance(sub, dict) else '') or ''
        if genre and genre not in genres:
            return False
        if region:
            if region == '华语':
                if not any(r in _CHINESE for r in regions):
                    return False
            elif region == '欧美':
                if not any(r in _WESTERN for r in regions):
                    return False
            elif region not in regions:
                return False
        if year and not self._year_match(item_year, year):
            return False
        return True

    def _sort_top250(self, items, sort):
        if sort == 'S':
            def gv(it):
                r = it.get('rating') or {}
                try:
                    return float(r.get('value') or 0)
                except (TypeError, ValueError):
                    return 0.0
            return sorted(items, key=gv, reverse=True)
        if sort == 'R':
            def gy(it):
                y, _, _ = self._split_card(it)
                m = re.match(r'(\d{4})', str(y))
                return int(m.group(1)) if m else 0
            return sorted(items, key=gy, reverse=True)
        return items

    def _fetch_top250(self, start, count, filters):
        all_items = self._get_top250_all()
        genre = self._f(filters, 'genre')
        region = self._f(filters, 'region')
        year = self._f(filters, 'year')
        sort = self._pick(filters, 'sort')
        if genre or region or year:
            all_items = [it for it in all_items if self._top250_match(it, genre, region, year)]
        all_items = self._sort_top250(all_items, sort)
        return all_items[start:start + count]

    # ---------------- 剧集集数补全（缓存式、按需） ----------------
    def _get_episodes_map(self, category):
        cached = self.ep_cache.get(category)
        if cached is not None:
            return cached
        map_info, seen = {}, set()
        for s in range(0, 120, 20):
            url = ("%s/subject/recent_hot/%s?start=%s&limit=20"
                   "&category=%s&type=%s" % (M_DOUBAN_HOST, category, s, category, category))
            data = self._request_mdouban(url, M_DOUBAN_REFERER_TV)
            arr = (data or {}).get('items') or []
            if not arr:
                break
            for it in arr:
                iid = str(it.get('id', ''))
                if iid and iid not in seen and it.get('episodes_info'):
                    seen.add(iid)
                    map_info[iid] = str(it['episodes_info']).strip()
        self.ep_cache.set(category, map_info)
        return map_info

    def _fill_episodes(self, tid, items):
        """只在确实缺少集数信息时才发请求，且用缓存的热门榜 map 兜底"""
        if not items:
            return
        missing = [it for it in items
                   if not (it.get('episodes_info')
                           or (it.get('subject') or {}).get('episodes_info'))]
        if not missing:
            return
        category = 'show' if tid == 'show_filter' else 'tv'
        ep_map = self._get_episodes_map(category)
        if not ep_map:
            return
        for it in missing:
            sid = self._get_subject_id(it)
            if sid and sid in ep_map:
                it['episodes_info'] = ep_map[sid]

    # ---------------- 分类总入口 ----------------
    FETCHERS = None  # init 时无法引用绑定方法，故在方法内 dispatch

    def _fetch_category_items(self, tid, page, filters):
        count = PAGE_SIZE
        start = (page - 1) * count

        if tid == 'hot_movie':
            items = self._fetch_hot_movie(start, count, filters)
        elif tid == 'tv_hot':
            items = self._fetch_tv_hot(start, count, filters)
        elif tid == 'anime_hot':
            items = self._fetch_anime_hot(start, count, filters)
        elif tid == 'show_hot':
            items = self._fetch_show_hot(start, count, filters)
        elif tid == 'top_250':
            return self._fetch_top250(start, count, filters)  # 内部已去重
        elif tid == 'movie_filter':
            items = self._fetch_movie_filter(start, count, filters)
        elif tid == 'tv_filter':
            items = self._fetch_tv_filter(start, count, filters)
        elif tid == 'show_filter':
            items = self._fetch_show_filter(start, count, filters)
        elif tid == 'anime':
            items = self._fetch_anime(start, count, filters)
        elif tid == 'hot_anime':
            items = self._fetch_hot_anime(start, count, filters)
        else:
            items = []

        if tid in ('tv_filter', 'show_filter', 'anime'):
            self._fill_episodes(tid, items)
        return items

    def _category(self, tid, page, filters):
        cache_key = "%s_%s_%s" % (tid, page, json.dumps(filters or {}, sort_keys=True,
                                                        ensure_ascii=False))
        cached = self.page_cache.get(cache_key)
        if cached is not None:
            return cached

        video_list, seen = [], set()
        for it in self._fetch_category_items(tid, page, filters):
            if len(video_list) >= PAGE_SIZE:
                break
            vid = self._get_subject_id(it)
            if not vid or vid in seen:
                continue
            seen.add(vid)
            vod = self._parse_item(it)
            if vod:
                video_list.append(vod)

        total = len(video_list)
        result = {
            'list': video_list,
            'page': page,
            'pagecount': page if total < PAGE_SIZE else page + 1,
            'limit': PAGE_SIZE,
            'total': total if total < PAGE_SIZE else 999999,
        }
        self.page_cache.set(cache_key, result)
        return result

    # ---------------- 对外接口 ----------------
    def homeContent(self, filter=False):
        classes = [
            {"type_id": "hot_movie", "type_name": "热门电影"},
            {"type_id": "tv_hot", "type_name": "热播剧集"},
            {"type_id": "anime_hot", "type_name": "热门动漫"},
            {"type_id": "show_hot", "type_name": "热播综艺"},
            {"type_id": "movie_filter", "type_name": "豆瓣电影"},
            {"type_id": "tv_filter", "type_name": "豆瓣剧集"},
            {"type_id": "show_filter", "type_name": "豆瓣综艺"},
            {"type_id": "anime", "type_name": "动漫剧集"},
            {"type_id": "hot_anime", "type_name": "动漫电影"},
            {"type_id": "top_250", "type_name": "电影Top250"},
        ]
        return {"class": classes, "filters": FILTER_CONFIG, "list": []}

    def homeVideoContent(self):
        return {"list": []}

    def categoryContent(self, tid, pg=1, filter=False, extend=None):
        try:
            page = max(1, int(pg or 1))
        except (TypeError, ValueError):
            page = 1
        filters = {}
        if isinstance(extend, dict):
            filters = extend
        elif isinstance(extend, str) and extend.strip():
            try:
                filters = json.loads(extend)
            except Exception:
                filters = {}
        if not isinstance(filters, dict):
            filters = {}
        return self._category(tid, page, filters)

    # ---------------- 详情 ----------------
    def _fetch_subject_detail(self, sid):
        for path in ('/movie/%s', '/tv/%s'):
            data = self._request_douban(DOUBAN_HOST + (path % sid))
            if data and (data.get('title') or data.get('id')):
                return data
        return None

    @staticmethod
    def _names(seq):
        out = []
        for x in (seq or []):
            if isinstance(x, dict):
                n = x.get('name') or x.get('title')
            else:
                n = x
            if n:
                out.append(str(n))
        return ','.join(out)

    def detailContent(self, ids):
        if isinstance(ids, (list, tuple)):
            sid = ids[0] if ids else ''
        else:
            sid = ids
        sid = str(sid or '').strip()
        if not sid:
            return {"list": []}

        # ★ 混合源: 搜索命中 wsyzy 的 id 带 wsy_ 前缀 -> 直接构建 wsyzy 详情
        if sid.startswith('wsy_'):
            return self._build_wsyzy_detail(sid[4:])

        d = self._fetch_subject_detail(sid)
        if not d:
            return {"list": []}

        pic_obj = d.get('pic') or d.get('cover') or {}
        if isinstance(pic_obj, dict):
            pic_url = pic_obj.get('large') or pic_obj.get('normal') or pic_obj.get('url') or ''
        else:
            pic_url = pic_obj if isinstance(pic_obj, str) else ''
        if pic_url:
            pic_url = re.sub(r'img\d+\.doubanio\.com', 'img1.doubanio.com', pic_url)

        rating = d.get('rating') or {}
        score = self._fmt_score(rating.get('value') if isinstance(rating, dict) else None)
        year = str(d.get('year') or '')
        area = self._names(d.get('countries'))
        genres = self._names(d.get('genres'))
        intro = (d.get('intro') or d.get('card_subtitle') or '').strip()
        vname = d.get('title') or '未知'

        # ★ 混合源: 豆瓣管 UI, 播放按片名查 wsyzy 取真实地址
        vod_play_from = '豆瓣'
        vod_play_url = '豆瓣$%s' % sid
        wsy = self._wsy_lookup_by_name(sid, vname, year)
        if wsy:
            vod_play_from = '豆瓣'
            vod_play_url = wsy

        vod = {
            'vod_id': sid,
            'vod_name': vname,
            'vod_pic': (pic_url + PIC_SUFFIX) if pic_url else '',
            'vod_year': year,
            'vod_area': area,
            'vod_lang': '',
            'vod_actor': self._names(d.get('actors')),
            'vod_director': self._names(d.get('directors')),
            'vod_content': intro,
            'type_name': genres,
            'vod_remarks': ("%s分" % score) if score != '0' else '',
            'vod_play_from': vod_play_from,
            'vod_play_url': vod_play_url,
        }
        return {"list": [vod]}

    # ---------------- 搜索 ----------------
    def _normalize_search_item(self, it):
        if not isinstance(it, dict):
            return None
        t = it.get('target') or it.get('subject') or {}
        if isinstance(t, dict) and (t.get('id') or t.get('title')):
            merged = dict(t)
            merged.setdefault('title', it.get('title') or t.get('title'))
            merged.setdefault('rating', it.get('rating') or t.get('rating'))
            return merged
        if it.get('id') or it.get('title'):
            return it
        return None

    def searchContent(self, key, quick=False, pg=1):
        try:
            page = max(1, int(pg or 1))
        except (TypeError, ValueError):
            page = 1
        kw = str(key or '').strip()
        if not kw:
            return {"list": [], "page": page, "pagecount": 1}

        count = PAGE_SIZE
        start = (page - 1) * count

        video_list = []
        for wid, name, pic in self._wsy_search(kw):
            video_list.append({
                "vod_id": "wsy_%s" % wid,
                "vod_name": name,
                "vod_pic": pic,  # 搜索结果封面可能为空, 用户接受(不影响播放)
                "vod_remarks": "",
            })
        pagecount = 1 if len(video_list) < PAGE_SIZE else page + 1
        return {"list": video_list, "page": page, "pagecount": pagecount}
    # ================= 播放 (实时清洗代理去广告) =================
    def playerContent(self, flag, id, vipFlags=None):
        header = {
            "User-Agent": WSY_UA,
            "Referer": WSY_SITE,
        }
        raw = str(id or '').strip()
        # 情形1: id 已是 '集名$真实地址'(detailContent 命中 wsyzy 时填入) -> 直接取地址
        play_url = raw
        if '$' in play_url:
            play_url = play_url.rsplit('$', 1)[-1]
        play_url = unquote(play_url) if play_url else play_url
        if play_url and re.match(r'^https?://', play_url, re.I):
            # ★ 去广告: 实时清洗代理为主链路 ——
            #   先同步清洗一次判是否有广告(结果进单条缓存): 有广告且本地服务可用 ->
            #   返回 http://127.0.0.1:<port>/adfree?url=<原链>, 播放器请求到达时再实时
            #   拉源站 m3u8 清洗后返回(分片/KEY URI 已重写为 CDN 绝对地址, 分片直连 CDN);
            #   无广告 -> 直接原链; 本地服务不可用 / 清洗失败 -> 原链(带广告但能播)。
            real_url = play_url
            if '.m3u8' in play_url.lower():
                try:
                    real_url, note = player_url(
                        play_url, fallback_file=ADFREE_FILE_FALLBACK)
                    if note != 'no-ad':
                        self._log('去广告:%s -> %s' % (note, real_url[:110]))
                except Exception as e:
                    self._log('去广告异常, 回退原链: %s' % repr(e)[:110])
                    real_url = play_url
            return {
                "parse": 0,
                "url": real_url,
                "header": json.dumps(header, ensure_ascii=False)
            }

        # 情形2/3: 非 m3u8 直链(豆瓣 sid 占位 / wsyzy wid 占位) -> 按片名重查 wsyzy 取真实地址
        sid = raw
        if '$' in sid:
            sid = sid.rsplit('$', 1)[-1]
        if sid.startswith('wsy_'):
            sid = sid[4:]
        name = ''
        try:
            detail = self._fetch_subject_detail(sid)
            if detail:
                name = detail.get('title') or ''
        except Exception as e:
            self._log("取片名失败 sid=%s: %s" % (sid, e))
        if name:
            wsy = self._wsy_lookup_by_name(sid, name)
            if wsy:
                # wsy 是 '集名$地址#...$$$...' 串, 取首个源首集真实地址后走同一去广告链路
                first = wsy.split('$$$')[0].split('#')[0]
                if '$' in first:
                    first = first.split('$', 1)[-1]
                if re.match(r'^https?://', first, re.I):
                    real_url = first
                    if '.m3u8' in first.lower():
                        try:
                            real_url, note = player_url(
                                first, fallback_file=ADFREE_FILE_FALLBACK)
                            if note != 'no-ad':
                                self._log('去广告:%s -> %s' % (note, real_url[:110]))
                        except Exception as e:
                            self._log('去广告异常, 回退原链: %s' % repr(e)[:110])
                            real_url = first
                    return {"parse": 0, "url": real_url,
                            "header": json.dumps(header, ensure_ascii=False)}
        # 兜底: 都没取到真实地址, 退回豆瓣页面(无法直链播放)
        return {"parse": 1, "url": "https://movie.douban.com/subject/%s/" % sid,
                "header": json.dumps(header, ensure_ascii=False)}

    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        pass

    def destroy(self):
        try:
            self.session.close()
        except Exception:
            pass
        # 关停去广告本地服务
        try:
            if _DEFAULT_PROXY is not None:
                _DEFAULT_PROXY.shutdown()
        except Exception:
            pass

    def localProxy(self, param):
        """FongMi 壳 /proxy 回调: 壳把请求转给本方法, 返回真实流地址字符串。
        用于把去广告代理地址(127.0.0.1)回源到真实 m3u8(壳的网络栈可达源站)。"""
        src = None
        if isinstance(param, dict):
            src = param.get('url') or param.get('src')
            if not src:
                for _k, _v in param.items():
                    if isinstance(_v, str) and _v.startswith(('http://', 'https://')) and '.m3u8' in _v:
                        src = _v
                        break
            if not src:
                try:
                    _joined = ' '.join(str(_v) for _v in param.values())
                    _mm = re.search(r'https?://[^\s&"\']+', _joined)
                    if _mm:
                        src = _mm.group(0)
                except Exception:
                    src = None
        elif isinstance(param, str):
            src = param
        if not src:
            return ''
        if '://' not in src and '%' in src:
            try:
                src = unquote(src)
            except Exception:
                pass
        for _t in ('&action=', '&src=', '&do='):
            if _t in src:
                src = src.split(_t)[0]
        return src
