"""python -m hkstock [run] [--no-news] [--no-ai] [--open]"""
import sys
import webbrowser

from . import pipeline
from .data import DOCS


def main(argv: list[str]) -> None:
    out = pipeline.run(use_news="--no-news" not in argv, use_ai="--no-ai" not in argv)
    r = out["regime"]
    print(f"\n今日型勢:{r['label']} ({r['score']:+.0f})  {r['plan']}")
    print("熱門板塊:" + ", ".join(f"{t['theme']}[{t['verdict']}]" for t in out["themes"][:3]))
    print("短炒候選:" + ", ".join(f"{p['name']}" for p in out["short"][:5]))
    print("長揸候選:" + ", ".join(f"{p['name']}" for p in out["long"][:5]))
    if "--open" in argv:
        webbrowser.open((DOCS / "index.html").as_uri())


if __name__ == "__main__":
    main(sys.argv[1:])
