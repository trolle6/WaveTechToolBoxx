# minimal_cog.py

import discord
from discord.ext import commands

class MinimalCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.slash_command(name="hello", description="Says hello!")
    async def hello(self, ctx: discord.ApplicationContext):
        await ctx.respond(f"Hello, {ctx.author.display_name}!")

def setup(bot):
    bot.add_cog(MinimalCog(bot))
