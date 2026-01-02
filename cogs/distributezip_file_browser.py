"""
File Browser for DistributeZip - Interactive file selection UI

Provides an interactive file browser using Discord select menus
to make file selection easier than typing file names.
"""

import disnake
from pathlib import Path
from typing import List, Optional, Callable, Awaitable, Tuple


def create_file_browser_view(
    files_dir: Path,
    metadata: dict,
    action_type: str = "get"
) -> Tuple[disnake.Embed, Optional['FileBrowserSelectView']]:
    """Create a file browser view for file selection"""
    files = metadata.get("files", {})
    
    if not files:
        embed = disnake.Embed(
            title="📁 File Browser",
            description="No files available",
            color=disnake.Color.red()
        )
        return embed, None
    
    # Sort files by upload time (newest first)
    sorted_files = sorted(
        files.items(),
        key=lambda x: x[1].get("uploaded_at", 0),
        reverse=True
    )
    
    # Create embed
    action_descriptions = {
        "get": "Select a file to download",
        "remove": "Select a file to remove",
        "browse": "Browse and view files"
    }
    
    embed = disnake.Embed(
        title="📁 File Browser",
        description=f"{action_descriptions.get(action_type, 'Select a file')} from the dropdown menu below",
        color=disnake.Color.blue()
    )
    embed.add_field(
        name="📦 Available Files",
        value=f"{len(files)} file(s) available",
        inline=False
    )
    embed.set_footer(text="💡 Like File Explorer (Windows) or Finder (Mac/Linux) - just click to select!")
    
    # Create view
    view = FileBrowserSelectView(files_dir, metadata, sorted_files, action_type)
    
    return embed, view


class FileSelectMenu(disnake.ui.Select):
    """Select menu for file selection"""
    
    def __init__(self, options: List[disnake.SelectOption], placeholder: str):
        super().__init__(
            placeholder=placeholder,
            options=options,
            min_values=1,
            max_values=1
        )
        self.view: Optional['FileBrowserSelectView'] = None
    
    async def callback(self, inter: disnake.MessageInteraction):
        """Handle file selection"""
        view = self.view
        if not view:
            await inter.response.send_message("❌ View not found", ephemeral=True)
            return
        
        file_id = self.values[0]
        file_data = view.metadata["files"].get(file_id)
        
        if not file_data:
            await inter.response.send_message("❌ File not found in metadata", ephemeral=True)
            return
        
        filename = file_data.get("filename")
        file_path = view.files_dir / filename
        
        if not file_path.exists():
            await inter.response.send_message(
                f"❌ File '{file_data.get('name')}' not found on disk",
                ephemeral=True
            )
            return
        
        # Call the selection handler if set
        if view.selection_handler:
            await view.selection_handler(inter, file_id, file_data, file_path)
        else:
            await inter.response.send_message("❌ No handler configured for file selection", ephemeral=True)


class FileBrowserSelectView(disnake.ui.View):
    """Interactive file browser using Discord select menus"""
    
    def __init__(self, files_dir: Path, metadata: dict, sorted_files: List, action_type: str, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.files_dir = files_dir
        self.metadata = metadata
        self.sorted_files = sorted_files
        self.action_type = action_type
        self.selection_handler: Optional[Callable[[disnake.MessageInteraction, str, dict, Path], Awaitable[None]]] = None
        
        # Limit to 25 options (Discord's max per select menu)
        display_files = sorted_files[:25]
        
        # Create select options
        options = []
        for file_id, file_data in display_files:
            file_name = file_data.get("name", "Unknown")
            size_mb = file_data.get("size", 0) / 1024 / 1024
            
            # Truncate long names (Discord label limit: 100 chars)
            display_name = file_name[:90] + "..." if len(file_name) > 90 else file_name
            
            # Description with size (Discord limit: 100 chars)
            description = f"{size_mb:.2f} MB"[:100]
            
            options.append(
                disnake.SelectOption(
                    label=display_name,
                    value=file_id,
                    description=description,
                    emoji="📦"
                )
            )
        
        # Create and add select menu
        if options:
            placeholder = f"📦 Select a file to {action_type}..."
            select_menu = FileSelectMenu(options, placeholder)
            select_menu.view = self
            self.add_item(select_menu)
    
    async def on_timeout(self):
        """Disable all components when view times out"""
        for item in self.children:
            item.disabled = True
