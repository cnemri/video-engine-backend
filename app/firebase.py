import os
import logging
import firebase_admin
from firebase_admin import credentials, firestore, auth, storage

# Try to initialize Firebase.
try:
    if not firebase_admin._apps:
        # Explicitly check for the env var we set in Dockerfile to force using that key
        key_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        if key_path and os.path.exists(key_path):
            logging.info(f"Loading Firebase creds from {key_path}")
            cred = credentials.Certificate(key_path)
        else:
            logging.info("Loading Firebase creds from Application Default")
            cred = credentials.ApplicationDefault()

        firebase_admin.initialize_app(cred, {
            'storageBucket': os.getenv('FIREBASE_STORAGE_BUCKET')
        })
        
    db = firestore.client()
    bucket = storage.bucket()
    logging.info("Firebase initialized successfully.")
except Exception as e:
    logging.error(f"Firebase initialization failed: {e}", exc_info=True)
    db = None
    bucket = None
