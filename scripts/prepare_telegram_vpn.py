"""Extract a Telegram-only sing-box gateway from the local VPN configuration."""

import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.getenv("VPN_SOURCE_CONFIG", ROOT / "vpn" / "gateway.json"))
TARGET = Path(os.getenv("VPN_GENERATED_CONFIG", ROOT / "vpn" / "telegram-gateway.json"))


def main() -> None:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    upstream = next(item for item in source["outbounds"] if item.get("type") == "socks" and item.get("tag") == "proxy")
    proxy = {key: upstream[key] for key in ("type", "tag", "server", "server_port", "username", "password") if key in upstream}
    gateway = {
        "log": {"level": "warn"},
        "inbounds": [{"type": "mixed", "tag": "telegram-in", "listen": "0.0.0.0", "listen_port": 2080}],
        "outbounds": [proxy, {"type": "direct", "tag": "direct"}],
        "route": {
            "rules": [{"domain": ["api.telegram.org"], "outbound": "proxy"}],
            "final": "direct",
        },
    }
    TARGET.write_text(json.dumps(gateway, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
