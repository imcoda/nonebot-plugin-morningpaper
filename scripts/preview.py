"""Build a digest without starting NoneBot or sending messages."""

import asyncio
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nonebot_plugin_morningpaper"))

from news import collect_sources, format_digest, load_config, select_news


async def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("morning_news.yaml")
    config = load_config(path)
    results = await collect_sources(config)
    selected = select_news(results, config)
    counts = Counter(news.source.name for news in selected)
    for result in results:
        print(
            f"{result.source.name}: fresh={len(result.items)}, "
            f"selected={counts[result.source.name]}/{result.source.count}, "
            f"skipped_dates={result.skipped_dates}, error={result.error or '-'}",
            file=sys.stderr,
        )
    print(format_digest(selected, config))


if __name__ == "__main__":
    asyncio.run(main())
