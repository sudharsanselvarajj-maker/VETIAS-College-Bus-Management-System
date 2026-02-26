import os
import smtplib
import socket
import ssl
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

def test_smtp():
    print("="*50)
    print("🚀 VET IAS SYSTEM: SMTP DIAGNOSTIC TOOL")
    print("="*50)

    # 1. Load Configuration
    server_addr = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    port = int(os.environ.get('SMTP_PORT', 587))
    user = os.environ.get('SMTP_USER')
    password = os.environ.get('SMTP_PASSWORD')

    if not user or not password:
        print("❌ ERROR: SMTP_USER or SMTP_PASSWORD not set in environment!")
        print("Please check your .env file or Render environment variables.")
        return

    print(f"📍 Configuration:")
    print(f"   - Server: {server_addr}")
    print(f"   - Port: {port}")
    print(f"   - User: {user}")
    print(f"   - Password: {'*'*len(password) if password else 'MISSING'}")
    print("-" * 50)

    # 2. Test DNS/Connectivity
    print(f"🔍 Testing connectivity to {server_addr}...")
    try:
        host_ip = socket.gethostbyname(server_addr)
        print(f"✅ Host resolved to {host_ip}")
    except Exception as e:
        print(f"❌ DNS ERROR: Could not resolve {server_addr}. Check your internet/DNS settings.")
        print(f"Details: {e}")
        return

    # 3. Method 1: SSL on 465
    print("\n🔵 TESTING METHOD 1: SSL (Port 465)")
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(server_addr, 465, context=context, timeout=10) as server:
            print("   - Connection established.")
            server.login(user, password)
            print("   - Login successful!")
            
            # Send Test Message
            msg = MIMEMultipart()
            msg['Subject'] = "VET IAS: SMTP Diagnostic Test (SSL 465)"
            msg['From'] = f"Diagnostic Tool <{user}>"
            msg['To'] = user
            msg.attach(MIMEText("This is a test to verify SMTP SSL 465 connectivity.", 'plain'))
            
            server.send_message(msg)
            print("   ✅ TEST EMAIL SENT SUCCESSFULLY via 465!")
    except Exception as e:
        print(f"   ❌ METHOD 1 FAILED: {e}")

    # 4. Method 2: STARTTLS on 587
    print("\n🔴 TESTING METHOD 2: STARTTLS (Port 587)")
    try:
        server = smtplib.SMTP(server_addr, 587, timeout=10)
        print("   - Connection established.")
        server.starttls()
        print("   - STARTTLS handshake successful.")
        server.login(user, password)
        print("   - Login successful!")
        
        # Send Test Message
        msg = MIMEMultipart()
        msg['Subject'] = "VET IAS: SMTP Diagnostic Test (STARTTLS 587)"
        msg['From'] = f"Diagnostic Tool <{user}>"
        msg['To'] = user
        msg.attach(MIMEText("This is a test to verify SMTP STARTTLS 587 connectivity.", 'plain'))
        
        server.send_message(msg)
        print("   ✅ TEST EMAIL SENT SUCCESSFULLY via 587!")
        server.quit()
    except Exception as e:
        print(f"   ❌ METHOD 2 FAILED: {e}")

    print("\n" + "="*50)
    print("⭐ DIAGNOSTIC COMPLETE")
    print("="*50)

if __name__ == "__main__":
    test_smtp()
