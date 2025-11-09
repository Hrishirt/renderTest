# Download the helper library from https://www.twilio.com/docs/python/install
import os
from twilio.rest import Client
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import Response
import uvicorn
import threading
import time

load_dotenv()

app = FastAPI()

NGROK_URL = os.getenv("NGROK_URL", "").rstrip("/")

# Initialize Twilio client at module level
account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
client = Client(account_sid, auth_token) if account_sid and auth_token else None
@app.get("/voice")
@app.post("/voice")
def voice():
    return Response(
        content=f'''<Response>
  <Say voice="alice">Press 1 to continue.</Say>
  <Gather input="dtmf" numDigits="1" action="{NGROK_URL}/gather" method="POST" timeout="10">
    <Say>Press 1 now.</Say>
  </Gather>
  <Say>No input received. Goodbye.</Say>
  <Hangup/>
</Response>''',
        media_type="application/xml"
    )

@app.get("/emergency")
@app.post("/emergency")
def emergency():
    return Response(
        content='<Response><Say voice="alice">This is an emergency call from SafeHouse. The user has triggered an emergency alert. Please check on them immediately.</Say><Hangup/></Response>',
        media_type="application/xml"
    )

@app.get("/temperature-alert")
@app.post("/temperature-alert")
def temperature_alert():
    return Response(
        content='<Response><Say voice="alice">Alert from SafeHouse. Your home temperature is dangerously high. This may indicate a heatwave or fire. Please check your home immediately.</Say><Hangup/></Response>',
        media_type="application/xml"
    )

@app.get("/gather")
@app.post("/gather")
async def gather(request: Request):
    digits = ""
    if request.method == "POST":
        form_data = await request.form()
        digits = form_data.get("Digits", "")
    else:
        digits = request.query_params.get("Digits", "")
    
    if digits == "1":
        # Do something when they press 1
        return Response(
            content='<Response><Say voice="alice">You pressed 1. Action completed!</Say><Hangup/></Response>',
            media_type="application/xml"
        )
    else:
        # Make emergency call in background thread
        def make_emergency_call():
            if client:
                try:
                    emergency_call = client.calls.create(
                        url=f"{NGROK_URL}/emergency",
                        to=os.getenv("ABUS_NUMBER"),
                        from_=os.getenv("TWILIO_PHONE_NUMBER")
                    )
                    print(f"Emergency call initiated: {emergency_call.sid}")
                except Exception as e:
                    print(f"Error calling emergency number: {e}")
        
        # Start emergency call in background
        threading.Thread(target=make_emergency_call, daemon=True).start()
        
        # Return hangup response immediately
        return Response(
            content='<Response><Say voice="alice">Emergency noted calling emergency number. Goodbye.</Say><Hangup/></Response>',
            media_type="application/xml"
        )

def check_call_status(call_sid, max_wait=30):
    """Check if a call was answered by a human. Returns True if answered, False otherwise."""
    if not client:
        return False
    
    start_time = time.time()
    in_progress_start = None
    
    while time.time() - start_time < max_wait:
        try:
            call = client.calls(call_sid).fetch()
            status = call.status
            
            # Track when call goes in-progress
            if status == "in-progress":
                if in_progress_start is None:
                    in_progress_start = time.time()
                    print(f"Call went in-progress, waiting to confirm...")
                else:
                    # Check if it's been in-progress for at least 10 seconds
                    time_in_progress = time.time() - in_progress_start
                    if time_in_progress >= 10:
                        print(f"Call has been in-progress for {time_in_progress:.1f} seconds - confirmed answered")
                        return True  # Been in progress for 10+ seconds, definitely answered
            else:
                # Status changed from in-progress, reset
                in_progress_start = None
            
            # If call completed, check duration
            if status == "completed":
                duration = int(call.duration) if call.duration else 0
                answered_by = getattr(call, 'answered_by', None)
                
                print(f"Call completed. Duration: {duration}s, Answered by: {answered_by}")
                
                # Require minimum 15 seconds duration to count as answered
                # Voicemail usually completes quickly or has shorter duration
                if duration >= 15:
                    # If answered_by is available, prefer human answers
                    if answered_by:
                        if answered_by == "human":
                            return True
                        elif answered_by in ["machine", "fax"]:
                            print("Call answered by machine/fax - not counting as answered")
                            return False  # Voicemail or fax, not a real answer
                    else:
                        # No answered_by info, but duration is long enough (15+ seconds)
                        return True
                else:
                    # Duration too short, likely voicemail or not answered
                    print(f"Call duration {duration}s too short - not counting as answered")
                    return False
            
            # If call failed, busy, no-answer, or canceled, it wasn't answered
            if status in ["failed", "busy", "no-answer", "canceled"]:
                print(f"Call status: {status} - not answered")
                return False
            
            time.sleep(2)  # Check every 2 seconds
        except Exception as e:
            print(f"Error checking call status: {e}")
            return False
    
    # If we timeout, assume not answered
    print("Call status check timed out - assuming not answered")
    return False

def escalate_calls():
    """Call user twice, if no answer both times call emergency contact"""
    if not client:
        print("ERROR: Twilio client not initialized")
        return
    
    my_number = os.getenv("MY_PHONE_NUMBER")
    abus_number = os.getenv("ABUS_NUMBER")
    
    if not my_number or not abus_number:
        print("ERROR: MY_PHONE_NUMBER or ABUS_NUMBER not set in .env")
        return
    
    # First call attempt
    print("Making first call attempt...")
    try:
        call1 = client.calls.create(
            url=f"{NGROK_URL}/temperature-alert",
            to=my_number,
            from_=os.getenv("TWILIO_PHONE_NUMBER")
        )
        print(f"First call SID: {call1.sid}")
        
        # Wait and check if answered
        time.sleep(5)  # Give it time to connect
        answered = check_call_status(call1.sid, max_wait=25)
        
        if answered:
            print("First call was answered. No escalation needed.")
            return
        
        print("First call not answered. Making second attempt...")
        
        # Second call attempt
        call2 = client.calls.create(
            url=f"{NGROK_URL}/temperature-alert",
            to=my_number,
            from_=os.getenv("TWILIO_PHONE_NUMBER")
        )
        print(f"Second call SID: {call2.sid}")
        
        # Wait and check if answered
        time.sleep(5)
        answered = check_call_status(call2.sid, max_wait=25)
        
        if answered:
            print("Second call was answered. No escalation needed.")
            return
        
        print("Second call not answered. Escalating to emergency contact...")
        
        # Escalate to emergency contact
        emergency_call = client.calls.create(
            url=f"{NGROK_URL}/emergency",
            to=abus_number,
            from_=os.getenv("TWILIO_PHONE_NUMBER")
        )
        print(f"Emergency call to {abus_number} initiated: {emergency_call.sid}")
        
    except Exception as e:
        print(f"Error in escalation process: {e}")

if __name__ == "__main__":
    if not NGROK_URL:
        print("ERROR: NGROK_URL is not set in your .env file!")
        print("Make sure you have NGROK_URL=https://your-ngrok-url.ngrok-free.dev in your .env")
        exit(1)
    
    # Start server
    threading.Thread(target=lambda: uvicorn.run(app, host="0.0.0.0", port=8001), daemon=True).start()
    time.sleep(2)
    
    # Make the call
    if not client:
        print("ERROR: Twilio credentials not set!")
        exit(1)
    
    # Temperature threshold
    temperature_threshold = 100
    current_temperature = 105  # Change this to your actual temperature reading
    
    # Check if temperature exceeds threshold
    if current_temperature > temperature_threshold:
        print(f"Temperature {current_temperature} exceeds threshold {temperature_threshold}. Triggering alert sequence...")
        escalate_calls()
    else:
        # Normal call flow
        call = client.calls.create(
          url=f"{NGROK_URL}/voice",
          to=os.getenv("MY_PHONE_NUMBER"),
          from_=os.getenv("TWILIO_PHONE_NUMBER")
        )
        print(call.sid)
    
    # Keep server running
    while True:
        time.sleep(1)