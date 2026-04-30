from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .mcd_mcp_client import McdMcpClient, McdToolError
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

        for item in cart_items:
            matched_name, facts = self._match_mcp_nutrition(item.product_name, nutrition_index)
            items.append(
                NutritionLineItem(
                    product_name=item.product_name,
                    quantity=item.quantity,
                    nutrition=facts,
                    source="mcp" if facts else "mcp_unmatched",
                    note=f"MCP 命中名称: {matched_name}" if matched_name else "MCP 未命中该商品，可能需要人工核对",
                )
            )
            if facts:
                self._add_facts(total, facts, item.quantity)

        return NutritionReport(
            source="mcp",
            items=items,
            total=total,
            note="营养结果优先来自麦当劳 MCP 营养工具。",
            generated_at=datetime.now().isoformat(),
            raw={"matched_count": sum(1 for item in items if item.nutrition)},
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
            parsed = self._extract_nutrition_dicts(result)
            if parsed:
                return parsed

        raise McdToolError("无法从 MCP 营养工具中提取结构化数据。")

    def _extract_nutrition_dicts(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = self.mcp_tool_client.extract_json_objects(result)
        flattened: list[dict[str, Any]] = []
        for candidate in candidates:
            if self._looks_like_nutrition_record(candidate):
                flattened.append(candidate)
            for value in candidate.values():
                if isinstance(value, list):
                    flattened.extend(item for item in value if isinstance(item, dict) and self._looks_like_nutrition_record(item))
        return flattened

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
