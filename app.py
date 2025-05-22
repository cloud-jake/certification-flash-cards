import os
import gspread
from google.oauth2.service_account import Credentials
from google.auth import default
from flask import Flask, render_template, redirect, url_for, request, session, abort
import time
import threading # For cache lock

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "your_very_secret_key_for_dev")

# --- Configuration ---
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")

# --- Caching Setup ---
EXAM_DATA_CACHE = {}
CACHE_DURATION_SECONDS = int(os.environ.get("CACHE_DURATION_SECONDS", 10 * 60))
CACHE_LOCK = threading.Lock()

# --- Google Sheets Setup (get_gspread_client, get_exam_sheets, _fetch_and_parse_questions, get_questions_for_exam_from_sheet, get_cached_questions_for_exam) ---
# ... (Keep these functions exactly as they were in the previous correct version) ...
# For brevity, I'm omitting the Google Sheets and caching functions here,
# but they should remain unchanged from the version that fixed the API quota error.
# Ensure get_cached_questions_for_exam is present and working.

# <PASTE PREVIOUSLY WORKING Google Sheets and Caching functions here>
# --- Google Sheets Setup ---
def get_gspread_client():
    """Authenticates with Google Sheets API."""
    try:
        if os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
            creds = Credentials.from_service_account_file(
                os.environ['GOOGLE_APPLICATION_CREDENTIALS'],
                scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
            )
        else:
            creds, _ = default(scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        app.logger.error(f"Error initializing gspread client: {e}")
        return None

def get_exam_sheets(client):
    if not GOOGLE_SHEET_ID:
        app.logger.error("GOOGLE_SHEET_ID not set.")
        return [], "GOOGLE_SHEET_ID environment variable not set."
    try:
        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
        return [sheet.title for sheet in spreadsheet.worksheets()], None
    except gspread.exceptions.SpreadsheetNotFound:
        app.logger.error("Spreadsheet not found. Check ID and permissions.")
        return [], "Spreadsheet not found. Please check the GOOGLE_SHEET_ID and ensure the service account has access."
    except gspread.exceptions.APIError as e:
        app.logger.error(f"Google Sheets API Error fetching sheet names: {e}")
        if e.response.status_code == 429: # type: ignore
            return [], "Quota exceeded while fetching list of exams. Please try again in a minute."
        return [], f"A Google Sheets API error occurred: {e}"
    except Exception as e:
        app.logger.error(f"Error fetching sheet names: {e}")
        return [], f"An error occurred: {e}"

def _fetch_and_parse_questions(worksheet, exam_name):
    all_values = worksheet.get_values()
    if not all_values or len(all_values) < 2:
        app.logger.warning(f"No data or no data rows found in worksheet '{exam_name}'.")
        return [], f"No questions found for exam '{exam_name}'. Sheet might be empty or only contain a header."
    header_row = [h.strip() for h in all_values[0]]
    data_rows = all_values[1:]
    expected_headers = {
        "question_col": "Question", "ans_a_col": "Answer A", "ans_b_col": "Answer B",
        "ans_c_col": "Answer C", "ans_d_col": "Answer D", "correct_col": "Correct Answer",
        "exp_correct_col": "Explanation-Correct", "exp_incorrect_col": "Explanation-Incorrect"
    }
    if not all(h in header_row for h in [expected_headers["question_col"], expected_headers["correct_col"]]):
        app.logger.error(f"Missing critical headers in '{exam_name}'. Found: {header_row}.")
        return [], f"Sheet '{exam_name}' is missing critical header columns."
    questions = []
    for idx, row_values in enumerate(data_rows):
        row_dict = {}
        for i, header_name in enumerate(header_row):
            if i < len(row_values): row_dict[header_name] = row_values[i]
            else: row_dict[header_name] = ''
        question_text = row_dict.get(expected_headers["question_col"], "").strip()
        correct_answer_key = str(row_dict.get(expected_headers["correct_col"], "")).strip().upper()
        if not question_text or not correct_answer_key:
            app.logger.warning(f"Skipping row {idx+2} in '{exam_name}' due to empty Question/Correct Answer.")
            continue
        questions.append({
            "id": idx, "question": question_text,
            "options": {
                "A": row_dict.get(expected_headers["ans_a_col"], ''), "B": row_dict.get(expected_headers["ans_b_col"], ''),
                "C": row_dict.get(expected_headers["ans_c_col"], ''), "D": row_dict.get(expected_headers["ans_d_col"], '')
            },
            "correct_option_key": correct_answer_key,
            "explanation_correct": row_dict.get(expected_headers["exp_correct_col"], ''),
            "explanation_incorrect": row_dict.get(expected_headers["exp_incorrect_col"], '')
        })
    if not questions:
        app.logger.warning(f"No valid questions processed for exam '{exam_name}'.")
        return [], f"No valid questions found for exam '{exam_name}' after processing."
    return questions, None

def get_questions_for_exam_from_sheet(client, exam_name):
    if not GOOGLE_SHEET_ID: return [], "GOOGLE_SHEET_ID environment variable not set."
    try:
        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = spreadsheet.worksheet(exam_name)
        return _fetch_and_parse_questions(worksheet, exam_name)
    except gspread.exceptions.WorksheetNotFound:
        app.logger.error(f"Worksheet '{exam_name}' not found.")
        return [], f"Exam '{exam_name}' not found in the Google Sheet."
    except gspread.exceptions.APIError as e:
        app.logger.error(f"Google Sheets API Error for {exam_name}: {e}")
        if e.response.status_code == 429: # type: ignore
             return [], "Quota exceeded while fetching questions. Please try again in a minute."
        return [], f"A Google Sheets API error occurred: {getattr(e, 'message', str(e))}"
    except Exception as e:
        app.logger.error(f"Error fetching questions for {exam_name}: {e}")
        return [], f"An unexpected error occurred: {e}"

def get_cached_questions_for_exam(client, exam_name):
    current_time = time.time()
    with CACHE_LOCK: cached_entry = EXAM_DATA_CACHE.get(exam_name)
    if cached_entry and (current_time - cached_entry['timestamp'] < CACHE_DURATION_SECONDS):
        app.logger.info(f"Serving '{exam_name}' questions from cache.")
        return cached_entry['data'], cached_entry['error']
    app.logger.info(f"Cache expired or not found for '{exam_name}'. Fetching fresh data.")
    questions, error_msg = get_questions_for_exam_from_sheet(client, exam_name)
    with CACHE_LOCK:
        EXAM_DATA_CACHE[exam_name] = {
            'timestamp': current_time, 'data': questions, 'error': error_msg
        }
    return questions, error_msg
# </PASTE PREVIOUSLY WORKING Google Sheets and Caching functions here>


# --- Flask Routes ---
@app.route('/')
def main_page():
    client = get_gspread_client()
    if not client:
        return render_template('main.html', error="Could not connect to Google Sheets API. Check logs.")
    
    exams, error_msg = get_exam_sheets(client)
    if error_msg:
        return render_template('main.html', error=error_msg)
    
    # Clear only specific study-related session keys, not the entire session if not needed
    session.pop('exam_name', None)
    session.pop('question_index', None)
    for key in list(session.keys()): # Clear any previous feedback
        if key.startswith('feedback_q'):
            session.pop(key)
            
    return render_template('main.html', exams=exams, title="Select Exam")

@app.route('/exam/<exam_name>')
def start_exam(exam_name):
    # Ensure questions are loaded into cache if not already (for immediate access in show_question)
    client = get_gspread_client()
    if not client:
        abort(500, "Could not connect to Google Sheets API.")
    
    questions, error_msg = get_cached_questions_for_exam(client, exam_name)
    if error_msg:
        return redirect(url_for('main_page', error=f"Error loading exam '{exam_name}': {error_msg}"))
    if not questions:
        return redirect(url_for('main_page', error=f"No questions found for exam '{exam_name}'."))

    # Store only identifiers in session, NOT the full question list
    session['exam_name'] = exam_name
    session['question_index'] = 0 # Start at the first question
    
    # Clear any previous feedback for this new exam attempt
    for key in list(session.keys()):
        if key.startswith('feedback_q'):
            session.pop(key)
            
    return redirect(url_for('show_question_page')) # Changed to a generic route

@app.route('/question') # Generic route, exam_name and index come from session
def show_question_page():
    exam_name = session.get('exam_name')
    question_index = session.get('question_index')

    if exam_name is None or question_index is None:
        return redirect(url_for('main_page', error="Exam session not initialized. Please select an exam."))

    client = get_gspread_client() # Needed to potentially refresh cache if it was cleared or for first load
    if not client: abort(500, "Could not connect to Google Sheets API.") # Should ideally not happen here if start_exam worked

    # Retrieve questions from server-side cache
    questions, error_msg = get_cached_questions_for_exam(client, exam_name)

    if error_msg:
        return redirect(url_for('main_page', error=f"Error retrieving questions for '{exam_name}': {error_msg}"))
    if not questions:
         return redirect(url_for('main_page', error=f"No questions available for exam '{exam_name}'."))
    
    if not 0 <= question_index < len(questions):
        # Attempt to reset to the first question if index is bad but exam exists
        # Or redirect to main page if something is seriously wrong
        app.logger.warning(f"Invalid question_index {question_index} for exam {exam_name} with {len(questions)} questions. Resetting.")
        # For simplicity, redirect to main page. Could also try resetting index to 0.
        return redirect(url_for('main_page', error="Invalid question index. Please select exam again."))

    current_question = questions[question_index]
    feedback_info = session.get(f'feedback_q{current_question["id"]}')

    return render_template('flashcard.html',
                           title=f"{exam_name} - Q{question_index+1}",
                           exam_name=exam_name,
                           question_index=question_index, # For display and form action
                           total_questions=len(questions),
                           current_question=current_question,
                           feedback=feedback_info)

@app.route('/answer', methods=['POST']) # Generic route, exam_name and index come from session
def submit_answer_page():
    exam_name = session.get('exam_name')
    question_index = session.get('question_index') # This is the index of the question that was answered

    if exam_name is None or question_index is None:
        return redirect(url_for('main_page', error="Exam session not initialized."))

    client = get_gspread_client()
    if not client: abort(500, "Could not connect to Google Sheets API.")

    questions, error_msg = get_cached_questions_for_exam(client, exam_name)
    if error_msg or not questions:
        return redirect(url_for('main_page', error=f"Error retrieving questions for '{exam_name}' to process answer."))
    
    if not 0 <= question_index < len(questions):
        return redirect(url_for('main_page', error="Invalid question index during answer submission."))

    current_question = questions[question_index] # The question that was just answered
    
    # The question_id submitted by the form should match current_question["id"]
    # For simplicity, we rely on the question_index from session.
    # submitted_question_id = int(request.form.get('question_id')) # if you add this to form

    user_answer_key = request.form.get('answer')
    user_answer_text = current_question['options'].get(user_answer_key, "N/A")
    is_correct = (user_answer_key == current_question['correct_option_key'])

    feedback = {
        "user_answer": user_answer_key,
        "user_answer_text": user_answer_text,
        "is_correct": is_correct,
    }
    session[f'feedback_q{current_question["id"]}'] = feedback
    
    # User stays on the same question index to see feedback
    return redirect(url_for('show_question_page'))


@app.route('/next_question')
def next_question_page():
    exam_name = session.get('exam_name')
    question_index = session.get('question_index')

    if exam_name is None or question_index is None:
        return redirect(url_for('main_page', error="Exam session not initialized."))

    client = get_gspread_client()
    if not client: abort(500, "Could not connect to Google Sheets API.")
    
    questions, error_msg = get_cached_questions_for_exam(client, exam_name) # Get total_questions
    if error_msg or not questions:
         return redirect(url_for('main_page', error=f"Error retrieving questions for '{exam_name}'."))

    total_questions = len(questions)
    if question_index + 1 < total_questions:
        session['question_index'] = question_index + 1
    else:
        # Optionally, redirect to a "completed" page or back to main
        # For now, just stay on the last question if they try to go beyond
        # Or, more clearly:
        # session['question_index'] = total_questions -1 # Stay on last if they click next again
        # A better UX might be to go to main page or show a summary
        return redirect(url_for('main_page', message="You've completed all questions for this exam!"))


    return redirect(url_for('show_question_page'))


if __name__ == '__main__':
    app.run(debug=os.environ.get("FLASK_DEBUG", "False").lower() == "true",
            host='0.0.0.0',
            port=int(os.environ.get('PORT', 8080)))