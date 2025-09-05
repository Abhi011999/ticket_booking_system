import uuid
import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from sqlalchemy.exc import IntegrityError

from .db import get_db, Event, Hold, Booking
from .schemas import (
    EventCreate, EventResponse, HoldRequest, HoldResponse,
    BookingRequest, BookingResponse, EventStatusResponse
)


# Background task to clean up expired holds
async def cleanup_expired_holds():
    """Background task that runs every 30 seconds to mark expired holds"""
    while True:
        try:
            async with get_db().__anext__() as db:
                db: AsyncSession
                now = datetime.now(timezone.utc)
                # Mark expired holds as expired
                await db.execute(
                    f"UPDATE holds SET is_expired = true WHERE expires_at <= '{now}' AND is_expired = false"
                )
                await db.commit()
        except Exception as e:
            print(f"Error in cleanup task: {e}")
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
    event = Event(
        name=event_data.name,
        total_seats=event_data.total_seats
    )
    
    db.add(event)
    try:
        await db.commit()
        await db.refresh(event)
    except IntegrityError:
        await db.rollback()
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
    """Create a temporary hold on seats for 2 minutes"""
    async with db.begin():  # Use transaction for concurrency control
        # Check if event exists
        event_result = await db.execute(
            select(Event).where(Event.id == hold_request.event_id)
        )
        event = event_result.scalar_one_or_none()
        
        if not event:
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
        
        # Check if enough seats are available
        total_used = active_holds + booked_seats + hold_request.qty
        if total_used > event.total_seats:
            available = event.total_seats - (active_holds + booked_seats)
            raise HTTPException(
                status_code=400,
                detail=f"Not enough seats available. Requested: {hold_request.qty}, Available: {available}"
            )
        
        # Create hold with 2-minute expiry
        payment_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)
        
        hold = Hold(
            event_id=hold_request.event_id,
            quantity=hold_request.qty,
            payment_token=payment_token,
            expires_at=expires_at
        )
        
        db.add(hold)
        await db.flush()  # Flush to get the ID but don't commit yet
        
        return HoldResponse(
            hold_id=hold.id,
            expires_at=hold.expires_at,
            payment_token=hold.payment_token
        )


@app.post("/book", response_model=BookingResponse)
async def create_booking(
    booking_request: BookingRequest,
    db: AsyncSession = Depends(get_db)
):
    """Confirm a booking using an active hold"""
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
            return BookingResponse(booking_id=existing_booking.id)
        
        # Verify hold exists and is valid
        hold_result = await db.execute(
            select(Hold).where(Hold.id == booking_request.hold_id)
        )
        hold = hold_result.scalar_one_or_none()
        
        if not hold:
            raise HTTPException(status_code=404, detail="Hold not found")
        
        if hold.payment_token != booking_request.payment_token:
            raise HTTPException(status_code=400, detail="Invalid payment token")
        
        if hold.is_expired or hold.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="Hold has expired")
        
        # Check if hold is already booked
        existing_booking_for_hold = await db.execute(
            select(Booking).where(Booking.hold_id == booking_request.hold_id)
        )
        if existing_booking_for_hold.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Hold already booked")
        
        # Create booking
        booking = Booking(
            hold_id=booking_request.hold_id,
            payment_token=booking_request.payment_token
        )
        
        db.add(booking)
        await db.flush()
        
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


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
