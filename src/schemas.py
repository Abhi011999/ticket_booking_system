from uuid import UUID
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

# Request schemas
class EventCreate(BaseModel):
    name: str
    total_seats: int

class HoldRequest(BaseModel):
    event_id: UUID
    qty: int
    allow_partial: bool = Field(default=False, description="Allow partial fulfillment if not enough seats available")
    hold_ttl_minutes: Optional[int] = Field(default=None, description="Custom hold TTL in minutes (max 60)")

class BookingRequest(BaseModel):
    hold_id: UUID
    payment_token: str

# Response schemas
class EventResponse(BaseModel):
    event_id: UUID
    total_seats: int
    created_at: datetime

    class Config:
        from_attributes = True

class HoldResponse(BaseModel):
    hold_id: UUID
    expires_at: datetime
    payment_token: str
    quantity_held: int
    quantity_requested: int
    partial_fulfillment: bool = Field(default=False, description="True if this is a partial fulfillment")

    class Config:
        from_attributes = True

class BookingResponse(BaseModel):
    booking_id: UUID

    class Config:
        from_attributes = True

class EventStatusResponse(BaseModel):
    total: int
    available: int
    held: int
    booked: int

    class Config:
        from_attributes = True

class MetricsResponse(BaseModel):
    """System-wide metrics"""
    total_events: int
    total_holds: int
    active_holds: int
    expired_holds: int
    total_bookings: int
    total_seats_booked: int
    total_seats_held: int
    holds_expiring_soon: int = Field(description="Holds expiring within next 5 minutes")

    class Config:
        from_attributes = True
