# GCP Certification Study App

A simple Flask web application to help study for GCP certification exams using questions from a Google Sheet.

## Features

- Lists exams based on tabs in a Google Sheet.
- Provides a flashcard interface with multiple-choice questions.
- Shows correct/incorrect answers and explanations.
- Simple, modern UI using Pico.css.
- Deployable on Google Cloud Run.

## Google Sheet Setup

1.  **Create a Google Sheet.**
2.  **Structure your sheet:**
    * The first row of each sheet (tab) will be treated as the header.
    * Each tab in the Google Sheet will represent a different "Certification Exam".
    * For each exam tab, create the following columns (case-sensitive headers):
        * `Question`: The text of the question.
        * `Answer A`: Text for option A.
        * `Answer B`: Text for option B.
        * `Answer C`: Text for option C.
        * `Answer D`: Text for option D.
        * `Correct Answer`: The letter of the correct option (e.g., "A", "B", "C", or "D"). This should be just the letter.
        * `Explanation-Correct`: The explanation for why the correct answer is correct.
        * `Explanation-Incorrect`: (Optional) A general explanation for common pitfalls or why other options are incorrect.
3.  **Get the Google Sheet ID:** This is the long string of characters in the URL of your Google Sheet.
    Example: `https://docs.google.com/spreadsheets/d/THIS_IS_THE_SHEET_ID/edit#gid=0`

## Local Development Setup

1.  **Clone the repository.**
2.  **Create a Python virtual environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```
3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Set up Google Cloud Authentication:**
    * Create a Google Cloud Project.
    * Enable the **Google Sheets API**.
    * Create a **Service Account**.
    * Download the JSON key file for this service account.
    * **Share your Google Sheet** with the service account's email address (give it "Viewer" or "Commenter" permission is enough for reading).
5.  **Set Environment Variables:**
    Create a `.env` file in the root directory (optional, if using `python-dotenv` and loading it in `app.py`, or set them manually in your shell):
    ```env
    export FLASK_APP=app.py
    export FLASK_DEBUG=True
    export GOOGLE_SHEET_ID="your_google_sheet_id"
    export GOOGLE_APPLICATION_CREDENTIALS="/path/to/your/service-account-key.json"
    export FLASK_SECRET_KEY="a_local_secret_key" # Can be anything for local dev
    ```
    Or set them in your terminal before running.
6.  **Run the app:**
    ```bash
    flask run
    ```
    Or directly using python:
    ```bash
    python app.py
    ```
    The app will be available at `http://127.0.0.1:8080` (or the port specified by Flask/`app.py`).

## Deployment to Google Cloud Run

1.  **Ensure Google Cloud SDK is installed and configured (`gcloud init`).**
2.  **Enable necessary APIs:**
    * Google Sheets API
    * Cloud Build API (for building the container)
    * Cloud Run API
3.  **Create a Service Account for Cloud Run (Recommended):**
    * Go to IAM & Admin > Service Accounts in Google Cloud Console.
    * Create a new service account (e.g., `study-app-runner`).
    * **Grant this service account "Viewer" access to your Google Sheet** by sharing the sheet with its email address.
    * This service account does *not* need a downloadable key if it's assigned to the Cloud Run service; it will use the metadata server for credentials.
4.  **Build and Deploy:**
    You can build the container image using Google Cloud Build and deploy to Cloud Run in one step:
    ```bash
    gcloud run deploy gcp-study-app \
        --source . \
        --platform managed \
        --region YOUR_REGION \
        --allow-unauthenticated \
        --service-account YOUR_CLOUD_RUN_SERVICE_ACCOUNT_EMAIL \
        --set-env-vars GOOGLE_SHEET_ID="your_google_sheet_id" \
        --set-env-vars FLASK_SECRET_KEY="generate_a_strong_random_secret_for_production" \
        --set-env-vars GOOGLE_ENTRYPOINT="gunicorn -b :$PORT app:app"
    ```
    Replace `YOUR_REGION` with your preferred region (e.g., `us-central1`), `YOUR_CLOUD_RUN_SERVICE_ACCOUNT_EMAIL` with the email of the service account you created for Cloud Run, and `your_google_sheet_id` with the actual ID.
    **Important:** `FLASK_SECRET_KEY` should be a strong, random string for production.

    * `--allow-unauthenticated` makes the app publicly accessible. Remove this if you want to manage access through IAM.
    * The `Dockerfile` in your source code will be used by Cloud Build.

5.  **Access your app:** The command will output the URL of your deployed service.

## Important Notes:

* **Sheet Formatting:** The app expects the column headers as specified. Empty rows or rows with missing essential columns (`Question`, `Correct Answer`) will be skipped.
* **Error Handling:** Basic error handling is in place for sheet access and missing data. Check application logs in Cloud Run for more details if issues arise.
* **Security:**
    * Ensure your `FLASK_SECRET_KEY` is strong and kept secret in production.
    * The Google Sheet should only be shared with the service account with read-only ("Viewer") permissions if that's all the app needs.
* **Scalability:** For a small to medium number of users, this setup should work well. If you anticipate very high traffic, you might consider caching strategies for the Google Sheet data within the Flask app (e.g., using Flask-Caching) to reduce API calls.
