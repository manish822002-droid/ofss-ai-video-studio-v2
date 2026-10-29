
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import edge_tts
import imageio_ffmpeg

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

app = FastAPI(
    title="OFSS AI Video Studio",
    version="2.0.0",
)

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
    "en-US-JennyNeural",
    "en-US-ChristopherNeural",
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
        "docs": "/docs",
    }


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "ffmpeg": "available",
    }


def run_command(command):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=900,
    )

    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or "FFmpeg failed")[-3000:]
        )

    return result


def get_audio_duration(audio_path):
    result = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-i", str(audio_path),
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
        raise RuntimeError("Could not read voice audio duration.")

    hours, minutes, seconds = match.groups()

    duration = (
        int(hours) * 3600
        + int(minutes) * 60
        + float(seconds)
    )

    if duration <= 0:
        raise RuntimeError("Generated audio has no duration.")

    return duration


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
    if not images or len(images) > 60:
        raise HTTPException(
            status_code=400,
            detail="Upload between 1 and 60 images.",
        )

    if voice not in VOICES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported voice.",
        )

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

    if len(script) > 20000:
        raise HTTPException(
            status_code=400,
            detail="Script must be under 20,000 characters.",
        )

    workdir = Path(tempfile.mkdtemp(prefix="ofss_video_"))

    try:
        valid_images = []

        for index, upload in enumerate(images):
            try:
                data = await upload.read()

                if not data:
                    continue

                if len(data) > 15 * 1024 * 1024:
                    continue

                suffix = Path(upload.filename or "").suffix.lower()

                allowed_types = {
                    "image/jpeg": ".jpg",
                    "image/png": ".png",
                    "image/webp": ".webp",
                }

                if upload.content_type not in allowed_types:
                    continue

                if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
                    suffix = allowed_types[upload.content_type]

                path = workdir / f"image_{index:03d}{suffix}"
                path.write_bytes(data)

                valid_images.append(path)

            finally:
                await upload.close()

        if not valid_images:
            raise HTTPException(
                status_code=400,
                detail="Upload valid JPG, PNG, or WebP images. "
                       "Each image must be under 15 MB.",
            )

        audio_path = workdir / "voice.mp3"
        audio_duration = 0.0

        if script.strip():
            communicate = edge_tts.Communicate(
                script.strip(),
                voice,
            )

            await communicate.save(str(audio_path))
            audio_duration = get_audio_duration(audio_path)

        total_duration = (
            audio_duration
            if audio_duration > 0
            else len(valid_images) * seconds_per_image
        )

        if total_duration <= 0:
            raise HTTPException(
                status_code=400,
                detail="Could not determine video duration.",
            )

        if aspect_ratio == "16:9":
            width, height = 1280, 720
        else:
            width, height = 720, 1280

        fps = 24
        segment_duration = total_duration / len(valid_images)
        segment_paths = []

        for index, image_path in enumerate(valid_images):
            segment = workdir / f"segment_{index:03d}.mp4"

            video_filter = (
                f"scale={width}:{height}:"
                "force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
                "zoompan="
                "z='min(zoom+0.0005,1.08)':"
                f"d=1:s={width}x{height}:fps={fps},"
                "format=yuv420p"
            )

            run_command([
                FFMPEG,
                "-y",
                "-loop", "1",
                "-framerate", str(fps),
                "-i", str(image_path),
                "-t", str(segment_duration),
                "-vf", video_filter,
                "-r", str(fps),
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-an",
                "-movflags", "+faststart",
                str(segment),
            ])

            if not segment.exists() or segment.stat().st_size == 0:
                raise RuntimeError(
                    f"Failed to create video segment {index + 1}."
                )

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
            FFMPEG,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            "-movflags", "+faststart",
            str(silent_video),
        ])

        output = workdir / "OFSS-AI-Video.mp4"

        if audio_duration > 0:
            run_command([
                FFMPEG,
                "-y",
                "-i", str(silent_video),
                "-i", str(audio_path),
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "128k",
                "-shortest",
                "-movflags", "+faststart",
                str(output),
            ])
        else:
            shutil.copy2(silent_video, output)

        if not output.exists() or output.stat().st_size == 0:
            raise RuntimeError("The MP4 output file was not created.")

        return FileResponse(
            path=str(output),
            media_type="video/mp4",
            filename="OFSS-AI-Video.mp4",
            background=BackgroundTask(
                shutil.rmtree,
                str(workdir),
                ignore_errors=True,
            ),
        )

    except HTTPException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise

    except Exception as exc:
        shutil.rmtree(workdir, ignore_errors=True)

        raise HTTPException(
            status_code=500,
            detail=f"Video generation failed: {str(exc)[:800]}",
        )
