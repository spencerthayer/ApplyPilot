#!/usr/bin/env python3
"""Quick demo of multi-backend support.

Run: python3 scripts/demo_backends.py
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from applypilot.apply.backends import (
    get_backend,
    detect_backends,
    get_preferred_backend,
)


def main():
    print("=" * 60)
    print("ApplyPilot Multi-Backend Support Demo")
    print("=" * 60)
    print()

    # 1. Detect available backends
    print("1. Detecting available backends...")
    available = detect_backends()
    print(f"   Found: {', '.join(available) if available else 'None'}")
    print()

    if not available:
        print("❌ No backends installed!")
        print("   Install OpenCode: curl -fsSL https://opencode.ai/install | bash")
        print("   Install Claude: https://claude.ai/code")
        return 1

    # 2. Get preferred backend
    print("2. Getting preferred backend...")
    preferred = get_preferred_backend()
    print(f"   Preferred: {preferred}")
    print()

    # 3. Initialize backend
    print("3. Initializing backend...")
    backend = get_backend(preferred)
    print(f"   Backend: {backend.name}")
    print(f"   Version: {backend.get_version()}")
    print()

    # 4. Check MCP servers
    print("4. Checking MCP servers...")
    try:
        servers = backend.list_mcp_servers()
        print(f"   Configured servers: {len(servers)}")
        for server in servers:
            print(f"     • {server}")
        print()

        # Check required servers
        required = {"playwright", "gmail"}
        present = required & set(servers)
        missing = required - set(servers)

        if missing:
            print(f"   ⚠️ Missing required servers: {', '.join(missing)}")
        else:
            print(f"   ✅ All required servers present!")

    except Exception as e:
        print(f"   ⚠️ Could not list MCP servers: {e}")

    print()
    print("=" * 60)
    print("Demo complete!")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
