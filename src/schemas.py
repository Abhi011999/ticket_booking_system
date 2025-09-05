from uuid import UUID
from datetime import datetime

from pydantic import BaseModel

# Request schemas
class EventCreate(BaseModel):
    name: str
    total_seats: int

class HoldRequest(BaseModel):
    event_id: UUID
    qty: int

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
