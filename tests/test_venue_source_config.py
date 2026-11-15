import json

import httpx
import pytest

from src.services import venue_collector as vc


def write_config(path, items):
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def source(code="newtheatre", url="https://newtheatre.example/afisha/", parser="opera"):
    return {"code": code, "name": "Новый театр", "url": url,
            "address": "Уфа, тестовый адрес", "parser": parser}


def test_reads_default_sources_and_parser_types():
    config = vc.load_source_config()
    assert {item["code"] for item in config} == {"gkz", "bashopera", "bashgf", "nur"}
    assert {venue.code for venue in vc.venues_from_config()} == {"bashopera", "bashgf", "nur"}


def test_configured_new_source_uses_existing_html_adapter(tmp_path, monkeypatch):
    path = tmp_path / "sources.json"
    write_config(path, [source()])
    venue, = vc.venues_from_config(path)
    monkeypatch.setattr(vc, "_date", lambda *args: "2099-11-16T19:00:00")
    html = '<h3><a href="/repertoire/test/">Новая постановка</a></h3><div>Состав 16 ноября в 19:00</div>'
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=html, request=request)))
    collector = vc.VenueCollector(venue, client=client)
    monkeypatch.setitem(vc.PARSERS, venue.code, vc.PARSER_ADAPTERS[venue.parser])
    try:
        result = collector.run()
    finally:
        client.close()
    assert result.ok and result.stats["accepted"] == 1


@pytest.mark.parametrize("url", ["http://example.com/events", "https:///missing-host", "https://user:pass@example.com/"])
def test_rejects_invalid_source_url(tmp_path, url):
    path = tmp_path / "sources.json"
    write_config(path, [source(url=url)])
    with pytest.raises(ValueError, match="URL"):
        vc.load_source_config(path)


def test_empty_source_list_is_valid(tmp_path):
    path = tmp_path / "sources.json"
    write_config(path, [])
    assert vc.load_source_config(path) == []
    assert vc.venues_from_config(path) == ()


def test_unavailable_source_fails_without_exception_or_database_write(tmp_path):
    path = tmp_path / "sources.json"
    write_config(path, [source()])
    venue, = vc.venues_from_config(path)
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline", request=request))))
    try:
        result = vc.VenueCollector(venue, client=client, timeout=0.1).run()
    finally:
        client.close()
    assert not result.ok
    assert result.stats["accepted"] == 0
