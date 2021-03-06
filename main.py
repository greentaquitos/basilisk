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
        utils.get_resource("tiles.png"),16,16, tcod.tileset.CHARMAP_CP437
    )
    
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        with tcod.context.new_terminal(
            screen_width,
            screen_height,
            tileset=tileset,
            title="Basilisk",
            vsync=True,
            renderer=tcod.RENDERER_SDL2
        ) as context:
            root_console = tcod.Console(screen_width, screen_height, order="F")
            handler: input_handlers.BaseEventHandler = setup_game.MainMenu(context,root_console)
            if handler.meta.fullscreen:
                toggle_fullscreen(context)
            try:
                while True:
                    root_console.clear()
                    handler.on_render(console=root_console)
                    context.present(root_console, integer_scaling=True, clear_color=(10,10,10))

                    try:
                        for event in tcod.event.get():
                            context.convert_event(event)
                            handler = handler.handle_events(event)

                    except exceptions.VictoryAnimation as v:
                        init_handler = handler = v.handler
                        while init_handler == handler:
                            root_console.clear()
                            handler.on_render(console=root_console)
                            context.present(root_console, integer_scaling=True)

                            for event in tcod.event.get():
                                context.convert_event(event)
                                handler = handler.handle_events(event)

                    except exceptions.ToggleFullscreen:
                        toggle_fullscreen(context)
                        if hasattr(handler,'engine'):
                            handler.engine.meta.fullscreen = not handler.engine.meta.fullscreen

                    except exceptions.QuitToMenu:
                        save_game(handler, utils.get_resource("savegame.sav"))
                        handler = setup_game.MainMenu(context,root_console)

                    except exceptions.QuitWithoutSaving:
                        handler = setup_game.MainMenu(context,root_console)

                    except Exception:  # Handle exceptions in game.
                        traceback.print_exc()  # Print error to stderr.
                        # Then print the error to the message log.
                        if isinstance(handler, input_handlers.EventHandler):
                            handler.engine.message_log.add_message(
                                traceback.format_exc(), color.red
                            )

            except SystemExit:  # Save and quit.
                save_game(handler, utils.get_resource("savegame.sav"))
                raise
            except BaseException:  # Save on any other unexpected exception.
                save_game(handler, utils.get_resource("savegame.sav"))
                raise


if __name__ == "__main__":
    main()