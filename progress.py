"""
Progress Tracking Utilities
===========================

Functions for tracking and displaying progress of the autonomous coding agent.
"""

import json
from pathlib import Path
from typing import Dict, Any


def count_passing_tests(project_dir: Path) -> tuple[int, int]:
    """
    Count passing and total tests in feature_list.json.

    Args:
        project_dir: Root project directory (looks in .harness/ for feature_list.json)

    Returns:
        (passing_count, total_count)
    """
    # Check .harness/ first (new structure), fall back to root (legacy)
    harness_dir = project_dir / ".harness"
    tests_file = harness_dir / "feature_list.json"
    if not tests_file.exists():
        tests_file = project_dir / "feature_list.json"  # Legacy fallback

    if not tests_file.exists():
        return 0, 0

    try:
        with open(tests_file, "r") as f:
            data = json.load(f)

        # Handle both formats: flat array or {"features": [...]}
        if isinstance(data, dict) and "features" in data:
            tests = data["features"]
        elif isinstance(data, list):
            tests = data
        else:
            return 0, 0

        total = len(tests)
        passing = sum(1 for test in tests if isinstance(test, dict) and test.get("passes", False))

        return passing, total
    except (json.JSONDecodeError, IOError):
        return 0, 0


def get_test_stats(project_dir: Path) -> Dict[str, Any]:
    """
    Get detailed statistics about tests in feature_list.json.
    
    Args:
        project_dir: Root project directory (looks in .harness/ for feature_list.json)
        
    Returns:
        Dictionary with stats by category and overall
    """
    # Check .harness/ first (new structure), fall back to root (legacy)
    harness_dir = project_dir / ".harness"
    tests_file = harness_dir / "feature_list.json"
    if not tests_file.exists():
        tests_file = project_dir / "feature_list.json"  # Legacy fallback
    
    if not tests_file.exists():
        return {"total": 0, "passing": 0, "categories": {}}
    
    try:
        with open(tests_file, "r") as f:
            data = json.load(f)

        # Handle both formats: flat array or {"features": [...]}
        if isinstance(data, dict) and "features" in data:
            tests = data["features"]
        elif isinstance(data, list):
            tests = data
        else:
            return {"total": 0, "passing": 0, "categories": {}}

        # Count by category
        categories: Dict[str, Dict[str, int]] = {}
        for test in tests:
            if not isinstance(test, dict):
                continue
            cat = test.get("category", "unknown")
            if cat not in categories:
                categories[cat] = {"total": 0, "passing": 0}
            categories[cat]["total"] += 1
            if test.get("passes", False):
                categories[cat]["passing"] += 1

        total = len(tests)
        passing = sum(1 for test in tests if isinstance(test, dict) and test.get("passes", False))
        
        return {
            "total": total,
            "passing": passing,
            "categories": categories,
        }
    except (json.JSONDecodeError, IOError):
        return {"total": 0, "passing": 0, "categories": {}}


def print_detailed_progress(project_dir: Path) -> None:
    """Print detailed progress by category."""
    stats = get_test_stats(project_dir)
    
    if stats["total"] == 0:
        print("\n📊 Progress: feature_list.json not yet created")
        return
    
    total = stats["total"]
    passing = stats["passing"]
    pct = (passing / total * 100) if total > 0 else 0
    
    # Progress bar
    bar_width = 30
    filled = int(bar_width * passing / total) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_width - filled)
    
    print(f"\n📊 Overall Progress: [{bar}] {passing}/{total} ({pct:.1f}%)")
    
    # By category
    if stats["categories"]:
        print("\n   By Category:")
        for cat, cat_stats in sorted(stats["categories"].items()):
            cat_total = cat_stats["total"]
            cat_passing = cat_stats["passing"]
            cat_pct = (cat_passing / cat_total * 100) if cat_total > 0 else 0
            status = "✅" if cat_passing == cat_total else "🔄"
            print(f"   {status} {cat}: {cat_passing}/{cat_total} ({cat_pct:.0f}%)")


def print_session_header(session_num: int, is_initializer: bool) -> None:
    """Print a formatted header for the session."""
    session_type = "INITIALIZER" if is_initializer else "CODING AGENT"

    print("\n" + "=" * 70)
    print(f"  SESSION {session_num}: {session_type}")
    print("=" * 70)
    print()


def print_progress_summary(project_dir: Path) -> None:
    """Print a summary of current progress."""
    passing, total = count_passing_tests(project_dir)

    if total > 0:
        percentage = (passing / total) * 100
        print(f"\nProgress: {passing}/{total} tests passing ({percentage:.1f}%)")
    else:
        print("\nProgress: feature_list.json not yet created")
