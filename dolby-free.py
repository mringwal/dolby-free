#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "config.py"


@dataclass
class Settings:
    media_folders: list[Path]
    media_extensions: set[str]
    traversal_stop_components: set[str]
    audio_codecs_to_avoid: set[str]
    video_codecs_to_avoid: set[str]
    container_formats_to_avoid: set[str]
    target_extension: str
    target_video_codec: str
    target_audio_codec: str
    target_subtitle_codec: str
    target_audio_bitrate: str
    target_crf: int
    target_preset: str
    output_suffix: str
    replace_original_after_success: bool
    delete_original_after_success: bool
    skip_if_output_exists: bool
    cache_file: Path
    force_rescan: bool


@dataclass
class ProbeSummary:
    format_names: set[str]
    video_codecs: list[str]
    audio_codecs: list[str]
    subtitle_codecs: list[str]


def load_config_module(config_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("dolby_free_config", config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load config from {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_set(values: Any) -> set[str]:
    if values is None:
        return set()
    return {str(value).strip().lower() for value in values if str(value).strip()}


def load_settings(config_path: Path) -> Settings:
    config = load_config_module(config_path)
    avoid = getattr(config, "FORMATS_TO_AVOID", {})
    cache_file = getattr(config, "resolve_cache_file", None)
    if callable(cache_file):
        resolved_cache_file = Path(cache_file())
    else:
        raw_cache = Path(getattr(config, "CACHE_FILE", ".dolby-free-cache.json"))
        resolved_cache_file = raw_cache if raw_cache.is_absolute() else config_path.parent / raw_cache

    return Settings(
        media_folders=[Path(path).expanduser() for path in getattr(config, "MEDIA_FOLDERS", [])],
        media_extensions={str(ext).lower() for ext in getattr(config, "MEDIA_EXTENSIONS", set())},
        traversal_stop_components={str(part).strip() for part in getattr(config, "TRAVERSAL_STOP_COMPONENTS", set()) if str(part).strip()},
        audio_codecs_to_avoid=normalize_set(avoid.get("audio_codecs")),
        video_codecs_to_avoid=normalize_set(avoid.get("video_codecs")),
        container_formats_to_avoid=normalize_set(avoid.get("container_formats")),
        target_extension=str(getattr(config, "TARGET_EXTENSION", ".mkv")).lower(),
        target_video_codec=str(getattr(config, "TARGET_VIDEO_CODEC", "libx264")),
        target_audio_codec=str(getattr(config, "TARGET_AUDIO_CODEC", "aac")),
        target_subtitle_codec=str(getattr(config, "TARGET_SUBTITLE_CODEC", "copy")),
        target_audio_bitrate=str(getattr(config, "TARGET_AUDIO_BITRATE", "192k")),
        target_crf=int(getattr(config, "TARGET_CRF", 20)),
        target_preset=str(getattr(config, "TARGET_PRESET", "medium")),
        output_suffix=str(getattr(config, "OUTPUT_SUFFIX", "_dolby_free")),
        replace_original_after_success=bool(getattr(config, "REPLACE_ORIGINAL_AFTER_SUCCESS", False)),
        delete_original_after_success=bool(getattr(config, "DELETE_ORIGINAL_AFTER_SUCCESS", False)),
        skip_if_output_exists=bool(getattr(config, "SKIP_IF_OUTPUT_EXISTS", True)),
        cache_file=resolved_cache_file,
        force_rescan=bool(getattr(config, "FORCE_RESCAN", False)),
    )


def check_dependencies() -> None:
    missing = [name for name in ("ffprobe", "ffmpeg") if shutil.which(name) is None]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing required tool(s): {joined}")


def validate_settings(settings: Settings) -> None:
    if not settings.target_extension.startswith("."):
        raise RuntimeError("TARGET_EXTENSION must start with a dot, for example '.mkv'")
    if any(not ext.startswith(".") for ext in settings.media_extensions):
        raise RuntimeError("Every MEDIA_EXTENSIONS entry must start with a dot.")
    if settings.replace_original_after_success and settings.delete_original_after_success:
        raise RuntimeError(
            "REPLACE_ORIGINAL_AFTER_SUCCESS and DELETE_ORIGINAL_AFTER_SUCCESS cannot both be enabled."
        )
    if (
        not settings.replace_original_after_success
        and not settings.output_suffix
        and settings.target_extension in settings.media_extensions
    ):
        raise RuntimeError(
            "OUTPUT_SUFFIX cannot be empty when TARGET_EXTENSION can match the source file extension."
        )


def build_config_hash(settings: Settings) -> str:
    payload = {
        "media_extensions": sorted(settings.media_extensions),
        "traversal_stop_components": sorted(settings.traversal_stop_components),
        "audio_codecs_to_avoid": sorted(settings.audio_codecs_to_avoid),
        "video_codecs_to_avoid": sorted(settings.video_codecs_to_avoid),
        "container_formats_to_avoid": sorted(settings.container_formats_to_avoid),
        "target_extension": settings.target_extension,
        "target_video_codec": settings.target_video_codec,
        "target_audio_codec": settings.target_audio_codec,
        "target_subtitle_codec": settings.target_subtitle_codec,
        "target_audio_bitrate": settings.target_audio_bitrate,
        "target_crf": settings.target_crf,
        "target_preset": settings.target_preset,
        "output_suffix": settings.output_suffix,
        "replace_original_after_success": settings.replace_original_after_success,
        "delete_original_after_success": settings.delete_original_after_success,
        "skip_if_output_exists": settings.skip_if_output_exists,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def load_cache(cache_path: Path, config_hash: str) -> dict[str, Any]:
    if not cache_path.exists():
        return {"config_hash": config_hash, "entries": {}}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"config_hash": config_hash, "entries": {}}
    if data.get("config_hash") != config_hash:
        return {"config_hash": config_hash, "entries": {}}
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    return data


def save_cache(cache_path: Path, cache: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temp_file.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    temp_file.replace(cache_path)


def file_signature(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def iter_media_files(settings: Settings) -> list[Path]:
    files: list[Path] = []
    for folder in settings.media_folders:
        if not folder.exists():
            print(f"[warn] Folder does not exist: {folder}", file=sys.stderr)
            continue
        if not folder.is_dir():
            print(f"[warn] Not a directory: {folder}", file=sys.stderr)
            continue

        for root, dirnames, filenames in os.walk(folder, topdown=True):
            root_path = Path(root)
            if settings.traversal_stop_components and any(
                part in settings.traversal_stop_components for part in root_path.parts
            ):
                dirnames[:] = []
                continue

            if settings.traversal_stop_components:
                dirnames[:] = [
                    dirname
                    for dirname in dirnames
                    if dirname not in settings.traversal_stop_components
                ]

            for filename in filenames:
                path = root_path / filename
                if path.suffix.lower() not in settings.media_extensions:
                    continue
                if settings.output_suffix and path.stem.endswith(settings.output_suffix):
                    continue
                files.append(path)
    return sorted(files)


def run_ffprobe(path: Path) -> ProbeSummary:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"ffprobe failed for {path}")
    data = json.loads(result.stdout)
    format_names = normalize_set((data.get("format") or {}).get("format_name", "").split(","))
    video_codecs: list[str] = []
    audio_codecs: list[str] = []
    subtitle_codecs: list[str] = []
    for stream in data.get("streams", []):
        codec_name = str(stream.get("codec_name", "")).strip().lower()
        codec_type = str(stream.get("codec_type", "")).strip().lower()
        if codec_type == "video" and codec_name:
            video_codecs.append(codec_name)
        elif codec_type == "audio" and codec_name:
            audio_codecs.append(codec_name)
        elif codec_type == "subtitle" and codec_name:
            subtitle_codecs.append(codec_name)
    return ProbeSummary(
        format_names=format_names,
        video_codecs=video_codecs,
        audio_codecs=audio_codecs,
        subtitle_codecs=subtitle_codecs,
    )


def summarize_probe(probe: ProbeSummary) -> dict[str, list[str]]:
    return {
        "format_names": sorted(probe.format_names),
        "video_codecs": probe.video_codecs,
        "audio_codecs": probe.audio_codecs,
        "subtitle_codecs": probe.subtitle_codecs,
    }


def analyze_probe(probe: ProbeSummary, settings: Settings) -> dict[str, Any]:
    bad_audio = sorted(set(probe.audio_codecs) & settings.audio_codecs_to_avoid)
    bad_video = sorted(set(probe.video_codecs) & settings.video_codecs_to_avoid)
    bad_containers = sorted(probe.format_names & settings.container_formats_to_avoid)
    return {
        "bad_audio": bad_audio,
        "bad_video": bad_video,
        "bad_containers": bad_containers,
        "needs_conversion": bool(bad_audio or bad_video or bad_containers),
    }


def build_output_path(source: Path, settings: Settings) -> Path:
    if settings.replace_original_after_success:
        return source.with_suffix(settings.target_extension)
    return source.with_name(f"{source.stem}{settings.output_suffix}{settings.target_extension}")


def build_ffmpeg_command(source: Path, target: Path, analysis: dict[str, Any], settings: Settings) -> list[str]:
    video_mode = "transcode" if analysis["bad_video"] else "copy"
    audio_mode = "transcode" if analysis["bad_audio"] else "copy"

    command = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v?",
        "-map",
        "0:a?",
        "-map",
        "0:s?",
    ]

    if video_mode == "transcode":
        command.extend(
            [
                "-c:v",
                settings.target_video_codec,
                "-preset",
                settings.target_preset,
                "-crf",
                str(settings.target_crf),
            ]
        )
    else:
        command.extend(["-c:v", "copy"])

    if audio_mode == "transcode":
        command.extend(
            [
                "-c:a",
                settings.target_audio_codec,
                "-b:a",
                settings.target_audio_bitrate,
            ]
        )
    else:
        command.extend(["-c:a", "copy"])

    command.extend(["-c:s", settings.target_subtitle_codec])

    if settings.target_extension == ".mp4":
        command.extend(["-movflags", "+faststart"])

    command.append(str(target))
    return command


def ensure_target_parent(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)


def convert_file(
    source: Path,
    output_path: Path,
    analysis: dict[str, Any],
    settings: Settings,
    dry_run: bool,
) -> tuple[str, bool]:
    ensure_target_parent(output_path)
    if settings.skip_if_output_exists and output_path.exists() and output_path != source:
        return "output_exists", False

    if dry_run:
        command = build_ffmpeg_command(source, output_path, analysis, settings)
        print(f"[dry-run] {' '.join(command)}")
        return "dry_run", False

    with tempfile.NamedTemporaryFile(
        prefix=f"{output_path.stem}.",
        suffix=f".partial{output_path.suffix}",
        dir=output_path.parent,
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)

    command = build_ffmpeg_command(source, temp_path, analysis, settings)
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        temp_path.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed for {source}")

    if output_path.exists() and output_path != source:
        output_path.unlink()

    temp_path.replace(output_path)
    source_removed = False
    if settings.replace_original_after_success and output_path != source:
        source.unlink()
        source_removed = True
    elif settings.delete_original_after_success:
        source.unlink()
        source_removed = True
    return "converted", source_removed


def cache_entry_for(path: Path, signature: dict[str, int], probe: ProbeSummary, analysis: dict[str, Any], output_path: Path, status: str) -> dict[str, Any]:
    return {
        "signature": signature,
        "probe": summarize_probe(probe),
        "analysis": analysis,
        "output_path": str(output_path),
        "status": status,
    }


def probe_from_cache(entry: dict[str, Any]) -> ProbeSummary:
    probe = entry.get("probe", {})
    return ProbeSummary(
        format_names=set(probe.get("format_names", [])),
        video_codecs=list(probe.get("video_codecs", [])),
        audio_codecs=list(probe.get("audio_codecs", [])),
        subtitle_codecs=list(probe.get("subtitle_codecs", [])),
    )


def signatures_match(left: dict[str, Any], right: dict[str, int]) -> bool:
    return left.get("mtime_ns") == right["mtime_ns"] and left.get("size") == right["size"]


def build_final_cache_entry(path: Path, settings: Settings, status: str) -> dict[str, Any]:
    probe = run_ffprobe(path)
    analysis = analyze_probe(probe, settings)
    return cache_entry_for(
        path=path,
        signature=file_signature(path),
        probe=probe,
        analysis=analysis,
        output_path=path,
        status=status,
    )


def process_files(settings: Settings, dry_run: bool, force_rescan: bool) -> int:
    config_hash = build_config_hash(settings)
    cache = load_cache(settings.cache_file, config_hash)
    entries = cache.setdefault("entries", {})
    media_files = iter_media_files(settings)

    if not media_files:
        print("No media files found.")
        save_cache(settings.cache_file, cache)
        return 0

    stats = {
        "scanned": 0,
        "probed": 0,
        "converted": 0,
        "skipped_clean": 0,
        "skipped_cached": 0,
        "skipped_output_exists": 0,
        "errors": 0,
    }

    seen_paths: set[str] = set()
    for path in media_files:
        path_key = str(path.resolve())
        seen_paths.add(path_key)
        stats["scanned"] += 1
        signature = file_signature(path)
        entry = entries.get(path_key)

        use_cache = (
            entry is not None
            and not force_rescan
            and not settings.force_rescan
            and "probe" in entry
            and "analysis" in entry
            and signatures_match(entry.get("signature", {}), signature)
        )

        try:
            if use_cache:
                probe = probe_from_cache(entry)
                analysis = entry.get("analysis", analyze_probe(probe, settings))
                stats["skipped_cached"] += 1
            else:
                probe = run_ffprobe(path)
                analysis = analyze_probe(probe, settings)
                stats["probed"] += 1

            output_path = build_output_path(path, settings)
            if output_path == path and not settings.replace_original_after_success:
                raise RuntimeError("Output path resolves to the same file as the source.")

            if analysis["needs_conversion"]:
                status, source_removed = convert_file(path, output_path, analysis, settings, dry_run)
                if status == "converted":
                    stats["converted"] += 1
                elif status == "output_exists":
                    stats["skipped_output_exists"] += 1
            else:
                status = "clean"
                source_removed = False
                stats["skipped_clean"] += 1

            if status == "converted" and not dry_run and output_path.exists():
                if output_path == path:
                    entries[path_key] = build_final_cache_entry(output_path, settings, status)
                elif settings.replace_original_after_success and source_removed:
                    entries.pop(path_key, None)
                    output_key = str(output_path.resolve())
                    entries[output_key] = build_final_cache_entry(output_path, settings, status)
                    seen_paths.add(output_key)
                else:
                    entries[path_key] = cache_entry_for(path, signature, probe, analysis, output_path, status)
            elif source_removed:
                entries.pop(path_key, None)
            else:
                entries[path_key] = cache_entry_for(path, signature, probe, analysis, output_path, status)

            save_cache(settings.cache_file, cache)

            if not use_cache or status in {"converted", "dry_run"}:
                reason_bits = []
                if analysis["bad_audio"]:
                    reason_bits.append(f"audio={','.join(analysis['bad_audio'])}")
                if analysis["bad_video"]:
                    reason_bits.append(f"video={','.join(analysis['bad_video'])}")
                if analysis["bad_containers"]:
                    reason_bits.append(f"container={','.join(analysis['bad_containers'])}")
                reason = "; ".join(reason_bits) if reason_bits else "already compatible"
                print(f"[{status}] {path} -> {output_path} ({reason})")
        except Exception as exc:
            stats["errors"] += 1
            print(f"[error] {path}: {exc}", file=sys.stderr)
            entries[path_key] = {
                "signature": signature,
                "status": "error",
                "error": str(exc),
            }
            save_cache(settings.cache_file, cache)

    stale_paths = [path for path in entries if path not in seen_paths]
    for path in stale_paths:
        del entries[path]

    save_cache(settings.cache_file, cache)

    print("")
    print("Summary")
    print(f"  scanned: {stats['scanned']}")
    print(f"  probed: {stats['probed']}")
    print(f"  converted: {stats['converted']}")
    print(f"  skipped_clean: {stats['skipped_clean']}")
    print(f"  skipped_cached: {stats['skipped_cached']}")
    print(f"  skipped_output_exists: {stats['skipped_output_exists']}")
    print(f"  errors: {stats['errors']}")

    return 1 if stats["errors"] else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan folders for avoid-listed media formats and convert them with ffmpeg."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to config.py",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be converted without writing files.",
    )
    parser.add_argument(
        "--force-rescan",
        action="store_true",
        help="Ignore the scan cache and run ffprobe on every media file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings(args.config.resolve())
    check_dependencies()
    validate_settings(settings)
    if not settings.media_folders:
        print("No MEDIA_FOLDERS configured in config.py.", file=sys.stderr)
        return 1
    return process_files(settings, dry_run=args.dry_run, force_rescan=args.force_rescan)


if __name__ == "__main__":
    raise SystemExit(main())
