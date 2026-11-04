from flask import Flask, render_template, request, jsonify, send_from_directory
from pathlib import Path
from typing import Any, cast
import yt_dlp
import os
import threading
import time
import re

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

active_downloads = {}

# Helper to remove those ugly terminal color codes
def clean_text(text):
    if not text: return ""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', str(text))

def format_size(value):
    if not value:
        return "Unknown size"

    try:
        size_mb = value / 1024 / 1024
        if size_mb >= 1024:
            return f"{size_mb / 1024:.2f} GB"
        return f"{size_mb:.1f} MB"
    except Exception:
        return "Unknown size"

def build_options(info):
    options = [
        {
            "label": "Best available",
            "format_id": "best",
            "size": "Auto",
            "note": "Recommended"
        },
        {
            "label": "Best video + audio",
            "format_id": "bv*+ba/best",
            "size": "Auto",
            "note": "Highest quality"
        },
        {
            "label": "Audio only",
            "format_id": "bestaudio/best",
            "size": "Auto",
            "note": "Smaller file"
        },
    ]

    seen = set()
    raw_formats = []

    for item in info.get('formats', []):
        format_id = item.get('format_id')
        if not format_id or format_id in seen:
            continue

        seen.add(format_id)

        if item.get('format_note') == 'storyboard':
            continue

        height = item.get('height')
        width = item.get('width')
        fps = item.get('fps')
        ext = item.get('ext') or 'unknown'
        vcodec = item.get('vcodec')
        acodec = item.get('acodec')
        size = format_size(item.get('filesize_approx') or item.get('filesize'))

        raw_formats.append({
            "format_id": format_id,
            "height": height or 0,
            "width": width or 0,
            "fps": fps or 0,
            "ext": ext,
            "size": size,
            "vcodec": vcodec,
            "acodec": acodec,
            "is_audio_only": vcodec == 'none' and acodec != 'none',
        })

    raw_formats.sort(key=lambda item: (item["height"], item["width"], item["fps"]), reverse=True)

    for item in raw_formats:
        if item["is_audio_only"]:
            label = f"Audio • {item['ext']}"
            details = "Audio stream"
        else:
            quality = f"{item['height']}p" if item["height"] else "Source"
            codec_parts = []
            if item["vcodec"] and item["vcodec"] != 'none':
                codec_parts.append(item["vcodec"])
            if item["acodec"] and item["acodec"] != 'none':
                codec_parts.append(item["acodec"])
            codec_text = " + ".join(codec_parts) if codec_parts else item["ext"]
            label = f"{quality} • {item['ext']}"
            details = codec_text

        options.append({
            "label": label,
            "format_id": item["format_id"],
            "size": item["size"],
            "note": details
        })

        if len(options) >= 13:
            break

    return options

def run_download(url, task_id, format_id):
    # Hook must be defined INSIDE to capture 'task_id' correctly
    def progress_hook(d):
        if d['status'] == 'downloading':
            p = clean_text(d.get('_percent_str', '0%')).replace('%','')
            speed = clean_text(d.get('_speed_str', 'N/A'))
            eta = clean_text(d.get('_eta_str', 'N/A'))
            
            active_downloads[task_id] = {
                "status": "downloading",
                "percent": p,
                "speed": speed,
                "eta": eta
            }
        elif d['status'] == 'finished':
            active_downloads[task_id] = {"status": "finished", "percent": "100", "filename": os.path.basename(d.get('filename', ''))}
    
    if format_id in {"best", "bv*+ba/best", "bestaudio/best"}:
        final_format = format_id if format_id != "best" else "bestvideo+bestaudio/best"
    else:
        final_format = f"{format_id}+bestaudio/best"

    ydl_opts = {
        "format": final_format,
        "merge_output_format": "mp4",
        "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
        "quiet": True,
        "noplaylist": True,
        "progress_hooks": [progress_hook],
    }

    try:
        with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
            ydl.download([url])
    except Exception as e:
        active_downloads[task_id] = {"status": "error", "message": str(e)}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze_video():
    url = request.json.get("url")
    if not url: return jsonify({"error": "No URL provided"}), 400

    try:
        ydl_opts = {
            "quiet": True, 
            "noplaylist": True,
        }
        
        with yt_dlp.YoutubeDL(cast(Any, ydl_opts)) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = build_options(info)
            
            return jsonify({
                "title": info.get('title'),
                "thumbnail": info.get('thumbnail'),
                "uploader": info.get('uploader') or info.get('channel') or info.get('extractor_key') or 'Unknown source',
                "duration": info.get('duration'),
                "options": formats
            })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/start-download", methods=["POST"])
def start_download():
    data = request.json
    url = data.get("url")
    format_id = data.get("format_id", "best")
    
    task_id = str(int(time.time()))
    active_downloads[task_id] = {"status": "starting", "percent": "0"}

    thread = threading.Thread(target=run_download, args=(url, task_id, format_id))
    thread.start()

    return jsonify({"task_id": task_id})

@app.route("/progress/<task_id>", methods=["GET"])
def get_progress(task_id):
    return jsonify(active_downloads.get(task_id, {"status": "unknown"}))

@app.route("/downloads/<path:filename>", methods=["GET"])
def download_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True)