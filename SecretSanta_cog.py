from disnake.ext import commands
import random


class SecretSantaCog(commands.Cog):
    def __init__(self, bot, config):
        self.bot = bot
        self.participants = []
        self.reaction_users_dict = {}  # Initialize this dictionary
        self.target_message_id = int(config['discord']['secret_santa_message_id'])  # Reading from config.json

    @commands.Cog.listener()
    async def on_ready(self):
        print("SecretSantaCog is ready.")

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if reaction.message.id == self.target_message_id:
            if user.id not in self.participants:
                self.participants.append(user.id)

    @commands.command(name="start_secretsanta")
    async def start_secretsanta(self, ctx):
        if ctx.author.id not in self.participants:
            await ctx.send("You're not a participant!")
            return

        random.shuffle(self.participants)
        assignments = {}

        for i in range(len(self.participants)):
            giver = self.participants[i]
            receiver = self.participants[(i + 1) % len(self.participants)]
            assignments[giver] = receiver

        for giver, receiver in assignments.items():
            giver_user = self.bot.get_user(giver)
            receiver_user = self.bot.get_user(receiver)
            if giver_user and receiver_user:
                await giver_user.send(f"Hey, you have {receiver_user.display_name} for Secret Santa!")

        await ctx.send("Secret Santa assignments have been DM'd to participants.")

    @commands.command()
    async def pick_secret_santa(self, ctx):
        reaction_users = self.reaction_users_dict.get(self.target_message_id, [])
        if reaction_users:
            picked_user = random.choice(reaction_users)
            await ctx.send(f"The picked Secret Santa is {picked_user.mention}!")
            self.reaction_users_dict[self.target_message_id].remove(
                picked_user)  # Remove the picked user so they won't be picked again
        else:
            await ctx.send("No users have reacted to the target message.")


def setup(bot):
    bot.add_cog(SecretSantaCog(bot, bot.config))
