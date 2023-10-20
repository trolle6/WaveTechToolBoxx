from disnake.ext import commands

class AdminCog(commands.Cog, name="Admin Commands"):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.is_owner()  # This ensures that only the bot owner can use this command
    async def some_admin_command(self, ctx):
        await ctx.send("Admin command executed.")

    @commands.command()
    @commands.is_owner()
    async def kick(self, ctx, member: commands.MemberConverter):
        await member.kick()
        await ctx.send(f"{member.name} has been kicked.")

    @commands.command()
    @commands.is_owner()
    async def ban(self, ctx, member: commands.MemberConverter):
        await member.ban()
        await ctx.send(f"{member.name} has been banned.")

    @commands.command()
    @commands.is_owner()
    async def serverinfo(self, ctx):
        guild = ctx.guild
        await ctx.send(f"Server Name: {guild.name}\nServer Size: {guild.member_count}")

    @commands.command()
    async def ping(self, ctx):
        await ctx.send(f"Pong! Latency is {self.bot.latency * 1000:.2f}ms")

def setup(bot):
    bot.add_cog(AdminCog(bot))
