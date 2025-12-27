from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from pydantic import BaseModel, HttpUrl
from typing import List, Optional, Dict
import os
import uuid
import asyncio
from pathlib import Path
import yt_dlp
import aiofiles
from datetime import datetime
import json

app = FastAPI(title="GIF Generator API")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directories
UPLOAD_DIR = Path("uploads")
VIDEO_DIR = Path("videos")
GIF_DIR = Path("gifs")
UPLOAD_DIR.mkdir(exist_ok=True)
VIDEO_DIR.mkdir(exist_ok=True)
GIF_DIR.mkdir(exist_ok=True)

# Processing state
processing_state: Dict[str, Dict] = {}


def check_ffmpeg_available():
    """Check if FFmpeg is installed and available"""
    import subprocess
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5
        )
        if result.returncode != 0:
            return False, "FFmpeg returned non-zero exit code"
        return True, "FFmpeg is available"
    except FileNotFoundError:
        return False, "FFmpeg not found in PATH. Please install FFmpeg."
    except subprocess.TimeoutExpired:
        return False, "FFmpeg check timed out"
    except Exception as e:
        return False, f"Error checking FFmpeg: {str(e)}"


# Check FFmpeg on startup
ffmpeg_available, ffmpeg_message = check_ffmpeg_available()
if not ffmpeg_available:
    print(f"WARNING: {ffmpeg_message}")
    print("GIF generation will fail without FFmpeg installed.")


class YouTubeDownloadRequest(BaseModel):
    url: str


class CropRegion(BaseModel):
    x: float
    y: float
    width: float
    height: float
    unit: str = "pixels"  # "pixels" or "percent"


class GIFConfig(BaseModel):
    filename: str
    start_time: float
    duration: float
    fps: int = 15
    scale: Optional[int] = None
    crop: CropRegion
    
    class Config:
        # Allow validation of nested models
        validate_assignment = True


class ProcessGIFRequest(BaseModel):
    video_id: str
    gif_configs: List[GIFConfig]


class GridCropRequest(BaseModel):
    video_id: str
    rows: int
    columns: int
    gif_configs: List[GIFConfig]


class LineCropRequest(BaseModel):
    video_id: str
    horizontal_lines: List[float]  # positions in pixels or percent
    vertical_lines: List[float]
    line_unit: str = "pixels"  # "pixels" or "percent"
    gif_configs: List[GIFConfig]


@app.get("/")
async def root():
    return {"message": "GIF Generator API"}


@app.get("/api/health")
async def health_check():
    """Health check endpoint with FFmpeg status"""
    return {
        "status": "healthy",
        "ffmpeg_available": ffmpeg_available,
        "ffmpeg_message": ffmpeg_message
    }


@app.post("/api/download-youtube")
async def download_youtube(request: YouTubeDownloadRequest, background_tasks: BackgroundTasks):
    """Download video from YouTube URL"""
    video_id = str(uuid.uuid4())
    video_path = VIDEO_DIR / f"{video_id}.mp4"
    
    # Initialize state
    processing_state[video_id] = {
        "status": "downloading",
        "progress": 0,
        "video_path": str(video_path),
        "error": None
    }
    
    def progress_hook(d):
        if d['status'] == 'downloading':
            if 'total_bytes' in d:
                percent = (d['downloaded_bytes'] / d['total_bytes']) * 100
            elif 'total_bytes_estimate' in d:
                percent = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
            else:
                percent = 0
            processing_state[video_id]["progress"] = min(percent, 99)
        elif d['status'] == 'finished':
            processing_state[video_id]["progress"] = 100
            processing_state[video_id]["status"] = "ready"
    
    def download_video():
        try:
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': str(video_path),
                'progress_hooks': [progress_hook],
                'merge_output_format': 'mp4',
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([request.url])
            
            processing_state[video_id]["status"] = "ready"
            processing_state[video_id]["progress"] = 100
        except Exception as e:
            processing_state[video_id]["status"] = "error"
            processing_state[video_id]["error"] = str(e)
    
    background_tasks.add_task(download_video)
    
    return {"video_id": video_id, "status": "downloading"}


@app.post("/api/upload-video")
async def upload_video(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    """Upload local video file"""
    # Validate file type
    allowed_extensions = {'.mp4', '.mov', '.webm'}
    file_ext = Path(file.filename).suffix.lower()
    
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Invalid file type. Allowed: {', '.join(allowed_extensions)}")
    
    video_id = str(uuid.uuid4())
    video_path = VIDEO_DIR / f"{video_id}{file_ext}"
    
    # Initialize state
    processing_state[video_id] = {
        "status": "uploading",
        "progress": 0,
        "video_path": str(video_path),
        "error": None
    }
    
    async def save_file():
        try:
            total_size = 0
            async with aiofiles.open(video_path, 'wb') as f:
                while True:
                    chunk = await file.read(8192)
                    if not chunk:
                        break
                    await f.write(chunk)
                    total_size += len(chunk)
                    # Update progress (simplified - would need file size header for accurate progress)
                    if hasattr(file, 'size') and file.size:
                        processing_state[video_id]["progress"] = min((total_size / file.size) * 100, 99)
            
            processing_state[video_id]["status"] = "ready"
            processing_state[video_id]["progress"] = 100
        except Exception as e:
            processing_state[video_id]["status"] = "error"
            processing_state[video_id]["error"] = str(e)
    
    if background_tasks:
        background_tasks.add_task(save_file)
    else:
        await save_file()
    
    return {"video_id": video_id, "status": "uploading"}


@app.get("/api/video-status/{video_id}")
async def get_video_status(video_id: str):
    """Get video download/upload status"""
    if video_id not in processing_state:
        raise HTTPException(status_code=404, detail="Video not found")
    
    state = processing_state[video_id]
    return {
        "status": state["status"],
        "progress": state.get("progress", 0),
        "error": state.get("error"),
        "video_path": state.get("video_path")
    }


@app.get("/api/video/{video_id}")
async def get_video(video_id: str):
    """Stream video file"""
    if video_id not in processing_state:
        raise HTTPException(status_code=404, detail="Video not found")
    
    video_path = Path(processing_state[video_id]["video_path"])
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    
    return FileResponse(
        video_path,
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes"}
    )


@app.post("/api/process-gifs")
async def process_gifs(request: ProcessGIFRequest, background_tasks: BackgroundTasks):
    """Process multiple GIFs from video with crop regions"""
    if request.video_id not in processing_state:
        raise HTTPException(status_code=404, detail="Video not found")
    
    video_path = Path(processing_state[request.video_id]["video_path"])
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    
    # Validate configs
    if not request.gif_configs:
        raise HTTPException(status_code=400, detail="No GIF configurations provided")
    
    for i, config in enumerate(request.gif_configs):
        if config.duration <= 0:
            raise HTTPException(status_code=400, detail=f"GIF {i+1}: Duration must be greater than 0")
        if config.start_time < 0:
            raise HTTPException(status_code=400, detail=f"GIF {i+1}: Start time cannot be negative")
        if config.fps < 1 or config.fps > 60:
            raise HTTPException(status_code=400, detail=f"GIF {i+1}: FPS must be between 1 and 60")
        if config.scale is not None and config.scale < 1:
            raise HTTPException(status_code=400, detail=f"GIF {i+1}: Scale must be greater than 0")
    
    # Initialize GIF processing states
    gif_tasks = {}
    for i, config in enumerate(request.gif_configs):
        gif_id = str(uuid.uuid4())
        gif_tasks[gif_id] = {
            "gif_id": gif_id,
            "filename": config.filename,
            "status": "waiting",
            "progress": 0,
            "error": None
        }
    
    processing_state[request.video_id]["gif_tasks"] = gif_tasks
    
    # Process GIFs - BackgroundTasks handles async functions correctly
    background_tasks.add_task(process_gifs_background, request, gif_tasks)
    
    return {"gif_tasks": list(gif_tasks.keys())}


async def process_gifs_background(request: ProcessGIFRequest, gif_tasks: Dict):
    """Background task to process GIFs sequentially"""
    video_path = Path(processing_state[request.video_id]["video_path"])
    
    if not video_path.exists():
        for gif_id in gif_tasks.keys():
            gif_tasks[gif_id]["status"] = "failed"
            gif_tasks[gif_id]["error"] = "Video file not found"
        return
    
    # Process GIFs sequentially to avoid resource exhaustion
    for gif_id, config in zip(gif_tasks.keys(), request.gif_configs):
        try:
            gif_tasks[gif_id]["status"] = "processing"
            gif_tasks[gif_id]["progress"] = 0
            
            # Generate GIF using FFmpeg
            gif_path = await generate_gif(
                video_path,
                config,
                GIF_DIR / f"{gif_id}.gif",
                gif_tasks[gif_id]
            )
            
            gif_tasks[gif_id]["status"] = "completed"
            gif_tasks[gif_id]["progress"] = 100
            gif_tasks[gif_id]["gif_path"] = str(gif_path)
        except Exception as e:
            gif_tasks[gif_id]["status"] = "failed"
            gif_tasks[gif_id]["error"] = str(e)
            gif_tasks[gif_id]["progress"] = 0
            # Log error for debugging
            print(f"Error generating GIF {gif_id}: {str(e)}")


async def generate_gif(video_path: Path, config: GIFConfig, output_path: Path, progress_state: Dict):
    """Generate GIF from video using FFmpeg with optimized two-pass palette method"""
    import subprocess
    
    # Check FFmpeg availability
    if not ffmpeg_available:
        raise Exception("FFmpeg is not installed or not available in PATH")
    
    # Get video dimensions
    probe_cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", str(video_path)
    ]
    
    try:
        result = subprocess.run(
            probe_cmd, 
            capture_output=True, 
            text=True, 
            timeout=10,
            check=True
        )
        video_info = json.loads(result.stdout)
        if not video_info.get("streams"):
            raise Exception("Could not get video stream information")
        video_width = video_info["streams"][0]["width"]
        video_height = video_info["streams"][0]["height"]
        
        # Get video duration
        duration_cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "format=duration",
            "-of", "json", str(video_path)
        ]
        duration_result = subprocess.run(
            duration_cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=True
        )
        duration_info = json.loads(duration_result.stdout)
        video_duration = float(duration_info.get("format", {}).get("duration", 0))
        
        # Validate start time and duration
        if config.start_time >= video_duration:
            raise Exception(f"Start time ({config.start_time}s) exceeds video duration ({video_duration:.2f}s)")
        if config.start_time + config.duration > video_duration:
            # Adjust duration to fit within video
            adjusted_duration = video_duration - config.start_time
            raise Exception(f"Duration ({config.duration}s) exceeds remaining video length. Maximum duration: {adjusted_duration:.2f}s")
    except subprocess.TimeoutExpired:
        raise Exception("FFprobe timeout - video file may be corrupted")
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode('utf-8', errors='ignore') if isinstance(e.stderr, bytes) else str(e.stderr)
        raise Exception(f"FFprobe error: {error_msg}")
    except (json.JSONDecodeError, KeyError, IndexError, ValueError) as e:
        raise Exception(f"Failed to parse video info: {str(e)}")
    
    # Calculate crop coordinates
    if config.crop.unit == "percent":
        x = int((config.crop.x / 100) * video_width)
        y = int((config.crop.y / 100) * video_height)
        width = int((config.crop.width / 100) * video_width)
        height = int((config.crop.height / 100) * video_height)
    else:
        x = int(config.crop.x)
        y = int(config.crop.y)
        width = int(config.crop.width)
        height = int(config.crop.height)
    
    # Validate crop coordinates
    x = max(0, min(x, video_width - 1))
    y = max(0, min(y, video_height - 1))
    width = max(1, min(width, video_width - x))
    height = max(1, min(height, video_height - y))
    
    # Ensure width and height are even (required by some codecs)
    width = width - (width % 2)
    height = height - (height % 2)
    
    if width <= 0 or height <= 0:
        raise Exception(f"Invalid crop dimensions: {width}x{height}")
    
    progress_state["progress"] = 10
    
    # Build filter chain
    filters = [f"crop={width}:{height}:{x}:{y}"]
    
    # Add scale if specified
    if config.scale:
        filters.append(f"scale={config.scale}:-1:flags=lanczos")
    
    # Add FPS
    filters.append(f"fps={config.fps}")
    
    filter_chain = ",".join(filters)
    
    # Use two-pass palette method for better quality and smaller file size
    palette_path = output_path.with_suffix('.png')
    
    # First pass: Generate palette
    palette_cmd = [
        "ffmpeg", "-y",
        "-ss", str(config.start_time),
        "-t", str(config.duration),
        "-i", str(video_path),
        "-vf", f"{filter_chain},palettegen=max_colors=256",
        str(palette_path)
    ]
    
    progress_state["progress"] = 30
    
    try:
        process = await asyncio.create_subprocess_exec(
            *palette_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
        
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8', errors='ignore') if stderr else "Unknown error"
            raise Exception(f"Palette generation failed: {error_msg}")
    except asyncio.TimeoutError:
        raise Exception("Palette generation timeout - video segment may be too long")
    except Exception as e:
        raise Exception(f"Palette generation error: {str(e)}")
    
    progress_state["progress"] = 60
    
    # Second pass: Generate GIF using palette
    gif_cmd = [
        "ffmpeg", "-y",
        "-ss", str(config.start_time),
        "-t", str(config.duration),
        "-i", str(video_path),
        "-i", str(palette_path),
        "-lavfi", f"{filter_chain}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5",
        "-loop", "0",
        str(output_path)
    ]
    
    try:
        process = await asyncio.create_subprocess_exec(
            *gif_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
        
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8', errors='ignore') if stderr else "Unknown error"
            # Clean up palette file on error
            if palette_path.exists():
                palette_path.unlink()
            raise Exception(f"GIF generation failed: {error_msg}")
    except asyncio.TimeoutError:
        if palette_path.exists():
            palette_path.unlink()
        raise Exception("GIF generation timeout - try reducing duration or FPS")
    except Exception as e:
        if palette_path.exists():
            palette_path.unlink()
        raise Exception(f"GIF generation error: {str(e)}")
    
    # Clean up palette file
    if palette_path.exists():
        try:
            palette_path.unlink()
        except:
            pass
    
    # Verify output file exists and is not empty
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise Exception("Generated GIF file is empty or missing")
    
    progress_state["progress"] = 100
    return output_path


@app.post("/api/grid-crop")
async def grid_crop(request: GridCropRequest, background_tasks: BackgroundTasks):
    """Generate crop regions from grid"""
    if request.video_id not in processing_state:
        raise HTTPException(status_code=404, detail="Video not found")
    
    video_path = Path(processing_state[request.video_id]["video_path"])
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    
    # Get video dimensions
    import subprocess
    import json
    
    probe_cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", str(video_path)
    ]
    
    result = subprocess.run(probe_cmd, capture_output=True, text=True)
    video_info = json.loads(result.stdout)
    video_width = video_info["streams"][0]["width"]
    video_height = video_info["streams"][0]["height"]
    
    # Calculate grid regions
    cell_width = video_width / request.columns
    cell_height = video_height / request.rows
    
    crop_regions = []
    for row in range(request.rows):
        for col in range(request.columns):
            crop_regions.append({
                "x": col * cell_width,
                "y": row * cell_height,
                "width": cell_width,
                "height": cell_height,
                "unit": "pixels"
            })
    
    # Create GIF configs with crop regions
    gif_configs = []
    for i, region in enumerate(crop_regions):
        row = i // request.columns
        col = i % request.columns
        if i < len(request.gif_configs):
            config_data = request.gif_configs[i]
            if isinstance(config_data, dict):
                config = GIFConfig(**config_data)
            else:
                config = config_data
            config.crop = CropRegion(**region)
            gif_configs.append(config)
        else:
            # Default config for extra regions
            gif_configs.append(GIFConfig(
                filename=f"grid_{row}_{col}.gif",
                start_time=0,
                duration=5,
                fps=15,
                crop=CropRegion(**region)
            ))
    
    # Process GIFs
    process_request = ProcessGIFRequest(
        video_id=request.video_id,
        gif_configs=gif_configs
    )
    
    return await process_gifs(process_request, background_tasks)


@app.post("/api/line-crop")
async def line_crop(request: LineCropRequest, background_tasks: BackgroundTasks):
    """Generate crop regions from lines"""
    if request.video_id not in processing_state:
        raise HTTPException(status_code=404, detail="Video not found")
    
    video_path = Path(processing_state[request.video_id]["video_path"])
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    
    # Get video dimensions
    import subprocess
    import json
    
    probe_cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", str(video_path)
    ]
    
    result = subprocess.run(probe_cmd, capture_output=True, text=True)
    video_info = json.loads(result.stdout)
    video_width = video_info["streams"][0]["width"]
    video_height = video_info["streams"][0]["height"]
    
    # Convert lines to pixels if needed
    h_lines = request.horizontal_lines.copy()
    v_lines = request.vertical_lines.copy()
    
    if request.line_unit == "percent":
        h_lines = [l * video_height / 100 for l in h_lines]
        v_lines = [l * video_width / 100 for l in v_lines]
    
    # Add boundaries
    h_lines = [0] + sorted(h_lines) + [video_height]
    v_lines = [0] + sorted(v_lines) + [video_width]
    
    # Generate crop regions
    crop_regions = []
    for i in range(len(h_lines) - 1):
        for j in range(len(v_lines) - 1):
            crop_regions.append({
                "x": v_lines[j],
                "y": h_lines[i],
                "width": v_lines[j + 1] - v_lines[j],
                "height": h_lines[i + 1] - h_lines[i],
                "unit": "pixels"
            })
    
    # Create GIF configs with crop regions
    gif_configs = []
    v_regions = len(v_lines) - 1
    for i, region in enumerate(crop_regions):
        if i < len(request.gif_configs):
            config_data = request.gif_configs[i]
            if isinstance(config_data, dict):
                config = GIFConfig(**config_data)
            else:
                config = config_data
            config.crop = CropRegion(**region)
            gif_configs.append(config)
        else:
            h_idx = i // v_regions
            v_idx = i % v_regions
            gif_configs.append(GIFConfig(
                filename=f"line_{h_idx}_{v_idx}.gif",
                start_time=0,
                duration=5,
                fps=15,
                crop=CropRegion(**region)
            ))
    
    # Process GIFs
    process_request = ProcessGIFRequest(
        video_id=request.video_id,
        gif_configs=gif_configs
    )
    
    return await process_gifs(process_request, background_tasks)


@app.get("/api/gif-status/{video_id}")
async def get_gif_status(video_id: str):
    """Get GIF processing status"""
    if video_id not in processing_state:
        raise HTTPException(status_code=404, detail="Video not found")
    
    gif_tasks = processing_state[video_id].get("gif_tasks", {})
    return {"gif_tasks": gif_tasks}


@app.get("/api/gif/{gif_id}")
async def get_gif(gif_id: str):
    """Download generated GIF"""
    # Find GIF in all video states
    for video_id, state in processing_state.items():
        gif_tasks = state.get("gif_tasks", {})
        if gif_id in gif_tasks:
            gif_path = Path(gif_tasks[gif_id].get("gif_path"))
            if gif_path and gif_path.exists():
                return FileResponse(
                    gif_path,
                    media_type="image/gif",
                    filename=gif_tasks[gif_id]["filename"]
                )
    
    raise HTTPException(status_code=404, detail="GIF not found")


@app.delete("/api/clear-gifs/{video_id}")
async def clear_gifs(video_id: str):
    """Clear all GIFs for a video"""
    if video_id not in processing_state:
        raise HTTPException(status_code=404, detail="Video not found")
    
    # Get all GIF tasks
    gif_tasks = processing_state[video_id].get("gif_tasks", {})
    
    # Delete GIF files from disk
    deleted_count = 0
    for gif_id, task in gif_tasks.items():
        gif_path = task.get("gif_path")
        if gif_path:
            try:
                path = Path(gif_path)
                if path.exists():
                    path.unlink()
                    deleted_count += 1
            except Exception as e:
                print(f"Error deleting GIF file {gif_path}: {str(e)}")
    
    # Clear GIF tasks from state
    processing_state[video_id]["gif_tasks"] = {}
    
    return {
        "message": "GIFs cleared successfully",
        "deleted_files": deleted_count,
        "total_tasks": len(gif_tasks)
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

