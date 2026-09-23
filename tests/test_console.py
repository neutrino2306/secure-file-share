import re

from fastapi.testclient import TestClient

from app.console import CONSOLE_PATH, csp_hash


def test_console_is_served_without_api_key(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Secure File Share Console</title>" in response.text


def test_console_sends_security_headers(client: TestClient) -> None:
    headers = client.get("/").headers

    csp = headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"


def test_csp_pins_the_exact_inline_script_and_style(client: TestClient) -> None:
    # If the hashes did not match the served blocks, browsers would block the page.
    response = client.get("/")
    csp = response.headers["content-security-policy"]
    scripts = re.findall(r"<script>(.*?)</script>", response.text, re.DOTALL)
    styles = re.findall(r"<style>(.*?)</style>", response.text, re.DOTALL)

    assert len(scripts) == 1
    assert len(styles) == 1
    assert f"script-src {csp_hash(scripts[0])}" in csp
    assert f"style-src {csp_hash(styles[0])}" in csp


def test_console_is_not_part_of_the_api_schema(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/" not in paths


def test_console_has_no_external_resources_or_unsafe_dom_apis() -> None:
    html = CONSOLE_PATH.read_text(encoding="utf-8")

    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", html)
    assert "@import" not in html
    for forbidden in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "document.cookie",
    ):
        assert forbidden not in html, f"console must not use {forbidden}"
