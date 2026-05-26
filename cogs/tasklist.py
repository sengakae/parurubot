import asyncio
import copy
import logging

import discord
from discord.ext import commands

import db

logger = logging.getLogger(__name__)

CHECKED_EMOJI_NAME = "\N{WHITE HEAVY CHECK MARK}"
MAX_TASKS = 25
BUILDER_TIMEOUT = 300
CREATOR_PANEL_TIMEOUT = 3600


def _toggle_custom_id(message_id: int, index: int) -> str:
    return f"tasklist:toggle:{message_id}:{index}"


def _edit_custom_id(message_id: int) -> str:
    return f"tasklist:edit:{message_id}"


def format_task_line(index: int, task: dict) -> str:
    prefix = f"{index + 1}. {task['text']}"
    if task["done"]:
        return f"### {CHECKED_EMOJI_NAME} {prefix}"
    return f"### {prefix}"


def _author_only(interaction: discord.Interaction, author_id: int) -> bool:
    return interaction.user.id == author_id


def _select_option_label(index: int, task: str) -> str:
    label = f"{index + 1}. {task}"
    return label[:100]


async def _save_tasklist(view: "TaskListView") -> None:
    await db.save_tasklist(
        view.message_id,
        view.channel_id,
        view.author_id,
        view.author_name,
        view.tasks,
    )


def _register_tasklist_view(bot: discord.Client, view: "TaskListView") -> None:
    bot.add_view(view)


async def _publish_tasklist(
    bot: discord.Client,
    view: "TaskListView",
) -> None:
    await _save_tasklist(view)
    _register_tasklist_view(bot, view)


def _edit_button_row(task_count: int) -> int | None:
    """Row with a free slot for an Edit list button, or None if all rows are full."""
    if task_count <= 0:
        return 0
    if task_count >= 25:
        return None
    last_row = (task_count - 1) // 5
    if task_count % 5 != 0:
        return last_row
    if last_row < 4:
        return last_row + 1
    return None


async def _refresh_builder(
    interaction: discord.Interaction, builder_view: "TaskListBuilderView"
):
    builder_view.clear_items()
    builder_view._attach_buttons()
    await interaction.response.edit_message(
        embed=builder_view.build_embed(),
        view=builder_view,
    )


async def _refresh_published_editor(
    interaction: discord.Interaction, editor_view: "TaskListPublishedEditView"
):
    editor_view.clear_items()
    editor_view._attach_buttons()
    await interaction.response.edit_message(
        embed=editor_view.build_embed(),
        view=editor_view,
    )


async def _start_published_edit(
    interaction: discord.Interaction,
    *,
    tasks: list[dict],
    author_id: int,
    author_name: str,
    channel_id: int,
    message_id: int,
    bot: discord.Client,
    ephemeral_ack: str | None = None,
):
    if not _author_only(interaction, author_id):
        await interaction.response.send_message(
            "Only the person who created this list can edit it.",
            ephemeral=True,
        )
        return

    edit_view = TaskListPublishedEditView(
        tasks=tasks,
        author_id=author_id,
        author_name=author_name,
        channel_id=channel_id,
        message_id=message_id,
        bot=bot,
    )

    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    message = await channel.fetch_message(message_id)
    embed = edit_view.build_embed()

    if (
        not interaction.response.is_done()
        and interaction.message is not None
        and interaction.message.id == message_id
    ):
        await interaction.response.edit_message(embed=embed, view=edit_view)
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral_ack is not None)

    await message.edit(embed=embed, view=edit_view)

    if ephemeral_ack:
        await interaction.followup.send(ephemeral_ack, ephemeral=True)


class AddTaskModal(discord.ui.Modal, title="Add a task"):
    def __init__(self, builder_view: "TaskListBuilderView"):
        super().__init__()
        self.builder_view = builder_view
        self.task_input = discord.ui.TextInput(
            label="Task",
            placeholder="What do you need to do?",
            max_length=200,
            required=True,
        )
        self.add_item(self.task_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not _author_only(interaction, self.builder_view.author_id):
            await interaction.response.send_message(
                "Only the person who started this list can add tasks.",
                ephemeral=True,
            )
            return

        text = self.task_input.value.strip()
        if not text:
            await interaction.response.send_message(
                "Task cannot be empty.", ephemeral=True
            )
            return

        if len(self.builder_view.tasks) >= MAX_TASKS:
            await interaction.response.send_message(
                f"You can add at most {MAX_TASKS} tasks.", ephemeral=True
            )
            return

        self.builder_view.tasks.append(text)
        await _refresh_builder(interaction, self.builder_view)


class EditTaskModal(discord.ui.Modal, title="Edit task"):
    def __init__(self, builder_view: "TaskListBuilderView", index: int):
        super().__init__()
        self.builder_view = builder_view
        self.index = index
        self.task_input = discord.ui.TextInput(
            label="Task",
            default=builder_view.tasks[index],
            max_length=200,
            required=True,
        )
        self.add_item(self.task_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not _author_only(interaction, self.builder_view.author_id):
            await interaction.response.send_message(
                "Only the person who started this list can edit tasks.",
                ephemeral=True,
            )
            return

        text = self.task_input.value.strip()
        if not text:
            await interaction.response.send_message(
                "Task cannot be empty.", ephemeral=True
            )
            return

        self.builder_view.tasks[self.index] = text
        await _refresh_builder(interaction, self.builder_view)


class PublishedAddTaskModal(discord.ui.Modal, title="Add a task"):
    def __init__(self, editor_view: "TaskListPublishedEditView"):
        super().__init__()
        self.editor_view = editor_view
        self.task_input = discord.ui.TextInput(
            label="Task",
            placeholder="What do you need to do?",
            max_length=200,
            required=True,
        )
        self.add_item(self.task_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not _author_only(interaction, self.editor_view.author_id):
            await interaction.response.send_message(
                "Only the person who created this list can add tasks.",
                ephemeral=True,
            )
            return

        text = self.task_input.value.strip()
        if not text:
            await interaction.response.send_message(
                "Task cannot be empty.", ephemeral=True
            )
            return

        if len(self.editor_view.tasks) >= MAX_TASKS:
            await interaction.response.send_message(
                f"You can add at most {MAX_TASKS} tasks.", ephemeral=True
            )
            return

        self.editor_view.tasks.append({"text": text, "done": False})
        await _refresh_published_editor(interaction, self.editor_view)


class PublishedEditTaskModal(discord.ui.Modal, title="Edit task"):
    def __init__(self, editor_view: "TaskListPublishedEditView", index: int):
        super().__init__()
        self.editor_view = editor_view
        self.index = index
        self.task_input = discord.ui.TextInput(
            label="Task",
            default=editor_view.tasks[index]["text"],
            max_length=200,
            required=True,
        )
        self.add_item(self.task_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not _author_only(interaction, self.editor_view.author_id):
            await interaction.response.send_message(
                "Only the person who created this list can edit tasks.",
                ephemeral=True,
            )
            return

        text = self.task_input.value.strip()
        if not text:
            await interaction.response.send_message(
                "Task cannot be empty.", ephemeral=True
            )
            return

        self.editor_view.tasks[self.index]["text"] = text
        await _refresh_published_editor(interaction, self.editor_view)


class EditTaskSelect(discord.ui.Select):
    def __init__(self, tasks: list[str]):
        options = [
            discord.SelectOption(
                label=_select_option_label(index, task),
                value=str(index),
            )
            for index, task in enumerate(tasks)
        ]
        super().__init__(
            placeholder="Edit a task...",
            options=options,
            min_values=1,
            max_values=1,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        view: TaskListBuilderView = self.view
        if not _author_only(interaction, view.author_id):
            await interaction.response.send_message(
                "Only the person who started this list can edit tasks.",
                ephemeral=True,
            )
            return

        index = int(self.values[0])
        await interaction.response.send_modal(EditTaskModal(view, index))


class PublishedEditTaskSelect(discord.ui.Select):
    def __init__(self, tasks: list[dict]):
        options = [
            discord.SelectOption(
                label=_select_option_label(index, task["text"]),
                value=str(index),
            )
            for index, task in enumerate(tasks)
        ]
        super().__init__(
            placeholder="Edit a task...",
            options=options,
            min_values=1,
            max_values=1,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        view: TaskListPublishedEditView = self.view
        if not _author_only(interaction, view.author_id):
            await interaction.response.send_message(
                "Only the person who created this list can edit tasks.",
                ephemeral=True,
            )
            return

        index = int(self.values[0])
        await interaction.response.send_modal(PublishedEditTaskModal(view, index))


class AddTaskButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = False):
        super().__init__(
            label="Add task",
            style=discord.ButtonStyle.primary,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        view: TaskListBuilderView = self.view
        if not _author_only(interaction, view.author_id):
            await interaction.response.send_message(
                "Only the person who started this list can add tasks.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(AddTaskModal(view))


class PublishedAddTaskButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = False):
        super().__init__(
            label="Add task",
            style=discord.ButtonStyle.primary,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        view: TaskListPublishedEditView = self.view
        if not _author_only(interaction, view.author_id):
            await interaction.response.send_message(
                "Only the person who created this list can add tasks.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(PublishedAddTaskModal(view))


class FinishListButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Finish", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        view: TaskListBuilderView = self.view
        if not _author_only(interaction, view.author_id):
            await interaction.response.send_message(
                "Only the person who started this list can finish it.",
                ephemeral=True,
            )
            return

        if not view.tasks:
            await interaction.response.send_message(
                "Add at least one task before finishing.", ephemeral=True
            )
            return

        task_view = TaskListView(
            view.author_name,
            view.author_id,
            channel_id=interaction.channel_id,
            message_id=interaction.message.id,
            task_texts=view.tasks,
        )
        await interaction.response.edit_message(
            embed=task_view.build_embed(),
            view=task_view,
        )
        await _publish_tasklist(interaction.client, task_view)

        panel = CreatorTaskListPanel(
            bot=interaction.client,
            channel_id=interaction.channel_id,
            message_id=interaction.message.id,
            author_id=view.author_id,
            author_name=view.author_name,
        )
        await interaction.followup.send(
            "You can edit this list anytime with **Edit list** (only you can use it).",
            view=panel,
            ephemeral=True,
        )


class CancelListButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        view: TaskListBuilderView = self.view
        if not _author_only(interaction, view.author_id):
            await interaction.response.send_message(
                "Only the person who started this list can cancel it.",
                ephemeral=True,
            )
            return

        for item in view.children:
            item.disabled = True

        embed = discord.Embed(
            title="Task list cancelled",
            description="This task list was not created.",
            color=discord.Color.dark_grey(),
        )
        await interaction.response.edit_message(embed=embed, view=view)


class SaveTaskListButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Save", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        view: TaskListPublishedEditView = self.view
        if not _author_only(interaction, view.author_id):
            await interaction.response.send_message(
                "Only the person who created this list can save changes.",
                ephemeral=True,
            )
            return

        if not view.tasks:
            await interaction.response.send_message(
                "Add at least one task before saving.", ephemeral=True
            )
            return

        task_view = TaskListView(
            view.author_name,
            view.author_id,
            channel_id=view.channel_id,
            message_id=view.message_id,
            tasks=view.tasks,
        )
        await interaction.response.edit_message(
            embed=task_view.build_embed(),
            view=task_view,
        )
        await _publish_tasklist(interaction.client, task_view)


class CancelPublishedEditButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        view: TaskListPublishedEditView = self.view
        if not _author_only(interaction, view.author_id):
            await interaction.response.send_message(
                "Only the person who created this list can cancel editing.",
                ephemeral=True,
            )
            return

        task_view = TaskListView(
            view.author_name,
            view.author_id,
            channel_id=view.channel_id,
            message_id=view.message_id,
            tasks=view.original_tasks,
        )
        await interaction.response.edit_message(
            embed=task_view.build_embed(),
            view=task_view,
        )
        await _publish_tasklist(interaction.client, task_view)


class EditListButton(discord.ui.Button):
    def __init__(self, message_id: int, *, row: int = 0):
        super().__init__(
            label="Edit list",
            style=discord.ButtonStyle.secondary,
            row=row,
            custom_id=_edit_custom_id(message_id),
        )

    async def callback(self, interaction: discord.Interaction):
        view: TaskListView = self.view
        await _start_published_edit(
            interaction,
            tasks=[dict(task) for task in view.tasks],
            author_id=view.author_id,
            author_name=view.author_name,
            channel_id=interaction.channel_id,
            message_id=interaction.message.id,
            bot=interaction.client,
        )


class CreatorEditListButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Edit list", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        panel: CreatorTaskListPanel = self.view
        if not _author_only(interaction, panel.author_id):
            await interaction.response.send_message(
                "Only the person who created this list can edit it.",
                ephemeral=True,
            )
            return

        stored = await db.get_tasklist(panel.message_id)
        if stored is None:
            await interaction.response.send_message(
                "This list is no longer active.", ephemeral=True
            )
            return

        await _start_published_edit(
            interaction,
            tasks=[dict(task) for task in stored["tasks"]],
            author_id=stored["author_id"],
            author_name=stored["author_name"],
            channel_id=stored["channel_id"],
            message_id=stored["message_id"],
            bot=panel.bot,
            ephemeral_ack="Editing the task list above.",
        )


class TaskListBuilderView(discord.ui.View):
    def __init__(self, author_id: int, author_name: str):
        super().__init__(timeout=BUILDER_TIMEOUT)
        self.author_id = author_id
        self.author_name = author_name
        self.tasks: list[str] = []
        self._attach_buttons()

    def _attach_buttons(self):
        at_limit = len(self.tasks) >= MAX_TASKS
        self.add_item(AddTaskButton(disabled=at_limit))
        self.add_item(FinishListButton())
        self.add_item(CancelListButton())
        if self.tasks:
            self.add_item(EditTaskSelect(self.tasks))

    def build_embed(self) -> discord.Embed:
        if not self.tasks:
            description = (
                "No tasks yet. Click **Add task** to get started.\n\n"
                "When you're done, click **Finish** to create your checklist."
            )
        else:
            lines = [
                f"### {index + 1}. {task}" for index, task in enumerate(self.tasks)
            ]
            description = (
                "\n".join(lines)
                + "\n\n_Click **Add task** for more, use **Edit a task** to change one, "
                "or **Finish** to publish._"
            )

        embed = discord.Embed(
            title="Build your task list",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"{len(self.tasks)}/{MAX_TASKS} tasks · {self.author_name}"
        )
        return embed

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class TaskListPublishedEditView(discord.ui.View):
    def __init__(
        self,
        *,
        tasks: list[dict],
        author_id: int,
        author_name: str,
        channel_id: int,
        message_id: int,
        bot: discord.Client,
    ):
        super().__init__(timeout=BUILDER_TIMEOUT)
        self.tasks = [dict(task) for task in tasks]
        self.original_tasks = copy.deepcopy(self.tasks)
        self.author_id = author_id
        self.author_name = author_name
        self.channel_id = channel_id
        self.message_id = message_id
        self.bot = bot
        self._attach_buttons()

    def _attach_buttons(self):
        at_limit = len(self.tasks) >= MAX_TASKS
        self.add_item(PublishedAddTaskButton(disabled=at_limit))
        self.add_item(SaveTaskListButton())
        self.add_item(CancelPublishedEditButton())
        if self.tasks:
            self.add_item(PublishedEditTaskSelect(self.tasks))

    def build_embed(self) -> discord.Embed:
        lines = [
            format_task_line(index, task)
            for index, task in enumerate(self.tasks)
        ]
        description = (
            "\n".join(lines)
            + "\n\n_Click **Add task** or **Edit a task** to update the list, "
            "then **Save** to apply or **Cancel** to discard changes._"
        )
        embed = discord.Embed(
            title="Edit task list",
            description=description,
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"{len(self.tasks)}/{MAX_TASKS} tasks · {self.author_name}"
        )
        return embed

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class CreatorTaskListPanel(discord.ui.View):
    def __init__(
        self,
        *,
        bot: discord.Client,
        channel_id: int,
        message_id: int,
        author_id: int,
        author_name: str,
    ):
        super().__init__(timeout=CREATOR_PANEL_TIMEOUT)
        self.bot = bot
        self.channel_id = channel_id
        self.message_id = message_id
        self.author_id = author_id
        self.author_name = author_name
        self.add_item(CreatorEditListButton())


class TaskToggleButton(discord.ui.Button):
    def __init__(self, message_id: int, index: int, done: bool):
        super().__init__(
            label=str(index + 1),
            style=(
                discord.ButtonStyle.success if done else discord.ButtonStyle.secondary
            ),
            row=index // 5,
            custom_id=_toggle_custom_id(message_id, index),
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: TaskListView = self.view
        view.tasks[self.index]["done"] = not view.tasks[self.index]["done"]
        view.clear_items()
        view._attach_buttons()
        await _save_tasklist(view)
        await interaction.response.edit_message(
            embed=view.build_embed(),
            view=view,
        )


class TaskListView(discord.ui.View):
    def __init__(
        self,
        author_name: str,
        author_id: int,
        channel_id: int,
        message_id: int,
        *,
        task_texts: list[str] | None = None,
        tasks: list[dict] | None = None,
    ):
        super().__init__(timeout=None)
        self.author_name = author_name
        self.author_id = author_id
        self.channel_id = channel_id
        self.message_id = message_id
        if tasks is not None:
            self.tasks = [dict(task) for task in tasks]
        elif task_texts is not None:
            self.tasks = [{"text": text, "done": False} for text in task_texts]
        else:
            raise ValueError("task_texts or tasks is required")
        self._attach_buttons()

    @classmethod
    def from_stored(cls, stored: dict) -> "TaskListView":
        return cls(
            stored["author_name"],
            stored["author_id"],
            stored["channel_id"],
            stored["message_id"],
            tasks=stored["tasks"],
        )

    def _attach_buttons(self):
        for index in range(len(self.tasks)):
            self.add_item(
                TaskToggleButton(
                    self.message_id, index, self.tasks[index]["done"]
                )
            )
        edit_row = _edit_button_row(len(self.tasks))
        if edit_row is not None:
            self.add_item(EditListButton(self.message_id, row=edit_row))

    def build_embed(self) -> discord.Embed:
        lines = [
            format_task_line(index, task)
            for index, task in enumerate(self.tasks)
        ]
        completed = sum(1 for task in self.tasks if task["done"])
        embed = discord.Embed(
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        footer = f"{completed}/{len(self.tasks)} completed · Created by {self.author_name}"
        if _edit_button_row(len(self.tasks)) is None:
            footer += " · Use your private **Edit list** message to edit"
        embed.set_footer(text=footer)
        return embed


class TaskListCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._restored = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._restored:
            return
        for _ in range(20):
            if db.pool is not None:
                self._restored = True
                await self._restore_tasklists()
                return
            await asyncio.sleep(0.25)
        logger.warning("TaskList: database pool not ready; lists not restored")

    async def _restore_tasklists(self):
        stored_lists = await db.get_all_tasklists()
        for stored in stored_lists:
            view = TaskListView.from_stored(stored)
            _register_tasklist_view(self.bot, view)
        if stored_lists:
            logger.info("Restored %d persistent task list(s)", len(stored_lists))

    @commands.command(
        name="tasklist",
        help="Create an interactive to-do list with checkboxes.",
    )
    async def tasklist(self, ctx):
        view = TaskListBuilderView(ctx.author.id, ctx.author.display_name)
        await ctx.send(embed=view.build_embed(), view=view)


async def setup(bot):
    await bot.add_cog(TaskListCog(bot))
