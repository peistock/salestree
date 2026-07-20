# 微信群消息接入销销方案

> 创建时间：2026-07-20
> 状态：方案已定，环境验证中（待公司电脑测试）
> 目标：把微信客户群的聊天记录变成销销可分析的数据源，与飞书/钉钉群同等对待

---

## 一、方案选型结论

**数据层用 [wx-cli](https://github.com/pandorafuture/wx-cli)，不搬 baoyu-wechat-summary 本体。**

| 选项 | 结论 | 理由 |
|------|------|------|
| wx-cli（Rust 二进制） | ✅ 采用 | 只读解密本地微信数据库，不注入客户端，封号风险低；CLI + REST API 双形态 |
| baoyu-wechat-summary（skill） | ❌ 不直接搬 | 只是 Claude Code 专用的提示词工作流，真正干活的还是 wx-cli；其摘要/画像逻辑后续可改写为销销 skill |
| iPad 协议机器人 | ❌ 排除 | 注入式，封号风险高，客户群不敢用 |

## 二、整体架构

```
微信桌面客户端（Mac，已登录）
    ↓ 本地数据库（SQLCipher 加密）
wx-cli（解密只读，跑在同一台 Mac）
    ↓ CLI 调用
scripts/wechat/pull_account.py（复刻 scripts/feishu/pull_account.py 模式）
    ↓ 落盘
data/projects/{account}_messages.json
    ↓ 与飞书/钉钉数据合并
混合来源项目看板 / 销销分析流程
```

**关键约束：wx-cli 只能跑在微信登录的那台 Mac 上，JD Cloud 服务器上没有微信客户端。** 拉取动作发生在本地，数据再进销销。

## 三、环境前提（wx-cli v0.7.3）

| 项 | 要求 | 个人 Mac 现状 |
|----|------|--------------|
| 平台 | macOS arm64（Apple Silicon） | ✅ 满足 |
| 微信版本 | 官方写 4.1.7.x / 4.1.8.x | ⚠️ 本机 4.1.9，兼容性待验证，**先别升级微信** |
| SIP | 密钥提取时需临时关闭，拿到密钥后可重开 | 当前开启 |
| 其他 | `sudo DevToolsSecurity -enable` + lldb + `_developer` 组 | lldb/组已满足 |

## 四、公司电脑测试 SOP（明天执行）

> 个人 Mac 已完成第 1 步，以下步骤在公司电脑上从头走一遍。

1. **安装**：`curl -fSL <release-url> -o wx-cli.tar.gz && tar xzf && mv wx-cli ~/.local/bin/ && chmod +x` → 验证 `wx-cli --version`
2. **环境检查**：`wx-cli doctor` → 确认 lldb、`_developer` 组、DevToolsSecurity 三项
3. **开启 DevToolsSecurity**：`sudo DevToolsSecurity -enable`
4. **临时关 SIP**：关机 → 按住电源键进恢复模式 → 实用工具 → 终端 → `csrutil disable` → 重启
5. **登录微信**，保持运行
6. **提取密钥**：`wx-cli key extract --timeout 120`（会重启微信）→ 验证 `wx-cli key list` 有 64 位密钥
7. **重开 SIP**：再进恢复模式 → `csrutil enable` → 重启
8. **验证读取**：`wx-cli sessions --limit 5` 和 `wx-cli query <某客户群名> --limit 10` 能出消息即成功

### 公司电脑额外注意

- **先确认公司 IT 政策**：公司电脑如有 MDM / 安全管控软件（Jamf、CrowdStrike 等），关 SIP 可能触发告警甚至违反规定，测试前先确认这台机器是否允许
- 微信 4.1.9 若提取失败（LLDB hook 偏移对不上），退路：等 wx-cli 更新支持，不要贸然给微信降级
- 提取出的密钥 = 微信全部聊天记录的钥匙，**不进代码、不进 git、不进日志**，只存在本机 `wx-cli key set` 的存储里

## 五、销销侧接入步骤（环境验证通过后开工）

1. **`data/projects/wechat_accounts.json`** — 声明客户群配置（群名、对应客户/项目），格式对齐 `feishu_accounts.json`
   → 验证：JSON 可解析，字段与飞书配置同构
2. **`scripts/wechat/pull_account.py`** — 复刻飞书 pull 脚本：`wx-cli query <群名> --since ... --json` → 合并去重 → 写 `data/projects/{account}_messages.json`
   → 验证：对一个真实客户群跑出 JSON，消息字段（发送人/时间/内容）完整
3. **打通看板** — 混合来源看板增加微信数据源
   → 验证：看板上微信客户群消息与飞书群消息并列展示
4. **（可选）定时拉取** — launchd 定时跑 pull 脚本
   → 验证：到点自动更新 JSON

## 六、合规边界

- wx-cli 只读解密本地数据库，不向微信注入任何东西，但仍违反微信用户协议——只用于自己的客户群，不做消息外发自动化
- 客户群消息进销销后按现有数据规范处理，提取业务必要信息，不做全量留存扩散
- 群成员隐私：分析输出避免对外暴露与业务无关的个人言论

## 七、当前进度

- [x] 个人 Mac：wx-cli v0.7.3 安装完成（`~/.local/bin/wx-cli`），doctor 通过（除 SIP/DevToolsSecurity）
- [ ] 公司电脑：环境测试（第四节 SOP）
- [ ] 密钥提取 + 读取验证
- [ ] 销销接入（第五节 4 步）
