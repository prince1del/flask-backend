from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Distributor:
    id: str
    name: str
    contact_person: str
    phone: str
    email: str
    address: str
    city: str
    state: str
    gst_number: Optional[str] = None
    credit_limit: Optional[float] = None
    balance: float = 0.0
    status: str = "active"
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Retailer:
    id: str
    name: str
    contact_person: str
    phone: str
    email: str
    address: str
    city: str
    state: str
    gst_number: Optional[str] = None
    credit_limit: Optional[float] = None
    balance: float = 0.0
    status: str = "active"
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Product:
    id: str
    name: str
    category: str
    unit: str
    price: float
    stock_quantity: int
    description: Optional[str] = None
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StockTransfer:
    id: str
    distributor_id: str
    retailer_id: str
    product_id: str
    quantity: int
    transfer_date: str
    status: str = "pending"
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Payment:
    id: str
    party_type: str
    party_id: str
    amount: float
    payment_date: str
    payment_mode: str
    notes: Optional[str] = None
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)
