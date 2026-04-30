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
    """本地营养库"""
    
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
    """菜单推荐引擎"""
    
    def __init__(self, catalog: NutritionCatalog) -> None:
        self.catalog = catalog

    def _normalize_price(self, value: Any) -> int | float | Decimal | None:
        """规范化价格字段"""
        if value is None:
            return None
        if isinstance(value, (int, float, Decimal)):
            return value
        try:
            return Decimal(str(value))
        except (ValueError, TypeError):
            return None

    def enrich_and_rank(self, menu_items: list[dict], preference: UserPreference) -> list[CandidateItem]:
        """根据用户偏好对菜单进行排序"""
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
        """计算商品评分"""
        score = 0.0
        reasons: list[str] = []
        nutrition_pref = preference.nutrition
        lower_name = item.name.lower()

        # 口味偏好加分
        for liked in preference.taste_preferences:
            if liked.lower() in lower_name:
                score += 1.2
                reasons.append(f"符合口味偏好: {liked}")

        # 不喜欢食材减分
        for disliked in preference.disliked_ingredients:
            if disliked.lower() in lower_name:
                score -= 3.0
                reasons.append(f"命中不喜欢的食材: {disliked}")

        # 过敏原减分
        for allergen in preference.allergens:
            if allergen.lower() in lower_name:
                score -= 5.0
                reasons.append(f"可能命中过敏原关键词: {allergen}")

        # 营养评分
        if item.nutrition:
            nutrition_score, nutrition_reasons = self._score_nutrition(item.nutrition, nutrition_pref)
            score += nutrition_score
            reasons.extend(nutrition_reasons)
        else:
            reasons.append("暂无营养数据，按口味和菜单可用性推荐")

        # 餐型匹配
        if preference.meal_type and preference.meal_type.lower() in lower_name:
            score += 0.8
            reasons.append(f"更接近目标餐型: {preference.meal_type}")

        return score, reasons

    def _score_nutrition(self, facts: NutritionFacts, preference: NutritionPreference) -> tuple[float, list[str]]:
        """根据营养偏好评分"""
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
    """营养分析器 - 整合 MCP 和本地营养库"""
    
    def __init__(self, catalog: NutritionCatalog, mcp_tool_client: McdMcpClient | None = None) -> None:
        self.catalog = catalog
        self.mcp_tool_client = mcp_tool_client

    def query_nutrition(self, product_name: str) -> dict[str, Any] | None:
        """
        直接查询单个商品的营养成分
        
        优先级：
        1. 优先从 MCP 营养工具查询（麦当劳官方数据）
        2. MCP 查询失败或无匹配时，使用本地营养库兜底
        """
        # 优先从 MCP 查询营养数据
        if self.mcp_tool_client:
            try:
                # 使用 McdMcpClient 的 query_nutrition 方法
                mcp_result = self.mcp_tool_client.query_nutrition(product_name)
                if mcp_result:
                    return mcp_result
            except McdToolError as exc:
                logger.warning(f"MCP 营养查询失败: {exc}")
        
        # MCP 查询失败或无匹配时，使用本地营养库兜底
        local_nutrition = self.catalog.find(product_name)
        if local_nutrition:
            return {
                "product_name": product_name,
                "source": "local_catalog",
                "nutrition": local_nutrition.model_dump(),
                "note": "MCP 未返回数据，使用本地营养库"
            }
        
        return None

    def analyze_order(self, cart_items: list[OrderItem]) -> NutritionReport:
        """分析购物车中所有商品的整体营养"""
        if self.mcp_tool_client:
            try:
                return self._analyze_with_mcp(cart_items)
            except McdToolError as exc:
                logger.warning(f"MCP 营养分析失败: {exc}")
                local_report = self._analyze_with_local_catalog(cart_items)
                local_report.note = f"MCP 营养查询失败，已回退到本地营养库: {exc}"
                return local_report
        return self._analyze_with_local_catalog(cart_items)

    def _analyze_with_local_catalog(self, cart_items: list[OrderItem]) -> NutritionReport:
        """使用本地营养库分析"""
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
                    note=None if facts else "本地营养库没有命中该商品"
                )
            )
            if facts:
                self._add_facts(total, facts, item.quantity)
        
        return NutritionReport(
            source="local_catalog",
            items=items,
            total=total,
            note="当前为本地营养库估算结果。",
            generated_at=datetime.now().isoformat()
        )

    def _analyze_with_mcp(self, cart_items: list[OrderItem]) -> NutritionReport:
        """使用 MCP 营养工具分析"""
        if not self.mcp_tool_client:
            raise McdToolError("MCP 客户端不可用。")

        # 获取所有营养数据
        nutrition_index = self.mcp_tool_client.call_nutrition_tool("")
        
        if not nutrition_index:
            raise McdToolError("无法获取 MCP 营养数据")

        items: list[NutritionLineItem] = []
        total = NutritionFacts()
        mcp_matched = 0
        local_matched = 0

        for item in cart_items:
            # 优先从 MCP 营养索引匹配
            nutrition_data = self.mcp_tool_client._find_matching_nutrition(item.product_name, nutrition_index)
            
            if nutrition_data:
                # MCP 命中
                facts = NutritionFacts(
                    calories=nutrition_data.get('calories'),
                    protein_g=nutrition_data.get('protein_g'),
                    fat_g=nutrition_data.get('fat_g'),
                    carbs_g=nutrition_data.get('carbs_g'),
                    sodium_mg=nutrition_data.get('sodium_mg')
                )
                items.append(
                    NutritionLineItem(
                        product_name=item.product_name,
                        quantity=item.quantity,
                        nutrition=facts,
                        source="mcp",
                        note=f"MCP 命中: {item.product_name}"
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
                            note="MCP 未命中，使用本地营养库补充"
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
                            note="MCP 和本地库均未命中该商品"
                        )
                    )

        return NutritionReport(
            source="mcp_with_local_fallback",
            items=items,
            total=total,
            note=f"营养结果：{mcp_matched} 项来自 MCP，{local_matched} 项来自本地库补充。",
            generated_at=datetime.now().isoformat(),
            raw={
                "mcp_matched": mcp_matched,
                "local_matched": local_matched,
                "unmatched": len(cart_items) - mcp_matched - local_matched
            }
        )

    @staticmethod
    def _add_facts(total: NutritionFacts, facts: NutritionFacts, quantity: int) -> None:
        """累加营养数据"""
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
