"""The SABnzbd side: answering Sonarr's download-client calls.

Shaped by Sonarr 4's SabnzbdProxy / Sabnzbd client:
- every call is GET/POST /api?mode=...&apikey=...&output=json;
  {"status": false, "error": "..."} is how a failure is reported
- version must look like X.Y.Z (>= 0.7.0)
- get_config: sorting off and history kept, or Sonarr raises health warnings;
  a "*" category makes any category Sonarr is set to resolve
- queue slots: timeleft "H:MM:SS", integer percentage/index, a known status
- history: status Completed/Failed; storage = the job folder, whose name is
  the job name, so Sonarr finds the output path by walking up from it
"""
import time

VERSION = "4.3.3"
QUEUE_STATUS = {"Queued": "Queued", "Downloading": "Downloading"}


def error(message):
    return {"status": False, "error": message}


def config(complete_dir, categories):
    cats = [{"name": "*", "dir": "", "pp": "3", "script": "None", "priority": 0}]
    cats += [{"name": c, "dir": c, "pp": "3", "script": "None", "priority": -100}
             for c in categories]
    return {"config": {
        "misc": {
            "complete_dir": complete_dir,
            "pre_check": False,
            "enable_tv_sorting": False,
            "enable_movie_sorting": False,
            "enable_date_sorting": False,
            "tv_categories": [], "movie_categories": [], "date_categories": [],
            "history_retention": "",
            "history_retention_option": "all",
            "history_retention_number": 0,
        },
        "categories": cats,
        "servers": [],
        "sorters": [],
    }}


def _hms(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def queue_slot(job, release_size, index, now=None):
    """One queue slot. Size is the release's estimate; progress is by length."""
    now = now or time.time()
    total = job.total_seconds or 0
    frac = min(job.done_seconds / total, 1.0) if total else 0.0
    mb = max(release_size, 1024 * 1024) / (1024 * 1024)
    if job.status == "Downloading" and frac > 0 and job.started_at:
        elapsed = now - job.started_at
        left = elapsed / frac * (1 - frac)
    else:
        left = 0
    return {
        "nzo_id": job.nzo_id,
        "filename": job.name,
        "cat": job.category,
        "status": QUEUE_STATUS.get(job.status, "Downloading"),
        "mb": f"{mb:.2f}",
        "mbleft": f"{mb * (1 - frac):.2f}",
        "percentage": str(int(frac * 100)),
        "timeleft": _hms(left),
        "index": index,
        "priority": "Normal",
    }


def history_slot(job):
    took = int((job.finished_at or 0) - (job.started_at or job.added_at)) if job.finished_at else 0
    return {
        "nzo_id": job.nzo_id,
        "name": job.name,
        "nzb_name": f"{job.name}.nzb",
        "category": job.category,
        "status": job.status,
        "bytes": job.size_bytes,
        "fail_message": job.fail_message,
        "storage": job.storage,
        "download_time": max(took, 0),
    }
