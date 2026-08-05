#!/usr/bin/env python3
"""
Pytest configuration
"""
import sys
import os
from pathlib import Path

# Add the project root to the Python path
root_dir = Path(__file__).parent.parent
sys.path.insert(0, str(root_dir))

# This ensures all test files can import from src