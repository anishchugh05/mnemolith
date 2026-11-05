import json
import time
import urllib.request


def get(url, timeout=5, retries=3):
    """GET a JSON document, retrying on network errors."""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return json.loads(response.read())
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
