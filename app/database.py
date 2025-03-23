from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone, timedelta
import os
from dotenv import load_dotenv
import requests
import json

# Load environment variables
load_dotenv()

# Get database URL from environment variable
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://camiloreyes@localhost:5432/iaoff')

# Create SQLAlchemy engine with echo=True for debugging
engine = create_engine(DATABASE_URL, echo=True)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create declarative base
Base = declarative_base()

# Define schema name
SCHEMA_NAME = 'iaoff'

def get_utc_now():
    """Get current UTC datetime"""
    return datetime.now(timezone.utc)

class Token(Base):
    """Token model for storing OAuth tokens"""
    __tablename__ = "tokens"
    __table_args__ = {'schema': SCHEMA_NAME}

    id = Column(Integer, primary_key=True, index=True)
    access_token = Column(String)
    refresh_token = Column(String)
    location_id = Column(String)
    expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=get_utc_now)
    is_active = Column(Boolean, default=True)

    def needs_refresh(self):
        """Check if token needs refresh"""
        if not self.expires_at:
            return True
        # Refresh if less than 1 hour until expiration
        return get_utc_now() + timedelta(hours=1) >= self.expires_at

    def is_expired(self):
        """Check if token is expired"""
        if not self.expires_at:
            return True
        return get_utc_now() >= self.expires_at

def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def save_token(token_info, location_id=None):
    """Save token to database"""
    try:
        print("\n=== SAVING TOKEN TO DATABASE ===")
        print(f"Location ID: {location_id}")
        print(f"Token info keys: {list(token_info.keys())}")
        
        db = SessionLocal()
        print("Database session created")
        
        token = Token(
            access_token=token_info['access_token'],
            refresh_token=token_info.get('refresh_token'),
            location_id=location_id,
            expires_at=get_utc_now() + timedelta(seconds=token_info.get('expires_in', 3600)),
            is_active=True
        )
        print("Token object created")
        
        db.add(token)
        print("Token added to session")
        
        db.commit()
        print("Changes committed to database")
        
        db.refresh(token)
        print("Token refreshed from database")
        
        print(f"Token saved successfully with ID: {token.id}")
        return token
    except Exception as e:
        print(f"Error saving token: {str(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        if 'db' in locals():
            db.rollback()
        return None
    finally:
        if 'db' in locals():
            db.close()
            print("Database session closed")

def get_valid_token():
    """Get a valid access token from database"""
    try:
        db = SessionLocal()
        token = db.query(Token).filter(Token.is_active == True).order_by(Token.created_at.desc()).first()
        
        if not token:
            print("No active token found")
            return None
            
        if token.is_expired():
            print("Token is expired")
            return None
            
        return token.access_token
    except Exception as e:
        print(f"Error getting valid token: {str(e)}")
        return None
    finally:
        if 'db' in locals():
            db.close()

def refresh_token(token_info):
    """Refresh token in database"""
    try:
        db = SessionLocal()
        # Create new token instead of updating existing one
        token = Token(
            access_token=token_info['access_token'],
            refresh_token=token_info.get('refresh_token'),
            expires_at=get_utc_now() + timedelta(seconds=token_info.get('expires_in', 3600)),
            is_active=True
        )
        db.add(token)
        db.commit()
        db.refresh(token)
        return token
    except Exception as e:
        print(f"Error refreshing token: {str(e)}")
        if 'db' in locals():
            db.rollback()
        return None
    finally:
        if 'db' in locals():
            db.close()

# Bind the engine to the Base
Base.metadata.bind = engine 