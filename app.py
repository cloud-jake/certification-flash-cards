import os
import gspread
from google.oauth2.service_account import Credentials
from google.auth import default
from flask import Flask, render_template, redirect, url_for, request, session, abort
import time
import threading # For cache lock
import random # For shuffling questions

app = Flask(__name__)
# IMPORTANT: Set a strong, random secret key in your environment for production!
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev_default_strong_random_secret_key_123!")

# --- Configuration ---
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
if not GOOGLE_SHEET_ID:
    app.logger.warning("GOOGLE_SHEET_ID environment variable is not set!")

# --- Caching Setup ---
EXAM_DATA_CACHE = {}
CACHE_DURATION_SECONDS = int(os.environ.get("CACHE_DURATION_SECONDS", 10 * 60))
CACHE_LOCK = threading.Lock()

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
            creds, _ = default(scopes=scopes)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        app.logger.error(f"Error initializing gspread client: {e}")
        return None

def get_exam_sheets(client):
    """Gets all sheet names (exams) from the Google Sheet,
       filtering out sheets that begin with an underscore."""
    if not GOOGLE_SHEET_ID:
        app.logger.error("GOOGLE_SHEET_ID not set for get_exam_sheets.")
        return [], "GOOGLE_SHEET_ID environment variable not set."
    if not client:
        app.logger.error("gspread client not available for get_exam_sheets.")
        return [], "gspread client not available."
        
    try:
        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
        app.logger.info("Fetching all worksheet titles from Google Sheet for exam list...")
        all_worksheet_titles = [sheet.title for sheet in spreadsheet.worksheets()]
        app.logger.info(f"ALL TITLES RETRIEVED: {all_worksheet_titles}")

        exam_titles = [
            sheet_title for sheet_title in all_worksheet_titles
            if not sheet_title.startswith('_')
        ]
        app.logger.info(f"FILTERED EXAM TITLES: {exam_titles}")
        return exam_titles, None
    except gspread.exceptions.SpreadsheetNotFound:
        app.logger.error(f"Spreadsheet not found with ID: {GOOGLE_SHEET_ID}.")
        return [], "Spreadsheet not found."
    except gspread.exceptions.APIError as e:
        app.logger.error(f"Google Sheets API Error fetching sheet names: {e}")
        status_code = getattr(getattr(e, 'response', None), 'status_code', None)
        if status_code == 429:
            return [], "Quota exceeded while fetching list of exams."
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

    if not all_values or len(all_values) < 2:
        app.logger.warning(f"No data or no data rows in '{exam_name}'. Values count: {len(all_values) if all_values else 0}")
        return [], f"No questions found for exam '{exam_name}'. Sheet empty or only header."

    header_row = [str(h).strip() for h in all_values[0]]
    data_rows = all_values[1:]
    expected_headers = {
        "question_col": "Question", "ans_a_col": "Answer A", "ans_b_col": "Answer B",
        "ans_c_col": "Answer C", "ans_d_col": "Answer D", "correct_col": "Correct Answer",
        "exp_correct_col": "Explanation-Correct", "exp_incorrect_col": "Explanation-Incorrect"
    }

    if not all(h_key in header_row for h_key in [expected_headers["question_col"], expected_headers["correct_col"]]):
        app.logger.error(f"Missing critical headers in '{exam_name}'. Found: {header_row}.")
        return [], f"Sheet '{exam_name}' is missing critical headers (Question, Correct Answer)."

    questions = []
    for idx, row_values in enumerate(data_rows):
        row_dict = {header_name: (str(row_values[i]) if i < len(row_values) else '') for i, header_name in enumerate(header_row)}
        question_text = row_dict.get(expected_headers["question_col"], "").strip()
        correct_answer_key = row_dict.get(expected_headers["correct_col"], "").strip().upper()

        if not question_text or not correct_answer_key:
            app.logger.warning(f"Skipping row {idx+2} in '{exam_name}': empty Question/Correct Answer.")
            continue
        
        questions.append({
            "id": idx, # This 'id' is the original 0-based index from the sheet
            "question": question_text,
            "options": {key[0].upper(): row_dict.get(expected_headers[f"ans_{key[0].lower()}_col"], '') for key in ["A", "B", "C", "D"]},
            "correct_option_key": correct_answer_key,
            "explanation_correct": row_dict.get(expected_headers["exp_correct_col"], ''),
            "explanation_incorrect": row_dict.get(expected_headers["exp_incorrect_col"], '')
        })
    
    if not questions:
        app.logger.warning(f"No valid questions processed for '{exam_name}'.")
        return [], f"No valid questions found for exam '{exam_name}' after processing."
    
    app.logger.info(f"Parsed {len(questions)} questions for exam '{exam_name}'.")
    return questions, None

def get_questions_for_exam_from_sheet(client, exam_name):
    if not GOOGLE_SHEET_ID: return [], "GOOGLE_SHEET_ID not set."
    if not client: return [], "gspread client not available."
    try:
        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = spreadsheet.worksheet(exam_name)
        return _fetch_and_parse_questions(worksheet, exam_name)
    except gspread.exceptions.WorksheetNotFound:
        app.logger.error(f"Worksheet '{exam_name}' not found in {GOOGLE_SHEET_ID}.")
        return [], f"Exam tab '{exam_name}' not found."
    except gspread.exceptions.APIError as e:
        app.logger.error(f"Sheets API Error for {exam_name}: {e}")
        status_code = getattr(getattr(e, 'response', None), 'status_code', None)
        if status_code == 429: return [], "Quota exceeded fetching questions."
        return [], f"Sheets API error: {getattr(e, 'message', str(e))}"
    except Exception as e:
        app.logger.error(f"Error fetching questions for {exam_name}: {e}")
        return [], f"Unexpected error fetching questions: {e}"

def get_cached_questions_for_exam(client, exam_name):
    current_time = time.time()
    with CACHE_LOCK: cached_entry = EXAM_DATA_CACHE.get(exam_name)

    if cached_entry and (current_time - cached_entry['timestamp'] < CACHE_DURATION_SECONDS):
        if cached_entry['error'] and "Quota exceeded" not in cached_entry['error'] and \
           current_time - cached_entry['timestamp'] > 60: # Re-fetch non-quota errors after 1 min
            app.logger.info(f"Stale non-quota error in cache for '{exam_name}'. Re-fetching.")
        else:
            app.logger.info(f"Serving '{exam_name}' from cache. Cached at {time.ctime(cached_entry['timestamp'])}.")
            return cached_entry['data'], cached_entry['error']
    
    app.logger.info(f"Cache miss/expired/stale-error for '{exam_name}'. Fetching fresh data.")
    questions, error_msg = get_questions_for_exam_from_sheet(client, exam_name)
    with CACHE_LOCK:
        EXAM_DATA_CACHE[exam_name] = {
            'timestamp': current_time, 'data': questions, 'error': error_msg
        }
        app.logger.info(f"Updated cache for '{exam_name}'. Error: {error_msg is not None}")
    return questions, error_msg

# --- Flask Routes ---
@app.route('/')
def main_page():
    client = get_gspread_client()
    if not client:
        app.logger.error("Main page: No gspread client.")
        return render_template('main.html', error="Could not connect to data source.", exams=[])
    
    exams, error_msg = get_exam_sheets(client)
    if error_msg:
        app.logger.error(f"Main page: Error fetching exam sheets: {error_msg}")
        return render_template('main.html', error=error_msg, exams=exams or []) 
    
    session.pop('exam_name', None)
    session.pop('shuffled_question_indices', None) # For randomized version
    session.pop('current_shuffled_idx_position', None) # For randomized version
    for key in [k for k in session if k.startswith('feedback_q')]: session.pop(key, None)
            
    return render_template('main.html', exams=exams, title="Select Exam", message=request.args.get('message'))

@app.route('/exam/<exam_name>')
def start_exam(exam_name):
    client = get_gspread_client()
    if not client:
        app.logger.error(f"Start exam '{exam_name}': No gspread client.")
        abort(500, "Application setup error: Could not connect to data source.") 
    
    questions_original_order, error_msg = get_cached_questions_for_exam(client, exam_name)
    if error_msg:
        app.logger.error(f"Start exam '{exam_name}': Error loading questions: {error_msg}")
        return redirect(url_for('main_page', error=f"Error loading '{exam_name}': {error_msg}"))
    if not questions_original_order:
        app.logger.warning(f"Start exam '{exam_name}': No questions found.")
        return redirect(url_for('main_page', error=f"No questions found for exam '{exam_name}'."))

    # --- Randomization Logic ---
    original_indices = list(range(len(questions_original_order)))
    random.shuffle(original_indices)
    app.logger.info(f"Starting exam '{exam_name}'. Original question count: {len(questions_original_order)}. Shuffled indices order: {original_indices[:5]}...") # Log first 5 shuffled

    session['exam_name'] = exam_name
    session['shuffled_question_indices'] = original_indices 
    session['current_shuffled_idx_position'] = 0 
    
    for key in [k for k in session if k.startswith('feedback_q')]: session.pop(key, None)
            
    return redirect(url_for('show_question_page'))

@app.route('/question') 
def show_question_page():
    exam_name = session.get('exam_name')
    shuffled_indices = session.get('shuffled_question_indices')
    current_shuffled_idx_position = session.get('current_shuffled_idx_position')

    if None in [exam_name, shuffled_indices, current_shuffled_idx_position]:
        app.logger.warning("Show question: Exam session not fully initialized.")
        return redirect(url_for('main_page', error="Exam session not initialized. Select an exam."))

    client = get_gspread_client()
    if not client: 
        app.logger.error("Show question: No gspread client.")
        abort(500, "Application setup error: Could not connect to data source.")

    all_questions_for_exam, error_msg = get_cached_questions_for_exam(client, exam_name)
    if error_msg or not all_questions_for_exam:
        app.logger.error(f"Show question '{exam_name}': Error/No questions from cache: {error_msg}")
        return redirect(url_for('main_page', error=f"Error retrieving questions for '{exam_name}': {error_msg or 'No questions available'}"))
    
    if not 0 <= current_shuffled_idx_position < len(shuffled_indices):
        app.logger.warning(f"Show question '{exam_name}': Invalid position {current_shuffled_idx_position}. Resetting.")
        return redirect(url_for('main_page', error="Invalid question position. Select exam again."))

    actual_question_index_in_original_list = shuffled_indices[current_shuffled_idx_position]
    
    if not 0 <= actual_question_index_in_original_list < len(all_questions_for_exam):
        app.logger.error(f"Show question '{exam_name}': Shuffled index points to invalid original index {actual_question_index_in_original_list}.")
        return redirect(url_for('main_page', error="Internal error with question order. Select exam again."))

    current_question = all_questions_for_exam[actual_question_index_in_original_list]
    feedback_info = session.get(f'feedback_q{current_question["id"]}') 

    return render_template('flashcard.html',
                           title=f"{exam_name} - Q{current_shuffled_idx_position + 1}",
                           exam_name=exam_name,
                           question_index=current_shuffled_idx_position, # For display and form
                           total_questions=len(shuffled_indices),
                           current_question=current_question,
                           feedback=feedback_info)

@app.route('/answer', methods=['POST'])
def submit_answer_page():
    exam_name = session.get('exam_name')
    shuffled_indices = session.get('shuffled_question_indices')
    current_shuffled_idx_position = session.get('current_shuffled_idx_position')

    if None in [exam_name, shuffled_indices, current_shuffled_idx_position]:
        app.logger.warning("Submit answer: Exam session not fully initialized.")
        return redirect(url_for('main_page', error="Exam session not initialized."))

    client = get_gspread_client()
    if not client: 
        app.logger.error("Submit answer: No gspread client.")
        abort(500, "Application setup error.")

    all_questions_for_exam, error_msg = get_cached_questions_for_exam(client, exam_name)
    if error_msg or not all_questions_for_exam:
        app.logger.error(f"Submit answer '{exam_name}': Error/No questions from cache: {error_msg}")
        return redirect(url_for('main_page', error=f"Error retrieving questions for '{exam_name}' to process answer."))
    
    if not 0 <= current_shuffled_idx_position < len(shuffled_indices):
        app.logger.warning(f"Submit answer '{exam_name}': Invalid position {current_shuffled_idx_position}.")
        return redirect(url_for('main_page', error="Invalid question position during answer submission."))

    actual_question_index_in_original_list = shuffled_indices[current_shuffled_idx_position]
    if not 0 <= actual_question_index_in_original_list < len(all_questions_for_exam):
        app.logger.error(f"Submit answer '{exam_name}': Shuffled index points to invalid original index {actual_question_index_in_original_list}.")
        return redirect(url_for('main_page', error="Internal error with question order. Select exam again."))
        
    current_question = all_questions_for_exam[actual_question_index_in_original_list]
    user_answer_key = request.form.get('answer')

    if not user_answer_key:
        app.logger.warning(f"Submit answer '{exam_name}', Q_id {current_question['id']}: No answer submitted.")
        return redirect(url_for('show_question_page')) # Stay on current question

    user_answer_text = current_question['options'].get(user_answer_key, "N/A")
    is_correct = (user_answer_key == current_question['correct_option_key'])
    feedback = {"user_answer": user_answer_key, "user_answer_text": user_answer_text, "is_correct": is_correct}
    session[f'feedback_q{current_question["id"]}'] = feedback 
    app.logger.info(f"Answer for '{exam_name}', Q_id {current_question['id']} (shuffled_pos {current_shuffled_idx_position}): User '{user_answer_key}', Correct: {is_correct}")
    
    return redirect(url_for('show_question_page'))

@app.route('/next_question')
def next_question_page():
    exam_name = session.get('exam_name')
    shuffled_indices = session.get('shuffled_question_indices')
    current_shuffled_idx_position = session.get('current_shuffled_idx_position')

    if None in [exam_name, shuffled_indices, current_shuffled_idx_position]:
        app.logger.warning("Next question: Exam session not fully initialized.")
        return redirect(url_for('main_page', error="Exam session not initialized."))

    # Clear feedback for the current question before moving
    client = get_gspread_client()
    if client: # Only attempt if client is available
        all_questions_for_exam, _ = get_cached_questions_for_exam(client, exam_name)
        if all_questions_for_exam and 0 <= current_shuffled_idx_position < len(shuffled_indices):
            actual_old_idx = shuffled_indices[current_shuffled_idx_position]
            if 0 <= actual_old_idx < len(all_questions_for_exam):
                question_id_to_clear = all_questions_for_exam[actual_old_idx]['id']
                session.pop(f'feedback_q{question_id_to_clear}', None)
    else:
        app.logger.warning("Next question: No gspread client, cannot fetch questions to clear feedback precisely by ID.")


    if current_shuffled_idx_position + 1 < len(shuffled_indices):
        session['current_shuffled_idx_position'] = current_shuffled_idx_position + 1
        app.logger.info(f"Next Q for '{exam_name}', new shuffled_pos: {session['current_shuffled_idx_position']}")
    else:
        app.logger.info(f"Completed all questions for '{exam_name}'.")
        session.pop('exam_name', None)
        session.pop('shuffled_question_indices', None)
        session.pop('current_shuffled_idx_position', None)
        # Keep feedback for the last question, or clear all feedback_q*
        return redirect(url_for('main_page', message=f"You've completed all questions for {exam_name}! Choose another exam."))

    return redirect(url_for('show_question_page'))

if __name__ == '__main__':
    is_debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
    app.logger.info(f"Application starting in {'DEBUG' if is_debug_mode else 'PRODUCTION'} mode.")
    app.run(debug=is_debug_mode, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))