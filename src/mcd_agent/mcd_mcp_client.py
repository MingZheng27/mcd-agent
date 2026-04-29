from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import requests
from requests import RequestException, Response

from .config import Settings

logger = logging.getLogger(__name__)


class McdApiError(RuntimeError):
    pass


class McdToolError(RuntimeError):
    pass


class McdMcpClient:
    _MCP_TOOL_CANDIDATES: dict[str, dict[str, list[str]]] = {
        "list_addresses": {
            "exact_names": [
                "delivery-query-addresses",
            ],
            "keywords": ["delivery-query-addresses", "配送地址", "外送", "麦乐送"],
        },
        "create_address": {
            "exact_names": [
                "delivery-create-address",
            ],
            "keywords": ["delivery-create-address", "配送地址", "新增地址", "外送"],
        },
        "delete_address": {
            "exact_names": [
            ],
            "keywords": ["delete-address", "remove-address", "删除地址"],
        },
        "stores_vicinity": {
            "exact_names": [
                "query-nearby-stores",
            ],
            "keywords": ["query-nearby-stores", "门店", "附近", "到店"],
        },
    }

    def __init__(self, settings: Settings, timeout: int = 20) -> None:
        self.settings = settings
        self.timeout = timeout
        self._session_id: str | None = None
        self._request_id = 0
        self._tools_cache: list[dict[str, Any]] | None = None
        self._resolved_tools: dict[str, dict[str, Any]] = {}

    def _sign(self, body: str, timestamp: str) -> str:
        sign_str = (
            f"AppId={self.settings.mcd_app_id}"
            f"&Body={body}"
            f"&MerchantId={self.settings.mcd_merchant_id}"
            f"&Timestamp={timestamp}"
            f"&key={self.settings.mcd_sign_key}"
        )
        return hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()

    def _headers(self, body: str) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        trace_id = uuid.uuid4().hex
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "AppId": self.settings.mcd_app_id,
            "MerchantId": self.settings.mcd_merchant_id,
            "Timestamp": timestamp,
            "TraceId": trace_id,
            "Version": self.settings.mcd_version,
            "Sign": self._sign(body=body, timestamp=timestamp),
        }

    def _base_headers(self, method_name: str, tool_name: str | None = None) -> dict[str, str]:
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

    def _validate_credentials(self) -> None:
        required = [
            self.settings.mcd_app_id,
            self.settings.mcd_merchant_id,
            self.settings.mcd_sign_key,
        ]
        if not all(required):
            raise McdApiError("麦当劳开放平台凭据未配置，请填写 MCD_APP_ID / MCD_MERCHANT_ID / MCD_SIGN_KEY。")

    def _validate_token(self) -> None:
        if not self.settings.mcd_mcp_token:
            raise McdToolError("麦当劳 MCP Token 未配置，请填写 MCD_MCP_TOKEN。")

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _request_openapi(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._validate_credentials()

        if method.upper() == "GET":
            query = f"?{urlencode(params or {}, doseq=True)}" if params else ""
            body_for_sign = f"{path}{query}"
            url = f"{self.settings.mcd_base_url}{path}{query}"
            data = None
        else:
            payload = payload or {}
            body_for_sign = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            url = f"{self.settings.mcd_base_url}{path}"
            data = body_for_sign.encode("utf-8")

        headers = self._headers(body=body_for_sign)
        safe_headers = {**headers, "AppId": "***", "MerchantId": "***", "Sign": "***"}
        logger.info("Calling McD OpenAPI method=%s path=%s headers=%s payload=%s", method, path, safe_headers, payload or params)

        try:
            response = requests.request(method, url, headers=headers, data=data, timeout=self.timeout)
            response.raise_for_status()
            result = response.json()
        except RequestException as exc:
            raise McdApiError(f"请求麦当劳接口失败: {exc}") from exc
        except ValueError as exc:
            raise McdApiError("麦当劳接口返回了不可解析的 JSON。") from exc

        logger.info("McD OpenAPI response path=%s code=%s success=%s", path, result.get("code"), result.get("success"))
        if result.get("success") is False:
            raise McdApiError(result.get("message") or f"接口调用失败: {path}")
        return result

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

    def _post(self, method_name: str, params: dict[str, Any] | None = None, tool_name: str | None = None) -> dict[str, Any]:
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
    def _today_compact() -> str:
        return time.strftime("%Y%m%d")

    @staticmethod
    def _time_hm() -> str:
        return time.strftime("%H:%M")

    @staticmethod
    def _time_hms() -> str:
        return time.strftime("%H:%M:%S")

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(ch.lower() for ch in value if ch.isalnum())

    @staticmethod
    def _coerce_json(text: str) -> Any | None:
        candidate = text.strip()
        if not candidate:
            return None
        try:
            return json.loads(candidate)
        except ValueError:
            return None

    @staticmethod
    def _looks_like_address(record: dict[str, Any]) -> bool:
        keys = {key.lower() for key in record.keys()}
        return (
            ("citycode" in keys and "detail" in keys)
            or ("fullname" in keys and "phone" in keys)
            or ("addressid" in keys and "contactname" in keys and "fulladdress" in keys)
            or ("addressid" in keys and "storecode" in keys and "becode" in keys)
        )

    @staticmethod
    def _looks_like_store(record: dict[str, Any]) -> bool:
        keys = {key.lower() for key in record.keys()}
        return (
            "code" in keys and ("distance" in keys or "distancetext" in keys or "becode" in keys or "dayparts" in keys)
        ) or ("storecode" in keys and "storename" in keys)

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
            parsed = cls._coerce_json(value)
            if parsed is not None:
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
        candidates.extend(cls._collect_dicts(result))
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
    def _extract_address_dicts(cls, result: dict[str, Any]) -> list[dict[str, Any]]:
        addresses: list[dict[str, Any]] = []
        for candidate in cls._extract_dicts_from_result(result):
            if cls._looks_like_address(candidate):
                addresses.append(candidate)
            for key in ("addresses", "list", "data", "items"):
                nested = candidate.get(key)
                if isinstance(nested, list):
                    addresses.extend(item for item in nested if isinstance(item, dict) and cls._looks_like_address(item))
        return addresses

    @classmethod
    def _extract_store_dicts(cls, result: dict[str, Any]) -> list[dict[str, Any]]:
        stores: list[dict[str, Any]] = []
        for candidate in cls._extract_dicts_from_result(result):
            if isinstance(candidate.get("mdsStore"), dict):
                stores.append(candidate)
                continue
            if cls._looks_like_store(candidate):
                stores.append(candidate)
            for key in ("stores", "list", "data", "items"):
                nested = candidate.get(key)
                if isinstance(nested, list):
                    for item in nested:
                        if isinstance(item, dict) and (isinstance(item.get("mdsStore"), dict) or cls._looks_like_store(item)):
                            stores.append(item)
        return stores

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
            text_parts = [
                str(tool.get("name") or ""),
                str(tool.get("description") or ""),
                json.dumps(tool.get("inputSchema") or {}, ensure_ascii=False, sort_keys=True),
            ]
            haystack = " ".join(text_parts).lower()
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
            return arguments

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
        if self._session_id:
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

        initialized_headers = self._base_headers("notifications/initialized")
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        try:
            requests.post(
                self.settings.mcd_mcp_base_url,
                headers=initialized_headers,
                json=notification,
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

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        self.initialize()
        return self._post("tools/call", {"name": name, "arguments": arguments or {}}, tool_name=name)

    def find_tool(self, name: str) -> dict[str, Any] | None:
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

    def get_cities(self, get_current: bool = False) -> dict[str, Any]:
        return self._request_openapi("GET", "/cities/all", params={"getCurrent": int(get_current)})

    def get_addresses(self, address_id: str | None = None) -> dict[str, Any]:
        tool = self._resolve_tool("list_addresses")
        arguments = self._adapt_arguments(tool, {"beType": 2})
        result = self.call_tool(str(tool.get("name")), arguments)
        addresses = self._extract_address_dicts(result)
        if address_id:
            addresses = [item for item in addresses if str(item.get("id") or item.get("addressId") or "") == str(address_id)]
        return {"data": addresses}

    def create_address(self, payload: dict[str, Any]) -> dict[str, Any]:
        tool = self._resolve_tool("create_address")
        arguments = {
            "beType": 2,
            "city": payload.get("city") or payload.get("cityName") or payload.get("city_code") or payload.get("cityCode"),
            "contactName": payload.get("contactName") or payload.get("fullName") or payload.get("full_name"),
            "gender": payload.get("gender"),
            "phone": payload.get("phone"),
            "address": payload.get("address"),
            "addressDetail": payload.get("addressDetail") or payload.get("detail"),
        }
        result = self.call_tool(str(tool.get("name")), self._adapt_arguments(tool, arguments))

        addresses = self._extract_address_dicts(result)
        if addresses:
            return {"data": addresses[0]}

        for candidate in self._extract_dicts_from_result(result):
            address_id = candidate.get("id") or candidate.get("addressId")
            if address_id:
                return {"data": {"id": address_id}}

        raise McdToolError("新增地址成功后未能从 MCP 结果中提取 addressId。")

    def delete_address(self, address_id: str) -> dict[str, Any]:
        raise McdToolError("截至 2026-04-29，真实麦当劳 MCP tools/list 中未暴露删除地址工具，当前无法通过 MCP 执行删除地址。")

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
        city_code: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        order_type: int = 2,
        be_type: int | None = None,
        distance: int | None = None,
        keyword: str | None = None,
        show_type: int | None = None,
    ) -> dict[str, Any]:
        # The live delivery MCP currently returns the matched deliverable store on each address
        # instead of exposing a separate "delivery stores vicinity" tool.
        addresses = self.get_addresses(address_id).get("data") or []
        stores: list[dict[str, Any]] = []
        for item in addresses:
            store_code = item.get("storeCode")
            be_code = item.get("beCode")
            store_name = item.get("storeName")
            if not store_code:
                continue
            stores.append(
                {
                    "storeCode": store_code,
                    "storeName": store_name,
                    "beCode": be_code,
                    "address": item.get("fullAddress"),
                }
            )
        return {"data": stores}

    def get_store_detail(self, store_code: str) -> dict[str, Any]:
        return self._request_openapi("GET", f"/stores/{store_code}")

    def get_store_be_detail(self, be_code: str, is_group_meal: int = 0) -> dict[str, Any]:
        return self._request_openapi(
            "GET",
            f"/stores/be/{be_code}",
            params={
                "date": self._today_compact(),
                "time": self._time_hm(),
                "isGroupMeal": is_group_meal,
            },
        )

    def get_menu(
        self,
        *,
        store_code: str,
        be_code: str,
        order_type: int,
        day_part_code: str,
        date: str | None = None,
        time_value: str | None = None,
        channel_code: str | None = None,
        is_group_meal: int = 0,
    ) -> dict[str, Any]:
        payload = {
            "date": date or time.strftime("%Y-%m-%d"),
            "orderType": order_type,
            "beCode": be_code,
            "dayPartCode": day_part_code,
            "time": time_value or self._time_hms(),
            "isGroupMeal": is_group_meal,
            "channelCode": channel_code or self.settings.default_channel_code,
            "storeCode": store_code,
        }
        return self._request_openapi("POST", "/products/menu", payload=payload)

    def get_cart(
        self,
        *,
        store_code: str,
        order_type: int,
        day_part_code: str,
        be_code: str | None = None,
        cart_type: int = 1,
        is_group_meal: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "daypartCode": day_part_code,
            "cartType": cart_type,
            "date": self._today_compact(),
            "time": self._time_hm(),
            "isGroupMeal": is_group_meal,
        }
        if be_code:
            params["beCode"] = be_code
        return self._request_openapi("GET", "/carts", params=params)

    def update_cart(
        self,
        *,
        store_code: str,
        order_type: int,
        day_part_code: str,
        products: list[dict[str, Any]],
        be_code: str | None = None,
        cart_type: int = 1,
        data_source: int = 1,
        is_group_meal: int = 0,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "daypartCode": int(day_part_code),
            "cartType": cart_type,
            "date": self._today_compact(),
            "time": self._time_hm(),
            "dataSource": data_source,
            "isGroupMeal": is_group_meal,
            "products": products,
        }
        if be_code:
            payload["beCode"] = be_code
        return self._request_openapi("PUT", "/carts", payload=payload)

    def clear_cart(
        self,
        *,
        store_code: str,
        order_type: int,
        day_part_code: str,
        be_code: str | None = None,
        cart_type: int = 1,
        is_group_meal: int = 0,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "daypartCode": int(day_part_code),
            "cartType": cart_type,
            "date": self._today_compact(),
            "time": self._time_hm(),
            "isGroupMeal": is_group_meal,
        }
        if be_code:
            payload["beCode"] = be_code
        return self._request_openapi("PUT", "/carts/empty", payload=payload)

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_openapi("POST", "/orders", payload=payload)
