from disnake.ext import commands
import random

class ReactionCog(commands.Cog):
    def __init__(self, bot, target_message_id):
        self.bot = bot
        self.target_message_id = target_message_id  # Initialize with the target_message_id
        self.reaction_users_dict = {}  # Initialize an empty dictionary for reaction users
        self.reaction_users_dict[self.target_message_id] = []  # Initialize an empty list for the target message

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.message_id == self.target_message_id:
            user = self.bot.get_user(payload.user_id)
            if user and user != self.bot.user and user not in self.reaction_users_dict[self.target_message_id]:
                self.reaction_users_dict[self.target_message_id].append(user)

    @commands.command()
    async def pick_secret_santa(self, ctx):
        reaction_users = self.reaction_users_dict.get(self.target_message_id, [])
        if reaction_users:
            picked_user = random.choice(reaction_users)
            await ctx.send(f"The picked Secret Santa is {picked_user.mention}!")
            self.reaction_users_dict[self.target_message_id].remove(picked_user)  # Remove the picked user so they won't be picked again
        else:
            await ctx.send("No users have reacted to the target message.")

def setup(bot):
    # Access the target_message_id from the bot's config attribute
    target_message_id = bot.target_message_id
    bot.add_cog(ReactionCog(bot, target_message_id))
