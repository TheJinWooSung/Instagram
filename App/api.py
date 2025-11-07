from flask import Blueprint, request, jsonify
from app.downloader import download_reel

api_bp = Blueprint("api", __name__, url_prefix="/api")

@api_bp.route("/download", methods=["POST"])
def download_api():
    """
    Example request:
    {
        "url": "https://www.instagram.com/reel/abc123/"
    }
    """
    data = request.get_json()
    if not data or "url" not in data:
        return jsonify({"error": "Missing 'url' field"}), 400

    try:
        path = download_reel(data["url"])
        return jsonify({"status": "ok", "file_path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
