import os
import shutil
from typing import List
from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Form, Depends
from ..services.projects import create_project, get_project, list_projects, update_project, delete_project
from ..services.tasks import step_ingest, step_detective, step_planning, step_assets, step_anchors, step_production, step_assembly
from ..services.storage import upload_bytes_to_gcs, get_gcs_path
from ..dependencies import get_current_user

router = APIRouter()

# Helper to ensure ownership
def ensure_owner(proj, uid):
    if not proj: raise HTTPException(404, "Project not found")
    if proj.get("owner_id") != uid:
        raise HTTPException(403, "Not authorized to access this project")
    return proj

@router.get("/projects")
async def list_projects_endpoint(uid: str = Depends(get_current_user)):
    return [{"id": p["id"], "name": p["name"], "status": p["status"], "created_at": p["created_at"]} for p in list_projects(uid)]

@router.post("/projects")
async def create_project_endpoint(name: str = Form(...), prompt: str = Form(...), uid: str = Depends(get_current_user)):
    return {"id": create_project(uid, name, prompt)}

@router.get("/projects/{pid}")
async def get_project_endpoint(pid: str, uid: str = Depends(get_current_user)):
    proj = get_project(pid)
    return ensure_owner(proj, uid)

@router.delete("/projects/{pid}")
async def delete_project_endpoint(pid: str, uid: str = Depends(get_current_user)):
    proj = get_project(pid)
    ensure_owner(proj, uid)
    delete_project(pid)
    return {"status": "deleted"}

@router.post("/projects/{pid}/upload")
async def upload_files_endpoint(
    pid: str, 
    files: List[UploadFile] = File(...), 
    description: str = Form(None),
    uid: str = Depends(get_current_user)
):
    proj = get_project(pid)
    ensure_owner(proj, uid)
    
    saved_items = []
    for file in files:
        if file.filename:
            gcs_path = get_gcs_path(pid, "uploads", file.filename)
            content = await file.read()
            upload_bytes_to_gcs(content, gcs_path, content_type=file.content_type)
            saved_items.append({
                "path": gcs_path,
                "description": description or ""
            })
    
    current_paths = proj.get("file_paths", [])
    current_paths.extend(saved_items)
    update_project(pid, file_paths=current_paths)
    
    return {"status": "uploaded", "count": len(saved_items)}

@router.post("/projects/{pid}/step/{step_name}")
async def run_step_endpoint(pid: str, step_name: str, background_tasks: BackgroundTasks, uid: str = Depends(get_current_user)):
    proj = get_project(pid)
    ensure_owner(proj, uid)
    
    steps = {"ingest": step_ingest, "detective": step_detective, "planning": step_planning, "assets": step_assets, "anchors": step_anchors, "production": step_production, "assembly": step_assembly}
    if step_name not in steps: raise HTTPException(400, "Invalid step")
    background_tasks.add_task(steps[step_name], pid)
    return {"status": "started", "step": step_name}