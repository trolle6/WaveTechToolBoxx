import disnake
from disnake.ext import commands
import json
import boto3
from tempfile import NamedTemporaryFile
from enum import Enum
import random
from typing import Union
import asyncio
from pydub import AudioSegment
import atexit

# Load config.json
try:
    with open('config.json', 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    print("The config.json file was not found.")
    exit()
except json.JSONDecodeError:
    print("Error parsing config.json. Please make sure it is a valid JSON file.")
    exit()

TOKEN = config['discord']['token']
intents = disnake.Intents.all()
bot = commands.Bot(command_prefix=config['discord']['command_prefix'], intents=intents)

# Define some blocks for random palette generation
blocks = [
    "Stone", "Dirt", "Grass Block", "Cobblestone", "Sand", "Gravel",
    "Gold Ore", "Iron Ore", "Coal Ore", "Oak Log", "Spruce Log", "Birch Log"
    # Add more blocks as needed
]

# Define color groups
color_groups = {
    "monochrome": ["Black", "Gray", "White"],
    "warm": ["Red", "Orange", "Yellow"],
    "cool": ["Green", "Lime"],
    "blues": ["Dark Blue", "Light Blue"],
    "earth": ["Brown", "Skin"]
}

class ColorGroup(str, Enum):
    Monochrome = "monochrome"
    Warm = "warm"
    Cool = "cool"
    Blues = "blues"
    Earth = "earth"

@bot.event
async def on_ready():
    print("Bot is ready.")
    channel = bot.get_channel(926896065410637874)  # Replace YOUR_CHANNEL_ID with the ID of the channel
    await channel.send("ToolBox Active.")

@bot.event
async def on_disconnecting():
    print("Bot is shutting down.")
    channel = bot.get_channel(926896065410637874)  # Replace YOUR_CHANNEL_ID with the ID of the channel
    await channel.send("ToolBox Deactive.")

@bot.slash_command(description="Generate a color palette.")
async def color_palette(ctx, color_group: str, number_of_colors: int = 5):
    try:
        color_group = ColorGroup[color_group]
    except KeyError:
        await ctx.send("Invalid color group.")
        return

    group_colors = color_groups[color_group.value]

    if number_of_colors < 1 or number_of_colors > len(group_colors):
        await ctx.send(f"Please choose a number between 1 and {len(group_colors)}.")
        return

    start_index = random.randint(0, len(group_colors) - number_of_colors)
    selected_colors = group_colors[start_index:start_index + number_of_colors]
    palette_text = "\n".join(selected_colors)

    await ctx.send(f"Here's your color palette:\n{palette_text}")


@bot.slash_command(description="Generate a random block palette.")
async def palette(ctx, number_of_blocks: int = 5):
    if number_of_blocks < 1 or number_of_blocks > len(blocks):
        await ctx.send(f"Please choose a number between 1 and {len(blocks)}.")
        return

    random_palette = random.sample(blocks, number_of_blocks)
    palette_text = "\n".join(random_palette)

    await ctx.send(f"Here's your random block palette:\n{palette_text}")


# Configure Polly client
polly_client = boto3.Session(
    aws_access_key_id=config['aws']['access_key_id'],
    aws_secret_access_key=config['aws']['secret_access_key'],
    region_name=config['aws']['region_name']
).client('polly')


async def speak(text, voice_client):
    print("Starting to speak...")
    await asyncio.sleep(0)  # Delay Before Speaking

    # Load the quiet sound
    quiet_sound = AudioSegment.from_wav("yt1s_nYWSz5R.wav")

    try:
        response = polly_client.synthesize_speech(
            VoiceId='Matthew',
            OutputFormat='mp3',
            Text=text
        )
        with NamedTemporaryFile(delete=False, suffix='.mp3') as file:
            file.write(response['AudioStream'].read())
            file.flush()

            # Concatenate the quiet sound and the speech
            speech_sound = AudioSegment.from_mp3(file.name)
            combined_sound = quiet_sound + speech_sound
            combined_sound.export("combined_sound.mp3", format="mp3")

            source = disnake.FFmpegPCMAudio("combined_sound.mp3")  # Use the combined sound file
            voice_client.play(source, after=lambda e: print('done', e))
    except Exception as e:
        print(f"An error occurred: {e}")

    print("Finished speaking.")


class Dimension(str, Enum):
    Nether = 'nether'
    Overworld = 'overworld'

@bot.slash_command(description="Calculates the coordinates for a Nether portal.")
async def portal(ctx, x: int, z: int, dimension: Dimension):
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

# Todo list implementation
todo_list = {}

@bot.slash_command(description="Add a job to your todo list.")
async def add(ctx, *, job: str):
    author_id = str(ctx.author.id)
    if author_id not in todo_list:
        todo_list[author_id] = []
    todo_list[author_id].append(job)
    await ctx.send(f"Added '{job}' to your todo list.")

@bot.slash_command(description="See your todo list.")
async def todo(ctx):
    author_id = str(ctx.author.id)
    user_todo_list = todo_list.get(author_id, [])
    if user_todo_list:
        list_text = "\n".join(f"{index + 1}. {job}" for index, job in enumerate(user_todo_list))
        await ctx.send(f"Your Todo List:\n{list_text}")
    else:
        await ctx.send("Your todo list is empty.")

volume_factors = {
    "items": 1,
    "st": 64,
    "sb": 1728,
    "dcos": 1728 * 54,
    "bulk": 1728 * 27,  # Corrected the 'bulk' unit with its conversion factor to items (1728 * 27)
}
volume_units = {
    "items": "Items",
    "st": "Stack",
    "sb": "Shulker Box",
    "dcos": "Double Chests Of Shulkers (DCOS)",
    "bulk": "Bulk",
    "shulker": "Shulker",  # Add the 'shulker' unit with its conversion factor to items (1728)
}


class VolumeUnit(str, Enum):
    Items = "items"
    Stack = "st"
    Shulker_Box = "sb"
    Double_Chests_Of_Shulkers = "dcos"
    Bulk = "bulk"

@bot.slash_command(description="Calculate different volumes.")
async def calc(ctx, volume_amount: int, volume_unit: VolumeUnit):
    try:
        # Use volume_unit directly, assuming it's already a string matching the keys in volume_factors
        if volume_unit in volume_factors:
            # Get the conversion factor for the specified unit
            conversion_factor = volume_factors[volume_unit]

            # Convert to items
            items_amount = volume_amount * conversion_factor
            # Prepare a response with the converted amounts in different units
            results_text = (
                f"{volume_amount} {volume_units[volume_unit]} is equivalent to:\n"
                f"{round(items_amount / volume_factors['bulk'], 3)} {volume_units['bulk']}\n"
                f"{round(items_amount / volume_factors['dcos'], 3)} {volume_units['dcos']}\n"
                f"{round(items_amount / volume_factors['sb'], 3)} {volume_units['shulker']}\n" # Update 'shulker' here as well
                f"{items_amount} {volume_units['items']}"
            )

            # Send the response
            await ctx.send(results_text)
        else:
            available_units = ", ".join(volume_units.values())
            await ctx.send(f"Invalid volume unit. Available units: {available_units}.")
    except ValueError:
        await ctx.send("Invalid format. Please use '/calc <volume_amount> <volume_unit>' (e.g., '/calc 1 bulk').")

# Voice channel leave function
async def check_voice_channels():
    for guild in bot.guilds:
        for voice_channel in guild.voice_channels:
            if len(voice_channel.members) == 1 and bot.user in voice_channel.members:
                voice_client = voice_channel.guild.voice_client
                if voice_client and voice_client.is_connected():
                    await voice_client.disconnect()

@bot.event
async def on_voice_state_update(member, before, after):
    await check_voice_channels()

@bot.slash_command(description="Make someone do something.")
async def godo(ctx, user: disnake.Member, *, task: str):
    if user == ctx.author:
        await ctx.send("You can't godo yourself!")
    else:
        await ctx.send(f"{user.mention}, Please Godo: {task}")

@bot.event
async def on_message(message):
    await bot.process_commands(message)
    print("Message received.")
    if message.author == bot.user:
        return

    if message.channel.name == "no-mic-bot" and "no-mic" in [role.name for role in message.author.roles]:
        print("Correct role and channel detected.")
        try:
            voice_channel = message.author.voice.channel
            if voice_channel is not None:
                voice_client = message.guild.voice_client
                if voice_client and voice_client.is_connected():
                    await speak(message.content, voice_client)
                else:
                    voice_client = await voice_channel.connect()
                    await speak(message.content, voice_client)
            else:
                await message.channel.send("Please join a voice channel.")
        except AttributeError:
            await message.channel.send("You need to be in a voice channel to use this feature.")

class MyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def ping(self, ctx):
        await ctx.send(f'Pong! Latency is {bot.latency * 1000}ms')

target_message_id = 1139202018645377075  # Replace with your specific message ID

# Dictionary to store the reaction users for the specific message
reaction_users_dict = {1139202018645377075: []}  # Update the message ID as required

@bot.event
async def on_raw_reaction_add(payload: disnake.RawReactionActionEvent):
    if payload.message_id == target_message_id:
        user = bot.get_user(payload.user_id)
        if user and user != bot.user and user not in reaction_users_dict[target_message_id]:
            reaction_users_dict[target_message_id].append(user)

@bot.slash_command(description="Pick a Secret Santa from the users who reacted to the target message.")
async def pick_secret_santa(ctx):
    reaction_users = reaction_users_dict.get(target_message_id, [])
    if reaction_users:
        picked_user = random.choice(reaction_users)
        await ctx.send(f"The picked Secret Santa is {picked_user.mention}!")
        reaction_users_dict[target_message_id].remove(picked_user)  # Optional: remove the picked user so they won't be picked again
    else:
        await ctx.send("No users have reacted to the target message.")


bot.add_cog(MyCog(bot))
bot.run(TOKEN)