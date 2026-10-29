import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from html import escape

import edge_tts
import imageio_ffmpeg

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask


app = FastAPI(
    title="OFSS AI Video Studio",
    version="3.0.0",
    description="Create MP4 videos from images with optional AI narration."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

VOICES = {
    "en-US-AriaNeural": "English - Aria (Female)",
    "en-US-GuyNeural": "English - Guy (Male)",
    "en-US-JennyNeural": "English - Jenny (Female)",
    "en-US-ChristopherNeural": "English - Christopher (Male)",
    "en-GB-SoniaNeural": "British English - Sonia",
    "en-GB-RyanNeural": "British English - Ryan",
    "hi-IN-SwaraNeural": "Hindi - Swara (Female)",
    "hi-IN-MadhurNeural": "Hindi - Madhur (Male)",
}

MAX_IMAGES = 40
MAX_IMAGE_SIZE = 12 * 1024 * 1024
MAX_SCRIPT_LENGTH = 12000


@app.get("/")
def home():
    return {
        "service": "OFSS AI Video Studio",
        "version": "3.0.0",
        "status": "ok",
        "upload_page": "/upload",
        "docs": "/docs",
        "endpoint": "/api/video",
    }


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "ffmpeg": "available",
        "version": "3.0.0",
    }


@app.get("/upload", response_class=HTMLResponse)
def upload_page():
    voice_options = "".join(
        f'<option value="{escape(name)}">{escape(label)}</option>'
        for name, label in VOICES.items()
    )

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OFSS AI Video Studio</title>
<style>
* { box-sizing: border-box; }
body {
    margin: 0;
    padding: 20px;
    font-family: Arial, sans-serif;
    background: #f1f5fb;
    color: #172033;
}
main { max-width: 720px; margin: 20px auto; }
header {
    padding: 25px;
    background: #142a52;
    color: white;
    border-radius: 14px 14px 0 0;
}
header h1 { margin: 0 0 10px; }
header p { line-height: 1.6; margin-bottom: 0; }
form {
    padding: 24px;
    background: white;
    border-radius: 0 0 14px 14px;
    box-shadow: 0 8px 28px #152b4d15;
}
label {
    display: block;
    margin: 19px 0 8px;
    font-weight: bold;
}
input, select, textarea, button {
    width: 100%;
    padding: 13px;
    font-size: 16px;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
}
input[type=file] { background: #f8faff; }
textarea { resize: vertical; line-height: 1.5; }
small {
    display: block;
    margin-top: 6px;
    color: #5b6678;
    line-height: 1.5;
}
button {
    margin-top: 24px;
    background: #155eef;
    color: white;
    border: 0;
    font-weight: bold;
    cursor: pointer;
}
button:disabled { opacity: .6; cursor: wait; }
#status {
    display: none;
    margin-top: 18px;
    padding: 14px;
    border-radius: 8px;
    line-height: 1.6;
    overflow-wrap: anywhere;
}
.note {
    margin-top: 18px;
    padding: 13px;
    border-radius: 8px;
    background: #edf4ff;
    font-size: 14px;
    line-height: 1.6;
}
</style>
</head>
<body>
<main>
<header>
<h1>OFSS AI Video Studio</h1>
<p>Create MP4 videos from your images with optional Hindi or English
AI narration. Choose YouTube landscape or vertical Reels format.</p>
</header>

<form id="videoForm">
<label for="images">Upload images *</label>
<input id="images" name="images" type="file"
accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"
multiple required>
<small>Select 1–40 JPG, PNG, or WebP images. Maximum 12 MB per image.</small>

<label for="script">Voice-over script (optional)</label>
<textarea id="script" name="script" rows="7" maxlength="12000"
placeholder="Paste your Hindi or English narration here..."></textarea>
<small>Maximum 12,000 characters. Leave empty for a video without narration.</small>

<label for="voice">AI voice</label>
<select id="voice" name="voice">VOICE_OPTIONS</select>

<label for="aspect_ratio">Video format</label>
<select id="aspect_ratio" name="aspect_ratio">
<option value="16:9">Landscape 16:9 — YouTube</option>
<option value="9:16">Portrait 9:16 — Shorts / Reels</option>
</select>

<label for="seconds_per_image">Seconds per image</label>
<input id="seconds_per_image" name="seconds_per_image"
type="number" min="3" max="30" value="8" required>
<small>Used when no script is provided. With narration, the audio determines duration.</small>

<button id="submitButton" type="submit">Create and Download MP4</button>
<div id="status" role="status" aria-live="polite"></div>

<div class="note">
Keep this page open while the video is being generated. Free hosting
may take several minutes to process multiple images.
</div>
</form>
</main>

<script>
const form = document.getElementById("videoForm");
const button = document.getElementById("submitButton");
const statusBox = document.getElementById("status");

form.addEventListener("submit", async function(event) {
    event.preventDefault();

    const files = document.getElementById("images").files;
    const script = document.getElementById("script").value;

    if (!files.length || files.length > 40) {
        alert("Please select between 1 and 40 images.");
        return;
    }

    if (script.length > 12000) {
        alert("Script must not exceed 12,000 characters.");
        return;
    }

    const data = new FormData(form);

    button.disabled = true;
    statusBox.style.display = "block";
    statusBox.style.background = "#edf4ff";
    statusBox.textContent =
        "Creating your video. Please wait and keep this page open.";

    try {
        const response = await fetch("/api/video", {
            method: "POST",
            body: data
        });

        if (!response.ok) {
            let message = "Video generation failed.";
            try {
                const error = await response.json();
                message = error.detail || message;
            } catch (_) {}
            throw new Error(message);
        }

        const blob = await response.blob();

        if (!blob.size) {
            throw new Error("The server returned an empty video.");
        }

        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = "OFSS-AI-Video.mp4";
        document.body.appendChild(link);
        link.click();
        link.remove();

        setTimeout(() => URL.revokeObjectURL(url), 60000);

        statusBox.style.background = "#e8f8ed";
        statusBox.textContent =
            "Video created. Your MP4 download should have started.";

    } catch (error) {
        statusBox.style.background = "#fff0f0";
        statusBox.textContent = "Error: " + error.message;
    } finally {
        button.disabled = false;
    }
});
</script>
</body>
</html>
"""
    html = html.replace("VOICE_OPTIONS", voice_options)
    return HTMLResponse(content=html)


def run_ffmpeg(args, timeout=900):
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-y", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        message = (result.stderr or "FFmpeg failed")[-2500:]
        raise RuntimeError(message)

    return result


def audio_duration(path):
    result = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-i", str(path),
            "-f", "null",
            "-"
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    output = (result.stderr or "") + (result.stdout or "")
    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
        output,
    )

    if not match:
        raise RuntimeError("Could not determine audio duration.")

    hours, minutes, seconds = match.groups()
    duration = (
        int(hours) * 3600
        + int(minutes) * 60
        + float(seconds)
    )

    if duration <= 0:
        raise RuntimeError("Generated audio duration is zero.")

    return duration


def cleanup_directory(path):
    shutil.rmtree(str(path), ignore_errors=True)


@app.post("/api/video")
async def create_video(
    images: list[UploadFile] = File(
        ...,
        description="Upload one or more JPG, PNG, or WebP image files",
    ),
    script: str = Form(""),
    voice: str = Form("en-US-AriaNeural"),
    aspect_ratio: str = Form("16:9"),
    seconds_per_image: int = Form(8),
):
    if not images or len(images) > MAX_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Upload between 1 and {MAX_IMAGES} images.",
        )

    if voice not in VOICES:
        raise HTTPException(status_code=400, detail="Unsupported voice.")

    if aspect_ratio not in ("16:9", "9:16"):
        raise HTTPException(
            status_code=400,
            detail="Choose 16:9 or 9:16.",
        )

    if not 3 <= seconds_per_image <= 30:
        raise HTTPException(
            status_code=400,
            detail="Seconds per image must be between 3 and 30.",
        )

    if len(script) > MAX_SCRIPT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail="Script must not exceed 12,000 characters.",
        )

    workdir = Path(tempfile.mkdtemp(prefix="ofss_video_"))
    output_path = workdir / "OFSS-AI-Video.mp4"

    try:
        valid_images = []

        for index, upload in enumerate(images):
            try:
                content_type = (upload.content_type or "").lower()

                if content_type not in {
                    "image/jpeg",
                    "image/png",
                    "image/webp",
                }:
                    continue

                data = await upload.read(MAX_IMAGE_SIZE + 1)

                if not data or len(data) > MAX_IMAGE_SIZE:
                    continue

                suffix = {
                    "image/jpeg": ".jpg",
                    "image/png": ".png",
                    "image/webp": ".webp",
                }[content_type]

                image_path = workdir / f"image_{index:03d}{suffix}"
                image_path.write_bytes(data)
                valid_images.append(image_path)

            finally:
                await upload.close()

        if not valid_images:
            raise HTTPException(
                status_code=400,
                detail="No valid images. Use JPG, PNG, or WebP files under 12 MB each.",
            )

        width, height = (
            (1280, 720)
            if aspect_ratio == "16:9"
            else (720, 1280)
        )

        fps = 24
        narration = script.strip()
        voice_path = workdir / "voice.mp3"

        if narration:
            try:
                await edge_tts.Communicate(
                    narration,
                    voice,
                    rate="+0%",
                ).save(str(voice_path))
            except Exception as exc:
                raise RuntimeError(
                    f"AI voice generation failed: {str(exc)[:400]}"
                ) from exc

            duration = audio_duration(voice_path)
        else:
            duration = len(valid_images) * seconds_per_image

        per_image = duration / len(valid_images)
        segments = []

        for index, image_path in enumerate(valid_images):
            segment_path = workdir / f"segment_{index:03d}.mp4"

            video_filter = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
                "zoompan=z='min(zoom+0.0005,1.08)':"
                f"d=1:s={width}x{height}:fps={fps},"
                "format=yuv420p"
            )

            run_ffmpeg([
                "-loop", "1",
                "-framerate", str(fps),
                "-i", str(image_path),
                "-vf", video_filter,
                "-an",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-r", str(fps),
                "-t", f"{per_image:.3f}",
                str(segment_path),
            ])

            if not segment_path.exists() or segment_path.stat().st_size == 0:
                raise RuntimeError(
                    f"Could not create video segment {index + 1}."
                )

            segments.append(segment_path)

        concat_path = workdir / "segments.txt"
        concat_path.write_text(
            "".join(
                f"file '{segment.as_posix()}'\n"
                for segment in segments
            ),
            encoding="utf-8",
        )

        silent_video = workdir / "silent.mp4"

        run_ffmpeg([
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_path),
            "-c", "copy",
            "-movflags", "+faststart",
            str(silent_video),
        ])

        if narration:
            run_ffmpeg([
                "-i", str(silent_video),
                "-i", str(voice_path),
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "128k",
                "-shortest",
                "-movflags", "+faststart",
                str(output_path),
            ])
        else:
            shutil.copy2(silent_video, output_path)

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("The MP4 output was not created.")

        return FileResponse(
            path=str(output_path),
            media_type="video/mp4",
            filename="OFSS-AI-Video.mp4",
            background=BackgroundTask(cleanup_directory, workdir),
        )

    except HTTPException:
        cleanup_directory(workdir)
        raise

    except subprocess.TimeoutExpired:
        cleanup_directory(workdir)
        raise HTTPException(
            status_code=504,
            detail="Video creation timed out. Try fewer images or a shorter script.",
        )

    except Exception as exc:
        cleanup_directory(workdir)
        raise HTTPException(
            status_code=500,
            detail=f"Video generation failed: {str(exc)[:1000]}",
        )
