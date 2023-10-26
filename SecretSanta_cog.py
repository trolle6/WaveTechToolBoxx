import logging
import random
from disnake.ext import commands

logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')


class SecretSantaCog(commands.Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.config = config
        self.target_message_id = int(self.config['discord']['secret_santa_message_id'])
        logging.info("SecretSantaCog initialized")

    @commands.Cog.listener()
    async def on_ready(self):
        logging.info("SecretSantaCog is ready.")

    @commands.slash_command(name="start_secretsanta", description="Start the Secret Santa event.")
    async def start_secretsanta(self, ctx):
        await ctx.response.defer()

        guild_id = int(self.config['discord']['guild_id'])
        target_channel_id = int(self.config['discord']['channel_id'])

        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(target_channel_id)

        # Fetch the reactors to populate the participants list
        message = await channel.fetch_message(self.target_message_id)
        participants = []
        for reaction in message.reactions:
            async for user in reaction.users():
                if user.id not in participants and not user.bot:
                    participants.append(user.id)

        if not participants:
            await ctx.send("No participants to assign. Make sure people have reacted to the target message.")
            return

        # Shuffle the participants
        random.shuffle(participants)

        assignments = {}
        for i in range(len(participants)):
            giver = participants[i]
            receiver = participants[(i + 1) % len(participants)]
            assignments[giver] = receiver

        # DM the assignments
        for giver, receiver in assignments.items():
            giver_user = await self.bot.fetch_user(giver)
            receiver_user = await self.bot.fetch_user(receiver)
            try:
                await giver_user.send(f"Hey, you have {receiver_user.display_name} for Secret Santa!")
            except Exception as e:
                logging.error(f"Failed to send DM to {giver_user.id}. Error: {e}")

        await ctx.edit_original_message(content="Secret Santa assignments have been DM'd to participants.")


def setup(bot):
    bot.add_cog(SecretSantaCog(bot, bot.config))
