# AI Video Engine Backend

This is the backend service for the AI Video Engine, powered by FastAPI, Google Gemini, Imagen, and Veo.

## Prerequisites

*   **Python 3.12+**
*   **FFmpeg** (Required for video assembly)
    *   macOS: `brew install ffmpeg`
    *   Linux: `sudo apt install ffmpeg`
*   **Google Cloud Project** with the following APIs enabled:
    *   Vertex AI API
    *   Cloud Text-to-Speech API
    *   Cloud Build API
    *   Cloud Run API
    *   Artifact Registry API (or Container Registry)

## Installation

1.  Navigate to the backend directory:
    ```bash
    cd backend
    ```

2.  Install dependencies (using `uv` or `pip`):
    ```bash
    # If using uv (recommended)
    uv sync

    # If using standard pip
    pip install -r requirements.txt
    ```

## Configuration

1.  Create a `.env` file from the example:
    ```bash
    cp .env.example .env
    ```

2.  Edit `.env` and set your Google Cloud Project ID:
    ```ini
    GOOGLE_CLOUD_PROJECT=your-project-id
    GOOGLE_CLOUD_LOCATION=us-central1
    ```

## Authentication

You need to authenticate with Google Cloud to use the AI models.

### Option A: Local Development (Recommended)

This is the easiest and most secure way for local development.

1.  Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install).
2.  Login with your Google account:
    ```bash
    gcloud auth application-default login
    ```
3.  Set your project:
    ```bash
    gcloud config set project your-project-id
    ```

The application will automatically pick up these credentials.

### Option B: Cloud Run (Production)

When deployed to Google Cloud Run, the application automatically uses the **Application Default Credentials (ADC)** of the service account attached to the Cloud Run service.

**Requirements:**
Ensure the Cloud Run service account (usually the default Compute Engine service account) has the following IAM roles:
*   **Vertex AI User** (`roles/aiplatform.user`)
*   **Service Usage Consumer** (`roles/serviceusage.serviceUsageConsumer`)

## Running the Server

```bash
# Using fastapi CLI
fastapi dev app/main.py

# OR using uvicorn directly
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.

## Deployment (Google Cloud Run)

To deploy the backend to Google Cloud Run, you can use the provided `deploy.sh` script. This script automatically grants the necessary IAM permissions and deploys the container.

1.  **Run the deployment script:**
    ```bash
    ./deploy.sh
    ```

    ```bash
    export FIREBASE_STORAGE_BUCKET=your-bucket-name.firebasestorage.app
    ./deploy.sh
    ```

Alternatively, you can run the commands manually:

1.  **Grant Permissions:**
    (See `deploy.sh` for the exact `gcloud projects add-iam-policy-binding` commands)

2.  **Build the Container:**
    ```bash
    gcloud builds submit --tag gcr.io/$(gcloud config get-value project)/video-engine-backend
    ```

3.  **Deploy to Cloud Run:**
    ```bash
    gcloud run deploy video-engine-backend \
       --image gcr.io/$(gcloud config get-value project)/video-engine-backend \
       --platform managed \
       --region us-central1 \
       --allow-unauthenticated \
       --memory 32Gi \
       --cpu 8 \
       --timeout 3600 \
       --set-env-vars GOOGLE_CLOUD_PROJECT=$(gcloud config get-value project) \
       --set-env-vars GOOGLE_CLOUD_LOCATION=global \
       --set-env-vars FIREBASE_STORAGE_BUCKET=YOUR_FIREBASE_BUCKET \
       --set-env-vars OUTPUT_DIR=/tmp
    ```