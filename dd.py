"""
Droid Tycoon companion bot (discord.py 2.x)

Setup:
  1. pip install -U discord.py python-dotenv
  2. Create a .env file next to this file:
       DISCORD_TOKEN=your-bot-token-here
       GUILD_ID=optional-server-id-for-instant-slash-command-sync
       ADMIN_ROLE_ID=optional-role-id-for-admin-commands
  3. python droid_bot.py
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = os.getenv("GUILD_ID")
ADMIN_ROLE_ID = os.getenv("ADMIN_ROLE_ID")
DB_PATH = os.getenv("DB_PATH", "droid_bot.db")
ALERT_CHANNEL_ID = os.getenv("ALERT_CHANNEL_ID")  # optional: spawn/mission pings

# ──────────────────────────────────────────────────────────────────────────────
# Constants and reference data
# ──────────────────────────────────────────────────────────────────────────────

RARITY_COLORS = {
    "Common": 0x7BE0B0, "Rare": 0x7EEEFF, "Epic": 0xB06CF0,
    "Legendary": 0xEE8F33, "Mythic": 0xE8336F, "Iconic": 0xF6D34D,
}
VARIANTS = ["Default", "Gold", "Diamond", "Rainbow", "Beskar", "Galactic", "Stellar", "Kyber"]
VARIANT_COLORS = {
    "Default": 0xA3A3A3, "Gold": 0xF6D34D, "Diamond": 0x42DFF5, "Rainbow": 0xF275D8,
    "Beskar": 0xC7D2E3, "Galactic": 0xB78CFF, "Stellar": 0xF2B84D, "Kyber": 0xE5E7EB,
}
MISSIONS = ["Storm Trooper", "Droids", "Sarlac Fishing", "Asteroids"]
MISSION_RUN_SECONDS = 5 * 60
MISSION_CYCLE_MINUTES = 35
MISSION_EPOCH_UTC_MINUTE = 15  # first mission starts at :15 UTC each hour

SPAWN_SCHEDULES = {
    "Kyber Spawn": {"interval_min": 60, "offset_min": 15, "color": 0xC7D2E3},
    "Mythic Spawn": {"interval_min": 60, "offset_min": 55, "color": 0xF275B5},
    "Stellar Spawn": {"interval_min": 30, "offset_min": 5, "color": 0xFFE599},
}

# Upgrade chart (editable per user in the app; these are the shipped defaults).
# Keys are rarity, values are the chip cost to reach each tier.
UPGRADE_TIERS = ["Gold", "Diamond", "Rainbow", "Beskar", "Galactic", "Stellar", "Kyber"]
DEFAULT_UPGRADE_CHART: dict[str, dict[str, int]] = {
    # Fill this in from your app's defaultUpgradeChart. Placeholder values below.
    "Common":    {"Gold": 40, "Diamond": 90, "Rainbow": 180, "Beskar": 320, "Galactic": 520, "Stellar": 800, "Kyber": 240},
    "Rare":      {"Gold": 80, "Diamond": 180, "Rainbow": 350, "Beskar": 600, "Galactic": 950, "Stellar": 1400, "Kyber": 1000},
    "Epic":      {"Gold": 160, "Diamond": 360, "Rainbow": 700, "Beskar": 1200, "Galactic": 1900, "Stellar": 2800, "Kyber": 2000},
    "Legendary": {"Gold": 320, "Diamond": 720, "Rainbow": 1400, "Beskar": 2400, "Galactic": 3800, "Stellar": 5600, "Kyber": 4000},
    "Mythic":    {"Gold": 640, "Diamond": 1440, "Rainbow": 2800, "Beskar": 4800, "Galactic": 7600, "Stellar": 11200, "Kyber": 8000},
}

# Fusion recipes: name -> (rarity, [ingredients]). Add the full list from fusionData.ts.
FUSION_RECIPES = {
    "Kyber Mythic": ("Mythic", ["Kyber Legendary", "Kyber Legendary", "Kyber Legendary"]),
    "Kyber Legendary": ("Legendary", ["Kyber Epic", "Kyber Epic", "Kyber Epic"]),
    "Kyber Epic": ("Epic", ["Kyber Rare", "Kyber Rare", "Kyber Rare"]),
}

# Rebirth data: copy `rebirthPaths` from your rebirthData.ts into REBIRTH_PATHS.
# Shape: list of 6 cycles, each a list of 40 steps, each step a list of 3 droids
# as (variant, name, rarity, sell) where sell is "sell", "keep21", or None.
REBIRTH_COSTS = [
    "10K", "150K", "975K", "2.95M", "5.35M", "9.85M", "14.5M", "36M", "89M", "220M",
    "550M", "1.36B", "3.4B", "8.45B", "21B", "52B", "130B", "325B", "810B", "2T",
    "3T", "4.5T", "6T", "9T", "13.5T", "21T", "32T", "45T", "68T", "100T",
    "150T", "230T", "345T", "520T", "778T", "1.2Q", "2.5Q", "4.5Q", "8Q", "15Q",
]
# (rb, srb, coins%, xp%) by rebirth number. Matches rebirthRewards in the app.
REBIRTH_REWARDS: dict[int, tuple[int, int, int, int]] = {
    12: (0, 11, 22, 110), 13: (0, 16, 32, 160), 14: (0, 22, 44, 220), 15: (0, 29, 58, 290),
    16: (0, 37, 74, 370), 17: (0, 46, 92, 460), 18: (0, 56, 112, 560), 19: (0, 67, 134, 670),
    20: (5, 79, 158, 790), 21: (10, 92, 184, 920), 22: (15, 106, 212, 1060), 23: (20, 121, 242, 1210),
    24: (25, 137, 274, 1370), 25: (40, 154, 308, 1540), 26: (50, 172, 344, 1720), 27: (60, 191, 382, 1910),
    28: (70, 211, 422, 2110), 29: (80, 232, 464, 2320), 30: (120, 254, 508, 2540), 31: (140, 277, 554, 2770),
    32: (160, 301, 602, 3010), 33: (180, 326, 652, 3260), 34: (200, 352, 704, 3520), 35: (300, 379, 758, 3790),
    36: (300, 407, 814, 4070), 37: (300, 436, 872, 4360), 38: (300, 466, 932, 4660), 39: (300, 497, 994, 4970),
    40: (300, 529, 1058, 5290),
}
# ──────────────────────────────────────────────────────────────────────────────
# Super rebirth paths: 6 cycles × 40 rebirths × 3 droids, from rebirthData.ts
# Format per line: Variant|Name|S, Variant|Name|S, Variant|Name|S
# S: Y = sell, R = sell but needed again at rebirth 21+, - = keep
# ──────────────────────────────────────────────────────────────────────────────

DROID_RARITY: dict[str, str] = {
    "PIT": "Common", "DRK-1 PROBE": "Common", "CB": "Common", "MOUSE": "Common", "GONK": "Common",
    "ID10": "Common", "R3": "Common", "R5": "Common", "R8": "Common", "B1 BATTLE": "Common",
    "IMPERIAL PRO": "Common", "BAL-CORE": "Rare", "BDX EXPLORER": "Rare", "2BB": "Rare", "B-U4D": "Rare",
    "A-LT": "Rare", "R9": "Rare", "ARG": "Rare", "B1 SECURITY": "Rare", "HOV-R": "Rare", "ROLL-R": "Rare",
    "SENATE HOVER": "Rare", "NAV-EX": "Rare", "VECT-ARM": "Rare", "R4": "Rare", "EG-58": "Rare",
    "GROUNDMECH": "Epic", "BB": "Epic", "UTIL-TEC": "Epic", "LO": "Epic", "R6": "Epic", "TRAK-R": "Epic",
    "STRIKE-ORB": "Epic", "HAUL-R": "Epic", "AMP WALKER": "Epic", "B1 HEAVY": "Epic", "ORB-WALKER": "Epic",
    "GUNRUNNER": "Epic", "B2 SUPER": "Epic", "R2": "Epic", "B2 HEAVY": "Epic", "OPTI-POD": "Epic",
    "SEN-TRI": "Epic", "LNG-SHOT": "Epic", "EGL": "Epic", "KT": "Epic", "MPH": "Epic",
    "BB9": "Legendary", "MONO-WLKR": "Legendary", "MECHA-DROID": "Legendary", "PROTO-ROLLER": "Legendary",
    "B2-RP": "Legendary", "CYCLO-GRAV": "Legendary", "R7": "Legendary", "OPTI-STRK": "Legendary",
    "JO9-4MN": "Legendary", "PLNK": "Legendary",
    "MO-TRAK": "Mythic", "IG": "Mythic", "DRFT-R": "Mythic", "CYCLENS": "Mythic", "LOADLIFTER": "Mythic",
    "RIC-1200": "Mythic", "KX": "Mythic", "TRI-TEK": "Mythic", "SNOW MOUSE": "Mythic", "RIC": "Mythic",
    "LEP": "Mythic", "ECG": "Mythic",
}

SELL_CODES = {"Y": "sell", "R": "keep21", "-": None}

RAW_REBIRTH_PATHS: list[str] = [
    # ── Cycle 1 ──
    """
    Base|PIT|Y, Base|DRK-1 PROBE|Y, Base|CB|Y
    Base|BAL-CORE|Y, Base|BDX EXPLORER|R, Base|2BB|R
    Base|B-U4D|-, Base|A-LT|-, Gold|R9|-
    Gold|ARG|-, Gold|B1 SECURITY|-, Base|GROUNDMECH|-
    Gold|B-U4D|-, Gold|HOV-R|-, Diamond|R9|Y
    Diamond|ARG|Y, Diamond|A-LT|R, Gold|GROUNDMECH|-
    Diamond|B-U4D|Y, Diamond|B1 SECURITY|Y, Gold|BB|R
    Diamond|HOV-R|Y, Gold|LO|-, Gold|UTIL-TEC|Y
    Gold|R6|R, Gold|TRAK-R|Y, Rainbow|GROUNDMECH|R
    Gold|STRIKE-ORB|Y, Rainbow|LO|Y, Rainbow|HAUL-R|Y
    Rainbow|AMP WALKER|R, Rainbow|B1 HEAVY|R, Base|BB9|-
    Base|MONO-WLKR|-, Base|MECHA-DROID|-, Gold|PROTO-ROLLER|-
    Base|B2-RP|-, Base|CYCLO-GRAV|-, Base|R7|-
    Base|OPTI-STRK|-, Gold|MECHA-DROID|-, Gold|MONO-WLKR|-
    Gold|B2-RP|-, Gold|R7|-, Gold|BB9|-
    Gold|OPTI-STRK|-, Diamond|PROTO-ROLLER|-, Diamond|MONO-WLKR|-
    Diamond|MECHA-DROID|-, Diamond|CYCLO-GRAV|-, Diamond|B2-RP|-
    Diamond|R7|-, Diamond|BB9|R, Rainbow|MONO-WLKR|R
    Rainbow|PROTO-ROLLER|R, Rainbow|CYCLO-GRAV|R, Rainbow|B2-RP|R
    Rainbow|MECHA-DROID|R, Rainbow|OPTI-STRK|R, Rainbow|R7|R
    Beskar|GROUNDMECH|-, Beskar|ORB-WALKER|-, Beskar|BB|-
    Beskar|AMP WALKER|Y, Beskar|B1 HEAVY|-, Beskar|PROTO-ROLLER|-
    Beskar|MONO-WLKR|-, Beskar|OPTI-STRK|Y, Beskar|R7|-
    Beskar|CYCLO-GRAV|-, Beskar|BB9|-, Base|MO-TRAK|-
    Beskar|B2-RP|-, Base|IG|-, Gold|DRFT-R|-
    Gold|CYCLENS|-, Diamond|LOADLIFTER|-, Rainbow|RIC-1200|Y
    Diamond|KX|-, Rainbow|TRI-TEK|Y, Beskar|SNOW MOUSE|-
    Galactic|PROTO-ROLLER|-, Rainbow|MO-TRAK|Y, Beskar|DRFT-R|-
    Galactic|MECHA-DROID|Y, Galactic|MONO-WLKR|Y, Beskar|IG|-
    Galactic|B2-RP|Y, Beskar|CYCLENS|-, Galactic|LOADLIFTER|Y
    Stellar|SEN-TRI|Y, Beskar|PROTO-ROLLER|-, Beskar|KX|Y
    Galactic|OPTI-POD|Y, Galactic|ORB-WALKER|Y, Beskar|RIC|Y
    Stellar|B1 HEAVY|-, Galactic|CYCLO-GRAV|Y, Galactic|DRFT-R|-
    Stellar|GROUNDMECH|-, Stellar|BB|-, Galactic|CYCLENS|-
    Stellar|BB9|-, Stellar|SNOW MOUSE|-, Stellar|IG|-
    Kyber|BDX EXPLORER|Y, Kyber|A-LT|Y, Kyber|2BB|Y
    Kyber|GROUNDMECH|Y, Kyber|B1 HEAVY|Y, Kyber|BB|Y
    Kyber|R6|Y, Kyber|R2|Y, Kyber|BB9|Y
    Kyber|PROTO-ROLLER|Y, Kyber|R7|Y, Kyber|IG|Y
    Kyber|SNOW MOUSE|Y, Kyber|CYCLENS|Y, Kyber|DRFT-R|Y
    """,
    # ── Cycle 2 ──
    """
    Base|MOUSE|Y, Base|GONK|Y, Base|ID10|Y
    Base|ROLL-R|-, Base|SENATE HOVER|Y, Base|NAV-EX|-
    Base|BDX EXPLORER|-, Base|VECT-ARM|-, Base|R4|-
    Base|BAL-CORE|-, Base|2BB|-, Base|ORB-WALKER|-
    Gold|VECT-ARM|-, Gold|NAV-EX|-, Gold|R4|-
    Diamond|BAL-CORE|-, Diamond|2BB|Y, Base|GUNRUNNER|-
    Diamond|ROLL-R|Y, Diamond|BDX EXPLORER|Y, Gold|R2|-
    Diamond|R4|R, Gold|GUNRUNNER|R, Gold|B2 SUPER|-
    Rainbow|NAV-EX|Y, Gold|AMP WALKER|-, Gold|STRIKE-ORB|-
    Rainbow|VECT-ARM|Y, Diamond|B2 SUPER|-, Diamond|R2|-
    Rainbow|BAL-CORE|Y, Diamond|B2 HEAVY|-, Diamond|STRIKE-ORB|-
    Rainbow|R2|R, Rainbow|ORB-WALKER|R, Base|BB9|-
    Rainbow|B2 SUPER|R, Base|PROTO-ROLLER|-, Base|MECHA-DROID|-
    Rainbow|B2 HEAVY|R, Base|B2-RP|-, Gold|R7|-
    Rainbow|STRIKE-ORB|R, Gold|PROTO-ROLLER|-, Gold|BB9|-
    Rainbow|AMP WALKER|Y, Gold|MECHA-DROID|-, Diamond|B2-RP|-
    Rainbow|OPTI-POD|Y, Gold|MONO-WLKR|-, Diamond|R7|-
    Rainbow|UTIL-TEC|Y, Diamond|PROTO-ROLLER|R, Diamond|BB9|R
    Diamond|MECHA-DROID|R, Rainbow|B2-RP|R, Rainbow|R7|R
    Rainbow|MONO-WLKR|R, Rainbow|CYCLO-GRAV|R, Rainbow|OPTI-STRK|R
    Beskar|LO|-, Beskar|HAUL-R|Y, Beskar|R6|-
    Beskar|SEN-TRI|Y, Beskar|STRIKE-ORB|Y, Beskar|PROTO-ROLLER|Y
    Beskar|B2-RP|-, Beskar|CYCLO-GRAV|-, Beskar|BB9|Y
    Beskar|B2-RP|-, Beskar|OPTI-STRK|-, Base|SNOW MOUSE|-
    Beskar|MONO-WLKR|Y, Base|RIC-1200|Y, Gold|TRI-TEK|-
    Gold|KX|-, Diamond|DRFT-R|-, Rainbow|IG|Y
    Diamond|LEP|-, Rainbow|LOADLIFTER|-, Beskar|MO-TRAK|Y
    Galactic|MECHA-DROID|-, Rainbow|SNOW MOUSE|Y, Beskar|TRI-TEK|Y
    Galactic|CYCLO-GRAV|-, Galactic|R7|-, Beskar|RIC|-
    Galactic|OPTI-STRK|-, Beskar|KX|-, Galactic|DRFT-R|-
    Stellar|B2 SUPER|-, Beskar|B2-RP|-, Beskar|LOADLIFTER|-
    Galactic|GUNRUNNER|Y, Galactic|B1 HEAVY|Y, Beskar|LEP|-
    Stellar|R2|Y, Galactic|OPTI-STRK|Y, Galactic|KX|-
    Stellar|LO|-, Stellar|R6|Y, Galactic|RIC|-
    Stellar|R7|Y, Stellar|CYCLENS|Y, Stellar|DRFT-R|Y
    Kyber|B1 SECURITY|Y, Kyber|R9|Y, Kyber|R4|Y
    Kyber|LO|Y, Kyber|ORB-WALKER|Y, Kyber|TRAK-R|Y
    Kyber|B2 HEAVY|Y, Kyber|B2 SUPER|Y, Kyber|B2-RP|Y
    Kyber|MECHA-DROID|Y, Kyber|CYCLO-GRAV|Y, Kyber|KX|Y
    Kyber|LEP|Y, Kyber|LOADLIFTER|Y, Kyber|RIC|Y
    """,
    # ── Cycle 3 ──
    """
    Base|GONK|Y, Base|PIT|Y, Base|MOUSE|Y
    Base|R3|-, Base|SENATE HOVER|-, Base|2BB|-
    Base|R5|-, Base|R8|-, Base|R4|-
    Gold|B1 BATTLE|-, Gold|B1 SECURITY|-, Gold|R9|-
    Gold|R3|-, Gold|SENATE HOVER|-, Gold|2BB|-
    Diamond|R5|-, Diamond|BDX EXPLORER|-, Diamond|R4|-
    Diamond|B1 BATTLE|Y, Diamond|R8|Y, Diamond|R9|Y
    Rainbow|R3|Y, Rainbow|B1 SECURITY|Y, Rainbow|2BB|Y
    Rainbow|R5|Y, Rainbow|BDX EXPLORER|Y, Rainbow|R4|Y
    Rainbow|SENATE HOVER|R, Base|GROUNDMECH|-, Base|TRAK-R|-
    Base|B2 SUPER|-, Base|B2 HEAVY|-, Base|UTIL-TEC|-
    Rainbow|BAL-CORE|Y, Gold|GROUNDMECH|R, Gold|TRAK-R|R
    Rainbow|B2 SUPER|R, Base|PROTO-ROLLER|-, Base|MECHA-DROID|-
    Rainbow|B2 HEAVY|R, Base|B2-RP|-, Gold|R7|-
    Rainbow|STRIKE-ORB|R, Gold|PROTO-ROLLER|-, Gold|BB9|-
    Rainbow|AMP WALKER|R, Gold|MECHA-DROID|-, Diamond|B2-RP|-
    Rainbow|OPTI-POD|R, Gold|MONO-WLKR|-, Diamond|R7|-
    Rainbow|UTIL-TEC|R, Diamond|PROTO-ROLLER|R, Diamond|BB9|R
    Diamond|MECHA-DROID|R, Rainbow|B2-RP|R, Rainbow|R7|R
    Rainbow|MONO-WLKR|R, Rainbow|CYCLO-GRAV|R, Rainbow|OPTI-STRK|R
    Beskar|OPTI-POD|Y, Beskar|B2 SUPER|-, Beskar|R2|Y
    Beskar|GUNRUNNER|Y, Beskar|LNG-SHOT|-, Beskar|B2-RP|-
    Beskar|MECHA-DROID|-, Beskar|MONO-WLKR|-, Beskar|CYCLO-GRAV|Y
    Beskar|B2-RP|Y, Beskar|BB9|-, Base|RIC|-
    Beskar|PROTO-ROLLER|-, Base|LOADLIFTER|-, Gold|MO-TRAK|-
    Gold|LEP|-, Diamond|TRI-TEK|-, Rainbow|SNOW MOUSE|Y
    Diamond|RIC-1200|-, Rainbow|IG|-, Beskar|DRFT-R|-
    Galactic|BB9|-, Rainbow|RIC|-, Beskar|MO-TRAK|-
    Galactic|MECHA-DROID|-, Galactic|OPTI-STRK|-, Beskar|IG|-
    Galactic|R7|Y, Beskar|LEP|-, Galactic|DRFT-R|Y
    Stellar|B2 HEAVY|Y, Beskar|MECHA-DROID|Y, Beskar|RIC-1200|-
    Galactic|GROUNDMECH|Y, Galactic|BB|Y, Beskar|MO-TRAK|-
    Stellar|TRAK-R|Y, Galactic|MONO-WLKR|-, Galactic|LOADLIFTER|Y
    Stellar|B2 SUPER|Y, Stellar|ORB-WALKER|Y, Galactic|LEP|Y
    Stellar|PROTO-ROLLER|Y, Stellar|RIC|Y, Stellar|KX|Y
    Kyber|B-U4D|Y, Kyber|SENATE HOVER|Y, Kyber|ARG|Y
    Kyber|AMP WALKER|Y, Kyber|STRIKE-ORB|Y, Kyber|UTIL-TEC|Y
    Kyber|LNG-SHOT|Y, Kyber|HAUL-R|Y, Kyber|OPTI-STRK|Y
    Kyber|MONO-WLKR|Y, Kyber|BB9|Y, Kyber|RIC-1200|Y
    Kyber|IG|Y, Kyber|TRI-TEK|Y, Kyber|MO-TRAK|Y
    """,
    # ── Cycle 4 ──
    """
    Base|PIT|Y, Base|DRK-1 PROBE|Y, Base|ID10|Y
    Base|R3|-, Base|SENATE HOVER|-, Base|2BB|-
    Gold|R8|-, Gold|R5|-, Base|R4|-
    Gold|B1 BATTLE|-, Gold|B1 SECURITY|-, Gold|R9|-
    Gold|R3|-, Gold|SENATE HOVER|-, Gold|2BB|-
    Diamond|R5|-, Diamond|BDX EXPLORER|-, Diamond|R4|-
    Diamond|B1 BATTLE|Y, Diamond|R8|Y, Diamond|R9|Y
    Rainbow|R3|Y, Rainbow|B1 SECURITY|Y, Rainbow|2BB|Y
    Rainbow|R5|Y, Rainbow|BDX EXPLORER|Y, Rainbow|R4|Y
    Rainbow|SENATE HOVER|Y, Base|GROUNDMECH|-, Base|TRAK-R|-
    Base|B2 SUPER|-, Base|B2 HEAVY|-, Base|UTIL-TEC|-
    Rainbow|BAL-CORE|-, Gold|GROUNDMECH|-, Gold|TRAK-R|-
    Rainbow|B2 SUPER|-, Base|PROTO-ROLLER|R, Base|MECHA-DROID|-
    Diamond|BAL-CORE|R, Diamond|GROUNDMECH|R, Rainbow|TRAK-R|R
    Diamond|B2 HEAVY|R, Rainbow|B2 SUPER|R, Base|B2-RP|-
    Rainbow|UTIL-TEC|Y, Base|BB9|-, Gold|R7|-
    Base|OPTI-STRK|-, Gold|MECHA-DROID|-, Gold|CYCLO-GRAV|-
    Gold|B2-RP|-, Gold|BB9|R, Diamond|R7|-
    Diamond|MECHA-DROID|R, Rainbow|B2-RP|R, Rainbow|R7|R
    Rainbow|MONO-WLKR|R, Rainbow|CYCLO-GRAV|R, Rainbow|OPTI-STRK|R
    Beskar|GROUNDMECH|-, Beskar|AMP WALKER|-, Beskar|HAUL-R|Y
    Beskar|GUNRUNNER|-, Beskar|B2 SUPER|Y, Beskar|STRIKE-ORB|-
    Beskar|MONO-WLKR|-, Beskar|B2-RP|-, Beskar|CYCLO-GRAV|-
    Beskar|PROTO-ROLLER|-, Beskar|MECHA-DROID|Y, Base|MO-TRAK|-
    Beskar|OPTI-STRK|-, Base|TRI-TEK|-, Gold|DRFT-R|-
    Gold|CYCLENS|-, Diamond|LEP|-, Rainbow|MO-TRAK|-
    Diamond|RIC-1200|-, Rainbow|SNOW MOUSE|-, Beskar|LOADLIFTER|-
    Galactic|OPTI-STRK|Y, Rainbow|IG|-, Beskar|KX|-
    Galactic|BB9|-, Galactic|R7|-, Beskar|TRI-TEK|-
    Galactic|MONO-WLKR|Y, Beskar|CYCLENS|-, Galactic|IG|-
    Stellar|TRAK-R|Y, Beskar|CYCLO-GRAV|Y, Beskar|TRI-TEK|Y
    Galactic|R6|Y, Galactic|R2|Y, Beskar|IG|Y
    Stellar|B2 HEAVY|Y, Galactic|BB9|Y, Galactic|RIC-1200|Y
    Stellar|AMP WALKER|Y, Stellar|STRIKE-ORB|Y, Galactic|MO-TRAK|Y
    Stellar|B2-RP|-, Stellar|LEP|Y, Stellar|LOADLIFTER|Y
    Kyber|BAL-CORE|Y, Kyber|VECT-ARM|Y, Kyber|NAV-EX|Y
    Kyber|GUNRUNNER|Y, Kyber|OPTI-POD|Y, Kyber|SEN-TRI|Y
    Kyber|GROUNDMECH|Y, Kyber|B1 HEAVY|Y, Kyber|R7|Y
    Kyber|PROTO-ROLLER|Y, Kyber|B2-RP|Y, Kyber|SNOW MOUSE|Y
    Kyber|KX|Y, Kyber|CYCLENS|Y, Kyber|DRFT-R|Y
    """,
    # ── Cycle 5 ──
    """
    Base|GONK|Y, Base|MOUSE|R, Base|ID10|Y
    Gold|IMPERIAL PRO|-, Base|ROLL-R|-, Base|2BB|-
    Base|VECT-ARM|Y, Base|R4|-, Gold|BDX EXPLORER|-
    Gold|B1 BATTLE|-, Gold|B1 SECURITY|Y, Gold|R9|Y
    Gold|R3|Y, Gold|BAL-CORE|-, Gold|R4|Y
    Diamond|BDX EXPLORER|Y, Diamond|2BB|Y, Base|GUNRUNNER|R
    Diamond|R5|Y, Diamond|ROLL-R|R, Gold|R2|-
    Diamond|B1 BATTLE|Y, Diamond|R8|Y, Gold|B2 SUPER|-
    Rainbow|NAV-EX|Y, Gold|AMP WALKER|R, Gold|STRIKE-ORB|R
    Beskar|IMPERIAL PRO|Y, Base|GROUNDMECH|-, Base|TRAK-R|-
    Base|B2 SUPER|-, Base|B2 HEAVY|-, Base|UTIL-TEC|-
    Rainbow|BAL-CORE|Y, Gold|GROUNDMECH|-, Gold|TRAK-R|-
    Gold|B2 HEAVY|-, Gold|B2 SUPER|-, Diamond|R2|R
    Diamond|GROUNDMECH|Y, Diamond|TRAK-R|R, Rainbow|UTIL-TEC|R
    Diamond|B2 SUPER|Y, Diamond|B2 HEAVY|Y, Base|B2-RP|-
    Base|BB9|-, Gold|PROTO-ROLLER|Y, Gold|R7|-
    Base|OPTI-STRK|-, Gold|MECHA-DROID|-, Gold|CYCLO-GRAV|-
    Gold|B2-RP|-, Gold|BB9|-, Diamond|R7|-
    Diamond|CYCLO-GRAV|R, Diamond|OPTI-STRK|R, Rainbow|MECHA-DROID|R
    Rainbow|B2-RP|R, Rainbow|R7|R, Rainbow|BB9|R
    Beskar|LO|-, Beskar|HAUL-R|-, Beskar|STRIKE-ORB|Y
    Beskar|GUNRUNNER|Y, Beskar|SEN-TRI|Y, Beskar|R6|-
    Beskar|B2-RP|Y, Beskar|CYCLO-GRAV|-, Beskar|BB9|-
    Beskar|MONO-WLKR|Y, Beskar|OPTI-STRK|-, Base|MO-TRAK|-
    Beskar|MECHA-DROID|-, Base|RIC|-, Gold|TRI-TEK|-
    Gold|CYCLENS|-, Diamond|LEP|-, Rainbow|SNOW MOUSE|-
    Diamond|RIC-1200|-, Rainbow|IG|-, Beskar|LOADLIFTER|-
    Galactic|BB9|Y, Rainbow|RIC|-, Beskar|MO-TRAK|-
    Galactic|MECHA-DROID|-, Galactic|OPTI-STRK|-, Beskar|IG|-
    Galactic|R7|-, Beskar|LEP|-, Galactic|CYCLENS|Y
    Stellar|AMP WALKER|Y, Beskar|OPTI-STRK|-, Beskar|SNOW MOUSE|Y
    Galactic|LO|-, Galactic|TRAK-R|-, Beskar|DRFT-R|Y
    Stellar|UTIL-TEC|Y, Galactic|R7|Y, Galactic|TRI-TEK|Y
    Stellar|LNG-SHOT|Y, Stellar|HAUL-R|Y, Galactic|IG|Y
    Stellar|MECHA-DROID|-, Stellar|RIC-1200|-, Stellar|MO-TRAK|Y
    Kyber|MOUSE|Y, Kyber|ROLL-R|Y, Kyber|HOV-R|Y
    Kyber|R6|Y, Kyber|R2|Y, Kyber|BB|Y
    Kyber|LO|Y, Kyber|TRAK-R|Y, Kyber|MECHA-DROID|Y
    Kyber|OPTI-STRK|Y, Kyber|CYCLO-GRAV|Y, Kyber|RIC|Y
    Kyber|RIC-1200|Y, Kyber|LEP|Y, Kyber|LOADLIFTER|Y
    """,
    # ── Cycle 6 ──
    """
    Base|MOUSE|Y, Base|GONK|-, Base|ID10|Y
    Base|PIT|Y, Base|EG-58|-, Base|2BB|-
    Base|R3|Y, Base|ROLL-R|R, Base|R4|-
    Gold|EG-58|-, Gold|B1 SECURITY|-, Gold|R9|-
    Gold|GONK|-, Gold|R5|Y, Gold|VECT-ARM|Y
    Diamond|EG-58|R, Diamond|2BB|Y, Base|EGL|-
    Diamond|R9|Y, Diamond|R4|Y, Base|KT|-
    Diamond|NAV-EX|Y, Diamond|B1 SECURITY|Y, Base|MPH|-
    Diamond|GONK|-, Gold|EGL|-, Gold|STRIKE-ORB|Y
    Base|AMP WALKER|Y, Base|GROUNDMECH|-, Base|TRAK-R|-
    Base|HAUL-R|R, Base|B2 SUPER|-, Base|R2|R
    Rainbow|GONK|R, Gold|GROUNDMECH|R, Gold|TRAK-R|R
    Gold|KT|-, Gold|MPH|-, Base|PROTO-ROLLER|-
    Gold|B2 SUPER|R, Diamond|EGL|-, Base|B2-RP|-
    Diamond|KT|-, Diamond|MPH|-, Base|JO9-4MN|-
    Base|PLNK|-, Base|BB9|R, Gold|R7|-
    Rainbow|EGL|R, Base|MECHA-DROID|-, Gold|JO9-4MN|-
    Rainbow|KT|R, Rainbow|MPH|R, Gold|PLNK|-
    Gold|PROTO-ROLLER|R, Diamond|JO9-4MN|R, Diamond|R7|R
    Rainbow|MECHA-DROID|Y, Rainbow|PLNK|R, Rainbow|B2-RP|R
    Beskar|GONK|-, Beskar|EGL|-, Beskar|HAUL-R|-
    Beskar|GROUNDMECH|Y, Beskar|KT|-, Beskar|R2|-
    Beskar|MPH|-, Beskar|JO9-4MN|-, Beskar|BB9|-
    Beskar|PROTO-ROLLER|Y, Beskar|PLNK|-, Base|ECG|-
    Beskar|R7|Y, Base|LOADLIFTER|Y, Base|IG|-
    Gold|ECG|-, Gold|MO-TRAK|Y, Diamond|DRFT-R|-
    Rainbow|SNOW MOUSE|-, Rainbow|CYCLENS|-, Beskar|TRI-TEK|-
    Galactic|EGL|-, Galactic|JO9-4MN|-, Galactic|BB9|Y
    Galactic|KT|-, Galactic|PLNK|-, Beskar|IG|-
    Galactic|MPH|-, Galactic|B2-RP|Y, Galactic|DRFT-R|-
    Beskar|EG-58|-, Stellar|EGL|-, Beskar|SNOW MOUSE|-
    Galactic|TRAK-R|Y, Beskar|CYCLENS|Y, Galactic|ECG|-
    Stellar|KT|-, Galactic|CYCLO-GRAV|-, Galactic|TRI-TEK|-
    Stellar|HAUL-R|Y, Stellar|MPH|-, Galactic|IG|Y
    Stellar|PLNK|-, Stellar|JO9-4MN|-, Stellar|ECG|-
    Kyber|GONK|Y, Kyber|ROLL-R|Y, Kyber|EG-58|Y
    Kyber|EGL|Y, Kyber|B2 SUPER|Y, Kyber|R2|Y
    Kyber|KT|Y, Kyber|MPH|Y, Kyber|SNOW MOUSE|Y
    Kyber|JO9-4MN|Y, Kyber|CYCLO-GRAV|Y, Kyber|TRI-TEK|Y
    Kyber|PLNK|Y, Kyber|DRFT-R|Y, Kyber|ECG|Y
    """,
]

PATH_COLORS = [0x7FD8A8, 0x7EEEFF, 0xB06CF0, 0xEE8F33, 0xE8336F, 0xF2D38A]


def _parse_rebirth_cycle(text: str) -> list[list[tuple]]:
    steps = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        step = []
        for code in line.split(","):
            variant, name, sell = (part.strip() for part in code.strip().split("|"))
            step.append((variant, name, DROID_RARITY.get(name, "Common"), SELL_CODES[sell]))
        steps.append(step)
    assert len(steps) == 40, f"expected 40 rebirths, got {len(steps)}"
    assert all(len(s) == 3 for s in steps), "each rebirth needs exactly 3 droids"
    return steps


REBIRTH_PATHS: list[dict] = [
    {"id": i + 1, "name": f"Cycle {i + 1}", "color": PATH_COLORS[i], "steps": _parse_rebirth_cycle(text)}
    for i, text in enumerate(RAW_REBIRTH_PATHS)
]

# Trait simulator data
TRAITS = [
    # id, name, tier, min, max, unit, effect, iconic droid names
    ("credit-per-second", "Credit Per Second Multiplier", "standard", 10, 50, "%", "Credits Per Second", None),
    ("pickaxe-level", "Pickaxe Level", "standard", 2, 10, "", "Pickaxe Level", None),
    ("health", "Health", "standard", 50, 250, "", "Max Health", None),
    ("crafting-speed", "Crafting Speed", "standard", 5, 25, "%", "Crafting Speed", None),
    ("critical-chance", "Critical Chance", "standard", 5, 25, "%", "Critical Chance", None),
    ("critical-amount", "Critical Amount", "standard", 10, 50, "%", "Critical Amount", None),
    ("upgrade-chip-bonus", "Upgrade Chip Bonus", "standard", 20, 100, "%", "Upgrade Chips", None),
    ("scrap-specialist", "Scrap Specialist", "standard", 10, 50, "%", "Credits from Scrap Stations", None),
    ("fusion-expert", "Fusion Expert", "standard", 20, 100, "%", "Pickaxe Amount on Fusions", None),
    ("mission-efficiency", "Mission Efficiency", "standard", 5, 25, "%", "Astromech Mission Speed", None),
    ("offline-earnings", "Offline Earnings", "standard", 2, 10, "%", "Total Offline Earnings", None),
    ("passive-credits", "Passive Credits", "rainbow", None, None, "", "25% Credits/5sec as Companion", None),
    ("overclocked", "Overclocked", "rainbow", None, None, "", "Doubles Companion Bonus", None),
    ("chip-finder", "Chip Finder", "rainbow", None, None, "", "100% Upgrade Chips when Building Droids", None),
    ("fusion-preservation", "Fusion Preservation", "rainbow", None, None, "", "Droid kept if used in fusion, then trait locks", None),
    ("golden-splash-refill", "Golden Splash Refill", "rainbow", None, None, "", "Gives Gold Splash / 2min While Hitting Depot", None),
    ("upgrade-chip-master", "Upgrade Chip Master", "iconic", None, None, "", "Companion Bonus Active while working", ["BB-8"]),
    ("lifesteal", "Lifesteal", "iconic", None, None, "", "Heal 20% HP when defeating enemies", ["MISTER BONES"]),
    ("blueprint-protector", "Blueprint Protector", "iconic", None, None, "", "No one can pick up your blueprints", ["IG-11"]),
    ("hyperdrive", "Hyperdrive", "iconic", None, None, "", "3x CB-23 Mission Rewards", ["CB-23"]),
    ("world-mission-master", "World Mission Master", "iconic", None, None, "", "Companion Bonus Active while working", ["DJ R-3X"]),
    ("double-crate-chance", "Double Crate Chance", "iconic", None, None, "", "50% chance to get two crates from Astromech", ["R2-D2"]),
    ("droid-sell-master", "Droid Sell Master", "iconic", None, None, "", "Companion Bonus Active while working", ["C-3PO"]),
    ("crit-expert", "Crit Expert", "iconic", None, None, "", "Guarantees first Multi Crit", ["Chopper"]),
    ("fusion-master", "Fusion Master", "iconic", None, None, "", "Companion Bonus Active while working", ["D-O", "D-0"]),
    ("flawless-duplicator", "Flawless Duplicator", "iconic", None, None, "", "Flawless Build Provides Blueprint", ["WG-22"]),
]
TRAIT_BY_ID = {t[0]: t for t in TRAITS}
LEVELS = ["Default", "Gold", "Diamond", "Rainbow"]
ICONIC_ROLL_COST = 200
ICONIC_TRAIT_CHANCE = 3
MAX_CREDIT_SPEND = 1e21
MAX_AUTO_ROLLS = 10_000
MAX_HISTORY = 25
DEFAULT_RAINBOW_CHANCE = 5.0
DEFAULT_LEVEL_WEIGHTS = {"Default": 50, "Gold": 30, "Diamond": 15, "Rainbow": 5}
DEFAULT_PRICE_FIRST = {"Common|Galactic": "9.6M", "Rare|Stellar": "7.2M"}
DEFAULT_REROLL_START_PCT = 100 / 3
DEFAULT_REROLL_GROWTH_PCT = 25.0

ALL_DROIDS: list[tuple[str, str]] = [
    # (name, rarity). Paste the full list from droidData.ts for a complete catalog.
    ("PIT", "Common"), ("MOUSE", "Common"), ("GONK", "Common"),
    ("BAL-CORE", "Rare"), ("R9", "Rare"), ("R4", "Rare"),
    ("GROUNDMECH", "Epic"), ("BB", "Epic"), ("R6", "Epic"),
    ("BB9", "Legendary"), ("R7", "Legendary"),
    ("IG", "Mythic"), ("KX", "Mythic"), ("RIC", "Mythic"),
]
ICONIC_DROIDS = [("BB-8", "Iconic"), ("C-3PO", "Iconic"), ("R2-D2", "Iconic"), ("CB-23", "Iconic"), ("IG-11", "Iconic")]
CATALOG = ALL_DROIDS + ICONIC_DROIDS + [(r["name"], "Fusion") for r in []]  # fusion names added below
CATALOG += [(name, rarity) for name, (rarity, _) in FUSION_RECIPES.items()]

# ──────────────────────────────────────────────────────────────────────────────
# Formatting and parsing helpers
# ──────────────────────────────────────────────────────────────────────────────

def fmt_num(value: float) -> str:
    return f"{value:,.0f}" if abs(value) < 1e4 else f"{value:,.2f}".rstrip("0").rstrip(".")


def fmt_credits(value: float) -> str:
    if not math.isfinite(value):
        return "—"
    for scale, suffix in [(1e18, "Qi"), (1e15, "Qa"), (1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")]:
        if abs(value) >= scale:
            # Truncate to two decimals, the way the game shows prices.
            truncated = math.floor((value / scale) * 100 + 1e-9) / 100
            return f"{truncated:,.2f}".rstrip("0").rstrip(".") + suffix
    return fmt_num(value)


_MULT = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12, "q": 1e15, "qa": 1e15, "qi": 1e18}


def parse_credits(text: str) -> Optional[int]:
    """Reads '7200000', '7.2m', '1.5B', '2Qa'. Returns None if unreadable."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(k|m|b|t|qa|qi|q)?\s*", text.replace(",", "").lower())
    if not match:
        return None
    return round(float(match.group(1)) * _MULT.get(match.group(2) or "", 1))


def parse_duration(text: str) -> Optional[int]:
    """Reads '1h 30m', '45m', '1:45', '1:45:30' or a bare number of seconds. Returns seconds."""
    value = text.strip().lower()
    if not value:
        return None
    if re.fullmatch(r"\d+", value):
        return int(value)
    clock = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", value)
    if clock:
        h, m, s = int(clock.group(1)), int(clock.group(2)), int(clock.group(3) or 0)
        return h * 3600 + m * 60 + s if m < 60 and s < 60 else None
    total, matched = 0.0, False
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)", value):
        matched = True
        total += float(amount) * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1)
    return round(total) if matched else None


def fmt_hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def discord_ts(dt: datetime, style: str = "R") -> str:
    return f"<t:{int(dt.timestamp())}:{style}>"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ──────────────────────────────────────────────────────────────────────────────
# Safe arithmetic calculator (no eval)
# ──────────────────────────────────────────────────────────────────────────────

import ast
import operator as op

_ALLOWED_OPS = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.USub: op.neg, ast.UAdd: op.pos}


def safe_eval(expression: str) -> float:
    """Evaluates + − × ÷ and parentheses with exact integers where possible."""
    expression = expression.replace("×", "*").replace("÷", "/").replace("−", "-")
    if not re.fullmatch(r"[0-9.+\-*/() \t]+", expression):
        raise ValueError("Use only numbers, + - * / and parentheses.")

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise ValueError("Cannot divide by zero.")
            return _ALLOWED_OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
            return _ALLOWED_OPS[type(node.op)](walk(node.operand))
        raise ValueError("Unsupported expression.")

    return walk(ast.parse(expression, mode="eval"))


# ──────────────────────────────────────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────────────────────────────────────

class Store:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id TEXT PRIMARY KEY,
                craft_seconds INTEGER DEFAULT 5400,
                craft_multiplier REAL DEFAULT 2.0,
                current_chips INTEGER DEFAULT 0,
                chip_rate REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, user_id TEXT, created_at TEXT,
                credits INTEGER, chips INTEGER, crystals INTEGER, title TEXT, notes TEXT, duration_sec INTEGER
            );
            CREATE TABLE IF NOT EXISTS droid_marks (
                user_id TEXT, droid_key TEXT, secured_at TEXT,
                PRIMARY KEY (user_id, droid_key)
            );
            CREATE TABLE IF NOT EXISTS trait_droids (
                id TEXT PRIMARY KEY, user_id TEXT, name TEXT, rarity TEXT, variant TEXT,
                rolls INTEGER DEFAULT 0, spent REAL DEFAULT 0, spent_crystals INTEGER DEFAULT 0,
                trait_json TEXT, history_json TEXT DEFAULT '[]', first_price TEXT
            );
            CREATE TABLE IF NOT EXISTS trait_settings (
                user_id TEXT PRIMARY KEY, rainbow_chance REAL, weights_json TEXT
            );
            CREATE TABLE IF NOT EXISTS price_settings (
                user_id TEXT PRIMARY KEY, first_json TEXT, reroll_start_pct REAL, reroll_growth_pct REAL
            );
            CREATE TABLE IF NOT EXISTS rebirth_progress (
                user_id TEXT, cycle INTEGER, done INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, cycle)
            );
            CREATE TABLE IF NOT EXISTS upgrade_chart (
                user_id TEXT PRIMARY KEY, chart_json TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, name TEXT, starts_at TEXT, ends_at TEXT, color INTEGER,
                intro TEXT, details_json TEXT, boosts_json TEXT
            );
            CREATE TABLE IF NOT EXISTS announcements (
                id TEXT PRIMARY KEY, title TEXT, body TEXT, kind TEXT, pinned INTEGER, posted_at TEXT, author TEXT
            );
        """)
        self.conn.commit()

    def _one(self, sql: str, args=()):
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, args=()):
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def _run(self, sql: str, args=()):
        self.conn.execute(sql, args)
        self.conn.commit()

    # profile
    def profile(self, user_id: str) -> dict:
        row = self._one("SELECT * FROM profiles WHERE user_id=?", (user_id,))
        if row:
            return row
        self._run("INSERT INTO profiles (user_id) VALUES (?)", (user_id,))
        return self.profile(user_id)

    def update_profile(self, user_id: str, **fields):
        self.profile(user_id)
        cols = ", ".join(f"{k}=?" for k in fields)
        self._run(f"UPDATE profiles SET {cols} WHERE user_id=?", (*fields.values(), user_id))

    # sessions
    def add_session(self, user_id, credits, chips, crystals, title, notes, duration_sec):
        self._run(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex[:10], user_id, utc_now().isoformat(), credits, chips, crystals, title, notes, duration_sec),
        )

    def sessions(self, user_id: str, limit: int = 50):
        return self._all("SELECT * FROM sessions WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit))

    def delete_session(self, user_id: str, session_id: str):
        self._run("DELETE FROM sessions WHERE user_id=? AND id=?", (user_id, session_id))

    # droid marks (Droidex)
    def toggle_mark(self, user_id: str, key: str) -> bool:
        if self._one("SELECT 1 FROM droid_marks WHERE user_id=? AND droid_key=?", (user_id, key)):
            self._run("DELETE FROM droid_marks WHERE user_id=? AND droid_key=?", (user_id, key))
            return False
        self._run("INSERT INTO droid_marks VALUES (?,?,?)", (user_id, key, utc_now().isoformat()))
        return True

    def marked_keys(self, user_id: str) -> set[str]:
        return {r["droid_key"] for r in self._all("SELECT droid_key FROM droid_marks WHERE user_id=?", (user_id,))}

    # trait droids
    def trait_droids(self, user_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM trait_droids WHERE user_id=? ORDER BY rowid DESC", (user_id,))
        for r in rows:
            r["trait"] = json.loads(r["trait_json"]) if r["trait_json"] else None
            r["history"] = json.loads(r["history_json"] or "[]")
        return rows

    def save_trait_droid(self, user_id: str, d: dict):
        self._run(
            """INSERT INTO trait_droids (id,user_id,name,rarity,variant,rolls,spent,spent_crystals,trait_json,history_json,first_price)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET rolls=excluded.rolls, spent=excluded.spent,
               spent_crystals=excluded.spent_crystals, trait_json=excluded.trait_json,
               history_json=excluded.history_json, first_price=excluded.first_price, variant=excluded.variant""",
            (d["id"], user_id, d["name"], d["rarity"], d["variant"], d["rolls"], d["spent"],
             d.get("spent_crystals", 0), json.dumps(d.get("trait")) if d.get("trait") else None,
             json.dumps(d.get("history", [])), d.get("first_price")),
        )

    def delete_trait_droid(self, user_id: str, droid_id: str):
        self._run("DELETE FROM trait_droids WHERE user_id=? AND id=?", (user_id, droid_id))

    def trait_settings(self, user_id: str) -> tuple[float, dict]:
        row = self._one("SELECT * FROM trait_settings WHERE user_id=?", (user_id,))
        if not row:
            return DEFAULT_RAINBOW_CHANCE, dict(DEFAULT_LEVEL_WEIGHTS)
        return row["rainbow_chance"], json.loads(row["weights_json"])

    def save_trait_settings(self, user_id: str, rainbow_chance: float, weights: dict):
        self._run(
            "INSERT INTO trait_settings VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET rainbow_chance=excluded.rainbow_chance, weights_json=excluded.weights_json",
            (user_id, rainbow_chance, json.dumps(weights)),
        )

    def prices(self, user_id: str) -> dict:
        row = self._one("SELECT * FROM price_settings WHERE user_id=?", (user_id,))
        if not row:
            return {"first": dict(DEFAULT_PRICE_FIRST), "start": DEFAULT_REROLL_START_PCT, "growth": DEFAULT_REROLL_GROWTH_PCT}
        return {"first": json.loads(row["first_json"]), "start": row["reroll_start_pct"], "growth": row["reroll_growth_pct"]}

    def save_prices(self, user_id: str, prices: dict):
        self._run(
            "INSERT INTO price_settings VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET first_json=excluded.first_json, reroll_start_pct=excluded.reroll_start_pct, reroll_growth_pct=excluded.reroll_growth_pct",
            (user_id, json.dumps(prices["first"]), prices["start"], prices["growth"]),
        )

    # rebirth progress
    def rebirth_done(self, user_id: str, cycle: int) -> int:
        row = self._one("SELECT done FROM rebirth_progress WHERE user_id=? AND cycle=?", (user_id, cycle))
        return row["done"] if row else 0

    def set_rebirth_done(self, user_id: str, cycle: int, done: int):
        self._run(
            "INSERT INTO rebirth_progress VALUES (?,?,?) ON CONFLICT(user_id,cycle) DO UPDATE SET done=excluded.done",
            (user_id, cycle, max(0, min(40, done))),
        )

    # upgrade chart (per user; falls back to defaults)
    def upgrade_chart(self, user_id: str) -> dict:
        row = self._one("SELECT chart_json FROM upgrade_chart WHERE user_id=?", (user_id,))
        return json.loads(row["chart_json"]) if row else DEFAULT_UPGRADE_CHART

    def save_upgrade_chart(self, user_id: str, chart: dict):
        self._run(
            "INSERT INTO upgrade_chart VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET chart_json=excluded.chart_json",
            (user_id, json.dumps(chart)),
        )

    # events and announcements
    def events(self) -> list[dict]:
        rows = self._all("SELECT * FROM events ORDER BY starts_at DESC")
        for r in rows:
            r["details"] = json.loads(r["details_json"] or "[]")
            r["boosts"] = json.loads(r["boosts_json"] or "[]")
        return rows

    def save_event(self, e: dict):
        self._run(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, starts_at=excluded.starts_at, ends_at=excluded.ends_at, color=excluded.color, intro=excluded.intro, details_json=excluded.details_json, boosts_json=excluded.boosts_json",
            (e["id"], e["name"], e["starts_at"], e["ends_at"], e["color"], e["intro"],
             json.dumps(e["details"]), json.dumps(e["boosts"])),
        )

    def delete_event(self, event_id: str):
        self._run("DELETE FROM events WHERE id=?", (event_id,))

    def announcements(self, limit: int = 10) -> list[dict]:
        return self._all("SELECT * FROM announcements ORDER BY pinned DESC, posted_at DESC LIMIT ?", (limit,))

    def add_announcement(self, title, body, kind, pinned, author):
        self._run(
            "INSERT INTO announcements VALUES (?,?,?,?,?,?,?)",
            (uuid.uuid4().hex[:10], title, body, kind, int(pinned), utc_now().isoformat(), author),
        )


store = Store(DB_PATH)

# ──────────────────────────────────────────────────────────────────────────────
# Timers: spawn and mission schedules (pure math, UTC)
# ──────────────────────────────────────────────────────────────────────────────

def next_spawn(interval_min: int, offset_min: int, now: datetime) -> datetime:
    """Next boundary at offset_min past each interval, measured in UTC minutes."""
    minutes = now.hour * 60 + now.minute + now.second / 60
    period = interval_min
    k = math.floor((minutes - offset_min) / period) + 1
    target_min = k * period + offset_min
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(minutes=target_min)


def mission_state(now: datetime) -> dict:
    """The 35-minute mission cycle: a 5-minute run, then a 30-minute countdown.
    Missions rotate through MISSIONS in order, starting at :15 UTC each hour."""
    epoch = now.replace(minute=MISSION_EPOCH_UTC_MINUTE, second=0, microsecond=0)
    if epoch > now:
        epoch -= timedelta(hours=1)
    elapsed = (now - epoch).total_seconds()
    slot = int(elapsed // (MISSION_CYCLE_MINUTES * 60))
    slot_start = epoch + timedelta(minutes=slot * MISSION_CYCLE_MINUTES)
    into_slot = (now - slot_start).total_seconds()
    running = into_slot < MISSION_RUN_SECONDS
    mission_index = (slot) % len(MISSIONS)
    return {
        "mission": MISSIONS[mission_index],
        "running": running,
        "run_ends": slot_start + timedelta(seconds=MISSION_RUN_SECONDS),
        "next_start": slot_start + timedelta(minutes=MISSION_CYCLE_MINUTES),
        "next_mission": MISSIONS[(mission_index + 1) % len(MISSIONS)],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Trait simulator logic
# ──────────────────────────────────────────────────────────────────────────────

def trait_chance(trait: tuple, rainbow_pct: float, droid_name: Optional[str]) -> float:
    owned = None
    if droid_name:
        for t in TRAITS:
            if t[2] == "iconic" and t[7] and any(n.lower() == droid_name.lower() for n in t[7]):
                owned = t
                break
    tier = trait[2]
    if tier == "iconic":
        return ICONIC_TRAIT_CHANCE if owned and owned[0] == trait[0] else 0.0
    rainbow = min(100.0, max(0.0, rainbow_pct))
    if tier == "rainbow":
        base = rainbow / 5
    else:
        base = (100 - rainbow) / 11
    return base * 97 / 100 if owned else base


def pick_level(weights: dict) -> str:
    total = sum(max(0, weights.get(l, 0)) for l in LEVELS) or 1
    pick = random.random() * total
    for level in LEVELS:
        pick -= max(0, weights.get(level, 0))
        if pick < 0:
            return level
    return "Default"


def level_value(trait: tuple, level: str) -> float:
    mn, mx = trait[3], trait[4]
    if level == "Default":
        return mn
    if level == "Gold":
        return mn * 2
    if level == "Diamond":
        return mn * 3
    return mx


def roll_trait(rainbow_pct: float, weights: dict, droid_name: Optional[str]) -> dict:
    pick = random.random() * 100
    for trait in TRAITS:
        pick -= trait_chance(trait, rainbow_pct, droid_name)
        if pick < 0:
            if trait[2] != "standard":
                return {"traitId": trait[0]}
            level = pick_level(weights)
            return {"traitId": trait[0], "level": level, "value": level_value(trait, level)}
    last = [t for t in TRAITS if t[2] == "standard"][-1]
    return {"traitId": last[0], "level": "Rainbow", "value": last[4]}


def describe_roll(roll: dict) -> str:
    trait = TRAIT_BY_ID[roll["traitId"]]
    if trait[2] != "standard":
        return trait[6]
    return f"+{fmt_num(roll.get('value', trait[3]))}{trait[5]} {trait[6]}"


def roll_cost(droid: dict, start_index: int, count: int, prices: dict) -> float:
    if droid["rarity"].lower() == "iconic":
        return count * ICONIC_ROLL_COST
    first = droid.get("first_price") and parse_credits(droid["first_price"])
    if first is None:
        first = parse_credits(prices["first"].get(f"{droid['rarity'] if droid['rarity'] in RARITY_COLORS else 'Common'}|{droid['variant']}", "")) or 0
    total = 0.0
    for idx in range(start_index, start_index + count):
        if total >= MAX_CREDIT_SPEND:
            break
        if idx == 0:
            price = first
        else:
            price = first * (prices["start"] / 100) * (1 + prices["growth"] / 100) ** (idx - 1)
        total += round(price)
    return min(MAX_CREDIT_SPEND, total)


# ──────────────────────────────────────────────────────────────────────────────
# Embeds and shared UI helpers
# ──────────────────────────────────────────────────────────────────────────────

def embed(title: str, description: str = "", color: int = 0x58E6F7) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color)
    e.timestamp = utc_now()
    return e


def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    if ADMIN_ROLE_ID and isinstance(interaction.user, discord.Member):
        return any(str(r.id) == ADMIN_ROLE_ID for r in interaction.user.roles)
    return False


class NumberModal(discord.ui.Modal):
    """Generic one-field modal. Calls on_submit_value(interaction, text) on submit."""

    def __init__(self, title: str, label: str, placeholder: str, callback, default: str = ""):
        super().__init__(title=title)
        self.field = discord.ui.TextInput(label=label, placeholder=placeholder, default=default, required=True, max_length=40)
        self.add_item(self.field)
        self.callback_fn = callback

    async def on_submit(self, interaction: discord.Interaction):
        await self.callback_fn(interaction, self.field.value.strip())


# ──────────────────────────────────────────────────────────────────────────────
# Bot setup
# ──────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ──────────────────────────────────────────────────────────────────────────────
# /calc  (craft and AFK calculators)
# ──────────────────────────────────────────────────────────────────────────────

calc_group = app_commands.Group(name="calc", description="Crafting calculators")
tree.add_command(calc_group)


def craft_embed(seconds: int, multiplier: float) -> discord.Embed:
    result = round(seconds / max(0.01, multiplier))
    e = embed("⚙️ Craft Calculator", "Projected completion with your saved multiplier.", 0x58E6F7)
    e.add_field(name="Base duration", value=fmt_hms(seconds), inline=True)
    e.add_field(name="Multiplier", value=f"{multiplier:.2f}×", inline=True)
    e.add_field(name="Projected", value=f"**{fmt_hms(result)}**", inline=False)
    e.add_field(name="Time saved", value=f"{max(0, seconds - result)}s", inline=True)
    return e


class CraftView(discord.ui.View):
    def __init__(self, user_id: str):
        super().__init__(timeout=600)
        self.user_id = user_id

    @discord.ui.button(label="Set time", style=discord.ButtonStyle.primary, emoji="⏱️")
    async def set_time(self, interaction: discord.Interaction, _):
        async def save(i: discord.Interaction, text: str):
            seconds = parse_duration(text)
            if seconds is None:
                return await i.response.send_message("Could not read that time. Try `1h 30m` or `5400`.", ephemeral=True)
            store.update_profile(self.user_id, craft_seconds=seconds)
            p = store.profile(self.user_id)
            await i.response.edit_message(embed=craft_embed(p["craft_seconds"], p["craft_multiplier"]), view=self)
        await interaction.response.send_modal(NumberModal("Base craft time", "Time", "1h 30m or 5400", save))

    @discord.ui.button(label="Multiplier", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def set_mult(self, interaction: discord.Interaction, _):
        async def save(i: discord.Interaction, text: str):
            try:
                value = float(text)
            except ValueError:
                return await i.response.send_message("Enter a number like 2.0.", ephemeral=True)
            if value < 0.01:
                return await i.response.send_message("Multiplier must be at least 0.01.", ephemeral=True)
            store.update_profile(self.user_id, craft_multiplier=value)
            p = store.profile(self.user_id)
            await i.response.edit_message(embed=craft_embed(p["craft_seconds"], p["craft_multiplier"]), view=self)
        await interaction.response.send_modal(NumberModal("Craft multiplier", "Multiplier", "2.0", save))


@calc_group.command(name="craft", description="Craft time with your saved multiplier")
async def calc_craft(interaction: discord.Interaction):
    p = store.profile(str(interaction.user.id))
    await interaction.response.send_message(
        embed=craft_embed(p["craft_seconds"], p["craft_multiplier"]),
        view=CraftView(str(interaction.user.id)),
        ephemeral=True,
    )


@calc_group.command(name="afk", description="AFK completion estimate")
async def calc_afk(interaction: discord.Interaction):
    async def compute(i: discord.Interaction, text: str):
        # Expects "craft_time, rate", e.g. "1h 30m, 12"
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 2:
            return await i.response.send_message("Format: `craft time, rate`, e.g. `1h 30m, 12`.", ephemeral=True)
        seconds = parse_duration(parts[0])
        try:
            rate = float(parts[1])
        except ValueError:
            rate = 0
        if seconds is None or rate <= 0:
            return await i.response.send_message("Enter a valid craft time and a positive rate.", ephemeral=True)
        real = math.ceil(seconds / rate)
        e = embed("⏳ AFK Calculator", "Estimated real time to finish.", 0xE5383B)
        e.add_field(name="Craft duration", value=fmt_hms(seconds), inline=True)
        e.add_field(name="Rate", value=f"{rate:g} craft sec / real sec", inline=True)
        e.add_field(name="Real time", value=f"**{fmt_hms(real)}**", inline=False)
        await i.response.send_message(embed=e, ephemeral=True)

    await interaction.response.send_modal(NumberModal("AFK calculator", "Craft time, rate", "1h 30m, 12", compute))


# ──────────────────────────────────────────────────────────────────────────────
# /math  (exact calculator)
# ──────────────────────────────────────────────────────────────────────────────

@tree.command(name="math", description="Quick exact calculator, e.g. (1250000000 * 3) / 2")
@app_commands.describe(expression="Numbers with + - * / and parentheses")
async def math_cmd(interaction: discord.Interaction, expression: str):
    try:
        result = safe_eval(expression)
    except (ValueError, SyntaxError, ZeroDivisionError) as err:
        return await interaction.response.send_message(f"❌ {err}", ephemeral=True)
    text = f"{result:,.0f}" if float(result).is_integer() else f"{result:,.6f}".rstrip("0")
    await interaction.response.send_message(embed=embed("🧮 Result", f"`{expression}` = **{text}**", 0x42FF8C), ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# /timers  (spawns and missions)
# ──────────────────────────────────────────────────────────────────────────────

class TimerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="timers:refresh")
    async def refresh(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(embed=timers_embed(), view=self)


def timers_embed() -> discord.Embed:
    now = utc_now()
    e = embed("📡 Spawns & Missions", "Countdowns are live in your Discord client.", 0x58E6F7)
    for name, s in SPAWN_SCHEDULES.items():
        nxt = next_spawn(s["interval_min"], s["offset_min"], now)
        e.add_field(name=name, value=f"{discord_ts(nxt, 'R')} · {nxt.strftime('%H:%M')} UTC", inline=False)
    m = mission_state(now)
    if m["running"]:
        status = f"**{m['mission']}** running, ends {discord_ts(m['run_ends'], 'R')}"
    else:
        status = f"Next: **{m['next_mission']}** {discord_ts(m['next_start'], 'R')}"
    e.add_field(name="Missions", value=status, inline=False)
    return e


@tree.command(name="timers", description="Spawn and mission countdowns")
async def timers_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(embed=timers_embed(), view=TimerView())


# ──────────────────────────────────────────────────────────────────────────────
# /fusion
# ──────────────────────────────────────────────────────────────────────────────

class FusionSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=name, description=rarity) for name, (rarity, _) in list(FUSION_RECIPES.items())[:25]]
        super().__init__(placeholder="Pick a fusion", options=options)

    async def callback(self, interaction: discord.Interaction):
        name = self.values[0]
        rarity, ingredients = FUSION_RECIPES[name]
        e = embed(f"🔀 {name}", f"Rarity: **{rarity}**", RARITY_COLORS.get(rarity, 0x58E6F7))
        e.add_field(name="Ingredients", value="\n".join(f"• {i}" for i in ingredients), inline=False)
        await interaction.response.edit_message(embed=e, view=self.view)


@tree.command(name="fusion", description="Fusion recipes")
async def fusion_cmd(interaction: discord.Interaction):
    view = discord.ui.View(timeout=600)
    view.add_item(FusionSelect())
    await interaction.response.send_message(embed=embed("🔀 Fusion recipes", "Choose a fusion to see its ingredients."), view=view, ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# /upgrade  (chip cost planner using the user's upgrade chart)
# ──────────────────────────────────────────────────────────────────────────────

@tree.command(name="upgrade", description="Chip cost to upgrade a droid's rarity to a variant")
@app_commands.describe(rarity="Droid rarity", target="Target variant")
@app_commands.choices(
    rarity=[app_commands.Choice(name=r, value=r) for r in ["Common", "Rare", "Epic", "Legendary", "Mythic"]],
    target=[app_commands.Choice(name=v, value=v) for v in UPGRADE_TIERS],
)
async def upgrade_cmd(interaction: discord.Interaction, rarity: app_commands.Choice[str], target: app_commands.Choice[str]):
    chart = store.upgrade_chart(str(interaction.user.id))
    row = chart.get(rarity.value, {})
    steps = UPGRADE_TIERS[: UPGRADE_TIERS.index(target.value) + 1]
    lines = [f"→ {s}: **{fmt_num(row.get(s, 0))}** chips" for s in steps]
    total = sum(row.get(s, 0) for s in steps)
    e = embed(f"⬆️ {rarity.value} → {target.value}", "\n".join(lines), RARITY_COLORS[rarity.value])
    e.add_field(name="Total", value=f"**{fmt_num(total)}** chips", inline=False)
    await interaction.response.send_message(embed=e, ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# /droidex  (secured tracking)
# ──────────────────────────────────────────────────────────────────────────────

class DroidexSelect(discord.ui.Select):
    def __init__(self, user_id: str, page: int = 0):
        self.user_id = user_id
        names = ALL_DROIDS + ICONIC_DROIDS
        chunk = names[page * 25:(page + 1) * 25]
        marked = store.marked_keys(user_id)
        options = []
        for name, rarity in chunk:
            key = f"{rarity}:{name}"
            options.append(discord.SelectOption(label=name, description=rarity, value=key, default=key in marked))
        super().__init__(placeholder="Toggle secured droids", min_values=0, max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction):
        marked = store.marked_keys(self.user_id)
        chosen = set(self.values)
        for opt in self.options:
            if (opt.value in chosen) != (opt.value in marked):
                store.toggle_mark(self.user_id, opt.value)
        total = len(ALL_DROIDS) + len(ICONIC_DROIDS)
        done = len(store.marked_keys(self.user_id))
        await interaction.response.edit_message(embed=embed("📘 Droidex", f"Secured **{done} / {total}**"), view=DroidexView(self.user_id))


class DroidexView(discord.ui.View):
    def __init__(self, user_id: str):
        super().__init__(timeout=600)
        self.add_item(DroidexSelect(user_id))


@tree.command(name="droidex", description="Mark droids as secured")
async def droidex_cmd(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    done = len(store.marked_keys(uid))
    total = len(ALL_DROIDS) + len(ICONIC_DROIDS)
    await interaction.response.send_message(embed=embed("📘 Droidex", f"Secured **{done} / {total}**. Select droids to toggle them."), view=DroidexView(uid), ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# /profile and /session
# ──────────────────────────────────────────────────────────────────────────────

session_group = app_commands.Group(name="session", description="Log and review Droid Tycoon runs")
tree.add_command(session_group)


@session_group.command(name="log", description="Log a run's resources")
@app_commands.describe(credits="Credits gained", chips="Upgrade chips gained", crystals="Nova crystals gained",
                       length="Session length, e.g. 1h 4m", title="Optional title", notes="Optional notes")
async def session_log(interaction: discord.Interaction, credits: int = 0, chips: int = 0, crystals: int = 0,
                      length: str = "", title: str = "", notes: str = ""):
    duration = parse_duration(length) if length else None
    if length and duration is None:
        return await interaction.response.send_message("Session length must look like `1h 4m` or `1:45`.", ephemeral=True)
    if min(credits, chips, crystals) < 0:
        return await interaction.response.send_message("Values can't be negative.", ephemeral=True)
    store.add_session(str(interaction.user.id), credits, chips, crystals, title or None, notes or None, duration)
    await interaction.response.send_message(
        embed=embed("✅ Session logged", f"+{fmt_num(credits)} credits · +{fmt_num(chips)} chips · +{fmt_num(crystals)} crystals", 0x42FF8C),
        ephemeral=True,
    )


@session_group.command(name="list", description="Your recent runs and totals")
async def session_list(interaction: discord.Interaction):
    rows = store.sessions(str(interaction.user.id), 10)
    if not rows:
        return await interaction.response.send_message("No sessions logged yet.", ephemeral=True)
    totals = {k: sum(r[k] or 0 for r in rows) for k in ("credits", "chips", "crystals")}
    e = embed("📋 Recent sessions", f"Last {len(rows)} runs · +{fmt_num(totals['credits'])} credits · +{fmt_num(totals['chips'])} chips · +{fmt_num(totals['crystals'])} crystals")
    for r in rows:
        when = datetime.fromisoformat(r["created_at"])
        label = r["title"] or "Run"
        e.add_field(name=f"{label} · {discord_ts(when, 'd')}", value=f"+{fmt_num(r['credits'])} cr · +{fmt_num(r['chips'])} ch · +{fmt_num(r['crystals'])} cy", inline=False)
    await interaction.response.send_message(embed=e, ephemeral=True)


@tree.command(name="profile", description="Your saved craft settings and chip balance")
async def profile_cmd(interaction: discord.Interaction):
    p = store.profile(str(interaction.user.id))
    e = embed(f"👤 {interaction.user.display_name}", "Saved settings", 0x58E6F7)
    e.add_field(name="Craft time", value=fmt_hms(p["craft_seconds"]), inline=True)
    e.add_field(name="Multiplier", value=f"{p['craft_multiplier']:.2f}×", inline=True)
    e.add_field(name="Upgrade chips on hand", value=fmt_num(p["current_chips"]), inline=True)
    await interaction.response.send_message(embed=e, ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# /trait  (Nova Crystal Shop trait simulator)
# ──────────────────────────────────────────────────────────────────────────────

trait_group = app_commands.Group(name="trait", description="Trait roll simulator")
tree.add_command(trait_group)


def droid_panel_embed(d: dict, prices: dict) -> discord.Embed:
    color = RARITY_COLORS.get(d["rarity"], 0x58E6F7)
    trait = d.get("trait")
    desc = f"**Current trait:** {TRAIT_BY_ID[trait['traitId']][1]}\n{describe_roll(trait)}" if trait else "*No trait yet. Press Roll to discover one.*"
    e = embed(f"🎲 {d['variant']} {d['name']}", desc, color)
    if d["rarity"].lower() == "iconic":
        spent = f"{fmt_num(d['spent_crystals'])} Nova crystals"
        nxt = f"{ICONIC_ROLL_COST} Nova crystals"
    else:
        spent = f"{fmt_credits(d['spent'])} credits"
        nxt_price = roll_cost(d, d["rolls"], 1, prices)
        nxt = f"{fmt_credits(nxt_price)} credits"
    e.add_field(name="Rolls", value=fmt_num(d["rolls"]), inline=True)
    e.add_field(name="Spent", value=spent, inline=True)
    e.add_field(name="Next roll", value=nxt, inline=True)
    return e


class TraitPanel(discord.ui.View):
    def __init__(self, user_id: str, droid_id: Optional[str] = None):
        super().__init__(timeout=900)
        self.user_id = user_id
        droids = store.trait_droids(user_id)
        self.droid = next((d for d in droids if d["id"] == droid_id), droids[0] if droids else None)
        if droids:
            options = [discord.SelectOption(label=f"{d['variant']} {d['name']}"[:100], description=f"{d['rarity']} · {d['rolls']} rolls",
                                            value=d["id"], default=bool(self.droid and d["id"] == self.droid["id"])) for d in droids[:25]]
            self.add_item(TraitSwitch(options))

    @discord.ui.button(label="Roll", style=discord.ButtonStyle.primary, emoji="🎲", row=1)
    async def roll1(self, interaction: discord.Interaction, _):
        await self._roll(interaction, 1)

    @discord.ui.button(label="Roll ×10", style=discord.ButtonStyle.secondary, row=1)
    async def roll10(self, interaction: discord.Interaction, _):
        await self._roll(interaction, 10)

    @discord.ui.button(label="Roll until…", style=discord.ButtonStyle.success, emoji="⚡", row=1)
    async def until(self, interaction: discord.Interaction, _):
        if not self.droid:
            return await interaction.response.send_message("Add a droid first.", ephemeral=True)
        await interaction.response.send_modal(UntilModal(self))

    @discord.ui.button(label="History", style=discord.ButtonStyle.secondary, emoji="📜", row=2)
    async def history(self, interaction: discord.Interaction, _):
        if not self.droid:
            return await interaction.response.send_message("Add a droid first.", ephemeral=True)
        lines = []
        for h in self.droid["history"][:15]:
            t = TRAIT_BY_ID.get(h["traitId"])
            lines.append(f"• {t[1]}" + (f" ({h['level']})" if h.get("level") else "") if t else "• Unknown")
        await interaction.response.send_message(embed=embed(f"📜 {self.droid['name']} · recent rolls", "\n".join(lines) or "No rolls yet."), ephemeral=True)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.danger, row=2)
    async def reset(self, interaction: discord.Interaction, _):
        if not self.droid:
            return
        d = self.droid
        d.update(trait=None, rolls=0, spent=0, spent_crystals=0, history=[])
        store.save_trait_droid(self.user_id, d)
        await interaction.response.edit_message(embed=droid_panel_embed(d, store.prices(self.user_id)), view=TraitPanel(self.user_id, d["id"]))

    async def _roll(self, interaction: discord.Interaction, count: int):
        if not self.droid:
            return await interaction.response.send_message("Add a droid first.", ephemeral=True)
        rainbow, weights = store.trait_settings(self.user_id)
        prices = store.prices(self.user_id)
        results = [roll_trait(rainbow, weights, self.droid["name"]) for _ in range(count)]
        apply_rolls(self.user_id, self.droid, results, prices)
        announce = None
        last = results[-1]
        t = TRAIT_BY_ID[last["traitId"]]
        if t[2] == "rainbow":
            announce = f"🌈 **Rainbow trait!** {t[1]}"
        elif last.get("level") not in (None, "Default"):
            announce = f"✨ **{last['level']} trait!** {describe_roll(last)}"
        await interaction.response.edit_message(embed=droid_panel_embed(self.droid, prices), view=TraitPanel(self.user_id, self.droid["id"]))
        if announce:
            await interaction.followup.send(announce, ephemeral=True)


def apply_rolls(user_id: str, droid: dict, results: list[dict], prices: dict):
    cost = roll_cost(droid, droid["rolls"], len(results), prices)
    droid["rolls"] += len(results)
    if droid["rarity"].lower() == "iconic":
        droid["spent_crystals"] = droid.get("spent_crystals", 0) + cost
    else:
        droid["spent"] = min(MAX_CREDIT_SPEND, droid["spent"] + cost)
    droid["trait"] = results[-1]
    droid["history"] = (list(reversed(results[-MAX_HISTORY:])) + droid["history"])[:MAX_HISTORY]
    store.save_trait_droid(user_id, droid)


class TraitSwitch(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="Switch droid", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        await interaction.response.edit_message(view=TraitPanel(uid, self.values[0]),
                                                embed=droid_panel_embed(next(d for d in store.trait_droids(uid) if d["id"] == self.values[0]), store.prices(uid)))


class UntilModal(discord.ui.Modal, title="Roll until"):
    trait_id = discord.ui.TextInput(label="Trait id (e.g. credit-per-second)", max_length=60)
    min_level = discord.ui.TextInput(label="Min level: Default / Gold / Diamond / Rainbow", required=False, default="Default", max_length=10)

    def __init__(self, panel: TraitPanel):
        super().__init__()
        self.panel = panel

    async def on_submit(self, interaction: discord.Interaction):
        target = TRAIT_BY_ID.get(self.trait_id.value.strip())
        if not target:
            return await interaction.response.send_message("Unknown trait id. Try `/trait ids` to see them.", ephemeral=True)
        min_level = (self.min_level.value or "Default").strip().title()
        if min_level not in LEVELS:
            return await interaction.response.send_message(f"Level must be one of: {', '.join(LEVELS)}.", ephemeral=True)
        rainbow, weights = store.trait_settings(self.panel.user_id)
        prices = store.prices(self.panel.user_id)
        droid = self.panel.droid
        results, hit = [], None
        while len(results) < MAX_AUTO_ROLLS:
            r = roll_trait(rainbow, weights, droid["name"])
            results.append(r)
            ok = r["traitId"] == target[0] and (target[2] == "rainbow" or LEVELS.index(r.get("level", "Default")) >= LEVELS.index(min_level))
            if ok:
                hit = r
                break
        cost = roll_cost(droid, droid["rolls"], len(results), prices)
        apply_rolls(self.panel.user_id, droid, results, prices)
        cost_text = f"{fmt_num(cost)} Nova crystals" if droid["rarity"].lower() == "iconic" else f"{fmt_credits(cost)} credits"
        if hit:
            e = embed("✅ Trait found", f"**{target[1]}** after {len(results):,} rolls.\nCost: {cost_text}", 0x42FF8C)
        else:
            e = embed("❌ Not found", f"No {target[1]} at {min_level} or better in {MAX_AUTO_ROLLS:,} rolls.\nCost: {cost_text}", 0xE5383B)
        await interaction.response.send_message(embed=e, ephemeral=True)


@trait_group.command(name="add", description="Add a droid to your trait list")
@app_commands.describe(name="Droid name", variant="Variant", first_price="First roll price, e.g. 9.6M (optional)")
async def trait_add(interaction: discord.Interaction, name: str, variant: str = "Default", first_price: str = ""):
    match = next(((n, r) for n, r in CATALOG if n.lower() == name.strip().lower()), None)
    if not match:
        return await interaction.response.send_message("Unknown droid. Try autocomplete or check the spelling.", ephemeral=True)
    if first_price and parse_credits(first_price) is None:
        return await interaction.response.send_message("Price must look like `9.6M` or `1.5B`.", ephemeral=True)
    droid = {"id": uuid.uuid4().hex, "name": match[0], "rarity": match[1], "variant": variant if variant in VARIANTS else "Default",
             "rolls": 0, "spent": 0, "spent_crystals": 0, "history": [], "trait": None,
             "first_price": first_price or None}
    store.save_trait_droid(str(interaction.user.id), droid)
    await interaction.response.send_message(f"➕ Added **{droid['variant']} {droid['name']}**. Use `/trait roll` to start.", ephemeral=True)


@trait_add.autocomplete("name")
async def trait_add_ac(interaction: discord.Interaction, current: str):
    q = current.lower()
    return [app_commands.Choice(name=n, value=n) for n, _ in CATALOG if q in n.lower()][:25]


@trait_group.command(name="roll", description="Open the trait roll panel")
async def trait_roll(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    droids = store.trait_droids(uid)
    if not droids:
        return await interaction.response.send_message("No droids yet. Use `/trait add` first.", ephemeral=True)
    panel = TraitPanel(uid)
    await interaction.response.send_message(embed=droid_panel_embed(panel.droid, store.prices(uid)), view=panel, ephemeral=True)


@trait_group.command(name="settings", description="Set special-trait chance and level weights")
@app_commands.describe(rainbow_chance="Special trait chance, 0–100", default="Default weight", gold="Gold weight", diamond="Diamond weight", rainbow="Rainbow weight")
async def trait_settings_cmd(interaction: discord.Interaction, rainbow_chance: float = DEFAULT_RAINBOW_CHANCE,
                             default: int = 50, gold: int = 30, diamond: int = 15, rainbow: int = 5):
    rainbow_chance = min(100.0, max(0.0, rainbow_chance))
    store.save_trait_settings(str(interaction.user.id), rainbow_chance, {"Default": default, "Gold": gold, "Diamond": diamond, "Rainbow": rainbow})
    await interaction.response.send_message(f"Saved. Special chance {rainbow_chance:g}%, level weights {default}/{gold}/{diamond}/{rainbow}.", ephemeral=True)


@trait_group.command(name="ids", description="List trait ids for Roll until")
async def trait_ids(interaction: discord.Interaction):
    lines = [f"`{t[0]}` · {t[1]}" for t in TRAITS]
    await interaction.response.send_message(embed=embed("🧬 Trait ids", "\n".join(lines)[:4000]), ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# /rebirth  (super rebirth paths, progress, Nova calculator)
# ──────────────────────────────────────────────────────────────────────────────

rebirth_group = app_commands.Group(name="rebirth", description="Super rebirth paths and progress")
tree.add_command(rebirth_group)
REBIRTH_TIERS = [("1–10", 1, 10, 0x8EF06B), ("11–20", 11, 20, 0x8EF06B), ("21–30", 21, 30, 0xE5383B), ("31–35", 31, 35, 0xE8C25A), ("36–40", 36, 40, 0xD6DBE3)]


def rebirth_embed(cycle: int, tier_index: int, done: int) -> discord.Embed:
    path = REBIRTH_PATHS[cycle - 1]
    label, lo, hi, color = REBIRTH_TIERS[tier_index]
    e = embed(f"🔁 {path['name']} · Rebirths {label}", f"Progress **{done}/40** · Next rebirth **{min(40, done + 1)}**", color)
    for n in range(lo, hi + 1):
        step = path["steps"][n - 1]
        mark = "✅" if n <= done else "▶️" if n == done + 1 else "▫️"
        lines = []
        for variant, name, _rarity, sell in step:
            tag = " 💰" if sell == "sell" else " ⚠️" if sell == "keep21" else ""
            lines.append(f"• **{variant}** {name}{tag}")
        reward = REBIRTH_REWARDS.get(n)
        if reward:
            rb, srb, coins, xp = reward
            lines.append(f"💎 {rb} RB · 🔮 {srb} SRB · +{coins}% coins · XP +{xp}%")
        e.add_field(name=f"{mark} Rebirth {n} · {REBIRTH_COSTS[n - 1]}", value="\n".join(lines), inline=False)
    e.set_footer(text="💰 can be sold · ⚠️ needed again at rebirth 21+")
    return e


class RebirthView(discord.ui.View):
    def __init__(self, user_id: str, cycle: int, tier_index: Optional[int] = None):
        super().__init__(timeout=900)
        self.user_id, self.cycle = user_id, cycle
        done = store.rebirth_done(user_id, cycle)
        self.done = done
        self.tier_index = tier_index if tier_index is not None else next(
            (i for i, (_, lo, hi, _) in enumerate(REBIRTH_TIERS) if lo <= min(40, done + 1) <= hi), 0)
        self.add_item(CycleSelect(cycle))
        for i, (label, *_rest) in enumerate(REBIRTH_TIERS):
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary if i == self.tier_index else discord.ButtonStyle.secondary, row=1)
            btn.callback = self._make_tier_cb(i)
            self.add_item(btn)
        nxt = min(40, done + 1)
        mark = discord.ui.Button(label=f"Mark rebirth {nxt} done", style=discord.ButtonStyle.success, emoji="✅", row=2, disabled=done >= 40)
        mark.callback = self._mark
        undo = discord.ui.Button(label="Undo", style=discord.ButtonStyle.secondary, row=2, disabled=done == 0)
        undo.callback = self._undo
        reset = discord.ui.Button(label="Reset path", style=discord.ButtonStyle.danger, row=2, disabled=done == 0)
        reset.callback = self._reset
        self.add_item(mark)
        self.add_item(undo)
        self.add_item(reset)

    def _make_tier_cb(self, index: int):
        async def cb(interaction: discord.Interaction):
            await interaction.response.edit_message(embed=rebirth_embed(self.cycle, index, store.rebirth_done(self.user_id, self.cycle)),
                                                    view=RebirthView(self.user_id, self.cycle, index))
        return cb

    async def _mark(self, interaction: discord.Interaction):
        store.set_rebirth_done(self.user_id, self.cycle, self.done + 1)
        await self._refresh(interaction)

    async def _undo(self, interaction: discord.Interaction):
        store.set_rebirth_done(self.user_id, self.cycle, self.done - 1)
        await self._refresh(interaction)

    async def _reset(self, interaction: discord.Interaction):
        store.set_rebirth_done(self.user_id, self.cycle, 0)
        await self._refresh(interaction)

    async def _refresh(self, interaction: discord.Interaction):
        done = store.rebirth_done(self.user_id, self.cycle)
        await interaction.response.edit_message(embed=rebirth_embed(self.cycle, self.tier_index, done),
                                                view=RebirthView(self.user_id, self.cycle, self.tier_index))


class CycleSelect(discord.ui.Select):
    def __init__(self, current: int):
        super().__init__(placeholder="Choose cycle", row=0,
                         options=[discord.SelectOption(label=p["name"], value=str(p["id"]), default=p["id"] == current) for p in REBIRTH_PATHS])

    async def callback(self, interaction: discord.Interaction):
        cycle = int(self.values[0])
        uid = str(interaction.user.id)
        await interaction.response.edit_message(embed=rebirth_embed(cycle, 0, store.rebirth_done(uid, cycle)), view=RebirthView(uid, cycle, 0))


@rebirth_group.command(name="path", description="Show a cycle with your progress")
@app_commands.describe(cycle="Cycle 1–6")
async def rebirth_path(interaction: discord.Interaction, cycle: app_commands.Range[int, 1, 6] = 1):
    uid = str(interaction.user.id)
    done = store.rebirth_done(uid, cycle)
    view = RebirthView(uid, cycle)
    await interaction.response.send_message(embed=rebirth_embed(cycle, view.tier_index, done), view=view, ephemeral=True)


@rebirth_group.command(name="nova", description="Nova crystals for N super rebirths")
@app_commands.describe(count="Number of super rebirths", level="Rebirth you super rebirth at (12–40)")
async def rebirth_nova(interaction: discord.Interaction, count: app_commands.Range[int, 1, 100000], level: app_commands.Range[int, 12, 40] = 40):
    regular_per = sum(rb for n, (rb, _, _, _) in REBIRTH_REWARDS.items() if n <= level)
    super_per = REBIRTH_REWARDS[level][1]
    total = (regular_per + super_per) * count
    e = embed("💎 Nova calculator", f"**{fmt_num(total)}** Nova crystals", 0xA78BFA)
    e.add_field(name="Super rebirths", value=fmt_num(count), inline=True)
    e.add_field(name="At rebirth", value=str(level), inline=True)
    e.add_field(name="Per cycle", value=f"{fmt_num(regular_per)} + {fmt_num(super_per)}", inline=True)
    await interaction.response.send_message(embed=e, ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# /admin  (events and announcements)
# ──────────────────────────────────────────────────────────────────────────────

admin_group = app_commands.Group(name="admin", description="Admin tools", default_permissions=discord.Permissions(administrator=True))
tree.add_command(admin_group)


@admin_group.command(name="announce", description="Post an announcement to this channel")
@app_commands.choices(kind=[app_commands.Choice(name=k, value=k) for k in ["news", "update", "patch", "event", "maintenance"]])
async def admin_announce(interaction: discord.Interaction, kind: app_commands.Choice[str], title: str, body: str = "", pinned: bool = False):
    if not is_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    store.add_announcement(title, body, kind.value, pinned, interaction.user.display_name)
    colors = {"news": 0x58E6F7, "update": 0xB7F34A, "patch": 0xD38CFF, "event": 0xFF9F43, "maintenance": 0xF44336}
    e = embed(f"📢 {title}", body, colors[kind.value])
    e.set_author(name=f"{kind.name.upper()} · {interaction.user.display_name}")
    await interaction.response.send_message(embed=e)


@admin_group.command(name="event", description="Add an event (times in UTC, ISO format)")
@app_commands.describe(name="Event name", starts="Start, e.g. 2026-10-24 16:00", ends="End, e.g. 2026-10-25 16:00", intro="What's happening")
async def admin_event(interaction: discord.Interaction, name: str, starts: str, ends: str, intro: str = ""):
    if not is_admin(interaction):
        return await interaction.response.send_message("Admins only.", ephemeral=True)
    try:
        start = datetime.fromisoformat(starts).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(ends).replace(tzinfo=timezone.utc)
    except ValueError:
        return await interaction.response.send_message("Use dates like `2026-10-24 16:00`.", ephemeral=True)
    if end <= start:
        return await interaction.response.send_message("The end must be after the start.", ephemeral=True)
    store.save_event({"id": uuid.uuid4().hex[:10], "name": name, "starts_at": start.isoformat(), "ends_at": end.isoformat(),
                      "color": 0xFF4D6D, "intro": intro, "details": [], "boosts": []})
    await interaction.response.send_message(embed=embed("✅ Event added", f"**{name}** {discord_ts(start)} → {discord_ts(end)}", 0x42FF8C), ephemeral=True)


@tree.command(name="events", description="Upcoming and live events")
async def events_cmd(interaction: discord.Interaction):
    now = utc_now()
    rows = store.events()
    e = embed("🎉 Events", "Live and upcoming events.", 0xFF9F43)
    shown = 0
    for r in rows:
        start, end = datetime.fromisoformat(r["starts_at"]), datetime.fromisoformat(r["ends_at"])
        if end < now:
            continue
        status = "🔴 Live" if start <= now else "⏳ Upcoming"
        when = f"Ends {discord_ts(end)}" if start <= now else f"Starts {discord_ts(start)}"
        e.add_field(name=f"{status} · {r['name']}", value=f"{when}\n{r['intro'] or ''}"[:1000], inline=False)
        shown += 1
    if not shown:
        e.description = "No events scheduled."
    await interaction.response.send_message(embed=e)


@tree.command(name="news", description="Latest announcements")
async def news_cmd(interaction: discord.Interaction):
    items = store.announcements(5)
    if not items:
        return await interaction.response.send_message("No announcements yet.", ephemeral=True)
    e = embed("📰 Announcements", "", 0x58E6F7)
    for a in items:
        posted = datetime.fromisoformat(a["posted_at"])
        e.add_field(name=("📌 " if a["pinned"] else "") + a["title"], value=f"{a['body'] or ''}\n{discord_ts(posted, 'R')}"[:1000], inline=False)
    await interaction.response.send_message(embed=e, ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# Help and scheduled alerts
# ──────────────────────────────────────────────────────────────────────────────

@tree.command(name="help", description="List all bot commands")
async def help_cmd(interaction: discord.Interaction):
    e = embed("🤖 Droid Tycoon bot", "Everything is private to you unless noted.", 0x58E6F7)
    e.add_field(name="Calculators", value="`/calc craft` `/calc afk` `/math`", inline=False)
    e.add_field(name="Timers", value="`/timers` · spawns and missions", inline=False)
    e.add_field(name="Progress", value="`/droidex` `/profile` `/session log|list` `/rebirth path|nova`", inline=False)
    e.add_field(name="Trait simulator", value="`/trait add|roll|settings|ids`", inline=False)
    e.add_field(name="Reference", value="`/fusion` `/upgrade` `/events` `/news`", inline=False)
    e.add_field(name="Admin", value="`/admin announce` `/admin event`", inline=False)
    await interaction.response.send_message(embed=e, ephemeral=True)


@tasks.loop(minutes=1)
async def alert_loop():
    if not ALERT_CHANNEL_ID:
        return
    channel = bot.get_channel(int(ALERT_CHANNEL_ID))
    if channel is None:
        return
    now = utc_now()
    for name, s in SPAWN_SCHEDULES.items():
        nxt = next_spawn(s["interval_min"], s["offset_min"], now)
        if 0 <= (nxt - now).total_seconds() < 60 and not getattr(alert_loop, f"sent_{name}", None) == nxt:
            setattr(alert_loop, f"sent_{name}", nxt)
            await channel.send(embed=embed(f"🔔 {name} incoming", f"Spawns {discord_ts(nxt, 'R')}", s["color"]))


@bot.event
async def on_ready():
    bot.add_view(TimerView())
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    if ALERT_CHANNEL_ID and not alert_loop.is_running():
        alert_loop.start()
    print(f"Logged in as {bot.user} (commands synced)")


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in your .env file or environment.")
    bot.run(TOKEN)