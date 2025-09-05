import os
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, String, Integer, DateTime, Boolean, ForeignKey, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID

# Database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/ticket_booking")

# Create async engine
engine = create_async_engine(DATABASE_URL, echo=True)
async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class Event(Base):
    __tablename__ = "events"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    total_seats = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    __table_args__ = (
        CheckConstraint('total_seats > 0', name='check_total_seats_positive'),
    )

class Hold(Base):
    __tablename__ = "holds"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    quantity = Column(Integer, nullable=False)
    payment_token = Column(String(255), nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    is_expired = Column(Boolean, default=False)
    
    __table_args__ = (
        CheckConstraint('quantity > 0', name='check_quantity_positive'),
    )

class Booking(Base):
    __tablename__ = "bookings"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hold_id = Column(UUID(as_uuid=True), ForeignKey("holds.id", ondelete="CASCADE"), nullable=False)
    payment_token = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    __table_args__ = (
        # This ensures idempotency - same hold_id + payment_token can't be booked twice
        CheckConstraint('hold_id IS NOT NULL AND payment_token IS NOT NULL', name='check_booking_required_fields'),
    )

async def get_db():
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()
