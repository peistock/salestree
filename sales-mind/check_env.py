import os
print("WECHAT_SECRET:", bool(os.getenv("WECHAT_SECRET")))
print("WECHAT_CORPID:", os.getenv("WECHAT_CORPID", "NOT SET"))
