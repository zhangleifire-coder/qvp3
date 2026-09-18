"""dsh_serve：DeepSeek Harness (dsh) 之上的 OpenAI 兼容创作网关薄层。

协议与后端客户端 1:1 对齐，src/gateway/dsh_client.py 只需把
base_url 即可切换。组件独立：只读环境变量，不 import src/。
"""
