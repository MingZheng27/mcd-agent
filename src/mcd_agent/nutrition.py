from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .mcd_mcp_client import McdMcpClient, McdToolError, logger
from .models import (
    CandidateItem,
    NutritionFacts,
    NutritionLineItem,
    NutritionPreference,
    NutritionReport,
    OrderItem,
    UserPreference,
)


class NutritionCatalog:
    def __init__(self, catalog_path: Path) -> None:
        self.catalog_path = catalog_path
        self.items = self._load()

    def _load(self) -> dict[str, NutritionFacts]:
        if not self.catalog_path.exists():
            return {}
        raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        return {name.lower(): NutritionFacts.model_validate(value) for name, value in raw.items()}

    def find(self, product_name: str) -> NutritionFacts | None:
        return self.items.get(product_name.strip().lower())


class RecommendationEngine:
    def __init__(self, catalog: NutritionCatalog) -> None:
        self.catalog = catalog

    def _normalize_price(self, value: Any) -> int | float | Decimal | None:
        """规范化价格字段，支持整数、浮点、Decimal或None"""
        if value is None:
            return None
        if isinstance(value, (int, float, Decimal)):
            return value
        try:
            return Decimal(str(value))
        except (ValueError, TypeError):
            return None

    def enrich_and_rank(self, menu_items: list[dict], preference: UserPreference) -> list[CandidateItem]:
        ranked: list[CandidateItem] = []
        for item in menu_items:
            name = str(item.get("name") or item.get("productName") or "").strip()
            if not name:
                continue
            nutrition = self.catalog.find(name)
            candidate = CandidateItem(
                code=str(item.get("code") or item.get("productCode") or ""),
                name=name,
                category=item.get("categoryName"),
                price=self._normalize_price(item.get("price") or item.get("realPrice")),
                nutrition=nutrition,
                raw=item,
            )
            candidate.score, candidate.reasons = self._score(candidate, preference)
            ranked.append(candidate)

        ranked.sort(key=lambda x: x.score, reverse=True)
        return ranked

    def _score(self, item: CandidateItem, preference: UserPreference) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        nutrition_pref = preference.nutrition
        lower_name = item.name.lower()

        for liked in preference.taste_preferences:
            if liked.lower() in lower_name:
                score += 1.2
                reasons.append(f"符合口味偏好: {liked}")

        for disliked in preference.disliked_ingredients:
            if disliked.lower() in lower_name:
                score -= 3.0
                reasons.append(f"命中不喜欢的食材: {disliked}")

        for allergen in preference.allergens:
            if allergen.lower() in lower_name:
                score -= 5.0
                reasons.append(f"可能命中过敏原关键词: {allergen}")

        if item.nutrition:
            nutrition_score, nutrition_reasons = self._score_nutrition(item.nutrition, nutrition_pref)
            score += nutrition_score
            reasons.extend(nutrition_reasons)
        else:
            reasons.append("暂无营养数据，按口味和菜单可用性推荐")

        if preference.meal_type and preference.meal_type.lower() in lower_name:
            score += 0.8
            reasons.append(f"更接近目标餐型: {preference.meal_type}")

        return score, reasons

    def _score_nutrition(self, facts: NutritionFacts, preference: NutritionPreference) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []

        if preference.max_calories is not None and facts.calories is not None:
            if facts.calories <= preference.max_calories:
                score += 2.0
                reasons.append(f"热量 {facts.calories} <= 目标上限 {preference.max_calories}")
            else:
                score -= 2.0
                reasons.append(f"热量 {facts.calories} 超出目标上限 {preference.max_calories}")

        if preference.min_protein_g is not None and facts.protein_g is not None:
            if facts.protein_g >= preference.min_protein_g:
                score += 1.5
                reasons.append(f"蛋白质 {facts.protein_g}g 达到目标")
            else:
                score -= 1.0
                reasons.append(f"蛋白质 {facts.protein_g}g 低于目标")

        if preference.max_fat_g is not None and facts.fat_g is not None:
            if facts.fat_g <= preference.max_fat_g:
                score += 1.0
                reasons.append(f"脂肪 {facts.fat_g}g 在可接受范围")
            else:
                score -= 1.0
                reasons.append(f"脂肪 {facts.fat_g}g 偏高")

        if preference.max_sodium_mg is not None and facts.sodium_mg is not None:
            if facts.sodium_mg <= preference.max_sodium_mg:
                score += 1.0
                reasons.append(f"钠 {facts.sodium_mg}mg 在可接受范围")
            else:
                score -= 1.0
                reasons.append(f"钠 {facts.sodium_mg}mg 偏高")

        if preference.max_sugar_g is not None and facts.sugar_g is not None:
            if facts.sugar_g <= preference.max_sugar_g:
                score += 1.0
                reasons.append(f"糖 {facts.sugar_g}g 在可接受范围")
            else:
                score -= 1.0
                reasons.append(f"糖 {facts.sugar_g}g 偏高")

        if preference.dietary_focus:
            reasons.append(f"营养目标: {preference.dietary_focus}")

        return score, reasons


class NutritionAnalyzer:
    def __init__(self, catalog: NutritionCatalog, mcp_tool_client: McdMcpClient | None = None) -> None:
        self.catalog = catalog
        self.mcp_tool_client = mcp_tool_client

    def query_nutrition(self, product_name: str) -> dict[str, Any] | None:
        """
        直接查询单个商品的营养成分
        
        优先级：
        1. 优先从 MCP 营养工具查询（麦当劳官方数据）
        2. MCP 查询失败或无匹配时，使用本地营养库兜底
        
        Args:
            product_name: 商品名称
            
        Returns:
            包含营养成分的字典，查询失败返回None
        """
        # 优先从 MCP 查询营养数据
        if self.mcp_tool_client:
            try:
                mcp_result = self._query_mcp_nutrition(product_name)
                if mcp_result:
                    return mcp_result
            except McdToolError as exc:
                logger.warning(f"MCP营养查询失败: {exc}")
        
        # MCP 查询失败或无匹配时，使用本地营养库兜底
        local_nutrition = self.catalog.find(product_name)
        if local_nutrition:
            return {
                "product_name": product_name,
                "source": "local_catalog",
                "nutrition": local_nutrition.model_dump(),
                "note": "MCP未返回数据，使用本地营养库"
            }
        
        return None

    def _query_mcp_nutrition(self, product_name: str) -> dict[str, Any] | None:
        """从MCP工具查询营养成分"""
        if not self.mcp_tool_client:
            return None
        
        tool = self.mcp_tool_client.find_tool("list-nutrition-foods")
        if not tool:
            raise McdToolError("未发现 list-nutrition-foods 工具")
        
        # 尝试不同的关键词查询
        for keyword in [product_name, "麦当劳", ""]:
            try:
                arguments = {}
                # 根据工具schema构建参数
                schema = tool.get("inputSchema") or {}
                properties = schema.get("properties") or {}
                
                # 尝试不同的参数名
                for field_name in ("keyword", "query", "foodName", "productName", "name"):
                    if field_name in properties:
                        arguments[field_name] = keyword
                        break
                
                result = self.mcp_tool_client.call_tool("list-nutrition-foods", arguments)
                
                # ✅ 从 structuredContent.data 节点提取 TOON 格式数据
                toon_data = self._extract_structured_content_data(result)
                if not toon_data:
                    logger.debug(f"未找到 structuredContent.data，尝试使用文本解析")
                    # 备用：使用 extract_text
                    toon_data = self.mcp_tool_client.extract_text(result)
                
                # 查找匹配的营养记录
                matched = self._parse_nutrition_from_text(product_name, toon_data)
                if matched:
                    return {
                        "product_name": product_name,
                        "source": "mcp",
                        "nutrition": matched,
                        "note": "来自麦当劳MCP营养工具"
                    }
            except (McdToolError, Exception) as exc:
                logger.debug(f"MCP查询尝试失败 (keyword={keyword}): {exc}")
                continue
        
        return None

    def _extract_structured_content_data(self, result: dict[str, Any]) -> str | None:
        """
        从 MCP 结果中提取 structuredContent.data 节点
        
        实际返回格式：
        {
            "result": {
                "content": [...],
                "structuredContent": {
                    "success": true,
                    "code": 200,
                    "data": "[160]{productName,...}:\n  猪柳麦满分,null,..."
                }
            }
        }
        """
        structured = result.get("result", {}).get("structuredContent", {})
        data = structured.get("data")
        
        if isinstance(data, str) and data.strip():
            return data
        
        return None

    def _parse_nutrition_from_text(self, product_name: str, text_content: str) -> dict[str, Any] | None:
        """
        从 TOON 格式的文本中解析营养成分
        
        TOON 格式示例：
        [1]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
          猪柳麦满分,null,1288,308,16,16,24,781,213
        
        Args:
            product_name: 要查询的商品名称
            text_content: MCP 返回的 TOON 格式文本
            
        Returns:
            营养数据字典，解析失败返回 None
        """
        if not text_content or not text_content.strip():
            return None
        
        normalized_query = self._normalize_name(product_name)
        
        # 解析 TOON 格式
        nutrition_data = self._parse_toon_format(text_content)
        
        if not nutrition_data:
            return None
        
        # 在解析的数据中查找匹配的商品
        for record in nutrition_data:
            product_name_field = record.get("productName", "")
            if not product_name_field:
                continue
            
            # 名称匹配
            normalized_record_name = self._normalize_name(product_name_field)
            if (normalized_query == normalized_record_name or 
                normalized_query in normalized_record_name or 
                normalized_record_name in normalized_query):
                
                # 转换为标准营养数据格式
                return self._convert_toon_to_nutrition(record)
        
        return None

    def _parse_toon_format(self, text: str) -> list[dict[str, Any]]:
        """
        解析 TOON 格式文本
        
        TOON 格式：
        [1]{field1,field2,field3}:
          value1,value2,value3
        
        Returns:
            解析后的字典列表
        """
        lines = text.strip().split('\n')
        if not lines:
            return []
        
        # 解析头部：提取字段名
        header_line = lines[0].strip()
        
        # 匹配格式: [1]{field1,field2,...} 或 [N]{field1,field2,...}
        import re
        header_match = re.search(r'\[(\d+)\]\{(.+?)\}', header_line)
        if not header_match:
            # 尝试其他格式
            return self._parse_simple_csv_format(text)
        
        fields_str = header_match.group(2)
        fields = [f.strip() for f in fields_str.split(',')]
        
        # 解析数据行
        records = []
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            
            # 解析 CSV 格式的数据
            values = self._parse_toon_csv_line(line, len(fields))
            if len(values) == len(fields):
                record = dict(zip(fields, values))
                records.append(record)
        
        return records

    def _parse_toon_csv_line(self, line: str, expected_count: int) -> list[str]:
        """
        解析 TOON CSV 格式的行
        
        处理逗号分隔的值，处理 null 值
        """
        values = []
        current = ""
        in_quotes = False
        
        for char in line:
            if char == '"':
                in_quotes = not in_quotes
            elif char == ',' and not in_quotes:
                # 结束当前字段
                value = current.strip()
                values.append(value if value.lower() != 'null' else None)
                current = ""
            else:
                current += char
        
        # 添加最后一个字段
        value = current.strip()
        values.append(value if value.lower() != 'null' else None)
        
        return values

    def _parse_simple_csv_format(self, text: str) -> list[dict[str, Any]]:
        """
        解析简单的 CSV 格式（备用方案）
        
        格式：
        productName,energyKcal,protein,fat,carbohydrate,sodium
        猪柳麦满分,308,16,16,24,781
        """
        lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
        if len(lines) < 2:
            return []
        
        # 假设第一行是表头
        header = [h.strip() for h in lines[0].split(',')]
        
        records = []
        for line in lines[1:]:
            values = self._parse_toon_csv_line(line, len(header))
            if len(values) == len(header):
                record = dict(zip(header, values))
                records.append(record)
        
        return records

    def _convert_toon_to_nutrition(self, record: dict[str, Any]) -> dict[str, Any]:
        """
        将 TOON 格式的营养记录转换为标准格式
        
        TOON 字段 -> 标准字段：
        - productName -> name
        - energyKcal -> calories
        - energyKj -> energy_kj
        - protein -> protein_g
        - fat -> fat_g
        - carbohydrate -> carbs_g
        - sodium -> sodium_mg
        - calcium -> calcium_mg
        """
        nutrition = {}
        
        # 热量（千卡）
        energy_kcal = record.get('energyKcal')
        if energy_kcal and energy_kcal != 'null':
            try:
                nutrition['calories'] = int(float(energy_kcal))
            except (ValueError, TypeError):
                pass
        
        # 能量（千焦）- 保存备用
        energy_kj = record.get('energyKj')
        if energy_kj and energy_kj != 'null':
            try:
                nutrition['energy_kj'] = int(float(energy_kj))
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
        
        # 糖（TOON 格式中没有单独的糖字段，用 carbohydrate 代替）
        # 钙
        calcium = record.get('calcium')
        if calcium and calcium != 'null':
            try:
                nutrition['calcium_mg'] = float(calcium)
            except (ValueError, TypeError):
                pass
        
        return nutrition

    def _find_matching_nutrition(self, product_name: str, nutrition_records: list[dict[str, Any]]) -> dict[str, Any] | None:
        """从营养记录列表中找到匹配的商品"""
        normalized_query = self._normalize_name(product_name)
        
        for record in nutrition_records:
            if not self._looks_like_nutrition_record(record):
                continue
                
            candidate_name = self._pick_name(record)
            if not candidate_name:
                continue
                
            normalized_candidate = self._normalize_name(candidate_name)
            
            # 匹配逻辑：完全匹配或包含关系
            if (normalized_query == normalized_candidate or 
                normalized_query in normalized_candidate or 
                normalized_candidate in normalized_query):
                return self._record_to_facts(record)
        
        return None

    def analyze_order(self, cart_items: list[OrderItem]) -> NutritionReport:
        if self.mcp_tool_client:
            try:
                return self._analyze_with_mcp(cart_items)
            except McdToolError as exc:
                local_report = self._analyze_with_local_catalog(cart_items)
                local_report.note = f"MCP 营养查询失败，已回退到本地营养库: {exc}"
                return local_report
        return self._analyze_with_local_catalog(cart_items)

    def _analyze_with_local_catalog(self, cart_items: list[OrderItem]) -> NutritionReport:
        items: list[NutritionLineItem] = []
        total = NutritionFacts()
        for item in cart_items:
            facts = self.catalog.find(item.product_name)
            items.append(
                NutritionLineItem(
                    product_name=item.product_name,
                    quantity=item.quantity,
                    nutrition=facts,
                    source="local_catalog",
                    note=None if facts else "本地营养库没有命中该商品",
                )
            )
            if facts:
                self._add_facts(total, facts, item.quantity)
        return NutritionReport(
            source="local_catalog",
            items=items,
            total=total,
            note="当前为本地营养库估算结果。",
            generated_at=datetime.now().isoformat(),
        )

    def _analyze_with_mcp(self, cart_items: list[OrderItem]) -> NutritionReport:
        if not self.mcp_tool_client:
            raise McdToolError("MCP 客户端不可用。")

        tool = self.mcp_tool_client.find_tool("list-nutrition-foods")
        if not tool:
            raise McdToolError("未发现 list-nutrition-foods 工具。")

        nutrition_index = self._fetch_mcp_nutrition_index(tool)
        items: list[NutritionLineItem] = []
        total = NutritionFacts()
        mcp_matched = 0
        local_matched = 0

        for item in cart_items:
            # 优先从 MCP 营养索引匹配
            matched_name, facts = self._match_mcp_nutrition(item.product_name, nutrition_index)
            
            # 如果 MCP 命中，使用 MCP 数据
            if facts:
                items.append(
                    NutritionLineItem(
                        product_name=item.product_name,
                        quantity=item.quantity,
                        nutrition=facts,
                        source="mcp",
                        note=f"MCP 命中: {matched_name}",
                    )
                )
                self._add_facts(total, facts, item.quantity)
                mcp_matched += 1
            else:
                # MCP 未命中时，使用本地营养库兜底
                local_facts = self.catalog.find(item.product_name)
                if local_facts:
                    items.append(
                        NutritionLineItem(
                            product_name=item.product_name,
                            quantity=item.quantity,
                            nutrition=local_facts,
                            source="local_fallback",
                            note="MCP未命中，使用本地营养库补充",
                        )
                    )
                    self._add_facts(total, local_facts, item.quantity)
                    local_matched += 1
                else:
                    # 本地库也没有，标记为未命中
                    items.append(
                        NutritionLineItem(
                            product_name=item.product_name,
                            quantity=item.quantity,
                            nutrition=None,
                            source="unmatched",
                            note="MCP和本地库均未命中该商品",
                        )
                    )

        return NutritionReport(
            source="mcp_with_local_fallback",
            items=items,
            total=total,
            note=f"营养结果：{mcp_matched}项来自MCP，{local_matched}项来自本地库补充。",
            generated_at=datetime.now().isoformat(),
            raw={"mcp_matched": mcp_matched, "local_matched": local_matched, "unmatched": len(cart_items) - mcp_matched - local_matched},
        )

    def _fetch_mcp_nutrition_index(self, tool: dict[str, Any]) -> list[dict[str, Any]]:
        schema = tool.get("inputSchema") or {}
        properties = schema.get("properties") or {}
        required = schema.get("required") or []

        candidate_args: list[dict[str, Any]] = []
        if not required:
            candidate_args.append({})

        for field_name in ("keyword", "query", "foodName", "productName", "name"):
            if field_name in properties:
                candidate_args.append({field_name: ""})
                candidate_args.append({field_name: "麦当劳"})

        seen: set[str] = set()
        for arguments in candidate_args:
            key = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            result = self.mcp_tool_client.call_tool("list-nutrition-foods", arguments)
            # ✅ 使用 structuredContent.data 解析
            parsed = self._parse_nutrition_index_from_text(result)
            if parsed:
                return parsed

        raise McdToolError("无法从 MCP 营养工具中提取结构化数据。")

    def _parse_nutrition_index_from_text(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        """
        从 TOON 格式的 MCP 响应中解析营养数据索引
        
        实际返回格式（来自 structuredContent.data）：
        [160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
          猪柳麦满分,null,1288,308,16,16,24,781,213
          猪柳蛋麦满分,null,1618,387,23,21,25,846,243
          ...
        
        Returns:
            营养数据字典列表
        """
        # ✅ 从 structuredContent.data 节点提取 TOON 数据
        text_content = self._extract_structured_content_data(result)
        
        if not text_content or not text_content.strip():
            return []
        
        # 使用新的 TOON 解析器
        nutrition_records = self._parse_toon_format(text_content)
        
        if nutrition_records:
            # 转换为兼容格式
            return [
                {
                    "name": record.get("productName", ""),
                    "nutritionDescription": record.get("nutritionDescription"),
                    "energyKj": record.get("energyKj"),
                    "energyKcal": record.get("energyKcal"),
                    "protein": record.get("protein"),
                    "fat": record.get("fat"),
                    "carbohydrate": record.get("carbohydrate"),
                    "sodium": record.get("sodium"),
                    "calcium": record.get("calcium"),
                }
                for record in nutrition_records
                if record.get("productName")
            ]
        
        return []


    @staticmethod
    def _looks_like_nutrition_record(record: dict[str, Any]) -> bool:
        keys = {key.lower() for key in record.keys()}
        nutrient_keys = {"calories", "protein", "fat", "carbs", "sodium", "sugar", "能量", "蛋白质", "脂肪", "碳水", "钠", "糖"}
        return bool(keys & nutrient_keys) and any(
            name_key in keys for name_key in {"name", "foodname", "productname", "mealname", "食品名称", "商品名称", "名称"}
        )

    def _match_mcp_nutrition(self, product_name: str, nutrition_index: list[dict[str, Any]]) -> tuple[str | None, NutritionFacts | None]:
        normalized_query = self._normalize_name(product_name)
        best_match: dict[str, Any] | None = None
        best_name: str | None = None

        for record in nutrition_index:
            candidate_name = self._pick_name(record)
            if not candidate_name:
                continue
            normalized_candidate = self._normalize_name(candidate_name)
            if normalized_query == normalized_candidate or normalized_query in normalized_candidate or normalized_candidate in normalized_query:
                best_match = record
                best_name = candidate_name
                break

        if not best_match:
            return None, None
        return best_name, self._record_to_facts(best_match)

    @staticmethod
    def _normalize_name(name: str) -> str:
        return "".join(name.lower().replace("（", "(").replace("）", ")").split())

    @staticmethod
    def _pick_name(record: dict[str, Any]) -> str | None:
        for key in ("name", "foodName", "productName", "mealName", "食品名称", "商品名称", "名称"):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _record_to_facts(self, record: dict[str, Any]) -> NutritionFacts:
        return NutritionFacts(
            calories=self._pick_number(record, "calories", "energy", "能量", "热量"),
            protein_g=self._pick_number(record, "protein_g", "protein", "蛋白质"),
            fat_g=self._pick_number(record, "fat_g", "fat", "脂肪"),
            carbs_g=self._pick_number(record, "carbs_g", "carbs", "carbohydrates", "碳水", "碳水化合物"),
            sodium_mg=self._pick_number(record, "sodium_mg", "sodium", "钠"),
            sugar_g=self._pick_number(record, "sugar_g", "sugar", "糖"),
        )

    @staticmethod
    def _pick_number(record: dict[str, Any], *keys: str) -> int | float | None:
        lowered = {str(key).lower(): value for key, value in record.items()}
        for key in keys:
            value = lowered.get(key.lower())
            if value is None:
                continue
            if isinstance(value, (int, float)):
                return value
            if isinstance(value, str):
                stripped = "".join(ch for ch in value if ch.isdigit() or ch in {".", "-"})
                if not stripped:
                    continue
                try:
                    number = float(stripped)
                except ValueError:
                    continue
                return int(number) if number.is_integer() else number
        return None

    @staticmethod
    def _add_facts(total: NutritionFacts, facts: NutritionFacts, quantity: int) -> None:
        if facts.calories is not None:
            total.calories = (total.calories or 0) + int(facts.calories * quantity)
        if facts.protein_g is not None:
            total.protein_g = (total.protein_g or 0) + facts.protein_g * quantity
        if facts.fat_g is not None:
            total.fat_g = (total.fat_g or 0) + facts.fat_g * quantity
        if facts.carbs_g is not None:
            total.carbs_g = (total.carbs_g or 0) + facts.carbs_g * quantity
        if facts.sodium_mg is not None:
            total.sodium_mg = (total.sodium_mg or 0) + facts.sodium_mg * quantity
        if facts.sugar_g is not None:
            total.sugar_g = (total.sugar_g or 0) + facts.sugar_g * quantity
