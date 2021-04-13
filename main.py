#!/usr/bin/env python3
import traceback
import warnings
import tcod

from basilisk import color, exceptions
from basilisk.game_map import GameMap
from basilisk import input_handlers, setup_game

import utils


def save_game(handler: input_handlers.BaseEventHandler, filename: str) -> None:
    """If the current event handler has an active Engine then save it."""
    if isinstance(handler, input_handlers.EventHandler):
        handler.engine.save_as(filename)
        print("Game saved.")

def toggle_fullscreen(context: tcod.context.Context) -> None:
    """Toggle a context window between fullscreen and windowed modes."""
    if not context.sdl_window_p:
        return
    fullscreen = tcod.lib.SDL_GetWindowFlags(context.sdl_window_p) & (
        tcod.lib.SDL_WINDOW_FULLSCREEN | tcod.lib.SDL_WINDOW_FULLSCREEN_DESKTOP
    )
    tcod.lib.SDL_SetWindowFullscreen(
        context.sdl_window_p,
        0 if fullscreen else tcod.lib.SDL_WINDOW_FULLSCREEN_DESKTOP,
    )

def main() -> None:
    screen_width = 80
    screen_height = 50

    tileset = tcod.tileset.load_tilesheet(
        utils.get_resource("tiles.png"), 32, 8, tcod.tileset.CHARMAP_TCOD
    )

    handler: input_handlers.BaseEventHandler = setup_game.MainMenu()

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        with tcod.context.new_terminal(
            screen_width,
            screen_height,
            tileset=tileset,
            title="Basilisk",
            vsync=True,
        ) as context:
            root_console = tcod.Console(screen_width, screen_height, order="F")
            toggle_fullscreen(context)
            try:
                while True:
                    root_console.clear()
                    handler.on_render(console=root_console)
                    context.present(root_console)

                    try:
                        for event in tcod.event.wait():
                            context.convert_event(event)
                            handler = handler.handle_events(event)
                    except Exception:  # Handle exceptions in game.
                        traceback.print_exc()  # Print error to stderr.
                        # Then print the error to the message log.
                        if isinstance(handler, input_handlers.EventHandler):
                            handler.engine.message_log.add_message(
                                traceback.format_exc(), color.red
                            )
            except exceptions.QuitWithoutSaving:
                raise
            except SystemExit:  # Save and quit.
                save_game(handler, utils.get_resource("savegame.sav"))
                raise
            except BaseException:  # Save on any other unexpected exception.
                save_game(handler, utils.get_resource("savegame.sav"))
                raise


if __name__ == "__main__":
    main()