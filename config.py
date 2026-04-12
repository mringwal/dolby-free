from pathlib import Path


# Folders scanned recursively for movie files.
MEDIA_FOLDERS = [
    "/path/to/movies",
]

# Extensions treated as media files.
MEDIA_EXTENSIONS = {
    ".avi",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpg",
    ".mpeg",
    ".ts",
    ".wmv",
}

# ffprobe codec/container names that should trigger a conversion.
FORMATS_TO_AVOID = {
    "audio_codecs": {
        "ac3",
        "eac3",
        "mlp",
        "truehd",
    },
    "video_codecs": set(),
    "container_formats": set(),
}

# Output defaults: H.264 video + AAC audio in MKV keeps compatibility high
# while preserving subtitles more reliably than MP4.
TARGET_EXTENSION = ".mkv"
TARGET_VIDEO_CODEC = "libx264"
TARGET_AUDIO_CODEC = "aac"
TARGET_SUBTITLE_CODEC = "copy"
TARGET_AUDIO_BITRATE = "192k"
TARGET_CRF = 20
TARGET_PRESET = "medium"

# Output naming and file handling.
OUTPUT_SUFFIX = "_dolby_free"
REPLACE_ORIGINAL_AFTER_SUCCESS = False
DELETE_ORIGINAL_AFTER_SUCCESS = False
SKIP_IF_OUTPUT_EXISTS = True

# Scan cache location. Relative paths are resolved next to this config file.
CACHE_FILE = ".dolby-free-cache.json"

# Set to True if you want to force ffprobe on every file even when unchanged.
FORCE_RESCAN = False


def resolve_cache_file() -> Path:
    cache_file = Path(CACHE_FILE)
    if cache_file.is_absolute():
        return cache_file
    return Path(__file__).resolve().parent / cache_file
