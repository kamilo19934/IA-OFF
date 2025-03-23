from app.database import engine, Base, Token
from sqlalchemy import text

def test_connection():
    try:
        # Try to connect to the database
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            print("Database connection successful!")
            
            # Check if the tokens table exists
            result = connection.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'tokens'
                );
            """))
            exists = result.scalar()
            print(f"Tokens table exists: {exists}")
            
            if not exists:
                print("Creating tables...")
                Base.metadata.create_all(bind=engine)
                print("Tables created successfully!")
            
    except Exception as e:
        print(f"Error connecting to database: {str(e)}")

if __name__ == "__main__":
    test_connection() 