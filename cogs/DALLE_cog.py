# **Updated SecretSanta_cog.py**

from disnake.ext import commands


class SecretSantaCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.participants = {}  # Placeholder, load from secret_santa_data.json later
        self.assignments = {}
        self.logger = self.bot.logger
        self.config = self.bot.config  # Get configuration from main bot
        self.load_secret_santa_data()

    def load_secret_santa_data(self):
        # Load the secret_santa_data.json file
        try:
            with open('secret_santa_data.json', 'r') as file:
                data = json.load(file)
                self.participants = data.get("participants", {})
                self.assignments = data.get("assignments", {})
                self.logger.info("Secret Santa data loaded successfully.")
        except FileNotFoundError:
            self.logger.warning("Secret Santa data file not found. Starting with empty data.")
        except json.JSONDecodeError as e:
            self.logger.error(f"Error decoding JSON from secret_santa_data.json: {e}")

    def save_secret_santa_data(self):
        # Save the current state to secret_santa_data.json
        data = {
            "participants": self.participants,
            "assignments": self.assignments
        }
        try:
            with open('secret_santa_data.json', 'w') as file:
                json.dump(data, file, indent=4)
                self.logger.info("Secret Santa data saved successfully.")
        except Exception as e:
            self.logger.error(f"Failed to save Secret Santa data: {e}")

    def cog_unload(self):
        # Handle unloading cog synchronously
        self.logger.info("SecretSantaCog is being unloaded.")
        self.save_secret_santa_data()

    # Commands and methods for managing Secret Santa...
    @commands.slash_command(name="join_secret_santa", description="Join the Secret Santa event.")
    async def join_secret_santa(self, inter):
        if inter.author.id not in self.participants:
            self.participants[inter.author.id] = ""  # Example data structure for participants
            await inter.send("You have successfully joined the Secret Santa event!")
            self.logger.info(f"User {inter.author} joined the Secret Santa event.")
        else:
            await inter.send("You have already joined the Secret Santa event.")

    @commands.slash_command(name="assign_secret_santa", description="Assign Secret Santa matches.")
    async def assign_secret_santa(self, inter):
        # Logic to assign Secret Santa matches goes here
        self.logger.info("Assigning Secret Santa matches...")
        # Assignments code...
        await inter.send("Secret Santa assignments have been made.")

    @commands.slash_command(name="view_assignment", description="View your Secret Santa assignment.")
    async def view_assignment(self, inter):
        if inter.author.id in self.assignments:
            assignment = self.assignments[inter.author.id]
            await inter.send(f"You are assigned to: {assignment}")
        else:
            await inter.send("You do not have an assignment yet.")


# Add setup function
def setup(bot):
    bot.add_cog(SecretSantaCog(bot))


# **Updated VoiceProcessingCog (voice_cog.py)**

from disnake.ext import commands
import asyncio
import os


class VoiceProcessingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_clients = {}
        self.logger = self.bot.logger
        self.audio_path = "path_to_audio_file"  # Placeholder audio path
        self.process_queue_task = None

    def cog_unload(self):
        # Handle unloading cog synchronously
        self.logger.info("VoiceProcessingCog is being unloaded.")
        # Disconnect all voice clients managed by this cog
        for guild_id, voice_client in self.voice_clients.items():
            if voice_client.is_connected():
                asyncio.create_task(voice_client.disconnect())
        # Cancel the process_queue task if it's running
        if self.process_queue_task and not self.process_queue_task.done():
            self.process_queue_task.cancel()
        # Remove audio file if it exists
        if os.path.exists(self.audio_path):
            try:
                os.remove(self.audio_path)
                self.logger.info(f"Removed fixed audio file: {self.audio_path}")
            except Exception as exc:
                self.logger.error(f"Failed to remove fixed audio file: {self.audio_path}. Error: {exc}", exc_info=True)

    @commands.slash_command(name="join_voice_channel", description="Join a voice channel.")
    async def join_voice_channel(self, inter, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel:
            voice_client = await channel.connect()
            self.voice_clients[inter.guild.id] = voice_client
            await inter.send(f"Joined voice channel: {channel.name}")
        else:
            await inter.send("Invalid channel ID.")

    @commands.slash_command(name="leave_voice_channel", description="Leave the current voice channel.")
    async def leave_voice_channel(self, inter):
        voice_client = self.voice_clients.get(inter.guild.id)
        if voice_client and voice_client.is_connected():
            await voice_client.disconnect()
            await inter.send("Disconnected from the voice channel.")
        else:
            await inter.send("Not connected to any voice channel.")


from disnake.ext import commands
import requests
import json


class DALLECog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = self.bot.logger
        self.config = self.bot.config  # Load the bot config for API calls

    def cog_unload(self):
        # Handle unloading cog synchronously
        self.logger.info("DALLECog is being unloaded.")

    # Command to generate images using DALL-E...
    @commands.slash_command(name="generate_image", description="Generate an image using DALL-E.")
    async def generate_image(self, inter, prompt: str):
        await inter.send(f"Generating an image for: {prompt}")
        # Logic to call DALL-E API and send the image to Discord
        try:
            api_key = self.config["openai_api_key"]
            api_url = self.config["dalle"]["api_url"]
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            data = {
                "prompt": prompt,
                "n": 1,
                "size": self.config["dalle"]["settings"]["size"]
            }
            response = requests.post(api_url, headers=headers, json=data)
            if response.status_code == 200:
                image_url = response.json()["data"][0]["url"]
                await inter.send(image_url)
            else:
                await inter.send(f"Failed to generate the image: {response.text}")
        except Exception as e:
            self.logger.error(f"Failed to generate image: {e}")
            await inter.send("Failed to generate the image.")


# Add setup function
def setup(bot):
    bot.add_cog(DALLECog(bot))
