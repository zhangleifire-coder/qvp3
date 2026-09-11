"""dsh_serve：DeepSeek Harness (dsh) 之上的 OpenAI 兼容创作网关薄层。

协议与 Nanobot 网关 1:1 对齐，后端 src/gateway/nanobot_client.py 只需改
base_url 即可切换。组件独立：只读环境变量，不 import src/。
"""
