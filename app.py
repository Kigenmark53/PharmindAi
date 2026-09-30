from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import json
import sqlite3
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
    conn = sqlite3.connect('pharmamind.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Patients Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Patients_Table (
        Patient_ID TEXT PRIMARY KEY,
        Full_Name TEXT,
        Age INTEGER,
        Known_Allergies TEXT,
        Current_Medications TEXT
    )
    """)

    # Users / Pharmacists Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Users_Table (
        Pharmacist_ID TEXT PRIMARY KEY,
        Full_Name TEXT,
        Role_Level TEXT
    )
    """)

    # Audit Logs Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Verification_Logs_Table (
        Log_ID INTEGER PRIMARY KEY AUTOINCREMENT,
        Patient_ID TEXT,
        Medication TEXT,
        AI_Status TEXT,
        AI_Flag TEXT,
        Timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Drug Knowledge Base Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Drug_Monographs (
        Drug_Name TEXT PRIMARY KEY,
        Drug_Class TEXT,
        Indications TEXT,
        Contraindications TEXT,
        Mechanism TEXT
    )
    """)

    # Default Seed Patient
    cursor.execute("""
    INSERT OR IGNORE INTO Patients_Table (Patient_ID, Full_Name, Age, Known_Allergies, Current_Medications) 
    VALUES ('PT-001', 'John Doe', 21, 'Penicillin', 'None')
    """)

    # Default Seed Monographs
    sample_drugs = [
        ('Amoxicillin', 'Antibiotic (Penicillin class)', 'Bacterial infections, otitis media, strep throat', 'Penicillin allergy', 'Inhibits bacterial cell wall synthesis'),
        ('Ibuprofen', 'NSAID', 'Pain, fever, inflammation', 'Active GI ulcer, severe heart failure', 'Non-selective COX inhibitor, reducing prostaglandin synthesis'),
        ('Lisinopril', 'ACE Inhibitor', 'Hypertension, heart failure', 'History of angioedema, pregnancy', 'Inhibits angiotensin-converting enzyme')
    ]
    for drug in sample_drugs:
        cursor.execute("""
        INSERT OR IGNORE INTO Drug_Monographs (Drug_Name, Drug_Class, Indications, Contraindications, Mechanism) 
        VALUES (?, ?, ?, ?, ?)
        """, drug)

    conn.commit()
    conn.close()

# Auto-initialize database tables on server start
init_db()

# --- FRONTEND & HEALTH ROUTES ---
@app.route('/', methods=['GET'])
def serve_dashboard():
    return send_file('index.html')

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "online", "system": "PharmaMind API (Groq)"}), 200

# --- 1. VERIFICATION ENDPOINT (GROQ) ---
@app.route('/api/verify', methods=['POST'])
def verify_prescription():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    patient_id = data.get('patient_id')
    medication = data.get('medication')

    conn = get_db_connection()
    patient = conn.execute('SELECT * FROM Patients_Table WHERE Patient_ID = ?', (patient_id,)).fetchone()

    if patient is None:
        conn.close()
        return jsonify({"error": f"Patient ID {patient_id} not found."}), 404

    patient_history = f"Patient is {patient['Age']} years old. Known allergies: {patient['Known_Allergies']}. Current medications: {patient['Current_Medications']}."

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

        conn.execute(
            "INSERT INTO Verification_Logs_Table (Patient_ID, Medication, AI_Status, AI_Flag) VALUES (?, ?, ?, ?)",
            (patient_id, medication, ai_analysis.get('status'), ai_analysis.get('flag'))
        )
        conn.commit()

        return jsonify({
            "patient_id": patient_id,
            "patient_name": patient['Full_Name'],
            "scanned_medication": medication,
            "ai_verification": ai_analysis
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# --- 2. PHARM CALCULATOR ENDPOINT (HYBRID ENGINE) ---
@app.route('/api/calculate', methods=['POST'])
def calculate_formulation():
    data = request.get_json()

    prep_type = data.get('prep_type', '')
    active_ingredient = data.get('active_ingredient', '')
    concentration_str = data.get('concentration', '')
    total_volume_str = data.get('total_volume', '')

    try:
        # Deterministic Math Engine
        conc_match = re.search(r"([0-9]*\.?[0-9]+)", concentration_str)
        vol_match = re.search(r"([0-9]*\.?[0-9]+)", total_volume_str)
        
        if not conc_match or not vol_match:
            return jsonify({"error": "Please provide clear numbers for volume and concentration."}), 400
            
        concentration = float(conc_match.group(1))
        total_volume = float(vol_match.group(1))
        
        # Determine unit
        vol_unit = re.sub(r"[0-9]*\.?[0-9]+", "", total_volume_str).strip().lower()
        if not vol_unit:
            vol_unit = "ml" if prep_type in ["Syrup", "Suspension"] else "g"

        # Math execution in Python
        active_amount_val = (concentration / 100) * total_volume
        base_amount_val = total_volume - active_amount_val
        
        active_amount = f"{active_amount_val:.2f}{vol_unit}"
        base_amount = f"{base_amount_val:.2f}{vol_unit}"

        # AI-Generated Protocols
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
        patient = conn.execute('SELECT * FROM Patients_Table WHERE Patient_ID = ?', (patient_id,)).fetchone()
        conn.close()
        
        if patient:
            patient_history = f"Age: {patient['Age']}, Allergies: {patient['Known_Allergies']}, Current Medications: {patient['Current_Medications']}"

    prompt = f"""
    You are an expert Clinical Decision Support AI assisting a pharmacist.
    Provide evidence-based medication suggestions for the following condition, taking into account the patient's history.
    
    Condition/Diagnosis: {diagnosis}
    Patient History: {patient_history}
    
    Return exactly this JSON structure (no markdown tags, just the raw object):
    {{
        "primary_suggestions": ["Medication 1 with reasoning", "Medication 2 with reasoning"],
        "contraindications": "List of drugs to avoid based on the patient's specific history and allergies",
        "clinical_guidance": "A brief professional summary for the pharmacist regarding treatment approach"
    }}
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
    patients = conn.execute('SELECT * FROM Patients_Table').fetchall()
    conn.close()
    return jsonify([dict(ix) for ix in patients]), 200

@app.route('/api/patients', methods=['POST'])
def create_patient():
    data = request.get_json()
    conn = get_db_connection()
    try:
        conn.execute(
            '''INSERT INTO Patients_Table 
               (Patient_ID, Full_Name, Age, Known_Allergies, Current_Medications) 
               VALUES (?, ?, ?, ?, ?)''',
            (data['patient_id'], data['full_name'], data['age'], data['allergies'], data['medications'])
        )
        conn.commit()
        return jsonify({"status": "Patient added"}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "Patient ID exists"}), 400
    finally:
        conn.close()

@app.route('/api/patients/<patient_id>', methods=['PUT'])
def update_patient(patient_id):
    data = request.get_json()
    conn = get_db_connection()
    conn.execute(
        '''UPDATE Patients_Table 
           SET Full_Name=?, Age=?, Known_Allergies=?, Current_Medications=? 
           WHERE Patient_ID=?''',
        (data['full_name'], data['age'], data['allergies'], data['medications'], patient_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "Patient updated"}), 200

@app.route('/api/patients/<patient_id>', methods=['DELETE'])
def delete_patient(patient_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM Patients_Table WHERE Patient_ID = ?', (patient_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "Patient deleted"}), 200

# --- 5. AUDIT LOGS ENDPOINT ---
@app.route('/api/logs', methods=['GET'])
def get_logs():
    conn = get_db_connection()
    logs = conn.execute('SELECT * FROM Verification_Logs_Table ORDER BY Timestamp DESC').fetchall()
    conn.close()
    return jsonify([dict(ix) for ix in logs]), 200

if __name__ == '__main__':
    app.run(debug=True, port=5000)