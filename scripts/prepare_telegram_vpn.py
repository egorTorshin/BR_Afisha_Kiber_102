"""Extract a Telegram-only sing-box gateway from the local VPN configuration."""

import json
import ipaddress
import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.getenv("VPN_SOURCE_CONFIG", ROOT / "vpn" / "gateway.json"))
TARGET = Path(os.getenv("VPN_GENERATED_CONFIG", ROOT / "vpn" / "telegram-gateway.json"))


def main() -> None:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    link_path = SOURCE.parent / "vpn.txt"
    if link_path.is_file():
        link = urlsplit(link_path.read_text(encoding="utf-8-sig").strip())
        query = parse_qs(link.query)
        if link.scheme != "vless" or not link.hostname or not link.username or not link.port:
            raise ValueError("Ожидается ссылка VLESS с адресом сервера, портом и UUID")
        transport = query.get("type", ["tcp"])[0]
        if transport not in {"tcp", "raw"}:
            raise ValueError("Этот тип транспорта VLESS пока не поддерживается")
        proxy = {"type": "vless", "tag": "proxy", "server": link.hostname,
                 "server_port": link.port, "uuid": unquote(link.username)}
        if query.get("flow", [""])[0]:
            proxy["flow"] = query["flow"][0]
        security = query.get("security", ["none"])[0]
        if security in {"tls", "reality"}:
            tls = {"enabled": True, "server_name": query.get("sni", [link.hostname])[0]}
            if query.get("fp", [""])[0]:
                tls["utls"] = {"enabled": True, "fingerprint": query["fp"][0]}
            if security == "reality":
                tls["reality"] = {"enabled": True, "public_key": query.get("pbk", [""])[0],
                                  "short_id": query.get("sid", [""])[0]}
            proxy["tls"] = tls
        elif security != "none":
            raise ValueError("Неподдерживаемый режим безопасности VLESS")
        source["outbounds"] = [proxy, {"type": "direct", "tag": "direct"}]
    upstream = next(item for item in source["outbounds"] if item.get("tag") == "proxy")
    server = upstream.get("server", "")
    try:
        loopback = ipaddress.ip_address(server).is_loopback
    except ValueError:
        loopback = server.lower() == "localhost"
    if loopback:
        raise ValueError("VPN outbound proxy указывает на localhost; нужен профиль с удалённым сервером")
    outbounds = source["outbounds"]
    if not any(item.get("tag") == "direct" for item in outbounds):
        outbounds = [*outbounds, {"type": "direct", "tag": "direct"}]
    gateway = {
        "log": {"level": "warn"},
        "inbounds": [{"type": "mixed", "tag": "telegram-in", "listen": "0.0.0.0", "listen_port": 2080}],
        "outbounds": outbounds,
        "route": {
            "rules": [{"domain": ["api.telegram.org"], "outbound": "proxy"}],
            "final": "direct",
        },
    }
    TARGET.write_text(json.dumps(gateway, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
