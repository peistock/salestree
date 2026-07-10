import os
import sys

check = {
    "WECHAT_SECRET": bool(os.getenv("WECHAT_SECRET")),
    "WECHAT_CORPID": os.getenv("WECHAT_CORPID", "NOT SET"),
    "WECHAT_AGENTID": os.getenv("WECHAT_AGENTID", "NOT SET"),
    "WECHAT_TOKEN": bool(os.getenv("WECHAT_TOKEN")),
    "PWD": os.getcwd(),
}
print(check)
