#!/bin/bash
#
# source .env
SA="flashcard-sa@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"
#
echo "Your service account is $SA"
gcloud alpha run deploy gcp-study-app \
    --source . \
    --platform managed \
    --region us-central1 \
    --service-account $SA \
    --allow-unauthenticated \
    --set-env-vars GOOGLE_SHEET_ID="${GOOGLE_SHEET_ID}" \
    --set-env-vars FLASK_SECRET_KEY="generate_a_strong_random_secret_for_production" \
    --set-env-vars SUPPORT_NAME="${SUPPORT_NAME}" \
    --set-env-vars SUPPORT_EMAIL="${SUPPORT_EMAIL}"