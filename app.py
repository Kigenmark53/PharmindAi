from flask import Flask, request, jsonify, send_file, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
import json
import time
import pymysql
import pymysql.cursors
import re
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Initialize Groq client via OpenAI SDK
client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)
MODEL_ID = "openai/gpt-oss-20b"

app = Flask(__name__)
# Secure secret key and session configuration
app.secret_key = os.getenv("SECRET_KEY", "pharmamind-super-secret-dev-key") 
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production"
)

# Restrict CORS to trusted origins
ALLOWED_ORIGINS = [
    "https://pharmindai.onrender.com",
    "http://127.0.0.1:5000",
    "http://localhost:5000"
]
CORS(app, supports_credentials=True, origins=ALLOWED_ORIGINS)

# Lightweight In-Memory Rate Limiter (Protects Groq Credits & Auth)
RATE_LIMIT_STORE = {}

def rate_limit(limit=10, per=60):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            ip = request.headers.get('X-Forwarded-For', request.remote_addr)
            key = f"{f.__name__}:{ip}"
            now = time.time()
            timestamps = [t for t in RATE_LIMIT_STORE.get(key, []) if now - t < per]
            if len(timestamps) >= limit:
                return jsonify({"error": f"Rate limit exceeded ({limit} requests per {per}s). Please wait."}), 429
            timestamps.append(now)
            RATE_LIMIT_STORE[key] = timestamps
            return f(*args, **kwargs)
        return wrapped
    return decorator

def get_db_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "pharmamind"),
        cursorclass=pymysql.cursors.DictCursor,
        ssl={} # Forces an encrypted connection (Required by Aiven)
    )

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Core Clinical Tables (Supports Soft Deletes)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Patients_Table (
        Patient_ID VARCHAR(50) PRIMARY KEY,
        Full_Name VARCHAR(100),
        Age INT,
        Known_Allergies TEXT,
        Current_Medications TEXT,
        Is_Deleted TINYINT(1) DEFAULT 0
    )
    """)

    # Ensure Is_Deleted column exists on older schemas
    try:
        cursor.execute("ALTER TABLE Patients_Table ADD COLUMN Is_Deleted TINYINT(1) DEFAULT 0")
    except Exception:
        pass

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Verification_Logs_Table (
        Log_ID INT AUTO_INCREMENT PRIMARY KEY,
        Patient_ID VARCHAR(50),
        Medication VARCHAR(100),
        AI_Status VARCHAR(50),
        AI_Flag VARCHAR(100),
        Timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Patient_Audit_Logs (
        Audit_ID INT AUTO_INCREMENT PRIMARY KEY,
        User_Email VARCHAR(100),
        Action VARCHAR(20),
        Patient_ID VARCHAR(50),
        Before_Data TEXT,
        After_Data TEXT,
        Timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Drug_Monographs (
        Drug_Name VARCHAR(100) PRIMARY KEY,
        Drug_Class VARCHAR(100),
        Indications TEXT,
        Contraindications TEXT,
        Mechanism TEXT
    )
    """)

    # Persistent Users Table (Removed DROP TABLE so accounts survive restarts)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Users_Table (
        User_ID INT AUTO_INCREMENT PRIMARY KEY,
        Full_Name VARCHAR(100),
        Email VARCHAR(100) UNIQUE,
        Password_Hash VARCHAR(255),
        Role VARCHAR(20) DEFAULT 'user'
    )
    """)

    # Create Default Admin & Standard Pharmacist Accounts
    admin_pass = generate_password_hash("Admin123!")
    cursor.execute("""
    INSERT IGNORE INTO Users_Table (Full_Name, Email, Password_Hash, Role) 
    VALUES ('System Admin', 'admin@pharmamind.com', %s, 'admin')
    """, (admin_pass,))

    user_pass = generate_password_hash("User123!")
    cursor.execute("""
    INSERT IGNORE INTO Users_Table (Full_Name, Email, Password_Hash, Role) 
    VALUES ('Staff Pharmacist', 'pharmacist@pharmamind.com', %s, 'user')
    """, (user_pass,))

    cursor.execute("""
    INSERT IGNORE INTO Patients_Table (Patient_ID, Full_Name, Age, Known_Allergies, Current_Medications, Is_Deleted) 
    VALUES ('PT-001', 'John Doe (Demo)', 21, 'Penicillin', 'None', 0)
    """)

    conn.commit()
    conn.close()

# Auto-initialize tables only if DB credentials exist
if os.getenv("DB_HOST"):
    try:
        init_db()
    except Exception as e:
        print(f"Database initialization bypassed or failed: {e}")


# --- SECURITY DECORATORS ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"error": "Unauthorized. Please log in."}), 401
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'admin':
            return jsonify({"error": "Forbidden. Administrator access required."}), 403
        return f(*args, **kwargs)
    return decorated_function


# --- AUTHENTICATION ENDPOINTS ---
@app.route('/api/auth/signup', methods=['POST'])
@rate_limit(limit=5, per=300)
def signup():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not name or not email or len(password) < 6:
        return jsonify({"error": "Name, valid email, and password (min 6 chars) are required."}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        pw_hash = generate_password_hash(password)
        # Signup strictly provisions 'user' role; admins are elevated in DB only
        cursor.execute(
            "INSERT INTO Users_Table (Full_Name, Email, Password_Hash, Role) VALUES (%s, %s, %s, 'user')",
            (name, email, pw_hash)
        )
        conn.commit()
        return jsonify({"message": "Account created as read-only clinician. Please sign in."}), 201
    except pymysql.IntegrityError:
        return jsonify({"error": "An account with this email already exists."}), 400
    finally:
        conn.close()

@app.route('/api/auth/login', methods=['POST'])
@rate_limit(limit=10, per=60)
def login():
    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM Users_Table WHERE Email = %s", (email,))
    user = cursor.fetchone()
    conn.close()

    if user and check_password_hash(user['Password_Hash'], password):
        session.clear()
        session['user_id'] = user['User_ID']
        session['email'] = user['Email']
        session['role'] = user['Role']
        session['name'] = user['Full_Name']
        return jsonify({
            "message": "Logged in successfully",
            "role": user['Role'],
            "name": user['Full_Name'],
            "email": user['Email']
        }), 200
    
    return jsonify({"error": "Invalid email or password"}), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"message": "Logged out successfully"}), 200

@app.route('/api/auth/me', methods=['GET'])
def get_current_user():
    if 'user_id' in session:
        return jsonify({
            "authenticated": True,
            "user_id": session['user_id'],
            "name": session['name'],
            "email": session.get('email', ''),
            "role": session['role']
        }), 200
    return jsonify({"authenticated": False}), 200


@app.route('/', methods=['GET'])
def serve_dashboard():
    return send_file('index.html')

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "online", "system": "PharmaMind API (Secured)"}), 200

# --- 1. VERIFICATION ENDPOINT ---
@app.route('/api/verify', methods=['POST'])
@login_required
@rate_limit(limit=15, per=60)
def verify_prescription():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    patient_id = data.get('patient_id')
    medication = data.get('medication')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Patients_Table WHERE Patient_ID = %s AND (Is_Deleted = 0 OR Is_Deleted IS NULL)', (patient_id,))
    patient = cursor.fetchone()

    if patient is None:
        conn.close()
        return jsonify({"error": f"Patient ID {patient_id} not found."}), 404

    age = patient.get('Age', patient.get('age'))
    allergies = patient.get('Known_Allergies', patient.get('known_allergies'))
    meds = patient.get('Current_Medications', patient.get('current_medications'))
    name = patient.get('Full_Name', patient.get('full_name'))

    patient_history = f"Patient is {age} years old. Known allergies: {allergies}. Current medications: {meds}."

    prompt = f"""
    You are an expert Clinical Pharmacy Assistant AI.
    Analyze this prescription against the patient's medical history.
    Patient History: {patient_history}
    Prescribed Medication: {medication}

    Return exactly this JSON structure (no markdown tags, just the raw object):
    {{
        "status": "SAFE", "WARNING", or "CRITICAL",
        "flag": "2-3 word summary or None",
        "description": "Brief explanation",
        "recommendation": "Actionable advice"
    }}
    """

    try:
        response = client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        ai_analysis = json.loads(response.choices[0].message.content)

        cursor.execute(
            "INSERT INTO Verification_Logs_Table (Patient_ID, Medication, AI_Status, AI_Flag) VALUES (%s, %s, %s, %s)",
            (patient_id, medication, ai_analysis.get('status'), ai_analysis.get('flag'))
        )
        conn.commit()

        return jsonify({
            "patient_id": patient_id,
            "patient_name": name,
            "scanned_medication": medication,
            "ai_verification": ai_analysis
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# --- 2. PHARM CALCULATOR ENDPOINT ---
@app.route('/api/calculate', methods=['POST'])
@login_required
@rate_limit(limit=15, per=60)
def calculate_formulation():
    data = request.get_json()

    prep_type = data.get('prep_type', '')
    active_ingredient = data.get('active_ingredient', '')
    concentration_str = data.get('concentration', '')
    total_volume_str = data.get('total_volume', '')

    try:
        conc_match = re.search(r"([0-9]*\.?[0-9]+)", concentration_str)
        vol_match = re.search(r"([0-9]*\.?[0-9]+)", total_volume_str)
        
        if not conc_match or not vol_match:
            return jsonify({"error": "Please provide clear numbers for volume and concentration."}), 400
            
        concentration = float(conc_match.group(1))
        total_volume = float(vol_match.group(1))
        
        vol_unit = re.sub(r"[0-9]*\.?[0-9]+", "", total_volume_str).strip().lower()
        if not vol_unit:
            vol_unit = "ml" if prep_type in ["Syrup", "Suspension"] else "g"

        active_amount_val = (concentration / 100) * total_volume
        base_amount_val = total_volume - active_amount_val
        
        active_amount = f"{active_amount_val:.2f}{vol_unit}"
        base_amount = f"{base_amount_val:.2f}{vol_unit}"

        prompt = f"""
        You are a Master Compounding Pharmacist AI.
        A pharmacist is compounding a {prep_type} of {active_ingredient}.
        Target Concentration: {concentration}%
        Total Volume/Weight: {total_volume}{vol_unit}
        
        The required mathematical measurements are already safely calculated:
        - Active Ingredient Required: {active_amount}
        - Base/Excipient Required: {base_amount}

        Based on these figures, provide the compounding steps and clinical safety feedback.

        Return exactly this JSON structure (no markdown tags, just the raw object):
        {{
            "instructions": "Step-by-step compounding instructions (2-3 sentences)",
            "feedback": "Clinical guidance on vehicle choice, stability, or precautions"
        }}
        """

        response = client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        
        ai_data = json.loads(response.choices[0].message.content)
        
        final_formulation = {
            "active_amount": active_amount,
            "base_amount": base_amount,
            "instructions": ai_data.get("instructions", "No instructions generated."),
            "feedback": ai_data.get("feedback", "No feedback generated.")
        }
        
        return jsonify({"formulation": final_formulation}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- 3. CLINICAL DECISION SUPPORT ---
@app.route('/api/suggest', methods=['POST'])
@login_required
@rate_limit(limit=15, per=60)
def clinical_decision_support():
    data = request.get_json()
    
    patient_id = data.get('patient_id')
    diagnosis = data.get('diagnosis')
    
    if not diagnosis:
        return jsonify({"error": "Diagnosis is required to provide suggestions."}), 400

    patient_history = "No patient history provided."
    
    if patient_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM Patients_Table WHERE Patient_ID = %s AND (Is_Deleted = 0 OR Is_Deleted IS NULL)', (patient_id,))
        patient = cursor.fetchone()
        conn.close()
        
        if patient:
            age = patient.get('Age', patient.get('age'))
            allergies = patient.get('Known_Allergies', patient.get('known_allergies'))
            meds = patient.get('Current_Medications', patient.get('current_medications'))
            patient_history = f"Age: {age}, Allergies: {allergies}, Current Medications: {meds}"

    prompt = f"""
    You are an expert Clinical Decision Support AI assisting a pharmacist.
    Provide evidence-based medication suggestions for the following condition, taking into account the patient's history.
    
    Condition/Diagnosis: {diagnosis}
    Patient History: {patient_history}
    
    Return exactly this JSON structure (no markdown tags, no backtick code blocks, just the raw object):
    {{
        "primary_suggestions": ["Medication 1 with reasoning", "Medication 2 with reasoning"],
        "contraindications": "List of drugs to avoid based on the patient's specific history and allergies",
        "clinical_guidance": "A brief professional summary for the pharmacist regarding treatment approach"
    }}

    CRITICAL: Ensure the output is strictly valid JSON. Do NOT escape single quotes (use ' not \') inside your text strings.
    """
    
    try:
        response = client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        ai_suggestion = json.loads(response.choices[0].message.content)
        return jsonify({"decision_support": ai_suggestion}), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- 4. PATIENTS CRUD ENDPOINTS (WITH SOFT DELETE & AUDIT LOGS) ---
@app.route('/api/patients', methods=['GET'])
@login_required
def get_patients():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Patients_Table WHERE Is_Deleted = 0 OR Is_Deleted IS NULL')
    patients = cursor.fetchall()
    conn.close()
    
    patients_list = []
    for p in patients:
        patients_list.append({
            "Patient_ID": p.get("Patient_ID", p.get("patient_id")),
            "Full_Name": p.get("Full_Name", p.get("full_name")),
            "Age": p.get("Age", p.get("age")),
            "Known_Allergies": p.get("Known_Allergies", p.get("known_allergies")),
            "Current_Medications": p.get("Current_Medications", p.get("current_medications"))
        })
    return jsonify(patients_list), 200

@app.route('/api/patients', methods=['POST'])
@admin_required
def create_patient():
    data = request.get_json()
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        pid = data['patient_id']
        cursor.execute('SELECT * FROM Patients_Table WHERE Patient_ID = %s', (pid,))
        existing = cursor.fetchone()

        after_state = json.dumps({
            "Full_Name": data['full_name'],
            "Age": data['age'],
            "Known_Allergies": data['allergies'],
            "Current_Medications": data['medications']
        })

        # If previously soft-deleted, restore and update
        if existing and existing.get('Is_Deleted') == 1:
            cursor.execute(
                '''UPDATE Patients_Table 
                   SET Full_Name=%s, Age=%s, Known_Allergies=%s, Current_Medications=%s, Is_Deleted=0 
                   WHERE Patient_ID=%s''',
                (data['full_name'], data['age'], data['allergies'], data['medications'], pid)
            )
            action_type = 'RESTORE'
            before_state = json.dumps({"Is_Deleted": 1})
        else:
            cursor.execute(
                '''INSERT INTO Patients_Table 
                   (Patient_ID, Full_Name, Age, Known_Allergies, Current_Medications, Is_Deleted) 
                   VALUES (%s, %s, %s, %s, %s, 0)''',
                (pid, data['full_name'], data['age'], data['allergies'], data['medications'])
            )
            action_type = 'CREATE'
            before_state = None

        cursor.execute(
            '''INSERT INTO Patient_Audit_Logs (User_Email, Action, Patient_ID, Before_Data, After_Data)
               VALUES (%s, %s, %s, %s, %s)''',
            (session.get('email', 'admin'), action_type, pid, before_state, after_state)
        )
        conn.commit()
        return jsonify({"status": "Patient added"}), 201
    except pymysql.IntegrityError:
        return jsonify({"error": "Patient ID already exists"}), 400
    finally:
        conn.close()

@app.route('/api/patients/<patient_id>', methods=['PUT'])
@admin_required
def update_patient(patient_id):
    data = request.get_json()
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM Patients_Table WHERE Patient_ID = %s', (patient_id,))
    before = cursor.fetchone()
    before_json = json.dumps({
        "Full_Name": before.get("Full_Name") if before else "",
        "Age": before.get("Age") if before else "",
        "Known_Allergies": before.get("Known_Allergies") if before else "",
        "Current_Medications": before.get("Current_Medications") if before else ""
    }) if before else None

    cursor.execute(
        '''UPDATE Patients_Table 
           SET Full_Name=%s, Age=%s, Known_Allergies=%s, Current_Medications=%s 
           WHERE Patient_ID=%s''',
        (data['full_name'], data['age'], data['allergies'], data['medications'], patient_id)
    )

    after_json = json.dumps({
        "Full_Name": data['full_name'],
        "Age": data['age'],
        "Known_Allergies": data['allergies'],
        "Current_Medications": data['medications']
    })

    cursor.execute(
        '''INSERT INTO Patient_Audit_Logs (User_Email, Action, Patient_ID, Before_Data, After_Data)
           VALUES (%s, 'UPDATE', %s, %s, %s)''',
        (session.get('email', 'admin'), patient_id, before_json, after_json)
    )

    conn.commit()
    conn.close()
    return jsonify({"status": "Patient updated"}), 200

@app.route('/api/patients/<patient_id>', methods=['DELETE'])
@admin_required
def delete_patient(patient_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM Patients_Table WHERE Patient_ID = %s', (patient_id,))
    before = cursor.fetchone()
    before_json = json.dumps({
        "Full_Name": before.get("Full_Name") if before else "",
        "Age": before.get("Age") if before else "",
        "Known_Allergies": before.get("Known_Allergies") if before else "",
        "Current_Medications": before.get("Current_Medications") if before else ""
    }) if before else None

    # Soft delete instead of permanent removal
    cursor.execute('UPDATE Patients_Table SET Is_Deleted = 1 WHERE Patient_ID = %s', (patient_id,))

    cursor.execute(
        '''INSERT INTO Patient_Audit_Logs (User_Email, Action, Patient_ID, Before_Data, After_Data)
           VALUES (%s, 'SOFT_DELETE', %s, %s, %s)''',
        (session.get('email', 'admin'), patient_id, before_json, json.dumps({"Is_Deleted": 1}))
    )

    conn.commit()
    conn.close()
    return jsonify({"status": "Patient archived (soft-deleted)"}), 200

# --- 5. AUDIT & VERIFICATION LOGS ENDPOINTS ---
@app.route('/api/logs', methods=['GET'])
@login_required
def get_logs():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Verification_Logs_Table ORDER BY Timestamp DESC')
    logs = cursor.fetchall()
    conn.close()
    
    logs_list = []
    for l in logs:
        timestamp = l.get("Timestamp", l.get("timestamp"))
        logs_list.append({
            "Log_ID": l.get("Log_ID", l.get("log_id")),
            "Patient_ID": l.get("Patient_ID", l.get("patient_id")),
            "Medication": l.get("Medication", l.get("medication")),
            "AI_Status": l.get("AI_Status", l.get("ai_status")),
            "AI_Flag": l.get("AI_Flag", l.get("ai_flag")),
            "Timestamp": timestamp.isoformat() + "Z" if timestamp else ""
        })
    return jsonify(logs_list), 200

@app.route('/api/audit-logs', methods=['GET'])
@admin_required
def get_audit_logs():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Patient_Audit_Logs ORDER BY Timestamp DESC LIMIT 200')
    audits = cursor.fetchall()
    conn.close()

    audit_list = []
    for a in audits:
        ts = a.get("Timestamp", a.get("timestamp"))
        audit_list.append({
            "Audit_ID": a.get("Audit_ID"),
            "User_Email": a.get("User_Email"),
            "Action": a.get("Action"),
            "Patient_ID": a.get("Patient_ID"),
            "Before_Data": a.get("Before_Data"),
            "After_Data": a.get("After_Data"),
            "Timestamp": ts.isoformat() + "Z" if ts else ""
        })
    return jsonify(audit_list), 200

if __name__ == '__main__':
    is_debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=is_debug, port=int(os.getenv("PORT", 5000)))