import os
import datetime
import json
import math
import smtplib
import threading
import logging
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_session import Session
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

# Configure Logging
logging.basicConfig(
    filename='system_activity.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s'
)

# Load Environment Variables
load_dotenv()

db = SQLAlchemy()

# Initialize App
app = Flask(__name__)

# Absolute Path Configuration for Database
basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, 'instance', 'smart_bus.db')
if not os.path.exists(os.path.dirname(db_path)):
    os.makedirs(os.path.dirname(db_path))

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'secure_smart_bus_secret_key_123')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_TYPE'] = 'filesystem'
SKIP_DEVICE_CHECK = os.environ.get('SKIP_DEVICE_CHECK', 'False') == 'True'

db.init_app(app)
Session(app)

# VERSION STAMP FOR RENDER LOGS
print("\n" + "="*50)
print("🚀 VET IAS SYSTEM: VERSION 3.0 (CLOUD HARDENED) STARTED")
print(f"   SMTP USER: {os.environ.get('SMTP_USER', 'NOT SET')}")
print("="*50 + "\n")

# In-Memory Cache for Bus Locations (Energy Efficient - No DB Write)
BUS_LOCATION_CACHE = {} 


# --------------------------
# Database Models
# --------------------------

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True) 
    student_id_str = db.Column(db.String(50), unique=True, nullable=True) 
    phone = db.Column(db.String(20), nullable=True)
    department = db.Column(db.String(100), nullable=True)
    year = db.Column(db.String(20), nullable=True)
    semester = db.Column(db.String(20), nullable=True)
    address = db.Column(db.Text, nullable=True)
    stop_location = db.Column(db.String(100), nullable=True)
    parent_name = db.Column(db.String(100), nullable=True) 
    # Unique ID for the student's primary device (browser fingerprint hash)
    device_id = db.Column(db.String(200), unique=True, nullable=True) 
    parent_email = db.Column(db.String(120), nullable=False)
    parent_phone = db.Column(db.String(20), nullable=True) # New Field for SMS
    fee_status = db.Column(db.String(20), default='Paid') # Paid, Unpaid, Pending
    bus_no = db.Column(db.String(20), nullable=False)
    password = db.Column(db.String(100), nullable=False) # Simple password for demo

class BusLive(db.Model):
    bus_no = db.Column(db.String(20), primary_key=True)
    driver_name = db.Column(db.String(100))
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    last_updated = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'), nullable=False)
    student_name = db.Column(db.String(100)) # Cached for easier reporting
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    method = db.Column(db.String(50)) # 'QR' or 'Barcode'
    loc_verified = db.Column(db.Boolean, default=False)
    bus_no = db.Column(db.String(20))
    # New Columns for Enhanced Tracking
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    device_id = db.Column(db.String(200)) # To check against Student's registered device
    entry_method = db.Column(db.String(20)) # QR, MANUAL, BARCODE
    verification_status = db.Column(db.String(50)) # VERIFIED, FLAGGED

class SystemAudit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(200), nullable=False)
    admin_name = db.Column(db.String(100))
    student_id = db.Column(db.Integer)
    reason = db.Column(db.String(200))
    timestamp = db.Column(db.DateTime, default=datetime.datetime.now)

class Complaint(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'))
    subject = db.Column(db.String(200), nullable=False, default="General Issue")
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='Pending')
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class NotificationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recipient = db.Column(db.String(120), nullable=False)
    type = db.Column(db.String(20)) # 'Email' or 'SMS'
    subject = db.Column(db.String(200))
    status = db.Column(db.String(20)) # 'Sent' or 'Failed'
    error_message = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)

# --------------------------
# Helper Functions
# --------------------------

def haversine(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points 
    on the earth (specified in decimal degrees)
    Returns distance in meters.
    """
    # Convert decimal degrees to radians 
    lat1, lon1, lat2, lon2 = map(math.radians, [float(lat1), float(lon1), float(lat2), float(lon2)])

    # Haversine formula 
    dlon = lon2 - lon1 
    dlat = lat2 - lat1 
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a)) 
    r = 6371000 # Radius of earth in meters
    return c * r

# --------------------------
# Notification Service Layer
# --------------------------

class NotificationService:
    @staticmethod
    def log_notification(recipient, n_type, subject, status, error=None):
        try:
            log = NotificationLog(
                recipient=recipient,
                type=n_type,
                subject=subject,
                status=status,
                error_message=str(error) if error else None
            )
            db.session.add(log)
            db.session.commit()
        except Exception as e:
            print(f"FAILED TO LOG NOTIFICATION: {e}")
            sys.stdout.flush()

    @staticmethod
    def send_parent_email(parent_email, student_name, bus_no, timestamp, date):
        # Environment Variable Hardening (Defaults for Render)
        smtp_user = os.environ.get('SMTP_USER')
        smtp_pass = os.environ.get('SMTP_PASSWORD')
        smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.environ.get('SMTP_PORT', 587))

        if not smtp_user or not smtp_pass:
            print("[CRITICAL ERROR] SMTP Credentials missing in environment variables!")
            sys.stdout.flush()
            return False

        if os.environ.get('EMAIL_MODE', 'True') != 'True':
            print("[EMAIL MODE OFF] Skipping email dispatch.")
            sys.stdout.flush()
            return False
            
        if not parent_email or '@' not in parent_email:
            print(f"INVALID EMAIL DETECTED: {parent_email}")
            NotificationService.log_notification(parent_email or "Unknown", 'Email', "Boarding Confirmation", 'Failed', "Missing/Invalid Email")
            return False

        to_email = parent_email
        subject = f"🚌 VET IAS Transport: Boarding Confirmation ({student_name})"
        
        # Professional Message Template
        body = f"""Dear Parent,

This is to inform you that your ward {student_name} has successfully boarded Bus {bus_no} at {timestamp} on {date}.
Attendance has been securely recorded in the system.

Regards,
VET Institute of Arts and Science
Transport Administration
"""
        
        # HTML Version for Bonus
        html_body = f"""
        <html>
            <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; border: 1px solid #eee; border-radius: 10px; overflow: hidden;">
                <div style="background-color: #004d40; color: white; padding: 20px; text-align: center;">
                    <h2 style="margin: 0;">Boarding Confirmation</h2>
                </div>
                <div style="padding: 30px;">
                    <p>Dear Parent,</p>
                    <p>This is to inform you that your ward <strong>{student_name}</strong> has successfully boarded <strong>Bus {bus_no}</strong> at <strong>{timestamp}</strong> on <strong>{date}</strong>.</p>
                    <p>Attendance has been securely recorded in our smart transport system using geofence verification.</p>
                    <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; font-size: 0.9em; color: #777;">
                        <p>Regards,<br><strong>VET Institute of Arts and Science</strong><br>Transport Administration</p>
                    </div>
                </div>
                <div style="background-image: linear-gradient(to right, #f9f9f9, #fff); padding: 15px; text-align: center; font-size: 0.8em; color: #999;">
                    <p>This is an automated security notification. Do not reply to this email.</p>
                </div>
            </body>
        </html>
        """

        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = Header(subject, 'utf-8')
            msg['From'] = f"VET IAS Transport Alerts <{os.environ.get('SMTP_USER')}>"
            msg['To'] = to_email

            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_body, 'html', 'utf-8'))

            # Setup SMTP Connection
            print(f"[SMTP] Connecting to {smtp_server}:{smtp_port}...")
            sys.stdout.flush()
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.set_debuglevel(1) # Enable debug for console logs
            server.starttls()
            
            print(f"[SMTP] Attempting login for {smtp_user}...")
            sys.stdout.flush()
            server.login(smtp_user, smtp_pass)
            
            print(f"[SMTP] Handshake successful. Sending message...")
            sys.stdout.flush()
            server.send_message(msg)
            server.quit()

            print(f"EMAIL SENT SUCCESSFULLY TO: {to_email}")
            sys.stdout.flush()
            NotificationService.log_notification(to_email, 'Email', subject, 'Sent')
            return True

        except Exception as e:
            print(f"EMAIL DISPATCH ERROR: {str(e)}")
            sys.stdout.flush()
            NotificationService.log_notification(to_email, 'Email', subject, 'Failed', str(e))
            return False

def send_parent_sms(student, bus_no, timestamp, date):
    """
    Mock SMS function. In production, use Twilio/Other API.
    """
    target = student.parent_phone or student.parent_email
    msg = f"Dear Parent,\n\nThis is to inform you that your ward {student.name} has successfully boarded the college bus (Bus No: {bus_no}) at {timestamp} on {date}.\n\nWe appreciate your trust in our secure transport system."
    print(f"--- SMS SIMULATION ---")
    print(f"To: {target} (SMS)")
    print(f"Message:\n{msg}")
    print(f"----------------------")

def send_fee_reminder_sms(student):
    """
    Mock Fee Reminder SMS.
    """
    target = student.parent_phone or student.parent_email
    msg = f"URGENT: Dear Parent, the college bus fee for {student.name} is PENDING. Please settle it immediately to avoid service interruption. - VET IAS Account Office."
    print(f"--- SMS SIMULATION (FEE) ---")
    print(f"To: {target} (SMS)")
    print(f"Message: {msg}")
    print(f"----------------------")

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("user_id") is None:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --------------------------
# Routes
# --------------------------

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        fullname = request.form.get('fullname')
        email = request.form.get('email')
        student_id = request.form.get('studentId')
        phone = request.form.get('phone')
        department = request.form.get('department')
        year = request.form.get('year')
        semester = request.form.get('semester')
        address = request.form.get('address')
        bus_route = request.form.get('busRoute')
        stop_location = request.form.get('stopLocation')
        emergency_contact_name = request.form.get('emergencyContactName')
        parent_phone = request.form.get('parent_phone')
        parent_email = request.form.get('parent_email')
        password = request.form.get('password')

        # Check if already exists
        if Student.query.filter((Student.email == email) | (Student.student_id_str == student_id)).first():
            return render_template('register.html', error="Email or Student ID already registered.")

        new_student = Student(
            name=fullname,
            email=email,
            student_id_str=student_id,
            phone=phone,
            department=department,
            year=year,
            semester=semester,
            address=address,
            bus_no=bus_route,
            stop_location=stop_location,
            parent_phone=parent_phone,
            parent_name=emergency_contact_name,
            parent_email=parent_email, 
            password=generate_password_hash(password)
        )
        db.session.add(new_student)
        db.session.commit()
        return redirect(url_for('login', success="Account created successfully! Please sign in."))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    success = request.args.get('success')
    if request.method == 'POST':
        # ... existing login logic ...
        user_type = request.form.get('user_type')
        username = request.form.get('username') # For student, this is ID. For driver/admin, it's name
        password = request.form.get('password')
        device_id = request.form.get('device_id') # From JS

        if user_type == 'student':
            # Support Login by Name, ID, String ID, or Email
            student = Student.query.filter(
                (Student.name == username) | 
                (Student.id == username) | 
                (Student.student_id_str == username) |
                (Student.email == username)
            ).first()
            if student and check_password_hash(student.password, password):
                # Device Binding Check
                print(f"[DEBUG LOGIN] Student: {student.name}, DB Device: {student.device_id}, Incoming Device: {device_id}")
                if student.device_id and not SKIP_DEVICE_CHECK:
                    if student.device_id != device_id:
                        print(f"[DEBUG LOGIN] MISMATCH: DB holds {student.device_id}, client sent {device_id}")
                        return render_template('login.html', error="Login Failed: New device detected. Please use your registered device.")
                else:
                    # First time login, bind device
                    if not device_id:
                        return render_template('login.html', error="Security Error: Device identity could not be verified. Please enable JavaScript.")
                    
                    try:
                        print(f"[DEBUG LOGIN] BINDING NEW DEVICE: {device_id}")
                        student.device_id = device_id
                        db.session.commit()
                        print(f"[DEBUG LOGIN] BINDING SUCCESS")
                    except Exception as e:
                        db.session.rollback()
                        print(f"[DEBUG LOGIN] BINDING FAILED (Collision or Rule): {str(e)}")
                        # This happens if the device is already bound to another student (Hardware Lock)
                        return render_template('login.html', error="Locked: This device is already registered to another account. Contact Admin to reset.")
                
                session['user_id'] = student.id
                session['user_type'] = 'student'
                session['name'] = student.name
                return redirect(url_for('student_dashboard'))
        
        elif user_type == 'driver':
            # Hardcoded driver for demo
            if username == 'driver' and password == 'pass':
                session['user_id'] = 999
                session['user_type'] = 'driver'
                session['bus_no'] = 'Bus-10' # Assigned bus
                return redirect(url_for('driver_dashboard'))

        elif user_type == 'admin':
             if username == 'admin' and password == 'admin':
                session['user_id'] = 1
                session['user_type'] = 'admin'
                return redirect(url_for('admin_dashboard'))

        return render_template('login.html', error="Invalid Credentials")

    return render_template('login.html', success=success)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- ATTENDANCE & STUDENT ---

@app.route('/student')
@login_required
def student_dashboard():
    if session['user_type'] != 'student': return redirect('/')
    student = Student.query.get(session['user_id'])
    history = Attendance.query.filter_by(student_id=student.id).order_by(Attendance.timestamp.desc()).all()
    return render_template('student.html', student=student, history=history)

@app.route('/api/mark-attendance', methods=['POST'])
@login_required
def mark_attendance():
    data = request.json
    qr_data = data.get('qr_data')
    lat = data.get('lat')
    lng = data.get('lng')
    
    student = Student.query.get(session['user_id'])
    
    # 1. Decode QR Data
    try:
        bus_no_qr, timestamp_qr = qr_data.split('_', 1)
    except Exception as e:
        print(f"[FAIL] QR Decode Error: {e}")
        return jsonify({'status': 'error', 'message': 'Invalid QR Code'})

    bus_live = BusLive.query.filter_by(bus_no=bus_no_qr).first()
    cached_loc = BUS_LOCATION_CACHE.get(bus_no_qr)
    
    master_lat, master_lng = 0, 0
    if cached_loc:
        master_lat = cached_loc['lat']
        master_lng = cached_loc['lng']
    elif bus_live:
        master_lat = bus_live.lat
        master_lng = bus_live.lng
    else:
        print(f"[FAIL] Bus {bus_no_qr} location not available in DB or Cache.")
        return jsonify({'status': 'error', 'message': 'Bus not active/Syncing...'})

    # 3. Geofence Check (Strict 15m)
    distance = haversine(lat, lng, master_lat, master_lng)
    
    # SYSTEM LOGS (VERY IMPORTANT FOR USER)
    print(f"\n[SYSTEM GEO] Student: ({lat}, {lng}) | Bus: ({master_lat}, {master_lng})")
    print(f"[SYSTEM GEO] Distance: {distance:.2f} meters (Limit: 15m)")
    
    if distance > 15: 
         print(f"[FAIL] Geofence Blocked: {student.name} is too far ({int(distance)}m).")
         return jsonify({'status': 'error', 'message': f'Geofence Failed! Too far from bus ({int(distance)}m).'}) 

    # 4. Device Binding
    current_device = data.get('device_id')
    if not current_device:
        print("[FAIL] Missing Device ID.")
        return jsonify({'status': 'error', 'message': 'Missing Device Identifier'})

    if not student.device_id:
        student.device_id = current_device
        db.session.commit()
    elif student.device_id != current_device:
        print(f"[FAIL] Security Breach: {student.name} device mismatch.")
        return jsonify({'status': 'error', 'message': 'Security Breach: Device Mismatch.'}), 403
    
    # 5. Mark Attendance
    print(f"[SUCCESS] Marking attendance for {student.name} on {bus_no_qr}...")
    new_att = Attendance(
        student_id=student.id,
        student_name=student.name,
        method='QR',
        loc_verified=True, 
        bus_no=bus_no_qr,
        latitude=lat,
        longitude=lng,
        device_id=data.get('device_id', 'Unknown'),
        entry_method='QR',
        verification_status='VERIFIED'
    )
    db.session.add(new_att)
    db.session.commit()

    # 5. Notify Parent - ASYNC
    p_email = student.parent_email
    s_name = student.name
    now = datetime.datetime.now()
    time_str = now.strftime('%H:%M')
    date_str = now.strftime('%d-%m-%Y')

    def async_notification_wrapper(app_inst, email_addr, name_str, b_no, t_str, d_str):
        with app_inst.app_context():
            print(f"[ASYNC] ENGINE TRIGGERED for {name_str} at {email_addr}")
            print(f"[ASYNC] Triggering COMPULSORY email via SMTP...")
            sys.stdout.flush()
            NotificationService.send_parent_email(email_addr, name_str, b_no, t_str, d_str)

    threading.Thread(
        target=async_notification_wrapper, 
        args=(app, p_email, s_name, bus_no_qr, time_str, date_str)
    ).start()
    
    sys.stdout.flush()
    return jsonify({'status': 'success', 'message': 'Attendance Marked Successfully'})

@app.route('/api/submit-complaint', methods=['POST'])
@login_required
def submit_complaint():
    student_id = session['user_id']
    data = request.json
    subject = data.get('subject', 'General')
    msg = data.get('message')
    
    if msg:
        db.session.add(Complaint(student_id=student_id, subject=subject, message=msg))
        db.session.commit()
        return jsonify({'status':'success'})
    return jsonify({'status':'error'})

# --- DRIVER ---

@app.route('/driver')
@login_required
def driver_dashboard():
    if session['user_type'] != 'driver': return redirect('/')
    bus_no = session.get('bus_no', 'Bus-10')
    return render_template('driver.html', bus_no=bus_no)

@app.route('/api/update-master-location', methods=['POST'])
def update_master_location():
    # Deprecated manual endpoint, keeping for fallback compatibility
    data = request.json
    bus_no = data.get('bus_no')
    # ... logic ...
    return jsonify({'status': 'success', 'message': 'Manual Check-in Deprecated but Kept'})

@app.route('/api/driver-heartbeat', methods=['POST'])
@login_required
def driver_heartbeat():
    """
    Automated Heartbeat: Updates RAM Cache. 
    ZERO DB I/O for efficiency.
    """
    data = request.json
    
    # Security: Ensure it's a driver
    if session.get('user_type') != 'driver':
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
        
    bus_no = session.get('bus_no') # Trust session over payload
    lat = data.get('lat')
    lng = data.get('lng')

    # Update Memory
    BUS_LOCATION_CACHE[bus_no] = {
        'lat': lat,
        'lng': lng,
        'timestamp': datetime.datetime.now()
    }
    print(f"[DEBUG DRIVER] Bus {bus_no} Updated: {lat}, {lng}")
    
    # PERSIST TO DB (Fix for "Bus not active" after restart)
    try:
        bus_live = BusLive.query.get(bus_no)
        if not bus_live:
            bus_live = BusLive(bus_no=bus_no)
            db.session.add(bus_live)
        
        bus_live.lat = lat
        bus_live.lng = lng
        bus_live.driver_name = session.get('username', 'Driver') # Optional update
        bus_live.last_updated = datetime.datetime.utcnow()
        db.session.commit()
    except Exception as e:
        print(f"DB Write Error in Heartbeat: {e}")
    
    return jsonify({'status': 'success', 'sync': True})

@app.route('/api/get-qr')
@login_required
def get_qr():
    # Generate dynamic QR content
    # Format: BusNo_CurrentTime
    bus_no = session.get('bus_no', 'Bus-10')
    # Round time to nearest 10 seconds for valid window
    # timestamp = int(datetime.datetime.utcnow().timestamp())
    # For demo simplicity, just send a string. Real world: Encrypt(BusID + Salt + Time)
    
    # We'll just generate a raw string that the frontend renders
    data = f"{bus_no}_{datetime.datetime.now().isoformat()}"
    return jsonify({'qr_data': data})

@app.route('/api/bus-manifest')
@login_required
def bus_manifest():
    bus_no = session.get('bus_no', 'Bus-10')
    # Get attendance for this bus today
    today = datetime.datetime.now().date()
    # Filter by timestamp >= today start. Simplified for sqlite: just check date part in python or query
    # SQLite datetime is tricky, often stored as string.
    # For this demo, we'll just pull all and filter in python or just pull last 50
    
    # Ideally: Attendance.query.filter(Attendance.bus_no == bus_no, db.func.date(Attendance.timestamp) == today).all()
    # Simplified:
    atts = Attendance.query.filter_by(bus_no=bus_no).order_by(Attendance.timestamp.desc()).limit(50).all()
    
    manifest = []
    for a in atts:
        # Check if it's today (mocking 'today' as 'recent' for demo if needed, but let's try strict)
        if a.timestamp.date() == today:
             manifest.append({
                 'student_name': a.student_name,
                 'timestamp': a.timestamp.strftime('%H:%M:%S'),
                 'status': a.verification_status,
                 'method': a.entry_method
             })
    
    return jsonify({'manifest': manifest, 'count': len(manifest)})

@app.route('/api/manual-attendance', methods=['POST'])
@login_required
def manual_attendance():
    # Helper for Driver/Admin to manually add
    data = request.json
    bus_no = data.get('bus_no')
    identifier = data.get('identifier') # ID or Name
    
    student = Student.query.filter((Student.id == identifier) | (Student.name == identifier)).first()
    if not student:
        return jsonify({'status': 'error', 'message': 'Student not found'})

    new_att = Attendance(
        student_id=student.id,
        student_name=student.name,
        timestamp=datetime.datetime.now(),
        bus_no=bus_no,
        entry_method='MANUAL',
        verification_status='VERIFIED_MANUAL'
    )
    db.session.add(new_att)
    db.session.commit()
    
    now = datetime.datetime.now()
    time_str = now.strftime('%H:%M')
    date_str = now.strftime('%d-%m-%Y')

    # SMS Simulation
    if os.environ.get('SMS_SIMULATION_MODE', 'True') == 'True':
        send_parent_sms(student, bus_no, time_str, date_str)
    
    # Professional Email (New Service Layer)
    print(f"[DEBUG MANUAL] Success for {student.name}. Triggering email...")
    email_status = NotificationService.send_parent_email(student.parent_email, student.name, bus_no, time_str, date_str)
    print(f"[DEBUG MANUAL] Email Dispatch Result: {email_status}")
    
    return jsonify({'status': 'success', 'message': f'Added {student.name}'})

@app.route('/api/bus-empty-check', methods=['POST'])
@login_required
def bus_empty_check():
    bus_no = request.json.get('bus_no')
    # Log this event
    print(f"!!! BUS CHECKED EMPTY: {bus_no} by {session.get('user_type')} at {datetime.datetime.now()} !!!")
    return jsonify({'status': 'success'})

# --- ADMIN ---

@app.route('/admin')
@login_required
def admin_dashboard():
    if session['user_type'] != 'admin': return redirect('/')
    students = Student.query.all()
    attendance_log = Attendance.query.order_by(Attendance.timestamp.desc()).all()
    complaints = Complaint.query.all()
    return render_template('admin.html', students=students, logs=attendance_log, complaints=complaints)

@app.route('/api/toggle-fee/<int:student_id>')
@login_required
def toggle_fee(student_id):
    if session['user_type'] != 'admin': return jsonify({'error': 'Unauthorized'}), 401
    s = Student.query.get(student_id)
    # Cycle: Paid -> Unpaid -> Pending -> Paid
    if s.fee_status == 'Paid': s.fee_status = 'Unpaid'
    elif s.fee_status == 'Unpaid': s.fee_status = 'Pending'
    else: s.fee_status = 'Paid'
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/api/send-fee-sms/<int:student_id>', methods=['POST'])
@login_required
def send_fee_sms(student_id):
    if session['user_type'] != 'admin': return jsonify({'error': 'Unauthorized'}), 401
    s = Student.query.get(student_id)
    if not s: return jsonify({'status': 'error', 'message': 'Student not found'}), 404
    
    if s.fee_status != 'Pending':
        return jsonify({'status': 'error', 'message': 'SMS can only be sent for Pending status'}), 400
    
    send_fee_reminder_sms(s)
    return jsonify({'status': 'success', 'message': f'Fee reminder sent to {s.name}'})

@app.route('/init-db')
def init_db():
    db.create_all()
    # Create Dummy Data
    if not Student.query.filter_by(name='student1').first():
        s1 = Student(name='student1', parent_email='parent@example.com', parent_phone='9876543210', bus_no='Bus-10', password='pass')
        db.session.add(s1)
        db.session.commit()
    return "Database Initialized"

@app.route('/api/reset-device/<int:student_id>', methods=['POST'])
def reset_device(student_id):
    try:
        s = db.session.get(Student, student_id)
        if not s:
            print(f"[DEBUG RESET] Student ID {student_id} not found.")
            return jsonify({'status': 'error', 'message': 'Student not found'}), 404
        
        old_device = s.device_id
        s.device_id = None
        
        # Hard Reset: Nullify device_id in DB
        db.session.add(s)
        
        audit = SystemAudit(
            action=f"Device Reset (Old: {old_device})",
            admin_name="Admin",
            student_id=student_id,
            reason="Admin Forced Reset"
        )
        db.session.add(audit)
        db.session.commit()
        
        print(f"[DEBUG RESET] Device reset successfully for {s.name} (ID: {student_id})")
        return jsonify({'status': 'success', 'message': f'Device reset successfully for {s.name}'})
    except Exception as e:
        db.session.rollback()
        print(f"[DEBUG RESET] CRITICAL ERROR: {str(e)}")
        return jsonify({'status': 'error', 'message': f'Internal Error: {str(e)}'}), 500

@app.route('/api/delete-student/<int:student_id>', methods=['POST'])
@login_required
def delete_student(student_id):
    if session.get('user_type') != 'admin':
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
        
    try:
        s = db.session.get(Student, student_id)
        if not s:
            return jsonify({'status': 'error', 'message': 'Student not found'}), 404
        
        student_name = s.name
        
        # 1. Delete Related Attendance
        Attendance.query.filter_by(student_id=student_id).delete()
        
        # 2. Delete Related Complaints
        Complaint.query.filter_by(student_id=student_id).delete()
        
        # 3. Log Audit
        audit = SystemAudit(
            action=f"Account Deleted: {student_name}",
            admin_name=session.get('name', 'Admin'),
            student_id=student_id,
            reason="Admin Delete Action"
        )
        db.session.add(audit)
        
        # 4. Delete Student
        db.session.delete(s)
        db.session.commit()
        
        print(f"[DEBUG DELETE] Student {student_name} deleted successfully.")
        return jsonify({'status': 'success', 'message': f'Account for {student_name} deleted forever.'})
    except Exception as e:
        db.session.rollback()
        print(f"[DEBUG DELETE] ERROR: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/test-email-diagnostic')
def test_email_diagnostic():
    # Force a test email to the user's known address
    target = "sudharsans24aid@vetias.ac.in"
    print(f"\n[DIAGNOSTIC] Starting manual SMTP test to {target}...")
    sys.stdout.flush()
    
    success = NotificationService.send_parent_email(
        target, 
        "Sudharsan (Diag Test)", 
        "Bus-10", 
        datetime.datetime.now().strftime('%H:%M'),
        datetime.datetime.now().strftime('%d-%m-%Y')
    )
    
    if success:
        return "<h1>DIAGNOSTIC SUCCESS</h1><p>Check your email (and Spam folder) now.</p>"
    else:
        return "<h1>DIAGNOSTIC FAILED</h1><p>Check the Render Logs for the exact Error message.</p>"

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        # Create Dummy Data on Startup
        if not Student.query.filter_by(name='student1').first():
            print("Creating dummy student: student1")
            s1 = Student(
                name='student1', 
                parent_email='parent@example.com', 
                parent_phone='9876543210', 
                bus_no='Bus-10', 
                password=generate_password_hash('pass')
            )
            db.session.add(s1)
            db.session.commit()
    app.run(host="0.0.0.0", port=5000, debug=os.environ.get('FLASK_DEBUG', 'False') == 'True')
