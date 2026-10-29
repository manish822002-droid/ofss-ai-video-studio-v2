
import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path
from html import escape

import edge_tts
import imageio_ffmpeg

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.background import BackgroundTask


app = FastAPI(
    title="OFSS AI Video Studio",
    version="3.2.0",
    description=(
        "Create MP4 videos from images with smooth zoom, "
        "pan movement and optional AI narration."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

FPS = 25
MAX_IMAGES = 40
MAX_IMAGE_SIZE = 12 * 1024 * 1024

VOICES = {
    "en-US-AriaNeural",
    "en-US-GuyNeural",
    "en-US-JennyNeural",
    "en-US-ChristopherNeural",
    "en-GB-SoniaNeural",
    "en-GB-RyanNeural",
    "hi-IN-SwaraNeural",
    "hi-IN-MadhurNeural",
}

ASPECT_RATIOS = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
}


def run_ffmpeg(args):
    """Run FFmpeg and return a useful error if rendering fails."""
    command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
    command.extend(str(arg) for arg in args)

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=900,
    )

    if result.returncode != 0:
        message = (result.stderr or "Unknown FFmpeg error").strip()
        raise RuntimeError(message[-5000:])

    return result


def concat_escape(path):
    """Escape a path for FFmpeg concat demuxer."""
    return str(Path(path).resolve()).replace("\\", "\\\\").replace("'", "'\\''")


async def create_narration(script, voice, output_path):
    """Generate optional MP3 narration with Edge TTS."""
    communicate = edge_tts.Communicate(
        text=script,
        voice=voice,
    )
    await communicate.save(str(output_path))


@app.get("/")
async def home():
    return {
        "service": "OFSS AI Video Studio",
        "version": app.version,
        "upload_page": "/upload",
        "video_endpoint": "/api/video",
    }


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "ffmpeg": "available" if FFMPEG else "unavailable",
        "version": app.version,
    }


@app.get("/upload", response_class=HTMLResponse)
async def upload_page():
    return HTMLResponse(
        r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OFSS AI Video Studio</title>
<style>
*{box-sizing:border-box}
body{margin:0;padding:20px;background:#f2f5fa;
font-family:Arial,sans-serif;color:#172033}
main{max-width:720px;margin:20px auto;background:white;
padding:24px;border-radius:16px;box-shadow:0 5px 25px #0001}
h1{font-size:27px;margin-top:0}
p{line-height:1.6;color:#526078}
label{display:block;font-weight:700;margin:17px 0 8px}
input,select,textarea,button{width:100%;padding:12px;
border:1px solid #ccd4e0;border-radius:8px;font-size:16px}
textarea{min-height:130px;resize:vertical}
button{margin-top:22px;background:#175cd3;color:white;
border:0;font-weight:700;cursor:pointer}
button:disabled{opacity:.6;cursor:wait}
.small{font-size:13px;color:#657189}
#status{white-space:pre-wrap;margin-top:18px;font-weight:600}
</style>
</head>
<body>
<main>
<h1>OFSS AI Video Studio</h1>
<p>Upload photos to create an MP4 slideshow with smooth
camera zoom and left-right movement. Hindi or English
AI narration is optional.</p>

<form id="videoForm">
<label for="images">Select photos (1–40)</label>
<input id="images" name="images" type="file"
accept="image/jpeg,image/png,image/webp" multiple required>
<p class="small">Use JPG, PNG or WebP images. Maximum 12 MB each.</p>

<label for="aspect_ratio">Video orientation</label>
<select id="aspect_ratio" name="aspect_ratio">
<option value="16:9">Landscape (16:9)</option>
<option value="9:16">Portrait (9:16)</option>
</select>

<label for="seconds_per_image">Seconds per photo</label>
<select id="seconds_per_image" name="seconds_per_image">
<option value="4">4 seconds</option>
<option value="6">6 seconds</option>
<option value="8" selected>8 seconds</option>
<option value="10">10 seconds</option>
<option value="12">12 seconds</option>
<option value="15">15 seconds</option>
<option value="20">20 seconds</option>
<option value="30">30 seconds</option>
</select>

<label for="voice">Narration voice</label>
<select id="voice" name="voice">
<option value="en-US-AriaNeural">English — Aria (Female)</option>
<option value="en-US-GuyNeural">English — Guy (Male)</option>
<option value="en-US-JennyNeural">English — Jenny (Female)</option>
<option value="en-US-ChristopherNeural">English — Christopher (Male)</option>
<option value="en-GB-SoniaNeural">British English — Sonia (Female)</option>
<option value="en-GB-RyanNeural">British English — Ryan (Male)</option>
<option value="hi-IN-SwaraNeural">Hindi — Swara (Female)</option>
<option value="hi-IN-MadhurNeural">Hindi — Madhur (Male)</option>
</select>

<label for="script">Voice-over script (optional)</label>
<textarea id="script" name="script"
placeholder="Paste your narration script here. Leave blank for a silent video."></textarea>

<button id="submitButton" type="submit">Create MP4 Video</button>
<div id="status" role="status"></div>
</form>
</main>
<script>
const form = document.getElementById("videoForm");
const button = document.getElementById("submitButton");
const statusBox = document.getElementById("status");

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const files = document.getElementById("images").files;
  if (!files.length || files.length > 40) {
    statusBox.textContent = "Please select between 1 and 40 photos.";
    return;
  }

  for (const file of files) {
    if (file.size > 12 * 1024 * 1024) {
      statusBox.textContent = file.name + " exceeds the 12 MB limit.";
      return;
    }
  }

  button.disabled = true;
  statusBox.textContent =
    "Creating your video. Please wait; this can take several minutes.";

  try {
    const response = await fetch("/api/video", {
      method: "POST",
      body: new FormData(form)
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || ("Request failed: " + response.status));
    }

    const blob = await response.blob();
    if (!blob.size) throw new Error("The server returned an empty video.");

    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "ofss-ai-video.mp4";
    document.body.appendChild(link);
    link.click();
    link.remove();

    statusBox.textContent = "Video created. Your MP4 download should begin.";
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (error) {
    statusBox.textContent = "Could not create video:\n" + error.message;
  } finally {
    button.disabled = false;
  }
});
</script>
</body>
</html>
"""
    )


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

    if aspect_ratio not in ASPECT_RATIOS:
        raise HTTPException(
            status_code=400,
            detail="Choose 16:9 or 9:16 aspect ratio.",
        )

    if not 3 <= seconds_per_image <= 30:
        raise HTTPException(
            status_code=400,
            detail="Seconds per image must be between 3 and 30.",
        )

    if len(script) > 50000:
        raise HTTPException(
            status_code=400,
            detail="Narration script is too long (maximum 50,000 characters).",
        )

    width, height = ASPECT_RATIOS[aspect_ratio]
    frame_count = seconds_per_image * FPS
    denominator = max(frame_count - 1, 1)

    work_dir = Path(tempfile.mkdtemp(prefix="ofss_video_"))

    try:
        image_paths = []

        for index, upload in enumerate(images):
            filename = upload.filename or f"image_{index}.jpg"
            extension = Path(filename).suffix.lower()

            allowed_extensions = {".jpg", ".jpeg", ".png", ".webp"}
            allowed_types = {
                "image/jpeg",
                "image/png",
                "image/webp",
                "image/jpg",
            }

            if extension not in allowed_extensions:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported image file: {filename}",
                )

            if upload.content_type and upload.content_type not in allowed_types:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported image type: {filename}",
                )

            content = await upload.read()

            if not content:
                raise HTTPException(
                    status_code=400,
                    detail=f"Empty image file: {filename}",
                )

            if len(content) > MAX_IMAGE_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"{filename} exceeds the 12 MB limit.",
                )

            image_path = work_dir / f"input_{index:03d}{extension}"
            image_path.write_bytes(content)
            image_paths.append(image_path)

        segment_paths = []

        for index, image_path in enumerate(image_paths):
            segment_path = work_dir / f"segment_{index:03d}.mp4"

            # Alternate zoom-in and zoom-out between photos.
            zoom_in = index % 2 == 0

            # Alternate pan direction for a visible camera move.
            pan_forward = (index // 2) % 2 == 0

            if zoom_in:
                zoom_expr = (
                    f"min(1+on*0.35/{denominator},1.35)"
                )
            else:
                zoom_expr = (
                    f"max(1.35-on*0.35/{denominator},1)"
                )

            if pan_forward:
                x_expr = (
                    f"(iw-iw/zoom)*on/{denominator}"
                )
            else:
                x_expr = (
                    f"(iw-iw/zoom)*(1-on/{denominator})"
                )

            y_expr = "(ih-ih/zoom)/2"

            # Scale the image to twice the output dimensions first.
            # zoompan then crops/resizes each frame to the final size.
            filters = (
                f"scale={width * 2}:{height * 2}:"
                "force_original_aspect_ratio=increase,"
                f"crop={width * 2}:{height * 2},"
                f"zoompan=z='{zoom_expr}':"
                f"x='{x_expr}':"
                f"y='{y_expr}':"
                f"d=1:s={width}x{height}:fps={FPS},"
                f"trim=end_frame={frame_count},"
                "setpts=PTS-STARTPTS,"
                "setsar=1,format=yuv420p"
            )

            run_ffmpeg([
                "-loop", "1",
                "-framerate", str(FPS),
                "-i", str(image_path),
                "-vf", filters,
                "-frames:v", str(frame_count),
                "-an",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-r", str(FPS),
                "-movflags", "+faststart",
                str(segment_path),
            ])

            segment_paths.append(segment_path)

        concat_file = work_dir / "concat.txt"

        with concat_file.open("w", encoding="utf-8") as file:
            for segment in segment_paths:
                file.write(f"file '{concat_escape(segment)}'\n")

        silent_video = work_dir / "slideshow.mp4"

        run_ffmpeg([
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            "-movflags", "+faststart",
            str(silent_video),
        ])

        final_video = work_dir / "ofss-ai-video.mp4"

        if script.strip():
            audio_path = work_dir / "narration.mp3"

            try:
                await create_narration(
                    script.strip(),
                    voice,
                    audio_path,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Could not generate narration: {exc}"
                ) from exc

            run_ffmpeg([
                "-i", str(silent_video),
                "-i", str(audio_path),
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-af", "apad",
                "-movflags", "+faststart",
                str(final_video),
            ])
        else:
            shutil.copy2(silent_video, final_video)

        return FileResponse(
            path=str(final_video),
            media_type="video/mp4",
            filename="ofss-ai-video.mp4",
            background=BackgroundTask(
                shutil.rmtree,
                str(work_dir),
                ignore_errors=True,
            ),
        )

    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    except subprocess.TimeoutExpired:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(
            status_code=504,
            detail="Video rendering timed out. Try fewer photos or shorter duration.",
        )

    except Exception as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(
            status_code=500,
            detail=f"Video creation failed: {str(exc)}",
        ) from exc
