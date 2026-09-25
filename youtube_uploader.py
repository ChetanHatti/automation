"""
youtube_uploader.py - Upload a video to YouTube as a Short using the Data API v3.

Authentication model:
  - First run (local): Opens a browser for OAuth consent -> saves a token file.
  - Subsequent runs (CI/CD): Reuses the saved token (stored as a GitHub Secret).

The video is uploaded with "#Shorts" in the title so YouTube classifies it
as a Short automatically.
"""

import os
import json
import logging
from datetime import datetime

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  OAuth2 Authentication
# ---------------------------------------------------------------------------

def _get_authenticated_service():
    """Build and return an authenticated YouTube API service object.

    On first run, this launches an interactive OAuth flow in the browser.
    The resulting token is cached to TOKEN_PATH for headless re-use.

    For GitHub Actions (headless), you must:
      1. Run the OAuth flow once locally.
      2. Copy the contents of `token.json` into a GitHub Secret called
         YOUTUBE_OAUTH_TOKEN.
      3. The workflow writes that secret back to `token.json` before
         running main.py.
    """
    creds = None

    # -- Try loading saved token --
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    # -- Refresh or re-authenticate --
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired YouTube OAuth token ...")
            creds.refresh(Request())
        else:
            # Interactive flow - only works locally with a browser
            secrets_file = os.environ.get(
                "YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json"
            )
            if not os.path.exists(secrets_file):
                raise FileNotFoundError(
                    f"OAuth client secrets file not found: {secrets_file}\n"
                    "Download it from Google Cloud Console -> APIs & Services "
                    "-> Credentials -> OAuth 2.0 Client IDs."
                )
            logger.info("Starting interactive OAuth flow ...")
            flow = InstalledAppFlow.from_client_secrets_file(secrets_file, SCOPES)
            creds = flow.run_local_server(port=0)

        # Persist for future headless runs
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        logger.info("OAuth token saved -> %s", TOKEN_PATH)

    return build("youtube", "v3", credentials=creds)


# ---------------------------------------------------------------------------
#  Upload
# ---------------------------------------------------------------------------

def upload_to_youtube(
    video_path: str,
    fact_text: str,
    privacy: str = "public",
) -> str:
    """Upload *video_path* to YouTube as a Short.

    Args:
        video_path: Absolute path to the rendered MP4 file.
        fact_text:  The fun fact used to build title and description.
        privacy:    "public", "unlisted", or "private".

    Returns:
        The YouTube video ID of the uploaded Short.
    """
    youtube = _get_authenticated_service()

    # Build metadata
    today = datetime.utcnow().strftime("%Y-%m-%d")
    title = f"{fact_text[:70]} #Shorts #KidsFacts"
    description = (
        f"Fun Facts for Kids! - {today}\n\n"
        f"{fact_text}\n\n"
        "#Shorts #KidsFacts #FunFacts #DidYouKnow #LearningIsFun "
        "#KidsEducation #CoolFacts\n\n"
        "Amazing facts that kids will love! "
        "Learn something new every day with fun and exciting facts "
        "about animals, space, dinosaurs, the ocean, and more!"
    )

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": [
                "Shorts", "KidsFacts", "FunFacts", "DidYouKnow",
                "KidsEducation", "LearningIsFun", "CoolFacts",
                "FactsForKids", "Educational",
            ],
            "categoryId": "27",  # Education
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": True,
        },
    }

    media = MediaFileUpload(
        video_path,
        chunksize=10 * 1024 * 1024,  # 10 MB chunks
        resumable=True,
        mimetype="video/mp4",
    )

    logger.info("Uploading video to YouTube (privacy=%s) ...", privacy)

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("Upload progress: %d%%", int(status.progress() * 100))

    video_id = response["id"]
    logger.info(
        "Upload complete -> https://youtube.com/shorts/%s", video_id
    )
    return video_id
