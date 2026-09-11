"""模块级共享 httpx.AsyncClient 注册表（性能优化 P1-7）。

每调用新建 AsyncClient 要重付 TCP/TLS 握手 50-300ms（OCR/搜图/生图高频路径）；
改为按名称共享 client，连接池跨调用复用。

- 差异化 timeout / follow_redirects 用每请求参数覆盖（httpx 原生支持，
  语义与新建 client 一致），共享 client 只承载连接池；
- FastAPI lifespan 结束时 close_all() 统一关闭；
- 测试跨用例隔离用 reset_clients()（mock transport 不能串用例）。
"""
import httpx

_registry: dict[str, httpx.AsyncClient] = {}


def get_client(name: str, **kwargs) -> httpx.AsyncClient:
    """按名称取共享 AsyncClient（首次以 kwargs 创建）；已关闭则重建。"""
    c = _registry.get(name)
    if c is None or c.is_closed:
        c = httpx.AsyncClient(**kwargs)
        _registry[name] = c
    return c


async def close_all() -> None:
    """关闭全部共享 client（FastAPI lifespan 关闭钩子）。"""
    for c in list(_registry.values()):
        try:
            await c.aclose()
        except Exception:  # noqa: BLE001
            pass
    _registry.clear()


def reset_clients() -> None:
    """同步清空注册表（测试隔离用；生产关闭请走 close_all）。"""
    _registry.clear()
