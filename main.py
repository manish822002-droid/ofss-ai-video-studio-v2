
import os
import re
import shutil
import subprocess
import tempfile
import asyncio
from pathlib import Path

import edge_tts
import imageio_ffmpeg
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

app = FastAPI(title="OFSS AI Video Studio", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

VOICES = [
    "en-US-AriaNeural",
    "en-US-GuyNeural",
    "en-GB-SoniaNeural",
    "en-GB-RyanNeural",
    "hi-IN-SwaraNeural",
    "hi-IN-MadhurNeural",
]


@app.get("/")
def home():
    return {
        "service": "OFSS AI Video Studio",
        "status": "ok",
        "endpoint": "/api/video",
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "ffmpeg": "available"}


def run_command(command):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2500:])
    return result


def get_audio_duration(audio_path):
    result = run_command([
        FFMPEG, "-i", str(audio_path),
        "-f", "null", "-"
    ])
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
                      result.stderr)
    if not match:
        # FFmpeg normally reports duration in stderr.
        raise RuntimeError("Could not read audio duration.")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


@app.post("/api/video")
async def create_video(
    images: list[UploadFile] = File(...),
    script: str = Form(""),
    voice: str = Form("en-US-AriaNeural"),
    aspect_ratio: str = Form("16:9"),
    seconds_per_image: int = Form(8),
):
    if not images or len(images) > 60:
        raise HTTPException(400, "Upload between 1 and 60 images.")

    if voice not in VOICES:
        raise HTTPException(400, "Unsupported voice.")

    if aspect_ratio not in ("16:9", "9:16"):
        raise HTTPException(400, "Choose 16:9 or 9:16.")

    if not 3 <= seconds_per_image <= 30:
        raise HTTPException(400, "Seconds per image must be 3–30.")

    if len(script) > 20000:
        raise HTTPException(400, "Script must be under 20,000 characters.")

    workdir = Path(tempfile.mkdtemp(prefix="ofss_video_"))

    try:
        valid_images = []

        for index, upload in enumerate(images):
            if upload.content_type not in (
                "image/jpeg", "image/png", "image/webp"
            ):
                continue

            data = await upload.read()
            if not data or len(data) > 15 * 1024 * 1024:
                continue

            suffix = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
            }[upload.content_type]

            path = workdir / f"image_{index:03d}{suffix}"
            path.write_bytes(data)
            valid_images.append(path)

        if not valid_images:
            raise HTTPException(
                400, "Upload valid JPG, PNG, or WebP images."
            )

        audio_path = workdir / "voice.mp3"
        audio_duration = 0

        if script.strip():
            communicate = edge_tts.Communicate(
                script.strip(), voice
            )
            await communicate.save(str(audio_path))
            audio_duration = get_audio_duration(audio_path)

        total_duration = (
            audio_duration if audio_duration > 0
            else len(valid_images) * seconds_per_image
        )

        width, height = (
            (1280, 720) if aspect_ratio == "16:9"
            else (720, 1280)
        )
        fps = 24
        segment_duration = total_duration / len(valid_images)
        segment_paths = []

        for index, image_path in enumerate(valid_images):
            segment = workdir / f"segment_{index:03d}.mp4"
            frames = max(1, round(segment_duration * fps))

            video_filter = (
                f"scale={width}:{height}:"
                "force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
                "zoompan=z='min(zoom+0.0005,1.08)':"
                f"d=1:s={width}x{height}:fps={fps},"
                "format=yuv420p"
            )

            run_command([
                FFMPEG, "-y",
                "-loop", "1", "-i", str(image_path),
                "-t", str(segment_duration),
                "-vf", video_filter,
                "-r", str(fps),
                "-frames:v", str(frames),
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-an",
                str(segment),
            ])
            segment_paths.append(segment)

        concat_file = workdir / "segments.txt"
        concat_file.write_text(
            "".join(
                f"file '{path.as_posix()}'\n"
                for path in segment_paths
            ),
            encoding="utf-8",
        )

        silent_video = workdir / "silent.mp4"
        run_command([
            FFMPEG, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(silent_video),
        ])

        output = workdir / "ofss_video.mp4"

        if audio_duration > 0:
            run_command([
                FFMPEG, "-y",
                "-i", str(silent_video),
                "-i", str(audio_path),
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "128k",
                "-t", str(total_duration),
                "-movflags", "+faststart",
                str(output),
            ])
        else:
            run_command([
                FFMPEG, "-y",
                "-i", str(silent_video),
                "-c", "copy",
                "-movflags", "+faststart",
                str(output),
            ])

        return FileResponse(
            str(output),
            media_type="video/mp4",
            filename="OFSS-AI-Video.mp4",
            background=BackgroundTask(
                shutil.rmtree, str(workdir), ignore_errors=True
            ),
        )

    except HTTPException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(
            500, f"Video generation failed: {str(exc)[:800]}"
        )
