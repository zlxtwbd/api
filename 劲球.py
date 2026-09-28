# coding=utf-8
"""
劲球直播 (https://m.jqtgy.com) OK影视/TVBox Python爬虫
API: https://jk.jkjqtv.com/app/encryptionTypeHostRecommHotPlay
加密: AES-128-ECB, 密钥: KVksL2jJ6eLOP7cX
分类过滤: 按match_type字段过滤 (1=足球, 2=篮球, >=100=电竞)
播放: 直接返回带签名的m3u8地址，无广告
"""
import sys
import json
import base64
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from base.spider import Spider

sys.path.append('..')

# ============ 全局配置 ============
API_HOST = "https://jk.jkjqtv.com"
AES_KEY = b'KVksL2jJ6eLOP7cX'

HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Referer": "https://m.jqtgy.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 分类配置 (type_id: 0=全部, 1=足球, 2=篮球, 3=电竞)
# match_type: 1=足球, 2=篮球, >=100=电竞(103=DOTA2, 其他=LOL/CSGO等)
CATEGORIES = [
    {"type_id": "0", "type_name": "全部直播"},
    {"type_id": "1", "type_name": "足球"},
    {"type_id": "2", "type_name": "篮球"},
    {"type_id": "3", "type_name": "电竞"},
]


class Spider(Spider):
    """劲球直播爬虫"""

    def getName(self):
        return "劲球直播"

    def init(self, extend=""):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._all_rooms_cache = None
        print(f"[劲球直播] 初始化完成, API={API_HOST}")

    def destroy(self):
        if hasattr(self, 'session'):
            self.session.close()

    # ============ AES加密/解密 ============

    def _aes_encrypt(self, data):
        """AES-128-ECB加密"""
        if isinstance(data, dict):
            data = json.dumps(data, separators=(',', ':'))
        if isinstance(data, str):
            data = data.encode('utf-8')
        cipher = AES.new(AES_KEY, AES.MODE_ECB)
        encrypted = cipher.encrypt(pad(data, AES.block_size))
        return base64.b64encode(encrypted).decode('utf-8')

    def _aes_decrypt(self, data):
        """AES-128-ECB解密"""
        if isinstance(data, str):
            data = base64.b64decode(data)
        cipher = AES.new(AES_KEY, AES.MODE_ECB)
        decrypted = cipher.decrypt(data)
        return unpad(decrypted, AES.block_size).decode('utf-8')

    def _api_request(self, path, params=None):
        """发送加密API请求"""
        url = f"{API_HOST}{path}"
        encrypted_params = {}
        if params:
            for k, v in params.items():
                encrypted_params[k] = self._aes_encrypt(str(v))

        try:
            resp = self.session.get(url, params=encrypted_params, timeout=15)
            if resp.status_code == 200 and resp.text:
                result = json.loads(self._aes_decrypt(resp.text.strip()))
                return result
        except Exception as e:
            print(f"[劲球直播] API请求失败 {path}: {e}")
        return None

    # ============ 分类过滤 ============

    def _is_football(self, room):
        """判断是否足球直播"""
        return room.get('match_type') == 1

    def _is_basketball(self, room):
        """判断是否篮球直播"""
        return room.get('match_type') == 2

    def _is_esports(self, room):
        """判断是否电竞直播 (match_type >= 100, 如103=DOTA2)"""
        mt = room.get('match_type', 0)
        return isinstance(mt, (int, float)) and mt >= 100

    def _filter_by_category(self, rooms, category_id):
        """按分类过滤直播间"""
        if category_id == "0" or not category_id:
            return rooms
        elif category_id == "1":
            return [r for r in rooms if self._is_football(r)]
        elif category_id == "2":
            return [r for r in rooms if self._is_basketball(r)]
        elif category_id == "3":
            return [r for r in rooms if self._is_esports(r)]
        return rooms

    # ============ 首页功能 ============

    def homeContent(self, filter):
        result = {'class': CATEGORIES, 'filters': {}}
        try:
            videos = self._fetch_live_list("0", "1", "30")
            result['list'] = videos
        except Exception as e:
            print(f"[劲球直播] 首页失败: {e}")
            result['list'] = []
        return result

    def homeVideoContent(self):
        return self.homeContent(False)

    # ============ 分类页功能 ============

    def categoryContent(self, tid, pg, filter, extend):
        result = {}
        try:
            videos = self._fetch_live_list(tid, pg, "50")
            result['list'] = videos
            result['page'] = int(pg)
            result['pagecount'] = 1
            result['limit'] = len(videos)
            result['total'] = len(videos)
        except Exception as e:
            print(f"[劲球直播] 分类页失败: {e}")
            result = {'list': [], 'page': int(pg), 'pagecount': 1, 'limit': 0, 'total': 0}
        return result

    def _fetch_live_list(self, category_id, page, page_size):
        """获取直播列表（按match_type真正过滤分类，只保留可播放的主播直播间）"""
        # 获取所有热门直播间（有完整play_url和match_type）
        params = {
            "page": "1",
            "pageSize": "100",
        }
        result = self._api_request("/app/encryptionTypeHostRecommHotPlay", params)
        all_rooms = []
        if result and result.get('code') == '200':
            data = result.get('data', {})
            all_rooms = data.get('list', []) if isinstance(data, dict) else []

        # 所有直播间都显示（live_type=1主播直播间 + live_type=2比赛信号，播放时不带Referer即可正常播放）
        print(f"[劲球直播] 全部{len(all_rooms)}个直播间")

        # 按分类过滤
        filtered_rooms = self._filter_by_category(all_rooms, category_id)
        print(f"[劲球直播] 分类{category_id}: 过滤后{len(filtered_rooms)}个")

        # 解析为视频列表
        videos = []
        for room in filtered_rooms:
            video = self._parse_room(room)
            if video:
                videos.append(video)
        return videos

    def _parse_room(self, room):
        """解析直播间信息"""
        try:
            room_id = room.get('room_id', 0)
            match_id = room.get('match_id', 0)
            live_type = room.get('live_type', 1)

            # 关键修复：live_type=2的比赛信号room_id都是0，必须用match_id作为唯一标识
            # 否则电竞和足球比赛信号的vod_id都是0，详情/播放时会找错直播间
            if room_id == 0 or live_type == 2:
                vod_id = f"match_{match_id}"
            else:
                vod_id = str(room_id)

            name = room.get('room_title', '') or room.get('nickName', '')
            pic = room.get('room_cover', '') or room.get('screen_shots', '') or room.get('portrait', '')
            live_url = room.get('live_url', '')
            match_type = room.get('match_type_name', '') or room.get('short_name_zh', '')
            heat = room.get('heat', 0)
            nick_name = room.get('nickName', '')
            match_type_id = room.get('match_type', 0)

            # 分类标签
            category_tag = ""
            if self._is_football(room):
                category_tag = "足球"
            elif self._is_basketball(room):
                category_tag = "篮球"
            elif self._is_esports(room):
                category_tag = "电竞"

            # 备注信息
            remarks_parts = []
            if category_tag:
                remarks_parts.append(category_tag)
            if match_type and match_type != category_tag:
                remarks_parts.append(match_type)
            if nick_name:
                remarks_parts.append(f"主播:{nick_name}")
            if heat:
                remarks_parts.append(f"热度:{heat}")
            remarks = " | ".join(remarks_parts)

            return {
                "vod_id": vod_id,
                "vod_name": name[:100],
                "vod_pic": pic,
                "vod_remarks": remarks,
                "vod_play_url": live_url,
                "vod_area": category_tag or match_type,
                "match_type": match_type_id,
                "room_id": room_id,
                "match_id": match_id,
                "live_type": live_type,
            }
        except Exception as e:
            print(f"[劲球直播] 解析直播间失败: {e}")
            return None

    # ============ 详情页功能 ============

    def detailContent(self, ids):
        result = {'list': []}
        try:
            if isinstance(ids, list):
                room_id = ids[0]
            else:
                room_id = ids

            print(f"[劲球直播] 获取详情 room_id={room_id}")

            # 从所有直播间中查找
            room_info = self._find_room_by_id(room_id)
            if not room_info:
                print(f"[劲球直播] 未找到直播间 {room_id}")
                return result

            vod = {
                "vod_id": str(room_id),
                "vod_name": room_info.get('vod_name', ''),
                "vod_pic": room_info.get('vod_pic', ''),
                "vod_remarks": room_info.get('vod_remarks', ''),
                "vod_content": f"{room_info.get('vod_name', '')} - {room_info.get('vod_remarks', '')}",
                "vod_play_from": "劲球直播",
                "vod_play_url": f"直播${room_info.get('vod_play_url', '')}",
            }
            result['list'] = [vod]
        except Exception as e:
            print(f"[劲球直播] 详情页失败: {e}")
            import traceback
            traceback.print_exc()
        return result

    def _find_room_by_id(self, room_id):
        """通过vod_id查找直播间信息（支持room_id和match_{match_id}两种格式）"""
        videos = self._fetch_live_list("0", "1", "100")
        for v in videos:
            # 直接匹配vod_id
            if str(v.get('vod_id')) == str(room_id):
                return v
            # 兼容旧格式：匹配room_id
            if str(v.get('room_id')) == str(room_id) and v.get('room_id', 0) != 0:
                return v
            # 兼容match_id格式
            if str(room_id).startswith('match_'):
                mid = str(room_id).replace('match_', '')
                if str(v.get('match_id')) == mid:
                    return v
        return None

    # ============ 播放功能 ============

    def playerContent(self, flag, id, vipFlags):
        """
        播放解析 - 直接返回m3u8地址
        注意：播放地址已带时效签名，直接播放无广告
        电竞直播使用不同域名(bf.njscwh.com)，足球使用bf.nxit.net
        """
        result = {}
        try:
            print(f"[劲球直播] 播放请求 flag={flag}, id={id}")

            play_url = id

            # 如果是room_id，查找播放地址
            if not play_url or not play_url.startswith('http'):
                room_info = self._find_room_by_id(play_url)
                if room_info and room_info.get('vod_play_url'):
                    play_url = room_info['vod_play_url']
                    print(f"[劲球直播] 通过room_id获取播放地址: {play_url[:80]}")

            if play_url and play_url.startswith('http'):
                # 注意：bf.njscwh.com域名(电竞/部分比赛信号)带Referer会返回403，不带Referer才能正常播放
                # bf.nxit.net域名(足球主播直播间)带不带Referer都可以
                # 统一不带Referer，确保所有直播间都能正常播放
                result = {
                    "parse": 0,
                    "playUrl": "",
                    "url": play_url,
                    "header": json.dumps({
                        'User-Agent': HEADERS['User-Agent'],
                    })
                }
                print(f"[劲球直播] 播放地址: {play_url[:80]}")
            else:
                print(f"[劲球直播] 无有效播放地址")
                result = {
                    "parse": 0,
                    "playUrl": "",
                    "url": "",
                    "header": ""
                }
        except Exception as e:
            print(f"[劲球直播] 播放失败: {e}")
            import traceback
            traceback.print_exc()
            result = {
                "parse": 0,
                "playUrl": "",
                "url": id if id else "",
                "header": ""
            }
        return result

    # ============ 搜索功能 ============

    def searchContent(self, key, quick):
        return self.searchContentPage(key, quick, "1")

    def searchContentPage(self, key, quick, page):
        result = {'list': [], 'page': int(page), 'pagecount': 1, 'limit': 0, 'total': 0}
        try:
            print(f"[劲球直播] 搜索: {key}")
            videos = self._fetch_live_list("0", "1", "100")

            # 关键词匹配
            key_lower = key.lower()
            matched = []
            for v in videos:
                name = v.get('vod_name', '').lower()
                remarks = v.get('vod_remarks', '').lower()
                if key_lower in name or key_lower in remarks:
                    matched.append(v)

            result['list'] = matched
            result['limit'] = len(matched)
            result['total'] = len(matched)
            print(f"[劲球直播] 搜索到 {len(matched)} 个结果")
        except Exception as e:
            print(f"[劲球直播] 搜索失败: {e}")
        return result

    # ============ 其他方法 ============

    def isVideoFormat(self, url):
        if not url:
            return False
        return any(ext in url.lower() for ext in ['.m3u8', '.mp4', '.flv', '.ts'])

    def manualVideoCheck(self):
        pass

    def localProxy(self, param):
        return None


if __name__ == "__main__":
    spider = Spider()
    spider.init()

    print("\n=== 测试首页 ===")
    result = spider.homeContent(False)
    print(f"分类数: {len(result.get('class', []))}")
    print(f"视频数: {len(result.get('list', []))}")
    for v in result.get('list', [])[:3]:
        print(f"  {v.get('vod_name')} | {v.get('vod_remarks')}")

    print("\n=== 测试各分类 ===")
    for cat in CATEGORIES:
        result = spider.categoryContent(cat['type_id'], "1", False, {})
        print(f"{cat['type_name']}({cat['type_id']}): {len(result.get('list', []))} 个直播间")
        for v in result.get('list', [])[:2]:
            print(f"  {v.get('vod_name')} | match_type={v.get('match_type')} | play_url={v.get('vod_play_url', '')[:60]}")

    print("\n=== 测试详情+播放 ===")
    for cat in CATEGORIES:
        result = spider.categoryContent(cat['type_id'], "1", False, {})
        if result.get('list'):
            first = result['list'][0]
            print(f"\n{cat['type_name']} - {first.get('vod_name')}")
            detail = spider.detailContent([first['vod_id']])
            if detail.get('list'):
                vod = detail['list'][0]
                print(f"  播放地址: {vod.get('vod_play_url', '')[:100]}")
            play = spider.playerContent("劲球直播", first.get('vod_play_url', ''), [])
            print(f"  parse={play.get('parse')}, url={play.get('url', '')[:80]}")
            break

    spider.destroy()