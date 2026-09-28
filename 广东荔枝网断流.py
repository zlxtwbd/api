import sys
import re
import json
import time
import random
import struct
import requests
import urllib.parse
import hashlib
import hmac
import base64
import ssl
import socket
from base64 import b64encode, b64decode
from urllib.parse import quote, urljoin, urlparse, parse_qs
from urllib3 import disable_warnings
from base.spider import Spider
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import warnings

warnings.filterwarnings('ignore', message='Unverified HTTPS request')
disable_warnings()

# 检查 socks 支持
try:
    from requests.packages.urllib3.contrib.socks import SOCKSProxyManager
    SOCKS_AVAILABLE = True
except ImportError:
    SOCKS_AVAILABLE = False


class Spider(Spider):
    """
    广东荔枝网（GRTN）直播 Spider
    适用于 TVBox/FongMi/OK影视/绿豆类客户端
    """
    
    # ==================== 频道数据 ====================
    CHANNEL_MAP = {
        43: '广东卫视',
        44: '广东珠江',
        45: '广东新闻',
        46: '大湾区卫视（海外版）',
        47: '广东体育',
        48: '广东民生',
        51: '大湾区卫视',
        53: '广东影视',
        16: '4K超高清',
        54: '广东少儿',
        66: '嘉佳卡通',
        42: '南方购物',
        15: '岭南戏曲',
        74: '广东移动',
        111: '现代教育',
        100: '广东台经典剧',
        99: 'GRTN健康频道',
        75: 'GRTN文化频道',
        102: 'GRTN生活频道',
    }
    
    # 频道图标
    LOGOS = {
        43: 'https://cdn.jsdelivr.net/gh/wanglindl/TVlogo@main/img/Guangdong.png',
        44: 'https://cdn.jsdelivr.net/gh/wanglindl/TVlogo@main/img/Guangdong.png',
        51: 'https://cdn.jsdelivr.net/gh/wanglindl/TVlogo@main/img/Guangdong.png',
    }
    
    # ==================== 分类定义 ====================
    CATS = [
        {'name': '广东卫视', 'ids': [43, 44, 45, 46, 47, 48, 51, 53]},
        {'name': '高清频道', 'ids': [16, 54, 66]},
        {'name': '特色频道', 'ids': [42, 15, 74, 111]},
        {'name': 'GRTN频道', 'ids': [100, 99, 75, 102]},
    ]
    
    # ==================== 初始化 ====================
    
    def init(self, extend=""):
        self.session = self._create_session()
        self.cache_dir = self._init_cache_dir()
        self.common_headers = {
            'Accept': '*/*',
            'Connection': 'keep-alive',
            'Referer': 'https://www.gdtv.cn/',
            'Origin': 'https://www.gdtv.cn',
            'User-Agent': 'Mozilla/5.0 (Linux; U; Android 9)'
        }
        self.session.headers.update(self.common_headers)
        
    def _create_session(self) -> requests.Session:
        s = requests.Session()
        retry_strategy = Retry(total=2, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry_strategy)
        s.mount('http://', adapter)
        s.mount('https://', adapter)
        return s
    
    def _init_cache_dir(self) -> str:
        import os
        cache_dir = os.path.join(os.path.dirname(__file__), 'gdtvcache')
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, mode=0o775, exist_ok=True)
        return cache_dir
    
    def getName(self):
        return "广东荔枝网直播"
    
    # ==================== 蜘蛛壳接口 ====================
    
    def homeContent(self, filter=False):
        classes = [
            {'type_id': str(i), 'type_name': c['name']}
            for i, c in enumerate(self.CATS)
        ]
        result = {'class': classes}
        if filter:
            result['filters'] = {}
        return result
    
    def homeVideoContent(self):
        return self.categoryContent('0', '1', False, {})
    
    def categoryContent(self, tid, pg='1', filter=False, extend=None):
        try:
            cat_index = int(str(tid))
        except (TypeError, ValueError):
            cat_index = 0
            
        if cat_index < 0 or cat_index >= len(self.CATS):
            return self._empty_page()
            
        cat = self.CATS[cat_index]
        vod_list = []
        for cid in cat['ids']:
            name = self.CHANNEL_MAP.get(cid, f'频道{cid}')
            vod_list.append({
                'vod_id': str(cid),
                'vod_name': name,
                'vod_pic': self.LOGOS.get(cid, ''),
                'vod_remarks': '直播',
            })
        return {
            'page': 1,
            'pagecount': 1,
            'limit': len(vod_list),
            'total': len(vod_list),
            'list': vod_list,
        }
    
    def detailContent(self, ids):
        if not ids:
            return {'list': []}
        cid = int(str(ids[0]).strip())
        name = self.CHANNEL_MAP.get(cid, f'频道{cid}')
        vod = {
            'vod_id': str(cid),
            'vod_name': name,
            'vod_pic': self.LOGOS.get(cid, ''),
            'type_name': '电视直播',
            'vod_remarks': '直播',
            'vod_play_from': '广东荔枝网',
            'vod_play_url': f'直播${cid}',
            'vod_content': f'{name} 高清直播',
        }
        return {'list': [vod]}
    
    def playerContent(self, flag, id, vipFlags=None):
        cid = str(id).split('$$$')[-1].strip()
        try:
            channel_id = int(cid)
        except ValueError:
            return {'parse': 0, 'url': '', 'header': {}}
            
        if channel_id not in self.CHANNEL_MAP:
            return {'parse': 0, 'url': '', 'header': {}}
            
        try:
            play_url = self._get_play_url(channel_id)
            if play_url:
                return {
                    'parse': 1,  # 使用本地代理解析
                    'url': play_url,
                    'header': {
                        'User-Agent': 'Mozilla/5.0 (Linux; U; Android 9)',
                        'Referer': 'https://www.gdtv.cn/',
                        'Origin': 'https://www.gdtv.cn',
                    },
                }
        except Exception as e:
            print(f"[广东荔枝网] 获取播放地址失败: {e}")
            
        return {'parse': 0, 'url': '', 'header': {}}
    
    def searchContent(self, key, quick=False, pg='1'):
        keyword = str(key or '').strip().lower()
        if not keyword:
            return {'list': []}
        videos = []
        for cid, name in self.CHANNEL_MAP.items():
            if keyword in str(cid).lower() or keyword in name.lower():
                videos.append({
                    'vod_id': str(cid),
                    'vod_name': name,
                    'vod_pic': self.LOGOS.get(cid, ''),
                    'vod_remarks': '直播',
                })
        return {'list': videos}
    
    def isVideoFormat(self, url):
        return False
    
    def manualVideoCheck(self):
        return False
    
    def localProxy(self, param):
        """
        代理请求，用于处理M3U8和TS资源
        param 可能是 dict 或 string
        """
        try:
            # 解析参数
            if isinstance(param, dict):
                url = param.get('url', '')
            else:
                url = str(param)
            
            if not url or not url.startswith(('http://', 'https://')):
                return [400, 'text/plain; charset=utf-8', b'Bad Request']
            
            # 解析URL，获取真实目标地址
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            
            # 检查是否是代理请求 (包含 do=py 或 u= 参数)
            target_url = None
            
            # 方式1: 从 u 参数获取真实URL
            if 'u' in query_params:
                target_url = query_params['u'][0]
            # 方式2: 从 url 参数获取 (某些客户端使用)
            elif 'url' in query_params:
                target_url = query_params['url'][0]
            # 方式3: 直接是原始URL
            else:
                target_url = url
            
            # 如果是代理地址但没提取到目标，尝试解码
            if 'do=py' in url and not target_url:
                # 尝试从URL中提取编码后的目标
                import re
                match = re.search(r'[?&]u=([^&]+)', url)
                if match:
                    target_url = urllib.parse.unquote(match.group(1))
                else:
                    target_url = url
            
            # 如果没有目标URL，直接返回错误
            if not target_url:
                return [400, 'text/plain; charset=utf-8', b'No target URL']
            
            # 判断是否是M3U8
            is_m3u8 = '.m3u8' in target_url.lower() or '.m3u' in target_url.lower()
            
            # 请求目标资源
            headers = {
                'User-Agent': 'Mozilla/5.0 (Linux; U; Android 9)',
                'Referer': 'https://www.gdtv.cn/',
                'Origin': 'https://www.gdtv.cn',
                'Accept': '*/*',
                'Connection': 'keep-alive',
            }
            
            # 如果是M3U8，使用流式请求
            response = self.session.get(
                target_url,
                headers=headers,
                timeout=30,
                stream=is_m3u8,
                verify=False
            )
            response.raise_for_status()
            
            # 处理M3U8
            if is_m3u8:
                content = response.text
                # 重写M3U8中的资源URL
                rewritten = self._rewrite_m3u8(content, target_url)
                return [
                    200,
                    'application/vnd.apple.mpegurl',
                    rewritten.encode('utf-8')
                ]
            else:
                # 处理TS或其他资源
                content_type = response.headers.get('Content-Type', 'video/MP2T')
                return [200, content_type, response.content]
                
        except Exception as e:
            error_msg = f'Proxy Error: {str(e)}'.encode('utf-8')
            return [502, 'text/plain; charset=utf-8', error_msg]
    
    def _rewrite_m3u8(self, content, base_url):
        """重写M3U8内容，将资源URL替换为代理URL"""
        if not content:
            return content
            
        parsed = urlparse(base_url)
        base_dir = base_url.rsplit('/', 1)[0] + '/'
        
        output = []
        uri_pattern = re.compile(r'URI=(["\'])(.*?)\1', re.I)
        
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                output.append(raw_line)
                continue
                
            # 处理 EXT-X-KEY 中的 URI
            if line.startswith('#EXT-X-KEY'):
                def replace_uri(match):
                    uri = match.group(2)
                    # 补全URL
                    if not uri.startswith(('http://', 'https://')):
                        if uri.startswith('/'):
                            uri = f"{parsed.scheme}://{parsed.netloc}{uri}"
                        else:
                            uri = urljoin(base_dir, uri)
                    # 代理URI
                    proxy_uri = self._get_proxy_url(uri)
                    return f'URI={match.group(1)}{proxy_uri}{match.group(1)}'
                output.append(uri_pattern.sub(replace_uri, raw_line))
                continue
                
            # 保留注释行
            if line.startswith('#'):
                output.append(raw_line)
                continue
                
            # 处理资源URL (TS, M3U8等)
            if line.endswith('.ts') or line.endswith('.m3u8') or line.endswith('.m3u'):
                # 补全URL
                if not line.startswith(('http://', 'https://')):
                    if line.startswith('/'):
                        line = f"{parsed.scheme}://{parsed.netloc}{line}"
                    else:
                        line = urljoin(base_dir, line)
                # 代理资源
                output.append(self._get_proxy_url(line))
            else:
                output.append(raw_line)
                
        return '\n'.join(output) + '\n'
    
    def _get_proxy_url(self, upstream):
        """生成代理链接"""
        try:
            proxy_base = self.getProxyUrl()
        except Exception:
            return upstream
        if not proxy_base:
            return upstream
        separator = '&' if '?' in proxy_base else '?'
        return f'{proxy_base}{separator}u={quote(upstream, safe="")}'
    
    def destroy(self):
        try:
            self.session.close()
        except Exception:
            pass
    
    def _empty_page(self):
        return {
            'page': 1,
            'pagecount': 1,
            'limit': 0,
            'total': 0,
            'list': [],
        }
    
    # ==================== 播放地址获取 ====================
    
    def _get_play_url(self, channel_id: int) -> str:
        """获取直播播放地址"""
        try:
            # 1. getParam
            resp = self.session.get(
                "https://tcdn-api.itouchtv.cn/getParam",
                timeout=10,
                verify=False
            )
            node = json.loads(resp.text)['node']
            
            # 2. wsnode
            wsnode = self._get_wsnode(node)
            
            # 3. 频道 API
            api_url = f"https://gdtv-api.gdtv.cn/api/tv/v2/tvChannel/{channel_id}?node={base64.b64encode(wsnode.encode()).decode()}"
            
            # 预检请求
            self.session.options(api_url, timeout=10, verify=False)
            
            # 签名请求
            t, sign = self._generate_signature(api_url)
            extra_headers = {
                'X-Itouchtv-Ca-Key': '89541943007407288657755311868534',
                'X-Itouchtv-Ca-Signature': sign,
                'X-Itouchtv-Ca-Timestamp': t,
                'X-Itouchtv-Client': 'WEB_M',
                'X-Itouchtv-Device-Id': 'WEBM_0',
            }
            
            resp = self.session.get(api_url, headers=extra_headers, timeout=15, verify=False)
            data = json.loads(resp.text)
            play_url_obj = json.loads(data['playUrl'])
            return play_url_obj.get('hd', '')
            
        except Exception as e:
            print(f"[广东荔枝网] 获取播放地址失败: {e}")
            return ''
    
    # ==================== WebSocket ====================
    
    def _get_wsnode(self, node: str) -> str:
        """通过WebSocket获取wsnode"""
        host, port, path = 'tcdn-ws.itouchtv.cn', 3800, '/connect'
        raw_key = bytes([random.randint(33, 126) for _ in range(16)])
        key = base64.b64encode(raw_key).decode()
        
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        sock = socket.create_connection((host, port), timeout=8)
        ssl_sock = context.wrap_socket(sock, server_hostname=host)
        
        upgrade = (f"GET {path} HTTP/1.1\r\n"
                   f"Host: {host}:{port}\r\n"
                   f"Upgrade: websocket\r\n"
                   f"Connection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\n"
                   f"Sec-WebSocket-Version: 13\r\n\r\n")
        ssl_sock.sendall(upgrade.encode())
        
        response = b''
        while b'\r\n\r\n' not in response:
            response += ssl_sock.recv(4096)
            
        if b'101' not in response.split(b'\r\n')[0]:
            raise Exception('WebSocket upgrade failed')
            
        msg = json.dumps({"route": "getwsparam", "message": node})
        ssl_sock.sendall(self._encode_ws_frame(msg))
        recv = self._recv_ws_frame(ssl_sock)
        ssl_sock.close()
        
        data = json.loads(recv.decode())
        if 'wsnode' not in data:
            raise Exception('wsnode missing in WebSocket response')
        return data['wsnode']
    
    def _encode_ws_frame(self, text: str) -> bytes:
        data = text.encode()
        length = len(data)
        mask = [random.randint(0, 255) for _ in range(4)]
        frame = bytearray()
        frame.append(0x81)
        if length < 126:
            frame.append(0x80 | length)
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack('>H', length))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack('>Q', length))
        frame.extend(mask)
        masked = bytes([data[i] ^ mask[i % 4] for i in range(length)])
        frame.extend(masked)
        return bytes(frame)
    
    def _recv_ws_frame(self, sock) -> bytes:
        header = sock.recv(2)
        if len(header) < 2:
            raise Exception('Incomplete WebSocket frame header')
        opcode = header[0] & 0x0f
        if opcode != 0x1:
            raise Exception(f'Unexpected opcode {opcode}')
        payload_len = header[1] & 0x7f
        if payload_len == 126:
            buf = sock.recv(2)
            payload_len = struct.unpack('>H', buf)[0]
        elif payload_len == 127:
            buf = sock.recv(8)
            payload_len = struct.unpack('>Q', buf)[0]
        data = b''
        while len(data) < payload_len:
            chunk = sock.recv(min(4096, payload_len - len(data)))
            if not chunk:
                break
            data += chunk
        return data
    
    # ==================== 签名 ====================
    
    def _generate_signature(self, url: str) -> tuple:
        t = str(int(time.time() * 1000))
        k = 'dfkcY1c3sfuw1Cii9DWj8UO3iQy2hqlDxyvDXd1oVMxwYVDSgeB6phO9eW1dfuwX'
        msg = f"GET\n{url}\n{t}\n"
        sign = base64.b64encode(hmac.new(k.encode(), msg.encode(), hashlib.sha256).digest()).decode()
        return t, sign