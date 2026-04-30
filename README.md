# 🍔 麦当劳营养推荐与下单 Agent

一个基于 **Python + LangGraph** 构建的智能点餐 Agent，支持配置 **OpenAi Api兼容协议** 模型，并对接：

- **麦当劳 MCP**：地址查询、附近门店搜索、营养成分查询、菜单查询、购物车管理
- **本地营养库**：离线营养数据兜底

## ✨ 核心功能

### 🛠️ 智能点餐流程
- 根据用户偏好（口味、忌口、过敏原）和营养目标（热量、蛋白质、脂肪、钠、糖）推荐餐品
- 地址管理：查询已有地址 / 新增配送地址
- 门店选择：新增地址后自动查询附近门店，必须选择最近门店才能点餐
- 菜单查询：结合营养偏好排序推荐
- 购物车管理：同步、清空、查询明细
- 营养分析：下单确认前调用 MCP 营养能力，汇总本次点餐的营养成分
- 订单提交：用户明确确认后提交订单

### 🔍 营养查询
- **直接查询**：用户可随时查询任意商品的营养成分（热量、蛋白质、脂肪、碳水、钠等）
- **MCP 优先**：优先使用麦当劳官方 MCP 营养数据
- **本地兜底**：MCP 不可用时自动回退到本地营养库
- **智能匹配**：支持商品名模糊匹配

### 🧠 智能会话
- 持久化会话：保存到本地文件，支持跨会话恢复
- 滚动摘要：历史消息自动压缩，保持上下文精简
- 完整状态：记录用户偏好、地址、门店、购物车、订单草稿、营养报告

## 📁 目录结构

```
.
├── .env.example                 # 环境变量配置示例
├── README.md                    # 项目文档
├── pyproject.toml               # Python 项目配置
├── data/
│   └── nutrition_catalog.sample.json  # 本地营养数据
├── logs/                       # 日志目录
├── src/
│   └── mcd_agent/
│       ├── __init__.py
│       ├── agent.py            # LangGraph Agent 核心
│       ├── cli.py              # 命令行入口
│       ├── config.py           # 配置管理
│       ├── context.py           # 会话上下文管理
│       ├── llm.py              # LLM 模型接入
│       ├── logging_config.py    # 日志配置
│       ├── mcd_mcp_client.py   # MCP 客户端封装
│       ├── models.py           # 数据模型
│       ├── nutrition.py         # 营养分析与查询
│       ├── prompts.py          # Prompt 模板
│       └── tools.py            # 工具定义
└── tests/
    └── test_toon_parsing.py    # TOON 格式解析测试
```

## 🚀 快速开始

### 1. 安装依赖

```bash
# 建议使用 Python 3.9+
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入必要的配置
```

### 3. 运行 Agent

**交互式运行：**
```bash
mcd-agent --session-id demo
```

**单轮调用：**
```bash
mcd-agent --session-id demo --message "帮我查询杨浦区xx公寓附近的麦当劳"
```

## ⚙️ 配置说明

### LLM 配置

| 配置项 | 说明 | 必填 |
|--------|------|------|
| `LLM_PROVIDER` | 模型提供商：`minimax` 或 `openai` | ✅ |
| `MINIMAX_API_KEY` | MiniMax Token Plan Key | 当 LLM_PROVIDER=minimax 时必填 |
| `MINIMAX_BASE_URL` | MiniMax API 地址 | 默认 `https://api.minimaxi.com/v1` |
| `MINIMAX_MODEL` | MiniMax 模型 | 默认 `MiniMax-M2.7` |
| `OPENAI_API_KEY` | OpenAI API Key | 当 LLM_PROVIDER=openai 时必填 |
| `OPENAI_BASE_URL` | OpenAI API 地址 | 默认 `https://api.openai.com/v1` |
| `OPENAI_MODEL` | OpenAI 模型 | 默认 `gpt-4.1-mini` |

### 麦当劳 MCP 配置

| 配置项 | 说明 | 必填 |
|--------|------|------|
| `MCD_MCP_BASE_URL` | MCP 服务地址 | ✅ |
| `MCD_MCP_TOKEN` | MCP 访问令牌 | 用于地址、门店、营养查询、菜单、购物车 |
| `MCD_MCP_PROTOCOL_VERSION` | MCP 协议版本 | 默认 `2025-06-18` |

### 业务配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `DEFAULT_ORDER_TYPE` | 默认订单类型 | `2`（外送） |
| `DEFAULT_CHANNEL_CODE` | 默认渠道码 | `03` |
| `DRY_RUN_ORDERS` | 模拟下单模式 | `true` |

### Agent 配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `AGENT_RECURSION_LIMIT` | Agent 最大迭代次数 | `10000` |
| `SESSION_STORE_PATH` | 会话存储路径 | `.agent_state/sessions` |
| `LOG_DIR` | 日志目录 | `logs` |
| `NUTRITION_CATALOG_PATH` | 本地营养库路径 | `data/nutrition_catalog.sample.json` |

## 🎯 使用示例

### 对话示例

```text
用户：我想点一份高蛋白、低糖的麦当劳外卖，偏好鸡肉，不吃洋葱。

Agent：好的，已记录您的偏好：
- 口味偏好：鸡肉
- 忌口食材：洋葱
- 营养目标：高蛋白、低糖

请提供配送地址，我帮您查找附近的麦当劳。
```

### 营养查询

```text
用户：查询一下巨无霸的营养成分

Agent：🍔 巨无霸 营养成分表

| 营养素 | 含量 |
|--------|------|
| 热量 | 513 kcal |
| 蛋白质 | 27.0 g |
| 脂肪 | 26.0 g |
| 碳水化合物 | 42.0 g |
| 钠 | 961 mg |

💡 营养小结：
- ✅ 蛋白质含量较高（27g）
- ⚠️ 脂肪含量略高（26g）
- ⚠️ 钠含量偏高，高血压患者建议酌情食用
```

### 餐品推荐
```text
User: 我想点一份高蛋白、低糖的麦当劳外卖，偏好鸡肉，不吃洋葱。

Agent:
好的！我已根据您的偏好（鸡肉、高蛋白≥25g、低糖≤20g、无洋葱）为您筛选并查询了相关营养数据。以下是推荐方案：
---
## 🍗 推荐方案：板烧鸡腿堡 + 玉米杯
| 商品 | 热量 | 蛋白质 | 脂肪 | 碳水 | 钠 | 糖 |
|------|------|--------|------|------|-----|-----|
| 板烧鸡腿堡 | 391 kcal | 23.0 g | 17.0 g | 35.0 g | 1041 mg | — |
| 玉米杯 | 87 kcal | 4.0 g | 1.0 g | 12.0 g | 2 mg | — |
| **合计** | **478 kcal** | **27.0 g** | **18.0 g** | **47.0 g** | **1043 mg** | 低糖 |
### ✅ 满足情况：
| 目标 | 实际 | 是否满足 |
|------|------|----------|
| 高蛋白 ≥25g | 27.0 g | ✅ |
| 低糖 ≤20g | 碳水47g（含天然糖），糖分较低 | ✅ |
| 鸡肉偏好 | 板烧鸡腿堡是鸡肉堡 | ✅ |
| 无洋葱 | 两款均不含洋葱 | ✅ |
---
### 🍗 备选方案：那么大鸡排 + 玉米杯
| 商品 | 热量 | 蛋白质 | 脂肪 | 碳水 | 钠 |
|------|------|--------|------|------|-----|
| 那么大鸡排 | 385 kcal | 24.0 g | 21.0 g | 24.0 g | 996 mg |
| 玉米杯 | 87 kcal | 4.0 g | 1.0 g | 12.0 g | 2 mg |
| **合计** | **472 kcal** | **28.0 g** | **22.0 g** | **36.0 g** | **998 mg** |
**特点**：蛋白质更高(28g)、碳水更低(36g)，鸡排无洋葱。
```

## 🔧 工具列表

### 地址管理
- `query_addresses` - 查询已有配送地址
- `create_address` - 新增配送地址（自动查询附近门店）
- `delete_address` - 删除配送地址

### 门店选择
- `list_nearby_stores` - 查询附近麦当劳门店
- `select_store` - 选择点餐门店（必须先选择门店才能点餐）

### 菜单与推荐
- `fetch_menu_and_rank` - 查询菜单并结合营养偏好排序推荐
- `query_nutrition` - **直接查询**单个商品的营养成分

### 购物车
- `sync_cart` - 同步购物车
- `get_cart_detail` - 查询购物车明细

### 订单
- `update_order_options` - 更新订单选项（备注、取餐方式等）
- `prepare_order_confirmation` - 下单确认（汇总营养成分）
- `submit_confirmed_order` - 提交订单（需用户明确确认）

## 📊 营养数据来源

### 优先级策略

1. **MCP 优先**：优先使用麦当劳 MCP 官方营养数据（`list-nutrition-foods` 工具）
2. **本地兜底**：MCP 不可用或未命中时，使用本地营养库
3. **双重回退**：单品未命中时，尝试本地库补充

### TOON 格式解析

MCP 返回的 TOON 格式示例：

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  巨无霸,null,2146,513,27,26,42,961,171
  板烧鸡腿堡,null,1638,391,23,17,35,1041,93
  ...
```

系统自动解析并转换为标准营养数据格式。

### 数据标注

营养分析结果会明确标注来源：
- `mcp` - 来自 MCP 官方数据
- `local_catalog` - 来自本地营养库
- `local_fallback` - MCP 未命中，本地库补充
- `unmatched` - MCP 和本地库均未命中

## 🏗️ 系统架构

### LangGraph 工作流

```
┌─────────────┐
│   START     │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  assistant │◄────────────────┐
│   (LLM)    │                 │
└──────┬─────┘                 │
       │                       │
       │ 有工具调用             │ 工具执行完成
       ▼                       │
┌─────────────┐     ┌─────────────┐
│ route_after │────►│   tools     │
│ _assistant  │     │  (工具执行) │
└─────────────┘     └──────┬──────┘
                            │
                            └──────────► 返回 assistant
                                     │
                                     ▼
                              ┌─────────────┐
                              │    END      │
                              └─────────────┘
```

### 工具约束链路

```
无地址 ──► 无法查附近门店
无门店 ──► 无法查菜单
无菜单 ──► 无法同步购物车
无购物车 ──► 无法进入确认
无确认 ──► 无法提交订单
```

## 🔒 安全说明

- **敏感信息脱敏**：日志中自动脱敏 API Key、Token 等敏感字段
- **模拟下单**：默认 `DRY_RUN_ORDERS=true`，避免误发真实订单
- **会话隔离**：每个会话独立存储，支持多用户并发

## 📝 日志记录

日志输出到：
- 控制台（实时查看）
- `logs/agent.log`（持久化保存）

记录内容：
- 会话创建与保存
- 地址、门店、购物车、订单变化
- MCP 调用详情
- 错误与异常

## 📚 技术栈

- **Python 3.9+**
- **LangGraph** - Agent 工作流编排
- **LangChain** - LLM 工具调用框架
- **MiniMax / OpenAI GPT** - 大语言模型 (test based on mimimax-m2.7)
- **MCP (Model Context Protocol)** - 麦当劳 MCP 协议

## 参考文档

- **麦当劳MCP**(https://open.mcd.cn/mcp/doc)