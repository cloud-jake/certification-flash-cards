#!/bin/bash
#
# Script to run commands from README.md

# Create a Python virtual environment:
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

PROJECT_NUMBER=`gcloud projects describe $GOOGLE_CLOUD_PROJECT --format="value(projectNumber)"`

# Install dependencies:
pip install -r requirements.txt

# Set up Google Cloud Authentication:
#####################################
## Create a Google Cloud Project.

##Enable the Google Sheets API.
gcloud services enable sheets.googleapis.com cloudbuild.googleapis.com

gcloud projects add-iam-policy-binding $GOOGLE_CLOUD_PROJECT \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/storage.objectViewer"

gcloud projects add-iam-policy-binding $GOOGLE_CLOUD_PROJECT \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/logging.logWriter"

 gcloud projects add-iam-policy-binding $GOOGLE_CLOUD_PROJECT \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/artifactregistry.writer"

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

