from typing import Dict, Any, Literal
from fastapi import APIRouter, HTTPException, BackgroundTasks, Body, Depends
from ..models import VeoI2VSegment, VeoInterpSegment, VeoMode, DetectiveReport, AssetDef, ArchitectManifest
from ..services.projects import get_project, update_project, log_project
from ..services.tasks import task_gen_anchor, task_run_veo, task_render_audio
from ..dependencies import get_current_user

router = APIRouter()

def ensure_owner(proj, uid):
    if not proj: raise HTTPException(404, "Project not found")
    if proj.get("owner_id") != uid:
        raise HTTPException(403, "Not authorized to access this project")
    return proj

@router.post("/projects/{pid}/segments/{sid}/update")
async def update_segment_endpoint(pid: str, sid: str, seg_data: Dict[str, Any] = Body(...), uid: str = Depends(get_current_user)):
    proj = get_project(pid)
    ensure_owner(proj, uid)
    
    if proj.get("manifest"):
        manifest = proj["manifest"]
        for i, s in enumerate(manifest["timeline"]):
            if s["id"] == sid:
                manifest["timeline"][i].update(seg_data)
                update_project(pid, manifest=manifest)
                break
    return {"status": "updated"}

@router.post("/projects/{pid}/segments/{sid}/anchor/{type}/regenerate")
async def regenerate_anchor_endpoint(pid: str, sid: str, type: Literal["start", "end"], background_tasks: BackgroundTasks, uid: str = Depends(get_current_user)):
    proj = get_project(pid)
    ensure_owner(proj, uid)
    
    seg_data = next((s for s in proj["manifest"]["timeline"] if s["id"] == sid), None)
    if not seg_data: raise HTTPException(404, "Segment not found")
    
    def _regen_task(pid, sid, seg_data, type):
        try:
            seg = VeoI2VSegment(**seg_data) if seg_data['mode'] == VeoMode.I2V else VeoInterpSegment(**seg_data)
            curr_proj = get_project(pid)
            report = DetectiveReport(**curr_proj["report"])
            asset_map = {k: AssetDef(**v) for k,v in curr_proj["asset_map"].items()}
            start_anchor = curr_proj["anchor_map"].get(f"{sid}_start") if type == 'end' else None
            
            new_path = task_gen_anchor(pid, sid, seg, asset_map, report.visual_style, report.negative_prompt, is_end=(type=='end'), start_anchor_path=start_anchor)
            
            # Read-modify-write anchor_map
            curr_proj = get_project(pid)
            anchor_map = curr_proj.get("anchor_map", {})
            anchor_map[f"{sid}_{type}"] = new_path
            update_project(pid, anchor_map=anchor_map)
            
        except Exception as e:
            log_project(pid, f"Anchor regen failed: {e}")

    background_tasks.add_task(_regen_task, pid, sid, seg_data, type)
    return {"status": "started"}

@router.post("/projects/{pid}/segments/{sid}/video/regenerate")
async def regenerate_video_endpoint(pid: str, sid: str, background_tasks: BackgroundTasks, uid: str = Depends(get_current_user)):
    proj = get_project(pid)
    ensure_owner(proj, uid)
    
    seg_data = next((s for s in proj["manifest"]["timeline"] if s["id"] == sid), None)
    if not seg_data: raise HTTPException(404, "Segment not found")
    
    def _regen_task(pid, sid, seg_data):
        try:
            seg = VeoI2VSegment(**seg_data) if seg_data['mode'] == VeoMode.I2V else VeoInterpSegment(**seg_data)
            curr_proj = get_project(pid)
            report = DetectiveReport(**curr_proj["report"])
            anchor_start = curr_proj["anchor_map"].get(f"{sid}_start")
            anchor_end = curr_proj["anchor_map"].get(f"{sid}_end")
            if not anchor_start: raise Exception("Start anchor missing")
            
            new_path = task_run_veo(pid, sid, seg, anchor_start, report.visual_style, anchor_end)
            
            curr_proj = get_project(pid)
            video_map = curr_proj.get("video_map", {})
            video_map[sid] = new_path
            update_project(pid, video_map=video_map)
            
        except Exception as e:
            log_project(pid, f"Video regen failed: {e}")

    background_tasks.add_task(_regen_task, pid, sid, seg_data)
    return {"status": "started"}

@router.post("/projects/{pid}/segments/{sid}/tts/regenerate")
async def regenerate_tts_endpoint(pid: str, sid: str, background_tasks: BackgroundTasks, uid: str = Depends(get_current_user)):
    proj = get_project(pid)
    ensure_owner(proj, uid)
    
    seg_data = next((s for s in proj["manifest"]["timeline"] if s["id"] == sid), None)
    if not seg_data: raise HTTPException(404, "Segment not found")
    
    def _regen_task(pid, sid, seg_data):
        try:
            seg = VeoI2VSegment(**seg_data) if seg_data['mode'] == VeoMode.I2V else VeoInterpSegment(**seg_data)
            curr_proj = get_project(pid)
            manifest = ArchitectManifest(**curr_proj["manifest"])
            new_path = task_render_audio(sid, seg, manifest.narrator_voice_style, manifest.language)
            if new_path:
                curr_proj = get_project(pid)
                audio_map = curr_proj.get("audio_map", {})
                audio_map[sid] = new_path
                update_project(pid, audio_map=audio_map)
        except Exception as e:
            log_project(pid, f"TTS regen failed: {e}")

    background_tasks.add_task(_regen_task, pid, sid, seg_data)
    return {"status": "started"}