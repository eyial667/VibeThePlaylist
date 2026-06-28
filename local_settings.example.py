"""Optional local-only settings for private packaged builds.

Copy to `local_settings.py` on your machine, fill in the shared app credentials,
and keep that file uncommitted. `config.py` will read these values when the
environment variables are absent, which makes them easy to bundle into a
private PyInstaller build without shipping a `.env` file to end users.
"""

SPOTIFY_CLIENT_ID = ""
SPOTIFY_CLIENT_SECRET = ""
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8888/callback"
LASTFM_API_KEY = ""
ANTHROPIC_API_KEY = ""

# Optional overrides for the ISRC classifier providers.
RECCOBEATS_BASE_URL = "https://api.reccobeats.com"
RECCOBEATS_API_KEY = ""
DEEZER_BASE_URL = "https://api.deezer.com"

# Optional model override for `cli.py genre-classify`.
CLASSIFIER_MODEL = "claude-haiku-4-5"
