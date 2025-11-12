import os
import json
import time
import functools
import re
import uuid
import subprocess
import hashlib
import logging
from typing import List, Dict, Literal, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image
from google.genai import types
from google.cloud import texttospeech_v1beta1 as texttospeech

from ..config import (
    genai_client, tts_client, MODEL_THINKING, MODEL_IMAGEN, MODEL_VEO, MODEL_TTS,
    VOICE_NAME, WORKER_THREADS_ASSETS, WORKER_THREADS_VEO,
    STAGING_DIR, ANCHOR_DIR, TEMP_DIR, OUTPUT_DIR
)
from ..models import (
    PotentialAsset, AssetAnalysis, DetectiveReport, ArchitectManifest, CritiqueResult,
    AssetDef, AssetType, AnchorCritiqueResult, VeoSegmentBase, VeoSegmentUnion,
    VeoMode, VeoI2VSegment, VeoInterpSegment
)
from ..prompts import (
    PROMPT_ANALYZER, PROMPT_DETECTIVE, PROMPT_ARCHITECT, PROMPT_CRITIC,
    PROMPT_ASSET_GEN, PROMPT_ANCHOR_CRITIC, PROMPT_VEO_OPTIMIZER
)
from .projects import update_project, log_project, get_project
from .storage import upload_to_gcs, download_from_gcs, get_gcs_path, upload_bytes_to_gcs

# === HELPER FUNCTIONS ===
def retry_backoff(retries=2, delay=5):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cd = delay
            for i in range(retries + 1):
                try: return func(*args, **kwargs)
                except Exception as e:
                    if i == retries: raise
                    logging.warning(f"Retry {i}/{retries} ({func.__name__}): {e}")
                    time.sleep(cd); cd *= 2
        return wrapper
    return decorator

def get_media_duration(fpath):
    try: return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", fpath]).decode().strip())
    except: return 0.0

def has_audio_stream(fpath):
    try:
        out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", fpath])
        return len(out.strip()) > 0
    except:
        return False

def parse_json_response(res, model_cls):
    try:
        return json.loads(res.text)
    except Exception as e:
        logging.error(f"JSON Parse Error for {model_cls.__name__}: {res.text}")
        raise ValueError(f"Failed to parse {model_cls.__name__}: {e}")

def ensure_local(path: str) -> str:
    """Ensures a file is available locally, downloading from GCS if needed."""
    if not path: return path
    if os.path.exists(path): return path
    # Assume it's a GCS path if it doesn't exist locally.
    # Use hash of full path + UUID to avoid collisions between parallel tasks using same asset.
    name_hash = hashlib.md5(path.encode("utf-8")).hexdigest()
    ext = os.path.splitext(path)[1]
    local_path = os.path.join(TEMP_DIR, f"{name_hash}_{uuid.uuid4().hex[:6]}{ext}")
    return download_from_gcs(path, local_path)

def generate_with_thinking(pid: str, model: str, contents: List[Any], config: types.GenerateContentConfig, response_model: Any = None) -> Any:
    full_json_text = ""
    current_thought_buffer = ""
    last_update_time = 0 # Force immediate first update
    
    # Ensure we request JSON mime type if a schema is provided
    if response_model:
        config.response_mime_type = "application/json"
        config.response_schema = response_model

    try:
        # Use streaming
        for chunk in genai_client.models.generate_content_stream(model=model, contents=contents, config=config):
            if not chunk.candidates: continue
            
            for part in chunk.candidates[0].content.parts:
                # Check for thought
                is_thought = getattr(part, 'thought', False)
                
                if is_thought:
                    current_thought_buffer += part.text
                    # Update Firestore every 0.5 seconds
                    if pid and time.time() - last_update_time > 0.5:
                        # Strategy: Always show from the LAST bold title onwards.
                        # This ensures "Title + Details" are kept together.
                        bold_matches = list(re.finditer(r'\*\*.*?\*\*', current_thought_buffer, re.DOTALL))
                        if bold_matches:
                            last_match = bold_matches[-1]
                            display_thought = current_thought_buffer[last_match.start():]
                        else:
                            # Fallback: Last paragraph(s)
                            blocks = current_thought_buffer.split('\n\n')
                            display_thought = blocks[-1]
                            if len(display_thought) < 50 and len(blocks) > 1:
                                display_thought = blocks[-2] + "\n\n" + display_thought
                        
                        update_project(pid, current_thought=display_thought)
                        last_update_time = time.time()
                else:
                    full_json_text += part.text
        
        # Final update to clear thought
        if pid:
            update_project(pid, current_thought=None)
        
        if response_model:
             return json.loads(full_json_text)
        return full_json_text
        
    except Exception as e:
        logging.error(f"Generation failed: {e}")
        raise

# === CORE TASKS ===
@retry_backoff()
def task_analyze_image(pid: str, fpath: str, user_prompt: str, asset_description: str = None) -> List[Dict]:
    local_fpath = ensure_local(fpath)
    with open(local_fpath, "rb") as f: img_data = f.read()
    if not img_data: return []
    
    final_prompt = user_prompt
    if asset_description:
        final_prompt = f"{user_prompt}\n\nCONTEXT: The user has provided this image with the description: '{asset_description}'. Use this to correctly identify the subject."

    analysis = generate_with_thinking(
        pid=pid,
        model=MODEL_THINKING,
        contents=[PROMPT_ANALYZER.format(user_prompt=final_prompt, source_file=os.path.basename(fpath)), types.Part.from_bytes(data=img_data, mime_type="image/png")],
        config=types.GenerateContentConfig(
            temperature=0.1,
            thinking_config={"thinking_budget": -1, "include_thoughts": True} 
        ),
        response_model=AssetAnalysis
    )
    
    items = analysis.get("items", [])
    for item in items: item["source_file"] = fpath # Keep original (GCS) path in model
    if local_fpath != fpath and os.path.exists(local_fpath): os.remove(local_fpath) # Clean up temp
    return items

@retry_backoff()
def task_detective(pid: str, prompt: str, potential_assets: List[Dict]) -> Dict:
    pot_json = json.dumps(potential_assets, indent=2) if potential_assets else "[]"
    parts = [PROMPT_DETECTIVE.format(potential_assets_json=pot_json, user_prompt=prompt)]
    seen_files = set()
    if potential_assets:
        for p in potential_assets:
            src = p.get("source_file")
            if src and src not in seen_files:
                 try:
                     local_path = ensure_local(src)
                     with open(local_path, "rb") as f:
                         d = f.read()
                         if d: parts.append(types.Part.from_bytes(data=d, mime_type="image/png"))
                         seen_files.add(src)
                     if local_path != src and os.path.exists(local_path): os.remove(local_path)
                 except Exception as e:
                     log_project(pid, f"Warning: Failed to load potential asset {src}: {e}")
    
    report = generate_with_thinking(
        pid=pid,
        model=MODEL_THINKING, 
        contents=parts,
        config=types.GenerateContentConfig(
            temperature=0.2,
            thinking_config={"thinking_budget": -1, "include_thoughts": True}
        ),
        response_model=DetectiveReport
    )
    
    # Validate and fix assets
    for asset in report.get("assets", []):
        if not asset.get("name"):
            asset["name"] = f"Asset {asset.get('id', 'Unknown')}"
        if not asset.get("visual_prompt"):
            # Fallback: Use description + visual style
            asset["visual_prompt"] = f"{asset.get('description', '')}, {report.get('visual_style', '')}"
        
        # Enforce voice_style for characters
        if asset.get("type") == "character" and not asset.get("voice_style"):
            asset["voice_style"] = "Neutral, clear, standard voice"
            
    return report

@retry_backoff()
def task_architect(pid: str, report: Dict) -> Dict:
    manifest = generate_with_thinking(
        pid=pid,
        model=MODEL_THINKING, 
        contents=[PROMPT_ARCHITECT, json.dumps(report)],
        config=types.GenerateContentConfig(
            temperature=0.7,
            thinking_config={"thinking_budget": -1, "include_thoughts": True}
        ),
        response_model=ArchitectManifest
    )
    
    # Validate timeline prompts
    for seg in manifest.get("timeline", []):
        if not seg.get("anchor_prompt"):
            seg["anchor_prompt"] = seg.get("scene_details", {}).get("main_action", "Cinematic shot")
        if not seg.get("veo_prompt"):
            seg["veo_prompt"] = seg.get("anchor_prompt")
            
    return manifest

@retry_backoff()
def task_critic(pid: str, report: Dict, manifest: Dict) -> Dict:
    critique = generate_with_thinking(
        pid=pid,
        model=MODEL_THINKING, 
        contents=[PROMPT_CRITIC, f"DETECTIVE REPORT: {json.dumps(report)}\nDIRECTOR MANIFEST: {json.dumps(manifest)}"],
        config=types.GenerateContentConfig(
            temperature=0.1,
            thinking_config={"thinking_budget": -1, "include_thoughts": True}
        ),
        response_model=CritiqueResult
    )

    if not critique.get("approved") and critique.get("improved_manifest"):
        log_project(pid, f"CRITIQUE APPLIED: {critique.get('feedback')[:100]}...")
        improved = critique["improved_manifest"]
        improved["estimated_total_duration"] = sum([int(s.get("duration", 0)) for s in improved.get("timeline", [])])
        return improved
    log_project(pid, "CRITIQUE PASSED.")
    return manifest

@retry_backoff()
def task_extract_final(pid: str, asset: Dict) -> Dict:
    local_source = ensure_local(asset.get("source_file"))
    with open(local_source, "rb") as f: img_data = f.read()
    
    atype = asset.get("type", "object")
    prompt = asset.get("extraction_prompt", "")
    
    if atype in [AssetType.CHAR, AssetType.OBJ]:
        strict_prompt = (
            f"ISOLATED on a PURE, BLANK #FFFFFF WHITE BACKGROUND. NO SHADOWS. NO GROUNDPLANE. {prompt}. "
            "Create a PRECISE reference shot based EXACTLY on the provided image. Maintain all original colors, textures, and details perfectly. Full body/object visible, NO CROPPING."
        )
    elif atype == AssetType.LOC:
        strict_prompt = (
            f"EMPTY SCENE. NO PEOPLE. {prompt}. Create a CLEAN PLATE version based EXACTLY on the provided image. "
            "Maintain original architectural style and lighting. REMOVE ALL DYNAMIC OBJECTS."
        )
    else: strict_prompt = prompt
    
    last_error = None
    for attempt in range(3):
        try:
            res = genai_client.models.generate_content(model=MODEL_IMAGEN, contents=[strict_prompt, types.Part.from_bytes(data=img_data, mime_type="image/png")], config=types.GenerateContentConfig(response_modalities=["IMAGE"]))
            if res.candidates and res.candidates[0].content.parts[0].inline_data:
                # Upload directly from bytes if possible, or save temp then upload
                gcs_path = get_gcs_path(pid, "assets", f"SUPPLIED_{atype}_{asset['id']}_{uuid.uuid4().hex[:6]}.png")
                upload_bytes_to_gcs(res.candidates[0].content.parts[0].inline_data.data, gcs_path, content_type="image/png")
                asset["local_path"] = gcs_path # Using 'local_path' field to store GCS path now
                if local_source != asset.get("source_file") and os.path.exists(local_source): os.remove(local_source)
                return asset
        except Exception as e: 
            last_error = e
            time.sleep(2)
    raise RuntimeError(f"Failed to extract asset {asset.get('id')}: {last_error}")

@retry_backoff()
def task_gen_final(pid: str, asset: Dict, style: str, neg_prompt: str) -> Dict:
    atype = asset.get("type", "object")
    aspect = "16:9" if atype == AssetType.LOC else "1:1"
    full_prompt = PROMPT_ASSET_GEN.format(asset_type=atype.upper(), visual_style=style, negative_prompt=neg_prompt, visual_prompt=asset.get("visual_prompt"))
    if atype == AssetType.LOC: full_prompt = full_prompt.replace("ISOLATED on a PURE #FFFFFF WHITE BACKGROUND. NO SHADOWS.", "EMPTY SCENE. NO PEOPLE.")
    last_error = None
    for attempt in range(3):
        try:
            res = genai_client.models.generate_content(model=MODEL_IMAGEN, contents=[full_prompt], config=types.GenerateContentConfig(response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio=aspect)))
            if res.candidates and res.candidates[0].content.parts[0].inline_data:
                gcs_path = get_gcs_path(pid, "assets", f"GEN_{atype}_{asset['id']}_{uuid.uuid4().hex[:6]}.png")
                upload_bytes_to_gcs(res.candidates[0].content.parts[0].inline_data.data, gcs_path, content_type="image/png")
                asset["local_path"] = gcs_path
                return asset
        except Exception as e: 
            last_error = e
            time.sleep(2)
    raise RuntimeError(f"Failed to generate asset {asset.get('id')}: {last_error}")

def task_finalize_assets(pid: str, manifest: Dict, report: Dict) -> Dict[str, Dict]:
    log_project(pid, f"[4/6] Asset Finalization (Style: {report.get('visual_style')})...")
    final_map = {}
    with ThreadPoolExecutor(WORKER_THREADS_ASSETS) as pool:
        futs = {}
        for asset in report.get("assets", []):
            if asset.get("is_supplied") and asset.get("source_file"):
                futs[pool.submit(task_extract_final, pid, asset)] = asset["id"]
            else:
                futs[pool.submit(task_gen_final, pid, asset, report.get("visual_style"), report.get("negative_prompt"))] = asset["id"]
        
        total_tasks = len(futs)
        completed_count = 0
        update_project(pid, progress=0) # Reset progress for this step

        # INCREMENTAL UPDATE LOOP
        for f in as_completed(futs): 
            completed_count += 1
            prog = int((completed_count / total_tasks) * 100)
            
            try: 
                res = f.result()
                final_map[res["id"]] = res
                update_project(pid, asset_map=final_map, progress=prog)
            except Exception as e: 
                log_project(pid, f"Asset failed: {e}")
                update_project(pid, progress=prog)
    return final_map

def create_collage(images: List[str]) -> str:
    if not images: return None
    imgs = []
    local_paths = []
    for i in images:
        try: 
            local_p = ensure_local(i)
            local_paths.append(local_p)
            imgs.append(Image.open(local_p).convert('RGBA'))
        except: pass
    if not imgs: return None
    w = sum(i.width for i in imgs); h = max(i.height for i in imgs)
    col = Image.new('RGB', (w, h), (255, 255, 255))
    x = 0
    for i in imgs:
        if i.mode == 'RGBA':
             bg = Image.new('RGB', i.size, (255, 255, 255))
             bg.paste(i, mask=i.split()[3])
             col.paste(bg, (x, 0))
        else: col.paste(i, (x, 0))
        x += i.width
    out = os.path.join(TEMP_DIR, f"collage_{uuid.uuid4()}.png")
    col.save(out)
    # Clean up downloaded temp files
    for p in local_paths:
        if p not in images and os.path.exists(p): os.remove(p)
    return out

@retry_backoff()
def task_critique_anchor(pid: str, image_path: str, prompt: str, style: str) -> Dict:
    # image_path here is likely local temp from task_gen_anchor loop
    with open(image_path, "rb") as f: img_data = f.read()
    
    return generate_with_thinking(
        pid=None, # Disable thought updates for this step (progress bar only)
        model=MODEL_THINKING,
        contents=[PROMPT_ANCHOR_CRITIC.format(visual_style=style, prompt=prompt), types.Part.from_bytes(data=img_data, mime_type="image/png")],
        config=types.GenerateContentConfig(
            temperature=0.2,
            thinking_config={"thinking_budget": -1, "include_thoughts": True}
        ),
        response_model=AnchorCritiqueResult
    )

@retry_backoff()
def task_gen_anchor(pid: str, seg_id: str, seg: Dict, assets: Dict[str, Dict], style: str, neg_prompt: str, is_end=False, start_anchor_path=None):
    suffix = "end" if is_end else "start"
    gcs_final_path = get_gcs_path(pid, "anchors", f"{seg_id}_{suffix}_{uuid.uuid4().hex[:6]}.png")
    
    log_project(pid, f"[ANCHOR {seg_id}] Generating {suffix.upper()}...")
    
    asset_ids = seg.get("asset_ids", [])
    loc_refs = [assets[aid]["local_path"] for aid in asset_ids if aid in assets and assets[aid]["type"] == AssetType.LOC and assets[aid].get("local_path")]
    char_obj_refs = [assets[aid]["local_path"] for aid in asset_ids if aid in assets and assets[aid]["type"] != AssetType.LOC and assets[aid].get("local_path")]
    
    collage_path = create_collage(char_obj_refs) # Returns local temp path
    
    current_prompt = seg.get("end_anchor_prompt") if is_end and seg.get("end_anchor_prompt") else seg.get("anchor_prompt")

    last_error = None
    for attempt in range(3):
        parts = [f"ROLE: Cinematic Concept Artist. TASK: Create a photorealistic film still. STYLE: {style}. NEGATIVE PROMPT: {neg_prompt}", "SCENE DESCRIPTION:", current_prompt]
        
        if is_end and start_anchor_path:
            parts.append("STARTING FRAME REFERENCE (Logical continuation required):")
            local_start = ensure_local(start_anchor_path)
            with open(local_start, "rb") as f: parts.append(types.Part.from_bytes(data=f.read(), mime_type="image/png")),
            if local_start != start_anchor_path and os.path.exists(local_start): os.remove(local_start)

        if collage_path:
            parts.append("MANDATORY ASSETS (You MUST include these exactly as shown):")
            with open(collage_path, "rb") as f: parts.append(types.Part.from_bytes(data=f.read(), mime_type="image/png")),
            
        if loc_refs:
            parts.append("ENVIRONMENT REFERENCE (Adapt perspective to match scene, but keep these landmarks):")
            local_loc = ensure_local(loc_refs[0])
            with open(local_loc, "rb") as f: parts.append(types.Part.from_bytes(data=f.read(), mime_type="image/png")),
            if local_loc != loc_refs[0] and os.path.exists(local_loc): os.remove(local_loc)

        try:
            res = genai_client.models.generate_content(model=MODEL_IMAGEN, contents=parts, config=types.GenerateContentConfig(response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio="16:9")))
            if res.candidates and res.candidates[0].content.parts[0].inline_data:
                # Use UUID to prevent collisions between parallel retries
                temp_path = os.path.join(TEMP_DIR, f"{pid}_{seg_id}_{suffix}_try{attempt}_{uuid.uuid4().hex[:6]}.png")
                with open(temp_path, "wb") as f: f.write(res.candidates[0].content.parts[0].inline_data.data)
                critique = task_critique_anchor(pid, temp_path, current_prompt, style)
                
                # FORCE ACCEPT ON 3RD ATTEMPT (attempt index 2)
                if critique.get("approved") or attempt == 2:
                    if attempt == 2 and not critique.get("approved"):
                         log_project(pid, f"WARNING: Anchor {seg_id} {suffix} forced acceptance after 3 failed critiques.")
                         
                    upload_to_gcs(temp_path, gcs_final_path)
                    if os.path.exists(temp_path): os.remove(temp_path)
                    if collage_path and os.path.exists(collage_path): os.remove(collage_path)
                    return gcs_final_path
                else:
                    current_prompt = critique.get("improved_prompt", current_prompt)
                    if os.path.exists(temp_path): os.remove(temp_path)
            else:
                last_error = f"No image data. Response: {res}"
                log_project(pid, f"Anchor gen attempt {attempt} failed for {seg_id} {suffix}: {last_error}")
        except Exception as e:
            last_error = e
            log_project(pid, f"Anchor gen attempt {attempt} exception for {seg_id} {suffix}: {e}")
            time.sleep(2)

    if collage_path and os.path.exists(collage_path): os.remove(collage_path)
    raise RuntimeError(f"Failed to generate anchor {seg_id} {suffix}: {last_error}")

@retry_backoff()
def task_optimize_veo_prompt(pid: str, seg: Dict, style: str) -> str:
    dialogue = seg.get("dialogue", [])
    audio_context = "DIALOGUE PRESENT" if dialogue else "SILENT"
    dialogue_lines = "\n".join([f"- {l['speaker_id']}: {l['text']}" for l in dialogue]) if dialogue else "None"
    
    cinematography = seg.get("cinematography", {})
    scene_details = seg.get("scene_details", {})
    
    prompt_data = PROMPT_VEO_OPTIMIZER.format(
        original_prompt=seg.get("veo_prompt"), 
        shot_type=cinematography.get("shot_type"), 
        movement=cinematography.get("movement"), 
        lighting=cinematography.get("lighting"), 
        subject_focus=scene_details.get("subject_focus"), 
        main_action=scene_details.get("main_action"), 
        visual_style=style, 
        audio_context=audio_context, 
        dialogue_lines=dialogue_lines
    )
    
    res = generate_with_thinking(
        pid=None, # Disable thought updates for this step (progress bar only)
        model=MODEL_THINKING, 
        contents=[prompt_data], 
        config=types.GenerateContentConfig(
            temperature=0.3, 
            thinking_config={"thinking_budget": -1, "include_thoughts": True}
        )
    )
    return res.strip()

@retry_backoff()
def task_run_veo(pid: str, seg_id: str, seg: Dict, anchor_path: str, style: str, end_anchor_path: str = None):
    gcs_out_path = get_gcs_path(pid, "output", f"{seg_id}_raw_{uuid.uuid4().hex[:6]}.mp4")
    optimized_prompt = task_optimize_veo_prompt(pid, seg, style)
    mode = seg.get("mode", "i2v")
    duration = int(seg.get("duration", 4))
    
    log_project(pid, f"[VEO {seg_id}] {mode.upper()} {duration}s...")
    cfg = {"duration_seconds": duration, "aspect_ratio": "16:9"}
    
    local_anchor = ensure_local(anchor_path)
    gen_args = {"model": MODEL_VEO, "prompt": optimized_prompt, "image": types.Image.from_file(location=local_anchor)}
    
    if mode == VeoMode.FI and end_anchor_path:
        local_end = ensure_local(end_anchor_path)
        cfg["last_frame"] = types.Image.from_file(location=local_end)
        
    gen_args["config"] = types.GenerateVideosConfig(**cfg)
    
    for attempt in range(3):
        try:
            op = genai_client.models.generate_videos(**gen_args)
            while not op.done: time.sleep(5); op = genai_client.operations.get(op)
            if op.result and op.result.generated_videos:
                upload_bytes_to_gcs(op.result.generated_videos[0].video.video_bytes, gcs_out_path, content_type="video/mp4")
                # Cleanup local temp anchors
                if local_anchor != anchor_path and os.path.exists(local_anchor): os.remove(local_anchor)
                if mode == VeoMode.FI and end_anchor_path and local_end != end_anchor_path and os.path.exists(local_end): os.remove(local_end)
                return gcs_out_path
        except Exception as e: time.sleep(5)
        
    # Fallback (ffmpeg loop)
    temp_out = os.path.join(TEMP_DIR, f"{pid}_{seg_id}_fallback_{uuid.uuid4().hex[:6]}.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", local_anchor, "-t", str(duration), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", temp_out], check=True)
    upload_to_gcs(temp_out, gcs_out_path)
    if os.path.exists(temp_out): os.remove(temp_out)
    if local_anchor != anchor_path and os.path.exists(local_anchor): os.remove(local_anchor)
    return gcs_out_path

@retry_backoff()
def task_render_audio(pid: str, seg_id: str, seg: Dict, narrator_style: str, lang: str):
    if lang.lower() in ["english", "en"]: lang = "en-US"
    narration = seg.get("narration")
    if not narration: return None
    gcs_path = get_gcs_path(pid, "audio", f"tts_{seg_id}_{uuid.uuid4().hex[:6]}.mp3")
    res = tts_client.synthesize_speech(input=texttospeech.SynthesisInput(text=narration, prompt=narrator_style), voice=texttospeech.VoiceSelectionParams(language_code=lang, name=VOICE_NAME, model_name=MODEL_TTS), audio_config=texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3))
    upload_bytes_to_gcs(res.audio_content, gcs_path, content_type="audio/mpeg")
    return gcs_path

def task_assemble(pid: str, manifest: Dict, v_map: Dict[str, str], a_map: Dict[str, str]):
    log_project(pid, "[ASSEMBLY] Finalizing...")
    clips = []
    temp_files = []
    total_segments = len(manifest.get("timeline", []))
    
    for i, seg in enumerate(manifest.get("timeline", [])):
        # Progress range: 0 -> 100 (Step-specific)
        prog = int(((i + 1) / total_segments) * 100)
        update_project(pid, progress=prog)
        
        sid = seg.get("id")
        vid_gcs, aud_gcs = v_map.get(sid), a_map.get(sid)
        if not vid_gcs: continue
        
        vid_local = ensure_local(vid_gcs)
        temp_files.append(vid_local)
        
        norm = os.path.join(TEMP_DIR, f"{pid}_norm_{sid}_{uuid.uuid4().hex[:6]}.mp4")
        temp_files.append(norm)
        
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", vid_local]
        filter_chain = ["[0:v]fps=24,format=yuv420p[v]"]
        
        if aud_gcs:
             aud_local = ensure_local(aud_gcs)
             temp_files.append(aud_local)
             dur_v, dur_a = get_media_duration(vid_local), get_media_duration(aud_local)
             tempo = min(2.0, max(1.0, dur_a / dur_v)) if dur_v > 0 else 1.0
             cmd.extend(["-i", aud_local])
             
             # Mix video audio (30%) and narration (150%)
             filter_chain.append("[0:a]volume=0.3[bg]")
             filter_chain.append(f"[1:a]atempo={tempo},apad,volume=1.5[fg]")
             filter_chain.append("[bg][fg]amix=inputs=2:duration=first[a]")
        else:
             # Use video audio at 100%
             filter_chain.append("[0:a]aformat=channel_layouts=stereo[a]")
             
        cmd.extend(["-filter_complex", ";".join(filter_chain), "-map", "[v]", "-map", "[a]", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", norm])
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            clips.append(norm)
        except subprocess.CalledProcessError as e:
            log_project(pid, f"FFmpeg normalization failed for {sid}: {e.stderr.decode() if e.stderr else str(e)}")
        except Exception as e:
            log_project(pid, f"Normalization error for {sid}: {e}")

    if clips:
        list_txt = os.path.join(TEMP_DIR, f"concat_{pid}_{uuid.uuid4().hex[:6]}.txt")
        with open(list_txt, "w") as f:
            for c in clips: f.write(f"file '{os.path.abspath(c)}'\n")
        
        final_local = os.path.join(OUTPUT_DIR, f"FINAL_{pid}_{uuid.uuid4().hex[:6]}.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_txt, "-c", "copy", final_local], check=True, stdout=subprocess.DEVNULL)
        
        gcs_final = get_gcs_path(pid, "output", f"FINAL_{pid}.mp4")
        upload_to_gcs(final_local, gcs_final)
        
        # Cleanup
        if os.path.exists(list_txt): os.remove(list_txt)
        if os.path.exists(final_local): os.remove(final_local)
        for f in temp_files:
            if os.path.exists(f) and f.startswith(TEMP_DIR): os.remove(f)
            
        return gcs_final
    return None

# === STEP EXECUTORS ===
def step_ingest(pid: str):
    try:
        proj = get_project(pid)
        update_project(pid, status="running", current_step="ingest")
        log_project(pid, "Starting Ingestion...")
        potential = []
        file_paths = proj.get("file_paths", [])
        if file_paths:
            with ThreadPoolExecutor(WORKER_THREADS_ASSETS) as pool:
                futs = []
                for item in file_paths:
                    if isinstance(item, str):
                        path = item
                        desc = None
                    else:
                        path = item.get("path")
                        desc = item.get("description")
                    
                    if path:
                        futs.append(pool.submit(task_analyze_image, pid, path, proj["prompt"], desc))

                for f in as_completed(futs):
                    try: potential.extend(f.result())
                    except Exception as e: log_project(pid, f"Analysis warning: {e}")
        update_project(pid, potential_assets=potential, status="waiting_detective", progress=15)
        log_project(pid, "Ingestion complete.")
    except Exception as e:
        logging.error(f"Step ingest failed: {e}", exc_info=True)
        log_project(pid, f"ERROR: Ingest step failed: {str(e)}")
        update_project(pid, status="failed", error=str(e))

def step_detective(pid: str):
    try:
        proj = get_project(pid)
        update_project(pid, status="running", current_step="detective")
        log_project(pid, "Starting Detective...")
        potential = proj.get("potential_assets", [])
        report = task_detective(pid, proj["prompt"], potential)
        
        # Ensure Asset IDs and Types exist
        for asset in report.get("assets", []):
            if "id" not in asset: asset["id"] = str(uuid.uuid4())[:8]
            if "type" not in asset: asset["type"] = "object"

        update_project(pid, report=report, status="waiting_planning", progress=25)
        log_project(pid, "Detective complete.")
    except Exception as e:
        logging.error(f"Step detective failed: {e}", exc_info=True)
        log_project(pid, f"ERROR: Detective step failed: {str(e)}")
        update_project(pid, status="failed", error=str(e))

def step_planning(pid: str):
    try:
        proj = get_project(pid)
        update_project(pid, status="running", current_step="planning")
        log_project(pid, "Starting Planning...")
        report = proj["report"]
        raw_manifest = task_architect(pid, report)
        manifest = task_critic(pid, report, raw_manifest)
        
        # Ensure IDs exist
        for seg in manifest.get("timeline", []):
            if "id" not in seg: seg["id"] = str(uuid.uuid4())[:8]
            
        update_project(pid, manifest=manifest, status="waiting_assets", progress=35)
        log_project(pid, "Planning complete.")
    except Exception as e:
        logging.error(f"Step planning failed: {e}", exc_info=True)
        log_project(pid, f"ERROR: Planning step failed: {str(e)}")
        update_project(pid, status="failed", error=str(e))

def step_assets(pid: str):
    try:
        proj = get_project(pid)
        update_project(pid, status="running", current_step="assets")
        log_project(pid, "Starting Asset Finalization...")
        manifest = proj["manifest"]
        report = proj["report"]
        # task_finalize_assets now handles incremental updates internally
        task_finalize_assets(pid, manifest, report)
        update_project(pid, status="waiting_anchors", progress=0) # Reset for next step
        log_project(pid, "Assets complete.")
    except Exception as e:
        logging.error(f"Step assets failed: {e}", exc_info=True)
        log_project(pid, f"ERROR: Assets step failed: {str(e)}")
        update_project(pid, status="failed", error=str(e))

def step_anchors(pid: str):
    try:
        proj = get_project(pid)
        update_project(pid, status="running", current_step="anchors")
        log_project(pid, "Starting Anchor Generation...")
        manifest = proj["manifest"]
        
        # Ensure IDs exist (migration from Pydantic to dicts lost default_factory)
        timeline = manifest.get("timeline", [])
        modified = False
        for seg in timeline:
            if "id" not in seg:
                seg["id"] = str(uuid.uuid4())[:8]
                modified = True
        if modified:
            update_project(pid, manifest=manifest)

        report = proj["report"]
        asset_map = proj.get("asset_map", {})
        
        anchor_map = proj.get("anchor_map", {})
        
        with ThreadPoolExecutor(WORKER_THREADS_ASSETS) as pool:
            start_futs = {}
            end_futs = {}
            
            # Submit all start anchors initially
            for seg in manifest.get("timeline", []):
                f = pool.submit(task_gen_anchor, pid, seg["id"], seg, asset_map, report.get("visual_style"), report.get("negative_prompt"), False)
                start_futs[f] = seg["id"]

            # Wait for ANY future to complete (start or end)
            completed_count = 0
            # Estimate total tasks: start anchors + potential end anchors (assume 50% have end anchors for progress calc)
            total_estimated = len(manifest.get("timeline", [])) * 1.5 
            update_project(pid, progress=0) # Reset progress

            while start_futs or end_futs:
                current_futs = list(start_futs.keys()) + list(end_futs.keys())
                if not current_futs: break
                
                from concurrent.futures import wait, FIRST_COMPLETED
                done, not_done = wait(current_futs, return_when=FIRST_COMPLETED)
                
                for f in done:
                    completed_count += 1
                    # Progress range: 0 -> 100 (Step-specific)
                    prog = int((completed_count / total_estimated) * 100)
                    update_project(pid, progress=min(99, prog))
                    
                    try:
                        res = f.result()
                        if f in start_futs:
                            seg_id = start_futs.pop(f)
                            anchor_map[f"{seg_id}_start"] = res
                            update_project(pid, anchor_map=anchor_map)
                            seg = next(s for s in manifest["timeline"] if s["id"] == seg_id)
                            if seg.get("mode") == VeoMode.FI:
                                ef = pool.submit(task_gen_anchor, pid, seg["id"], seg, asset_map, report.get("visual_style"), report.get("negative_prompt"), True, res)
                                end_futs[ef] = seg["id"]
                        elif f in end_futs:
                            seg_id = end_futs.pop(f)
                            anchor_map[f"{seg_id}_end"] = res
                            update_project(pid, anchor_map=anchor_map)
                    except Exception as e:
                        if f in start_futs: log_project(pid, f"Start anchor failed for {start_futs.pop(f)}: {e}")
                        elif f in end_futs: log_project(pid, f"End anchor failed for {end_futs.pop(f)}: {e}")

        update_project(pid, status="waiting_production", progress=0) # Reset for next step
        log_project(pid, "Anchors complete.")
    except Exception as e:
        logging.error(f"Step anchors failed: {e}", exc_info=True)
        log_project(pid, f"ERROR: Anchors step failed: {str(e)}")
        update_project(pid, status="failed", error=str(e))

def step_production(pid: str):
    try:
        proj = get_project(pid)
        update_project(pid, status="running", current_step="production")
        log_project(pid, "Starting Production...")
        manifest = proj["manifest"]
        report = proj["report"]
        anchor_map = proj["anchor_map"]
        
        v_map, a_map = proj.get("video_map", {}), proj.get("audio_map", {})
        
        # Use separate pools for VEO (limited quota) and TTS (high quota)
        with ThreadPoolExecutor(WORKER_THREADS_VEO) as veo_pool, \
             ThreadPoolExecutor(max_workers=16) as tts_pool:
             
            veo_futs = {veo_pool.submit(task_run_veo, pid, seg["id"], seg, anchor_map[f"{seg['id']}_start"], report.get("visual_style"), anchor_map.get(f"{seg['id']}_end")): seg["id"] for seg in manifest.get("timeline", [])}
            tts_futs = {tts_pool.submit(task_render_audio, pid, seg["id"], seg, manifest.get("narrator_voice_style"), manifest.get("language", "en-US")): seg["id"] for seg in manifest.get("timeline", [])}
            
            futs_to_type = {}
            for f, sid in veo_futs.items(): futs_to_type[f] = ('video', sid)
            for f, sid in tts_futs.items(): futs_to_type[f] = ('audio', sid)
            
            total_tasks = len(futs_to_type)
            completed_count = 0
            update_project(pid, progress=0) # Reset progress
            
            for f in as_completed(futs_to_type):
                completed_count += 1
                # Progress range: 0 -> 100 (Step-specific)
                prog = int((completed_count / total_tasks) * 100)
                
                type_, sid = futs_to_type[f]
                try:
                    res = f.result()
                    if type_ == 'video': v_map[sid] = res
                    else: a_map[sid] = res
                    update_project(pid, video_map=v_map, audio_map=a_map, progress=min(99, prog))
                except Exception as e:
                    log_project(pid, f"{type_} failed for {sid}: {e}")
            
        update_project(pid, status="waiting_assembly", progress=0) # Reset for next step
        log_project(pid, "Production complete.")
    except Exception as e:
        logging.error(f"Step production failed: {e}", exc_info=True)
        log_project(pid, f"ERROR: Production step failed: {str(e)}")
        update_project(pid, status="failed", error=str(e))

def step_assembly(pid: str):
    try:
        proj = get_project(pid)
        update_project(pid, status="running", current_step="assembly")
        log_project(pid, "Starting Assembly...")
        manifest = proj["manifest"]
        final_path = task_assemble(pid, manifest, proj.get("video_map", {}), proj.get("audio_map", {}))
        if final_path:
            # Result URL is now a GCS path, frontend needs to handle it
            update_project(pid, status="completed", progress=100, result={"url": final_path})
            log_project(pid, "Assembly SUCCESS!")
        else:
            update_project(pid, status="failed", error="Assembly failed")
            log_project(pid, "Assembly FAILED.")
    except Exception as e:
        logging.error(f"Step assembly failed: {e}", exc_info=True)
        log_project(pid, f"ERROR: Assembly step failed: {str(e)}")
        update_project(pid, status="failed", error=str(e))