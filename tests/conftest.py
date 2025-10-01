from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import sys
import warnings

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

warnings.filterwarnings(
    "ignore",
    message="'audioop' is deprecated and slated for removal in Python 3.13",
    category=DeprecationWarning,
)

try:  # pragma: no cover - executed only when discord is unavailable
    import discord  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - fallback stub for tests
    discord = ModuleType("discord")

    class Intents:
        def __init__(self) -> None:
            self.message_content = False
            self.members = False

        @classmethod
        def default(cls) -> "Intents":
            return cls()

    class Color:
        @classmethod
        def blurple(cls) -> "Color":
            return cls()

    class Embed:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.fields: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
            self.footer: dict[str, Any] | None = None

        def add_field(self, *args: Any, **kwargs: Any) -> None:
            self.fields.append((args, kwargs))

        def set_footer(self, **kwargs: Any) -> None:
            self.footer = kwargs

    class Interaction:
        def __init__(self) -> None:
            self.response = SimpleNamespace(send_message=lambda *a, **k: None)
            self.client = SimpleNamespace(get_channel=lambda *_: None)
            self.user = SimpleNamespace(id=0, guild_permissions=None)
            self.guild_id = 0
            self.channel_id = 0

    class Client:
        def get_channel(self, *_: Any) -> Any:
            return None

        def add_view(self, *_: Any) -> None:
            return None

    class TextChannel:
        pass

    class Thread:
        pass

    class HTTPException(Exception):
        pass

    class NotFound(Exception):
        pass

    class ButtonStyle:
        secondary = 1

    class TextStyle:
        paragraph = 1

    class SelectOption:
        def __init__(self, label: str | None = None, description: str | None = None) -> None:
            self.label = label
            self.description = description

    ui_module = ModuleType("discord.ui")

    class View:
        def __init__(self, timeout: int | None = None) -> None:
            self.timeout = timeout
            self.children: list[Any] = []

        def add_item(self, item: Any) -> None:
            self.children.append(item)

    class Select:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.values: list[str] = []
            self.options = kwargs.get("options", [])
            self.disabled = kwargs.get("disabled", False)

        async def callback(self, interaction: Interaction) -> None:
            return None

    class Button:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        async def callback(self, interaction: Interaction) -> None:
            return None

    class Modal:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.children: list[Any] = []

        async def on_submit(self, interaction: Interaction) -> None:
            return None

    class TextInput:
        def __init__(
            self,
            *,
            label: str = "",
            default: str | None = None,
            required: bool = True,
            style: Any | None = None,
            placeholder: str | None = None,
            max_length: int | None = None,
        ) -> None:
            self.label = label
            self.value = default or ""
            self.required = required
            self.style = style
            self.placeholder = placeholder
            self.max_length = max_length

    ui_module.View = View
    ui_module.Select = Select
    ui_module.Button = Button
    ui_module.Modal = Modal
    ui_module.TextInput = TextInput

    app_commands_module = ModuleType("discord.app_commands")

    class Command:
        def __init__(self, callback: Any) -> None:
            self.callback = callback

    class Group:
        def __init__(self, name: str, description: str, parent: "Group" | None = None) -> None:
            self.name = name
            self.description = description
            self.parent = parent

        def command(self, *args: Any, **kwargs: Any):
            def decorator(func: Any) -> Command:
                return Command(func)

            return decorator

    def describe(**kwargs: Any):
        def decorator(func: Any) -> Any:
            return func

        return decorator

    class Choice:
        def __init__(self, name: str, value: int) -> None:
            self.name = name
            self.value = value

    class Range:
        def __class_getitem__(cls, item: Any) -> Any:
            return int

    app_commands_module.Group = Group
    app_commands_module.describe = describe
    app_commands_module.Choice = Choice
    app_commands_module.Range = Range
    app_commands_module.Command = Command

    ext_commands_module = ModuleType("discord.ext.commands")

    class Bot:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.tree = SimpleNamespace(add_command=lambda *a, **k: None, sync=lambda: None)

        def add_view(self, *_: Any) -> None:
            return None

        def run(self, *_: Any) -> None:
            return None

    ext_module = ModuleType("discord.ext")
    ext_commands_module.Bot = Bot
    ext_module.commands = ext_commands_module

    discord.Intents = Intents
    discord.Color = Color
    discord.Embed = Embed
    discord.Interaction = Interaction
    discord.Client = Client
    discord.TextChannel = TextChannel
    discord.Thread = Thread
    discord.HTTPException = HTTPException
    discord.NotFound = NotFound
    utils_module = ModuleType("discord.utils")

    def format_dt(value: Any, style: str = "f") -> str:  # pragma: no cover - stub helper
        if hasattr(value, "timestamp"):
            return f"<t:{int(value.timestamp())}:{style}>"
        return str(value)

    utils_module.format_dt = format_dt

    discord.ButtonStyle = ButtonStyle
    discord.TextStyle = TextStyle
    discord.SelectOption = SelectOption
    discord.ui = ui_module
    discord.app_commands = app_commands_module
    discord.ext = ext_module
    discord.utils = utils_module

    sys.modules["discord"] = discord
    sys.modules["discord.ui"] = ui_module
    sys.modules["discord.app_commands"] = app_commands_module
    sys.modules["discord.ext"] = ext_module
    sys.modules["discord.ext.commands"] = ext_commands_module
    sys.modules["discord.utils"] = utils_module

import config
import db


@pytest.fixture(autouse=True)
def temp_database(tmp_path) -> Iterator[None]:
    """Use an isolated SQLite database for each test."""

    old_path = config.DB_PATH
    test_db = tmp_path / "raids.db"
    config.DB_PATH = str(test_db)
    db.init_db()
    try:
        yield
    finally:
        config.DB_PATH = old_path


@dataclass
class StubMessage:
    id: int = 42


class StubChannel:
    def __init__(self) -> None:
        self.sent_payloads: list[dict[str, Any]] = []

    async def send(self, *args: Any, **kwargs: Any) -> StubMessage:
        self.sent_payloads.append({"args": args, "kwargs": kwargs})
        return StubMessage()


class StubClient:
    def __init__(self, channel: StubChannel | None = None) -> None:
        self._channel = channel or StubChannel()
        self.views: list[Any] = []

    def get_channel(self, _: int) -> StubChannel:
        return self._channel

    def add_view(self, view: Any) -> None:
        self.views.append(view)


@pytest.fixture
def stub_channel() -> StubChannel:
    return StubChannel()


@pytest.fixture
def stub_client(stub_channel: StubChannel) -> StubClient:
    return StubClient(stub_channel)

