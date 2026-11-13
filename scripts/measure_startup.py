"""Measure Compose startup until all selected services report healthy."""

import argparse
import json
import subprocess
import time


def compose(mode: str, *args: str) -> subprocess.CompletedProcess:
    files = ["-f", "docker-compose.yml"]
    if mode == "local-ai":
        files += ["-f", "compose.local-ai.yml"]
    return subprocess.run(
        ["docker", "compose", *files, *args], check=True, text=True, capture_output=True
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["local-ai", "no-ai"], default="local-ai")
    parser.add_argument("--timeout", type=int, default=480)
    args = parser.parse_args()
    services = ["db", "api", "router", "local-llm"] if args.mode == "local-ai" else ["db", "api"]
    started = time.monotonic()
    compose(args.mode, "up", "-d", "--wait", "--wait-timeout", str(args.timeout), *services)
    elapsed = time.monotonic() - started
    ids = compose(args.mode, "ps", "-q", *services).stdout.split()
    states = {}
    for container_id in ids:
        info = json.loads(subprocess.run(
            ["docker", "inspect", container_id], check=True, text=True, capture_output=True
        ).stdout)[0]
        health = info["State"].get("Health", {}).get("Status", info["State"]["Status"])
        states[info["Name"].lstrip("/")] = health
    print(json.dumps({
        "mode": args.mode,
        "seconds": round(elapsed, 2),
        "target_under_480_seconds": elapsed < 480,
        "containers": states,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
