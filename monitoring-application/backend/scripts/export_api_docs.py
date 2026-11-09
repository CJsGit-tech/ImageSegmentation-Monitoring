"""Export the API documentation to a Swagger UI HTML file and an OpenAPI JSON file.

The OpenAPI schema is embedded in the page, so no backend or database is needed to view it. Swagger UI
itself loads from the jsDelivr CDN, so viewing needs internet access. "Try it out" is turned off,
because a static page has no server to send requests to.

Run from the backend folder, or as shown in the README:
    python -m scripts.export_api_docs ../docs/api-documentation.html --openapi-json ../docs/openapi.json
"""

import argparse
import json
import os
from pathlib import Path

# Importing the app validates settings. Nothing connects or signs with these placeholders, and the
# schema doesn't contain them.
for name in (
    "DB_PASSWORD",
    "INITIAL_ADMIN_USERNAME",
    "INITIAL_ADMIN_PASSWORD",
    "INITIAL_USER_USERNAME",
    "INITIAL_USER_PASSWORD",
    "INITIAL_VIEWER_USERNAME",
    "INITIAL_VIEWER_PASSWORD",
):
    os.environ.setdefault(name, "docs-export-placeholder")
os.environ.setdefault("JWT_SECRET_KEY", "docs-export-placeholder-not-a-real-secret")

from fastapi.openapi.docs import get_swagger_ui_html  # noqa: E402

from app.main import app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="HTML file to write")
    parser.add_argument("--openapi-json", type=Path, help="OpenAPI JSON file to write")
    args = parser.parse_args()

    spec = app.openapi()
    page = get_swagger_ui_html(
        openapi_url="",  # Unused: Swagger UI ignores the URL when it is given a spec.
        title=f"{app.title} {app.version}",
        swagger_ui_parameters={"spec": spec, "supportedSubmitMethods": []},
    )
    args.output.write_bytes(page.body)
    print(f"Wrote {args.output}")
    if args.openapi_json:
        args.openapi_json.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.openapi_json}")


if __name__ == "__main__":
    main()
