import os
import logging
from dotenv import load_dotenv
from google import genai
from google.cloud import texttospeech_v1beta1 as texttospeech
from google.api_core import client_options

load_dotenv()

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.getenv("OUTPUT_DIR", os.path.join(BASE_DIR, "output"))
PROJECTS_DIR = os.path.join(OUTPUT_DIR, "projects")
STAGING_DIR = os.path.join(OUTPUT_DIR, "assets")
ANCHOR_DIR = os.path.join(OUTPUT_DIR, "anchors")
TEMP_DIR = os.path.join(OUTPUT_DIR, "temp")

for d in [OUTPUT_DIR, PROJECTS_DIR, STAGING_DIR, ANCHOR_DIR, TEMP_DIR]:
    os.makedirs(d, exist_ok=True)

WORKER_THREADS_ASSETS = 8
WORKER_THREADS_VEO = 4

MODEL_THINKING = "gemini-2.5-flash-preview-09-2025"
MODEL_IMAGEN = "gemini-2.5-flash-image"
MODEL_VEO = "veo-3.1-generate-preview"
MODEL_TTS = "gemini-2.5-pro-tts"
VOICE_NAME = "Algieba"

try:
    genai_client = genai.Client(project=PROJECT_ID, location=LOCATION, vertexai=True)
    tts_client = texttospeech.TextToSpeechClient(
        client_options=client_options.ClientOptions(quota_project_id=PROJECT_ID)
    )
except Exception as e:
    logging.warning(f"Failed to initialize Google Cloud clients: {e}")
    genai_client = None
    tts_client = None
