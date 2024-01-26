import random
from disnake.ext import commands

class SecretSantaCog(commands.Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.assignments = {}  # Dictionary to store Secret Santa assignments

    @commands.slash_command(name="start_secretsanta", description="Start the Secret Santa event.")
    async def start_secretsanta(self, ctx):
        await ctx.response.defer()

        guild_id = int(self.config['discord']['guild_id'])
        secret_santa_role_id = int(self.config['discord']['secret_santa_role_id'])

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
    @commands.has_permissions(administrator=True)
    async def get_secretsanta_list(self, ctx):
        if not self.assignments:
            await ctx.send("Secret Santa assignments have not been made yet.")
            return

        assignment_list = []
        for giver, receiver in self.assignments.items():
            giver_user = await self.bot.fetch_user(giver)
            receiver_user = await self.bot.fetch_user(receiver)
            assignment_list.append(f"{giver_user.display_name} -> {receiver_user.display_name}")

        assignments_str = "\n".join(assignment_list)
        try:
            await ctx.author.send(f"Secret Santa Assignments:\n{assignments_str}")
            await ctx.send("Sent the Secret Santa assignments list to your DM.")
        except Exception as e:
            await ctx.send(f"Failed to send DM. Error: {e}")

    @commands.slash_command(name="start_voting", description="Start voting for the best present.")
    @commands.has_permissions(administrator=True)
    async def start_voting(self, ctx):
        voting_message = "Vote for the best present:\n"
        for i, participant_id in enumerate(self.assignments.keys(), start=1):
            user = await self.bot.fetch_user(participant_id)
            voting_message += f"{i}. {user.display_name}\n"

        message = await ctx.send(voting_message)
        for i in range(1, len(self.assignments) + 1):
            await message.add_reaction(f"{i}\N{COMBINING ENCLOSING KEYCAP}")

    @commands.slash_command(name="end_voting", description="End voting and announce the winner.")
    @commands.has_permissions(administrator=True)
    async def end_voting(self, ctx):
        await ctx.send("Voting ended. Winner to be announced.")

    @commands.slash_command(name="collect_feedback", description="Collect feedback from the Secret Santa event.")
    async def collect_feedback(self, ctx):
        await ctx.send("Feedback collection is not yet implemented.")

    @commands.slash_command(name="customize_event", description="Customize the Secret Santa event settings.")
    async def customize_event(self, ctx, setting: str, value: str):
        await ctx.send(f"Customization for {setting} is set to {value}.")

    @commands.slash_command(name="integrate_event", description="Integrate Secret Santa with other server events.")
    async def integrate_event(self, ctx, event_name: str):
        await ctx.send(f"Integration with {event_name} is not yet implemented.")

    def add_personal_touch(self, message):
        # Logic to add a personal touch to bot messages
        return "🎄 " + message + " 🎁"

    @commands.slash_command(name="confirm_participation", description="Confirm participation in the Secret Santa event.")
    async def confirm_participation(self, ctx, user_id: int):
        user = await self.bot.fetch_user(user_id)
        await ctx.send(f"{user.display_name}'s participation is confirmed.")

def setup(bot):
    bot.add_cog(SecretSantaCog(bot, bot.config))
