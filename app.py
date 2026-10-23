from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import json
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
CORS(app)

def get_db_connection():
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "pharmamind"),
        cursorclass=pymysql.cursors.DictCursor
    )

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Patients_Table (
        Patient_ID VARCHAR(50) PRIMARY KEY,
        Full_Name VARCHAR(100),
        Age INT,
        Known_Allergies TEXT,
        Current_Medications TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Users_Table (
        Pharmacist_ID VARCHAR(50) PRIMARY KEY,
        Full_Name VARCHAR(100),
        Role_Level VARCHAR(50)
    )
    """)

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
    CREATE TABLE IF NOT EXISTS Drug_Monographs (
        Drug_Name VARCHAR(100) PRIMARY KEY,
        Drug_Class VARCHAR(100),
        Indications TEXT,
        Contraindications TEXT,
        Mechanism TEXT
    )
    """)

    cursor.execute("""
    INSERT IGNORE INTO Patients_Table (Patient_ID, Full_Name, Age, Known_Allergies, Current_Medications) 
    VALUES ('PT-001', 'John Doe', 21, 'Penicillin', 'None')
    """)

    sample_drugs = [
        ('Amoxicillin', 'Antibiotic (Penicillin class)', 'Bacterial infections, otitis media, strep throat', 'Penicillin allergy', 'Inhibits bacterial cell wall synthesis'),
        ('Ibuprofen', 'NSAID', 'Pain, fever, inflammation', 'Active GI ulcer, severe heart failure', 'Non-selective COX inhibitor, reducing prostaglandin synthesis'),
        ('Lisinopril', 'ACE Inhibitor', 'Hypertension, heart failure', 'History of angioedema, pregnancy', 'Inhibits angiotensin-converting enzyme')
    ]
    for drug in sample_drugs:
        cursor.execute("""
        INSERT IGNORE INTO Drug_Monographs (Drug_Name, Drug_Class, Indications, Contraindications, Mechanism) 
        VALUES (%s, %s, %s, %s, %s)
        """, drug)

    conn.commit()
    conn.close()

# Auto-initialize tables only if DB credentials exist
if os.getenv("DB_HOST"):
    try:
        init_db()
    except Exception as e:
        print(f"Database initialization bypassed or failed: {e}")

@app.route('/', methods=['GET'])
def serve_dashboard():
    return send_file('index.html')

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "online", "system": "PharmaMind API (Groq)"}), 200

# --- 1. VERIFICATION ENDPOINT ---
@app.route('/api/verify', methods=['POST'])
def verify_prescription():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    patient_id = data.get('patient_id')
    medication = data.get('medication')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Patients_Table WHERE Patient_ID = %s', (patient_id,))
    patient = cursor.fetchone()

    if patient is None:
        conn.close()
        return jsonify({"error": f"Patient ID {patient_id} not found."}), 404

    # Safe extraction depending on case sensitivity of the DB engine
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
        cursor.execute('SELECT * FROM Patients_Table WHERE Patient_ID = %s', (patient_id,))
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

# --- 4. PATIENTS CRUD ENDPOINTS ---
@app.route('/api/patients', methods=['GET'])
def get_patients():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM Patients_Table')
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
def create_patient():
    data = request.get_json()
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            '''INSERT INTO Patients_Table 
               (Patient_ID, Full_Name, Age, Known_Allergies, Current_Medications) 
               VALUES (%s, %s, %s, %s, %s)''',
            (data['patient_id'], data['full_name'], data['age'], data['allergies'], data['medications'])
        )
        conn.commit()
        return jsonify({"status": "Patient added"}), 201
    except pymysql.IntegrityError:
        return jsonify({"error": "Patient ID exists"}), 400
    finally:
        conn.close()

@app.route('/api/patients/<patient_id>', methods=['PUT'])
def update_patient(patient_id):
    data = request.get_json()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        '''UPDATE Patients_Table 
           SET Full_Name=%s, Age=%s, Known_Allergies=%s, Current_Medications=%s 
           WHERE Patient_ID=%s''',
        (data['full_name'], data['age'], data['allergies'], data['medications'], patient_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "Patient updated"}), 200

@app.route('/api/patients/<patient_id>', methods=['DELETE'])
def delete_patient(patient_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM Patients_Table WHERE Patient_ID = %s', (patient_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "Patient deleted"}), 200

# --- 5. AUDIT LOGS ENDPOINT ---
@app.route('/api/logs', methods=['GET'])
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

if __name__ == '__main__':
    app.run(debug=True, port=5000)