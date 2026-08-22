from fastapi import APIRouter

from src.stream.progress import NODE_LABEL, NODE_ORDER

router = APIRouter()


@router.get("/api/meta/nodes")
async def meta_nodes():
    """流水线节点元数据：按执行顺序返回节点名 + 中文标签。"""
    return {"nodes": [{"name": name, "label": NODE_LABEL[name]} for name in NODE_ORDER]}
