use pharma;

CREATE TABLE Patients_Table (
    Patient_ID VARCHAR(20) PRIMARY KEY,
    Full_Name VARCHAR(100),
    Age INT,
    Known_Allergies VARCHAR(255),
    Current_Medications VARCHAR(255)
);

CREATE TABLE Users_Table (
    Pharmacist_ID VARCHAR(20) PRIMARY KEY,
    Full_Name VARCHAR(100),
    Role_Level VARCHAR(50)
);

INSERT INTO Patients_Table (Patient_ID, Full_Name, Age, Known_Allergies, Current_Medications) 
VALUES ('PT-001', 'John Doe', 21, 'Penicillin', 'None');