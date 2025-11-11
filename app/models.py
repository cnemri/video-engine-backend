import uuid
from typing import List, Optional, Literal, Union
from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator

class VideoCategory(str, Enum):
    AD_SPOT = "ad_spot"
    EXPLAINER = "explainer"
    NARRATIVE = "narrative_film"
    SOCIAL_SHORT = "social_media_short"
    PRODUCT_SHOWCASE = "product_showcase"
    EDUCATIONAL = "educational_tutorial"
    CORPORATE = "corporate_comms"
    MUSIC_VISUALIZER = "music_video_visualizer"
    DOCUMENTARY = "documentary_segment"
    TRAILER = "movie_trailer"
    NEWS = "news_broadcast_segment"
    EVENT_RECAP = "event_recap_sizzle"
    REAL_ESTATE = "real_estate_tour"
    COMEDY = "comedy_skit"
    LIFESTYLE = "lifestyle_vlog"
    EXPERIMENTAL = "experimental_art"

class AssetType(str, Enum):
    CHAR = "character"
    OBJ = "object"
    LOC = "location"

class PotentialAsset(BaseModel):
    name: str
    type: AssetType
    source_file: str
    extraction_prompt: str

class AssetAnalysis(BaseModel):
    items: List[PotentialAsset]

class AssetDef(BaseModel):
    id: str
    type: AssetType
    is_supplied: bool = False
    description: str
    voice_style: Optional[str] = None
    source_file: Optional[str] = None
    extraction_prompt: Optional[str] = None
    local_path: Optional[str] = None
    visual_prompt: Optional[str] = None

class VeoMode(str, Enum):
    I2V = "i2v"
    FI = "fi"

class Cinematography(BaseModel):
    shot_type: Literal["Wide Establishing", "Full Body", "Medium Shot", "Close-up", "Extreme Close-up", "Over-the-Shoulder", "POV", "Low Angle", "High Angle", "Drone Shot"]
    movement: Literal["Static Tripod", "Slow Pan Left", "Slow Pan Right", "Tilt Up", "Tilt Down", "Slow Zoom In", "Slow Zoom Out", "Dolly Forward", "Dolly Backward", "Truck Left", "Truck Right", "Handheld Shake", "Orbit"]
    lighting: str

class SceneDetails(BaseModel):
    subject_focus: str
    pre_action_state: str
    main_action: str
    environment_context: str

class DialogueLine(BaseModel):
    speaker_id: str
    text: str

class VeoSegmentBase(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    duration: Literal["4", "6", "8"]
    mode: VeoMode
    cinematography: Cinematography
    scene_details: SceneDetails
    anchor_prompt: str
    veo_prompt: str
    narration: Optional[str] = None
    dialogue: List[DialogueLine] = Field(default_factory=list)
    asset_ids: List[str] = Field(default_factory=list)

class VeoI2VSegment(VeoSegmentBase):
    mode: Literal[VeoMode.I2V]

class VeoInterpSegment(VeoSegmentBase):
    mode: Literal[VeoMode.FI]
    end_anchor_prompt: str

VeoSegmentUnion = Union[VeoI2VSegment, VeoInterpSegment]

class DetectiveReport(BaseModel):
    category: VideoCategory
    target_duration_seconds: Optional[int] = None
    visual_style: str
    negative_prompt: str
    assets: List[AssetDef]
    creative_brief: str

class ArchitectManifest(BaseModel):
    timeline: List[VeoSegmentUnion]
    narrator_voice_style: str
    language: str = "en-US"
    estimated_total_duration: int

class CritiqueResult(BaseModel):
    approved: bool
    feedback: str
    improved_manifest: Optional[ArchitectManifest] = None

class AnchorCritiqueResult(BaseModel):
    approved: bool
    feedback: str
    improved_prompt: Optional[str] = None