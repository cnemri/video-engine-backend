import os
import uuid
import time
import random
import logging
from ..firebase import bucket

def get_gcs_path(pid: str, folder: str, filename: str) -> str:
    """Defines the standard structure for files in GCS."""
    return f"projects/{pid}/{folder}/{filename}"

def upload_to_gcs(local_path: str, destination_path: str) -> str:
    """
    Uploads a local file to GCS and returns the GCS blob path.
    """
    if not bucket:
        logging.warning(f"GCS not configured. Keeping file local at {local_path}")
        return local_path

    blob = bucket.blob(destination_path)
    blob.upload_from_filename(local_path)
    
    # Clean up local file after upload if running in Cloud Run to save disk space
    if os.environ.get("K_SERVICE") and os.path.exists(local_path):
        os.remove(local_path)
    
    logging.info(f"Uploaded {local_path} to gs://{bucket.name}/{destination_path}")
    return destination_path

def upload_bytes_to_gcs(data: bytes, destination_path: str, content_type: str = None) -> str:
    """
    Uploads bytes directly to GCS.
    """
    if not bucket: raise RuntimeError("GCS not configured")
    blob = bucket.blob(destination_path)
    blob.upload_from_string(data, content_type=content_type)
    return destination_path

def download_from_gcs(gcs_path: str, local_path: str) -> str:
    """
    Downloads a file from GCS to a local path.
    """
    if not bucket: return gcs_path # Assume it's already local if no bucket
    
    # If it's already a local path, just return it
    if os.path.exists(gcs_path): return gcs_path

    blob = bucket.blob(gcs_path)
    
    for attempt in range(3):
        try:
            blob.download_to_filename(local_path)
            return local_path
        except Exception as e:
            if attempt == 2:
                logging.error(f"Failed to download {gcs_path} after 3 attempts: {e}")
                raise
            # Exponential backoff with jitter: 1s, 2s, 4s + random
            sleep_time = (2 ** attempt) + random.random()
            logging.warning(f"Download failed for {gcs_path} (attempt {attempt+1}/3). Retrying in {sleep_time:.2f}s... Error: {e}")
            time.sleep(sleep_time)
            
    return local_path

def generate_signed_url(gcs_path: str, expiration_mins=60) -> str:
    """
    Generates a read-only signed URL for a GCS object.
    """
    if not bucket: return ""
    blob = bucket.blob(gcs_path)
    return blob.generate_signed_url(expiration=expiration_mins * 60)

def delete_gcs_folder(prefix: str):
    """
    Deletes all objects in GCS matching a prefix (effectively a folder).
    """
    if not bucket: return
    blobs = list(bucket.list_blobs(prefix=prefix))
    if blobs:
        bucket.delete_blobs(blobs)
        logging.info(f"Deleted {len(blobs)} files from GCS folder: {prefix}")