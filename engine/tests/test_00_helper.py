"""Exercise the native helper protocol before the larger document matrices."""

import json
import subprocess

import pytest

import imagekit
from docsan.image.helper import _environment


@pytest.mark.parametrize("command", ["probe", "ocr", "regions"])
def test_native_helper_returns_json(tmp_path, command):
    image = tmp_path / "synthetic.png"
    imagekit.render({
        "output": str(image), "width": 800, "height": 120,
        "items": [{"text": "DocSan synthetic sample", "x": 30, "y": 30, "size": 36}],
    })
    proc = subprocess.run(
        [str(imagekit.helper())],
        input=json.dumps({"command": command, "input": str(image)}).encode(),
        capture_output=True, timeout=180, env=_environment(), check=False,
    )
    # Only this invented fixture reaches the helper. Keep its runtime diagnostic
    # in an assertion so CI reveals failures hidden by a missing JSON response.
    diagnostic = (
        f"{command}: exit={proc.returncode}; stdout={proc.stdout[:2000]!r}; "
        f"stderr={proc.stderr[:6000]!r}"
    )
    assert proc.returncode == 0 and proc.stdout.strip(), diagnostic
    response = json.loads(proc.stdout)
    assert response.get("ok"), diagnostic
    if command == "regions":
        assert all(isinstance(response[key], list) for key in
                   ("barcodes", "code_candidates", "colour_components")), diagnostic
    else:
        assert response["width"] == 800 and response["height"] == 120
    if command == "ocr":
        text = " ".join(
            candidate["text"]
            for entry in response["passes"]
            for line in entry["lines"]
            for candidate in line["candidates"]
        )
        assert "synthetic" in text.lower(), diagnostic
