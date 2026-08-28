#!/usr/bin/env python3
"""Single place where every KHMS script learns where the base lives.

Resolution order:
  1. $KHMS_ROOT                       — set this in your shell profile
  2. the parent directory of tools/   — so a copied tools/ dir works unconfigured

Nothing else in this repository contains an absolute path.
"""
import os

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("KHMS_ROOT") or os.path.dirname(TOOLS)

MEM = os.path.join(ROOT, "memory")
KNOW = os.path.join(MEM, "know")
VIEWS = os.path.join(MEM, "views")
INBOX = os.path.join(MEM, "inbox")
STAGING = os.path.join(INBOX, ".staging")
ARCHIVE_KNOW = os.path.join(MEM, "archive", "know")
JOURNAL = os.path.join(ROOT, "journal")
TOOLS_DIR = os.path.join(ROOT, "tools")

COUNTER = os.path.join(TOOLS_DIR, ".next_id")
RECALL_LOG = os.path.join(TOOLS_DIR, ".recall.log")
INJECT_LOG = os.path.join(TOOLS_DIR, ".inject.log")
PRECHECK_LOG = os.path.join(TOOLS_DIR, ".precheck.log")
HOOK_STATE = os.path.join(TOOLS_DIR, ".hook-state")
HOOKS_OFF = os.path.join(TOOLS_DIR, ".hooks-off")
LEN_CACHE = os.path.join(TOOLS_DIR, ".cardlen.json")
GLOSSARY = os.path.join(TOOLS_DIR, "glossary.txt")
MEMORY_MD = os.path.join(ROOT, "MEMORY.md")
