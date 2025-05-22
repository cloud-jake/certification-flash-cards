#!/bin/bash
#
# Script to run commands from README.md

# Create a Python virtual environment:
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies:
pip install -r requirements.txt

# Set up Google Cloud Authentication:
#####################################
## Create a Google Cloud Project.

##Enable the Google Sheets API.
gcloud services enable sheets.googleapis.com

##Create a Service Account.
gcloud iam service-accounts create flashcard-sa \
    --description="Service account for the Flashcard application" \
    --display-name="Flashcard SA"

##Download the JSON key file for this service account.
### 
SA="flashcard-sa@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
gcloud iam service-accounts keys create sa-key.json \
    --iam-account=$SA

##Share your Google Sheet with the service account's email address (give it "Viewer" or "Commenter" permission is enough for reading).
echo "hare your Google Sheet with the service account's email address (give it "Viewer" or "Commenter" permission is enough for reading)."
echo $SA

