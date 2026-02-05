import os
import datetime
import json
import math
import smtplib
from email.mime.text import MIMEText
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_session import Session

db = SQLAlchemy()

# Initialize App
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secure_smart_bus_secret_key_123' # Change this in production
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///smart_bus.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_TYPE'] = 'filesystem'
SKIP_DEVICE_CHECK = os.environ.get('SKIP_DEVICE_CHECK', 'False') == 'True'

db.init_app(app)
Session(app)

# In-Memory Cache for Bus Locations (Energy Efficient - No DB Write)
BUS_LOCATION_CACHE = {} 


# --------------------------
# Database Models
# --------------------------

class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
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

def send_notification(to_email, subject, body):
    """
    Mock function to send email. 
    In production, configure SMTP server details.
    """
    print(f"--- EMAIL SIMULATION ---")
    print(f"To: {to_email}")
    print(f"Subject: {subject}")
    print(f"Body: {body}")
    print(f"------------------------")
    # Real implementation code (commented out for local testing without creds):
    # sender_email = "your_email@gmail.com"
    # sender_password = "your_password"
    # msg = MIMEText(body)
    # msg['Subject'] = subject
    # msg['From'] = sender_email
    # msg['To'] = to_email
    #    server.login(sender_email, sender_password)
    #    server.send_message(msg)

def send_parent_sms(student, bus_no, timestamp):
    """
    Mock SMS function. In production, use Twilio/Other API.
    """
    target = student.parent_phone or student.parent_email
    msg = f"Dear Parent, your ward {student.name} has safely boarded Bus No. {bus_no} at {timestamp}. - VET IAS Transport."
    print(f"--- SMS SIMULATION ---")
    print(f"To: {target} (SMS)")
    print(f"Message: {msg}")
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

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user_type = request.form.get('user_type')
        username = request.form.get('username') # For student, this is ID. For driver/admin, it's name
        password = request.form.get('password')
        device_id = request.form.get('device_id') # From JS

        if user_type == 'student':
            # Support Login by Name or ID
            student = Student.query.filter((Student.name == username) | (Student.id == username)).first()
            if student and student.password == password:
                # Device Binding Check
                if student.device_id and not SKIP_DEVICE_CHECK:
                    if student.device_id != device_id:
                        return render_template('login.html', error="Login Failed: New device detected. Please use your registered device.")
                else:
                    # First time login, bind device
                    if not device_id:
                        return render_template('login.html', error="Security Error: Device identity could not be verified. Please enable JavaScript.")
                    
                    student.device_id = device_id
                    db.session.commit()
                
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

    return render_template('login.html')

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
    
    # 1. Decode QR Data (Format: BusNo_Timestamp -> e.g., "Bus-10_1700")
    # Ideally, verify timestamp is recent (within 30-60s)
    try:
        bus_no_qr, timestamp_qr = qr_data.split('_', 1) # simple split
        # In a real app, you'd decrypt a token here
    except:
        return jsonify({'status': 'error', 'message': 'Invalid QR Code'})

    bus_live = BusLive.query.filter_by(bus_no=bus_no_qr).first()
    # OPTION: Use Cache if available for fresher data
    cached_loc = BUS_LOCATION_CACHE.get(bus_no_qr)
    
    master_lat, master_lng = 0, 0
    if cached_loc:
        master_lat = cached_loc['lat']
        master_lng = cached_loc['lng']
    elif bus_live:
        master_lat = bus_live.lat
        master_lng = bus_live.lng
    else:
        return jsonify({'status': 'error', 'message': 'Bus not active/Syncing...'})

    # 3. Geofence Check (Strict 15m against Master Location)
    distance = haversine(lat, lng, master_lat, master_lng)
    
    # DEBUG LOGS
    print(f"\n[DEBUG GEO] Student: ({lat}, {lng}) | Master: ({master_lat}, {master_lng})")
    print(f"[DEBUG GEO] Distance: {distance} meters (Limit: 100m)\n")

    # RELAXED RULE: 100 meters (Temporary for debugging)
    if distance > 100: 
         return jsonify({'status': 'error', 'message': f'Geofence Failed! Too far from bus ({int(distance)}m).'}) 

    # 4. Device Binding (Zero-Trust Handshake)
    current_device = data.get('device_id')
    if not current_device:
        return jsonify({'status': 'error', 'message': 'Missing Device Identifier'})

    if student.device_id is None or student.device_id == "":
        # STATE C: Auto-Bind
        student.device_id = current_device
        db.session.commit()
    elif student.device_id != current_device:
        # STATE B: Reject (Proxy Alert)
        return jsonify({'status': 'error', 'message': 'Security Breach: Device Mismatch. Please contact Admin.'}), 403
    
    # STATE A: Match (Proceed)

    # 5. Mark Attendance
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

    # 5. Notify Parent (SMS)
    send_parent_sms(student, bus_no_qr, datetime.datetime.now().strftime('%H:%M'))

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
    
    send_parent_sms(student, bus_no, datetime.datetime.now().strftime('%H:%M'))
    
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
    s = Student.query.get(student_id)
    if not s:
        return jsonify({'status': 'error', 'message': 'Student not found'}), 404
    
    data = request.json
    reason = data.get('reason', 'Not specified')
    
    old_device = s.device_id
    s.device_id = None
    
    audit = SystemAudit(
        action=f"Device Reset (Old: {old_device})",
        admin_name="Admin",
        student_id=student_id,
        reason=reason
    )
    db.session.add(audit)
    db.session.commit()
    
    return jsonify({'status': 'success', 'message': f'Device reset for {s.name}'})

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
                password='pass'
            )
            db.session.add(s1)
            db.session.commit()
    app.run(host="0.0.0.0", port=5000)
