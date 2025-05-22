import os
import gspread
from google.oauth2.service_account import Credentials # For local dev with JSON key
from google.auth import default # For Cloud Run environment
from flask import Flask, render_template, redirect, url_for, request, session, abort

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "your_very_secret_key_for_dev") # Change for production!

# --- Configuration ---
# The ID of your Google Sheet (from its URL)
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")

# --- Google Sheets Setup ---
def get_gspread_client():
    """Authenticates with Google Sheets API."""
    try:
        if os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
            # Use service account key file if path is set (local development)
            creds = Credentials.from_service_account_file(
                os.environ['GOOGLE_APPLICATION_CREDENTIALS'],
                scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
            )
        else:
            # Use default credentials (suitable for Cloud Run with service account IAM)
            creds, _ = default(scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
        
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        app.logger.error(f"Error initializing gspread client: {e}")
        return None

def get_exam_sheets(client):
    """Gets all sheet names (exams) from the Google Sheet."""
    if not GOOGLE_SHEET_ID:
        app.logger.error("GOOGLE_SHEET_ID not set.")
        return [], "GOOGLE_SHEET_ID environment variable not set."
    try:
        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
        return [sheet.title for sheet in spreadsheet.worksheets()], None
    except gspread.exceptions.SpreadsheetNotFound:
        app.logger.error("Spreadsheet not found. Check ID and permissions.")
        return [], "Spreadsheet not found. Please check the GOOGLE_SHEET_ID and ensure the service account has access."
    except Exception as e:
        app.logger.error(f"Error fetching sheet names: {e}")
        return [], f"An error occurred: {e}"

def get_questions_for_exam(client, exam_name):
    """Gets all questions for a given exam (sheet name)."""
    if not GOOGLE_SHEET_ID:
        return [], "GOOGLE_SHEET_ID environment variable not set."
    try:
        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = spreadsheet.worksheet(exam_name)
        # Fetches all records, skipping the header row (gspread assumes first row is header)
        records = worksheet.get_all_records() 
        
        questions = []
        for idx, row in enumerate(records):
            # Validate that essential columns exist
            required_cols = ['Question', 'Answer A', 'Answer B', 'Answer C', 'Answer D', 'Correct Answer', 'Explanation-Correct']
            if not all(col in row for col in required_cols):
                app.logger.warning(f"Skipping row {idx+2} in '{exam_name}' due to missing columns: {row}")
                continue
            if not row.get('Question') or not row.get('Correct Answer'): # Skip if essential data is empty
                app.logger.warning(f"Skipping row {idx+2} in '{exam_name}' due to empty Question or Correct Answer.")
                continue

            questions.append({
                "id": idx,
                "question": row.get('Question', '').strip(),
                "options": {
                    "A": row.get('Answer A', ''),
                    "B": row.get('Answer B', ''),
                    "C": row.get('Answer C', ''),
                    "D": row.get('Answer D', '')
                },
                "correct_option_key": str(row.get('Correct Answer', '')).strip().upper(),
                "explanation_correct": row.get('Explanation-Correct', ''),
                "explanation_incorrect": row.get('Explanation-Incorrect', '') # Optional
            })
        return questions, None
    except gspread.exceptions.WorksheetNotFound:
        app.logger.error(f"Worksheet '{exam_name}' not found.")
        return [], f"Exam '{exam_name}' not found in the Google Sheet."
    except Exception as e:
        app.logger.error(f"Error fetching questions for {exam_name}: {e}")
        return [], f"An error occurred while fetching questions: {e}"

# --- Flask Routes ---
@app.route('/')
def main_page():
    client = get_gspread_client()
    if not client:
        return render_template('main.html', error="Could not connect to Google Sheets API. Check logs.")
    
    exams, error_msg = get_exam_sheets(client)
    if error_msg:
        return render_template('main.html', error=error_msg)
    
    session.clear() # Clear any previous exam state
    return render_template('main.html', exams=exams, title="Select Exam")

@app.route('/exam/<exam_name>')
def start_exam(exam_name):
    client = get_gspread_client()
    if not client:
        abort(500, "Could not connect to Google Sheets API.")

    questions, error_msg = get_questions_for_exam(client, exam_name)
    if error_msg:
        # Redirect to main page with error if exam questions can't be loaded
        return redirect(url_for('main_page', error=error_msg))
    
    if not questions:
        return redirect(url_for('main_page', error=f"No questions found for exam '{exam_name}'. Please check the sheet format."))

    session['current_exam_questions'] = questions
    session['exam_name'] = exam_name
    return redirect(url_for('show_question', exam_name=exam_name, question_index=0))

@app.route('/exam/<exam_name>/question/<int:question_index>')
def show_question(exam_name, question_index):
    if 'current_exam_questions' not in session or session.get('exam_name') != exam_name:
        return redirect(url_for('start_exam', exam_name=exam_name))

    questions = session['current_exam_questions']
    if not 0 <= question_index < len(questions):
        # If index is out of bounds, maybe they finished or URL was manipulated
        return redirect(url_for('main_page', error="Invalid question index."))

    current_question = questions[question_index]
    
    # Check if there's feedback for this question in the session
    feedback_info = session.pop(f'feedback_q{current_question["id"]}', None)

    return render_template('flashcard.html',
                           title=f"{exam_name} - Q{question_index+1}",
                           exam_name=exam_name,
                           question_index=question_index,
                           total_questions=len(questions),
                           current_question=current_question,
                           feedback=feedback_info)

@app.route('/exam/<exam_name>/question/<int:question_id>/answer', methods=['POST'])
def submit_answer(exam_name, question_id):
    if 'current_exam_questions' not in session or session.get('exam_name') != exam_name:
        return redirect(url_for('start_exam', exam_name=exam_name))

    questions = session['current_exam_questions']
    current_question = next((q for q in questions if q['id'] == question_id), None)
    question_index = next((i for i, q in enumerate(questions) if q['id'] == question_id), None)


    if current_question is None or question_index is None:
        abort(404, "Question not found.")

    user_answer_key = request.form.get('answer')
    user_answer_text = current_question['options'].get(user_answer_key, "N/A")
    is_correct = (user_answer_key == current_question['correct_option_key'])

    feedback = {
        "user_answer": user_answer_key,
        "user_answer_text": user_answer_text,
        "is_correct": is_correct,
        "correct_answer_key": current_question['correct_option_key'],
        "correct_answer_text": current_question['options'].get(current_question['correct_option_key'])
    }
    session[f'feedback_q{current_question["id"]}'] = feedback
    
    return redirect(url_for('show_question', exam_name=exam_name, question_index=question_index))


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
