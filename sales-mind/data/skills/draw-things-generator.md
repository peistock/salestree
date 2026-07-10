name: draw-things-generator
display_name: DrawThings生图
description: 使用本地 Draw Things 生成 AI 图片。当用户要求画图、生成图片、创建图像时自动调用。

parameters:
  prompt:
    type: string
    description: 详细的英文提示词（Stable Diffusion 格式）
    required: true
  width:
    type: integer
    default: 512
    description: 图片宽度（推荐 512 或 1024）
  height:
    type: integer
    default: 512
    description: 图片高度（推荐 512 或 1024）
  steps:
    type: integer
    default: 25
    description: 推理步数（20-50，越高细节越多但越慢）
  cfg_scale:
    type: integer
    default: 7
    description: CFG Scale（提示词相关性，5-15）

command: |
  python3 << 'PYEOF'
import urllib.request, urllib.error, json, base64, os, sys
from datetime import datetime

try:
    # 调用 Draw Things API
    data = {
        "prompt": """{{prompt}}""",
        "width": {{width}},
        "height": {{height}},
        "steps": {{steps}},
        "cfg_scale": {{cfg_scale}},
        "sampler_name": "DPM++ 2M Karras"
    }
    data_json = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(
        "http://localhost:7860/sdapi/v1/txt2img",
        data=data_json,
        headers={'Content-Type': 'application/json'}
    )
    
    with urllib.request.urlopen(req, timeout=300) as response:
        resp_data = json.load(response)
        
        if "images" in resp_data and len(resp_data["images"]) > 0:
            # 解码 base64 图片
            img_data = base64.b64decode(resp_data["images"][0])
            
            # 生成文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_prompt = """{{prompt}}"""[:20].replace(" ", "_").replace("/", "_")
            output_dir = os.path.expanduser("~/Desktop/DrawThings_Output")
            os.makedirs(output_dir, exist_ok=True)
            
            output_path = os.path.join(output_dir, f"{timestamp}_{safe_prompt}.png")
            
            # 保存图片
            with open(output_path, "wb") as f:
                f.write(img_data)
            
            print(f"✅ 图片生成成功！")
            print(f"📁 保存位置: {output_path}")
            print(f"🎨 提示词: {{prompt}}")
        else:
            print("❌ 生成失败: 未返回图片数据", file=sys.stderr)
            sys.exit(1)
        
except Exception as e:
    print(f"❌ 错误: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF

output:
  type: string
  description: 生成的图片文件路径