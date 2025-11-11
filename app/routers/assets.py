from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from typing import Dict
from ..models import AssetDef
from ..services.projects import get_project, update_project, log_project
from ..services.tasks import task_gen_final
from ..dependencies import get_current_user

router = APIRouter()

def ensure_owner(proj, uid):
    if not proj: raise HTTPException(404, "Project not found")
    if proj.get("owner_id") != uid:
        raise HTTPException(403, "Not authorized to access this project")
    return proj

@router.post("/projects/{pid}/assets/{aid}/update")
async def update_asset_endpoint(pid: str, aid: str, asset_data: Dict, uid: str = Depends(get_current_user)):
    proj = get_project(pid)
    ensure_owner(proj, uid)
    
    updates = {}
    if proj.get("report"):
        report = proj["report"]
        for i, a in enumerate(report.get("assets", [])):
            if a.get("id") == aid:
                report["assets"][i] = asset_data
                updates["report"] = report
                break
                
    if aid in proj.get("asset_map", {}):
        asset_map = proj["asset_map"]
        asset_map[aid] = asset_data
        updates["asset_map"] = asset_map
        
    if updates:
        update_project(pid, **updates)
        
    return {"status": "updated"}

@router.post("/projects/{pid}/assets/{aid}/regenerate")
async def regenerate_asset_endpoint(pid: str, aid: str, background_tasks: BackgroundTasks, uid: str = Depends(get_current_user)):
    proj = get_project(pid)
    ensure_owner(proj, uid)
    
    asset_def = None
    if proj.get("report"):
        asset_def = next((a for a in proj["report"].get("assets", []) if a.get("id") == aid), None)
    if not asset_def: raise HTTPException(404, "Asset not found in report")
    
    def _regen_task(pid, asset_data, style, neg):
        try:
            log_project(pid, f"Regenerating asset {asset_data.get('id')}...")
            new_asset = task_gen_final(pid, asset_data, style, neg)
            
            curr_proj = get_project(pid)
            if curr_proj:
                asset_map = curr_proj.get("asset_map", {})
                asset_map[new_asset['id']] = new_asset
                update_project(pid, asset_map=asset_map)
                
            log_project(pid, f"Asset {new_asset['id']} regenerated.")
        except Exception as e:
            log_project(pid, f"Asset regen failed: {e}")

    background_tasks.add_task(_regen_task, pid, asset_def, proj["report"].get("visual_style"), proj["report"].get("negative_prompt"))
    return {"status": "started"}