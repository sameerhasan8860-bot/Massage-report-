# Proxy disabled — was causing 60s+ delays per account
# All connections go direct

_proxy_pool = []

async def fetch_proxies():
    pass

def get_proxy(index: int):
    return None

async def rotate_forever():
    import asyncio
    while True:
        await asyncio.sleep(60)
