import os
import psycopg2
from dotenv import load_dotenv, find_dotenv
from urllib.parse import urlparse

def init_db():
    """Initialize database using schema.sql"""
    # Load environment variables
    env_path = find_dotenv()
    print(f"Loading .env from: {env_path}")
    load_dotenv(env_path, override=True)
    
    # Get database URL from environment variable
    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://localhost/iaoff')
    print(f"Using DATABASE_URL: {DATABASE_URL}")
    
    # Parse database URL properly
    parsed = urlparse(DATABASE_URL)
    dbname = parsed.path[1:]  # Remove leading slash
    user = parsed.username
    password = parsed.password
    host = parsed.hostname
    port = parsed.port or 5432  # Default to 5432 if not specified
    
    print(f"Connecting to database with parameters:")
    print(f"dbname: {dbname}")
    print(f"user: {user}")
    print(f"host: {host}")
    print(f"port: {port}")
    
    try:
        # Connect to database
        conn = psycopg2.connect(
            dbname=dbname,
            user=user,
            password=password,
            host=host,
            port=port
        )
        conn.autocommit = True
        print("Successfully connected to database")
        
        # Read and execute schema.sql
        schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
        print(f"Reading schema from: {schema_path}")
        
        with open(schema_path, 'r') as f:
            schema = f.read()
            print("Schema content:")
            print(schema)
            
        with conn.cursor() as cur:
            print("Executing schema...")
            cur.execute(schema)
            print("Schema executed successfully")
            
        print("Database initialized successfully")
        
    except Exception as e:
        print(f"Error initializing database: {str(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        raise
    finally:
        if 'conn' in locals():
            conn.close()
            print("Database connection closed")

if __name__ == '__main__':
    init_db() 