import sqlite3

# This will create 'pharmamind.db' in your project folder
conn = sqlite3.connect('pharmamind.db')
cursor = conn.cursor()

# Create the Patients Table
cursor.execute("""
CREATE TABLE IF NOT EXISTS Patients_Table (
    Patient_ID TEXT PRIMARY KEY,
    Full_Name TEXT,
    Age INTEGER,
    Known_Allergies TEXT,
    Current_Medications TEXT
)
""")

# Create the Pharmacists Table
cursor.execute("""
CREATE TABLE IF NOT EXISTS Users_Table (
    Pharmacist_ID TEXT PRIMARY KEY,
    Full_Name TEXT,
    Role_Level TEXT
)
""")

# Create the Audit Logs Table
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

# Create the Medical Knowledge Base Table
cursor.execute("""
CREATE TABLE IF NOT EXISTS Drug_Monographs (
    Drug_Name TEXT PRIMARY KEY,
    Drug_Class TEXT,
    Indications TEXT,
    Contraindications TEXT,
    Mechanism TEXT
)
""")

# Insert our test patient (PT-001)
# Using INSERT OR IGNORE so you can run this script multiple times without errors
cursor.execute("""
INSERT OR IGNORE INTO Patients_Table (Patient_ID, Full_Name, Age, Known_Allergies, Current_Medications) 
VALUES ('PT-001', 'John Doe', 21, 'Penicillin', 'None')
""")

# Insert sample drug monographs into the knowledge base
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

print("SQLite database 'pharmamind.db' successfully created and configured with logging and knowledge base capabilities!")