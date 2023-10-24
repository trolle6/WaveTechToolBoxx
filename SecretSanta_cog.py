import logging
import random
from disnake.ext import commands

logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')


class SecretSantaCog(commands.Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.participants = []
        self.target_message_id = int(config['discord']['secret_santa_message_id'])
        self.event_started = False  # <-- Add this line
        logging.info("SecretSantaCog initialized")

    @commands.Cog.listener()
    async def on_ready(self):
        logging.info("SecretSantaCog is ready.")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        logging.info(f"Received reaction from user {payload.user_id} on message {payload.message_id}")
        if payload.message_id == self.target_message_id:
            if payload.user_id not in self.participants:
                logging.info(f"Adding user {payload.user_id} to participants")
                self.participants.append(payload.user_id)

    @commands.slash_command(name="start_secretsanta", description="Start the Secret Santa event.")
    async def start_secretsanta(self, ctx):
        if self.event_started:  # <-- Add this block
            await ctx.send("The Secret Santa event has already been started.")
            return

        try:
            await ctx.response.defer()

            logging.info("Received command to start Secret Santa")
            if ctx.author.id not in self.participants:
                logging.info(f"Adding command author {ctx.author.id} to participants")
                self.participants.append(ctx.author.id)

            logging.info(f"Current participants: {self.participants}")

            if not self.participants:
                logging.warning("No participants found.")
                await ctx.send("No participants to assign. Make sure people have reacted to the target message.")
                return

            random.shuffle(self.participants)
            logging.info(f"Shuffled participants: {self.participants}")

            assignments = {}
            for i in range(len(self.participants)):
                giver = self.participants[i]
                receiver = self.participants[(i + 1) % len(self.participants)]
                assignments[giver] = receiver

            for giver, receiver in assignments.items():
                giver_user = await self.bot.fetch_user(giver)
                receiver_user = await self.bot.fetch_user(receiver)
                if giver_user and receiver_user:
                    logging.info(f"Sending DM to {giver_user.id}")
                    try:
                        await giver_user.send(f"Hey, you have {receiver_user.display_name} for Secret Santa!")
                    except Exception as e:
                        logging.error(f"Failed to send DM to {giver_user.id}. Error: {e}")

            self.event_started = True  # <-- Add this line
            await ctx.edit_original_message(content="Secret Santa assignments have been DM'd to participants.")

        except Exception as e:
            logging.error(f"An error occurred: {e}")
            await ctx.edit_original_message(content="An error occurred while executing the command.")

    @commands.slash_command(description="Pick a Secret Santa from the participants.")
    async def pick_secret_santa(self, ctx):
        if self.participants:
            picked_user = random.choice(self.participants)
            picked_user_obj = self.bot.get_user(picked_user)
            await ctx.send(f"The picked Secret Santa is {picked_user_obj.mention}!")
            logging.info(f"Picked user {picked_user} as Secret Santa")
        else:
            await ctx.send("No participants yet.")
            logging.warning("No participants for Secret Santa yet")


def setup(bot):
    logging.info("Setting up SecretSantaCog")
    bot.add_cog(SecretSantaCog(bot, bot.config))
