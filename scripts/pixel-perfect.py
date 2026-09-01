#!/usr/bin/env python3
"""Run the bundled pixel-perfect CLI.

Usage examples:
    python scripts/pixel-perfect.py inspect --reference proposals/ui.png --project-root .
    python scripts/pixel-perfect.py render --page-name dashboard --reference proposals/ui.png --iteration 0 --url http://localhost:3000
    python scripts/pixel-perfect.py crop --page-name dashboard --reference proposals/ui.png --iteration 1 --focus header --hypothesis header-boundary --iteration-note "Measure the header boundary." --section header=0,0,1440,64 --grid
    python scripts/pixel-perfect.py compare --page-name dashboard --reference proposals/ui.png --iteration 1 --focus header --candidate .artifacts/pixel-perfect/dashboard/iterations/001-header-header-boundary/candidate.png --section-only
    python scripts/pixel-perfect.py verify --page-name dashboard --reference proposals/ui.png --final --url http://localhost:3000
"""

from __future__ import annotations

from pixel_perfect.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
