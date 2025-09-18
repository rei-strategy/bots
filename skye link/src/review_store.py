import os, json, uuid, tempfile, shutil
from typing import Dict, Any, List, Optional

REVIEW_QUEUE_FILE = os.getenv("REVIEW_QUEUE_FILE", ".review_queue.json")

def _load() -> Dict[str, Any]:
    if not os.path.exists(REVIEW_QUEUE_FILE):
        return {"items": {}}
    try:
        with open(REVIEW_QUEUE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"items": {}}

def _save(data: Dict[str, Any]) -> None:
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="rq_", suffix=".json")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=2)
        shutil.move(tmp_path, REVIEW_QUEUE_FILE)
    except Exception:
        try: os.remove(tmp_path)
        except Exception: pass

def queue_enqueue(item: Dict[str, Any]) -> str:
    data = _load()
    item_id = str(uuid.uuid4())
    item["id"] = item_id
    data["items"][item_id] = item
    _save(data)
    return item_id

def queue_get(item_id: str) -> Optional[Dict[str, Any]]:
    data = _load()
    return data["items"].get(item_id)

def queue_list() -> List[Dict[str, Any]]:
    data = _load()
    # newest first
    return sorted(data["items"].values(), key=lambda x: x.get("created_at",""), reverse=True)

def queue_update(item_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = _load()
    if item_id not in data["items"]:
        return None
    data["items"][item_id].update(updates)
    _save(data)
    return data["items"][item_id]

def queue_delete(item_id: str) -> bool:
    data = _load()
    if item_id in data["items"]:
        del data["items"][item_id]
        _save(data)
        return True
    return False

def queue_clear() -> None:
    _save({"items": {}})
