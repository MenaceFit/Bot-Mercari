"""Notificateurs branchables sur le moteur."""

from .console import ConsoleNotifier
from .discord import DiscordNotifier

__all__ = ["ConsoleNotifier", "DiscordNotifier"]
