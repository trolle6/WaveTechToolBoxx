"""
File Browser for DistributeZip - Interactive file selection UI

Provides an interactive file browser using Discord select menus
to make file selection easier than typing file names. Supports
get, remove, and browse actions.
"""

import datetime as dt
from pathlib import Path
from typing import Callable, Awaitable, List, Optional, Tuple

import disnake

from .utils import safe_filename_in_dir


def create_file_browser_view(
    files_dir: Path,
    metadata: dict,
    action_type: str = "get"
) -> Tuple[disnake.Embed, Optional["FileBrowserSelectView"]]:
    """Create a file browser embed and view for file selection.

    Args:
        files_dir: Directory containing distributed files.
        metadata: File metadata dict with "files" key.
        action_type: "get", "remove", or "browse" - determines placeholder text.

    Returns:
        Tuple of (embed, view). View is None if no files available.
    """
    files = metadata.get("files") if isinstance(metadata, dict) else None
    if not isinstance(files, dict) or not files:
        embed = disnake.Embed(
            title="📁 File Browser",
            description="No files available",
            color=disnake.Color.red()
        )
        return embed, None
    
    # Sort files by upload time (newest first); skip corrupt metadata entries
    sorted_files = sorted(
        ((fid, data) for fid, data in files.items() if isinstance(data, dict)),
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
    """Discord select menu for file selection."""
    
    def __init__(self, options: List[disnake.SelectOption], placeholder: str):
        super().__init__(
            placeholder=placeholder,
            options=options,
            min_values=1,
            max_values=1
        )
    
    async def callback(self, inter: disnake.MessageInteraction):
        """Handle file selection"""
        view = self.view
        if not view:
            await inter.response.send_message("❌ View not found", ephemeral=True)
            return
        
        file_id = self.values[0]
        file_data = view.metadata["files"].get(file_id)
        
        if not file_data or not isinstance(file_data, dict):
            await inter.response.send_message("❌ File not found in metadata", ephemeral=True)
            return
        filename = (file_data.get("filename") or file_data.get("name") or "").strip()
        if not filename:
            await inter.response.send_message("❌ File metadata missing filename", ephemeral=True)
            return
        file_path = safe_filename_in_dir(filename, view.files_dir)
        if file_path is None or not file_path.exists():
            display_name = file_data.get("name") or filename
            await inter.response.send_message(
                f"❌ File '{display_name}' not found on disk",
                ephemeral=True
            )
            return
        
        # Call the selection handler if set
        if view.selection_handler:
            try:
                await view.selection_handler(inter, file_id, file_data, file_path)
            except Exception as e:
                if not inter.response.is_done():
                    await inter.response.send_message(
                        f"❌ Error handling selection: {e}", ephemeral=True
                    )
                else:
                    await inter.followup.send(
                        f"❌ Error handling selection: {e}", ephemeral=True
                    )
        else:
            await inter.response.send_message("❌ No handler configured for file selection", ephemeral=True)


class FileBrowserSelectView(disnake.ui.View):
    """Interactive file browser using Discord select menus (max 25 options)."""
    
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
            if not isinstance(file_data, dict):
                continue
            file_name = (file_data.get("name") or "Unknown").strip()
            size_val = file_data.get("size")
            size_mb = (size_val / 1024 / 1024) if isinstance(size_val, (int, float)) and size_val is not None else 0.0
            
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
            self.add_item(select_menu)
    
    async def on_timeout(self):
        """Disable all components when view times out"""
        for item in self.children:
            item.disabled = True


class FileListPaginator(disnake.ui.View):
    """Paginated list view for /distribute list (DistributeZip-only UI)."""

    def __init__(self, files: List[Tuple[str, dict]], timeout: float = 300):
        super().__init__(timeout=timeout)
        self.files = files
        self.current_page = 0
        self.items_per_page = 10
        self.total_pages = (len(files) + self.items_per_page - 1) // self.items_per_page
        self._update_buttons()

    def _update_buttons(self):
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1

    def get_embed(self) -> disnake.Embed:
        start_idx = self.current_page * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, len(self.files))
        page_files = self.files[start_idx:end_idx]

        embed = disnake.Embed(
            title="📦 Uploaded Files",
            color=disnake.Color.blue(),
            timestamp=dt.datetime.now(),
        )

        for _file_id, file_data in page_files:
            if not isinstance(file_data, dict):
                continue
            file_name = file_data.get("name") or "Unknown"
            uploaded_at = file_data.get("uploaded_at")
            size = file_data.get("size")
            size_mb = (size / 1024 / 1024) if isinstance(size, (int, float)) and size is not None else 0.0
            download_count = file_data.get("download_count", 0)
            uploaded_ts = int(uploaded_at) if isinstance(uploaded_at, (int, float)) and uploaded_at else 0
            embed.add_field(
                name=f"📦 {file_name}",
                value=(
                    f"Required by: 🎅 A Secret Santa\n"
                    f"Size: {size_mb:.2f} MB\n"
                    f"Sent to: {download_count} members\n"
                    f"Uploaded: <t:{uploaded_ts}:R>"
                ),
                inline=False,
            )

        if self.total_pages > 1:
            embed.set_footer(
                text=f"Page {self.current_page + 1}/{self.total_pages} • "
                f"Showing {len(page_files)} of {len(self.files)} files"
            )
        else:
            embed.set_footer(text=f"Total: {len(self.files)} file(s)")

        return embed

    @disnake.ui.button(label="◀ Previous", style=disnake.ButtonStyle.secondary)
    async def previous_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if self.current_page > 0:
            self.current_page -= 1
            self._update_buttons()
            await inter.response.edit_message(embed=self.get_embed(), view=self)

    @disnake.ui.button(label="Next ▶", style=disnake.ButtonStyle.secondary)
    async def next_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self._update_buttons()
            await inter.response.edit_message(embed=self.get_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
