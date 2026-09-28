#!/usr/bin/python
# -*- coding: utf-8 -*-
import re, json, requests, urllib.parse
from lxml import etree
from base.spider import Spider

class Spider(Spider):
    def getName(self):
        return "\u5948\u98de\u5de5\u5382"

    def init(self, extend=""):
        self.host = "https://www.netflixgc.net"
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Referer": self.host + "/"}
        self.api_url = self.host + "/index.php/ds_api/vod"
        self.categories = [
            {"type_id": "1", "type_name": "\u7535\u5f71"},
            {"type_id": "2", "type_name": "\u8fde\u7eed\u5267"},
            {"type_id": "3", "type_name": "\u6f2b\u5267"},
            {"type_id": "23", "type_name": "\u7efc\u827a"},
            {"type_id": "24", "type_name": "\u7eaa\u5f55\u7247"}
        ]
        cls_opts = [{"n":"\u5168\u90e8","v":""},{"n":"\u559c\u5267","v":"\u559c\u5267"},{"n":"\u7231\u60c5","v":"\u7231\u60c5"},{"n":"\u6050\u6016","v":"\u6050\u6016"},{"n":"\u52a8\u4f5c","v":"\u52a8\u4f5c"},{"n":"\u79d1\u5e7b","v":"\u79d1\u5e7b"},{"n":"\u5267\u60c5","v":"\u5267\u60c5"},{"n":"\u72af\u7f6a","v":"\u72af\u7f6a"},{"n":"\u5947\u5e7b","v":"\u5947\u5e7b"},{"n":"\u60ac\u7591","v":"\u60ac\u7591"},{"n":"\u60ca\u609a","v":"\u60ca\u609a"},{"n":"\u5bb6\u5ead","v":"\u5bb6\u5ead"},{"n":"\u5192\u9669","v":"\u5192\u9669"},{"n":"\u8fd0\u52a8","v":"\u8fd0\u52a8"},{"n":"\u6218\u4e89","v":"\u6218\u4e89"},{"n":"\u707e\u96be","v":"\u707e\u96be"},{"n":"NETFLIX","v":"NETFLIX"},{"n":"HBO","v":"HBO"},{"n":"BBC ONE","v":"BBC ONE"},{"n":"HULU","v":"HULU"},{"n":"APPLE TV","v":"APPLE TV"},{"n":"FOX","v":"FOX"}]
        area_opts = [{"n":"\u5168\u90e8","v":""},{"n":"\u7f8e\u56fd","v":"\u7f8e\u56fd"},{"n":"\u65e5\u672c","v":"\u65e5\u672c"},{"n":"\u97e9\u56fd","v":"\u97e9\u56fd"},{"n":"\u5370\u5ea6","v":"\u5370\u5ea6"},{"n":"\u82f1\u56fd","v":"\u82f1\u56fd"},{"n":"\u6cd5\u56fd","v":"\u6cd5\u56fd"},{"n":"\u745e\u5178","v":"\u745e\u5178"},{"n":"\u4fc4\u7f57\u65af","v":"\u4fc4\u7f57\u65af"},{"n":"\u6cf0\u56fd","v":"\u6cf0\u56fd"}]
        year_opts = [{"n":"\u5168\u90e8","v":""}]
        for y in range(2025, 1999, -1):
            year_opts.append({"n": str(y), "v": str(y)})
        order_opts = [{"n":"\u6309\u6700\u65b0","v":"time"},{"n":"\u6309\u6700\u70ed","v":"hits"},{"n":"\u6309\u8bc4\u5206","v":"score"}]
        lang_opts = [{"n":"\u5168\u90e8","v":""},{"n":"\u82f1\u8bed","v":"\u82f1\u8bed"},{"n":"\u65e5\u8bed","v":"\u65e5\u8bed"},{"n":"\u97e9\u8bed","v":"\u97e9\u8bed"},{"n":"\u6cd5\u8bed","v":"\u6cd5\u8bed"},{"n":"\u4fc4\u8bed","v":"\u4fc4\u8bed"},{"n":"\u6cf0\u8bed","v":"\u6cf0\u8bed"},{"n":"\u745e\u5178\u8bed","v":"\u745e\u5178\u8bed"},{"n":"\u5370\u5ea6\u8bed","v":"\u5370\u5ea6\u8bed"}]
        self.filters = {}
        cls_opts = [{"n":"全部","v":""},{"n":"喜剧","v":"喜剧"},{"n":"爱情","v":"爱情"},{"n":"恐怖","v":"恐怖"},{"n":"动作","v":"动作"},{"n":"科幻","v":"科幻"},{"n":"剧情","v":"剧情"},{"n":"犯罪","v":"犯罪"},{"n":"奇幻","v":"奇幻"},{"n":"悬疑","v":"悬疑"},{"n":"惊悚","v":"惊悚"},{"n":"家庭","v":"家庭"},{"n":"冒险","v":"冒险"},{"n":"运动","v":"运动"},{"n":"战争","v":"战争"},{"n":"灾难","v":"灾难"},{"n":"NETFLIX","v":"NETFLIX"},{"n":"HBO","v":"HBO"},{"n":"BBC ONE","v":"BBC ONE"},{"n":"HULU","v":"HULU"},{"n":"APPLE TV","v":"APPLE TV"},{"n":"FOX","v":"FOX"}]
        area_opts = [{"n":"全部","v":""},{"n":"美国","v":"美国"},{"n":"日本","v":"日本"},{"n":"韩国","v":"韩国"},{"n":"印度","v":"印度"},{"n":"英国","v":"英国"},{"n":"法国","v":"法国"},{"n":"瑞典","v":"瑞典"},{"n":"俄罗斯","v":"俄罗斯"},{"n":"泰国","v":"泰国"}]
        year_opts = [{"n":"全部","v":""}]
        for y in range(2025, 1999, -1):
            year_opts.append({"n": str(y), "v": str(y)})
        order_opts = [{"n":"按最新","v":"time"},{"n":"按最热","v":"hits"},{"n":"按评分","v":"score"}]
        lang_opts = [{"n":"全部","v":""},{"n":"英语","v":"英语"},{"n":"日语","v":"日语"},{"n":"韩语","v":"韩语"},{"n":"法语","v":"法语"},{"n":"俄语","v":"俄语"},{"n":"泰语","v":"泰语"},{"n":"瑞典语","v":"瑞典语"},{"n":"印度语","v":"印度语"}]
        for cat in self.categories:
            self.filters[cat["type_id"]] = [
                {"key": "class", "name": "类型", "value": cls_opts},
                {"key": "area", "name": "地区", "value": area_opts},
                {"key": "year", "name": "年份", "value": year_opts},
                {"key": "lang", "name": "语言", "value": lang_opts},
                {"key": "by", "name": "排序", "value": order_opts}
            ]
        for cat in self.categories:
            self.filters[cat["type_id"]] = [
                {"key": "class", "name": "\u7c7b\u578b", "value": cls_opts},
                {"key": "area", "name": "\u5730\u533a", "value": area_opts},
                {"key": "year", "name": "\u5e74\u4efd", "value": year_opts},
                {"key": "lang", "name": "\u8bed\u8a00", "value": lang_opts},
                {"key": "by", "name": "\u6392\u5e8f", "value": order_opts}
            ]

    def _post(self, url, data):
        try:
            r = requests.post(url, data=data, headers=self.headers, timeout=15)
            return r.json()
        except:
            return {}

    def _get(self, url):
        try:
            r = requests.get(url, headers=self.headers, timeout=15)
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except:
            return None

    def _fix(self, u):
        if not u: return ""
        if u.startswith("//"): return "https:" + u
        if u.startswith("/"): return self.host + u
        return u

    def _ajax_list(self, tid, pg, extend):
        params = {"mid": "1", "type": tid, "limit": "24", "page": str(pg)}
        if extend:
            for key in ["class", "area", "year", "lang", "by", "letter"]:
                val = extend.get(key, "")
                if val:
                    params[key] = val
        if "by" not in params:
            params["by"] = "time"
        data = self._post(self.api_url, params)
        items = []
        for v in data.get("list", []):
            pic = v.get("vod_pic", "")
            if pic and pic.startswith("//"): pic = "https:" + pic
            items.append({"vod_id": str(v.get("vod_id", "")), "vod_name": v.get("vod_name", ""), "vod_pic": pic, "vod_remarks": v.get("vod_remarks", "")})
        return {"list": items, "page": int(pg), "pagecount": data.get("pagecount", 1), "limit": data.get("limit", 24), "total": data.get("total", 0)}

    def homeContent(self, filter):
        try:
            result = self._ajax_list("0", "1", {"by": "time"})
            result["class"] = self.categories
            result["filters"] = self.filters
            return result
        except Exception as e:
            print(f"[{self.getName()}] \u9996\u9875\u9519\u8bef: {e}")
            return {"class": self.categories, "list": [], "filters": self.filters}

    def categoryContent(self, tid, pg, filter, extend):
        try:
            return self._ajax_list(tid, pg, extend)
        except Exception as e:
            print(f"[{self.getName()}] \u5206\u7c7b\u9519\u8bef: {e}")
            return {"list": [], "page": int(pg), "pagecount": 0, "limit": 24, "total": 0}

    def detailContent(self, ids):
        try:
            vid = ids[0]
            html = self._get(f"{self.host}/detail/{vid}.html")
            if not html: return {"list": []}

            tree = etree.HTML(html)

            name = ("".join(tree.xpath('//h1/text()')) or "".join(tree.xpath('//div[contains(@class,"detail-info")]//h1/text()'))).strip()

            pic = self._fix(
                "".join(tree.xpath('//div[contains(@class,"detail-pic")]//img/@data-original')) or
                "".join(tree.xpath('//div[contains(@class,"detail-pic")]//img/@data-src')) or
                "".join(tree.xpath('//div[contains(@class,"detail-pic")]//img/@src'))
            )

            sn, se = [], []

            tabs = tree.xpath('//div[contains(@class,"anthology-tab")]//*[contains(@class,"swiper-slide")]')

            pls = tree.xpath('//div[contains(@class,"anthology-list-box")]//ul[contains(@class,"anthology-list-play")]')

            if pls:
                for i, p in enumerate(pls):

                    sname = f"\u7ebf\u8def{i+1}"

                    if i < len(tabs):
                        t = tabs[i]

                        sname = (
                            t.get("data-dropdown-value", "") or
                            (t.xpath("text()[1]")[0] if t.xpath("text()[1]") else "")
                        ).strip() or sname

                    ep = [
                        f"{''.join(a.xpath('.//text()')).strip()}${self._fix(a.get('href', '') or a.get('data-play-href', ''))}"
                        for a in p.xpath(".//a")
                        if (a.get('href', '') or a.get('data-play-href', ''))
                        and ''.join(a.xpath('.//text()')).strip()
                    ]

                    if ep:
                        sn.append(sname)
                        se.append("#".join(ep))

            if not sn:
                panels = tree.xpath('//div[contains(@class,"panel") and contains(@class,"clearfix")]') or tree.xpath('//div[contains(@class,"panel")]')

                for p in panels:

                    sname = (
                        "".join(p.xpath('.//a[contains(@class,"option")]/@title')) or
                        "".join(p.xpath('.//a[contains(@class,"option")]/text()'))
                    ).strip() or "\u9ed8\u8ba4\u7ebf\u8def"

                    ep = [
                        f"{''.join(a.xpath('.//text()')).strip()}${self._fix(a.get('href', ''))}"
                        for a in p.xpath(".//ul[contains(@class,'playlistlink')]//a")
                        if a.get('href', '') and ''.join(a.xpath('.//text()')).strip()
                    ]

                    if ep:
                        sn.append(sname)
                        se.append("#".join(ep))

            if not sn:
                ep = [
                    f"{''.join(a.xpath('.//text()')).strip()}${self._fix(a.get('href', '') or a.get('data-play-href', ''))}"
                    for a in tree.xpath('//a[contains(@href,"/play/")]')
                    if (a.get('href', '') or a.get('data-play-href', ''))
                    and ''.join(a.xpath('.//text()')).strip()
                ]

                if ep:
                    sn.append("\u9ed8\u8ba4\u7ebf\u8def")
                    se.append("#".join(ep))

            return {
                "list": [{
                    "vod_id": vid,
                    "vod_name": name,
                    "vod_pic": pic,
                    "vod_play_from": "$$$".join(sn),
                    "vod_play_url": "$$$".join(se)
                }]
            }

        except Exception as e:
            print(f"[{self.getName()}] \u8be6\u60c5\u9519\u8bef: {e}")
            return {"list": []}

    def searchContent(self, key, quick, pg="1"):
        try:
            html = self._get(f"{self.host}/vodsearch/{urllib.parse.quote(key)}----------{pg}---.html")
            if not html: return {"list": [], "page": int(pg)}
            tree = etree.HTML(html)
            results, seen = [], set()
            items = tree.xpath('//div[contains(@class,"module-search-item")]') or tree.xpath('//div[contains(@class,"module-item")]')
            for item in items:
                try:
                    a = item.xpath('.//a[contains(@href,"/detail/")]') or item.xpath('.//a[contains(@href,"/") and .//img]')
                    if not a: continue
                    m = re.search(r'/(?:detail|voddetail)/(\d+)\.html', a[0].get("href", ""))
                    if not m or m.group(1) in seen: continue
                    seen.add(m.group(1))
                    img = item.xpath('.//img')
                    title = ("".join(item.xpath('.//h3//text()')) or a[0].get("title", "") or "".join(a[0].xpath(".//text()"))).strip()
                    pic = self._fix((img[0].get("data-original", "") or img[0].get("data-src", "") or img[0].get("src", "")) if img else "")
                    remark = "".join(item.xpath('.//span[contains(@class,"light")]//text()')).strip()
                    results.append({"vod_id": m.group(1), "vod_name": title, "vod_pic": pic, "vod_remarks": remark})
                except: continue
            print(f"[{self.getName()}] \u641c\u7d22\u5339\u914d {len(results)} \u6761")
            return {"list": results, "page": int(pg)}
        except Exception as e:
            print(f"[{self.getName()}] \u641c\u7d22\u9519\u8bef: {e}")
            return {"list": [], "page": int(pg)}

    def playerContent(self, flag, id, vipFlags):
        try:
            url = id if id.startswith("http") else self._fix(id)

            html = self._get(url)

            m = re.search(r'player_aaaa\s*=\s*(\{.*?\})', html)

            if not m:
                return {"parse": 1, "url": url, "header": self.headers}

            data = json.loads(m.group(1))

            play = unquote(data.get("url", ""))

            if not play:
                return {"parse": 1, "url": url, "header": self.headers}

            if not play.startswith("http"):
                play = self._fix(play)

            return {
                "parse": 0,
                "playUrl": "",
                "url": play,
                "header": self.headers
            }

        except:
            return {"parse": 1, "url": id, "header": self.headers}