#!/usr/bin/env python3
"""
Colony Game Telegram Bot
Suit ta planète, tes ressources et t'envoie des alertes stratégiques en temps réel.
"""

import asyncio
import logging
import json
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters
)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8567510242:AAFhxFz_b34IvmXn_5PJEjrb3ToTqvQh6iY")  # Variable d'env ou remplace directement
YOUR_CHAT_ID = None           # Sera auto-détecté au /start

# ─── DONNÉES DU JEU ──────────────────────────────────────────────────────────
PLANET_TYPES = {
    "metallic":    {"bonus_resource": "metals",  "bonus_rate": 500},
    "crystalline": {"bonus_resource": "crystal", "bonus_rate": 500},
    "gaseous":     {"bonus_resource": "gas",     "bonus_rate": 500},
    "starfield":   {"bonus_resource": "stardust","bonus_rate": 0.25},
}

BUILDING_PRODUCTION = {
    1: 2000, 2: 5000, 3: 9000,
    4: 14000, 5: 22000, 6: 32000, 7: 47000
}

BUILDING_COSTS = {
    1: {"metals": 30000,  "gas": 70000,  "crystal": 100000},
    2: {"metals": 40000,  "gas": 90000,  "crystal": 150000},
    3: {"metals": 50000,  "gas": 110000, "crystal": 200000},
    4: {"metals": 60000,  "gas": 130000, "crystal": 200000},
    5: {"metals": 70000,  "gas": 150000, "crystal": 200000},
    6: {"metals": 80000,  "gas": 170000, "crystal": 250000},
    7: {"metals": 100000, "gas": 200000, "crystal": 300000},
}

STARDUST_COSTS = {
    1: {"metals": 100000, "gas": 100000, "crystal": 100000},
    2: {"metals": 300000, "gas": 300000, "crystal": 300000},
    3: {"metals": 500000, "gas": 500000, "crystal": 500000},
}

STARDUST_PRODUCTION = {1: 1, 2: 2, 3: 3}

LEADERBOARD_POINTS = {
    "utility_building": {1: 200, 2: 500, 3: 1000, 4: 2000, 5: 3000, 6: 5000, 7: 8000},
    "stardust_building": {1: 500, 2: 1000, 3: 3000},
    "stardust_per_unit": 5000,
    "utility_per_unit": 1,
}

TIER_REWARDS = {
    "Explorer": {"percentile": 1,  "multiplier": 8.0, "sol": 0.80},
    "Diamond":  {"percentile": 5,  "multiplier": 3.0, "sol": 0.30},
    "Platinum": {"percentile": 10, "multiplier": 1.6, "sol": 0.16},
    "Gold":     {"percentile": 30, "multiplier": 1.2, "sol": 0.12},
    "Silver":   {"percentile": 50, "multiplier": 0.6, "sol": 0.06},
    "Bronze":   {"percentile": 70, "multiplier": 0.3, "sol": 0.03},
}

SEASON_DURATION_HOURS = 168  # 7 jours

# ─── STATE ────────────────────────────────────────────────────────────────────
STATE_FILE = "colony_state.json"

def default_state():
    return {
        "planet_type": None,
        "season_start": None,
        "buildings": {
            "metals":   0,
            "gas":      0,
            "crystal":  0,
            "stardust": 0,
        },
        "resources": {
            "metals":   100000,
            "gas":      100000,
            "crystal":  100000,
            "stardust": 0,
        },
        "stardust_collected": 0,
        "last_collect": None,
        "energy": 50,
        "last_energy_refill": None,
        "alerts_sent": [],
        "chat_id": None,
    }

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return default_state()

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)

# ─── CALCULS ──────────────────────────────────────────────────────────────────
def get_production_rate(state, resource):
    """Retourne le taux de production horaire pour une ressource."""
    base = 4000 if resource != "stardust" else 0
    planet_type = state.get("planet_type")

    # Bonus planète
    if planet_type and PLANET_TYPES.get(planet_type, {}).get("bonus_resource") == resource:
        if resource == "stardust":
            base += 0.25
        else:
            base += 500

    # Bonus bâtiment
    building_level = state["buildings"].get(resource, 0)
    if building_level > 0:
        if resource == "stardust":
            base += STARDUST_PRODUCTION.get(building_level, 0)
        else:
            base += BUILDING_PRODUCTION.get(building_level, 0)

    return base

def estimate_resources_now(state):
    """Estime les ressources actuelles basées sur le temps écoulé."""
    if not state.get("last_collect"):
        return state["resources"].copy()

    last = datetime.fromisoformat(state["last_collect"])
    hours_elapsed = (datetime.now() - last).total_seconds() / 3600

    estimated = state["resources"].copy()
    for resource in ["metals", "gas", "crystal", "stardust"]:
        rate = get_production_rate(state, resource)
        estimated[resource] = estimated.get(resource, 0) + (rate * hours_elapsed)

    return estimated

def calculate_points(state):
    """Calcule les points leaderboard estimés."""
    resources = estimate_resources_now(state)
    points = 0

    # Points des ressources collectées
    points += resources.get("metals", 0) * LEADERBOARD_POINTS["utility_per_unit"]
    points += resources.get("gas", 0) * LEADERBOARD_POINTS["utility_per_unit"]
    points += resources.get("crystal", 0) * LEADERBOARD_POINTS["utility_per_unit"]
    points += (state.get("stardust_collected", 0) + resources.get("stardust", 0)) * LEADERBOARD_POINTS["stardust_per_unit"]

    # Points des niveaux de bâtiments
    for resource in ["metals", "gas", "crystal"]:
        lvl = state["buildings"].get(resource, 0)
        if lvl > 0:
            points += LEADERBOARD_POINTS["utility_building"].get(lvl, 0)

    lvl_star = state["buildings"].get("stardust", 0)
    if lvl_star > 0:
        points += LEADERBOARD_POINTS["stardust_building"].get(lvl_star, 0)

    return int(points)

def get_next_action(state):
    """Détermine la prochaine action optimale."""
    resources = estimate_resources_now(state)
    buildings = state["buildings"]

    metals  = resources.get("metals", 0)
    gas     = resources.get("gas", 0)
    crystal = resources.get("crystal", 0)

    # Priorité 1 : construire les bâtiments utilitaires de base
    for res in ["crystal", "gas", "metals"]:
        if buildings.get(res, 0) == 0:
            cost = BUILDING_COSTS[1]
            if metals >= cost["metals"] and gas >= cost["gas"] and crystal >= cost["crystal"]:
                return f"✅ CONSTRUIS maintenant le bâtiment {res.upper()} lvl 1 !"
            else:
                missing = []
                if metals  < cost["metals"]:  missing.append(f"{cost['metals']-metals:.0f} MTL")
                if gas     < cost["gas"]:     missing.append(f"{cost['gas']-gas:.0f} GAS")
                if crystal < cost["crystal"]: missing.append(f"{cost['crystal']-crystal:.0f} CRY")
                time_needed = max(
                    max(0, cost["metals"]  - metals)  / max(get_production_rate(state,"metals"), 1),
                    max(0, cost["gas"]     - gas)     / max(get_production_rate(state,"gas"), 1),
                    max(0, cost["crystal"] - crystal) / max(get_production_rate(state,"crystal"), 1),
                )
                return f"⏳ Construis {res.upper()} lvl 1 dans ~{time_needed:.1f}h (manque: {', '.join(missing)})"

    # Priorité 2 : monter les utilitaires vers lvl 3
    for res in ["crystal", "gas", "metals"]:
        current_lvl = buildings.get(res, 0)
        if current_lvl < 3:
            next_lvl = current_lvl + 1
            cost = BUILDING_COSTS[next_lvl]
            if metals >= cost["metals"] and gas >= cost["gas"] and crystal >= cost["crystal"]:
                return f"✅ UPGRADE {res.upper()} → lvl {next_lvl} maintenant !"
            else:
                time_needed = max(
                    max(0, cost["metals"]  - metals)  / max(get_production_rate(state,"metals"), 1),
                    max(0, cost["gas"]     - gas)     / max(get_production_rate(state,"gas"), 1),
                    max(0, cost["crystal"] - crystal) / max(get_production_rate(state,"crystal"), 1),
                )
                return f"⏳ Upgrade {res.upper()} → lvl {next_lvl} dans ~{time_needed:.1f}h"

    # Priorité 3 : construire Stardust si pas encore fait
    if buildings.get("stardust", 0) == 0:
        cost = STARDUST_COSTS[1]
        if metals >= cost["metals"] and gas >= cost["gas"] and crystal >= cost["crystal"]:
            return "⭐ CONSTRUIS le bâtiment STARDUST lvl 1 maintenant !"
        else:
            time_needed = max(
                max(0, cost["metals"]  - metals)  / max(get_production_rate(state,"metals"), 1),
                max(0, cost["gas"]     - gas)     / max(get_production_rate(state,"gas"), 1),
                max(0, cost["crystal"] - crystal) / max(get_production_rate(state,"crystal"), 1),
            )
            return f"⏳ Stardust lvl 1 disponible dans ~{time_needed:.1f}h"

    # Priorité 4 : upgrade utilitaires vers lvl 5
    for res in ["crystal", "gas", "metals"]:
        current_lvl = buildings.get(res, 0)
        if current_lvl < 5:
            next_lvl = current_lvl + 1
            cost = BUILDING_COSTS[next_lvl]
            if metals >= cost["metals"] and gas >= cost["gas"] and crystal >= cost["crystal"]:
                return f"✅ UPGRADE {res.upper()} → lvl {next_lvl} !"

    # Priorité 5 : upgrade Stardust
    star_lvl = buildings.get("stardust", 0)
    if star_lvl < 3:
        next_lvl = star_lvl + 1
        cost = STARDUST_COSTS[next_lvl]
        if metals >= cost["metals"] and gas >= cost["gas"] and crystal >= cost["crystal"]:
            return f"⭐ UPGRADE STARDUST → lvl {next_lvl} maintenant !"
        else:
            time_needed = max(
                max(0, cost["metals"]  - metals)  / max(get_production_rate(state,"metals"), 1),
                max(0, cost["gas"]     - gas)     / max(get_production_rate(state,"gas"), 1),
                max(0, cost["crystal"] - crystal) / max(get_production_rate(state,"crystal"), 1),
            )
            return f"⏳ Stardust lvl {next_lvl} dans ~{time_needed:.1f}h"

    # Priorité 6 : monter utilitaires vers lvl 7
    for res in ["crystal", "gas", "metals"]:
        current_lvl = buildings.get(res, 0)
        if current_lvl < 7:
            next_lvl = current_lvl + 1
            cost = BUILDING_COSTS[next_lvl]
            if metals >= cost["metals"] and gas >= cost["gas"] and crystal >= cost["crystal"]:
                return f"✅ UPGRADE {res.upper()} → lvl {next_lvl} !"

    return "🏆 Tout est au maximum ! Continue de collecter et mine tes énergies toutes les 14h."

def get_season_progress(state):
    if not state.get("season_start"):
        return None, None
    start = datetime.fromisoformat(state["season_start"])
    end = start + timedelta(hours=SEASON_DURATION_HOURS)
    remaining = end - datetime.now()
    if remaining.total_seconds() < 0:
        return 100, timedelta(0)
    progress = 100 * (1 - remaining.total_seconds() / (SEASON_DURATION_HOURS * 3600))
    return progress, remaining

def format_number(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return f"{n:.0f}"

# ─── MESSAGES ────────────────────────────────────────────────────────────────
def build_status_message(state):
    resources = estimate_resources_now(state)
    points = calculate_points(state)
    next_action = get_next_action(state)
    progress, remaining = get_season_progress(state)

    planet_emoji = {"metallic": "🪨", "crystalline": "💎", "gaseous": "🌫️", "starfield": "⭐"}.get(state.get("planet_type", ""), "🪐")
    planet_name = (state.get("planet_type") or "Non défini").capitalize()

    # Saison
    if remaining:
        days = remaining.days
        hours = remaining.seconds // 3600
        mins = (remaining.seconds % 3600) // 60
        season_str = f"⏰ Temps restant : {days}j {hours}h {mins}m\n📊 Progression : {progress:.0f}%"
    else:
        season_str = "🔴 Saison non démarrée — lance /start_season"

    # Bâtiments
    b = state["buildings"]
    buildings_str = (
        f"  🔩 Métaux   : lvl {b.get('metals',0)}/7 — {format_number(get_production_rate(state,'metals'))}/hr\n"
        f"  ⚡ Gaz      : lvl {b.get('gas',0)}/7 — {format_number(get_production_rate(state,'gas'))}/hr\n"
        f"  💎 Cristaux : lvl {b.get('crystal',0)}/7 — {format_number(get_production_rate(state,'crystal'))}/hr\n"
        f"  ⭐ Stardust : lvl {b.get('stardust',0)}/3 — {get_production_rate(state,'stardust'):.2f}/hr"
    )

    # Ressources estimées
    resources_str = (
        f"  🔩 Métaux   : {format_number(resources.get('metals',0))}\n"
        f"  ⚡ Gaz      : {format_number(resources.get('gas',0))}\n"
        f"  💎 Cristaux : {format_number(resources.get('crystal',0))}\n"
        f"  ⭐ Stardust : {resources.get('stardust',0):.1f} (+{state.get('stardust_collected',0):.1f} collectés)"
    )

    # Énergie
    energy_str = f"⚡ Énergie : {state.get('energy', 50)}/50"
    if state.get("last_energy_refill"):
        last_refill = datetime.fromisoformat(state["last_energy_refill"])
        hours_since = (datetime.now() - last_refill).total_seconds() / 3600
        next_refill = max(0, 14 - hours_since)
        energy_str += f" (recharge dans {next_refill:.1f}h)"

    # Points et tier estimé
    tier = "Bronze"
    for t_name, t_info in TIER_REWARDS.items():
        tier = t_name
        break  # On ne peut pas savoir le percentile exact sans le leaderboard complet

    msg = (
        f"🪐 *COLONY — Tableau de bord*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{planet_emoji} Planète : *{planet_name}*\n\n"
        f"📅 *SAISON*\n{season_str}\n\n"
        f"🏗️ *BÂTIMENTS*\n{buildings_str}\n\n"
        f"📦 *RESSOURCES ESTIMÉES*\n{resources_str}\n\n"
        f"{energy_str}\n\n"
        f"🏆 *POINTS ESTIMÉS : {format_number(points)}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *PROCHAINE ACTION :*\n{next_action}"
    )
    return msg

# ─── HANDLERS ────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state["chat_id"] = update.effective_chat.id
    save_state(state)

    keyboard = [
        [InlineKeyboardButton("🪐 Configurer ma planète", callback_data="setup_planet")],
        [InlineKeyboardButton("▶️ Démarrer la saison", callback_data="start_season")],
        [InlineKeyboardButton("📊 Voir mon statut", callback_data="status")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🚀 *Bienvenue sur Colony Tracker !*\n\n"
        "Je vais suivre ta planète et t'envoyer des alertes pour maximiser tes points.\n\n"
        "Commence par configurer ta planète :",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    msg = build_status_message(state)
    keyboard = [
        [InlineKeyboardButton("🔄 Actualiser", callback_data="status"),
         InlineKeyboardButton("📥 J'ai collecté", callback_data="collected")],
        [InlineKeyboardButton("⛏️ J'ai miné", callback_data="mined"),
         InlineKeyboardButton("🏗️ Upgrade bâtiment", callback_data="upgrade_menu")],
    ]
    await update.message.reply_text(msg, parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(keyboard))

async def cmd_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Marque que tu as collecté les ressources."""
    state = load_state()
    resources = estimate_resources_now(state)

    # Collecte le stardust
    star_collected = resources.get("stardust", 0)
    state["stardust_collected"] = state.get("stardust_collected", 0) + star_collected

    # Remet à zéro les ressources et met à jour le timestamp
    state["resources"] = {
        "metals":   resources.get("metals", 100000),
        "gas":      resources.get("gas", 100000),
        "crystal":  resources.get("crystal", 100000),
        "stardust": 0,
    }
    state["last_collect"] = datetime.now().isoformat()
    save_state(state)

    await update.message.reply_text(
        f"✅ *Collecte enregistrée !*\n\n"
        f"⭐ Stardust collecté : {star_collected:.1f} (total : {state['stardust_collected']:.1f})\n"
        f"🏆 Points estimés : {format_number(calculate_points(state))}",
        parse_mode="Markdown"
    )

async def cmd_mine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enregistre une session de mining."""
    state = load_state()
    resources = estimate_resources_now(state)

    # Calcule la récompense de mining (0.2% de la production journalière par énergie)
    daily_metals  = get_production_rate(state, "metals")  * 24
    daily_gas     = get_production_rate(state, "gas")     * 24
    daily_crystal = get_production_rate(state, "crystal") * 24

    reward_per_mine_metals  = daily_metals  * 0.002
    reward_per_mine_gas     = daily_gas     * 0.002
    reward_per_mine_crystal = daily_crystal * 0.002

    energy_used = min(state.get("energy", 50), 50)

    total_metals  = reward_per_mine_metals  * energy_used
    total_gas     = reward_per_mine_gas     * energy_used
    total_crystal = reward_per_mine_crystal * energy_used

    state["resources"]["metals"]  = resources.get("metals", 0) + total_metals
    state["resources"]["gas"]     = resources.get("gas", 0) + total_gas
    state["resources"]["crystal"] = resources.get("crystal", 0) + total_crystal
    state["energy"] = 0
    state["last_energy_refill"] = datetime.now().isoformat()
    state["last_collect"] = datetime.now().isoformat()
    save_state(state)

    await update.message.reply_text(
        f"⛏️ *Mining effectué ! ({energy_used} énergies)*\n\n"
        f"  🔩 +{format_number(total_metals)} Métaux\n"
        f"  ⚡ +{format_number(total_gas)} Gaz\n"
        f"  💎 +{format_number(total_crystal)} Cristaux\n\n"
        f"⚡ Prochaine recharge d'énergie dans ~14h\n"
        f"🎯 {get_next_action(state)}",
        parse_mode="Markdown"
    )

async def cmd_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Menu d'upgrade des bâtiments."""
    keyboard = [
        [InlineKeyboardButton("🔩 Métaux",   callback_data="upgrade_metals"),
         InlineKeyboardButton("⚡ Gaz",      callback_data="upgrade_gas")],
        [InlineKeyboardButton("💎 Cristaux", callback_data="upgrade_crystal"),
         InlineKeyboardButton("⭐ Stardust", callback_data="upgrade_stardust")],
        [InlineKeyboardButton("❌ Annuler",  callback_data="cancel")],
    ]
    await update.message.reply_text(
        "🏗️ *Quel bâtiment as-tu upgradé ?*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cmd_start_season(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state["season_start"] = datetime.now().isoformat()
    state["last_collect"]  = datetime.now().isoformat()
    save_state(state)

    await update.message.reply_text(
        "🚀 *Saison démarrée !*\n\n"
        "Les alertes automatiques sont activées.\n"
        "Je te contacterai à chaque étape clé.\n\n"
        f"🎯 *Prochaine action :*\n{get_next_action(state)}",
        parse_mode="Markdown"
    )

    # Lance les alertes automatiques
    context.job_queue.run_once(alert_build_first, 5, data=state["chat_id"])
    context.job_queue.run_repeating(periodic_check, interval=3600, first=3600,
                                    data=state["chat_id"])
    context.job_queue.run_repeating(energy_alert, interval=14*3600, first=14*3600,
                                    data=state["chat_id"])

async def cmd_set_planet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🪨 Metallic (+500 MTL/hr)",   callback_data="planet_metallic")],
        [InlineKeyboardButton("💎 Crystalline (+500 CRY/hr)", callback_data="planet_crystalline")],
        [InlineKeyboardButton("🌫️ Gaseous (+500 GAS/hr)",    callback_data="planet_gaseous")],
        [InlineKeyboardButton("⭐ Starfield (+0.25 STAR/hr)", callback_data="planet_starfield")],
    ]
    await update.message.reply_text(
        "🪐 *Quel est le type de ta planète ?*\n\n"
        "_(Vérifie sur playcolony.xyz/play)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Commandes disponibles :*\n\n"
        "/start — Démarrer le bot\n"
        "/status — Voir ton tableau de bord\n"
        "/start\\_season — Lancer le timer de saison\n"
        "/set\\_planet — Définir le type de ta planète\n"
        "/collect — Enregistrer une collecte de ressources\n"
        "/mine — Enregistrer une session de mining (50 énergies)\n"
        "/upgrade — Enregistrer un upgrade de bâtiment\n"
        "/reset — Remettre à zéro (nouvelle saison)\n"
        "/help — Afficher cette aide",
        parse_mode="Markdown"
    )

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = default_state()
    state["chat_id"] = update.effective_chat.id
    save_state(state)
    await update.message.reply_text(
        "🔄 *Reset effectué !*\n\n"
        "Nouvelle saison → /set\\_planet puis /start\\_season",
        parse_mode="Markdown"
    )

# ─── CALLBACKS ───────────────────────────────────────────────────────────────
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    state = load_state()

    if data == "status":
        msg = build_status_message(state)
        keyboard = [
            [InlineKeyboardButton("🔄 Actualiser",     callback_data="status"),
             InlineKeyboardButton("📥 J'ai collecté", callback_data="collected")],
            [InlineKeyboardButton("⛏️ J'ai miné",     callback_data="mined"),
             InlineKeyboardButton("🏗️ Upgrade",       callback_data="upgrade_menu")],
        ]
        await query.edit_message_text(msg, parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "collected":
        resources = estimate_resources_now(state)
        star = resources.get("stardust", 0)
        state["stardust_collected"] = state.get("stardust_collected", 0) + star
        state["resources"] = {**resources, "stardust": 0}
        state["last_collect"] = datetime.now().isoformat()
        save_state(state)
        await query.edit_message_text(
            f"✅ Collecte enregistrée !\n⭐ +{star:.1f} Stardust\n"
            f"🏆 Points : {format_number(calculate_points(state))}\n\n"
            f"🎯 {get_next_action(state)}",
            parse_mode="Markdown"
        )

    elif data == "mined":
        await query.edit_message_text(
            "⛏️ *Mining enregistré !*\n\nUtilise /mine pour enregistrer le détail.",
            parse_mode="Markdown"
        )

    elif data == "upgrade_menu":
        keyboard = [
            [InlineKeyboardButton("🔩 Métaux",   callback_data="upgrade_metals"),
             InlineKeyboardButton("⚡ Gaz",      callback_data="upgrade_gas")],
            [InlineKeyboardButton("💎 Cristaux", callback_data="upgrade_crystal"),
             InlineKeyboardButton("⭐ Stardust", callback_data="upgrade_stardust")],
        ]
        await query.edit_message_text(
            "🏗️ *Quel bâtiment as-tu upgradé ?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("upgrade_"):
        resource = data.replace("upgrade_", "")
        current = state["buildings"].get(resource, 0)
        max_lvl = 3 if resource == "stardust" else 7
        if current < max_lvl:
            state["buildings"][resource] = current + 1
            save_state(state)
            emoji = {"metals": "🔩", "gas": "⚡", "crystal": "💎", "stardust": "⭐"}.get(resource, "🏗️")
            await query.edit_message_text(
                f"{emoji} *{resource.capitalize()} → lvl {current+1} !*\n\n"
                f"🎯 {get_next_action(state)}",
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(f"✅ {resource.capitalize()} est déjà au niveau maximum !")

    elif data.startswith("planet_"):
        planet = data.replace("planet_", "")
        state["planet_type"] = planet
        save_state(state)
        emoji = {"metallic": "🪨", "crystalline": "💎", "gaseous": "🌫️", "starfield": "⭐"}.get(planet, "🪐")
        await query.edit_message_text(
            f"{emoji} *Planète {planet.capitalize()} configurée !*\n\n"
            f"Lance /start\\_season quand la saison commence.",
            parse_mode="Markdown"
        )

    elif data == "setup_planet":
        keyboard = [
            [InlineKeyboardButton("🪨 Metallic",   callback_data="planet_metallic")],
            [InlineKeyboardButton("💎 Crystalline", callback_data="planet_crystalline")],
            [InlineKeyboardButton("🌫️ Gaseous",    callback_data="planet_gaseous")],
            [InlineKeyboardButton("⭐ Starfield",   callback_data="planet_starfield")],
        ]
        await query.edit_message_text(
            "🪐 *Type de ta planète ?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "start_season":
        state["season_start"] = datetime.now().isoformat()
        state["last_collect"] = datetime.now().isoformat()
        save_state(state)
        context.job_queue.run_once(alert_build_first, 5, data=query.message.chat_id)
        context.job_queue.run_repeating(periodic_check, interval=3600, first=3600,
                                        data=query.message.chat_id)
        context.job_queue.run_repeating(energy_alert, interval=14*3600, first=14*3600,
                                        data=query.message.chat_id)
        await query.edit_message_text(
            f"🚀 *Saison démarrée !*\n\n"
            f"🎯 {get_next_action(state)}\n\n"
            f"⚡ Je t'alerterai toutes les heures et quand tu dois miner.",
            parse_mode="Markdown"
        )

# ─── ALERTES AUTOMATIQUES ─────────────────────────────────────────────────────
async def alert_build_first(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data
    state = load_state()
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🚀 *LA SAISON COMMENCE !*\n\n"
            "📋 *Plan immédiat :*\n"
            "1️⃣ Construis d'abord le bâtiment CRYSTAL lvl 1\n"
            "   → Coût : 30k MTL + 70k GAS + 100k CRY\n\n"
            "2️⃣ Attends ~3h → construis GAS lvl 1\n"
            "3️⃣ Attends ~2h → construis METALS lvl 1\n\n"
            "⛏️ *Utilise tes 50 énergies sur Crystal maintenant !*\n\n"
            f"🎯 {get_next_action(state)}"
        ),
        parse_mode="Markdown"
    )

async def periodic_check(context: ContextTypes.DEFAULT_TYPE):
    """Vérifie toutes les heures et envoie des alertes si nécessaire."""
    chat_id = context.job.data
    state = load_state()

    if not state.get("season_start"):
        return

    next_action = get_next_action(state)

    # Alerte si une action est disponible maintenant
    if next_action.startswith("✅"):
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔔 *ACTION DISPONIBLE !*\n\n{next_action}\n\nUtilise /upgrade pour l'enregistrer.",
            parse_mode="Markdown"
        )

    # Alerte fin de saison
    progress, remaining = get_season_progress(state)
    if remaining and remaining.total_seconds() < 6 * 3600:  # moins de 6h
        hours = remaining.seconds // 3600
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"⚠️ *MOINS DE {hours}H AVANT LA FIN DE SAISON !*\n\n"
                f"🏆 Points actuels : {format_number(calculate_points(state))}\n"
                f"📥 Collecte max tes ressources !\n"
                f"⭐ Collecte tout ton Stardust !\n"
                f"🏗️ Monte les bâtiments au max pour les points de niveau !"
            ),
            parse_mode="Markdown"
        )

async def energy_alert(context: ContextTypes.DEFAULT_TYPE):
    """Alerte pour miner toutes les 14h."""
    chat_id = context.job.data
    state = load_state()
    state["energy"] = 50
    save_state(state)

    next_action = get_next_action(state)
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "⚡ *ÉNERGIE RECHARGÉE — MINE MAINTENANT !*\n\n"
            "Tu as 50 énergies disponibles.\n"
            "Mine la ressource dont tu as le plus besoin.\n\n"
            f"🎯 Conseil stratégique : {next_action}\n\n"
            "Tape /mine pour enregistrer ta session."
        ),
        parse_mode="Markdown"
    )

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )

    app = Application.builder().token(BOT_TOKEN).build()

    # Commandes
    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("status",       cmd_status))
    app.add_handler(CommandHandler("collect",      cmd_collect))
    app.add_handler(CommandHandler("mine",         cmd_mine))
    app.add_handler(CommandHandler("upgrade",      cmd_upgrade))
    app.add_handler(CommandHandler("start_season", cmd_start_season))
    app.add_handler(CommandHandler("set_planet",   cmd_set_planet))
    app.add_handler(CommandHandler("reset",        cmd_reset))
    app.add_handler(CommandHandler("help",         cmd_help))

    # Callbacks boutons
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🚀 Colony Bot démarré ! Ctrl+C pour arrêter.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
