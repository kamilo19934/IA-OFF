from flask import redirect, request, session, url_for, render_template, jsonify
from app import app
from app.database import save_token, get_valid_token, refresh_token, Token, SessionLocal
import requests
import json
import secrets
import os
from datetime import datetime, timezone
from urllib.parse import urlencode
import whisper
import tempfile
from pydub import AudioSegment
import io
import httpx
import ssl

# GoHighLevel API configuration
API_BASE_URL = 'https://services.leadconnectorhq.com'
API_VERSION = '2021-07-28'
GHL_AUTH_URL = 'https://marketplace.gohighlevel.com/oauth/chooselocation'
GHL_TOKEN_URL = 'https://services.leadconnectorhq.com/oauth/token'

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Get environment variables
GHL_CLIENT_ID = os.getenv('GHL_CLIENT_ID')
GHL_CLIENT_SECRET = os.getenv('GHL_CLIENT_SECRET')
GHL_REDIRECT_URI = os.getenv('GHL_REDIRECT_URI')

# Define required scopes
SCOPES = [
    'conversations.write',
    'conversations/message.readonly',
    'conversations/message.write',
    'conversations.readonly',
    'conversations/livechat.write',
    'locations.readonly',
    'locations/customValues.write',
    'locations/tags.write',
    'locations/tags.readonly',
    'locations/customValues.readonly',
    'locations/customFields.readonly',
    'locations/customFields.write',
    'contacts.write',
    'contacts.readonly'
]

# Initialize Whisper model
model = whisper.load_model("base")

def cleanup_resources():
    """Cleanup resources when the application shuts down"""
    try:
        if hasattr(model, 'cleanup'):
            model.cleanup()
        print("Resources cleaned up successfully")
    except Exception as e:
        print(f"Error cleaning up resources: {str(e)}")

# Register cleanup function
import atexit
atexit.register(cleanup_resources)

class MessageHandler:
    @staticmethod
    def process_attachments(attachments, conversation_id, message_type):
        """Process attachments from webhook data"""
        try:
            transcriptions = []
            for attachment in attachments:
                # Handle both string URLs and dictionary attachments
                if isinstance(attachment, str):
                    file_url = attachment
                else:
                    file_url = attachment.get('url')
                
                if not file_url:
                    print(f"No URL found in attachment: {attachment}")
                    continue
                
                # Download the file
                print(f"\nDownloading file from: {file_url}")
                response = requests.get(file_url)
                if response.status_code != 200:
                    print(f"Error downloading file: {response.status_code}")
                    continue
                
                # Save to temporary file
                with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_file:
                    temp_file.write(response.content)
                    temp_file_path = temp_file.name
                
                try:
                    # Transcribe the audio
                    print(f"Transcribing audio file: {temp_file_path}")
                    result = model.transcribe(temp_file_path)
                    transcription = result["text"]
                    
                    transcriptions.append({
                        'url': file_url,
                        'transcription': transcription
                    })
                    
                finally:
                    # Clean up temporary file
                    os.unlink(temp_file_path)
            
            return transcriptions
            
        except Exception as e:
            print(f"Error processing attachments: {str(e)}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
            return []

@app.route('/')
def index():
    """Render the index page"""
    locations = []
    if 'access_token' in session:
        locations = get_locations()
    return render_template('index.html', locations=locations)

@app.route('/login')
def login():
    """Initiate OAuth login flow"""
    try:
        # Generate state for CSRF protection
        state = secrets.token_urlsafe(16)
        session['oauth_state'] = state
        
        # Build authorization URL
        params = {
            'client_id': GHL_CLIENT_ID,
            'redirect_uri': GHL_REDIRECT_URI,
            'response_type': 'code',
            'scope': ' '.join(SCOPES),
            'state': state
        }
        
        auth_url = f"{GHL_AUTH_URL}?{urlencode(params)}"
        print(f"\nAuthorization URL: {auth_url}")
        
        return redirect(auth_url)
    except Exception as e:
        print(f"Error in login: {str(e)}")
        return f"Error: {str(e)}", 500

@app.route('/callback')
def callback():
    """Handle OAuth callback from GoHighLevel"""
    try:
        # Get authorization code from query parameters
        code = request.args.get('code')
        state = request.args.get('state')
        
        # Verify state to prevent CSRF attacks
        if state != session.get('oauth_state'):
            return "Invalid state parameter", 400
            
        # Exchange code for access token
        token_url = GHL_TOKEN_URL
        token_data = {
            "client_id": GHL_CLIENT_ID,
            "client_secret": GHL_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": GHL_REDIRECT_URI,
            "user_type": "Location"  # Add user_type parameter
        }
        
        print("\n=== TOKEN REQUEST ===")
        print(f"Token URL: {token_url}")
        print(f"Request data: {json.dumps({k: v for k, v in token_data.items() if k != 'client_secret'}, indent=2)}")
        
        response = requests.post(token_url, data=token_data)
        print(f"\nResponse status: {response.status_code}")
        print(f"Response body: {response.text}")
        
        response.raise_for_status()
        token_info = response.json()
        
        # Get location ID from token response
        location_id = token_info.get('locationId')
        if not location_id:
            print("No location ID in token response")
            return "No location ID in token response", 500
        
        # Save token with location ID
        token = save_token(token_info, location_id)
        if not token:
            print("Failed to save token")
            return "Failed to save token", 500
            
        # Store tokens in session
        session['access_token'] = token_info['access_token']
        session['refresh_token'] = token_info.get('refresh_token')
        session['location_id'] = location_id
        
        print("\n=== TOKEN SAVED ===")
        print(f"Access Token: {token_info['access_token'][:20]}...")
        print(f"Refresh Token: {token_info.get('refresh_token', '')[:20]}...")
        print(f"Location ID: {location_id}")
        
        return redirect(url_for('index'))
        
    except Exception as e:
        print(f"Error in callback: {str(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return f"Error: {str(e)}", 500

@app.route('/logout')
def logout():
    """Clear session and redirect to index"""
    session.clear()
    return redirect(url_for('index'))

def ensure_transcription_field(location_id, access_token):
    """Ensure the Transcription custom field exists, create it if it doesn't"""
    try:
        # Get the most recent token from database
        db = SessionLocal()
        try:
            token = db.query(Token).filter(Token.is_active == True).order_by(Token.created_at.desc()).first()
            if not token:
                print("No active token found")
                return None
                
            if token.is_expired():
                print("Token is expired")
                return None
                
            # Use the token from database instead of the passed one
            access_token = token.access_token
        finally:
            db.close()
            
        # First, check if the field exists
        url = f"{API_BASE_URL}/locations/{location_id}/customFields"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Version": API_VERSION
        }
        
        print(f"\nChecking custom fields at: {url}")
        print(f"Using token: {access_token[:20]}...")
        
        response = requests.get(url, headers=headers)
        print(f"Response status: {response.status_code}")
        print(f"Response body: {response.text}")
        
        if response.status_code == 200:
            fields = response.json().get('customFields', [])
            # Look for our Transcription field
            transcription_field = next((field for field in fields if field.get('name') == 'Transcription'), None)
            
            if transcription_field:
                print("Transcription field already exists")
                return transcription_field.get('id')
            
            # If we get here, the field doesn't exist, so let's create it
            print("Creating Transcription field")
            create_data = {
                "name": "Transcription",
                "dataType": "TEXT",
                "model": "contact",
                "placeholder": "Transcription of audio messages"
            }
            
            create_response = requests.post(url, headers=headers, json=create_data)
            if create_response.status_code == 200:
                new_field = create_response.json()
                print("Successfully created Transcription field")
                return new_field.get('id')
            else:
                print(f"Error creating field: {create_response.status_code} - {create_response.text}")
                return None
        else:
            print(f"Error checking fields: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        print(f"Error in ensure_transcription_field: {str(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return None

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhooks from GoHighLevel"""
    try:
        data = request.get_json()
        print("\n=== WEBHOOK DATA ===")
        print(json.dumps(data, indent=2))
        
        # Handle installation webhook
        if data.get('type') == 'INSTALL':
            location_id = data.get('locationId')
            if location_id:
                print(f"\nReceived installation webhook for location: {location_id}")
                
                # Update the token with the location ID
                db = SessionLocal()
                try:
                    # Get the most recent token
                    token = db.query(Token).order_by(Token.created_at.desc()).first()
                    if token:
                        token.location_id = location_id
                        db.commit()
                        print(f"Updated token with location ID: {location_id}")
                        
                        # Ensure transcription field exists
                        access_token = get_valid_token()
                        if access_token:
                            field_id = ensure_transcription_field(location_id, access_token)
                            if field_id:
                                print(f"Transcription field is ready with ID: {field_id}")
                    else:
                        print("No token found to update with location ID")
                except Exception as e:
                    print(f"Error updating token: {str(e)}")
                    db.rollback()
                finally:
                    db.close()
                
                return jsonify({'success': True})
        
        # Get the message type from the webhook data
        message_type = data.get('messageType')
        print(f"\nMessage Type: {message_type}")
        
        if not message_type:
            print("Error: No messageType in webhook data")
            return jsonify({'error': 'No messageType provided'}), 400
        
        # Get conversation ID
        conversation_id = data.get('conversationId')
        print(f"Conversation ID: {conversation_id}")
        
        if not conversation_id:
            print("Error: No conversationId in webhook data")
            return jsonify({'error': 'No conversationId provided'}), 400
        
        # Process attachments if present
        if 'attachments' in data:
            print(f"\nFound {len(data['attachments'])} attachments")
            transcriptions = MessageHandler.process_attachments(
                data['attachments'], 
                conversation_id,
                message_type
            )
            if transcriptions:
                print("\nTranscriptions:")
                for t in transcriptions:
                    print(f"URL: {t['url']}")
                    print(f"Transcription: {t['transcription']}")
                    print("---")
                    
                    # Get location ID from token
                    access_token = get_valid_token()
                    if access_token:
                        db = SessionLocal()
                        try:
                            token = db.query(Token).filter(Token.is_active == True).order_by(Token.created_at.desc()).first()
                            if token and token.location_id:
                                # Ensure transcription field exists
                                field_id = ensure_transcription_field(token.location_id, access_token)
                                if field_id:
                                    # Update contact with transcription
                                    contact_id = data.get('contactId')
                                    if contact_id:
                                        update_url = f"{API_BASE_URL}/contacts/{contact_id}"
                                        update_data = {
                                            "customFields": [
                                                {
                                                    "id": field_id,
                                                    "value": t['transcription']
                                                }
                                            ]
                                        }
                                        headers = {
                                            "Authorization": f"Bearer {access_token}",
                                            "Accept": "application/json",
                                            "Version": API_VERSION
                                        }
                                        update_response = requests.put(update_url, headers=headers, json=update_data)
                                        if update_response.status_code == 200:
                                            print(f"Successfully updated contact {contact_id} with transcription")
                                        else:
                                            print(f"Error updating contact: {update_response.status_code} - {update_response.text}")
                        finally:
                            db.close()
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        print(f"Exception type: {type(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

def get_locations():
    """Get list of locations from GoHighLevel"""
    try:
        access_token = get_valid_token()
        if not access_token:
            print("No valid access token found")
            return []
            
        # Get location ID from the token in database
        db = SessionLocal()
        try:
            token = db.query(Token).filter(Token.is_active == True).order_by(Token.created_at.desc()).first()
            if not token or not token.location_id:
                print("No location ID found in token")
                return []
                
            location_id = token.location_id
            print(f"\nUsing location ID: {location_id}")
            
            # Use the correct endpoint for getting location details
            url = f"https://services.leadconnectorhq.com/locations/{location_id}"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Version": API_VERSION
            }
            
            print(f"\nFetching location details from: {url}")
            print(f"Headers: {json.dumps({k: v for k, v in headers.items() if k != 'Authorization'}, indent=2)}")
            
            response = requests.get(url, headers=headers)
            print(f"Response status: {response.status_code}")
            print(f"Response headers: {json.dumps(dict(response.headers), indent=2)}")
            print(f"Response body: {response.text}")
            
            if response.status_code == 200:
                data = response.json()
                if 'location' in data:
                    return [data['location']]  # Return the location object as a list
                else:
                    print("No location data in response")
                    return []
            else:
                print(f"Error getting location: {response.status_code} - {response.text}")
                return []
        finally:
            db.close()
            
    except Exception as e:
        print(f"Error in get_locations: {str(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return [] 