"""
This file makes the app directory a Python package.
"""

from flask import Flask
from flask_session import Session
from flask_apscheduler import APScheduler
import os
from dotenv import load_dotenv

def init_env():
    """Initialize environment variables"""
    # Load environment variables from .env file
    load_dotenv()
    
    # Ensure required environment variables are set
    required_vars = [
        'GHL_CLIENT_ID',
        'GHL_CLIENT_SECRET',
        'GHL_REDIRECT_URI',
        'DATABASE_URL'
    ]
    
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Create Flask app
app = Flask(__name__)

# Configure Flask app
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour

# Initialize Flask extensions
Session(app)
scheduler = APScheduler()

# Import routes after app is created
from app import routes 