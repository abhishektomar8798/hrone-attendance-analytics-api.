## Local setup

1. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   ```

   On Windows PowerShell:

   ```powershell
   .venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create a `.env` file and set `MONGO_URI` and `MONGO_DB`. Do not commit `.env` to Git.

4. Load the sample data:

   ```bash
   python sample_seed.py
   ```

5. Start the application from the project root:

   ```bash
   uvicorn app.main:app --port 8000
   ```

MongoDB 6.0 or newer is required. MongoDB Atlas M0 is supported.