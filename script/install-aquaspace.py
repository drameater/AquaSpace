#!/usr/bin/env python3
"""Install a staged AquaSpace build; see --help for explicit restart/recovery modes."""
import sys
from aquaspace_update import main

if __name__ == '__main__':
    sys.exit(main())
