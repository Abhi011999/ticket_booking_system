-- Database initialization script
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Events table
CREATE TABLE events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    total_seats INTEGER NOT NULL CHECK (total_seats > 0),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Holds table
CREATE TABLE holds (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_id UUID NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    payment_token VARCHAR(255) NOT NULL UNIQUE,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_expired BOOLEAN DEFAULT FALSE
);

-- Bookings table
CREATE TABLE bookings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    hold_id UUID NOT NULL REFERENCES holds(id) ON DELETE CASCADE,
    payment_token VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(hold_id, payment_token) -- Ensures idempotency
);

-- Indexes for performance & scalability
CREATE INDEX idx_holds_event_id ON holds(event_id);
CREATE INDEX idx_holds_expires_at ON holds(expires_at);
CREATE INDEX idx_holds_payment_token ON holds(payment_token);
CREATE INDEX idx_bookings_hold_id ON bookings(hold_id);
CREATE INDEX idx_bookings_payment_token ON bookings(payment_token);
