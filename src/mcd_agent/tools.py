from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from langchain.tools import StructuredTool
from pydantic import BaseModel, Field

from .config import Settings
from .mcd_mcp_client import McdMcpClient, McdToolError
from .models import (
    AgentSessionState,
    CartSnapshot,
    DeliveryAddress,
    NutritionReport,
    OrderDraft,
    OrderItem,
    StoreProfile,
    UserPreference,
)
from .nutrition import NutritionAnalyzer, RecommendationEngine

logger = logging.getLogger(__name__)


class UpdatePreferenceInput(BaseModel):
    taste_preferences: list[str] = Field(default_factory=list)
    disliked_ingredients: list[str] = Field(default_factory=list)
    allergens: list[str] = Field(default_factory=list)
    meal_type: Optional[str] = None
    order_type: Optional[int] = None
    notes: Optional[str] = None
    max_calories: Optional[int] = None
    min_protein_g: Optional[float] = None
    max_fat_g: Optional[float] = None
    max_sodium_mg: Optional[float] = None
    max_sugar_g: Optional[float] = None
    dietary_focus: Optional[str] = None


class QueryAddressesInput(BaseModel):
    address_id: Optional[str] = None


class CreateAddressInput(BaseModel):
    full_name: str
    phone: str
    city_name: str
    city_code: Optional[str] = None
    address: str
    detail: str
    latitude: float = 0
    longitude: float = 0
    channel: str = "03"
    default_address: int = 0
    gender: int = 0


class ListNearbyStoresInput(BaseModel):
    address_id: Optional[str] = None
    order_type: Optional[int] = None
    keyword: Optional[str] = None
    distance: Optional[int] = None


class SelectStoreInput(BaseModel):
    store_code: Optional[str] = None
    prefer_nearest: bool = False


class FetchMenuInput(BaseModel):
    store_code: Optional[str] = None
    be_code: Optional[str] = None
    order_type: Optional[int] = None
    day_part_code: Optional[str] = None


class CartItemInput(BaseModel):
    product_code: str
    product_name: str
    quantity: int = 1


class SyncCartInput(BaseModel):
    items: list[CartItemInput]
    replace_cart: bool = True
    data_source: int = 1


class UpdateOrderOptionsInput(BaseModel):
    remark: Optional[str] = None
    tableware_code: Optional[str] = None
    eat_type_code: Optional[str] = None
    pickup_time_code: Optional[str] = None
    expect_delivery_time_code: Optional[str] = None
    expect_delivery_date_code: Optional[str] = None
    address_id: Optional[str] = None


class EmptyInput(BaseModel):
    pass


class SubmitOrderInput(BaseModel):
    confirm: bool


class DeleteAddressInput(BaseModel):
    address_id: str


def _parse_address(raw: dict[str, Any]) -> DeliveryAddress:
    full_address = raw.get("displayFullText") or raw.get("fullAddress")
    return DeliveryAddress(
        id=raw.get("id") or raw.get("addressId"),
        full_name=raw.get("fullName") or raw.get("contactName") or "",
        phone=raw.get("phone") or "",
        city_code=raw.get("cityCode") or "",
        city_name=raw.get("cityName"),
        address=raw.get("address") or full_address,
        detail=raw.get("detail") or raw.get("addressDetail") or full_address or "",
        latitude=float(raw.get("latitude") or 0),
        longitude=float(raw.get("longitude") or 0),
        channel=raw.get("channel") or "03",
        default_address=int(raw.get("defaultAddress") or 0),
        gender=int(raw.get("gender") or 0),
        display_full_text=full_address,
        raw=raw,
    )


def _parse_store(raw: dict[str, Any]) -> StoreProfile:
    source = raw.get("mdsStore") if isinstance(raw.get("mdsStore"), dict) else raw
    return StoreProfile(
        code=str(source.get("code") or source.get("storeCode") or ""),
        name=source.get("name") or source.get("shortName") or source.get("storeName"),
        be_code=source.get("beCode"),
        be_type=source.get("beType"),
        city_code=source.get("cityCode"),
        city_name=source.get("cityName"),
        address=source.get("address") or source.get("fullAddress"),
        latitude=float(source.get("latitude")) if source.get("latitude") is not None else None,
        longitude=float(source.get("longitude")) if source.get("longitude") is not None else None,
        distance=int(source.get("distance")) if source.get("distance") is not None else None,
        distance_text=source.get("distanceText"),
        duration=int(source.get("duration")) if source.get("duration") is not None else None,
        estimated_delivery_time=source.get("estimatedDeliveryTime"),
        delivery_time=source.get("deliveryTime"),
        business_status=int(source.get("businessStatus")) if source.get("businessStatus") is not None else None,
        dayparts=source.get("dayparts") or [],
        tags=source.get("tags") or [],
        raw=source,
    )


def _default_daypart(store: StoreProfile | None) -> str | None:
    if not store:
        return None
    for daypart in store.dayparts:
        if daypart.get("daypartFlag") is True:
            return str(daypart.get("daypartCode"))
    if store.dayparts:
        return str(store.dayparts[0].get("daypartCode"))
    return None


def _extract_cart_payload(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data")
    if isinstance(data, dict):
        if "cartDetail" in data and isinstance(data["cartDetail"], dict):
            return data["cartDetail"]
        return data
    if "cartDetail" in result and isinstance(result["cartDetail"], dict):
        return result["cartDetail"]
    return {}


def _cart_snapshot_from_payload(payload: dict[str, Any], *, store_code: str | None = None, be_code: str | None = None) -> CartSnapshot:
    if "productList" in payload or "price" in payload:
        return CartSnapshot(
            store_code=store_code,
            be_code=be_code,
            order_type=payload.get("orderType"),
            day_part_code=payload.get("dayPartCode"),
            cart_type=1,
            total_price=payload.get("originalPrice"),
            product_total_price=payload.get("productPrice"),
            discount_amount=payload.get("discount"),
            delivery_price=payload.get("deliveryOriginalPrice"),
            real_total_price=payload.get("price"),
            real_delivery_price=payload.get("deliveryPrice"),
            submit=None,
            products=payload.get("productList") or [],
            promotions=[],
            tips={},
            raw=payload,
        )
    return CartSnapshot(
        store_code=payload.get("storeCode") or store_code,
        be_code=payload.get("beCode") or be_code,
        order_type=payload.get("orderType"),
        day_part_code=str(payload.get("daypartCode")) if payload.get("daypartCode") is not None else None,
        cart_type=payload.get("cartType") or 1,
        total_price=payload.get("totalPrice"),
        product_total_price=payload.get("productTotalPrice") or payload.get("realProductTotalPrice"),
        discount_amount=payload.get("discountAmount"),
        delivery_price=payload.get("deliveryPrice"),
        real_total_price=payload.get("realTotalPrice"),
        real_delivery_price=payload.get("realDeliveryPrice"),
        submit=payload.get("submit"),
        products=payload.get("products") or [],
        promotions=payload.get("promotions") or [],
        tips=payload.get("tips") or {},
        raw=payload,
    )


def _products_to_order_items(products: list[dict[str, Any]]) -> list[OrderItem]:
    items: list[OrderItem] = []
    for index, product in enumerate(products):
        items.append(
            OrderItem(
                product_code=str(product.get("code") or product.get("productCode") or ""),
                product_name=product.get("name") or product.get("productName") or "",
                quantity=int(product.get("quantity") or 1),
                real_subtotal=product.get("realSubtotal") or product.get("subTotalPrice"),
                sequence=int(product.get("sequence") or index + 1),
                product_type=str(product.get("productType") or "single"),
                unique_key=product.get("uniqueKey"),
            )
        )
    return items


def _cents_to_yuan(value: int | None) -> str | None:
    if value is None:
        return None
    return f"{value / 100:.2f}"


def _summarize_store(store: StoreProfile) -> dict[str, Any]:
    return {
        "store_code": store.code,
        "be_code": store.be_code,
        "name": store.name,
        "distance": store.distance,
        "distance_text": store.distance_text,
        "estimated_delivery_time": store.estimated_delivery_time,
        "business_status": store.business_status,
        "dayparts": store.dayparts,
    }


def _summarize_address(address: DeliveryAddress) -> dict[str, Any]:
    return {
        "address_id": address.id,
        "full_name": address.full_name,
        "phone": address.phone,
        "city_code": address.city_code,
        "detail": address.detail,
        "display_full_text": address.display_full_text,
        "latitude": address.latitude,
        "longitude": address.longitude,
        "default_address": address.default_address,
    }


def _nutrition_report_to_dict(report: NutritionReport) -> dict[str, Any]:
    return {
        "source": report.source,
        "note": report.note,
        "generated_at": report.generated_at,
        "total": report.total.model_dump(),
        "items": [
            {
                "product_name": item.product_name,
                "quantity": item.quantity,
                "nutrition": item.nutrition.model_dump() if item.nutrition else None,
                "source": item.source,
                "note": item.note,
            }
            for item in report.items
        ],
    }


def build_tools(
    *,
    settings: Settings,
    session_state: AgentSessionState,
    client: McdMcpClient,
    recommender: RecommendationEngine,
    nutrition_analyzer: NutritionAnalyzer,
) -> list[StructuredTool]:
    def update_user_preferences(**kwargs: Any) -> str:
        pref = UserPreference.model_validate(
            {
                **session_state.preference.model_dump(),
                "taste_preferences": kwargs.get("taste_preferences") or session_state.preference.taste_preferences,
                "disliked_ingredients": kwargs.get("disliked_ingredients") or session_state.preference.disliked_ingredients,
                "allergens": kwargs.get("allergens") or session_state.preference.allergens,
                "meal_type": kwargs.get("meal_type") or session_state.preference.meal_type,
                "order_type": kwargs.get("order_type") or session_state.preference.order_type,
                "notes": kwargs.get("notes") or session_state.preference.notes,
                "nutrition": {
                    **session_state.preference.nutrition.model_dump(),
                    "max_calories": kwargs.get("max_calories", session_state.preference.nutrition.max_calories),
                    "min_protein_g": kwargs.get("min_protein_g", session_state.preference.nutrition.min_protein_g),
                    "max_fat_g": kwargs.get("max_fat_g", session_state.preference.nutrition.max_fat_g),
                    "max_sodium_mg": kwargs.get("max_sodium_mg", session_state.preference.nutrition.max_sodium_mg),
                    "max_sugar_g": kwargs.get("max_sugar_g", session_state.preference.nutrition.max_sugar_g),
                    "dietary_focus": kwargs.get("dietary_focus", session_state.preference.nutrition.dietary_focus),
                },
            }
        )
        session_state.preference = pref
        if pref.order_type is not None:
            session_state.order_draft.order_type = pref.order_type
        logger.info("Updated preference for session=%s", session_state.session_id)
        return "用户偏好和营养目标已更新。"

    def query_addresses(address_id: str | None = None) -> str:
        result = client.get_addresses(address_id)
        data = result.get("data") or []
        if isinstance(data, dict):
            data = [data]
        addresses = [_parse_address(item) for item in data if isinstance(item, dict)]
        session_state.addresses = addresses
        if address_id and addresses:
            session_state.selected_address = addresses[0]
            session_state.order_draft.address_id = addresses[0].id
        summary = [_summarize_address(address) for address in addresses]
        return json.dumps(summary, ensure_ascii=False, indent=2)

    def create_address(
        full_name: str,
        phone: str,
        city_name: str,
        detail: str,
        address: str,
        latitude: float = 0,
        longitude: float = 0,
        city_code: str | None = None,
        channel: str = "03",
        default_address: int = 0,
        gender: int = 0,
    ) -> str:
        payload = {
            "fullName": full_name,
            "contactName": full_name,
            "phone": phone,
            "cityCode": city_code,
            "cityName": city_name,
            "address": address,
            "detail": detail,
            "addressDetail": detail,
            "city": city_name,
            "latitude": latitude,
            "longitude": longitude,
            "channel": channel,
            "defaultAddress": default_address,
            "gender": gender,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        result = client.create_address(payload)
        created_data = result.get("data") or {}
        address_id = created_data.get("id") if isinstance(created_data, dict) else None
        if not address_id:
            raise McdToolError("新增地址成功，但未返回 addressId。")

        address_payload = created_data if isinstance(created_data, dict) and created_data.get("detail") else None
        if not address_payload:
            try:
                detail_result = client.get_address_detail(address_id)
                address_payload = detail_result.get("data")
            except McdToolError:
                address_payload = payload | {"id": address_id}

        address = _parse_address(address_payload or (payload | {"id": address_id}))
        session_state.selected_address = address
        session_state.order_draft.address_id = address.id

        addresses_result = client.get_addresses()
        all_addresses = addresses_result.get("data") or []
        session_state.addresses = [_parse_address(item) for item in all_addresses if isinstance(item, dict)]

        stores_result = client.get_stores_vicinity(
            address_id=address_id,
            city_code=address.city_code,
            latitude=address.latitude,
            longitude=address.longitude,
            order_type=session_state.preference.order_type or settings.default_order_type,
        )
        stores_data = stores_result.get("data") or []
        if isinstance(stores_data, dict):
            stores_data = [stores_data]
        nearby_stores = [_parse_store(item) for item in stores_data if isinstance(item, dict)]
        nearby_stores.sort(key=lambda x: x.distance or 10**9)
        session_state.nearby_stores = nearby_stores
        session_state.selected_store = None
        session_state.order_draft.store_code = None
        session_state.order_draft.be_code = None
        session_state.order_draft.day_part_code = None

        response = {
            "message": "地址已新增。请先选择最近门店后再进行点餐操作。",
            "selected_address": _summarize_address(address),
            "nearby_stores": [_summarize_store(store) for store in nearby_stores[:5]],
        }
        return json.dumps(response, ensure_ascii=False, indent=2)

    def delete_address(address_id: str) -> str:
        try:
            client.delete_address(address_id)
        except McdToolError as exc:
            return f"删除地址当前不可用: {exc}"

        session_state.addresses = [address for address in session_state.addresses if address.id != address_id]
        if session_state.selected_address and session_state.selected_address.id == address_id:
            session_state.selected_address = None
            session_state.nearby_stores = []
            session_state.selected_store = None
            session_state.order_draft.address_id = None
            session_state.order_draft.store_code = None
            session_state.order_draft.be_code = None
            session_state.order_draft.day_part_code = None

        return json.dumps(
            {
                "message": "地址已删除。",
                "deleted_address_id": address_id,
                "remaining_address_count": len(session_state.addresses),
            },
            ensure_ascii=False,
            indent=2,
        )

    def list_nearby_stores(
        address_id: str | None = None,
        order_type: int | None = None,
        keyword: str | None = None,
        distance: int | None = None,
    ) -> str:
        target_address = session_state.selected_address
        if address_id:
            detail_result = client.get_address_detail(address_id)
            target_address = _parse_address(detail_result.get("data") or {})
            session_state.selected_address = target_address
            session_state.order_draft.address_id = target_address.id
        if not target_address or not target_address.id:
            return "请先查询或新增地址，再查询附近门店。"

        stores_result = client.get_stores_vicinity(
            address_id=target_address.id,
            city_code=target_address.city_code,
            latitude=target_address.latitude,
            longitude=target_address.longitude,
            order_type=order_type or session_state.preference.order_type or settings.default_order_type,
            keyword=keyword,
            distance=distance,
        )
        stores_data = stores_result.get("data") or []
        if isinstance(stores_data, dict):
            stores_data = [stores_data]
        nearby_stores = [_parse_store(item) for item in stores_data if isinstance(item, dict)]
        nearby_stores.sort(key=lambda x: x.distance or 10**9)
        session_state.nearby_stores = nearby_stores
        return json.dumps([_summarize_store(store) for store in nearby_stores[:10]], ensure_ascii=False, indent=2)

    def select_store(store_code: str | None = None, prefer_nearest: bool = False) -> str:
        if not session_state.nearby_stores:
            return "当前没有可选门店。请先新增地址或查询附近门店。"

        chosen: StoreProfile | None = None
        if prefer_nearest:
            chosen = sorted(session_state.nearby_stores, key=lambda x: x.distance or 10**9)[0]
        elif store_code:
            chosen = next((store for store in session_state.nearby_stores if store.code == store_code), None)
        else:
            return "请传入 store_code，或设置 prefer_nearest=true 选择最近门店。"

        if not chosen:
            return "没有找到指定门店，请先查看附近门店列表。"

        session_state.selected_store = chosen
        session_state.order_draft.store_code = chosen.code
        session_state.order_draft.be_code = chosen.be_code
        session_state.order_draft.order_type = session_state.preference.order_type or settings.default_order_type
        session_state.order_draft.day_part_code = _default_daypart(chosen)

        response = {
            "message": "门店已选定，当前可以继续拉取菜单或同步购物车。",
            "selected_store": _summarize_store(chosen),
            "selected_day_part_code": session_state.order_draft.day_part_code,
        }
        return json.dumps(response, ensure_ascii=False, indent=2)

    def fetch_menu_and_rank(
        store_code: str | None = None,
        be_code: str | None = None,
        order_type: int | None = None,
        day_part_code: str | None = None,
    ) -> str:
        selected_store = session_state.selected_store
        if not selected_store:
            return "请先根据地址选择门店，之后才可以查询菜单。"

        store_code = store_code or selected_store.code
        be_code = be_code or selected_store.be_code
        order_type = order_type or session_state.order_draft.order_type or session_state.preference.order_type or settings.default_order_type
        day_part_code = day_part_code or session_state.order_draft.day_part_code or _default_daypart(selected_store)
        if not all([store_code, be_code]):
            return "门店上下文不完整，缺少 store_code / be_code。"

        try:
            menu_response = client.get_menu(
                store_code=store_code,
                be_code=be_code,
                order_type=order_type,
                day_part_code=day_part_code,
            )
        except McdToolError as exc:
            logger.warning("Falling back to local nutrition catalog for session=%s: %s", session_state.session_id, exc)
            flattened = [
                {
                    "categoryName": "本地营养样例",
                    "name": name,
                    "code": f"local-{index+1}",
                    "price": None,
                }
                for index, name in enumerate(recommender.catalog.items.keys())
            ]
            ranked = recommender.enrich_and_rank(flattened, session_state.preference)
            session_state.last_menu_context = {
                "store_code": store_code,
                "be_code": be_code,
                "order_type": order_type,
                "day_part_code": day_part_code,
                "fetched_at": datetime.now().isoformat(),
                "fallback": "local_nutrition_catalog",
            }
            session_state.candidate_items = ranked[:10]
            summary = {
                "message": f"当前无法拉取真实麦当劳菜单，已降级为本地营养样例推荐: {exc}",
                "recommendations": [
                    {
                        "name": item.name,
                        "code": item.code,
                        "price": item.price,
                        "score": round(item.score, 2),
                        "reasons": item.reasons[:3],
                        "nutrition": item.nutrition.model_dump() if item.nutrition else None,
                    }
                    for item in ranked[:5]
                ],
            }
            return json.dumps(summary, ensure_ascii=False, indent=2)

        menu = (menu_response.get("data") or {}).get("menu") or []
        flattened: list[dict[str, Any]] = []
        for category in menu:
            category_name = category.get("categoryName")
            for product in category.get("products", []) or []:
                flattened.append(
                    {
                        "categoryName": category_name,
                        "name": product.get("name") or product.get("productName"),
                        "code": product.get("code") or product.get("productCode"),
                        "price": product.get("price") or product.get("realPrice"),
                        **product,
                    }
                )

        ranked = recommender.enrich_and_rank(flattened, session_state.preference)
        session_state.last_menu_context = {
            "store_code": store_code,
            "be_code": be_code,
            "order_type": order_type,
            "day_part_code": day_part_code,
            "fetched_at": datetime.now().isoformat(),
        }
        session_state.candidate_items = ranked[:10]
        session_state.order_draft.store_code = store_code
        session_state.order_draft.be_code = be_code
        session_state.order_draft.order_type = order_type
        session_state.order_draft.day_part_code = day_part_code

        summary = [
            {
                "name": item.name,
                "code": item.code,
                "price": item.price,
                "score": round(item.score, 2),
                "reasons": item.reasons[:3],
                "nutrition": item.nutrition.model_dump() if item.nutrition else None,
            }
            for item in ranked[:5]
        ]
        return json.dumps(summary, ensure_ascii=False, indent=2)

    def sync_cart(items: list[CartItemInput], replace_cart: bool = True, data_source: int = 1) -> str:
        del data_source
        draft = session_state.order_draft
        if not draft.store_code or not draft.be_code or draft.order_type is None:
            return "请先完成地址和门店选择，并拉取菜单后再同步购物车。"

        merged_items = list(session_state.order_draft.cart_items if not replace_cart else [])
        merged_items.extend(
            [
                OrderItem(
                    product_code=item.product_code,
                    product_name=item.product_name,
                    quantity=item.quantity,
                    sequence=len(merged_items) + index + 1,
                )
                for index, item in enumerate(items)
            ]
        )
        products_payload = [
            {"productCode": item.product_code, "productName": item.product_name, "quantity": item.quantity}
            for item in merged_items
        ]
        price_result = client.calculate_price(
            store_code=draft.store_code,
            be_code=draft.be_code,
            order_type=draft.order_type,
            items=products_payload,
        )
        cart_payload = price_result.get("data") or {}
        session_state.cart_snapshot = _cart_snapshot_from_payload(cart_payload, store_code=draft.store_code, be_code=draft.be_code)
        session_state.order_draft.cart_items = [
            OrderItem(
                product_code=item.get("productCode") or "",
                product_name=item.get("productName") or "",
                quantity=int(item.get("quantity") or 1),
                real_subtotal=item.get("subtotal"),
                sequence=index + 1,
            )
            for index, item in enumerate(cart_payload.get("productList") or products_payload)
        ]
        session_state.nutrition_report = None
        return json.dumps(
            {
                "message": "商品价格已通过 MCP 计算并同步到当前订单草稿。",
                "cart_summary": {
                    "store_code": session_state.cart_snapshot.store_code,
                    "real_total_price": session_state.cart_snapshot.real_total_price,
                    "real_delivery_price": session_state.cart_snapshot.real_delivery_price,
                    "submit": session_state.cart_snapshot.submit,
                    "product_count": len(session_state.cart_snapshot.products),
                },
            },
            ensure_ascii=False,
            indent=2,
        )

    def get_cart_detail() -> str:
        draft = session_state.order_draft
        if not draft.store_code or not draft.be_code or draft.order_type is None:
            return "请先完成门店选择后再查询购物车明细。"
        if not draft.cart_items:
            return "购物车为空，无法查询价格明细。"
        cart_result = client.calculate_price(
            store_code=draft.store_code,
            be_code=draft.be_code,
            order_type=draft.order_type,
            items=[
                {"productCode": item.product_code, "productName": item.product_name, "quantity": item.quantity}
                for item in draft.cart_items
            ],
        )
        cart_payload = cart_result.get("data") or {}
        session_state.cart_snapshot = _cart_snapshot_from_payload(cart_payload, store_code=draft.store_code, be_code=draft.be_code)
        if cart_payload.get("productList"):
            session_state.order_draft.cart_items = _products_to_order_items(cart_payload["productList"])

        summary = {
            "store_code": session_state.cart_snapshot.store_code,
            "be_code": session_state.cart_snapshot.be_code,
            "order_type": session_state.cart_snapshot.order_type,
            "day_part_code": session_state.cart_snapshot.day_part_code,
            "total_price": session_state.cart_snapshot.total_price,
            "real_total_price": session_state.cart_snapshot.real_total_price,
            "delivery_price": session_state.cart_snapshot.delivery_price,
            "real_delivery_price": session_state.cart_snapshot.real_delivery_price,
            "discount_amount": session_state.cart_snapshot.discount_amount,
            "submit": session_state.cart_snapshot.submit,
            "products": session_state.cart_snapshot.products,
            "promotions": session_state.cart_snapshot.promotions,
        }
        return json.dumps(summary, ensure_ascii=False, indent=2)

    def update_order_options(
        remark: str | None = None,
        tableware_code: str | None = None,
        eat_type_code: str | None = None,
        pickup_time_code: str | None = None,
        expect_delivery_time_code: str | None = None,
        expect_delivery_date_code: str | None = None,
        address_id: str | None = None,
    ) -> str:
        payload = {
            **session_state.order_draft.model_dump(),
            "remark": remark if remark is not None else session_state.order_draft.remark,
            "tableware_code": tableware_code if tableware_code is not None else session_state.order_draft.tableware_code,
            "eat_type_code": eat_type_code if eat_type_code is not None else session_state.order_draft.eat_type_code,
            "pickup_time_code": pickup_time_code if pickup_time_code is not None else session_state.order_draft.pickup_time_code,
            "expect_delivery_time_code": expect_delivery_time_code if expect_delivery_time_code is not None else session_state.order_draft.expect_delivery_time_code,
            "expect_delivery_date_code": expect_delivery_date_code if expect_delivery_date_code is not None else session_state.order_draft.expect_delivery_date_code,
            "address_id": address_id if address_id is not None else session_state.order_draft.address_id,
        }
        session_state.order_draft = OrderDraft.model_validate(payload)
        session_state.confirmed = False
        return "订单附加选项已更新。"

    def prepare_order_confirmation() -> str:
        cart_json = get_cart_detail()
        if cart_json.startswith("请先"):
            return cart_json
        if not session_state.order_draft.cart_items:
            return "购物车为空，无法进入下单确认。"
        if not session_state.selected_address:
            return "当前没有配送地址，无法进入下单确认。"
        if not session_state.selected_store:
            return "当前没有选定门店，无法进入下单确认。"

        report = nutrition_analyzer.analyze_order(session_state.order_draft.cart_items)
        session_state.nutrition_report = report

        confirmation = {
            "message": "以下为下单前确认信息。请确认门店、地址、购物车金额和营养成分。",
            "address": _summarize_address(session_state.selected_address),
            "store": _summarize_store(session_state.selected_store),
            "cart": {
                "real_total_price_cent": session_state.cart_snapshot.real_total_price,
                "real_delivery_price_cent": session_state.cart_snapshot.real_delivery_price,
                "discount_amount_cent": session_state.cart_snapshot.discount_amount,
                "products": session_state.cart_snapshot.products,
                "promotions": session_state.cart_snapshot.promotions,
            },
            "nutrition": _nutrition_report_to_dict(report),
        }
        return json.dumps(confirmation, ensure_ascii=False, indent=2)

    def submit_confirmed_order(confirm: bool) -> str:
        if not confirm:
            return "用户尚未确认下单，已取消提交。"

        if not session_state.selected_address or not session_state.selected_store:
            return "请先完成地址和门店选择。"
        if not session_state.order_draft.cart_items:
            return "购物车为空，无法下单。"
        if session_state.nutrition_report is None:
            confirmation = prepare_order_confirmation()
            if confirmation.startswith("请先") or confirmation.startswith("购物车为空"):
                return confirmation

        draft = session_state.order_draft
        real_total_amount = draft.real_total_amount or _cents_to_yuan(session_state.cart_snapshot.real_total_price)
        real_delivery_price = draft.real_delivery_price or _cents_to_yuan(session_state.cart_snapshot.real_delivery_price)

        payload = {
            "orderType": draft.order_type,
            "storeCode": draft.store_code,
            "beCode": draft.be_code,
            "addressId": draft.address_id,
            "items": [
                {
                    "productCode": item.product_code,
                    "quantity": item.quantity,
                }
                for item in draft.cart_items
            ],
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        payload["items"] = [{k: v for k, v in item.items() if v is not None} for item in payload["items"]]

        if settings.dry_run_orders:
            session_state.confirmed = True
            return json.dumps(
                {
                    "message": "当前处于 DRY_RUN_ORDERS=true 模式，已完成模拟下单。",
                    "payload_preview": payload,
                    "price_summary": {
                        "real_total_amount": real_total_amount,
                        "real_delivery_price": real_delivery_price,
                    },
                    "nutrition": _nutrition_report_to_dict(session_state.nutrition_report) if session_state.nutrition_report else None,
                },
                ensure_ascii=False,
                indent=2,
            )

        try:
            result = client.create_order(payload)
        except McdToolError as exc:
            return f"下单失败: {exc}"
        session_state.confirmed = True
        return json.dumps(result, ensure_ascii=False, indent=2)

    return [
        StructuredTool.from_function(
            func=update_user_preferences,
            name="update_user_preferences",
            description="记录或更新用户口味偏好、忌口、过敏信息和营养目标。",
            args_schema=UpdatePreferenceInput,
        ),
        StructuredTool.from_function(
            func=query_addresses,
            name="query_addresses",
            description="查询用户已有配送地址；如提供 address_id，会同步设置当前地址。",
            args_schema=QueryAddressesInput,
        ),
        StructuredTool.from_function(
            func=create_address,
            name="create_address",
            description="新增配送地址，并在成功后自动查询附近门店。新增地址后必须先选择门店，才可以继续点餐。",
            args_schema=CreateAddressInput,
        ),
        StructuredTool.from_function(
            func=delete_address,
            name="delete_address",
            description="删除一个配送地址；如果删除的是当前已选地址，会一并清空当前门店上下文。",
            args_schema=DeleteAddressInput,
        ),
        StructuredTool.from_function(
            func=list_nearby_stores,
            name="list_nearby_stores",
            description="根据当前地址查询附近麦当劳门店列表，用于让用户选择最近门店。",
            args_schema=ListNearbyStoresInput,
        ),
        StructuredTool.from_function(
            func=select_store,
            name="select_store",
            description="从附近门店中选择本次点餐门店。支持按 store_code 指定，或使用 prefer_nearest=true 选择最近门店。",
            args_schema=SelectStoreInput,
        ),
        StructuredTool.from_function(
            func=fetch_menu_and_rank,
            name="fetch_menu_and_rank",
            description="在当前选定门店上下文中查询可售菜单，并结合用户营养偏好排序推荐。",
            args_schema=FetchMenuInput,
        ),
        StructuredTool.from_function(
            func=sync_cart,
            name="sync_cart",
            description="将用户选择的商品同步到购物车。默认会先清空购物车，再按当前商品列表重建。",
            args_schema=SyncCartInput,
        ),
        StructuredTool.from_function(
            func=get_cart_detail,
            name="get_cart_detail",
            description="查询当前门店和地址上下文下的购物车明细、金额和促销信息。",
            args_schema=EmptyInput,
        ),
        StructuredTool.from_function(
            func=update_order_options,
            name="update_order_options",
            description="更新订单附加选项，如备注、餐具、配送时间或取餐方式。",
            args_schema=UpdateOrderOptionsInput,
        ),
        StructuredTool.from_function(
            func=prepare_order_confirmation,
            name="prepare_order_confirmation",
            description="进入下单确认环节：刷新购物车明细，并调用麦当劳 MCP 营养能力给出本次点餐的营养成分汇总。",
            args_schema=EmptyInput,
        ),
        StructuredTool.from_function(
            func=submit_confirmed_order,
            name="submit_confirmed_order",
            description="仅在用户明确确认后提交订单。提交前会确保已经生成下单确认信息和营养汇总。",
            args_schema=SubmitOrderInput,
        ),
    ]
