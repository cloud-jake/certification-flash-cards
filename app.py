import os
import gspread
from google.oauth2.service_account import Credentials
from google.auth import default
from flask import Flask, render_template, redirect, url_for, request, session, abort
import time
import threading # For cache lock

app = Flask(__name__)
# IMPORTANT: Set a strong, random secret key in your environment for production!
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev_default_strong_random_secret_key_123!")

# --- Configuration ---
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
if not GOOGLE_SHEET_ID:
    app.logger.warning("GOOGLE_SHEET_ID environment variable is not set!")


# --- Caching Setup ---
# { 'exam_name': {'timestamp': time.time(), 'data': questions_list, 'error': error_msg_or_None} }
EXAM_DATA_CACHE = {}
CACHE_DURATION_SECONDS = int(os.environ.get("CACHE_DURATION_SECONDS", 10 * 60))  # Cache for 10 minutes by default
CACHE_LOCK = threading.Lock() # To prevent race conditions during cache updates

# --- Google Sheets Setup ---
def get_gspread_client():
    """Authenticates with Google Sheets API."""
    try:
        scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly']
        if os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
            creds = Credentials.from_service_account_file(
                os.environ['GOOGLE_APPLICATION_CREDENTIALS'], scopes=scopes
            )
        else:
            # Use default credentials (suitable for Cloud Run with service account IAM)
            creds, _ = default(scopes=scopes)
        
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        app.logger.error(f"Error initializing gspread client: {e}")
        return None

def get_exam_sheets(client):
    """Gets all sheet names (exams) from the Google Sheet,
       filtering out sheets that begin with an underscore. Includes debug logging."""
    if not GOOGLE_SHEET_ID:
        app.logger.error("GOOGLE_SHEET_ID not set for get_exam_sheets.")
        return [], "GOOGLE_SHEET_ID environment variable not set."
    if not client:
        app.logger.error("gspread client not available for get_exam_sheets.")
        return [], "gspread client not available."
        
    try:
        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
        
        # --- Start Debug Logging for Sheet Titles ---
        app.logger.info("Fetching all worksheet titles from Google Sheet...")
        all_worksheet_titles = []
        try:
            worksheets = spreadsheet.worksheets()
        except Exception as e_ws:
            app.logger.error(f"Error calling spreadsheet.worksheets(): {e_ws}")
            return [], f"Could not retrieve worksheets: {e_ws}"

        for sheet in worksheets:
            all_worksheet_titles.append(sheet.title)
            # Log each title and what startswith('_') returns for it
            app.logger.info(f"Sheet Title Raw: '{sheet.title}', Starts with '_': {sheet.title.startswith('_')}")
        app.logger.info(f"ALL TITLES RETRIEVED FROM SPREADSHEET: {all_worksheet_titles}")
        # --- End Debug Logging ---

        exam_titles = [
            sheet_title for sheet_title in all_worksheet_titles
            if not sheet_title.startswith('_')
        ]
        app.logger.info(f"FILTERED EXAM TITLES TO BE DISPLAYED: {exam_titles}")
        
        return exam_titles, None
    except gspread.exceptions.SpreadsheetNotFound:
        app.logger.error(f"Spreadsheet not found with ID: {GOOGLE_SHEET_ID}. Check ID and permissions.")
        return [], "Spreadsheet not found. Please check the GOOGLE_SHEET_ID and ensure the service account has access."
    except gspread.exceptions.APIError as e:
        app.logger.error(f"Google Sheets API Error fetching sheet names: {e}")
        status_code = getattr(getattr(e, 'response', None), 'status_code', None)
        if status_code == 429:
            return [], "Quota exceeded while fetching list of exams. Please try again in a minute."
        return [], f"A Google Sheets API error occurred: {e}"
    except Exception as e:
        app.logger.error(f"An unexpected error in get_exam_sheets: {e}")
        return [], f"An unexpected error occurred while fetching sheet names: {e}"


def _fetch_and_parse_questions(worksheet, exam_name):
    """Helper function to fetch and parse questions using get_values()."""
    try:
        all_values = worksheet.get_values()
    except Exception as e_gv:
        app.logger.error(f"Error calling worksheet.get_values() for exam '{exam_name}': {e_gv}")
        return [], f"Could not retrieve values from worksheet '{exam_name}': {e_gv}"

    if not all_values or len(all_values) < 2: # Need at least a header and one data row
        app.logger.warning(f"No data or no data rows found in worksheet '{exam_name}'. Values count: {len(all_values) if all_values else 0}")
        return [], f"No questions found for exam '{exam_name}'. Sheet might be empty or only contain a header."

    header_row = [str(h).strip() for h in all_values[0]]
    data_rows = all_values[1:]

    expected_headers = {
        "question_col": "Question", "ans_a_col": "Answer A", "ans_b_col": "Answer B",
        "ans_c_col": "Answer C", "ans_d_col": "Answer D", "correct_col": "Correct Answer",
        "exp_correct_col": "Explanation-Correct", "exp_incorrect_col": "Explanation-Incorrect"
    }

    if not all(h_key in header_row for h_key in [expected_headers["question_col"], expected_headers["correct_col"]]):
        app.logger.error(f"Missing critical headers in '{exam_name}'. Found: {header_row}. Expected at least: '{expected_headers['question_col']}', '{expected_headers['correct_col']}'.")
        return [], f"Sheet '{exam_name}' is missing critical header columns (e.g., Question, Correct Answer)."

    questions = []
    for idx, row_values in enumerate(data_rows):
        row_dict = {}
        for i, header_name in enumerate(header_row):
            if i < len(row_values):
                row_dict[header_name] = str(row_values[i]) # Ensure string conversion
            else:
                row_dict[header_name] = ''

        question_text = row_dict.get(expected_headers["question_col"], "").strip()
        correct_answer_key = row_dict.get(expected_headers["correct_col"], "").strip().upper()

        if not question_text or not correct_answer_key:
            app.logger.warning(f"Skipping row {idx+2} in '{exam_name}' due to empty Question ('{question_text}') or Correct Answer ('{correct_answer_key}').")
            continue
        
        questions.append({
            "id": idx, # 0-based index of data_rows
            "question": question_text,
            "options": {
                "A": row_dict.get(expected_headers["ans_a_col"], ''),
                "B": row_dict.get(expected_headers["ans_b_col"], ''),
                "C": row_dict.get(expected_headers["ans_c_col"], ''),
                "D": row_dict.get(expected_headers["ans_d_col"], '')
            },
            "correct_option_key": correct_answer_key,
            "explanation_correct": row_dict.get(expected_headers["exp_correct_col"], ''),
            "explanation_incorrect": row_dict.get(expected_headers["exp_incorrect_col"], '')
        })
    
    if not questions:
        app.logger.warning(f"No valid questions processed for exam '{exam_name}' after filtering all rows.")
        return [], f"No valid questions found for exam '{exam_name}' after processing the sheet rows."
    
    app.logger.info(f"Successfully parsed {len(questions)} questions for exam '{exam_name}'.")
    return questions, None


def get_questions_for_exam_from_sheet(client, exam_name):
    """
    Fetches questions for a given exam directly from the Google Sheet.
    Uses get_values() for efficiency.
    """
    if not GOOGLE_SHEET_ID:
        return [], "GOOGLE_SHEET_ID environment variable not set."
    if not client:
        return [], "gspread client not available."
    try:
        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = spreadsheet.worksheet(exam_name)
        return _fetch_and_parse_questions(worksheet, exam_name)
        
    except gspread.exceptions.WorksheetNotFound:
        app.logger.error(f"Worksheet '{exam_name}' not found in spreadsheet ID {GOOGLE_SHEET_ID}.")
        return [], f"Exam tab '{exam_name}' not found in the Google Sheet."
    except gspread.exceptions.APIError as e:
        app.logger.error(f"Google Sheets API Error for {exam_name}: {e}")
        status_code = getattr(getattr(e, 'response', None), 'status_code', None)
        if status_code == 429:
            return [], "Quota exceeded while fetching questions. Please try again in a minute."
        return [], f"A Google Sheets API error occurred: {getattr(e, 'message', str(e))}"
    except Exception as e:
        app.logger.error(f"Error fetching questions for {exam_name}: {e}")
        return [], f"An unexpected error occurred while fetching questions: {e}"


def get_cached_questions_for_exam(client, exam_name):
    """
    Retrieves questions for an exam, using a cache if available and valid.
    If not cached or cache is stale, fetches from sheet and updates cache.
    """
    current_time = time.time()
    
    # Check cache first (read operation)
    with CACHE_LOCK: # Lock for reading to ensure consistency if an update is happening
        cached_entry = EXAM_DATA_CACHE.get(exam_name)

    if cached_entry and (current_time - cached_entry['timestamp'] < CACHE_DURATION_SECONDS):
        # Check if cached entry was an error, if so, don't serve stale error for too long unless it's a quota error
        if cached_entry['error'] and "Quota exceeded" not in cached_entry['error']:
             if current_time - cached_entry['timestamp'] < 60: # Re-fetch non-quota errors faster (e.g. 1 min)
                app.logger.info(f"Serving '{exam_name}' error from short-term cache: {cached_entry['error']}")
                return cached_entry['data'], cached_entry['error']
        else:
            app.logger.info(f"Serving '{exam_name}' questions from cache. Cached at {time.ctime(cached_entry['timestamp'])}.")
            return cached_entry['data'], cached_entry['error'] # Return cached data (or cached error)
    
    app.logger.info(f"Cache expired or not found (or error re-fetch triggered) for '{exam_name}'. Fetching fresh data.")
    questions, error_msg = get_questions_for_exam_from_sheet(client, exam_name)

    # Update cache (write operation)
    with CACHE_LOCK:
        EXAM_DATA_CACHE[exam_name] = {
            'timestamp': current_time, 
            'data': questions,
            'error': error_msg
        }
        app.logger.info(f"Updated cache for '{exam_name}' at {time.ctime(current_time)}. Error: {error_msg is not None}")
    return questions, error_msg


# --- Flask Routes ---
@app.route('/')
def main_page():
    client = get_gspread_client()
    if not client:
        app.logger.error("Main page: Could not get gspread client.")
        return render_template('main.html', error="Could not connect to Google Sheets API. Please check application logs or try again later.", exams=[])
    
    exams, error_msg = get_exam_sheets(client)
    if error_msg:
        app.logger.error(f"Main page: Error fetching exam sheets: {error_msg}")
        # Display the error on the main page if exam list fetching fails
        return render_template('main.html', error=error_msg, exams=[]) 
    
    # Clear specific study-related session keys
    session.pop('exam_name', None)
    session.pop('question_index', None)
    # Clear any previous feedback more selectively
    feedback_keys_to_pop = [key for key in session if key.startswith('feedback_q')]
    for key in feedback_keys_to_pop:
        session.pop(key, None)
            
    page_message = request.args.get('message') # For messages like "exam completed"
    return render_template('main.html', exams=exams, title="Select Exam", message=page_message)

@app.route('/exam/<exam_name>')
def start_exam(exam_name):
    client = get_gspread_client()
    if not client:
        app.logger.error(f"Start exam '{exam_name}': Could not get gspread client.")
        abort(500, "Application setup error: Could not connect to data source.") 
    
    # This call also populates/refreshes the server-side cache for this exam
    questions, error_msg = get_cached_questions_for_exam(client, exam_name)
    if error_msg:
        app.logger.error(f"Start exam '{exam_name}': Error loading questions: {error_msg}")
        return redirect(url_for('main_page', error=f"Error loading exam '{exam_name}': {error_msg}"))
    if not questions: # Should ideally be caught by error_msg if the list is empty due to fetch error
        app.logger.warning(f"Start exam '{exam_name}': No questions found after attempting to load.")
        return redirect(url_for('main_page', error=f"No questions found for exam '{exam_name}'."))

    session['exam_name'] = exam_name
    session['question_index'] = 0 # Start at the first question
    
    feedback_keys_to_pop = [key for key in session if key.startswith('feedback_q')]
    for key in feedback_keys_to_pop:
        session.pop(key, None)
            
    return redirect(url_for('show_question_page'))

@app.route('/question') 
def show_question_page():
    exam_name = session.get('exam_name')
    question_index = session.get('question_index')

    if exam_name is None or question_index is None:
        app.logger.warning("Show question: Exam session not initialized.")
        return redirect(url_for('main_page', error="Exam session not initialized. Please select an exam."))

    client = get_gspread_client()
    if not client: 
        app.logger.error("Show question: Could not get gspread client.")
        abort(500, "Application setup error: Could not connect to data source.")

    questions, error_msg = get_cached_questions_for_exam(client, exam_name)

    if error_msg:
        app.logger.error(f"Show question '{exam_name}': Error retrieving questions from cache/source: {error_msg}")
        return redirect(url_for('main_page', error=f"Error retrieving questions for '{exam_name}': {error_msg}"))
    if not questions:
        app.logger.warning(f"Show question '{exam_name}': No questions available from cache/source.")
        return redirect(url_for('main_page', error=f"No questions available for exam '{exam_name}'."))
    
    if not 0 <= question_index < len(questions):
        app.logger.warning(f"Show question '{exam_name}': Invalid question_index {question_index} for {len(questions)} questions. Resetting to main.")
        return redirect(url_for('main_page', error="Invalid question index. Please select exam again."))

    current_question = questions[question_index]
    # Retrieve feedback for this specific question using its unique ID
    feedback_info = session.get(f'feedback_q{current_question["id"]}') 

    return render_template('flashcard.html',
                           title=f"{exam_name} - Q{question_index+1}",
                           exam_name=exam_name,
                           question_index=question_index,
                           total_questions=len(questions),
                           current_question=current_question,
                           feedback=feedback_info)

@app.route('/answer', methods=['POST'])
def submit_answer_page():
    exam_name = session.get('exam_name')
    question_index = session.get('question_index') # Index of the question that was displayed and answered

    if exam_name is None or question_index is None:
        app.logger.warning("Submit answer: Exam session not initialized.")
        return redirect(url_for('main_page', error="Exam session not initialized."))

    client = get_gspread_client()
    if not client: 
        app.logger.error("Submit answer: Could not get gspread client.")
        abort(500, "Application setup error: Could not connect to data source.")

    questions, error_msg = get_cached_questions_for_exam(client, exam_name)
    if error_msg or not questions:
        app.logger.error(f"Submit answer '{exam_name}': Error retrieving questions from cache/source: {error_msg if error_msg else 'No questions found'}")
        return redirect(url_for('main_page', error=f"Error retrieving questions for '{exam_name}' to process answer."))
    
    if not 0 <= question_index < len(questions):
        app.logger.warning(f"Submit answer '{exam_name}': Invalid question_index {question_index} during answer submission.")
        return redirect(url_for('main_page', error="Invalid question index during answer submission."))

    current_question = questions[question_index]
    
    user_answer_key = request.form.get('answer')
    if not user_answer_key: # Handle case where form might not submit 'answer' if no option selected (though buttons should always submit)
        app.logger.warning(f"Submit answer '{exam_name}', Q{question_index}: No answer submitted in form.")
        # Or redirect back with a message:
        return redirect(url_for('show_question_page'))


    user_answer_text = current_question['options'].get(user_answer_key, "N/A")
    is_correct = (user_answer_key == current_question['correct_option_key'])

    feedback = {
        "user_answer": user_answer_key,
        "user_answer_text": user_answer_text,
        "is_correct": is_correct,
    }
    # Store feedback using the question's unique ID from the list
    session[f'feedback_q{current_question["id"]}'] = feedback 
    app.logger.info(f"Answer submitted for exam '{exam_name}', Q_id {current_question['id']} (index {question_index}): User chose '{user_answer_key}', Correct: {is_correct}")
    
    return redirect(url_for('show_question_page'))


@app.route('/next_question')
def next_question_page():
    exam_name = session.get('exam_name')
    question_index = session.get('question_index')

    if exam_name is None or question_index is None:
        app.logger.warning("Next question: Exam session not initialized.")
        return redirect(url_for('main_page', error="Exam session not initialized."))

    client = get_gspread_client()
    if not client: 
        app.logger.error("Next question: Could not get gspread client.")
        abort(500, "Application setup error: Could not connect to data source.")
    
    questions, error_msg = get_cached_questions_for_exam(client, exam_name)
    if error_msg or not questions:
        app.logger.error(f"Next question '{exam_name}': Error retrieving questions from cache/source: {error_msg if error_msg else 'No questions found'}")
        return redirect(url_for('main_page', error=f"Error retrieving questions for '{exam_name}'."))

    total_questions = len(questions)
    # Clear feedback for the question we are moving away from
    if 0 <= question_index < total_questions:
        question_id_to_clear_feedback = questions[question_index]['id']
        session.pop(f'feedback_q{question_id_to_clear_feedback}', None)


    if question_index + 1 < total_questions:
        session['question_index'] = question_index + 1
        app.logger.info(f"Moving to next question for exam '{exam_name}', new index: {session['question_index']}")
    else:
        app.logger.info(f"Completed all questions for exam '{exam_name}'.")
        session.pop('exam_name', None) # Clear exam from session
        session.pop('question_index', None)
        return redirect(url_for('main_page', message=f"You've completed all questions for {exam_name}! Choose another exam."))

    return redirect(url_for('show_question_page'))


if __name__ == '__main__':
    # For local development, FLASK_DEBUG is useful.
    # Set FLASK_DEBUG=True or False as an environment variable.
    # Cloud Run sets the PORT environment variable.
    is_debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
    app.logger.info(f"Application starting in {'DEBUG' if is_debug_mode else 'PRODUCTION'} mode.")
    app.run(debug=is_debug_mode, 
            host='0.0.0.0', 
            port=int(os.environ.get('PORT', 8080)))