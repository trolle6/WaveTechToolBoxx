from disnake.ext import commands
import random
from enum import Enum

color_groups = {
    "monochrome": ["Black", "Gray", "White"],
    "warm": ["Red", "Orange", "Yellow"],
    "cool": ["Green", "Lime"],
    "blues": ["Dark Blue", "Light Blue"],
    "earth": ["Brown", "Skin"]
}

class ColorGroup(str, Enum):
    Monochrome = "monochrome"
    Warm = "warm"
    Cool = "cool"
    Blues = "blues"
    Earth = "earth"

blocks = [
    "Stone", "Dirt", "Grass Block", "Cobblestone", "Sand", "Gravel",
    "Gold Ore", "Iron Ore", "Coal Ore", "Oak Log", "Spruce Log", "Birch Log"
]

class ColorCog(commands.Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config

    @commands.slash_command(description="Generate a color palette.")
    async def color_palette(self, ctx, color_group: str, number_of_colors: int = 5):
        try:
            color_group = ColorGroup[color_group]
        except KeyError:
            await ctx.send("Invalid color group.")
            return

        group_colors = color_groups[color_group.value]

        if number_of_colors < 1 or number_of_colors > len(group_colors):
            await ctx.send(f"Please choose a number between 1 and {len(group_colors)}.")
            return

        start_index = random.randint(0, len(group_colors) - number_of_colors)
        selected_colors = group_colors[start_index:start_index + number_of_colors]
        palette_text = "\n".join(selected_colors)

        await ctx.send(f"Here's your color palette:\n{palette_text}")

    @commands.slash_command(description="Generate a random block palette.")
    async def palette(self, ctx, number_of_blocks: int = 5):
        if number_of_blocks < 1 or number_of_blocks > len(blocks):
            await ctx.send(f"Please choose a number between 1 and {len(blocks)}.")
            return

        random_palette = random.sample(blocks, number_of_blocks)
        palette_text = "\n".join(random_palette)

        await ctx.send(f"Here's your random block palette:\n{palette_text}")

def setup(bot):
    bot.add_cog(ColorCog(bot, bot.config))
