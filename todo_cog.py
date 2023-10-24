import disnake
from disnake.ext import commands

class TodoCog(commands.Cog, name="Todo"):
    def __init__(self, bot):
        self.bot = bot
        self.todo_list = {}  # Initializing the to-do list

    @commands.slash_command(description="Add a job to your to-do list.")
    async def add(self, ctx, *, job: str):
        author_id = str(ctx.author.id)
        if author_id not in self.todo_list:
            self.todo_list[author_id] = []
        self.todo_list[author_id].append(job)
        await ctx.send(f"Added '{job}' to your to-do list.")

    @commands.slash_command(description="See your to-do list.")
    async def todo(self, ctx):
        author_id = str(ctx.author.id)
        user_todo_list = self.todo_list.get(author_id, [])
        if user_todo_list:
            list_text = "\n".join(f"{index + 1}. {job}" for index, job in enumerate(user_todo_list))
            await ctx.send(f"Your Todo List:\n{list_text}")
        else:
            await ctx.send("Your to-do list is empty.")

    @commands.slash_command(description="Make someone do something.")
    async def godo(self, ctx, user: disnake.Member, *, task: str):
        if user == ctx.author:
            await ctx.send("You can't godo yourself!")
        else:
            await ctx.send(f"{user.mention}, Please Godo: {task}")


def setup(bot):
    bot.add_cog(TodoCog(bot))
