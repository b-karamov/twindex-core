from rich.console import Console
from rich.text import Text


def transcript_text(messages: list[str], console: Console, width: int) -> Text:
    """Wrap the body independently of the fixed three-cell speaker gutter."""
    result = Text()
    for index, markup in enumerate(messages):
        if index:
            result.append("\n\n")
        message = Text.from_markup(markup)
        lines = message[3:].wrap(console, max(1, width - 3), overflow="fold")
        for line_index, line in enumerate(lines):
            if line_index:
                result.append("\n")
            result.append(message[:3] if line_index == 0 else Text("   "))
            result.append(line)
    return result
