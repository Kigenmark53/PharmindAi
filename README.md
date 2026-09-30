
# PharmaMind // Neural Clinical Interface 🧬💊

PharmaMind is a web-based, AI-enhanced clinical pharmacy dashboard designed to modernize pharmaceutical workflows. By bridging traditional relational data management with advanced Large Language Model (LLM) processing, it provides a highly responsive, secure interface for patient management, automated prescription verification, and deterministic compounding calculations.

## 🚀 Core Features

*   **Scanner HUD (Prescription Verification):** Cross-references scanned prescriptions against a patient's medical history (age, allergies, current medications) to instantly flag interactions, contraindications, and provide clinical recommendations.
*   **Pharm Calc (Hybrid Compounding Engine):** Utilizes a deterministic Python math engine to safely calculate active ingredient and excipient weights, then leverages AI to generate step-by-step levigation/compounding protocols and stability warnings.
*   **Clinical Decision Support:** Acts as a prospective review assistant. Input a diagnosis and patient profile to receive evidence-based primary medication suggestions, contraindications, and professional treatment guidance.
*   **Data Matrix (Patient CRUD):** A fully functional patient management system to create, read, update, and delete patient records directly from the interface.
*   **System Logs (Audit Trail):** Silently records every AI verification query into an immutable database ledger for accountability and historical review.

## 🛠️ Technology Stack

*   **Frontend:** HTML5, CSS3 (Glassmorphism/Cyberpunk UI), Vanilla JavaScript.
*   **Backend:** Python 3, Flask, Flask-CORS, Regular Expressions (re).
*   **Database:** SQLite (Local development) / Ready for PostgreSQL (Production via Neon/Aiven).
*   **AI Engine:** Groq API (via OpenAI SDK) utilizing high-speed, open-source models (e.g., Llama 3 / GPT-OSS).
*   **Deployment:** Gunicorn WSGI, prepared for Render PaaS.

## ⚙️ Local Installation & Setup

1. **Clone the Repository**
   ```bash
   git clone [https://github.com/yourusername/PharmaMind.git](https://github.com/yourusername/PharmaMind.git)
   cd PharmaMind
