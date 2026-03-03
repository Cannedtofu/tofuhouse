"""
UI inspection utilities for the MuMu emulator.

Public API
----------
dump_ui() -> str
    Dump the current screen hierarchy via ADB and return the path to the
    pretty-printed XML file.

find_search_input(xml_path) -> dict | None
    Return the node that represents the search input area (home-screen search
    pill OR the EditText on the search screen), whichever is currently visible.

find_search_button(xml_path) -> dict | None
    Return the node for the search-submit button, or None if not found
    (caller should fall back to KEYCODE_SEARCH).

Standalone usage (emulator must be running, target app open):
    python screenshot.py
"""

import os
import re
import subprocess
import xml.dom.minidom
import xml.etree.ElementTree as ET
from typing import Optional

import config

REMOTE_XML = "/sdcard/ui_dump.xml"
LOCAL_XML  = os.path.join("output", "ui_dump.xml")


# ── Dump ──────────────────────────────────────────────────────────────────────

def _pretty_print(xml_str: str) -> str:
    """Parse an XML string and return a clean indented version."""
    pretty = xml.dom.minidom.parseString(xml_str.encode("utf-8")).toprettyxml(indent="  ")
    lines = [l for l in pretty.splitlines() if l.strip()]
    return "\n".join(lines)


def dump_ui() -> str:
    """
    Capture the current UI hierarchy via ADB uiautomator dump.
    Use this only when NO Appium session is active — the UIAutomator2 server
    that Appium starts will conflict with this command (exit code 137).
    When Appium is running, use dump_ui_from_driver(driver) instead.
    """
    os.makedirs("output", exist_ok=True)
    adb = [config.MUMU_ADB_PATH, "-s", config.ADB_ID]

    subprocess.run(adb + ["shell", "uiautomator", "dump", REMOTE_XML], check=True)
    subprocess.run(adb + ["pull", REMOTE_XML, LOCAL_XML], check=True)

    raw = open(LOCAL_XML, encoding="utf-8").read()
    with open(LOCAL_XML, "w", encoding="utf-8") as f:
        f.write(_pretty_print(raw))

    return LOCAL_XML


def dump_ui_from_driver(driver) -> str:
    """
    Capture the current UI hierarchy via an active Appium session.
    Use this when Appium is running — driver.page_source uses the
    existing UIAutomator2 server and avoids the exit-code-137 conflict.
    """
    os.makedirs("output", exist_ok=True)
    with open(LOCAL_XML, "w", encoding="utf-8") as f:
        f.write(_pretty_print(driver.page_source))
    return LOCAL_XML


# ── Parse ─────────────────────────────────────────────────────────────────────

def _parse_nodes(xml_path: str) -> list[dict]:
    """
    Return a flat list of every UI element's attributes, handling both formats:
    - ADB uiautomator dump: all elements are <node> tags with a 'class' attribute
    - Appium driver.page_source: each element's tag IS its class name
    """
    tree = ET.parse(xml_path)
    # ADB format
    nodes = [dict(elem.attrib) for elem in tree.iter("node")]
    if nodes:
        return nodes
    # Appium format: iterate all elements; skip the root <hierarchy> wrapper
    return [dict(elem.attrib) for elem in tree.iter() if elem.attrib.get("resource-id") is not None or elem.attrib.get("class")]


def bounds_center(bounds: str) -> tuple[int, int]:
    """Parse '[x1,y1][x2,y2]' → centre pixel (cx, cy)."""
    nums = list(map(int, re.findall(r"\d+", bounds)))
    return (nums[0] + nums[2]) // 2, (nums[1] + nums[3]) // 2


# ── Heuristic finders ─────────────────────────────────────────────────────────

def find_search_input(xml_path: str) -> Optional[dict]:
    """
    Locate the search input on the current screen. Priority:
      1. Focused EditText            — already on the search screen
      2. Clickable EditText          — search screen, not yet focused
      3. Clickable non-image node whose resource-id contains 'search'
                                     — home-screen search pill
    Returns the matching node dict, or None.
    """
    nodes = _parse_nodes(xml_path)

    for n in nodes:
        if n.get("class") == "android.widget.EditText" and n.get("focused") == "true":
            return n

    for n in nodes:
        if n.get("class") == "android.widget.EditText" and n.get("clickable") == "true":
            return n

    for n in nodes:
        rid = n.get("resource-id", "").lower()
        cls = n.get("class", "").lower()
        if "search" in rid and n.get("clickable") == "true" and "image" not in cls:
            return n

    return None


def find_search_button(xml_path: str) -> Optional[dict]:
    """
    Locate the search-submit button on the current screen. Priority:
      1. Clickable node with text '搜索'
      2. Clickable node with content-desc '搜索'
      3. Clickable button-class node whose resource-id contains 'search'
    Returns None when not found — caller should fall back to KEYCODE_SEARCH.
    """
    nodes = _parse_nodes(xml_path)
    BUTTON_CLASSES = {
        "android.widget.Button",
        "android.widget.TextView",
        "android.widget.ImageButton",
    }

    for n in nodes:
        if n.get("text") == "搜索" and n.get("clickable") == "true":
            return n

    for n in nodes:
        if n.get("content-desc") == "搜索" and n.get("clickable") == "true":
            return n

    for n in nodes:
        rid = n.get("resource-id", "").lower()
        if "search" in rid and n.get("clickable") == "true" and n.get("class") in BUTTON_CLASSES:
            return n

    return None


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    path = dump_ui()
    print(f"UI hierarchy saved → {path}")

    inp = find_search_input(path)
    btn = find_search_button(path)

    print(f"Search input : {inp.get('resource-id') or inp.get('bounds')  if inp else 'NOT FOUND'}")
    print(f"Search button: {btn.get('resource-id') or btn.get('bounds') if btn else 'NOT FOUND (will use KEYCODE_SEARCH)'}")


if __name__ == "__main__":
    main()
