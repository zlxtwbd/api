# coding=utf-8
"""
有广采王 —— 全网影视聚合爬虫（弹幕逻辑已抽离到独立模块 弹幕.py）

- 多源聚合（全球、厂长、母鸡、极速、百度、优质、红牛、魔都，共 8 源）
- 已移除首页推荐视频列表（homeVideoContent 返回空）
- 弹幕自动匹配委托给 弹幕.py 的 DanmakuMatcher（见 getDependence / init / detailContent / playerContent）
"""
import json
import time
import concurrent.futures
from urllib.parse import quote

from base.spider import Spider as BaseSpider


class SourceHTTPError(RuntimeError):
    """资源站接口明确故障（请求失败 / 非 2xx）时抛出。

    指南 §1.6：站点坏了要返回可诊断错误，不能伪装成空列表。
    多源聚合场景会捕获它做降级，只有「所有源都坏」才向外抛。"""


class Spider(BaseSpider):
    SOURCES = {
        '全球': {'name': '️全球', 'api': 'https://360zy.com/api.php/provide/vod/'},
        '厂长': {'name': '️厂长', 'api': 'https://api.changzhang.tv/api.php/provide/vod/from/changzhangm3u8/'},
        '母鸡': {'name': '️母鸡', 'api': 'https://mujiziyuan00.com/api.php/provide/vod/?ac=list'},
        '极速': {'name': '极速', 'api': 'https://jszyapi.com/api.php/provide/vod/from/jsm3u8/at/json'},
        '百度': {'name': '️百度', 'api': 'https://api.apibdzy.com/api.php/provide/vod/'},
        '优质': {'name': '️优质', 'api': 'https://api.yyzy-tv.vip/inc/apijson.php'},
        '红牛': {'name': '️红牛', 'api': 'https://www.hongniuzy3.com/api.php/provide/vod/from/hnm3u8/at/josn/'},
        '魔都': {'name': '️魔都', 'api': 'https://www.mdzyapi.com/api.php/provide/vod/'}
    }

    def init(self, extend=""):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
        }
        # 弹幕匹配器：独立模块 弹幕.py（详见 getDependence），由框架 loadModule 加载。
        self.danmaku = self.loadModule("弹幕").DanmakuMatcher(self.net)

    def getDependence(self):
        return ["弹幕"]

    # ==================== 核心爬虫方法 ====================
    def build_url(self, base, query):
        if '?' in base:
            if base.endswith('?') or base.endswith('&'):
                return base + query
            else:
                return base + '&' + query
        else:
            return base + '?' + query

    def fetch_json(self, url, timeout=10, strict=False):
        """统一走 self.net.req；net.req 不抛，须手检 error 再检 code（指南 §3.3）。

        strict=True 用于「单个源决定结果」的分类/详情：请求失败或非 2xx 抛
        SourceHTTPError（403 / 风控页不能伪装成空列表，指南 §1.6）。
        多源聚合（首页/搜索）用默认 False 降级跳过该源。
        """
        try:
            _ms = int(timeout * 1000)
            response = self.net.req(url, {
                "headers": self.headers,
                "timeout": _ms,
                "callTimeout": _ms,
            })
            if response.get("error"):
                raise RuntimeError("请求失败: %s" % (response.get("error"),))
            code = response.get("code", 0)
            if not 200 <= code < 300:
                raise RuntimeError("HTTP %s" % code)
            content = response.get("content", "")
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            return json.loads(content)
        except Exception as exc:
            if strict:
                raise SourceHTTPError("资源站接口异常：%s" % exc) from exc
            return None

    def clean_item(self, item, source_key, source_name, is_detail=False):
        if not is_detail:
            item['vod_id'] = f"{source_key}@@{item['vod_id']}"
        old_remark = item.get('vod_remarks', '')
        item['vod_remarks'] = f"{source_name} | {old_remark}" if old_remark else source_name
        item.pop('vod_down_from', None)
        item.pop('vod_down_url', None)
        return item

    def homeContent(self, filter=False):
        classes = []
        filters = {}
        def fetch_class(source_key, source_info):
            url = self.build_url(source_info['api'], "ac=list")
            data = self.fetch_json(url, timeout=4)
            return source_key, source_info, data
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch_class, k, v): k for k, v in self.SOURCES.items()}
            for future in concurrent.futures.as_completed(futures):
                source_key, source_info, data = future.result()
                classes.append({'type_id': source_key, 'type_name': source_info['name']})
                filter_values = [{'n': '全部(最新)', 'v': ''}]
                if data and 'class' in data:
                    for c in data['class']:
                        filter_values.append({'n': c['type_name'], 'v': c['type_id']})
                filters[source_key] = [{'key': 'cateId', 'name': '分类', 'value': filter_values}]
        return {'class': classes, 'filters': filters, 'list': []}

    def homeVideoContent(self):
        return {'list': []}

    def categoryContent(self, tid, pg=1, filter=False, extend=None):
        if tid not in self.SOURCES:
            return {'list': [], 'page': pg, 'pagecount': 0, 'limit': 20, 'total': 0}
        source = self.SOURCES[tid]
        if extend is None:
            extend = {}
        real_tid = extend.get('cateId', '')
        query = f"ac=detail&pg={pg}"
        if real_tid:
            query += f"&t={real_tid}"
        url = self.build_url(source['api'], query)
        # 分类只由这一个源决定结果：接口故障必须抛错，不能伪装成"这页没内容"
        data = self.fetch_json(url, timeout=10, strict=True)
        if not data:
            return {'list': [], 'page': pg, 'pagecount': 0, 'limit': 20, 'total': 0}
        video_list = []
        for item in data.get('list', []):
            video_list.append(self.clean_item(item, tid, source['name'], is_detail=False))
        return {
            'list': video_list,
            'page': data.get('page', pg),
            'pagecount': data.get('pagecount', 0),
            'limit': data.get('limit', 20),
            'total': data.get('total', 0)
        }

    def detailContent(self, ids):
        # 指南 §2.2：入参是 ID 列表，须保序、不去重、处理完整集合（旧版只读 ids[0]）
        if not ids:
            return {'list': []}
        result_list = []
        errors = []
        for full_id in ids:
            if '@@' not in full_id:
                continue
            source_key, real_id = full_id.split('@@', 1)
            source = self.SOURCES.get(source_key)
            if not source:
                continue
            url = self.build_url(source['api'], f"ac=detail&ids={real_id}")
            try:
                data = self.fetch_json(url, timeout=10, strict=True)
            except SourceHTTPError as exc:
                # 单个源故障先降级，只有「全部故障且无任何结果」才向外抛
                errors.append(str(exc))
                continue
            if not isinstance(data, dict) or not isinstance(data.get('list'), list):
                continue  # 结构完整但没有条目 = 真空，返回空即可
            for item in data['list']:
                cleaned = self.clean_item(item, source_key, source['name'], is_detail=True)
                cleaned['vod_id'] = full_id
                result_list.append(cleaned)
        if errors and not result_list:
            raise SourceHTTPError("全部资源站详情接口异常：%s" % errors[0])
        # 弹幕上下文同时只跟随一个播放对象，取结果集首条；构建失败不影响详情页
        if result_list:
            try:
                self.danmaku.build_context(result_list[0])
            except Exception:
                pass
        return {'list': result_list}

    def searchContent(self, key, quick, pg="1"):
        keyword = key.strip()
        if not keyword:
            return {'list': [], 'page': pg, 'pagecount': 0, 'limit': 40, 'total': 0}
        encoded_keyword = quote(keyword)
        def search_source(source_key, source_info):
            test_urls = [
                self.build_url(source_info['api'], f"ac=detail&wd={encoded_keyword}&pg={pg}"),
                self.build_url(source_info['api'], f"ac=list&wd={encoded_keyword}&pg={pg}")
            ]
            got_response = False
            for url in test_urls:
                data = self.fetch_json(url, timeout=8)
                if data is None:
                    continue
                # 至少拿到一次合法响应：用于区分「全站故障」与「确实搜不到」
                got_response = True
                if not data:
                    continue
                items = None
                if isinstance(data, dict):
                    if 'list' in data:
                        items = data['list']
                    elif 'data' in data and isinstance(data['data'], dict) and 'list' in data['data']:
                        items = data['data']['list']
                    elif 'data' in data and isinstance(data['data'], list):
                        items = data['data']
                if items:
                    return source_key, source_info, data, items, True
            return source_key, source_info, None, [], got_response
        all_items = []
        max_pagecount = 0
        ok_sources = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(search_source, k, v): k for k, v in self.SOURCES.items()}
            for future in concurrent.futures.as_completed(futures):
                try:
                    source_key, source_info, data, items, ok = future.result()
                except Exception:
                    continue
                if ok:
                    ok_sources += 1
                if items:
                    for item in items:
                        try:
                            if not isinstance(item, dict) or not item.get('vod_id'):
                                continue
                            all_items.append(self.clean_item(item, source_key, source_info['name'], is_detail=False))
                        except Exception:
                            pass
                    # 少数源返回的是 list，不能对非 dict 调 .get()
                    if isinstance(data, dict) and data.get('pagecount', 0) > max_pagecount:
                        max_pagecount = data.get('pagecount', 0)
        # 所有源都不可达时不能伪装成「没有搜索结果」（指南 §1.6）
        if ok_sources == 0:
            raise SourceHTTPError("全部资源站搜索接口不可达或返回异常")
        seen = set()
        unique = []
        for v in all_items:
            if v['vod_id'] not in seen:
                seen.add(v['vod_id'])
                unique.append(v)
        return {
            'list': unique,
            'page': pg,
            'pagecount': max_pagecount if max_pagecount else 1,
            'limit': 40,
            # 指南 §1.5：total 只填精确总数，绝不估算。这里是多站当页结果合并去重后的
            # 精确条数（不再是 9999 这种拍脑袋值）；能否翻页由 page < pagecount 决定。
            'total': len(unique)
        }

    def playerContent(self, flag, id, vipFlags=None):
        # ===== 弹幕：播放后异步匹配 + 推送本地桥（本版本不要求中文名）=====
        # match() 只起后台线程、立即返回，播放前零阻塞；匹配结果由弹幕.py 推送到
        # 127.0.0.1:9978/action?do=refresh&type=danmaku&path=<弹幕url>。
        # 不再返回 danmaku 数组（那是上一版为承载中文名用的，本版不需要）。
        try:
            self.danmaku.match(id)
        except Exception:
            pass
        # header 保持 dict：指南 §2.6 的 parse:0 示例用 json.dumps 字符串，而 §4 的
        # parse:1 央视频案例直接给 dict，两种写法宿主都接受；此处不改，避免无谓回归。
        result = {
            'parse': 0,
            'url': id,
            'header': {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
            }
        }
        return result

    def isVideoFormat(self, url):
        return False

    def manualVideoCheck(self):
        return False

    def destroy(self):
        # 切源/退出时作废后台匹配线程、清空上下文（弹幕模块 reset() 本就是为此写的）
        try:
            if getattr(self, 'danmaku', None) is not None:
                self.danmaku.reset()
        except Exception:
            pass
