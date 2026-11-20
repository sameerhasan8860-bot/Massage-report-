import asyncio
import aiohttp

FREE_PROXY_SOURCES = [
    "https://api.proxyscrape.com/v3/free-proxy-list/get?request=getproxies&protocol=socks5&timeout=5000&country=all&format=text",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt",
]

_proxy_pool: list[tuple] = []
_index: int = 0

async def fetch_proxies():
    global _proxy_pool
    fetched = set()
    async with aiohttp.ClientSession() as session:
        for url in FREE_PROXY_SOURCES:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    text = await r.text()
                    for line in text.strip().splitlines():
                        line = line.strip()
                        if ":" in line:
                            host, port = line.split(":", 1)
                            try:
                                fetched.add((host, int(port)))
                            except:
                                pass
            except:
                pass
    _proxy_pool = list(fetched)
    print(f"[proxy] loaded {len(_proxy_pool)} proxies")

def get_proxy(account_index: int) -> dict | None:
    if not _proxy_pool:
        return None
    proxy = _proxy_pool[account_index % len(_proxy_pool)]
    return {
        "proxy_type": "socks5",
        "addr": proxy[0],
        "port": proxy[1],
        "rdns": True
    }

async def rotate_forever():
    while True:
        await asyncio.sleep(1)
        global _index
        _index = (_index + 1) % max(1, len(_proxy_pool))
