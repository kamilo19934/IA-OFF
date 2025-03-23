from flask import redirect, request, session, url_for, render_template, jsonify
import requests
import os
from dotenv import load_dotenv
import sys
from urllib.parse import urlencode
import json
import secrets
from datetime import datetime, timezone
import whisper
import tempfile
from pydub import AudioSegment
import io
import httpx
import ssl
from flask_apscheduler import APScheduler
from app.database import save_token, get_valid_token, refresh_token, Token
from sqlalchemy.orm import Session as SQLAlchemySession

# GoHighLevel API configuration
API_BASE_URL = 'https://services.leadconnectorhq.com'
API_VERSION = '2021-07-28'
GHL_AUTH_URL = 'https://marketplace.gohighlevel.com/oauth/chooselocation'
GHL_TOKEN_URL = 'https://services.leadconnectorhq.com/oauth/token'

# Load environment variables
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
    'locations/customFields.write'
]

# Initialize Whisper model
model = whisper.load_model("base")

# Configuración del Scheduler
scheduler = APScheduler()
scheduler.init_app(app)

def check_token():
    """Función para verificar y refrescar el token si es necesario"""
    with app.app_context():
        try:
            print("\n=== TOKEN CHECK STARTED ===")
            print(f"Time: {datetime.now(timezone.utc).isoformat()}")
            
            token = get_valid_token()
            if token:
                print("✅ Token check: Token is valid")
                print(f"Token: {token[:20]}...")
            else:
                print("⚠️ Token check: Token needs refresh")
                print("Attempting to refresh token...")
                # Intentar refrescar el token
                new_token = refresh_token()
                if new_token:
                    print("✅ Token refreshed successfully")
                else:
                    print("❌ Failed to refresh token")
        except Exception as e:
            print(f"❌ Token check error: {str(e)}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
        print("=== TOKEN CHECK COMPLETED ===\n")

# Configurar el job para que se ejecute cada 5 minutos durante las pruebas
scheduler.add_job(id='check_token', func=check_token, trigger='interval', minutes=5)
scheduler.start()

print("\n=== SCHEDULER STARTED ===")
print("Token check will run every 5 minutes")
print("Press Ctrl+C to stop the application")

class MessageHandler:
    @staticmethod
    def download_audio(url):
        """Download audio file from URL using httpx"""
        try:
            # Create SSL context that ignores certificate verification
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            # Create httpx client with custom SSL context
            client = httpx.Client(
                verify=False,  # Disable SSL verification
                timeout=30.0,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
            )
            
            # First, make a HEAD request to check content type and size
            head_response = client.head(url)
            content_type = head_response.headers.get('content-type', '').lower()
            content_length = int(head_response.headers.get('content-length', 0))
            
            print(f"\nFile information:")
            print(f"Content-Type: {content_type}")
            print(f"Content-Length: {content_length} bytes ({content_length/1024:.2f} KB)")
            
            # Download the file
            response = client.get(url)
            response.raise_for_status()
            
            # Get the content
            content = response.content
            
            # Verify downloaded content size
            if len(content) != content_length:
                print(f"Warning: Downloaded content size ({len(content)} bytes) doesn't match Content-Length header ({content_length} bytes)")
                return None
            
            # If it's an MP4 file, try to extract audio
            if 'video/mp4' in content_type:
                print("Received MP4 file, attempting to extract audio...")
                try:
                    # Create a temporary file for the MP4
                    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_mp4:
                        temp_mp4.write(content)
                        temp_mp4_path = temp_mp4.name
                    
                    # Convert MP4 to MP3 using pydub
                    audio = AudioSegment.from_file(temp_mp4_path)
                    
                    # Create a temporary file for the MP3
                    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_mp3:
                        temp_mp3_path = temp_mp3.name
                    
                    # Export as MP3
                    audio.export(temp_mp3_path, format="mp3")
                    
                    # Read the MP3 content
                    with open(temp_mp3_path, 'rb') as f:
                        audio_content = f.read()
                    
                    # Clean up temporary files
                    os.unlink(temp_mp4_path)
                    os.unlink(temp_mp3_path)
                    
                    print("Successfully extracted audio from MP4")
                    return audio_content
                    
                except Exception as e:
                    print(f"Error extracting audio from MP4: {str(e)}")
                    if os.path.exists(temp_mp4_path):
                        os.unlink(temp_mp4_path)
                    if os.path.exists(temp_mp3_path):
                        os.unlink(temp_mp3_path)
                    return None
            
            # For other content types, check if they're audio
            elif 'audio' not in content_type and 'octet-stream' not in content_type:
                print(f"Warning: Unexpected content type: {content_type}")
                return None
            
            # Close the client
            client.close()
            
            return content
            
        except Exception as e:
            print(f"Error downloading with httpx: {str(e)}")
            return None

    @staticmethod
    def transcribe_audio(audio_content):
        """Transcribe audio using Whisper"""
        try:
            # Set OpenAI API key
            os.environ["OPENAI_API_KEY"] = os.getenv('OPENAI_API_KEY')
            
            # Create a temporary file to store the audio
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
                temp_file.write(audio_content)
                temp_file_path = temp_file.name
            
            # Create SSL context that ignores certificate verification
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            # Monkey patch urllib to use our SSL context
            import urllib.request
            original_urlopen = urllib.request.urlopen
            def custom_urlopen(*args, **kwargs):
                kwargs['context'] = ssl_context
                return original_urlopen(*args, **kwargs)
            urllib.request.urlopen = custom_urlopen
            
            try:
                # Suppress FP16 warning
                import warnings
                warnings.filterwarnings("ignore", message="FP16 is not supported on CPU; using FP32 instead")
                
                # Load model and transcribe
                model = whisper.load_model("base")
                result = model.transcribe(temp_file_path)
                
                # Clean up the temporary file
                os.unlink(temp_file_path)
                
                # Format the transcription
                transcription = result["text"].strip()
                duration = result.get("duration", 0)
                
                print(f"\nTranscription details:")
                print(f"Duration: {duration:.2f} seconds")
                print(f"Text: {transcription}")
                
                return transcription
            finally:
                # Restore original urlopen
                urllib.request.urlopen = original_urlopen
                
        except Exception as e:
            print(f"Error transcribing audio: {str(e)}")
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return None

    @staticmethod
    def send_inbound_message(conversation_id, message, message_type, attachments=None):
        """Send an inbound message with transcription to GoHighLevel"""
        try:
            url = f"{API_BASE_URL}/conversations/messages/inbound"
            
            # Obtener un token válido
            access_token = get_valid_token()
            if not access_token:
                print("Error: No valid token available")
                return None
                
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Version": API_VERSION
            }
            
            payload = {
                "type": message_type,
                "message": message,
                "conversationId": conversation_id,
                "direction": "inbound",
                "date": datetime.now(timezone.utc).isoformat()
            }
            
            if attachments:
                payload["attachments"] = attachments
            
            print("\n=== SENDING INBOUND MESSAGE ===")
            print(f"URL: {url}")
            print(f"Headers: {json.dumps({k: v for k, v in headers.items() if k != 'Authorization'}, indent=2)}")
            print(f"Payload: {json.dumps(payload, indent=2)}")
            
            try:
                response = requests.post(url, headers=headers, json=payload)
                print(f"\nResponse status: {response.status_code}")
                print(f"Response headers: {dict(response.headers)}")
                print(f"Response body: {response.text}")
                
                if response.status_code != 200:
                    print(f"Error response: {response.text}")
                    return None
                
                return response.json()
                
            except requests.exceptions.RequestException as e:
                print(f"Request error: {str(e)}")
                return None
            
        except Exception as e:
            print(f"Error sending inbound message: {str(e)}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
            return None

    @staticmethod
    def process_attachments(attachments, conversation_id, message_type):
        """Process attachments and transcribe audio files"""
        transcriptions = []
        
        for attachment_url in attachments:
            print(f"\nProcessing attachment: {attachment_url}")
            
            # Download the audio file
            audio_content = MessageHandler.download_audio(attachment_url)
            if not audio_content:
                print("Failed to download audio file")
                continue
            
            print("Audio file downloaded successfully")
            
            # Transcribe the audio
            transcription = MessageHandler.transcribe_audio(audio_content)
            if transcription:
                transcriptions.append({
                    'url': attachment_url,
                    'transcription': transcription
                })
                
                # Send transcription as inbound message
                message = f"🎯 Transcripción del audio:\n\n{transcription}"
                MessageHandler.send_inbound_message(conversation_id, message, message_type)
            else:
                print("Failed to transcribe audio")
        
        return transcriptions

    @staticmethod
    def print_webhook_data(data, message_type):
        """Print all webhook data in a structured way"""
        print("\n" + "="*50)
        print(f"WEBHOOK RECEIVED - {message_type}")
        print("="*50)
        
        # Print all fields from the webhook data
        for key, value in data.items():
            if isinstance(value, (list, dict)):
                print(f"{key}: {json.dumps(value, indent=2)}")
            else:
                print(f"{key}: {value}")
        
        # Process attachments if present
        attachments = data.get('attachments', [])
        if attachments:
            print("\nProcessing attachments...")
            transcriptions = MessageHandler.process_attachments(attachments, data.get('conversationId'), data.get('type'))
            if transcriptions:
                print("\nTranscriptions:")
                for trans in transcriptions:
                    print(f"\nURL: {trans['url']}")
                    print(f"Transcription: {trans['transcription']}")
        
        print("="*50 + "\n")

    @staticmethod
    def handle_outbound_message(data):
        """Handle outbound message webhook"""
        MessageHandler.print_webhook_data(data, "OutboundMessage")
        return True

    @staticmethod
    def handle_inbound_message(data):
        """Handle inbound message webhook"""
        MessageHandler.print_webhook_data(data, "InboundMessage")
        return True

@app.route('/')
def index():
    """Render the index page"""
    locations = get_locations() if session.get('access_token') else []
    return render_template('index.html', locations=locations)

@app.route('/logout')
def logout():
    """Clear the session and redirect to index"""
    session.clear()
    return redirect(url_for('index'))

def get_locations():
    """Get list of locations from GoHighLevel"""
    try:
        access_token = get_valid_token()
        if not access_token:
            return []
            
        # Get location ID from the token in database
        db = SQLAlchemySession()
        try:
            token = db.query(Token).order_by(Token.created_at.desc()).first()
            if not token or not token.location_id:
                print("No location ID found in token")
                return []
                
            location_id = token.location_id
            print(f"\nUsing location ID: {location_id}")
            
            url = f"{API_BASE_URL}/locations/{location_id}"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Version": API_VERSION
            }
            
            print(f"\nFetching location details from: {url}")
            response = requests.get(url, headers=headers)
            print(f"Response status: {response.status_code}")
            print(f"Response body: {response.text}")
            
            if response.status_code == 200:
                location = response.json()
                return [location]  # Return as a list to maintain compatibility
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

@app.route('/login')
def login():
    """Redirect to GoHighLevel OAuth page"""
    try:
        # Generate state for CSRF protection
        state = secrets.token_urlsafe(16)
        session['oauth_state'] = state
        
        # Build the authorization URL
        params = {
            'client_id': os.getenv('GHL_CLIENT_ID'),
            'redirect_uri': os.getenv('GHL_REDIRECT_URI'),
            'response_type': 'code',
            'scope': ' '.join(SCOPES)  # Use all scopes
        }
        
        # Add state parameter
        params['state'] = state
        
        # Construct the full URL with parameters
        auth_url = f"{GHL_AUTH_URL}?{urlencode(params)}"
        print(f"\nAuthorization URL: {auth_url}")
        
        return redirect(auth_url)
    except Exception as e:
        print(f"Error in login route: {str(e)}")
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
            "client_id": os.getenv('GHL_CLIENT_ID'),
            "client_secret": os.getenv('GHL_CLIENT_SECRET'),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": os.getenv('GHL_REDIRECT_URI')
        }
        
        print("\n=== TOKEN REQUEST ===")
        print(f"Token URL: {token_url}")
        print(f"Request data: {json.dumps({k: v for k, v in token_data.items() if k != 'client_secret'}, indent=2)}")
        
        response = requests.post(token_url, data=token_data)
        print(f"\nResponse status: {response.status_code}")
        print(f"Response body: {response.text}")
        
        response.raise_for_status()
        token_info = response.json()
        
        # Save token without location_id (it will be updated when we receive the webhook)
        token = save_token(token_info)
        if not token:
            print("Failed to save token")
            return "Failed to save token", 500
            
        # Store tokens in session
        session['access_token'] = token_info['access_token']
        session['refresh_token'] = token_info.get('refresh_token')
        
        print("\n=== TOKEN SAVED ===")
        print(f"Access Token: {token_info['access_token'][:20]}...")
        print(f"Refresh Token: {token_info.get('refresh_token', '')[:20]}...")
        
        return redirect(url_for('index'))
        
    except Exception as e:
        print(f"Error in callback: {str(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return f"Error: {str(e)}", 500

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
                db = SQLAlchemySession()
                try:
                    # Get the most recent token
                    token = db.query(Token).order_by(Token.created_at.desc()).first()
                    if token:
                        token.location_id = location_id
                        db.commit()
                        print(f"Updated token with location ID: {location_id}")
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
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        print(f"Exception type: {type(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True) 