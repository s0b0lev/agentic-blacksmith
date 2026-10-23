from pathlib import Path

import pytest

from blacksmith.preview import PLACEHOLDERS, preview_host, render_env

JIM_EXAMPLE = Path("/path/to/sample-shop/ops/.env.prod.example")


def _lines(out: str) -> dict[str, str]:
    result = {}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        result[key] = value
    return result


def test_host_with_zone():
    assert preview_host(123, "1.2.3.4", "preview.sample-shop.ru") == "pr-123.preview.sample-shop.ru"


@pytest.mark.parametrize("zone", [None, ""])
def test_host_without_zone(zone):
    assert preview_host(123, "1.2.3.4", zone) == "pr-123-1-2-3-4.nip.io"


def test_placeholder_replaced():
    out = render_env("POSTGRES_PASSWORD=change-me\nMEILI_MASTER_KEY=\n", {}, "h", rand=lambda: "R")
    assert _lines(out) == {"POSTGRES_PASSWORD": "'R'", "MEILI_MASTER_KEY": "'R'"}


def test_literal_values_quoted():
    out = render_env("KEY=a$b 'c' #d\n", {}, "h", rand=lambda: "R")
    assert out.strip() == "KEY='a$b '\\''c'\\'' #d'"


def test_comments_and_blank_lines_dropped():
    out = render_env("# c\n\nA=1\n", {}, "h")
    assert out.strip() == "A='1'"


def test_quoted_example_values_unwrapped():
    out = render_env("A=\"x y\"\nB='change-me'\n", {}, "h", rand=lambda: "R")
    assert _lines(out) == {"A": "'x y'", "B": "'R'"}


def test_override_expands_host():
    out = render_env(
        "MINIO_PUBLIC_URL=http://localhost\n",
        {"MINIO_PUBLIC_URL": "https://${PREVIEW_HOST}/media/product-images"},
        "pr-1.x",
    )
    assert "https://pr-1.x/media/product-images" in out
    assert out.count("MINIO_PUBLIC_URL") == 1


def test_example_values_not_host_expanded():
    out = render_env("A=${PREVIEW_HOST}\n", {}, "pr-1.x")
    assert out.strip() == "A='${PREVIEW_HOST}'"


def test_override_appends_missing_key():
    out = render_env("A=1\n", {"B": "2", "A": "9"}, "h")
    assert out.splitlines() == ["A='9'", "B='2'"]


def test_jim_scale_example_renders():
    if not JIM_EXAMPLE.exists():
        pytest.skip("sample-shop example not available")
    example = JIM_EXAMPLE.read_text()
    keys = [
        line.split("=", 1)[0]
        for line in example.splitlines()
        if line.strip() and not line.startswith("#") and "=" in line
    ]
    rendered = _lines(render_env(example, {}, "pr-1.x"))
    for key in keys:
        assert key in rendered
        assert rendered[key].strip("'") not in PLACEHOLDERS
    assert rendered["POSTGRES_PASSWORD"].strip("'") != "change-me"
