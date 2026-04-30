from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional, Union

from pydantic import BaseModel, Field


class NutritionFacts(BaseModel):
    calories: Optional[int] = None
    protein_g: Optional[float] = None
    fat_g: Optional[float] = None
    carbs_g: Optional[float] = None
    sodium_mg: Optional[float] = None
    sugar_g: Optional[float] = None


class NutritionPreference(BaseModel):
    max_calories: Optional[int] = None
    min_protein_g: Optional[float] = None
    max_fat_g: Optional[float] = None
    max_sodium_mg: Optional[float] = None
    max_sugar_g: Optional[float] = None
    dietary_focus: Optional[str] = None


class UserPreference(BaseModel):
    taste_preferences: list[str] = Field(default_factory=list)
    disliked_ingredients: list[str] = Field(default_factory=list)
    allergens: list[str] = Field(default_factory=list)
    meal_type: Optional[str] = None
    order_type: Optional[int] = None
    notes: Optional[str] = None
    nutrition: NutritionPreference = Field(default_factory=NutritionPreference)


class CandidateItem(BaseModel):
    code: str
    name: str
    category: Optional[str] = None
    price: Union[int, float, Decimal, None] = None
    nutrition: Optional[NutritionFacts] = None
    reasons: list[str] = Field(default_factory=list)
    score: float = 0.0
    raw: dict[str, Any] = Field(default_factory=dict)


class DeliveryAddress(BaseModel):
    id: Optional[str] = None
    full_name: str
    phone: str
    city_code: str
    city_name: Optional[str] = None
    address: Optional[str] = None
    detail: str
    latitude: float
    longitude: float
    channel: str = "03"
    default_address: int = 0
    gender: int = 0
    display_full_text: Optional[str] = None
    raw: dict[str, Any] = Field(default_factory=dict)


class StoreProfile(BaseModel):
    code: str
    name: Optional[str] = None
    be_code: Optional[str] = None
    be_type: Optional[Union[str, int]] = None
    city_code: Optional[str] = None
    city_name: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    distance: Optional[int] = None
    distance_text: Optional[str] = None
    duration: Optional[int] = None
    estimated_delivery_time: Optional[str] = None
    delivery_time: Optional[str] = None
    business_status: Optional[int] = None
    dayparts: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class OrderItem(BaseModel):
    product_code: str
    product_name: str
    quantity: int = 1
    real_subtotal: Union[int, float, Decimal, None] = None
    sequence: int = 1
    product_type: str = "single"
    unique_key: Optional[str] = None


class CartSnapshot(BaseModel):
    store_code: Optional[str] = None
    be_code: Optional[str] = None
    order_type: Optional[int] = None
    day_part_code: Optional[str] = None
    cart_type: int = 1
    total_price: Union[int, float, Decimal, None] = None
    product_total_price: Union[int, float, Decimal, None] = None
    discount_amount: Union[int, float, Decimal, None] = None
    delivery_price: Union[int, float, Decimal, None] = None
    real_total_price: Union[int, float, Decimal, None] = None
    real_delivery_price: Union[int, float, Decimal, None] = None
    submit: Union[int, float, Decimal, None] = None
    products: list[dict[str, Any]] = Field(default_factory=list)
    promotions: list[dict[str, Any]] = Field(default_factory=list)
    tips: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class NutritionLineItem(BaseModel):
    product_name: str
    quantity: int = 1
    nutrition: Optional[NutritionFacts] = None
    source: str = "unknown"
    note: Optional[str] = None


class NutritionReport(BaseModel):
    source: str = "unknown"
    items: list[NutritionLineItem] = Field(default_factory=list)
    total: NutritionFacts = Field(default_factory=NutritionFacts)
    note: Optional[str] = None
    generated_at: Optional[str] = None
    raw: dict[str, Any] = Field(default_factory=dict)


class OrderDraft(BaseModel):
    store_code: Optional[str] = None
    be_code: Optional[str] = None
    order_type: Optional[int] = None
    day_part_code: Optional[str] = None
    eat_type_code: Optional[str] = None
    pickup_time_code: Optional[str] = None
    expect_delivery_time_code: Optional[str] = None
    expect_delivery_date_code: Optional[str] = None
    address_id: Optional[str] = None
    tableware_code: Optional[str] = None
    remark: Optional[str] = None
    real_total_amount: Optional[str] = None
    real_delivery_price: Optional[str] = None
    card_id: Optional[str] = None
    cart_items: list[OrderItem] = Field(default_factory=list)


class AgentSessionState(BaseModel):
    session_id: str
    history: list[dict[str, str]] = Field(default_factory=list)
    rolling_summary: str = ""
    preference: UserPreference = Field(default_factory=UserPreference)
    addresses: list[DeliveryAddress] = Field(default_factory=list)
    selected_address: Optional[DeliveryAddress] = None
    nearby_stores: list[StoreProfile] = Field(default_factory=list)
    selected_store: Optional[StoreProfile] = None
    last_menu_context: dict[str, Any] = Field(default_factory=dict)
    candidate_items: list[CandidateItem] = Field(default_factory=list)
    cart_snapshot: CartSnapshot = Field(default_factory=CartSnapshot)
    nutrition_report: Optional[NutritionReport] = None
    order_draft: OrderDraft = Field(default_factory=OrderDraft)
    confirmed: bool = False
