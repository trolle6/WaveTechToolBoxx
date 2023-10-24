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

@commands.slash_command(name="update_config", description="Update the config (Admin Only).")
async def update_config(self, ctx, key: str, value: str):
    # Replace YOUR_DISCORD_ID with your actual Discord ID
    if ctx.author.id == 251835504654286852:
        if key == "target_message_id":
            try:
                self.target_message_id = int(value)
                await ctx.send(f"Configuration updated. New target_message_id is {self.target_message_id}.")
                # Optionally, save this to a file or database for persistence
            except ValueError:
                await ctx.send("Invalid value. Please provide an integer for target_message_id.")
        else:
            await ctx.send("Invalid key. Available keys: target_message_id")
    else:
        await ctx.send("You do not have permission to use this command.")

def setup(bot):
    bot.add_cog(AdminCog(bot))
