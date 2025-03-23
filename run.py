import os
import sys
from app import app
from init_db import init_db
import signal

def signal_handler(sig, frame):
    """Handle shutdown signals gracefully"""
    print("\nShutting down gracefully...")
    sys.exit(0)

def main():
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Ensure we're in the correct directory
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    # Initialize database
    init_db()
    
    # Start Flask application
    app.run(debug=True, use_reloader=False)

if __name__ == '__main__':
    main() 