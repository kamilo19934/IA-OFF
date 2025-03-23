-- Create schema if it doesn't exist
CREATE SCHEMA IF NOT EXISTS iaoff;

-- Create tokens table
CREATE TABLE IF NOT EXISTS iaoff.tokens (
    id SERIAL PRIMARY KEY,
    access_token VARCHAR NOT NULL,
    refresh_token VARCHAR,
    location_id VARCHAR,
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT true
); 