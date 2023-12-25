import random
from disnake.ext import commands

class SecretSantaCog(commands.Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.assignments = {}  # Dictionary to store Secret Santa assignments

    @commands.Cog.listener()
    async def on_ready(self):
        print("SecretSantaCog is ready.")

    @commands.slash_command(name="start_secretsanta", description="Start the Secret Santa event.")
    async def start_secretsanta(self, ctx):
        await ctx.response.defer()

        guild_id = int(self.config['discord']['guild_id'])
        secret_santa_role_id = int(self.config['discord']['secret_santa_role_id'])  # Fetch the role ID from the config

        guild = self.bot.get_guild(guild_id)
        secret_santa_role = guild.get_role(secret_santa_role_id)

        # Fetch members with the Secret Santa role to populate the participants list
        participants = [member.id for member in guild.members if secret_santa_role in member.roles and not member.bot]

        if not participants:
            await ctx.send("No participants to assign. Make sure members have the Secret Santa role.")
            return

        # Shuffle the participants
        random.shuffle(participants)

        # Store the assignments
        for i in range(len(participants)):
            giver = participants[i]
            receiver = participants[(i + 1) % len(participants)]
            self.assignments[giver] = receiver

        # DM the assignments
        for giver, receiver in self.assignments.items():
            giver_user = await self.bot.fetch_user(giver)
            receiver_user = await self.bot.fetch_user(receiver)
            try:
                await giver_user.send(f"Hey, you have {receiver_user.display_name} for Secret Santa!")
            except Exception as e:
                print(f"Failed to send Secret Santa message to {giver_user.display_name} ({giver_user.id}). Error: {e}")

        await ctx.send("Secret Santa assignments have been DM'd to participants.")

    @commands.slash_command(name="get_secretsanta_list", description="Get the Secret Santa assignments list.")
    @commands.has_permissions(administrator=True)  # Ensure only admins can use this
    async def get_secretsanta_list(self, ctx):
        if not self.assignments:
            await ctx.send("Secret Santa assignments have not been made yet.")
            return

        assignment_list = [f"{await self.bot.fetch_user(giver).display_name} -> {await self.bot.fetch_user(receiver).display_name}" for giver, receiver in self.assignments.items()]
        assignments_str = "\n".join(assignment_list)

        try:
            await ctx.author.send(f"Secret Santa Assignments:\n{assignments_str}")
            await ctx.send("Sent the Secret Santa assignments list to your DM.")
        except Exception as e:
            await ctx.send(f"Failed to send DM. Error: {e}")

def setup(bot):
    bot.add_cog(SecretSantaCog(bot, bot.config))
