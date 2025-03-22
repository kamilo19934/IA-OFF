from flask import Flask, redirect, request, session, url_for, render_template, jsonify
from flask_session import Session
import requests
import os
from dotenv import load_dotenv
import sys
from urllib.parse import urlencode
import json
import secrets
from datetime import datetime
import whisper
import tempfile
from pydub import AudioSegment
import io
import httpx
import ssl

def init_env():
    """Initialize and verify environment variables"""
    # Load environment variables
    load_dotenv(override=True)
    
    # Required environment variables
    required_vars = ['GHL_CLIENT_ID', 'GHL_CLIENT_SECRET', 'FLASK_SECRET_KEY', 'OPENAI_API_KEY']
    
    # Check if all required variables are set
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print("Error: Missing required environment variables:")
        for var in missing_vars:
            print(f"- {var}")
        print("\nPlease check your .env file and ensure all required variables are set.")
        sys.exit(1)
    
    # Print loaded variables (without sensitive data)
    print("\nEnvironment variables loaded successfully:")
    print(f"GHL_CLIENT_ID: {os.getenv('GHL_CLIENT_ID')}")
    print(f"GHL_REDIRECT_URI: {os.getenv('GHL_REDIRECT_URI', 'http://localhost:5000/callback')}")
    print(f"FLASK_SECRET_KEY: {'*' * 20}")  # Don't print the actual secret key
    print(f"OPENAI_API_KEY: {'*' * 20}")  # Don't print the actual API key

# Initialize environment variables
init_env()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY')
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

# GoHighLevel OAuth Configuration
GHL_CLIENT_ID = os.getenv('GHL_CLIENT_ID')
GHL_CLIENT_SECRET = os.getenv('GHL_CLIENT_SECRET')
GHL_REDIRECT_URI = os.getenv('GHL_REDIRECT_URI', 'http://localhost:5000/callback')
GHL_AUTH_URL = 'https://marketplace.gohighlevel.com/oauth/chooselocation'
GHL_TOKEN_URL = 'https://services.leadconnectorhq.com/oauth/token'  # Updated to use the correct endpoint

# Debug print
print(f"Client ID: {GHL_CLIENT_ID}")
print(f"Client Secret: {GHL_CLIENT_SECRET}")
print(f"Redirect URI: {GHL_REDIRECT_URI}")

# Scopes for the application
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
    'locations/customValues.readonly'
]

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
    def process_attachments(attachments):
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
            transcriptions = MessageHandler.process_attachments(attachments)
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
    if 'access_token' not in session:
        return render_template('login.html')
    return render_template('dashboard.html')

@app.route('/login')
def login():
    if not GHL_CLIENT_ID:
        return "Error: GHL_CLIENT_ID no está configurado", 500
    
    # Generate state parameter for security
    state = secrets.token_urlsafe(16)
    session['oauth_state'] = state
    
    # Prepare the authorization parameters
    params = {
        'client_id': GHL_CLIENT_ID,
        'redirect_uri': GHL_REDIRECT_URI,
        'response_type': 'code',
        'scope': ' '.join(SCOPES),
        'state': state  # Add state parameter
    }
    
    # Construct the authorization URL with proper encoding
    auth_url = f"{GHL_AUTH_URL}?{urlencode(params)}"
    
    # Debug prints
    print("\nAuthorization URL details:")
    print(f"Base URL: {GHL_AUTH_URL}")
    print(f"Parameters: {params}")
    print(f"Final URL: {auth_url}")
    
    return redirect(auth_url)

@app.route('/callback')
def callback():
    # Verify state parameter
    state = request.args.get('state')
    if not state or state != session.get('oauth_state'):
        return 'Error: Invalid state parameter', 400

    code = request.args.get('code')
    if not code:
        print("Error: No code received in callback")
        return 'Error: No code received', 400

    print(f"\nReceived code: {code}")

    # Exchange code for access token
    token_data = {
        'client_id': GHL_CLIENT_ID,
        'client_secret': GHL_CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': GHL_REDIRECT_URI,
        'user_type': 'Location'  # Added user_type parameter as required by the API
    }

    print("\nToken request details:")
    print(f"Token URL: {GHL_TOKEN_URL}")
    print(f"Request data: {json.dumps({k: v for k, v in token_data.items() if k != 'client_secret'}, indent=2)}")

    try:
        # Add headers to specify we want JSON response
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json'
        }
        
        response = requests.post(GHL_TOKEN_URL, data=token_data, headers=headers)
        print(f"\nResponse status code: {response.status_code}")
        print(f"Response headers: {dict(response.headers)}")
        print(f"Raw response: {response.text}")
        print(f"Response content type: {response.headers.get('content-type', 'Not specified')}")

        if response.status_code != 200:
            print(f"Error response: {response.text}")
            return f'Error: {response.text}', 400

        # Try to decode the response as JSON
        try:
            token_info = response.json()
            print(f"Decoded JSON response: {json.dumps(token_info, indent=2)}")
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON response: {e}")
            print(f"Raw response content: {response.text}")
            print(f"Response content type: {response.headers.get('content-type', 'Not specified')}")
            return f'Error: Invalid JSON response from server. Response: {response.text}', 500

        if 'access_token' not in token_info:
            print(f"Error: No access token in response. Response: {token_info}")
            return f'Error: No access token in response. Response: {json.dumps(token_info)}', 500

        session['access_token'] = token_info['access_token']
        session['refresh_token'] = token_info.get('refresh_token')
        session['expires_in'] = token_info.get('expires_in')
        session['location_id'] = token_info.get('locationId')  # Store location ID if available
        session['company_id'] = token_info.get('companyId')  # Store company ID if available

        print("\nToken exchange successful!")
        return redirect(url_for('index'))

    except requests.exceptions.RequestException as e:
        print(f"\nRequest error: {str(e)}")
        return f'Error during request: {str(e)}', 500
    except Exception as e:
        print(f"\nUnexpected error: {str(e)}")
        return f'Unexpected error: {str(e)}', 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming webhooks from GoHighLevel"""
    try:
        # Get the webhook data
        data = request.get_json()
        
        if not data:
            print("Error: No data received in webhook")
            return jsonify({'error': 'No data received'}), 400
        
        # Get the message type
        message_type = data.get('type')
        
        # Handle different message types
        if message_type == 'OutboundMessage':
            success = MessageHandler.handle_outbound_message(data)
        elif message_type == 'InboundMessage':
            success = MessageHandler.handle_inbound_message(data)
        else:
            print(f"Unknown message type: {message_type}")
            return jsonify({'error': 'Unknown message type'}), 400
        
        if success:
            return jsonify({'status': 'success'}), 200
        else:
            return jsonify({'error': 'Failed to process message'}), 500
            
    except Exception as e:
        print(f"Error processing webhook: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True) 