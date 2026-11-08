"""Sends one API request from inside the backend container, for the browser tests.

The backend is not published to the host, so conftest.py pipes this script into the container
with `docker compose exec`. The request arrives as JSON in the first argument, and the response
goes to stdout as JSON. The body is base64 because exports are binary.
"""

import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

API_URL = "http://127.0.0.1:8000/api/v1"
TIMEOUT_SECONDS = 15


def main() -> None:
    request = json.loads(sys.argv[1])
    url = API_URL + request["path"]
    if request["params"]:
        url += "?" + urllib.parse.urlencode(request["params"])
    headers = dict(request["headers"])
    data = None
    if request["json"] is not None:
        data = json.dumps(request["json"]).encode()
        headers["Content-Type"] = "application/json"
    outgoing = urllib.request.Request(url, data=data, headers=headers, method=request["method"])
    try:
        with urllib.request.urlopen(outgoing, timeout=TIMEOUT_SECONDS) as response:
            status, answer_headers, body = response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:  # A 4xx or 5xx is an answer the tests may expect.
        status, answer_headers, body = error.code, error.headers, error.read()
    print(json.dumps({
        "status": status,
        "headers": {name.lower(): value for name, value in answer_headers.items()},
        "body": base64.b64encode(body).decode(),
    }))


if __name__ == "__main__":
    main()
