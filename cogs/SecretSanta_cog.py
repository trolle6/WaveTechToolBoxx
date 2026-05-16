"""
Secret Santa — combines core helpers and slash-command mixin.

- secret_santa_core.py — state, DMs, shuffle, role helpers, lifecycle
- secret_santa_commands.py — /ss slash commands and event listeners
"""

from disnake.ext import commands

from .secret_santa_core import SecretSantaCore
from .secret_santa_commands import SecretSantaCommandsMixin


class SecretSantaCog(SecretSantaCommandsMixin, SecretSantaCore, commands.Cog):
    """Secret Santa event management."""

    def __init__(self, bot):
        SecretSantaCore.__init__(self, bot)


def setup(bot):
    bot.add_cog(SecretSantaCog(bot))
