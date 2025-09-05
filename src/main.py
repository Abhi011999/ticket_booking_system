import uuid
import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from sqlalchemy.exc import IntegrityError

from .db import get_db, Event, Hold, Booking
from .schemas import (
    EventCreate, EventResponse, HoldRequest, HoldResponse,
    BookingRequest, BookingResponse, EventStatusResponse, MetricsResponse
)
from .logging_config import logger

# Constants for hold TTL
DEFAULT_HOLD_TTL_MINUTES = 2
MAX_HOLD_TTL_MINUTES = 60
MIN_HOLD_TTL_MINUTES = 1


def generate_request_id() -> str:
    """Generate a unique request ID for correlation"""
    return str(uuid.uuid4())


def validate_hold_ttl(ttl_minutes: Optional[int]) -> int:
    """Validate and cap hold TTL within sensible limits"""
    if ttl_minutes is None:
        return DEFAULT_HOLD_TTL_MINUTES
    
    return max(MIN_HOLD_TTL_MINUTES, min(ttl_minutes, MAX_HOLD_TTL_MINUTES))


# Background task to clean up expired holds
async def cleanup_expired_holds():
    """Background task that runs every 30 seconds to mark expired holds"""
    while True:
        try:
            # Get a fresh database session for the cleanup task
            async for db in get_db():
                now = datetime.now(timezone.utc)
                
                # Get expired holds count before update
                expired_count_result = await db.execute(
                    select(func.count(Hold.id)).where(
                        and_(Hold.expires_at <= now, not Hold.is_expired)
                    )
                )
                expired_count = expired_count_result.scalar() or 0
                
                if expired_count > 0:
                    # Mark expired holds as expired
                    await db.execute(
                        f"UPDATE holds SET is_expired = true WHERE expires_at <= '{now}' AND is_expired = false"
                    )
                    await db.commit()
                    
                    logger.info(
                        "Expired holds cleanup",
                        event_type="holds_expired",
                        expired_count=expired_count,
                        cleanup_time=now.isoformat()
                    )
                break  # Exit the async for loop after processing
        except Exception as e:
            logger.error(
                "Error in cleanup task",
                event_type="cleanup_error",
                error=str(e)
            )
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start the cleanup task
    cleanup_task = asyncio.create_task(cleanup_expired_holds())
    yield
    # Shutdown: Cancel the cleanup task
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Abhishek's Box Office",
    version="1.0.0",
    lifespan=lifespan
)


@app.post("/events", response_model=EventResponse)
async def create_event(
    event_data: EventCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new event with specified name and total seats"""
    request_id = generate_request_id()
    
    logger.info(
        "Creating event",
        event_type="event_create_start",
        request_id=request_id,
        event_name=event_data.name,
        total_seats=event_data.total_seats
    )
    
    event = Event(
        name=event_data.name,
        total_seats=event_data.total_seats
    )
    
    db.add(event)
    try:
        await db.commit()
        await db.refresh(event)
        
        logger.info(
            "Event created successfully",
            event_type="event_create_success",
            request_id=request_id,
            event_id=str(event.id),
            event_name=event_data.name,
            total_seats=event_data.total_seats
        )
    except IntegrityError as e:
        await db.rollback()
        logger.error(
            "Failed to create event",
            event_type="event_create_error",
            request_id=request_id,
            error=str(e),
            event_name=event_data.name
        )
        raise HTTPException(status_code=400, detail="Failed to create event")
    
    return EventResponse(
        event_id=event.id,
        total_seats=event.total_seats,
        created_at=event.created_at
    )


@app.post("/holds", response_model=HoldResponse)
async def create_hold(
    hold_request: HoldRequest,
    db: AsyncSession = Depends(get_db)
):
    """Create a temporary hold on seats with configurable TTL and partial fulfillment support"""
    request_id = generate_request_id()
    
    # Validate and set hold TTL
    hold_ttl = validate_hold_ttl(hold_request.hold_ttl_minutes)
    
    logger.info(
        "Creating hold",
        event_type="hold_create_start",
        request_id=request_id,
        event_id=str(hold_request.event_id),
        requested_qty=hold_request.qty,
        allow_partial=hold_request.allow_partial,
        hold_ttl_minutes=hold_ttl
    )
    
    async with db.begin():  # Use transaction for concurrency control
        # Check if event exists
        event_result = await db.execute(
            select(Event).where(Event.id == hold_request.event_id)
        )
        event = event_result.scalar_one_or_none()
        
        if not event:
            logger.error(
                "Event not found",
                event_type="hold_create_error",
                request_id=request_id,
                event_id=str(hold_request.event_id),
                error="event_not_found"
            )
            raise HTTPException(status_code=404, detail="Event not found")
        
        # Calculate current seat usage (active holds + bookings)
        now = datetime.now(timezone.utc)
        
        # Count active holds (not expired and not booked)
        active_holds_result = await db.execute(
            select(func.coalesce(func.sum(Hold.quantity), 0)).where(
                and_(
                    Hold.event_id == hold_request.event_id,
                    Hold.expires_at > now,
                    not Hold.is_expired,
                    ~Hold.id.in_(
                        select(Booking.hold_id).where(Booking.hold_id == Hold.id)
                    )
                )
            )
        )
        active_holds = active_holds_result.scalar() or 0
        
        # Count confirmed bookings
        booked_seats_result = await db.execute(
            select(func.coalesce(func.sum(Hold.quantity), 0)).where(
                and_(
                    Hold.event_id == hold_request.event_id,
                    Hold.id.in_(
                        select(Booking.hold_id)
                    )
                )
            )
        )
        booked_seats = booked_seats_result.scalar() or 0
        
        # Calculate available seats
        available = event.total_seats - (active_holds + booked_seats)
        
        # Determine quantity to hold
        quantity_to_hold = hold_request.qty
        partial_fulfillment = False
        
        if available < hold_request.qty:
            if hold_request.allow_partial and available > 0:
                quantity_to_hold = available
                partial_fulfillment = True
                logger.info(
                    "Partial fulfillment approved",
                    event_type="hold_partial_fulfillment",
                    request_id=request_id,
                    event_id=str(hold_request.event_id),
                    requested_qty=hold_request.qty,
                    available_qty=available,
                    quantity_to_hold=quantity_to_hold
                )
            else:
                logger.error(
                    "Insufficient seats available",
                    event_type="hold_create_error",
                    request_id=request_id,
                    event_id=str(hold_request.event_id),
                    requested_qty=hold_request.qty,
                    available_qty=available,
                    error="insufficient_seats"
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Not enough seats available. Requested: {hold_request.qty}, Available: {available}"
                )
        
        # Create hold with custom TTL
        payment_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=hold_ttl)
        
        hold = Hold(
            event_id=hold_request.event_id,
            quantity=quantity_to_hold,
            payment_token=payment_token,
            expires_at=expires_at
        )
        
        db.add(hold)
        await db.flush()  # Flush to get the ID but don't commit yet
        
        logger.info(
            "Hold created successfully",
            event_type="hold_create_success",
            request_id=request_id,
            hold_id=str(hold.id),
            event_id=str(hold_request.event_id),
            requested_qty=hold_request.qty,
            quantity_held=quantity_to_hold,
            partial_fulfillment=partial_fulfillment,
            expires_at=expires_at.isoformat(),
            hold_ttl_minutes=hold_ttl
        )
        
        return HoldResponse(
            hold_id=hold.id,
            expires_at=hold.expires_at,
            payment_token=hold.payment_token,
            quantity_held=quantity_to_hold,
            quantity_requested=hold_request.qty,
            partial_fulfillment=partial_fulfillment
        )


@app.post("/book", response_model=BookingResponse)
async def create_booking(
    booking_request: BookingRequest,
    db: AsyncSession = Depends(get_db)
):
    """Confirm a booking using an active hold"""
    request_id = generate_request_id()
    
    logger.info(
        "Creating booking",
        event_type="booking_create_start",
        request_id=request_id,
        hold_id=str(booking_request.hold_id)
    )
    
    async with db.begin():  # Use transaction for concurrency control
        # Check if booking already exists (idempotency)
        existing_booking_result = await db.execute(
            select(Booking).where(
                and_(
                    Booking.hold_id == booking_request.hold_id,
                    Booking.payment_token == booking_request.payment_token
                )
            )
        )
        existing_booking = existing_booking_result.scalar_one_or_none()
        
        if existing_booking:
            # Return existing booking (idempotent)
            logger.info(
                "Returning existing booking (idempotent)",
                event_type="booking_create_idempotent",
                request_id=request_id,
                hold_id=str(booking_request.hold_id),
                booking_id=str(existing_booking.id)
            )
            return BookingResponse(booking_id=existing_booking.id)
        
        # Verify hold exists and is valid
        hold_result = await db.execute(
            select(Hold).where(Hold.id == booking_request.hold_id)
        )
        hold = hold_result.scalar_one_or_none()
        
        if not hold:
            logger.error(
                "Hold not found",
                event_type="booking_create_error",
                request_id=request_id,
                hold_id=str(booking_request.hold_id),
                error="hold_not_found"
            )
            raise HTTPException(status_code=404, detail="Hold not found")
        
        if hold.payment_token != booking_request.payment_token:
            logger.error(
                "Invalid payment token",
                event_type="booking_create_error",
                request_id=request_id,
                hold_id=str(booking_request.hold_id),
                error="invalid_payment_token"
            )
            raise HTTPException(status_code=400, detail="Invalid payment token")
        
        if hold.is_expired or hold.expires_at <= datetime.now(timezone.utc):
            logger.error(
                "Hold has expired",
                event_type="booking_create_error",
                request_id=request_id,
                hold_id=str(booking_request.hold_id),
                error="hold_expired",
                expires_at=hold.expires_at.isoformat()
            )
            raise HTTPException(status_code=400, detail="Hold has expired")
        
        # Check if hold is already booked
        existing_booking_for_hold = await db.execute(
            select(Booking).where(Booking.hold_id == booking_request.hold_id)
        )
        if existing_booking_for_hold.scalar_one_or_none():
            logger.error(
                "Hold already booked",
                event_type="booking_create_error",
                request_id=request_id,
                hold_id=str(booking_request.hold_id),
                error="hold_already_booked"
            )
            raise HTTPException(status_code=400, detail="Hold already booked")
        
        # Create booking
        booking = Booking(
            hold_id=booking_request.hold_id,
            payment_token=booking_request.payment_token
        )
        
        db.add(booking)
        await db.flush()
        
        logger.info(
            "Booking created successfully",
            event_type="booking_create_success",
            request_id=request_id,
            hold_id=str(booking_request.hold_id),
            booking_id=str(booking.id),
            event_id=str(hold.event_id),
            quantity=hold.quantity
        )
        
        return BookingResponse(booking_id=booking.id)


@app.get("/events/{event_id}", response_model=EventStatusResponse)
async def get_event_status(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """Get current event status with seat counts"""
    # Check if event exists
    event_result = await db.execute(
        select(Event).where(Event.id == event_id)
    )
    event = event_result.scalar_one_or_none()
    
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    now = datetime.now(timezone.utc)
    
    # Count active holds (not expired and not booked)
    active_holds_result = await db.execute(
        select(func.coalesce(func.sum(Hold.quantity), 0)).where(
            and_(
                Hold.event_id == event_id,
                Hold.expires_at > now,
                not Hold.is_expired,
                ~Hold.id.in_(
                    select(Booking.hold_id).where(Booking.hold_id == Hold.id)
                )
            )
        )
    )
    held_seats = active_holds_result.scalar() or 0
    
    # Count confirmed bookings
    booked_seats_result = await db.execute(
        select(func.coalesce(func.sum(Hold.quantity), 0)).where(
            and_(
                Hold.event_id == event_id,
                Hold.id.in_(
                    select(Booking.hold_id)
                )
            )
        )
    )
    booked_seats = booked_seats_result.scalar() or 0
    
    available_seats = event.total_seats - held_seats - booked_seats
    
    return EventStatusResponse(
        total=event.total_seats,
        available=available_seats,
        held=held_seats,
        booked=booked_seats
    )


@app.get("/metrics", response_model=MetricsResponse)
async def get_metrics(
    db: AsyncSession = Depends(get_db)
):
    """Get system-wide metrics including totals, active holds, bookings, and expiries"""
    request_id = generate_request_id()
    
    logger.info(
        "Fetching metrics",
        event_type="metrics_fetch_start",
        request_id=request_id
    )
    
    now = datetime.now(timezone.utc)
    five_minutes_from_now = now + timedelta(minutes=5)
    
    # Get total events count
    total_events_result = await db.execute(select(func.count(Event.id)))
    total_events = total_events_result.scalar() or 0
    
    # Get total holds count
    total_holds_result = await db.execute(select(func.count(Hold.id)))
    total_holds = total_holds_result.scalar() or 0
    
    # Get active holds count (not expired and not booked)
    active_holds_result = await db.execute(
        select(func.count(Hold.id)).where(
            and_(
                Hold.expires_at > now,
                not Hold.is_expired,
                ~Hold.id.in_(
                    select(Booking.hold_id).where(Booking.hold_id == Hold.id)
                )
            )
        )
    )
    active_holds = active_holds_result.scalar() or 0
    
    # Get expired holds count
    expired_holds_result = await db.execute(
        select(func.count(Hold.id)).where(
            or_(
                Hold.expires_at <= now,
                Hold.is_expired
            )
        )
    )
    expired_holds = expired_holds_result.scalar() or 0
    
    # Get total bookings count
    total_bookings_result = await db.execute(select(func.count(Booking.id)))
    total_bookings = total_bookings_result.scalar() or 0
    
    # Get total seats booked
    total_seats_booked_result = await db.execute(
        select(func.coalesce(func.sum(Hold.quantity), 0)).where(
            Hold.id.in_(select(Booking.hold_id))
        )
    )
    total_seats_booked = total_seats_booked_result.scalar() or 0
    
    # Get total seats held (active holds only)
    total_seats_held_result = await db.execute(
        select(func.coalesce(func.sum(Hold.quantity), 0)).where(
            and_(
                Hold.expires_at > now,
                not Hold.is_expired,
                ~Hold.id.in_(
                    select(Booking.hold_id).where(Booking.hold_id == Hold.id)
                )
            )
        )
    )
    total_seats_held = total_seats_held_result.scalar() or 0
    
    # Get holds expiring soon (within next 5 minutes)
    holds_expiring_soon_result = await db.execute(
        select(func.count(Hold.id)).where(
            and_(
                Hold.expires_at > now,
                Hold.expires_at <= five_minutes_from_now,
                not Hold.is_expired,
                ~Hold.id.in_(
                    select(Booking.hold_id).where(Booking.hold_id == Hold.id)
                )
            )
        )
    )
    holds_expiring_soon = holds_expiring_soon_result.scalar() or 0
    
    logger.info(
        "Metrics fetched successfully",
        event_type="metrics_fetch_success",
        request_id=request_id,
        total_events=total_events,
        active_holds=active_holds,
        total_bookings=total_bookings
    )
    
    return MetricsResponse(
        total_events=total_events,
        total_holds=total_holds,
        active_holds=active_holds,
        expired_holds=expired_holds,
        total_bookings=total_bookings,
        total_seats_booked=total_seats_booked,
        total_seats_held=total_seats_held,
        holds_expiring_soon=holds_expiring_soon
    )


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
