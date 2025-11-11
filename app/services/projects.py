import time
import uuid
import logging
from typing import Dict, Any, List, Optional
from google.cloud import firestore as google_firestore
from google.api_core.exceptions import NotFound
from ..firebase import db
from .storage import delete_gcs_folder

# === FIRESTORE IMPLEMENTATION ===

def get_project_ref(pid: str):
    if not db: raise RuntimeError("Firestore not initialized")
    return db.collection('projects').document(pid)

def create_project(uid: str, name: str, prompt: str) -> str:
    pid = str(uuid.uuid4())
    project_data = {
        "id": pid,
        "owner_id": uid,
        "name": name,
        "created_at": time.time(),
        "status": "idle",
        "logs": [],
        "prompt": prompt,
        "file_paths": [],
        "potential_assets": [],
        "report": None,
        "manifest": None,
        "asset_map": {},
        "anchor_map": {},
        "video_map": {},
        "audio_map": {},
        "current_step": None,
        "progress": 0
    }
    if db:
        get_project_ref(pid).set(project_data)
    else:
        logging.info(f"MOCK DB: Created project {pid}")
    return pid

def get_project(pid: str) -> Optional[Dict[str, Any]]:
    if not db: return None
    doc = get_project_ref(pid).get()
    if doc.exists:
        return doc.to_dict()
    return None

def list_projects(uid: str) -> List[Dict[str, Any]]:
    if not db: return []
    docs = db.collection('projects').where('owner_id', '==', uid).stream()
    return [doc.to_dict() for doc in docs]

def update_project(pid: str, **kwargs):
    if not db: return
    try:
        get_project_ref(pid).update(kwargs)
    except NotFound:
        logging.warning(f"Tried to update deleted project {pid}")

def delete_project(pid: str):
    if not db: return
    get_project_ref(pid).delete()
    delete_gcs_folder(f"projects/{pid}/")

def log_project(pid: str, msg: str):
    logging.info(f"[{pid}] {msg}")
    if not db: return
    try:
        get_project_ref(pid).update({
            "logs": google_firestore.ArrayUnion([f"[{time.strftime('%H:%M:%S')}] {msg}"])
        })
    except NotFound:
        pass # Project deleted, ignore log

# Deprecated
def save_project(pid: str):
    pass 

project_lock = None