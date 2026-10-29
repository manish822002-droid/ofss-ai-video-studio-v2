
import asyncio
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
    version="3.1.0",
    description="Create MP4 videos from images with smooth camera movement and optional AI narration."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

VOICES = {
    "en-US-AriaNeural": "English US - Aria Female",
    "en-US-GuyNeural": "English US - Guy Male",
    "en-US-JennyNeural": "English US - Jenny Female",
    "en-US-ChristopherNeural": "English US - Christopher Male",
    "en-GB-SoniaNeural": "English UK - Sonia Female",
    "en-GB-RyanNeural": "English UK - Ryan Male",
    "hi-IN-SwaraNeural": "Hindi - Swara Female",
    "hi-IN-MadhurNeural": "Hindi - Madhur Male",
}

MAX_IMAGES = 40
MAX_IMAGE_SIZE = 12 * 1024 * 1024
MAX_SCRIPT_LENGTH = 12000
ALLOWED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}


def run_ffmpeg(args, timeout=600):
    command = [
        FFMPEG,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
    ] + args

    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            504,
            "Video rendering timed out. Try fewer images."
        )

    if result.returncode != 0:
        message = (result.stderr or "FFmpeg failed")[-3000:]
        raise HTTPException(
            500,
            f"Video rendering failed: {message}"
        )

    return result


async def make_tts(script, voice, output_path):
    try:
        communicate = edge_tts.Communicate(
            script,
            voice
        )
        await communicate.save(str(output_path))
    except Exception as exc:
        raise HTTPException(
            502,
            f"Voice generation failed: {str(exc)[:500]}"
        )


def cleanup_folder(folder):
    shutil.rmtree(folder, ignore_errors=True)


@app.get("/")
async def home():
    return {
        "service": "OFSS AI Video Studio",
        "version": "3.1.0",
        "status": "ok",
        "upload_page": "/upload",
        "docs": "/docs",
        "endpoint": "/api/video",
    }


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "ffmpeg_available": Path(FFMPEG).exists(),
        "version": "3.1.0",
    }


@app.get("/upload", response_class=HTMLResponse)
async def upload_page():
    options = "".join(
        f'<option value="{escape(key)}">{escape(label)}</option>'
        for key, label in VOICES.items()
    )

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OFSS AI Video Studio</title>
<style>
*{{box-sizing:border-box}}
body{{font-family:Arial,sans-serif;background:#f1f5f9;color:#172033;margin:0;padding:18px}}
main{{max-width:720px;margin:20px auto;background:white;padding:24px;border-radius:15px;box-shadow:0 5px 25px #0001}}
h1{{margin-top:0}}
p{{color:#526176;line-height:1.5}}
label{{display:block;font-weight:bold;margin:18px 0 7px}}
input,select,textarea{{width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:8px;font-size:16px}}
textarea{{min-height:120px;resize:vertical}}
button{{width:100%;padding:14px;margin-top:22px;background:#155eef;color:white;border:0;border-radius:9px;font-size:17px;font-weight:bold;cursor:pointer}}
button:disabled{{opacity:.6}}
small{{color:#64748b}}
#status{{white-space:pre-wrap;margin-top:18px;line-height:1.5}}
</style>
</head>
<body>
<main>
<h1>OFSS AI Video Studio</h1>
<p>Create MP4 videos with smooth zoom-in, zoom-out and horizontal camera movement.</p>
<form id="videoForm">
<label>Choose photos (1–40)</label>
<input type="file" name="images" accept="image/jpeg,image/png,image/webp" multiple required>
<small>JPG, PNG or WebP. Maximum 12 MB per photo.</small>

<label>Voice-over script (optional)</label>
<textarea name="script" maxlength="12000" placeholder="Paste your Hindi or English narration here. Leave empty for no narration."></textarea>

<label>Voice</label>
<select name="voice">{options}</select>

<label>Video orientation</label>
<select name="aspect_ratio">
<option value="16:9">Landscape 16:9 - YouTube</option>
<option value="9:16">Portrait 9:16 - Shorts/Reels</option>
</select>

<label>Duration per photo</label>
<select name="seconds_per_image">
<option value="5">5 seconds</option>
<option value="6">6 seconds</option>
<option value="8" selected>8 seconds</option>
<option value="10">10 seconds</option>
<option value="12">12 seconds</option>
</select>

<button id="submitBtn" type="submit">Create and Download MP4</button>
</form>
<div id="status" role="status"></div>
</main>

<script>
const form = document.getElementById('videoForm');
const statusBox = document.getElementById('status');
const btn = document.getElementById('submitBtn');

form.addEventListener('submit', async (event) => {{
    event.preventDefault();

    const files = form.querySelector('input[name="images"]').files;

    if (!files.length) {{
        statusBox.textContent = 'Please select at least one photo.';
        return;
    }}

    if (files.length > 40) {{
        statusBox.textContent = 'Maximum 40 photos allowed.';
        return;
    }}

    btn.disabled = true;
    statusBox.textContent = 'Creating your video. Please keep this page open; rendering may take several minutes.';

    try {{
        const response = await fetch('/api/video', {{
            method: 'POST',
            body: new FormData(form)
        }});

        if (!response.ok) {{
            let message = await response.text();
            try {{
                message = JSON.parse(message).detail || message;
            }} catch (e) {{}}
            throw new Error(message);
        }}

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');

        link.href = url;
        link.download = 'ofss-ai-video.mp4';
        document.body.appendChild(link);
        link.click();
        link.remove();

        setTimeout(() => URL.revokeObjectURL(url), 60000);
        statusBox.textContent = 'Video created. Your MP4 download should begin.';
    }} catch (error) {{
        statusBox.textContent = 'Video creation failed: ' + error.message;
    }} finally {{
        btn.disabled = false;
    }}
}});
</script>
</body>
</html>""")


@app.post("/api/video")
async def create_video(
    images: list[UploadFile] = File(...),
    script: str = Form(""),
    voice: str = Form("en-US-AriaNeural"),
    aspect_ratio: str = Form("16:9"),
    seconds_per_image: int = Form(8),
):
    if not images or len(images) > MAX_IMAGES:
        raise HTTPException(400, "Upload between 1 and 40 images.")

    if voice not in VOICES:
        raise HTTPException(400, "Unsupported voice.")

    if aspect_ratio not in {"16:9", "9:16"}:
        raise HTTPException(400, "Invalid video orientation.")

    if seconds_per_image not in {5, 6, 8, 10, 12}:
        raise HTTPException(400, "Invalid photo duration.")

    script = (script or "").strip()

    if len(script) > MAX_SCRIPT_LENGTH:
        raise HTTPException(400, "Script exceeds 12000 characters.")

    workdir = Path(tempfile.mkdtemp(prefix="ofss_video_"))

    try:
        saved_images = []

        for index, upload in enumerate(images):
            content_type = (upload.content_type or "").lower()

            if content_type not in ALLOWED_TYPES:
                raise HTTPException(
                    400,
                    f"Photo {index + 1}: upload JPG, PNG or WebP."
                )

            data = await upload.read(MAX_IMAGE_SIZE + 1)

            if not data:
                raise HTTPException(
                    400,
                    f"Photo {index + 1} is empty."
                )

            if len(data) > MAX_IMAGE_SIZE:
                raise HTTPException(
                    400,
                    f"Photo {index + 1} exceeds 12 MB."
                )

            suffix = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
            }[content_type]

            image_path = workdir / f"image_{index:03d}{suffix}"
            image_path.write_bytes(data)
            saved_images.append(image_path)
            await upload.close()

        if aspect_ratio == "16:9":
            width, height = 1280, 720
        else:
            width, height = 720, 1280

        fps = 25
        frame_count = seconds_per_image * fps
        segments = []

        for index, image_path in enumerate(saved_images):
            segment_path = workdir / f"segment_{index:03d}.mp4"

            # Alternate between zooming in and out, and
            # reverse the horizontal pan on every other photo.
            zoom_in = index % 2 == 0
            pan_forward = (index // 2) % 2 == 0

            if zoom_in:
                zoom_expr = "min(zoom+0.0009,1.14)"
            else:
                zoom_expr = "if(eq(on,0),1.14,max(zoom-0.0009,1.0))"

            if pan_forward:
                x_expr = (
                    f"(iw-iw/zoom)*on/{frame_count - 1}"
                )
            else:
                x_expr = (
                    f"(iw-iw/zoom)*(1-on/{frame_count - 1})"
                )

            y_expr = "ih/2-ih/zoom/2"

            filters = (
                f"scale={width * 2}:{height * 2}:"
                "force_original_aspect_ratio=increase,"
                f"crop={width * 2}:{height * 2},"
                f"zoompan=z='{zoom_expr}':"
                f"x='{x_expr}':y='{y_expr}':"
                f"d=1:s={width}x{height}:fps={fps},"
                f"trim=duration={seconds_per_image},"
                "setpts=PTS-STARTPTS,setsar=1,format=yuv420p"
            )

            run_ffmpeg([
                "-loop", "1",
                "-framerate", str(fps),
                "-i", str(image_path),
                "-vf", filters,
                "-frames:v", str(frame_count),
                "-an",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(segment_path),
            ])

            segments.append(segment_path)

        concat_file = workdir / "concat.txt"

        concat_file.write_text(
            "".join(
                f"file '{path.as_posix()}'\n"
                for path in segments
            ),
            encoding="utf-8",
        )

        silent_video = workdir / "silent.mp4"

        run_ffmpeg([
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            "-an",
            "-movflags", "+faststart",
            str(silent_video),
        ])

        final_path = workdir / "ofss-ai-video.mp4"

        if script:
            audio_path = workdir / "narration.mp3"
            await make_tts(script, voice, audio_path)

            total_duration = len(saved_images) * seconds_per_image

            run_ffmpeg([
                "-i", str(silent_video),
                "-i", str(audio_path),
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "128k",
                "-t", str(total_duration),
                "-movflags", "+faststart",
                str(final_path),
            ])
        else:
            shutil.copy2(silent_video, final_path)

        return FileResponse(
            str(final_path),
            media_type="video/mp4",
            filename="ofss-ai-video.mp4",
            background=BackgroundTask(cleanup_folder, workdir),
        )

    except HTTPException:
        cleanup_folder(workdir)
        raise

    except Exception as exc:
        cleanup_folder(workdir)
        raise HTTPException(
            500,
            f"Video generation failed: {str(exc)[:1200]}"
        )
