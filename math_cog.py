from disnake.ext import commands
from enum import Enum

class VolumeUnit(str, Enum):
    Items = "items"
    Stack = "st"
    Shulker_Box = "sb"
    Double_Chests_Of_Shulkers = "dcos"
    Bulk = "bulk"

class Dimension(str, Enum):
    Nether = 'nether'
    Overworld = 'overworld'

class MathCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.volume_factors = {
            "items": 1,
            "st": 64,
            "sb": 1728,
            "dcos": 1728 * 54,
            "bulk": 1728 * 27,
        }

    @commands.slash_command(description="Calculate different volumes.")
    async def calc(self, ctx, volume_amount: int, volume_unit: VolumeUnit):
        # TODO: Implement the function or remove it
        pass

    @commands.slash_command(description="Calculates the coordinates for a Nether portal.")
    async def portal(self, ctx, x: int, z: int, dimension: Dimension):
        dimension_map = {
            Dimension.Nether: 8,
            Dimension.Overworld: 1,
        }
        multiplier = dimension_map.get(dimension)
        if multiplier is not None:
            nether_x = x // multiplier
            nether_z = z // multiplier
            await ctx.send(f"Coordinates for {dimension.title()} portal: X: {nether_x}, Z: {nether_z}")
        else:
            await ctx.send("Invalid dimension. Please use 'nether' or 'overworld'.")

def setup(bot):
    bot.add_cog(MathCog(bot))