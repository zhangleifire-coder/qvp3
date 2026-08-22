from fastapi import APIRouter

from src.stream.progress import NODE_LABEL, node_order

router = APIRouter()


@router.get("/api/meta/nodes")
async def meta_nodes():
    """流水线节点元数据：按执行顺序返回节点名 + 中文标签（双路径自适应）。"""
    return {"nodes": [{"name": name, "label": NODE_LABEL[name]}
                      for name in node_order()]}
