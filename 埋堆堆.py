# 埋堆堆 H5 (m.mddcloud.com.cn) 爬虫插件 | 基于base.spider | 按OK影视模板3重写
# 站型：Nuxt SSR + activity.mddcloud.com.cn JSON API（MD5 签名）
# 链路：分类词表 -> 列表(筛选/分页) -> 详情(剧集表) -> 取流(m3u8)
# 依赖：仅标准库（与模板不同：未引入requests，签名POST/备用GET均用urllib实现）
import json
import time
import hashlib
from urllib import request

# 核心：继承OK影视内置base.spider.Spider类（必须保留）
from base.spider import Spider as BaseSpider


class Spider(BaseSpider):
    # 分类词表缓存（6h，类级共享，跨实例复用）
    _tbl = {"t": 0, "v": None}

    # ========== 插件基础配置（必改：插件名称） ==========
    def getName(self):
        """插件名称：OK影视插件列表中显示的名称"""
        return "埋堆堆"

    # ========== 初始化全局配置（必改：站点/接口/签名/全局变量） ==========
    def init(self, extend=""):
        """
        初始化方法：配置站点地址、接口地址、签名私钥、请求头、全局变量
        extend：插件扩展参数（可留空）
        """
        # 继承父类初始化（必须保留，不可删除）
        super().init(extend)

        # 1. 目标站点基础地址（H5站点，数据全走JSON接口）
        self.site_url = "https://m.mddcloud.com.cn"
        # 2. 数据接口地址 + 签名私钥（接口签名用，勿修改）
        self.api = "https://activity.mddcloud.com.cn"
        self.pk = "8a4af424bd714be5b2baacbe3cf5bbbe0131effeb92e99356ef1662884558f28"
        # 3. 请求头（JSON接口：需携带 Content-Type / Origin / Referer）
        self.headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": ("Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/126.0.0.0 Mobile Safari/537.36"),
            "Origin": self.site_url,
            "Referer": self.site_url + "/",
            "Accept": "application/json, text/plain, */*",
        }
        # 4. 自定义全局参数
        # 注意：本站列表接口只接受 rows=12（实测 rows=8/10/11/13/20 等一律返回空列表），
        # 因此每页条数必须固定为 12，否则首页/分类/筛选会全部为空。
        self.page_size = 12  # 每页条数（接口rows，必须为12）
        self.total = 999999  # 模拟总条数（接口不返回真实总数，填大值即可）

    # ========== 通用请求方法（签名POST为主，GET备用） ==========
    def _sos(self, d):
        """
        接口签名参数序列化：key=value& 拼接
        键排序后逐项拼接，dict/list 值走紧凑JSON，其余直接str
        """
        buf = []
        for k in sorted(d.keys()):
            v = d[k]
            if isinstance(v, (dict, list)):
                buf.append(k + "=" + json.dumps(v, ensure_ascii=False,
                                                separators=(',', ':')) + "&")
            else:
                buf.append(k + "=" + str(v) + "&")
        return "".join(buf)

    def _post(self, path, data):
        """
        签名POST请求：全局复用（本站全部数据接口均走此方法）
        :param path: 接口路径（如 /api/vod/classify/list.action）
        :param data: 请求体字典（自动参与MD5签名）
        :return: 响应data字段 / None（msgType!=0 或请求异常）
        """
        try:
            ts = int(time.time() * 1000)
            src = "time:%d|privateKey:%s|data:%s" % (ts, self.pk, self._sos(data))
            body = json.dumps({
                "time": ts,
                "sign": hashlib.md5(src.encode("utf-8")).hexdigest(),
                "data": data,
            }, ensure_ascii=False).encode("utf-8")
            req = request.Request(self.api + path, data=body, headers=self.headers)
            with request.urlopen(req, timeout=15) as r:
                j = json.loads(r.read().decode("utf-8", "ignore"))
            return j.get("data") if j.get("msgType") == 0 else None
        except Exception:
            return None

    def fetch(self, url, timeout=10):
        """
        通用GET请求方法：备用（本站数据接口均为签名POST，
        如需抓取静态页面/做探测时可直接复用）
        :param url: 目标请求地址
        :param timeout: 超时时间
        :return: 页面文本 / None（请求失败）
        """
        try:
            req = request.Request(url, headers={
                "User-Agent": self.headers["User-Agent"],
                "Referer": self.site_url + "/",
            })
            with request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception:
            return None

    # ========== 内部辅助方法 ==========
    def _classify(self):
        """分类词表：接口返回后缓存6小时（21600s），缓存有效直接复用"""
        if self._tbl["v"] and time.time() - self._tbl["t"] < 21600:
            return self._tbl["v"]
        d = self._post("/api/vod/classify/list.action", {})
        if isinstance(d, list) and d:
            self._tbl["v"] = d
            self._tbl["t"] = time.time()
        return self._tbl["v"] or []

    def _mk(self, it):
        """列表条目 -> 标准视频字典（vod_id/vod_name/vod_pic/vod_remarks）"""
        total = it.get("totalNum") or 0
        return {
            "vod_id": it.get("vodUuid") or it.get("uuid"),
            "vod_name": it.get("name") or "",
            "vod_pic": it.get("downImage") or it.get("coverImage") or "",
            "vod_remarks": ("共%d集" % total) if total else "",
            "vod_year": "",
        }

    # ========== 壳兼容接口（保留原源能力，OK影视/FongMi 通用） ==========
    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def destroy(self):
        pass

    def localProxy(self, param):
        return [200, "text/plain", "ok"]

    # ========== 首页分类接口（必改：站点分类+筛选） ==========
    def homeContent(self, filter=False):
        """
        首页分类：返回站点分类列表、筛选条件与首页推荐列表
        :param filter: 分类过滤参数（一般留空，无需处理）
        :return: {'class': 分类列表, 'filters': 筛选条件, 'list': 推荐列表}
        """
        try:
            # 1. 主分类（保留前面几个主分类即可：最热/最新/好评/全部）
            cls = [{"type_id": "hot", "type_name": "最热"},
                   {"type_id": "new", "type_name": "最新"},
                   {"type_id": "good", "type_name": "好评"},
                   {"type_id": "all", "type_name": "全部"}]
            # 2. 筛选条件（分类/年份/语言/地区：分类词表能收进筛选的一律收，均含「全部」项）
            fl = {}
            for f in self._classify():
                code = f.get("fieldCode") or ""
                # 题材(material)作为筛选项，筛选键统一为 type
                if code == "material":
                    code = "type"
                if code not in ("type", "year", "lang", "region"):
                    continue
                vals = [{"n": "全部", "v": ""}]
                for w in f.get("words") or []:
                    if w.get("uuid"):
                        vals.append({"n": w.get("word") or "", "v": w["uuid"]})
                fl[code] = {"key": code,
                            "name": (f.get("fieldName") or code).replace("_name", ""),
                            "value": vals}
            # 3. 每个主分类都挂上完整筛选组（词表缺失的维度给「全部」兜底）
            filters = {}
            for c in cls:
                filters[c["type_id"]] = [fl.get(k, {"key": k, "name": k,
                                                    "value": [{"n": "全部", "v": ""}]})
                                         for k in ("type", "year", "lang", "region")]
            # 4. 首页推荐列表（最热分类第1页）
            videos = self.categoryContent("hot", 1, False, {}).get("list", [])
            return {"class": cls, "filters": filters, "list": videos}
        except Exception:
            return {"class": [], "filters": {}, "list": []}

    def homeVideoContent(self):
        """首页推荐：复用最热分类第1页"""
        return {"list": self.categoryContent("hot", 1, False, {}).get("list", [])}

    # ========== 分类列表接口（必改：站点列表解析规则） ==========
    def categoryContent(self, tid, pg, filter=False, extend=None):
        """
        分类列表：根据分类ID+页码（含题材/年份/语言/地区筛选），返回视频列表
        :param tid: 分类ID（homeContent中定义的type_id；m:xxx 为题材）
        :param pg: 页码（字符串，需转整数）
        :param filter/extend: 过滤/扩展参数（type/year/lang/region 筛选值放 extend）
        :return: 固定格式 {'list': [视频字典, ...], 'page': 当前页, 'pagecount': 总页数,
                           'limit': 每页条数, 'total': 总条数}
        """
        try:
            # 1. 拼接筛选条件（分类 + 年份/语言/地区，去重；兼容 m: 前缀）
            ext = extend or {}
            uu = []
            if isinstance(tid, str) and tid.startswith("m:") and len(tid) > 2:
                uu.append(tid[2:])
            for k in ("type", "year", "lang", "region"):
                v = ext.get(k) or ""
                if v.startswith("m:") and len(v) > 2:
                    v = v[2:]
                if v and v not in uu:
                    uu.append(v)
            # 2. 排序规则（new=1 最新, good=2 好评, 其余默认综合）
            order = {"new": 1, "good": 2}.get(tid, 0)
            # 3. 页码处理（兼容非数字传参，不可改）
            pg = int(pg) if str(pg).isdigit() else 1
            # 4. 请求列表接口（分页规则：startRow = (pg-1)*rows）
            d = self._post("/api/vod/classify/word/search.action", {
                "uuidList": uu,
                "rows": self.page_size,
                "startRow": (pg - 1) * self.page_size,
                "isVip": -1,
                "orderType": order,
            })
            # 5. 解析视频列表（过滤无ID条目）
            lst = [self._mk(x) for x in (d or [])
                   if x.get("vodUuid") or x.get("uuid")]
            # 6. 固定返回格式（接口不返回真实总数，pagecount/total 填大值）
            return {"page": pg, "pagecount": 9999, "limit": self.page_size,
                    "total": self.total, "list": lst}
        except Exception:
            return {"page": 1, "pagecount": 1, "limit": self.page_size,
                    "total": 0, "list": []}

    # ========== 视频详情接口（必改：站点详情解析规则） ==========
    def detailContent(self, ids):
        """
        视频详情：根据vod_id，返回视频播放信息（剧集表）
        :param ids: 视频ID数组（固定取第一个：ids[0]）
        :return: 固定格式 {'list': [详情字典]}
        播放串格式：集名$vodUuid|集数，多集用#分隔（playerContent 按|拆分）
        """
        try:
            # 1. 获取视频ID/地址（清洗竖线附加参数，不可改）
            vid = ids[0] if isinstance(ids, list) else ids
            vid = str(vid)
            if "|" in vid:
                vid = vid.split("|")[0]
            # 2. 请求详情接口
            d = self._post("/api/vod/getVodInfo.action", {"vodUuid": vid, "num": 1})
            if not d:
                return {"list": []}
            # 3. 构造剧集播放串（集名$vodUuid|集数）
            plays = []
            for s in d.get("vodSactionList") or []:
                nm = s.get("name") or ("第%s集" % s.get("num"))
                plays.append("%s$%s|%s" % (nm, s.get("vodUuid") or vid, s.get("num")))
            if not plays:
                plays = ["正片$%s|1" % vid]
            # 4. 年份（首播时间戳 -> 年）
            yr = ""
            ts = d.get("firstSowingTime")
            if ts:
                try:
                    yr = time.strftime("%Y", time.localtime(int(ts) / 1000))
                except Exception:
                    yr = ""
            # 5. 构造详情字典（固定格式，不可改核心字段名）
            return {"list": [{
                "vod_id": vid,
                "vod_name": d.get("name") or "",
                "vod_pic": d.get("coverImage") or d.get("downImage") or "",
                "type_name": "综艺" if d.get("vodType") == 2 else (
                    "剧集" if d.get("vodType") == 0 else ""),
                "vod_year": yr,
                "vod_area": "",
                "vod_remarks": "共%s集" % (d.get("totalNum") or len(plays)),
                "vod_actor": d.get("starring") or "",
                "vod_director": d.get("director") or "",
                "vod_content": (d.get("introduction") or "").strip(),
                "vod_play_from": "埋堆堆",
                "vod_play_url": "#".join(plays),
            }]}
        except Exception:
            return {"list": []}

    # ========== 搜索接口（站点无搜索接口，自建「翻页过滤」搜索） ==========
    def searchContent(self, key, quick=False, pg=1):
        """
        搜索接口：根据关键词返回搜索结果列表
        :param key: 搜索关键词
        :param quick: 快速搜索标识（一般留空，无需处理）
        :param pg: 页码（自建搜索一次性返回全部命中，页码仅回显）
        :return: 和categoryContent一致的列表格式
        站点H5未开放搜索入口与接口，此处采用自建搜索：
        按「最新」排序自第1页起翻页拉取列表，客户端按片名包含关键词过滤，
        命中即停（最多 100 页 / 约 10s 上限，避免拖慢播放器）。
        """
        try:
            key = (key or "").strip()
            if not key:
                return {"page": 1, "pagecount": 1, "limit": self.page_size,
                        "total": 0, "list": []}
            kw = key.lower()
            hit, scan, t0 = [], 0, time.time()
            while scan < 100 and time.time() - t0 < 10:
                d = self._post("/api/vod/classify/word/search.action", {
                    "uuidList": [],
                    "rows": self.page_size,
                    "startRow": scan * self.page_size,
                    "isVip": -1,
                    "orderType": 1,  # 1=最新（新剧在前，命中更快）
                })
                items = [x for x in (d or [])
                         if x.get("vodUuid") or x.get("uuid")]
                if not items:
                    break
                for x in items:
                    if kw in (x.get("name") or "").lower():
                        hit.append(self._mk(x))
                scan += 1
                if hit:  # 命中即停
                    break
                if len(items) < self.page_size:
                    break
            return {"page": int(pg) if str(pg).isdigit() else 1, "pagecount": 1,
                    "limit": self.page_size, "total": len(hit), "list": hit}
        except Exception:
            return {"page": 1, "pagecount": 1, "limit": self.page_size,
                    "total": 0, "list": []}

    # ========== 播放解析接口（必改：站点取流规则） ==========
    def playerContent(self, flag, id, vipFlags=None):
        """
        播放解析：解析播放地址，返回可直接播放的m3u8链接
        :param flag: 播放线路标识（一般留空，无需处理）
        :param id: 待解析的地址（detailContent中拼接的「集名$vodUuid|集数」）
        :param vipFlags: VIP标识（一般留空，无需处理）
        :return: 固定格式 {"parse": 0/1, "url": 最终播放地址, "header": 请求头}
        """
        try:
            # 1. 拆分「vodUuid|集数」（取流接口参数）
            s = str(id)
            if "|" in s:
                vu, num = s.split("|")[0], s.split("|")[1]
            else:
                vu, num = s, "1"
            # 2. 请求试看取流接口（返回m3u8直链，无需二次解析 parse=0）
            d = self._post("/api/vod/getTrySaction.action",
                           {"vodUuid": vu, "num": int(num)})
            url = ""
            if isinstance(d, dict):
                url = (d.get("tryM3u8Url") or d.get("tryUrl")
                       or d.get("tryMp4Url") or "")
            # 3. 解析失败返回空（不可改）
            if not url:
                return {"parse": 0, "playUrl": "", "url": "", "header": ""}
            # 4. 固定返回格式（m3u8直链，parse=0 无需二次解析）
            return {"parse": 0, "playUrl": "", "url": url, "header": ""}
        except Exception:
            return {"parse": 0, "playUrl": "", "url": "", "header": ""}