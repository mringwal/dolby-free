# dolby-free

`dolby-free.py` scans configured folders, uses `ffprobe` to inspect each media file, and converts files whose codecs or container match your avoid list.

## How it works

- Folders come from `config.py`.
- The script stores scan results in `.dolby-free-cache.json` and flushes progress after each processed file, so interrupted runs can resume without losing earlier work.
- If an avoid-listed audio codec is found, audio is transcoded to AAC.
- If an avoid-listed video codec is found, video is transcoded to H.264.
- If only the container is avoid-listed, the file is remuxed into the target container without re-encoding audio/video.
- Converted files are written next to the source file with the suffix from `OUTPUT_SUFFIX` by default.
- If `REPLACE_ORIGINAL_AFTER_SUCCESS` is enabled, the converted file replaces the original after a successful conversion.

## Configure

Edit [config.py](/Users/mringwal/Projects/dolby-free/config.py) and set:

- `MEDIA_FOLDERS` to the directories you want scanned.
- `FORMATS_TO_AVOID["audio_codecs"]`, `["video_codecs"]`, and `["container_formats"]` to the ffprobe names you want to reject.
- Output settings if you want something other than H.264/AAC in MKV.
- `REPLACE_ORIGINAL_AFTER_SUCCESS = True` if you want the converted file to become the main copy once conversion succeeds.
- `DELETE_ORIGINAL_AFTER_SUCCESS = True` only if you want to keep the suffixed output file and remove the source file.

The defaults target Dolby-family audio codecs:

```python
FORMATS_TO_AVOID = {
    "audio_codecs": {"ac3", "eac3", "mlp", "truehd"},
    "video_codecs": set(),
    "container_formats": set(),
}
```

## Run

Dry run first:

```bash
python3 dolby-free.py --dry-run
```

Then run it for real:

```bash
python3 dolby-free.py
```

If you want to ignore the cache and probe everything again:

```bash
python3 dolby-free.py --force-rescan
```
