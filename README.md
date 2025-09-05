# Ticket Booking System

A high-performance ticket booking system built with FastAPI, PostgreSQL, and Docker Compose. Designed to handle concurrent seat reservations without overbooking, with automatic hold expiry and idempotent booking confirmations.

## Architecture Overview

```mermaid
graph TD
    A["Client Applications"] --> B["FastAPI Application<br/>(main.py)"]
    B --> C["Database Layer<br/>(asyncpg + SQLAlchemy)"]
    C --> D["PostgreSQL Database"]
    
    B --> E["Background Tasks<br/>(Hold Expiry Worker)"]
    
    subgraph "API Endpoints"
        F["POST /events<br/>Create Event"]
        G["POST /holds<br/>Request Hold"]
        H["POST /book<br/>Confirm Booking"]
        I["GET /events/{id}<br/>Event Status"]
    end
    
    B --> F
    B --> G
    B --> H
    B --> I
    
    subgraph "Database Tables"
        J["events<br/>- id (UUID)<br/>- name<br/>- total_seats<br/>- created_at"]
        K["holds<br/>- id (UUID)<br/>- event_id<br/>- quantity<br/>- payment_token<br/>- expires_at<br/>- is_expired"]
        L["bookings<br/>- id (UUID)<br/>- hold_id<br/>- payment_token<br/>- created_at"]
    end
    
    D --> J
    D --> K
    D --> L
    
    subgraph "Concurrency Control"
        M["Database Transactions<br/>(BEGIN/COMMIT)"]
        N["Row-level Locking<br/>(SELECT FOR UPDATE)"]
        O["Unique Constraints<br/>(payment_token)"]
    end
    
    C --> M
    C --> N
    C --> O
    
    subgraph "Hold Management"
        P["2-minute TTL"]
        Q["Background Cleanup<br/>(30s intervals)"]
        R["Automatic Expiry"]
    end
    
    E --> P
    E --> Q
    E --> R
```

## API Endpoints

### 1. Create Event
```http
POST /events

{
  "name": "Concert XYZ",
  "total_seats": 1000
}
```

**Response:**
```json
{
  "event_id": "uuid-here",
  "total_seats": 1000,
  "created_at": "2024-01-01T12:00:00Z"
}
```

### 2. Request Hold
```http
POST /holds

{
  "event_id": "uuid-here",
  "qty": 5
}
```

**Response:**
```json
{
  "hold_id": "uuid-here",
  "expires_at": "2024-01-01T12:02:00Z",
  "payment_token": "secure-token-here"
}
```

**Error Cases:**
- `404`: Event not found
- `400`: Not enough seats available

### 3. Confirm Booking
```http
POST /book

{
  "hold_id": "uuid-here",
  "payment_token": "secure-token-here"
}
```

**Response:**
```json
{
  "booking_id": "uuid-here"
}
```

**Error Cases:**
- `404`: Hold not found
- `400`: Invalid payment token
- `400`: Hold expired or already booked

### 4. Event Status
```http
GET /events/{event_id}
```

**Response:**
```json
{
  "total": 1000,
  "available": 950,
  "held": 30,
  "booked": 20
}
```

## Expiry/Worker Design

### Background Worker
- **Cleanup Frequency**: Runs every 30 seconds
- **Async Implementation**: Non-blocking background task
- **Error Handling**: Graceful failure recovery with logging

### Expiry Logic
```sql
UPDATE holds 
SET is_expired = true 
WHERE expires_at <= NOW() 
  AND is_expired = false
```

### Real-time Availability Calculation
```sql
SELECT 
  total_seats - 
  (active_holds + confirmed_bookings) as available
FROM events e
WHERE e.id = ?
```
