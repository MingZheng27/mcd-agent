from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Optional

import requests
from requests import RequestException, Response

from .config import Settings

logger = logging.getLogger(__name__)


def _to_decimal(value: Any) -> Decimal | None:
    """将各种类型转换为 Decimal，处理 None 值和类型转换错误"""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (ValueError, TypeError):
        return None


class McdToolError(RuntimeError):
    pass


class McdMcpClient:
    _MCP_TOOL_CANDIDATES: dict[str, dict[str, list[str]]] = {
        "list_addresses": {
            "exact_names": ["delivery-query-addresses"],
            "keywords": ["delivery-query-addresses", "配送地址", "外送", "麦乐送"],
        },
        "create_address": {
            "exact_names": ["delivery-create-address"],
            "keywords": ["delivery-create-address", "配送地址", "新增地址", "外送"],
        },
        "query_meals": {
            "exact_names": ["query-meals"],
            "keywords": ["query-meals", "菜单", "餐品列表"],
        },
        "calculate_price": {
            "exact_names": ["calculate-price"],
            "keywords": ["calculate-price", "价格", "优惠", "总价"],
        },
        "create_order": {
            "exact_names": ["create-order"],
            "keywords": ["create-order", "下单", "创建订单"],
        },
        "query_order": {
            "exact_names": ["query-order"],
            "keywords": ["query-order", "订单详情", "订单状态"],
        },
    }

    def __init__(self, settings: Settings, timeout: int = 20) -> None:
        self.settings = settings
        self.timeout = timeout
        self._session_id: Optional[str] = None
        self._request_id = 0
        self._tools_cache: Optional[list[dict[str, Any]]] = None
        self._resolved_tools: dict[str, dict[str, Any]] = {}
        self._initialized: bool = False  # ✅ 标记是否已初始化

    def _validate_token(self) -> None:
        if not self.settings.mcd_mcp_token:
            raise McdToolError("麦当劳 MCP Token 未配置，请填写 MCD_MCP_TOKEN。")

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _base_headers(self, method_name: str, tool_name: Optional[str] = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.settings.mcd_mcp_token}",
            "Mcp-Method": method_name,
            "Origin": "https://open.mcd.cn",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if tool_name:
            headers["Mcp-Name"] = tool_name
        return headers

    def _parse_response(self, response: Response) -> dict[str, Any]:
        if session_id := response.headers.get("Mcp-Session-Id"):
            self._session_id = session_id

        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return response.json()
        if "text/event-stream" in content_type:
            return self._parse_sse_response(response.text)
        raise McdToolError(f"MCP 返回了不支持的 Content-Type: {content_type}")

    @staticmethod
    def _parse_sse_response(body: str) -> dict[str, Any]:
        payloads: list[str] = []
        for line in body.splitlines():
            if line.startswith("data:"):
                payloads.append(line[len("data:") :].strip())
        if not payloads:
            raise McdToolError("MCP SSE 响应中没有可解析的数据。")
        for payload in reversed(payloads):
            if payload and payload != "[DONE]":
                return json.loads(payload)
        raise McdToolError("MCP SSE 响应中没有 JSON-RPC 结果。")

    def _post(self, method_name: str, params: Optional[dict[str, Any]] = None, tool_name: Optional[str] = None) -> dict[str, Any]:
        self._validate_token()
        request_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method_name,
            "params": params or {},
        }
        headers = self._base_headers(method_name, tool_name)
        logger.info("Calling McD MCP method=%s tool=%s request_id=%s", method_name, tool_name, request_id)
        try:
            response = requests.post(
                self.settings.mcd_mcp_base_url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            result = self._parse_response(response)
        except RequestException as exc:
            raise McdToolError(f"请求麦当劳 MCP 失败: {exc}") from exc
        except ValueError as exc:
            raise McdToolError("麦当劳 MCP 返回了不可解析的 JSON。") from exc

        if "error" in result:
            raise McdToolError(str(result["error"]))
        return result

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(ch.lower() for ch in value if ch.isalnum())

    @staticmethod
    def _coerce_json(text: str) -> Any:
        return json.loads(text.strip())

    @classmethod
    def _collect_dicts(cls, value: Any) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        if isinstance(value, dict):
            collected.append(value)
            for nested in value.values():
                collected.extend(cls._collect_dicts(nested))
        elif isinstance(value, list):
            for item in value:
                collected.extend(cls._collect_dicts(item))
        elif isinstance(value, str):
            try:
                parsed = cls._coerce_json(value)
            except ValueError:
                return collected
            collected.extend(cls._collect_dicts(parsed))
        return collected

    @classmethod
    def _extract_dicts_from_result(cls, result: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        rpc_result = result.get("result") if isinstance(result, dict) else None
        if isinstance(rpc_result, dict):
            structured = rpc_result.get("structuredContent")
            if structured is not None:
                candidates.extend(cls._collect_dicts(structured))
            content = rpc_result.get("content") or []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    candidates.extend(cls._collect_dicts(block["text"]))
                elif block.get("type") == "json":
                    candidates.extend(cls._collect_dicts(block.get("json")))
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = json.dumps(candidate, ensure_ascii=False, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    @classmethod
    def _extract_structured_content(cls, result: dict[str, Any]) -> dict[str, Any]:
        rpc_result = result.get("result") or {}
        structured = rpc_result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        for candidate in cls._extract_dicts_from_result(result):
            if "success" in candidate or "code" in candidate or "data" in candidate:
                return candidate
        raise McdToolError("无法从 MCP 响应中提取结构化结果。")

    @staticmethod
    def _looks_like_address(record: dict[str, Any]) -> bool:
        keys = {key.lower() for key in record.keys()}
        return "addressid" in keys and "storecode" in keys and "becode" in keys

    @classmethod
    def _extract_address_dicts(cls, result: dict[str, Any]) -> list[dict[str, Any]]:
        addresses: list[dict[str, Any]] = []
        structured = cls._extract_structured_content(result)
        data = structured.get("data") or {}
        raw_addresses = data.get("addresses") or []
        for item in raw_addresses:
            if isinstance(item, dict) and cls._looks_like_address(item):
                addresses.append(item)
        return addresses

    def _resolve_tool(self, capability: str) -> dict[str, Any]:
        if capability in self._resolved_tools:
            return self._resolved_tools[capability]

        config = self._MCP_TOOL_CANDIDATES[capability]
        normalized_names = {self._normalize(name) for name in config["exact_names"]}
        tools = self.list_tools()

        for tool in tools:
            if self._normalize(str(tool.get("name") or "")) in normalized_names:
                self._resolved_tools[capability] = tool
                return tool

        scored: list[tuple[int, dict[str, Any]]] = []
        for tool in tools:
            haystack = " ".join(
                [
                    str(tool.get("name") or ""),
                    str(tool.get("description") or ""),
                    # call list/tools get all available tools and store input and output schema restriction
                    json.dumps(tool.get("inputSchema") or {}, ensure_ascii=False, sort_keys=True),
                ]
            ).lower()
            score = sum(1 for keyword in config["keywords"] if keyword.lower() in haystack)
            if score > 0:
                scored.append((score, tool))
        if scored:
            scored.sort(key=lambda item: item[0], reverse=True)
            chosen = scored[0][1]
            self._resolved_tools[capability] = chosen
            return chosen

        available = ", ".join(str(tool.get("name")) for tool in tools[:20])
        raise McdToolError(f"未发现可用于 {capability} 的 MCP 工具。当前可见工具: {available}")

    @staticmethod
    def _adapt_arguments(tool: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        schema = tool.get("inputSchema") or {}
        properties = schema.get("properties") or {}
        if not properties:
            return {k: v for k, v in arguments.items() if v is not None}

        normalized_properties = {McdMcpClient._normalize(key): key for key in properties.keys()}
        adapted: dict[str, Any] = {}
        for key, value in arguments.items():
            if value is None:
                continue
            if key in properties:
                adapted[key] = value
                continue
            normalized = McdMcpClient._normalize(key)
            if normalized in normalized_properties:
                adapted[normalized_properties[normalized]] = value
            else:
                adapted[key] = value
        return adapted

    def initialize(self) -> dict[str, Any]:
        if self._initialized:  # ✅ 已初始化，跳过
            return {"session_id": self._session_id}

        result = self._post(
            "initialize",
            {
                "protocolVersion": self.settings.mcd_mcp_protocol_version,
                "capabilities": {},
                "clientInfo": {
                    "name": "mcd-ordering-agent",
                    "version": "0.1.0",
                },
            },
        )
        self._initialized = True  # ✅ 标记已初始化
        try:
            requests.post(
                self.settings.mcd_mcp_base_url,
                headers=self._base_headers("notifications/initialized"),
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                timeout=self.timeout,
            )
        except RequestException:
            logger.warning("MCP initialized notification failed, continuing with current session.")
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        if self._tools_cache is not None:
            return self._tools_cache
        self.initialize()
        result = self._post("tools/list", {})
        tools = (result.get("result") or {}).get("tools") or []
        self._tools_cache = tools
        return tools

    def call_tool(self, name: str, arguments: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        self.initialize()
        return self._post("tools/call", {"name": name, "arguments": arguments or {}}, tool_name=name)

    def find_tool(self, name: str) -> Optional[dict[str, Any]]:
        normalized_target = self._normalize(name)
        for tool in self.list_tools():
            if self._normalize(str(tool.get("name") or "")) == normalized_target:
                return tool
        return None

    @staticmethod
    def extract_text(result: dict[str, Any]) -> str:
        blocks = (result.get("result") or {}).get("content") or []
        parts = [block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text"]
        return "\n".join(part for part in parts if part).strip()

    @classmethod
    def extract_json_objects(cls, result: dict[str, Any]) -> list[dict[str, Any]]:
        return cls._extract_dicts_from_result(result)

    def get_addresses(self, address_id: Optional[str] = None) -> dict[str, Any]:
        tool = self._resolve_tool("list_addresses")
        result = self.call_tool(str(tool.get("name")), self._adapt_arguments(tool, {"beType": 2}))
        addresses = self._extract_address_dicts(result)
        if address_id:
            addresses = [item for item in addresses if str(item.get("addressId") or "") == str(address_id)]
        return {"data": addresses}

    def create_address(self, payload: dict[str, Any]) -> dict[str, Any]:
        tool = self._resolve_tool("create_address")
        arguments = {
            "beType": 2,
            "city": payload.get("city") or payload.get("cityName") or payload.get("city_name") or payload.get("cityCode"),
            "contactName": payload.get("contactName") or payload.get("fullName") or payload.get("full_name"),
            "gender": payload.get("gender"),
            "phone": payload.get("phone"),
            "address": payload.get("address"),
            "addressDetail": payload.get("addressDetail") or payload.get("detail"),
        }
        result = self.call_tool(str(tool.get("name")), self._adapt_arguments(tool, arguments))
        structured = self._extract_structured_content(result)
        data = structured.get("data") or {}
        if isinstance(data, dict) and data.get("addressId"):
            return {"data": data}
        raise McdToolError("新增地址成功后未能从 MCP 结果中提取 addressId。")

    def delete_address(self, address_id: str) -> dict[str, Any]:
        raise McdToolError("截至 2026-04-30，真实麦当劳 MCP tools/list 中未暴露删除地址工具，当前无法通过 MCP 执行删除地址。")

    def get_address_detail(self, address_id: str) -> dict[str, Any]:
        result = self.get_addresses(address_id)
        addresses = result.get("data") or []
        if not addresses:
            raise McdToolError(f"未找到 addressId={address_id} 的地址。")
        return {"data": addresses[0]}

    def get_stores_vicinity(
        self,
        *,
        address_id: str,
        city_code: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        order_type: int = 2,
        be_type: Optional[int] = None,
        distance: Optional[int] = None,
        keyword: Optional[str] = None,
        show_type: Optional[int] = None,
    ) -> dict[str, Any]:
        del city_code, latitude, longitude, order_type, be_type, distance, keyword, show_type
        addresses = self.get_addresses(address_id).get("data") or []
        stores: list[dict[str, Any]] = []
        for item in addresses:
            store_code = item.get("storeCode")
            if not store_code:
                continue
            stores.append(
                {
                    "storeCode": store_code,
                    "storeName": item.get("storeName"),
                    "beCode": item.get("beCode"),
                    "address": item.get("fullAddress"),
                }
            )
        return {"data": stores}

    def get_menu(
        self,
        *,
        store_code: str,
        be_code: str,
        order_type: int,
        day_part_code: Optional[str] = None,
        date: Optional[str] = None,
        time_value: Optional[str] = None,
        channel_code: Optional[str] = None,
        is_group_meal: int = 0,
    ) -> dict[str, Any]:
        del day_part_code, date, time_value, channel_code, is_group_meal
        tool = self._resolve_tool("query_meals")
        arguments = self._adapt_arguments(
            tool,
            {
                "storeCode": store_code,
                "beCode": be_code,
                "orderType": order_type,
            },
        )
        result = self.call_tool(str(tool.get("name")), arguments)
        structured = self._extract_structured_content(result)
        data = structured.get("data") or {}
        categories = data.get("categories") or []
        meals_by_code = data.get("meals") or {}

        menu: list[dict[str, Any]] = []
        for category in categories:
            category_name = category.get("name")
            products: list[dict[str, Any]] = []
            for meal_ref in category.get("meals") or []:
                code = meal_ref.get("code")
                if not code:
                    continue
                meal_detail = meals_by_code.get(code) or {}
                products.append(
                    {
                        "code": code,
                        "name": meal_detail.get("name"),
                        "price": _to_decimal(meal_detail.get("currentPrice")),
                        "tags": meal_ref.get("tags") or [],
                        "daypart": category.get("daypart"),
                    }
                )
            menu.append({"categoryName": category_name, "products": products, "daypart": category.get("daypart")})
        return {"data": {"menu": menu}}

    def calculate_price(
        self,
        *,
        store_code: str,
        be_code: str,
        order_type: int,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        tool = self._resolve_tool("calculate_price")
        tool_items = []
        for item in items:
            tool_items.append(
                {
                    "productCode": item.get("productCode") or item.get("code"),
                    "quantity": item.get("quantity") or 1,
                    "couponId": item.get("couponId"),
                    "couponCode": item.get("couponCode"),
                }
            )
        arguments = self._adapt_arguments(
            tool,
            {
                "storeCode": store_code,
                "beCode": be_code,
                "orderType": order_type,
                "items": tool_items,
            },
        )
        result = self.call_tool(str(tool.get("name")), arguments)
        structured = self._extract_structured_content(result)
        return {"data": structured.get("data") or {}}

    def create_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        tool = self._resolve_tool("create_order")
        items = payload.get("items")
        if items is None:
            cart_items = payload.get("cartItems") or []
            items = [
                {
                    "productCode": item.get("productCode"),
                    "quantity": item.get("quantity"),
                    "couponId": item.get("couponId"),
                    "couponCode": item.get("couponCode"),
                }
                for item in cart_items
            ]
        arguments = self._adapt_arguments(
            tool,
            {
                "addressId": payload.get("addressId"),
                "beCode": payload.get("beCode"),
                "orderType": payload.get("orderType"),
                "storeCode": payload.get("storeCode"),
                "takeWayCode": payload.get("takeWayCode"),
                "items": items,
            },
        )
        result = self.call_tool(str(tool.get("name")), arguments)
        return self._extract_structured_content(result)

    def query_order(self, order_id: str) -> dict[str, Any]:
        tool = self._resolve_tool("query_order")
        result = self.call_tool(str(tool.get("name")), self._adapt_arguments(tool, {"orderId": order_id}))
        return self._extract_structured_content(result)

    # ==================== 营养查询相关方法 ====================
    
    def query_nutrition(self, product_name: str) -> dict[str, Any] | None:
        """
        直接查询单个商品的营养成分（优先使用 MCP）
        
        Args:
            product_name: 商品名称
            
        Returns:
            包含营养成分的字典，查询失败返回 None
        """
        # 尝试不同的关键词查询
        for keyword in [product_name, "麦当劳", ""]:
            try:
                result = self.call_nutrition_tool(keyword)
                if result:
                    # 从结果中查找匹配的商品
                    matched = self._find_matching_nutrition(product_name, result)
                    if matched:
                        return {
                            "product_name": product_name,
                            "source": "mcp",
                            "nutrition": matched,
                            "note": "来自麦当劳 MCP 营养工具"
                        }
            except McdToolError as exc:
                logger.debug(f"MCP 营养查询尝试失败 (keyword={keyword}): {exc}")
                continue
        
        return None

    def call_nutrition_tool(self, keyword: str = "") -> list[dict[str, Any]] | None:
        """
        调用 list-nutrition-foods 工具获取营养数据
        
        Args:
            keyword: 查询关键词
            
        Returns:
            解析后的营养数据列表，失败返回 None
        """
        tool = self.find_tool("list-nutrition-foods")
        if not tool:
            raise McdToolError("未发现 list-nutrition-foods 工具")
        
        # 根据工具 schema 构建参数
        arguments = {}
        schema = tool.get("inputSchema") or {}
        properties = schema.get("properties") or {}
        
        # 尝试不同的参数名
        for field_name in ("keyword", "query", "foodName", "productName", "name"):
            if field_name in properties:
                arguments[field_name] = keyword
                break
        
        result = self.call_tool("list-nutrition-foods", arguments)
        return self._parse_nutrition_response(result)

    def _parse_nutrition_response(self, result: dict[str, Any]) -> list[dict[str, Any]] | None:
        """
        解析营养查询响应
        
        优先从 structuredContent.data 节点提取数据
        
        Args:
            result: MCP 调用结果
            
        Returns:
            营养数据字典列表
        """
        # 优先从 structuredContent.data 提取
        toon_data = self._extract_structured_content_data(result)
        
        if not toon_data:
            # 备用：从 text content 提取
            toon_data = self.extract_text(result)
        
        if not toon_data or not toon_data.strip():
            return None
        
        # 解析 TOON 格式
        return self._parse_toon_format(toon_data)

    def _extract_structured_content_data(self, result: dict[str, Any]) -> str | None:
        """
        从 MCP 结果中提取 structuredContent.data 节点
        
        Args:
            result: MCP 调用结果
            
        Returns:
            TOON 格式数据字符串
        """
        structured = result.get("result", {}).get("structuredContent", {})
        data = structured.get("data")
        
        if isinstance(data, str) and data.strip():
            return data
        
        return None

    def _parse_toon_format(self, text: str) -> list[dict[str, Any]]:
        """
        解析 TOON 格式文本
        
        TOON 格式示例：
        [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
          猪柳麦满分,null,1288,308,16,16,24,781,213
          板烧鸡腿堡,null,1638,391,23,17,35,1041,93
          ...
        
        Args:
            text: TOON 格式文本
            
        Returns:
            解析后的字典列表
        """
        lines = text.strip().split('\n')
        if not lines:
            return []
        
        # 解析头部：提取字段名
        header_line = lines[0].strip()
        import re
        header_match = re.search(r'\[(\d+)\]\{(.+?)\}', header_line)
        if not header_match:
            return []
        
        fields = [f.strip() for f in header_match.group(2).split(',')]
        
        # 解析数据行
        records = []
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            
            values = self._parse_toon_csv_line(line, len(fields))
            if len(values) == len(fields):
                record = dict(zip(fields, values))
                records.append(record)
        
        return records

    def _parse_toon_csv_line(self, line: str, expected_count: int) -> list[str | None]:
        """
        解析 TOON CSV 格式的行
        
        Args:
            line: CSV 行
            expected_count: 期望的字段数量
            
        Returns:
            字段值列表
        """
        values = []
        current = ""
        in_quotes = False
        
        for char in line:
            if char == '"':
                in_quotes = not in_quotes
            elif char == ',' and not in_quotes:
                value = current.strip()
                values.append(None if value.lower() == 'null' else value)
                current = ""
            else:
                current += char
        
        # 添加最后一个字段
        value = current.strip()
        values.append(None if value.lower() == 'null' else value)
        
        return values

    def _find_matching_nutrition(self, product_name: str, nutrition_records: list[dict[str, Any]]) -> dict[str, Any] | None:
        """
        从营养记录列表中找到匹配的商品
        
        Args:
            product_name: 要查找的商品名称
            nutrition_records: 营养记录列表
            
        Returns:
            匹配的营养数据，查找失败返回 None
        """
        if not nutrition_records:
            return None
        
        normalized_query = self._normalize_nutrition_name(product_name)
        
        # 查找匹配
        for record in nutrition_records:
            product_name_field = record.get("productName", "")
            if not product_name_field:
                continue
            
            normalized_record_name = self._normalize_nutrition_name(product_name_field)
            
            # 匹配逻辑：完全匹配或包含关系
            if (normalized_query == normalized_record_name or 
                normalized_query in normalized_record_name or 
                normalized_record_name in normalized_query):
                return self._convert_toon_to_nutrition(record)
        
        return None

    @staticmethod
    def _normalize_nutrition_name(name: str) -> str:
        """
        标准化商品名称
        
        Args:
            name: 商品名称
            
        Returns:
            标准化后的名称
        """
        return "".join(name.lower().replace("（", "(").replace("）", ")").split())

    def _convert_toon_to_nutrition(self, record: dict[str, Any]) -> dict[str, Any]:
        """
        将 TOON 格式的营养记录转换为标准格式
        
        TOON 字段 -> 标准字段：
        - energyKcal -> calories
        - protein -> protein_g
        - fat -> fat_g
        - carbohydrate -> carbs_g
        - sodium -> sodium_mg
        
        Args:
            record: TOON 格式记录
            
        Returns:
            标准化的营养数据
        """
        nutrition = {}
        
        # 热量
        energy_kcal = record.get('energyKcal')
        if energy_kcal and energy_kcal != 'null':
            try:
                nutrition['calories'] = int(float(energy_kcal))
            except (ValueError, TypeError):
                pass
        
        # 蛋白质
        protein = record.get('protein')
        if protein and protein != 'null':
            try:
                nutrition['protein_g'] = float(protein)
            except (ValueError, TypeError):
                pass
        
        # 脂肪
        fat = record.get('fat')
        if fat and fat != 'null':
            try:
                nutrition['fat_g'] = float(fat)
            except (ValueError, TypeError):
                pass
        
        # 碳水化合物
        carbs = record.get('carbohydrate')
        if carbs and carbs != 'null':
            try:
                nutrition['carbs_g'] = float(carbs)
            except (ValueError, TypeError):
                pass
        
        # 钠
        sodium = record.get('sodium')
        if sodium and sodium != 'null':
            try:
                nutrition['sodium_mg'] = float(sodium)
            except (ValueError, TypeError):
                pass
        
        # 糖（TOON 格式中没有单独的糖字段）
        # 钙
        calcium = record.get('calcium')
        if calcium and calcium != 'null':
            try:
                nutrition['calcium_mg'] = float(calcium)
            except (ValueError, TypeError):
                pass
        
        return nutrition
