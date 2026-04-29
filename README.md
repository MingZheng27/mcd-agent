# 麦当劳营养推荐与下单 Agent

这是一个基于 `Python + LangGraph` 构建的 AI Agent，支持配置 `MiniMax` 或 OpenAI `GPT` 模型，并对接：

- 麦当劳 MCP：地址、附近门店与营养能力
- 麦当劳开放平台 OpenAPI：菜单、购物车、订单

当前版本已经扩展为更接近生产可用的流程：

- 根据用户偏好、忌口、过敏信息和营养目标推荐点餐
- 查询已有地址 / 新增地址
- 新增地址后自动查询附近门店
- 必须先选择最近门店，才允许进入点餐流程
- 查询菜单并结合营养偏好排序推荐
- 同步购物车并查询购物车明细
- 下单确认前调用 MCP 营养能力，汇总本次点餐的营养成分
- 在用户明确确认后提交订单
- 记录日志，并持久化会话上下文

## 目录结构

```text
.
├── .env.example
├── README.md
├── data/
│   └── nutrition_catalog.sample.json
├── pyproject.toml
└── src/
    └── mcd_agent/
        ├── agent.py
        ├── cli.py
        ├── config.py
        ├── context.py
        ├── logging_config.py
        ├── mcd_mcp_client.py
        ├── llm.py
        ├── models.py
        ├── nutrition.py
        ├── prompts.py
        └── tools.py
```

## 1. 安装与配置

### 1.1 安装依赖

建议使用 Python 3.11+

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 1.2 配置环境变量

```bash
cp .env.example .env
```

`.env` 示例：

```env
LLM_PROVIDER=minimax

MINIMAX_API_KEY=
MINIMAX_BASE_URL=https://api.minimaxi.com/v1
MINIMAX_MODEL=MiniMax-M2.7

OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini

MCD_BASE_URL=https://api.open.mcd.cn
MCD_APP_ID=
MCD_MERCHANT_ID=
MCD_SIGN_KEY=
MCD_VERSION=1.0

MCD_MCP_BASE_URL=https://mcp.mcd.cn
MCD_MCP_TOKEN=
MCD_MCP_PROTOCOL_VERSION=2025-06-18

DEFAULT_CHANNEL_CODE=03
DEFAULT_ORDER_TYPE=2
DEFAULT_BE_CODE=
DEFAULT_DAYPART_CODE=
DEFAULT_STORE_CODE=

SESSION_STORE_PATH=.agent_state/sessions
LOG_DIR=logs
NUTRITION_CATALOG_PATH=data/nutrition_catalog.sample.json
DRY_RUN_ORDERS=true
```

说明：

- `LLM_PROVIDER`：可选 `minimax` 或 `openai`
- `MINIMAX_API_KEY`：MiniMax Token Plan Key，不填无法实际调用模型
- `OPENAI_API_KEY`：当 `LLM_PROVIDER=openai` 时必填
- `MCD_APP_ID` / `MCD_MERCHANT_ID` / `MCD_SIGN_KEY`：麦当劳开放平台签名凭据，不填无法调用菜单、购物车与订单 OpenAPI
- `MCD_MCP_TOKEN`：麦当劳 MCP Token，用于地址、附近门店和营养能力调用；不填时这些 MCP 能力不可用，营养分析会回退到本地营养库
- `DEFAULT_ORDER_TYPE=2`：默认按配送场景运行，更符合“地址 -> 最近门店 -> 点餐”的链路
- `DRY_RUN_ORDERS=true`：默认模拟下单，避免误发真实订单

## 2. LLM 配置方式

项目通过 `langchain-openai` 的 `ChatOpenAI` 接入不同提供方，再由 `LangGraph` 编排工具调用流程。

### 2.1 MiniMax

- `base_url`: `https://api.minimaxi.com/v1`
- `model`: `MiniMax-M2.7`
- `api_key`: MiniMax Token Plan Key

### 2.2 OpenAI GPT

- `LLM_PROVIDER=openai`
- `base_url`: `https://api.openai.com/v1`
- `model`: 例如 `gpt-4.1-mini`、`gpt-4.1`
- `api_key`: OpenAI API Key

代码位置：

- [src/mcd_agent/llm.py](/Users/zhengming/Documents/Codex/mcd-agent/src/mcd_agent/llm.py)

## 3. 麦当劳接口接入说明

### 3.1 MCP

项目通过一个统一的 `McdMcpClient` 封装麦当劳 MCP Streamable HTTP Client，目前用于：

- 查询地址
- 新增地址
- 删除地址
  当前已对齐 client 逻辑，但根据 2026-04-29 实测的 `tools/list`，真实 MCP 暂未公开删除地址工具，因此运行时会明确返回不可用提示。
- 查询附近门店
  外送场景下，真实 MCP 没有单独的“按地址查附近门店”工具，地址查询结果会直接返回匹配的 `storeCode / storeName / beCode`，代码已按这个真实结构适配。
- 调用营养工具 `list-nutrition-foods`

地址与门店工具通过 `tools/list` 自动发现并匹配；营养工具优先使用显式工具名 `list-nutrition-foods`。

代码位置：

- [src/mcd_agent/mcd_mcp_client.py](/Users/zhengming/Documents/Codex/mcd-agent/src/mcd_agent/mcd_mcp_client.py)
- [src/mcd_agent/nutrition.py](/Users/zhengming/Documents/Codex/mcd-agent/src/mcd_agent/nutrition.py)

### 3.2 OpenAPI

项目仍保留这些关键 OpenAPI：

- 门店详情：`GET /stores/{storeCode}`
- 菜单查询：`POST /products/menu`
- 购物车明细：`GET /carts`
- 更新购物车：`PUT /carts`
- 清空购物车：`PUT /carts/empty`
- 提交订单：`POST /orders`

签名规则：

- `Sign = MD5("AppId=...&Body=...&MerchantId=...&Timestamp=...&key=...").upper()`

代码位置：

- [src/mcd_agent/mcd_mcp_client.py](/Users/zhengming/Documents/Codex/mcd-agent/src/mcd_agent/mcd_mcp_client.py)

## 4. 业务流程

当前 agent 的推荐与下单链路是：

1. 记录用户偏好和营养目标
2. 查询已有地址，或新增地址
3. 新增地址后自动查询附近门店
4. 用户必须选择最近门店后，才可以继续点餐
5. 查询门店菜单并做营养偏好推荐
6. 同步购物车
7. 查询购物车明细
8. 进入下单确认环节
9. 调用 MCP 营养能力，汇总本次点餐的营养成分
10. 用户确认后提交订单

这条链路是被工具约束的：

- 没有地址，不能查附近门店
- 没有选中门店，不能查菜单或同步购物车
- 没有购物车，不能进入确认或下单
- 没有确认，不能提交订单

## 5. LangGraph 工作流

项目使用 LangGraph 的显式工作流，而不是 LangChain 旧式 `AgentExecutor`。

核心节点：

- `assistant`：调用当前配置的 LLM（MiniMax 或 OpenAI GPT）
- `tools`：执行地址、门店、菜单、购物车、确认和下单工具

执行流程：

1. 用户输入进入 `assistant`
2. 如果模型返回工具调用，则进入 `tools`
3. 工具执行完成后回到 `assistant`
4. 当模型不再请求工具时，本轮结束

代码位置：

- [src/mcd_agent/agent.py](/Users/zhengming/Documents/Codex/mcd-agent/src/mcd_agent/agent.py)

## 6. 可用工具

当前 agent 主要工具如下：

- `update_user_preferences`
- `query_addresses`
- `create_address`
- `delete_address`
- `list_nearby_stores`
- `select_store`
- `fetch_menu_and_rank`
- `sync_cart`
- `get_cart_detail`
- `update_order_options`
- `prepare_order_confirmation`
- `submit_confirmed_order`

其中 `prepare_order_confirmation` 是关键确认步骤，会：

- 刷新购物车明细
- 汇总本次订单金额
- 调用 MCP 营养能力
- 输出订单级营养成分

代码位置：

- [src/mcd_agent/tools.py](/Users/zhengming/Documents/Codex/mcd-agent/src/mcd_agent/tools.py)

## 7. 使用方式

### 7.1 交互式运行

```bash
mcd-agent --session-id demo
```

示例对话：

```text
我想点一份高蛋白、低糖的麦当劳外卖，偏好鸡肉，不吃洋葱。
```

然后你可以继续让 agent 帮你完成：

- 查询已有地址
- 新增地址
- 选择最近门店
- 推荐商品
- 同步购物车
- 查看确认信息和营养成分
- 下单

### 7.2 单轮调用

```bash
mcd-agent --session-id demo --message "帮我新增一个配送地址，并看看附近最近的麦当劳"
```

## 8. 上下文管理

项目实现了两层上下文管理：

- 持久化会话：保存在 `SESSION_STORE_PATH`
- 滚动摘要：历史消息超过阈值后自动压缩为 `rolling_summary`

当前持久化内容不只是聊天记录，还包括：

- 用户偏好
- 地址列表和当前地址
- 附近门店和当前门店
- 当前购物车快照
- 订单草稿
- 最近一次营养确认结果

代码位置：

- [src/mcd_agent/context.py](/Users/zhengming/Documents/Codex/mcd-agent/src/mcd_agent/context.py)
- [src/mcd_agent/models.py](/Users/zhengming/Documents/Codex/mcd-agent/src/mcd_agent/models.py)

## 9. 日志记录

日志输出到：

- 控制台
- `logs/agent.log`

日志会记录：

- 会话创建与保存
- 地址、门店、购物车和订单草稿变化
- 麦当劳 OpenAPI 调用
- 麦当劳 MCP 调用

注意：

- OpenAPI 请求头中的敏感字段已脱敏
- MCP token 不会直接打印到日志

## 10. 营养能力说明

确认环节优先使用麦当劳 MCP 的 `list-nutrition-foods` 工具。

当前实现策略：

1. 先 `tools/list` 获取工具 schema
2. 自动尝试调用 `list-nutrition-foods`
3. 尝试从返回的 JSON 文本中提取营养记录
4. 按商品名模糊匹配购物车商品
5. 汇总为订单级营养报告

如果 MCP 不可用，则回退到：

- [data/nutrition_catalog.sample.json](/Users/zhengming/Documents/Codex/mcd-agent/data/nutrition_catalog.sample.json)

当前输出会明确标注来源是：

- `mcp`
- `local_catalog`
- `mcp_unmatched`

## 11. 真实下单前需要补齐的字段

如果你希望真正提交订单，需要补齐：

- LLM API Key
  例如 MiniMax API Key，或 `LLM_PROVIDER=openai` 时对应的 OpenAI API Key
- 麦当劳开放平台 AppId / MerchantId / Sign Key
- 麦当劳 MCP Token

并确保以下上下文完整：

- 已选定地址
- 已选定门店
- 已同步购物车
- 已完成确认环节
- 订单金额能从购物车正确回填

默认情况下，项目不会自动真实下单，除非：

1. `DRY_RUN_ORDERS=false`
2. OpenAPI 凭据已补齐
3. 用户明确确认

## 12. 已知限制

- 麦当劳 MCP 官方文档页面依赖前端渲染，当前实现基于公开可见的 MCP 接入信息和标准 MCP Streamable HTTP 协议实现
- `list-nutrition-foods` 的返回结构可能因服务端实现不同而变化，因此营养解析做了兼容和回退处理
- 订单提交请求中的部分金额字段以购物车明细回填，更适合先联调后再上生产

## 13. 参考文档

- 麦当劳开放平台文档：<https://open.mcd.cn/docs/>
- 麦当劳 MCP 文档入口：<https://open.mcd.cn/mcp/doc>
- MiniMax OpenAI 兼容接口文档：<https://platform.minimax.io/docs/api-reference/text-openai-api>
- OpenAI API 文档：<https://platform.openai.com/docs/api-reference>
- LangGraph 官方文档：<https://docs.langchain.com/oss/python/langgraph>
- MCP Streamable HTTP 规范：<https://modelcontextprotocol.io/specification/2025-06-18/basic/transports>
- MCP 工具概念：<https://modelcontextprotocol.io/docs/concepts/tools>
