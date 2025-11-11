#!/bin/bash
set -e

# 1. Configuration
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
SA_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
REGION="us-central1"
SERVICE_NAME="video-engine-backend"

FIREBASE_BUCKET=${FIREBASE_STORAGE_BUCKET:-"nemri-genai-bb.firebasestorage.app"}

echo "==================================================="
echo "Deploying to Project: $PROJECT_ID"
echo "Service Account:      $SA_EMAIL"
echo "Firebase Bucket:      $FIREBASE_BUCKET"
echo "==================================================="

# 2. Grant Permissions (Idempotent)
echo "Step 1: Granting IAM roles to Cloud Run Service Account..."
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/aiplatform.admin" --condition=None > /dev/null

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/serviceusage.serviceUsageConsumer" --condition=None > /dev/null

echo "Permissions granted."

# 3. Build Container
echo "Step 2: Building Container..."
gcloud builds submit --tag gcr.io/$PROJECT_ID/$SERVICE_NAME

# 4. Deploy
echo "Step 3: Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
   --image gcr.io/$PROJECT_ID/$SERVICE_NAME \
   --platform managed \
   --region $REGION \
   --allow-unauthenticated \
   --memory 32Gi \
   --cpu 8 \
   --timeout 3600 \
   --set-env-vars GOOGLE_CLOUD_PROJECT=$PROJECT_ID \
   --set-env-vars GOOGLE_CLOUD_LOCATION=global \
   --set-env-vars FIREBASE_STORAGE_BUCKET=$FIREBASE_BUCKET \
   --set-env-vars OUTPUT_DIR=/tmp

echo "Deployment Complete!"
