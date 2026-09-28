import sys, re, json, requests
sys.path.append('..')
from urllib.parse import quote
from lxml import etree
from base.spider import Spider

# ===== 筛选选项（模块级常量，不依赖 init 调用顺序，避免 homeContent 在 init 之前被调用时崩溃）=====
CLASS_OPTS = [{"n": "全部", "v": ""}] + [{"n": k, "v": k} for k in ["喜剧","爱情","动作","恐怖","科幻","剧情","犯罪","奇幻","战争","悬疑","动画","文艺","纪录","传记","歌舞","古装","历史","惊悚","伦理"]]
AREA_OPTS = [{"n": "全部", "v": ""}] + [{"n": k, "v": k} for k in ["大陆","香港","台湾","美国","韩国","日本","泰国","新加坡","马来西亚","印度","英国","法国","加拿大","西班牙","俄罗斯","其它"]]
YEAR_OPTS = [{"n": "全部", "v": ""}] + [{"n": str(y), "v": str(y)} for y in range(2026, 2004, -1)]

ZONGYI_CLASS = [{"n": "全部", "v": ""}] + [{"n": k, "v": k} for k in ["脱口秀","真人秀","搞笑","访谈","生活","音乐","美食","游戏","旅游","时尚","益智","职场","晚会","纪录"]]
ZONGYI_AREA = [{"n": "全部", "v": ""}] + [{"n": k, "v": k} for k in ["大陆","香港","台湾","美国","韩国","日本","英国","其他"]]
ZONGYI_YEAR = [{"n": "全部", "v": ""}] + [{"n": str(y), "v": str(y)} for y in range(2026, 2010, -1)]

DONGMAN_CLASS = [{"n": "全部", "v": ""}] + [{"n": k, "v": k} for k in ["热血","搞笑","科幻","剧情","冒险","奇幻","战斗","校园","恋爱","治愈","悬疑","推理","机战","运动","美食","历史","少儿"]]
DONGMAN_AREA = [{"n": "全部", "v": ""}] + [{"n": k, "v": k} for k in ["大陆","日本","美国","韩国","法国","英国","其他"]]
DONGMAN_YEAR = [{"n": "全部", "v": ""}] + [{"n": str(y), "v": str(y)} for y in range(2026, 2000, -1)]

DUANJU_CLASS = [{"n": "全部", "v": ""}] + [{"n": k, "v": k} for k in ["喜剧","爱情","动作","剧情","悬疑","奇幻","古装","都市","逆袭"]]
DUANJU_AREA = [{"n": "全部", "v": ""}] + [{"n": k, "v": k} for k in ["大陆","其他"]]
DUANJU_YEAR = [{"n": "全部", "v": ""}] + [{"n": str(y), "v": str(y)} for y in range(2026, 2020, -1)]

LUNLI_CLASS = [{"n": "全部", "v": ""}] + [{"n": k, "v": k} for k in ["剧情","爱情","惊悚"]]
LUNLI_AREA = [{"n": "全部", "v": ""}] + [{"n": k, "v": k} for k in ["大陆","香港","台湾","美国","法国","日本","韩国"]]
LUNLI_YEAR = [{"n": "全部", "v": ""}] + [{"n": str(y), "v": str(y)} for y in range(2026, 2000, -1)]

cateManual = [
    {"name": "电影", "tid": "1", "type": "dianying", "filters": [{"key": "class", "name": "类型", "value": CLASS_OPTS}, {"key": "area", "name": "地区", "value": AREA_OPTS}, {"key": "year", "name": "年份", "value": YEAR_OPTS}]},
    {"name": "连续剧", "tid": "2", "type": "lianxuju", "filters": [{"key": "class", "name": "类型", "value": CLASS_OPTS}, {"key": "area", "name": "地区", "value": AREA_OPTS}, {"key": "year", "name": "年份", "value": YEAR_OPTS}]},
    {"name": "综艺", "tid": "3", "type": "zongyi", "filters": [{"key": "class", "name": "类型", "value": ZONGYI_CLASS}, {"key": "area", "name": "地区", "value": ZONGYI_AREA}, {"key": "year", "name": "年份", "value": ZONGYI_YEAR}]},
    {"name": "动漫", "tid": "4", "type": "dongman", "filters": [{"key": "class", "name": "类型", "value": DONGMAN_CLASS}, {"key": "area", "name": "地区", "value": DONGMAN_AREA}, {"key": "year", "name": "年份", "value": DONGMAN_YEAR}]},
    {"name": "短剧", "tid": "5", "type": "duanju", "filters": [{"key": "class", "name": "类型", "value": DUANJU_CLASS}, {"key": "area", "name": "地区", "value": DUANJU_AREA}, {"key": "year", "name": "年份", "value": DUANJU_YEAR}]},
    {"name": "伦理", "tid": "6", "type": "lunli", "filters": [{"key": "class", "name": "类型", "value": LUNLI_CLASS}, {"key": "area", "name": "地区", "value": LUNLI_AREA}, {"key": "year", "name": "年份", "value": LUNLI_YEAR}]},
]

class Spider(Spider):
    siteUrl = "https://ys2046.lat"
    filterable = True
    searchable = True
    headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1", "Referer": "https://ys2046.lat/"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cateManual = cateManual

    def init(self, extend=""):
        self.cateManual = cateManual

    def _get(self, url):
        try: return requests.get(url, headers=self.headers, timeout=10).text
        except: return ""

    def _parse_list(self, html):
        tree = etree.HTML(html)
        return [{"vod_id": a.get("href", ""),
                 "vod_name": t.text.strip() if t.text else "", 
                 "vod_pic": a.get("data-original") or a.get("data-src") or a.get("src") or "", 
                 "vod_remarks": r[0].strip() if (r := li.xpath('.//span[contains(@class,"fed-list-remarks")]/text()')) else ""}
                for li in tree.xpath('//li[contains(@class,"fed-list-item")]')
                for a in li.xpath('.//a[contains(@class,"fed-list-pics")]')
                for t in li.xpath('.//a[contains(@class,"fed-list-title")]')
                if a.get("href") and (a.get("data-original") or a.get("data-src") or a.get("src")) and t.text]

    def homeContent(self, filter):
        classes = [{"type_id": c["tid"], "type_name": c["name"]} for c in self.cateManual]
        filters = {c["tid"]: c["filters"] for c in self.cateManual}
        return {"class": classes, "filters": filters, "type": "影视"}

    def homeVideoContent(self):
        return {"list": self._parse_list(self._get(self.siteUrl))}

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg)
        if isinstance(extend, str):
            try:
                extend = json.loads(extend) if extend.strip() else {}
            except Exception:
                extend = {}
        extend = extend or {}
        cls = extend.get("class", "")
        area = extend.get("area", "")
        year = extend.get("year", "")
        page = f"{pg}" if pg > 1 else ""
        return {"filter": filter,"page": pg, "pagecount": 9999, "limit": 9999, "total": 9999, "list": self._parse_list(self._get(f"{self.siteUrl}/show/{tid}-{cls}-{area}-{year}--{page}.html"))}

    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, list) else ids
        url = f"{self.siteUrl}{vid}" if not vid.startswith("http") else vid
        html = self._get(url)
        if len(html) < 500:
            return {"list": []}
        tree = etree.HTML(html)
        num_id = ""
        if m := re.search(r'/detail/[\w-]+-(\d+)\.html', url):
            num_id = m.group(1)
        type_name = ""
        for c in self.cateManual:
            if c['type'] in url:
                type_name = c['name']
                break
        name = n[0].strip() if (n := tree.xpath('//h1/a/text()')) else ""
        img = i[0] if (i := tree.xpath('//div[contains(@class,"fed-deta-info")]//img/@data-original | //div[contains(@class,"fed-deta")]//img/@data-src | //a[contains(@class,"fed-list-pics")]/@data-original')) else ""
        
        source_names = tree.xpath('//li[contains(@class,"fed-drop-btns")]//a/text()')
        source_names = [s.strip() for s in source_names if s.strip()]
        
        play_items = tree.xpath('//div[contains(@class,"fed-play-item")]')
        sources = []
        for i, item in enumerate(play_items):
            ep_links = item.xpath('.//a[contains(@href,"/play/")]')
            if not ep_links:
                continue
            first_href = ep_links[0].get("href", "")
            if m := re.search(r'/play/([\w-]+)-(\d+)-(\d+)-(\d+)\.html', first_href):
                vtype, vid_num, sid, nid = m.group(1), m.group(2), m.group(3), m.group(4)
                src_name = source_names[i] if i < len(source_names) else f"线路{sid}"
                sources.append({"name": src_name, "sid": sid, "vtype": vtype, "vid": vid_num})

        parts = []
        for src in sources:
            sid = src["sid"]
            vtype = src["vtype"]
            vid_num = src["vid"]
            ep_list = []
            play_tree = etree.HTML(self._get(f"{self.siteUrl}/play/{vtype}-{vid_num}-{sid}-1.html"))
            seen_nid = set()
            for ep in play_tree.xpath(f'//a[contains(@href,"-{sid}-")]'):
                href = ep.get("href", "")
                if em := re.search(rf'/play/[\w-]+-\d+-{sid}-(\d+)\.html', href):
                    nid = em.group(1)
                    if nid not in seen_nid:
                        ept = ep.text.strip() if ep.text else f"第{nid}集"
                        if ept in ["下集", "上集"]:
                            continue
                        seen_nid.add(nid)
                        ep_list.append(f"{ept}${vid_num}_{sid}_{nid}_{vtype}")
            parts.append("#".join(ep_list) if ep_list else f"第1集${vid_num}_{sid}_1_{vtype}")

        return {"list": [{"vod_id": num_id, "vod_name": name, "vod_pic": img, "type_name": type_name,
                "vod_year": "", "vod_area": "", "vod_remarks": "", "vod_actor": "", "vod_director": "", "vod_content": "",
                "vod_play_from": "$$$".join([s["name"] for s in sources]), "vod_play_url": "$$$".join(parts),}]}


    def searchContent(self, key, quick, pg=1):
        pg = int(pg)
        return {"list": self._parse_list(self._get(f"{self.siteUrl}/search/{quote(key)}---{pg}.html"))}

    def playerContent(self, flag, id, vipFlags):
        parts = id.split("_")
        if len(parts) == 4:
            vid, sid, nid, vtype = parts
            return {"parse": 1, "url": f"{self.siteUrl}/play/{vtype}-{vid}-{sid}-{nid}.html", "header": self.headers}
        if len(parts) == 3:
            vid, sid, nid = parts
            for c in self.cateManual:
                url = f"{self.siteUrl}/play/{c['type']}-{vid}-{sid}-{nid}.html"
                if len(self._get(url)) > 500: return {"parse": 1, "url": url, "header": self.headers}
        return {"parse": 1, "url": id if id.startswith("http") else self.siteUrl + id, "header": self.headers}
