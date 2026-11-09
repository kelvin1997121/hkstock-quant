"""python -m hkstock [--market hk|us|all|auto] [--no-news] [--no-ai] [--open]

--market auto (used by the scheduled job) runs each market only on its weekdays
between 08:00 and 18:30 local exchange time; the default runs both.
"""
import sys
import webbrowser

from . import data, pipeline
from .data import DOCS
from .universe import MARKETS


def main(argv: list[str]) -> None:
    which = argv[argv.index("--market") + 1] if "--market" in argv else "all"
    if which == "auto":
        markets = [m for m in MARKETS.values() if data.is_active(m)]
        if not markets:
            print("no market is active now; nothing to do")
    elif which == "all":
        markets = list(MARKETS.values())
    else:
        markets = [MARKETS[which]]
    failed = []
    for m in markets:
        try:
            out = pipeline.run(m, use_news="--no-news" not in argv, use_ai="--no-ai" not in argv)
        except Exception as e:  # one market failing must not stop the other
            print(f"!! {m.name} failed: {e!r}")
            failed.append(m.key)
            continue
        r = out["regime"]
        print(f"\n[{m.name}] 今日型勢:{r['label']} ({r['score']:+.0f})  {r['plan']}")
        print("熱門板塊:" + ", ".join(f"{t['theme']}[{t['verdict']}]" for t in out["themes"][:3]))
        print("短炒候選:" + ", ".join(f"{p['name']}" for p in out["short"][:5]))
        print("長揸候選:" + ", ".join(f"{p['name']}" for p in out["long"][:5]) + "\n")
    if "--open" in argv:
        webbrowser.open((DOCS / "index.html").as_uri())
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
