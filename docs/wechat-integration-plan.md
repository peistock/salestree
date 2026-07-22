# 微信群消息接入销销方案

> 创建时间：2026-07-20  
> 更新：2026-07-21（改用 jackwener/wx）  
> 状态：销销侧已接入，等待本地微信签名/密钥提取  
> 目标：把微信客户群的聊天记录变成销销可分析的数据源，与飞书/钉钉群同等对待

---

## 一、方案选型结论

**数据层用 [jackwener/wx-cli](https://www.npmjs.com/package/@jackwener/wx-cli)（二进制名 `wx`），不搬 baoyu-wechat-summary 本体。**

| 选项 | 结论 | 理由 |
|------|------|------|
| jackwener/wx（Rust 二进制） | ✅ 主方案 | 只读解密本地微信数据库；命令为 `wx history <群名> --json`；GitHub 仓库被 DMCA 屏蔽，通过 npm 平台包分发 |
| jackwener/wechat-summary（Python） | ✅ 过渡方案 | 截图 + OCR + Claude 总结；当 wx 不支持当前微信版本时自动 fallback |
| pandorafuture/wx-cli | ❌ 已弃用 | 早期测试过，命令体系不同（`wx-cli query`），与 jackwener/wx 不兼容，已卸载 |
| baoyu-wechat-summary（skill） | ❌ 不直接搬 | 只是 Claude Code 专用的提示词工作流，真正干活的还是 wx-cli；其摘要/画像逻辑后续可改写为销销 skill |
| iPad 协议机器人 | ❌ 排除 | 注入式，封号风险高，客户群不敢用 |

## 二、整体架构

### 主方案（数据库解密）

```
微信桌面客户端（Mac，已登录）
    ↓ 本地数据库（SQLCipher 加密）
jackwener/wx（解密只读，跑在同一台 Mac）
    ↓ CLI 调用
scripts/wechat/pull_account.py
    ↓ 落盘
data/projects/{account}_messages.json
    ↓ 与飞书/钉钉数据合并
混合来源项目看板 / 销销分析流程
```

### 过渡方案（截图+OCR+总结）

当 wx 无法提取数据库密钥时，`pull_account.py` 自动 fallback：

```
微信桌面客户端（Mac，已登录且窗口可见）
    ↓ AppleScript 控制窗口 + screencapture 截图
third_party/wechat-summary/main.py
    ↓ macOS Vision OCR + Claude API 总结
scripts/wechat/pull_account_summary.py
    ↓ 把 Markdown 总结作为一条消息写入
data/projects/{account}_messages.json
```

**关键约束：无论哪种方案，都只能跑在微信登录的那台 Mac 上，服务器上没有微信客户端。** 拉取动作发生在本地，数据再进销销。

## 三、环境前提

| 项 | 要求 | 现状 |
|----|------|------|
| 平台 | macOS arm64（Apple Silicon） | ✅ 满足 |
| 微信版本 | 本机 4.1.11 | ❌ **jackwener/wx 0.3.0 暂不支持**，内存扫描 0 个候选密钥（见下方第四节） |
| 代码签名 | `wx init` 需要 `task_for_pid`，必须先把 WeChat.app 改成 ad-hoc 签名 | ✅ 已完成 |
| SIP | 不需要关闭 | ✅ 保持开启即可 |

## 四、本地环境搭建 SOP

1. **安装 `wx`**：
   ```bash
   # npm 直接安装可能显示 up to date 但不落盘，推荐下载平台 tarball
   curl -fSL https://registry.npmjs.org/@jackwener/wx-cli-darwin-arm64/-/wx-cli-darwin-arm64-0.3.0.tgz -o wx.tgz
   tar xzf wx.tgz
   mkdir -p ~/.local/bin
   cp package/bin/wx ~/.local/bin/wx
   chmod +x ~/.local/bin/wx
   wx --version   # 应输出 wx 0.3.0
   ```

2. **退出微信**：
   ```bash
   killall WeChat
   ```

3. **签名 WeChat.app**（`wx init` 必需）：
   ```bash
   # 若 WeChat.app 归 root 所有，先改为自己用户
   sudo chown -R $(whoami):staff /Applications/WeChat.app

   # ad-hoc 签名
   codesign --force --deep --sign - /Applications/WeChat.app

   # 若报 vlc_plugins 内部错误，先移除签名再签
   # codesign --remove-signature --deep /Applications/WeChat.app
   # codesign --force --deep --sign - /Applications/WeChat.app
   ```

4. **启动微信并登录**。

5. **提取密钥**：
   ```bash
   sudo wx init
   ```
   成功后会显示数据目录和密钥信息。

6. **验证读取**：
   ```bash
   wx sessions
   wx history "某个群名" --since 2026-07-20 -n 5 --json
   ```

### 兼容性检查

如果 `sudo wx init` 显示：
```
找到 0 个候选密钥
匹配到 0/0 个密钥
成功提取 0 个数据库密钥
```
但 `task_for_pid` 已通、数据库能找到，说明当前微信版本不在 wx 0.3.0 的支持列表内。

- 已知问题：[jackwener/wx-cli #108](https://github.com/jackwener/wx-cli/issues/108) — WeChat 4.1.10 macOS 内存扫描 0 个候选密钥。
- 本机实测 WeChat 4.1.11 同样命中该问题。
- 原因：微信 WCDB 在 4.1.x 中改变了密钥在内存中的存储方式，自动扫描暂时失效。

### 注意事项

- 提取出的密钥 = 微信全部聊天记录的钥匙，**不进代码、不进 git、不进日志**，只存在本机 wx 的存储里。
- 若公司电脑有 MDM / 安全管控软件，改签名前确认是否允许；本方案不需要关 SIP，比旧方案更安全。
- 微信升级后可能需要重新执行第 3 步。

### 若命中 4.1.x 兼容性问题的可选方案

| 方案 | 说明 | 风险/代价 |
|------|------|----------|
| **降级微信** | 回退到 wx 0.3.0 确认支持的旧版本（如 4.0.x） | macOS 微信降级较麻烦，需找旧版安装包；可能触发本地数据库版本兼容问题 |
| **等待更新** | 关注 jackwener/wx-cli issue #108 | 时间不可控，但最安全 |
| **手动提供密钥** | 若通过其他途径（旧版微信/备份/其他工具）拿到 64 位十六进制密钥，可写入 wx 配置 | 需要额外能力或历史备份；密钥敏感，需妥善保管 |
| **换一台旧版微信的 Mac** | 在公司或个人其他机器上安装旧版微信 + wx | 需要额外机器，数据仍在本地 |

## 五、销销侧接入（已完成）

1. **`data/projects/wechat_accounts.json`** — 声明客户群配置，格式对齐 `feishu_accounts.json`。
2. **`scripts/wechat/pull_account.py`** — 主方案：调用 `wx history <群名> --since ... --json`；当 wx 无法解密时自动 fallback 到 `pull_account_summary.py`。
3. **`scripts/wechat/pull_account_summary.py`** — 过渡方案：调用 `third_party/wechat-summary/main.py` 做截图 → OCR → Claude 总结，把 Markdown 作为一条"群聊总结"消息写入 `{account}_messages.json`。
4. **`scripts/hybrid/pull_account.py`** — 已支持 `source == "wechat"`，可与飞书/钉钉混合来源合并。
5. **项目面板** — `server/src/index.ts` 已加载 `wechat_accounts.json`，刷新时调用 `scripts/wechat/pull_account.py`。
6. **Agent 工具** — `read_wechat_messages` 已注册，可读取 `{account}_messages.json`。

验证命令：
```bash
python3 scripts/wechat/pull_account.py --account 示例微信客户
python3 scripts/wechat/analyze_account.py --account 示例微信客户
```

当 wx 能解密时，输出的是逐条消息；当 wx 不能解密时，输出的是每个群的 Markdown 总结。

### 过渡方案依赖与权限

当命中 wx 4.1.x 兼容性问题时，`pull_account.py` 会自动走 `jackwener/wechat-summary`。

1. **克隆仓库**（已完成）：
   ```bash
   git clone https://github.com/jackwener/wechat-summary.git third_party/wechat-summary
   ```

2. **安装 Python 依赖**：
   ```bash
   pip install -r third_party/wechat-summary/requirements.txt
   # 或单独安装
   pip install pyobjc-framework-Vision pyobjc-framework-Quartz anthropic openai
   ```

3. **配置 LLM API Key**：

   默认使用 Anthropic Claude，需在环境变量设置：
   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```

   若使用 OpenAI 兼容接口（如 Agnes AI），在 `third_party/wechat-summary/.env.local` 写入：
   ```bash
   OPENAI_API_KEY=sk-I98wUzdukjJ1qOvjd7Hy34nYfWOArlEenzlKSkEIYzxxo4AF
   OPENAI_BASE_URL=https://apihub.agnes-ai.com/v1
   OPENAI_MODEL=agnes-2.5-flash
   ```
   该文件已被 `.gitignore` 忽略，不会进 git。

4. **授予终端/Codex 权限**：
   - 系统设置 → 隐私与安全性 → **辅助功能**
   - 系统设置 → 隐私与安全性 → **屏幕录制**
   - 把运行脚本的终端/Codex 应用加入上述两项

5. **运行前注意**：
   - 微信必须已登录，窗口不要被遮挡
   - 脚本运行期间不要操作鼠标/键盘
   - 每群默认截 30 页（约 300-450 条消息），可用 `--pages` 调整

## 六、合规边界

- wx 只读解密本地数据库，不向微信注入任何东西，但仍违反微信用户协议——只用于自己的客户群，不做消息外发自动化。
- 客户群消息进销销后按现有数据规范处理，提取业务必要信息，不做全量留存扩散。
- 群成员隐私：分析输出避免对外暴露与业务无关的个人言论。

## 七、当前进度

- [x] 安装 jackwener/wx 0.3.0（`~/.local/bin/wx`）
- [x] 销销侧接入（配置、脚本、看板、Agent 工具）
- [x] WeChat.app ad-hoc 签名
- [x] `sudo wx init` 执行（WeChat 4.1.11 提取 0 个密钥，命中已知兼容性问题）
- [x] 集成 jackwener/wechat-summary 作为过渡方案（`third_party/wechat-summary` + `scripts/wechat/pull_account_summary.py`）
- [x] `pull_account.py` 自动 fallback 逻辑
- [ ] 安装过渡方案依赖并授权
- [ ] 首次真实群聊总结拉取验证
