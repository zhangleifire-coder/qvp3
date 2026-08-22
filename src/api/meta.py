from fastapi import APIRouter

from src.config import settings
from src.stream.progress import NODE_LABEL, node_order

router = APIRouter()


@router.get("/api/meta/nodes")
async def meta_nodes():
    """流水线节点元数据：按执行顺序返回节点名 + 中文标签（双路径自适应）。"""
    return {"nodes": [{"name": name, "label": NODE_LABEL[name]}
                      for name in node_order()]}


@router.get("/api/meta/access")
async def meta_access():
    """前端权限口径：role_all_access=true 时审核台全员开放 A/B/C 角色切换。"""
    return {"role_all_access": settings.role_all_access}
