from pathlib import Path
from typing import Dict, List

from .autopublisher import AutopublisherWrapper
from .autovideo import AutoVideoWrapper
from .bulk_text_cleaner import BulkTextCleanerWrapper
from .content_combiner import ContentCombinerWrapper
from .director20 import Director20Wrapper
from .elevenlabs import YoutubeElevenLabsWrapper
from .site_tts_stage import SiteTtsStageWrapper
from .gemini_auto import GeminiAutoWrapper
from .youtube_safe_text import YoutubeSafeTextWrapper
from .youtube_selection import YoutubeSelectionWrapper
from .base import BaseWrapper


WRAPPER_REGISTRY = {
    BulkTextCleanerWrapper.contract.stage: BulkTextCleanerWrapper,
    GeminiAutoWrapper.contract.stage: GeminiAutoWrapper,
    SiteTtsStageWrapper.contract.stage: SiteTtsStageWrapper,
    YoutubeElevenLabsWrapper.contract.stage: YoutubeElevenLabsWrapper,
    ContentCombinerWrapper.contract.stage: ContentCombinerWrapper,
    AutopublisherWrapper.contract.stage: AutopublisherWrapper,
    YoutubeSelectionWrapper.contract.stage: YoutubeSelectionWrapper,
    YoutubeSafeTextWrapper.contract.stage: YoutubeSafeTextWrapper,
    Director20Wrapper.contract.stage: Director20Wrapper,
    AutoVideoWrapper.contract.stage: AutoVideoWrapper,
}

COMMON_STAGES = ["bulk_text_cleaner", "gemini_auto"]
SITE_STAGES = ["site_tts", "content_combiner", "autopublisher"]
YOUTUBE_STAGES = ["youtube_selection", "youtube_safe_text", "director20", "youtube_tts", "autovideo"]


def get_stage_sequence(pipeline: str) -> List[str]:
    if pipeline == "site":
        return [*COMMON_STAGES, *SITE_STAGES]
    if pipeline == "youtube":
        return [*COMMON_STAGES, *YOUTUBE_STAGES]
    if pipeline in {"full", "all"}:
        return [*COMMON_STAGES, *SITE_STAGES, *YOUTUBE_STAGES]
    return [*COMMON_STAGES]


def build_wrapper(stage: str, root: Path, legacy_entrypoints: Dict[str, str]) -> BaseWrapper:
    klass = WRAPPER_REGISTRY[stage]
    entry_rel = legacy_entrypoints.get(stage, klass.contract.entrypoint)
    entry = root / entry_rel if entry_rel else None
    if klass is SiteTtsStageWrapper:
        return SiteTtsStageWrapper(entry, root_dir=root)
    return klass(entry)


def build_wrappers_for_pipeline(
    pipeline: str,
    root: Path,
    legacy_entrypoints: Dict[str, str],
) -> List[BaseWrapper]:
    return [build_wrapper(s, root, legacy_entrypoints) for s in get_stage_sequence(pipeline)]
