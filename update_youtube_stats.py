"""Generate the static YouTube data consumed by GitHub Pages."""
import json
from pathlib import Path

from server import get_youtube_stats


OUTPUT_FILE = Path(__file__).with_name("youtube-stats.json")


def main():
    payload = get_youtube_stats()
    incomplete = [
        item.get("handle", "desconhecido")
        for item in payload.get("items", [])
        if not item.get("channel", {}).get("avatar")
        or not item.get("channel", {}).get("subscriberText")
        or not item.get("video", {}).get("viewText")
    ]
    if len(payload.get("items", [])) != 5 or incomplete:
        raise RuntimeError("Dados incompletos do YouTube: " + ", ".join(incomplete))

    OUTPUT_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Dados de {len(payload['items'])} canais gravados em {OUTPUT_FILE.name}")


if __name__ == "__main__":
    main()
