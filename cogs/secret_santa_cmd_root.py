"""Secret Santa /ss slash command group root."""
from __future__ import annotations

import disnake
from disnake.ext import commands


class SecretSantaRootMixin:
    """Defines the top-level /ss command group."""

    @commands.slash_command(name="ss")
    async def ss_root(self, inter: disnake.ApplicationCommandInteraction):
        """Secret Santa commands"""
        pass
