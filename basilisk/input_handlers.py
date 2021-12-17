from __future__ import annotations

import os

from typing import Callable, Optional, Tuple, TYPE_CHECKING, Union

import tcod.event
import math
import glob

from basilisk import actions, color, exceptions
from basilisk.actions import (
    Action,
    BumpAction,
    WaitAction,
    PickupAction,
)
from basilisk.render_functions import DIRECTIONS, D_ARROWS
from basilisk.components.status_effect import PetrifiedSnake
from basilisk.tile_types import NAMES, FLAVORS

if TYPE_CHECKING:
    from basilisk.engine import Engine
    from basilisk.entity import Item

import utils

MOVE_KEYS = {
    # Arrow keys.
    tcod.event.K_UP: (0, -1),
    tcod.event.K_DOWN: (0, 1),
    tcod.event.K_LEFT: (-1, 0),
    tcod.event.K_RIGHT: (1, 0),
    tcod.event.K_HOME: (-1, -1),
    tcod.event.K_END: (-1, 1),
    tcod.event.K_PAGEUP: (1, -1),
    tcod.event.K_PAGEDOWN: (1, 1),
    # Numpad keys.
    tcod.event.K_KP_1: (-1, 1),
    tcod.event.K_KP_2: (0, 1),
    tcod.event.K_KP_3: (1, 1),
    tcod.event.K_KP_4: (-1, 0),
    tcod.event.K_KP_6: (1, 0),
    tcod.event.K_KP_7: (-1, -1),
    tcod.event.K_KP_8: (0, -1),
    tcod.event.K_KP_9: (1, -1),
    # Vi keys.
    tcod.event.K_h: (-1, 0),
    tcod.event.K_j: (0, 1),
    tcod.event.K_k: (0, -1),
    tcod.event.K_l: (1, 0),
    tcod.event.K_y: (-1, -1),
    tcod.event.K_u: (1, -1),
    tcod.event.K_b: (-1, 1),
    tcod.event.K_n: (1, 1),
}

ALPHA_KEYS = {
    tcod.event.K_a: 0,
    tcod.event.K_b: 1,
    tcod.event.K_c: 2,
    tcod.event.K_d: 3,
    tcod.event.K_e: 4,
    tcod.event.K_f: 5,
    tcod.event.K_g: 6,
    tcod.event.K_h: 7,
    tcod.event.K_i: 8,
    tcod.event.K_j: 9,
    tcod.event.K_k: 10,
    tcod.event.K_l: 11,
    tcod.event.K_m: 12,
    tcod.event.K_n: 13,
    tcod.event.K_o: 14,
    tcod.event.K_p: 15,
    tcod.event.K_q: 16,
    tcod.event.K_r: 17,
    tcod.event.K_s: 18,
    tcod.event.K_t: 19,
    tcod.event.K_u: 20,
    tcod.event.K_v: 21,
    tcod.event.K_w: 22,
    tcod.event.K_x: 23,
    tcod.event.K_y: 24,
    tcod.event.K_z: 25
}

WAIT_KEYS = {
    tcod.event.K_PERIOD,
    tcod.event.K_KP_5,
    tcod.event.K_CLEAR,
}

CONFIRM_KEYS = {
    tcod.event.K_RETURN,
    tcod.event.K_KP_ENTER,
}


CURSOR_Y_KEYS = {
    tcod.event.K_UP: -1,
    tcod.event.K_DOWN: 1,
    tcod.event.K_PAGEUP: -10,
    tcod.event.K_PAGEDOWN: 10,
    tcod.event.K_j: 1,
    tcod.event.K_k: -1,
    tcod.event.K_KP_2: 1,
    tcod.event.K_KP_8: -1,
}

CURSOR_X_KEYS = {
    tcod.event.K_LEFT: -1,
    tcod.event.K_RIGHT: 1,
    tcod.event.K_h: -1,
    tcod.event.K_l: 1,
    tcod.event.K_KP_4: -1,
    tcod.event.K_KP_6: 1
}


ActionOrHandler = Union[Action, "BaseEventHandler"]
"""An event handler return value which can trigger an action or switch active handlers.

If a handler is returned then it will become the active handler for future events.
If an action is returned it will be attempted and if it's valid then
MainGameEventHandler will become the active handler.
"""


class BaseEventHandler(tcod.event.EventDispatch[ActionOrHandler]):
    def handle_events(self, event: tcod.event.Event) -> BaseEventHandler:
        """Handle an event and return the next active event handler."""
        state = self.dispatch(event)
        if isinstance(state, BaseEventHandler):
            return state
        assert not isinstance(state, Action), f"{self!r} can not handle actions."
        return self

    def on_render(self, console: tcod.Console) -> None:
        raise NotImplementedError()

    def ev_quit(self, event: tcod.event.Quit) -> Optional[Action]:
        raise SystemExit()


class EventHandler(BaseEventHandler):
    def __init__(self, engine: Engine):
        self.engine = engine
        engine.mouse_location = 0,0

    def handle_events(self, event: tcod.event.Event) -> BaseEventHandler:
        """Handle events for input handlers with an engine."""
        action_or_state = self.dispatch(event)
        if isinstance(action_or_state, BaseEventHandler):
            return action_or_state
        handled_action = self.handle_action(action_or_state)
        if isinstance(handled_action, BaseEventHandler):
            return handled_action
        elif handled_action:
            # A valid action was performed.
            if not self.engine.player.is_alive:
                # The player was killed sometime during or after the action.
                return GameOverEventHandler(self.engine)
            if self.engine.boss_killed:
                return VictoryEventHandler(self.engine)
            if self.engine.in_combat and not self.engine.confirmed_in_combat and self.engine.meta.do_combat_confirm:
                return ConfirmCombatHandler(self.engine)
            if not self.engine.in_combat and self.engine.confirmed_in_combat:
                self.engine.confirmed_in_combat = False
            return MainGameEventHandler(self.engine)  # Return to the main handler.
        return self

    def handle_action(self, action: Optional[Action]) -> bool:
        """Handle actions returned from event methods.

        Returns True if the action will advance a turn.
        """
        if action is None:
            return False

        try:
            action.perform()
        except exceptions.Impossible as exc:
            self.engine.message_log.add_message(exc.args[0], color.grey)
            return False  # Skip enemy turn on exceptions.
        except exceptions.UnorderedPickup as exc:
            return OrderPickupHandler(self.engine)

        else:
            while any(isinstance(s, PetrifiedSnake) for s in self.engine.player.statuses) and self.engine.player.is_alive:
                self.engine.handle_enemy_turns()

            if not self.engine.just_turned_back_time:
                self.engine.handle_enemy_turns()
            else:
                self.engine.just_turned_back_time = False

        self.engine.update_fov()
        return True

    def ev_mousemotion(self, event: tcod.event.MouseMotion) -> None:
        if self.engine.game_map.in_bounds(event.tile.x, event.tile.y):
            self.engine.mouse_location = event.tile.x, event.tile.y
        else:
            self.engine.mouse_location = (0,0)

    def on_render(self, console: tcod.Console) -> None:
        self.engine.render(console)


class MainGameEventHandler(EventHandler):
    def handle_events(self, event):
        te = self.engine.meta.tutorial_events

        if not self.engine.meta.tutorials or len(te) >= 14:
            return super().handle_events(event)

        if not "new game" in te:
            return TutorialConfirm(self.engine, "new game")
        if not "pick up" in te and len(self.engine.player.inventory.items) > 0:
            return TutorialConfirm(self.engine, "pick up")
        if not "word mode" in te and self.engine.word_mode:
            self.engine.meta.log_tutorial_event("word mode")
        if not "left word mode" in te and "word mode" in te and not self.engine.word_mode:
            self.engine.meta.log_tutorial_event("left word mode")
        if not "word mode 2" in te and "left word mode" in te and self.engine.word_mode:
            return TutorialConfirm(self.engine, "word mode 2")
        if not "enemy" in te and self.engine.can_see_enemies:
            return TutorialConfirm(self.engine, "enemy")
        if not "constrict" in te and self.engine.an_enemy_is_constricted:
            return TutorialConfirm(self.engine, "constrict")
        if not "consonant" in te and any(i.char not in ['a','e','i','o','u','y'] for i in self.engine.player.inventory.items):
            return TutorialConfirm(self.engine, "consonant")
        if not "stairs" in te and self.engine.stairs_visible:
            return TutorialConfirm(self.engine, "stairs")
        if not "snakestone" in te and not self.engine.game_map.tiles["walkable"][self.engine.player.xy]:
            return TutorialConfirm(self.engine, "snakestone")
        if not "new game 2" in te and "consonant" in te and self.engine.turn_count == 0:
            return TutorialConfirm(self.engine, "new game 2")
        if not "stat boost" in te and any(i > 0 for i in [self.engine.player.BILE,self.engine.player.TAIL,self.engine.player.MIND,self.engine.player.TONG]):
            return TutorialConfirm(self.engine, "stat boost")
        if not "100 turns" in te and self.engine.turn_count > 99:
            return TutorialConfirm(self.engine, "100 turns")

        return super().handle_events(event)

    def ev_keydown(self, event: tcod.event.KeyDown) -> Optional[ActionOrHandler]:
        action: Optional[Action] = None

        key = event.sym
        modifier = event.mod
        player = self.engine.player
 
        if modifier & (tcod.event.KMOD_LSHIFT | tcod.event.KMOD_RSHIFT):

            #if key == tcod.event.K_v:
            #    return BigHistoryViewer(self.engine)

            dsx, dsy = self.engine.game_map.downstairs_location

            if key in ALPHA_KEYS:
                if self.engine.mouse_location != (0,0):
                    # mouse is active -- use mouseover index
                    if len(self.engine.mouse_things) > ALPHA_KEYS[key]:
                        return InspectHandler(self.engine, key, self, 'mouse', self.engine.mouse_location)
                elif (
                    (key in ALPHA_KEYS and len(self.engine.fov_actors) > ALPHA_KEYS[key]) or
                    (key in ALPHA_KEYS and len(self.engine.fov_actors) == ALPHA_KEYS[key] and
                            self.engine.game_map.visible[dsx,dsy])
                ):
                    # mouse is inactive -- use default fov index
                    return InspectHandler(self.engine, key, self)

            return None

        if key in MOVE_KEYS:
            dx, dy = MOVE_KEYS[key]
            action = BumpAction(player, dx, dy)
        elif key in WAIT_KEYS:
            if player.in_danger:
                return Confirm(self, lambda: WaitAction(player), "Wait while enemies intend to attack?", None, self.engine)
            else:
                action = WaitAction(player)
        elif key == tcod.event.K_ESCAPE:
            return PlayMenuHandler(self.engine, self)
        elif key == tcod.event.K_v:
            return HistoryViewer(self.engine)

        elif key == tcod.event.K_i:
            return InventorySelectHandler(self.engine)
        elif key == tcod.event.K_s:
            return InventorySpitHandler(self.engine)
        elif key == tcod.event.K_d:
            return InventoryDigestHandler(self.engine)

        elif key == tcod.event.K_x:
            return LookHandler(self.engine)

        elif key == tcod.event.K_TAB:
            return LookHandler(self.engine,True)

        elif key == tcod.event.K_c:
            self.engine.show_instructions = not self.engine.show_instructions

        elif key == tcod.event.K_o:
            return DictionaryEventHandler(self.engine)

        elif key == tcod.event.K_p:
            return CompendiumHandler(self.engine)

        # No valid key was pressed
        return action

    # hopefully this helps with int'l keyboards not registering shift+/ or shift+.
    # upgrading tcod will also fix
    def ev_textinput(self, event: tcod.event.TextInput):
        if event.text == '?':
            return PopupMessage(self, self.engine.help_text, 'top')
        if event.text == '>':
            return actions.TakeStairsAction(self.engine.player)

    def ev_mousebuttondown(self, event: tcod.event.MouseButtonDown):
        if len(self.engine.mouse_things) > 0:
            return InspectHandler(self.engine, 97, self, 'mouse', self.engine.mouse_location)


class AskUserEventHandler(EventHandler):
    """Handles user input for actions which require special input."""

    def ev_keydown(self, event: tcod.event.KeyDown) -> Optional[ActionOrHandler]:
        """By default any key exits this input handler."""
        if event.sym in {  # Ignore modifier keys.
            tcod.event.K_LSHIFT,
            tcod.event.K_RSHIFT,
            tcod.event.K_LCTRL,
            tcod.event.K_RCTRL,
            tcod.event.K_LALT,
            tcod.event.K_RALT,
        }:
            return None
        return self.on_exit()

    def ev_mousebuttondown(
        self, event: tcod.event.MouseButtonDown
    ) -> Optional[ActionOrHandler]:
        """By default any mouse click exits this input handler."""
        return self.on_exit()

    def on_exit(self) -> Optional[ActionOrHandler]:
        """Called when the user is trying to exit or cancel an action.

        By default this returns to the main event handler.
        """
        return MainGameEventHandler(self.engine)

    def print_multicolor_box(self, console, x, y, width, height, parts):
        """For highlighting stat-affected numbers"""
        wx = x
        wy = y
        for part in parts:
            for word in str(part[0]).split():
                if wx + len(word) > x+width:
                    wx = x
                    wy += 1
                console.print(wx,wy,word,part[1])
                wx += len(word)+1


class DictionaryEventHandler(AskUserEventHandler):
    def __init__(self,engine):
        super().__init__(engine)
        self.input = ''
        self.first_o = False

    @property
    def valid_word(self):
        # lookup self.input
        return self.engine.is_valid_word(self.input)

    @property
    def cursor(self):
        return len(self.input)

    def on_render(self,console):
        super().on_render(console)
        v = self.valid_word
        # dictionary on top
        console.draw_frame(1,1,29,3,fg=color.offwhite)
        console.print_box(1,1,29,1,"Dictionary",fg=color.black,bg=color.offwhite,alignment=tcod.CENTER)

        console.print(2,2,self.input,fg=color.offwhite)
        if not v:
            console.print(2+self.cursor,2,'_'*(26-self.cursor),fg=color.offwhite)

        if self.cursor > 0:
            n = '☑' if v else 'x'
            c = color.player if v else color.red
            console.print(2+self.cursor,2,n,fg=c)
        else:
            console.print(2,2,'_',fg=color.black,bg=color.offwhite)

    def ev_textinput(self,event):
        # type to look stuff up
        if event.text.isalpha() and len(self.input) < 26:
            if event.text == 'o' and not self.first_o:
                self.first_o = True
                return
            self.input += event.text.lower()

    def ev_keydown(self,event):
        if event.sym == tcod.event.K_BACKSPACE and len(self.input) > 0:
            self.input = self.input[:-1]
            return
        if event.sym == tcod.event.K_ESCAPE:
            return self.on_exit()
        super().ev_keydown(event)


class Confirm(EventHandler):
    def __init__(self,parent,callback,prompt,cancel_callback=None,engine=None,type='y/n'):
        if(engine):
            super().__init__(engine)
        else:
            self.engine = None
        self.parent = parent
        self.callback = callback
        self.prompt = prompt
        self.cancel_callback = cancel_callback

    def on_render(self,console):
        self.parent.on_render(console)

        x, y = (7,7) if not self.engine else (1,37)
        h = 5 if not self.engine else 3
        w = 40 if not self.engine else len(self.prompt)+2

        console.draw_frame(x,y,w,h, fg=color.white, bg=color.black)
        console.print_box(x+1,y+1,w-2,3,self.prompt, fg=color.offwhite, bg=color.black)
        console.print_box(x,y+h-1,w,1,"(y/n)",alignment=tcod.CENTER, fg=color.white)

    def ev_keydown(self,event):
        if event.sym == tcod.event.K_n:
            if self.cancel_callback:
                return cancel_callback()
            else:
                return self.parent
        elif event.sym == tcod.event.K_y:
            return self.callback()


class TutorialConfirm(EventHandler):
    def __init__(self,engine,prompt):
        super().__init__(engine)
        engine.meta.log_tutorial_event(prompt)
        self.prompt = {
            'new game': "Welcome to Basilisk!\n\nMovement controls are shown in the bottom left. You can also move with the numpad and wait with (.) or (5). Try moving over a vowel to pick it up.\n\n(If you wish to disable these tips, access the menu with ESC)",
            'pick up': "You got your first item and added it to your tail!\n\nWhen you move, it will follow you around the dungeon. You can also see it in the panel on the right or in your inventory when you press i.\n\nNow that you've picked it up, you can't drop it, but you can digest or spit it by pressing d or s. It will also break when damaged.",
            'word mode 2': "You're back in WORD MODE!\n\nWhen you're in WORD MODE you can see what your enemies will do before they act, and any identified consonants in your inventory will boost one of your 4 stats.\n\nWhen you aren't in WORD MODE you only see enemies' field of movement, and they'll decide what to do after you act.\n\nIf you want to know whether a word is in our dictionary, press o and type it in!\n\nPress ? at any time to review this and for more information.",
            'enemy': "You spotted an enemy!\n\nOnce it can see a clear path to you, it will chase you relentlessly, making a bee-line for the closest piece of you. It will prefer your head over your tail, so be careful! It only takes one hit to the head to kill you.\n\nBut worry not, mighty Basilisk. Simply move your head into an adjacent tile to constrict and paralyze the enemy!",
            'constrict': "Yes, constrict your prey!\n\nAs long as some part of you touches the enemy, it can't act and its health is reduced! This health reduction is always equal to the number of tiles around it that you occupy plus your TAIL stat.\n\nSo if you release your prey, its health comes right back. But the more completely you encircle it, the more deadly your constriction becomes.\n\nTake its health below zero to kill it!",
            'consonant': "Oh, look! You've picked up a consonant!\n\nIf you check your inventory (i) you'll notice that this item is unidentified, but you can learn what it does by digesting it (d) or spitting it (s).\n\nYou can always check your compendium (p) for more info. It tracks all the items you've identified since you entered this dungeon.",
            'stairs': "Stairs ahoy!\n\nRest your head on that tile and press > to descend to the next level. Don't worry about leaving your tail vulnerable; these stairs are swift.",
            'snakestone': "You've just entered a patch of snakestone!\n\nEnemies can't attack what's in it or follow you through it. Use it wisely.",
            'new game 2': "Welcome back to the dungeon!\n\nYes, back to the very beginning. You'll notice, however, that your compendium (p) still knows about the consonants you found in your past lives. It just doesn't know what they look like yet.",
            'stat boost': "Woah, lookin good! One of your stats is buffed.\n\nCheck the panel in the bottom right: each colored-in letter represents +1 to the relevant stat. A number next to a stat is how many turns you have left on your buff.\n\nFind more info in the help (?) menu.",
            '100 turns': "Hey, have you tried using your mouse or the examination cursor (x) to look around?\n\nIf you click a tile or press the relevant index key (see the bottom left panel) while a tile is highlighted, you can learn more about your surroundings."
        }[prompt]

    def on_render(self,console):
        super().on_render(console)

        width = 50
        height = console.get_height_rect(
            0,0,width-2,40,self.prompt
        )

        console.draw_frame(0,38-height,width,height+2,fg=color.offwhite,bg=color.black)
        console.print_box(1,39-height,width-2,height,self.prompt,fg=color.offwhite,bg=color.black)
        console.print_box(0,39,width,1, "(space)", fg=color.offwhite,bg=color.black,alignment=tcod.CENTER)

    def ev_keydown(self,event):
        if event.sym == tcod.event.K_SPACE:
            self.engine.confirmed_in_combat = True
            return MainGameEventHandler(self.engine)


class ConfirmCombatHandler(EventHandler):
    def on_render(self,console):
        super().on_render(console)
        console.draw_frame(0,37,16,3,fg=color.offwhite, bg=color.black)
        console.print_box(1,38,79,1, "ENEMIES NEARBY", fg=color.offwhite, bg=color.black)
        console.print_box(0,39,16,1, "(space)", fg=color.offwhite, bg=color.black,alignment=tcod.CENTER)

    def ev_keydown(self,event):
        if event.sym == tcod.event.K_SPACE:
            self.engine.confirmed_in_combat = True
            return MainGameEventHandler(self.engine)


class GameOverEventHandler(EventHandler):
    def __init__(self,engine,loss=True):
        super().__init__(engine)
        if os.path.exists(utils.get_resource("savegame.sav")):
            os.remove(utils.get_resource("savegame.sav"))  # Deletes the active save file.
        snapshots = glob.glob(utils.get_resource("snapshot_*.sav"))
        for s in snapshots:
            os.remove(s)

        event = 'lose' if loss else 'win'
        self.engine.history.append((event,self.engine.player.cause_of_death,self.engine.turn_count))
        self.engine.log_run()

    def on_quit(self) -> None:
        return GameOverStatScreen(self.engine)
        
    def ev_quit(self, event: tcod.event.Quit):
        return self.on_quit()
        
    def ev_keydown(self, event: tcod.event.KeyDown):
        if event.sym == tcod.event.K_ESCAPE:
            return self.on_quit()


class VictoryEventHandler(GameOverEventHandler):
    def __init__(self,engine):
        super().__init__(engine,False)
        self.render_tally = 0
        self.frame_interval = 60
        self.min_frame_interval = 1
        self.start_animation()

    def start_animation(self):
        p = self.engine.player
        inv = p.inventory.items

        last_seg = [i for i in inv if all(x in i.get_adjacent_actors() for x in [self.engine.boss,p])][-1]
        if last_seg is not inv[-1]:
            inv[inv.index(last_seg)+1].die()

        self.engine.message_log.add_message("Congratulations! You've trapped the One Below and saved the world from annihilation!", color.purple)

        raise exceptions.VictoryAnimation(self)

    def animate_frame(self):
        p = self.engine.player
        t = self.engine.player.inventory.items[-1]
        p.move(t.x-p.x,t.y-p.y)

    def on_render(self,console):
        super().on_render(console)
        self.render_tally += 1
        if self.render_tally % self.frame_interval == 0:
            self.render_tally = 0
            self.frame_interval = max(min(self.frame_interval // 1.1, self.frame_interval-1),self.min_frame_interval)
            self.animate_frame()


class GameOverStatScreen(EventHandler):
    def ev_quit(self, event: tcod.event.Quit):
        return self.on_quit()
        
    def ev_keydown(self, event: tcod.event.KeyDown):
        if event.sym == tcod.event.K_ESCAPE:
            return self.on_quit()

    def on_quit(self):
        raise exceptions.QuitWithoutSaving()

    def on_render(self,console):
        history = self.engine.history
        words = [i[1] for i in history if i[0] == 'form word']
        uses = [i for i in history if i[0] in ['spit item','digest item']]
        kills = [i for i in history if i[0] == 'kill enemy']
        pname = words[-1] if words else ''

        if not self.engine.player.is_alive:
            console.print(1,1,"R.I.P.  "+' '*len(pname)+' the Basilisk',color.red)
            console.print(8,1,f"@{pname}",color.player)

            cod = self.engine.player.cause_of_death
            a = 'a ' if cod != 'suffocation' else ''
            console.print(1,3,f"Died on floor {self.engine.game_map.floor_number} to {a}{cod}",color.red)

        else:
            console.print(1,1,"Congratulations  "+' '*len(pname)+' the Basilisk',color.purple)
            console.print(17,1,f"@{pname}",color.player)

            console.print(1,3,f"Constricted the One Below!",color.purple)
        
        console.print(1,5,"Along the way:",color.offwhite)
        console.print(3,6,f"- Used {len(uses)} items",color.offwhite)
        console.print(3,7,f"- Killed {len(kills)} foes",color.offwhite)
        console.print(3,8,f"- Formed {len(set(words))} words",color.offwhite)

        lword = sorted(words,key=lambda x:len(x))[-1] if words else "n/a"
        console.print(1,10,f"Longest word: {lword}")
        console.print(1,12,f"Turn count: {self.engine.turn_count}")

        y = 3
        seen = set()
        for w in [i for i in reversed(words) if not (i in seen or seen.add(i))]:
            console.print(52,y,f"@{w}",tuple(c//2 for c in color.player))
            y += 1
            if y > 39:
                break


class HistoryViewer(EventHandler):
    """Print the history on a larger window which can be navigated."""

    def __init__(self, engine: Engine):
        super().__init__(engine)
        self.log_length = len(engine.message_log.messages)
        self.cursor = self.log_length - 1

    def on_render(self, console: tcod.Console) -> None:
        super().on_render(console)  # Draw the main state as the background.

        log_console = tcod.Console(console.width - 6, console.height - 6)

        # Draw a frame with a custom banner title.
        log_console.draw_frame(0, 0, log_console.width, log_console.height)
        log_console.print_box(
            0, 0, log_console.width, 1, "┤Message history├", alignment=tcod.CENTER
        )

        # Render the message log using the cursor parameter.
        self.engine.message_log.render_messages(
            log_console,
            1,
            1,
            log_console.width - 2,
            log_console.height - 2,
            self.engine.message_log.messages[: self.cursor + 1],
            False
        )
        log_console.blit(console, 3, 3)

    def ev_keydown(self, event: tcod.event.KeyDown) -> Optional[MainGameEventHandler]:
        # Fancy conditional movement to make it feel right.
        if event.sym in CURSOR_Y_KEYS:
            adjust = CURSOR_Y_KEYS[event.sym]
            if adjust < 0 and self.cursor == 0:
                # Only move from the top to the bottom when you're on the edge.
                self.cursor = self.log_length - 1
            elif adjust > 0 and self.cursor == self.log_length - 1:
                # Same with bottom to top movement.
                self.cursor = 0
            else:
                # Otherwise move while staying clamped to the bounds of the history log.
                self.cursor = max(0, min(self.cursor + adjust, self.log_length - 1))
        elif event.sym == tcod.event.K_HOME:
            self.cursor = 0  # Move directly to the top message.
        elif event.sym == tcod.event.K_END:
            self.cursor = self.log_length - 1  # Move directly to the last message.
        else:  # Any other key moves back to the main game state.
            return MainGameEventHandler(self.engine)
        return None


class BigHistoryViewer(HistoryViewer):
    def __init__(self,engine):
        super().__init__(engine)
        self.log_length = len(engine.history)
        self.cursor = self.log_length - 1

    def on_render(self, console: tcod.Console) -> None:
        super().on_render(console)  # Draw the main state as the background.

        log_console = tcod.Console(console.width - 6, console.height - 6)

        # Draw a frame with a custom banner title.
        log_console.draw_frame(0, 0, log_console.width, log_console.height)
        log_console.print_box(
            0, 0, log_console.width, 1, "┤Game history├", alignment=tcod.CENTER
        )

        log_console.print_box(
            1,1, log_console.width-2, log_console.height-2, self.make_content(log_console.height-2),color.offwhite
        )

        log_console.blit(console, 3, 3)

    def make_content(self,height):
        if len(self.engine.history) < 1:
            return ''

        history = self.engine.history[:self.cursor+1]
        history = history if len(history) <= height else history[self.cursor+1-height:self.cursor+1]

        col_widths = [
            max(len(str(i[2])) for i in history)+1,
            max(len(i[0]) for i in history)+1
        ]

        content = []

        for i in history:
            tc = str(i[2]) + ' '*(col_widths[0] - len(str(i[2])))
            ty = i[0] + ' '*(col_widths[1] - len(i[0]))

            content.append(tc+ty+str(i[1]))

        return "\n".join(content)


class InventoryEventHandler(AskUserEventHandler):
    """This handler lets the user select an item.

    What happens then depends on the subclass.
    """

    TITLE = "<missing title>"
    tooltip = "(d)igest/(s)pit"

    def __init__(self, engine: Engine, i_filter=lambda x: True):
        super().__init__(engine)
        self.items = [i for i in engine.player.inventory.items if i_filter(i)]
        self.inventory_length = len(self.items)
        self.cursor = 0
        self.frame_width = max(len(i) for i in (self.TITLE, range(31), self.tooltip) if i is not None)+4
        if engine.player.x <= 30:
            self.frame_x = 80-self.frame_width-1
        else:
            self.frame_x = 1
        self.frame_y = 1
        self.show_spit = self.show_digest = self.show_passive = True

    @property
    def highlighted_item(self) -> Optional[Item]:
        if self.inventory_length > max(self.cursor,0):
            return self.items[self.cursor]
        return None

    def get_frame_height(self, console: tcod.Console) -> int:
        inner = 3
        if self.highlighted_item:
            if self.highlighted_item.identified:
                if self.show_digest:
                    self.digest_height = console.get_height_rect(
                        self.frame_x+10,self.frame_y+1,self.frame_width-11,47-inner,self.highlighted_item.edible.description
                    )
                    inner += self.digest_height + 1

                if self.show_spit:
                    self.spit_height = console.get_height_rect(
                        self.frame_x+10,self.frame_y+1,self.frame_width-11,47-inner,self.highlighted_item.spitable.description
                    )
                    inner += self.spit_height + 1

                if self.show_passive and self.highlighted_item.stat:
                    self.passive_height = console.get_height_rect(
                        self.frame_x+10,self.frame_y+1,self.frame_width-11,47-inner,f"+1 to AAAA while in WORD MODE"
                    )
                    inner += self.passive_height + 1

            if self.highlighted_item.flavor:
                self.flavor_height = console.get_height_rect(
                    self.frame_x+1,self.frame_y+1,self.frame_width-2,47-inner,self.highlighted_item.flavor
                )
                inner += self.flavor_height + 1

        return inner

    def highlight_item(self, console: tcod.Console):
        x, y = self.highlighted_item.xy
        console.tiles_rgb["bg"][x, y] = color.white
        console.tiles_rgb["fg"][x, y] = color.black

    def render_item_panel(self, console: tcod.Console):
        # print main popup
        console.draw_frame(
            x=self.frame_x,
            y=self.frame_y,
            width=self.frame_width,
            height=self.frame_height,
            title=self.TITLE,
            clear=True,
            fg=(50, 150, 50),
            bg=(0, 0, 0),
        )

        y = self.frame_y+1
        x = self.frame_x+1

        if self.highlighted_item:
            console.print(x,y, self.highlighted_item.label, self.highlighted_item.color)
            y += 2

            if self.highlighted_item.identified:
                if self.show_digest:
                    console.print(x,y,"Digest:",color.offwhite)
                    self.print_multicolor_box(console, x+9,y,self.frame_width-11,self.frame_height-2,self.highlighted_item.edible.description_parts)
                    y += self.digest_height+1
                
                if self.show_spit:
                    console.print(x,y,"Spit:",color.offwhite)
                    self.print_multicolor_box(console, x+9,y,self.frame_width-11,self.frame_height-2,self.highlighted_item.spitable.description_parts)
                    y += self.spit_height+1

                if self.show_passive and self.highlighted_item.stat:
                    console.print(x,y,"Passive:",color.offwhite)
                    console.print_box(x+9,y,self.frame_width-11,self.frame_height-2,f"+1 to {self.highlighted_item.stat} while in WORD MODE",color.offwhite)
                    y += self.passive_height+1
            
            if self.highlighted_item.flavor:
                console.print_box(x,y,self.frame_width-2,self.frame_height-2,self.highlighted_item.flavor,color.grey)
        else:
            console.print(self.frame_x+1,self.frame_y+1,"(None)", color.grey)

    def render_items_drawer(self, console: tcod.Console):
        console.draw_frame(
            x=self.frame_x+1,
            y=self.frame_y+self.frame_height-1,
            width=self.frame_width-2 if self.frame_width % 2 != 0 else self.frame_width - 3,
            height=3,
            clear=True,
            fg=(100,100,100),
            bg=(0,0,0)
        )

        space = self.frame_width-6 if self.frame_width % 2 == 0 else self.frame_width-5
        start_at = min(self.cursor - (space/2), len(self.items)-space-1)
        end_at = max(start_at,0) + space
        i = 0
        for k, v in enumerate(self.items):
            if k < start_at:
                continue
            if k > end_at:
                break
            fg = color.black if v is self.highlighted_item else v.color
            bg = color.white if v is self.highlighted_item else color.black
            console.print(self.frame_x+2+i, self.frame_y+self.frame_height, v.char, fg=fg, bg=bg)
            i += 1

    def render_tooltip(self, console: tcod.Console):
        w = self.frame_width if self.frame_width % 2 == 0 else self.frame_width - 1
        ttw = len(self.tooltip) if len(self.tooltip) % 2 == 0 else len(self.tooltip) - 1
        ttx = int(self.frame_x + (w/2) - (ttw/2))
        console.print(ttx, self.frame_y+self.frame_height-1, self.tooltip)

    def render_menu(self, console: tcod.Console):
        self.frame_height = self.get_frame_height(console)

        if self.highlighted_item:
            self.render_items_drawer(console)
            self.highlight_item(console)

        self.render_item_panel(console)

        if self.tooltip:
            self.render_tooltip(console)


    def on_render(self, console: tcod.Console) -> None:
        super().on_render(console)
        self.render_menu(console)


    def ev_keydown(self, event: tcod.event.KeyDown) -> Optional[MainGameEventHandler]:
        if len(self.items) < 1:
            return super().ev_keydown(event)

        # Scroll through inventory
        if event.sym in CURSOR_X_KEYS:
            adjust = CURSOR_X_KEYS[event.sym]
            if adjust < 0 and self.cursor == 0:
                # Only move from the top to the bottom when you're on the edge.
                self.cursor = max(self.inventory_length - 1, 0)
            elif adjust > 0 and self.cursor == self.inventory_length - 1:
                # Same with bottom to top movement.
                self.cursor = 0
            else:
                # Otherwise move while staying clamped to the bounds of the history log.
                self.cursor = max(0, min(self.cursor + adjust, self.inventory_length - 1))

            return None

        # Select item
        elif event.sym in CONFIRM_KEYS and self.highlighted_item:
            return self.on_item_selected(self.highlighted_item)

        elif event.sym in (tcod.event.K_d, tcod.event.K_s):
            return self.on_item_used(self.highlighted_item, event)

        return super().ev_keydown(event)


    def on_item_selected(self, item: Item) -> Optional[ActionOrHandler]:
        """Called when the user selects a valid item."""
        return None

    def on_item_used(self, item: Item, event: tcod.event.KeyDown) -> Optional[ActionOrHandler]:
        return None

    def spit_item(self, item):
        return item.spitable.get_throw_action(self.engine.player)

    def eat_item(self, item):
        return item.edible.get_eat_action(self.engine.player)


class CompendiumHandler(AskUserEventHandler):
    def __init__(self, engine: Engine):
        super().__init__(engine)

        # vowels take up one index
        self.rows = [
            (0,'a','vowel',color.vowel,self.engine.game_map.vowel),
            (0,'e','vowel',color.vowel,self.engine.game_map.vowel),
            (0,'i','vowel',color.vowel,self.engine.game_map.vowel),
            (0,'o','vowel',color.vowel,self.engine.game_map.vowel),
            (0,'u','vowel',color.vowel,self.engine.game_map.vowel),
            (-1,'','',color.black,None),
            (1,'y','vowel?',color.vowel,self.engine.game_map.vowel)
        ]
        i_f = self.engine.game_map.item_factories
        i_f.sort(key=lambda x: x.char)

        # identified items come next
        index = 2
        identified_items = [(i+index,c.char,c.name,c._color,c) for i,c in enumerate(j for j in i_f if j._identified and j.char != 'y')]
        # then known unidentified items
        index += len(identified_items)
        def known(item):
            for r in self.engine.meta.old_runs:
                for e in r:
                    if e[1] == item.name:
                        return True
            return False
        i_f.sort(key=lambda x:x.name)
        known_items = [(i+index,'?',c.name,color.grey,c) for i,c in enumerate(j for j in i_f if not j._identified and known(j) and j.char != 'y')]
        # then the rest
        index += len(known_items)
        unknown_items = [(i+index,'?','???',color.grey,c) for i,c in enumerate(j for j in i_f if not j._identified and not known(j) and j.char != 'y')]

        spacer = [(-1,'','',color.black,None)]
        self.rows += spacer + identified_items
        if len(identified_items) > 0:
            self.rows += spacer
        self.rows += known_items
        if len(known_items) > 0:
            self.rows += spacer
        self.rows += unknown_items

        self.cursor = 0

    def on_render(self,console):
        super().on_render(console)
        c1 = color.offwhite
        c2 = color.black

        w = max(len(i[2]) for i in self.rows)+4
        h = len(self.rows)+2

        item = [i for i in self.rows if i[0] == self.cursor][0]
        x = w
        i = self.rows.index(item)
        y = min(max(2,i), 15)
        console.draw_frame(x,y,35,17,fg=c1,bg=c2)
        console.print_box(x,y,35,1,item[2],alignment=tcod.CENTER,fg=c2,bg=c1)
        console.print(x+1,y+1,"Digest:",fg=c1)
        console.print(x+1,y+5,"Spit:",fg=c1)
        console.print(x+1,y+9,"Passive:",fg=c1)

        if item[2] == '???':
            digest,spit,passive = ([('???',color.grey)],[('???',color.grey)],[('???',color.grey)])
        else:
            digest,spit = item[4].edible.description_parts, item[4].spitable.description_parts
            if not item[2].startswith('vowel'):
                passive = [("+1 to ",color.offwhite),(item[4].stat,color.stats[item[4].stat]),(" while in WORD MODE",color.offwhite)]
            else:
                passive = [('n/a',color.grey)]


        self.print_multicolor_box(console,x+11,y+1,22,3,digest)
        self.print_multicolor_box(console,x+11,y+5,22,3,spit)
        self.print_multicolor_box(console,x+11,y+9,22,3,passive)

        if item[1] != "y":
            console.print_box(x+1,y+13,33,3,item[4]._flavor,fg=color.grey)
        else:
            console.print_box(x+1,y+13,33,3,"Careful -- sometimes y is a vowel, but each game it can also duplicate one other consonant.",fg=color.grey)

        console.draw_frame(1,1,w,h,fg=c1,bg=c2)
        console.print_box(1,h,w,1,"(↑/↓)",fg=c1,bg=c2,alignment=tcod.CENTER)
        for i,r in enumerate(self.rows):
            console.print(2,2+i,r[1],fg=r[3],bg=c2)
            fg,bg = (c1,c2) if not self.cursor == r[0] else (c2,c1)
            console.print(4,2+i,r[2],fg=fg,bg=bg)

    def ev_keydown(self,event):
        if event.sym in CURSOR_Y_KEYS:
            self.cursor += CURSOR_Y_KEYS[event.sym]
            m = max(i[0] for i in self.rows)
            if self.cursor > m:
                self.cursor = 0
            if self.cursor < 0:
                self.cursor = m
            return
        return super().ev_keydown(event)



class InventorySelectHandler(InventoryEventHandler):
    TITLE = "Select a segment"

    def on_item_used(self, item: Item, event: tcod.event.KeyDown) -> Optional[ActionOrHandler]:
        if event.sym == tcod.event.K_s:
            return self.spit_item(item)

        elif event.sym == tcod.event.K_d:
            return self.eat_item(item)


class InventorySpitHandler(InventoryEventHandler):
    """Handle using an inventory item."""

    TITLE = "Select a segment to spit"
    tooltip = None

    def __init__(self,engine):
        super().__init__(engine)
        self.show_digest = self.show_passive = False

    def on_item_selected(self, item: Item) -> Optional[ActionOrHandler]:
        """Return the action for the selected item."""
        return self.spit_item(item)


class InventoryDigestHandler(InventoryEventHandler):
    TITLE = "Select a segment to digest"
    tooltip = None

    def __init__(self,engine):
        super().__init__(engine)
        self.show_spit = self.show_passive = False

    def on_item_selected(self, item: Item) -> Optional[ActionOrHandler]:
        return self.eat_item(item)


class InventoryIdentifyHandler(InventoryEventHandler):
    TITLE = "Select a segment to identify"
    tooltip = None

    def __init__(self,engine: Engine, identifier: Item):
        super().__init__(engine, lambda x:x.identified == False and x.char != identifier.char)
        self.identifier = identifier

    def on_item_selected(self, item: Optional[Item]) -> Optional[ActionOrHandler]:
        return actions.ItemAction(self.engine.player, self.identifier, target_item=item)

    def on_exit(self):
        if self.identifier.identified:
            return super().on_exit()

class InventoryRearrangeHandler(InventoryEventHandler):
    TITLE = "Rearrange yourself"
    tooltip = None

    def __init__(self, engine: Engine, rearranger: Item):
        super().__init__(engine, lambda x:x is not rearranger)
        self.rearranger = rearranger
        self.selected_items = []
        self.all_items = self.engine.player.inventory.items[:]

    def on_exit(self):
        if self.rearranger.identified:
            return super().on_exit()

    def render_menu(self, console: tcod.Console):
        self.frame_height = 3

        self.render_items_drawer(console)
        self.render_input_panel(console)

    def render_input_panel(self, console: tcod.Console):
        console.draw_frame(
            x=self.frame_x,
            y=self.frame_y,
            width=self.frame_width,
            height=self.frame_height,
            clear=True,
            fg=(50,150,50),
            bg=(0,0,0)
        )

        chars = ''.join([i.char for i in self.selected_items])+'_'*len(self.items)
        console.print(self.frame_x+1,self.frame_y+1,chars,fg=color.grey)

    def on_item_selected(self, item: Optional[Item]):
        self.items.remove(item)
        self.inventory_length -= 1
        self.selected_items.append(item)
        if self.cursor > len(self.items)-1:
            self.cursor = len(self.items)-1

        if len(self.items) == 0:
            return self.on_final_item_selected()

    def on_final_item_selected(self):
        new_order = self.selected_items + [self.rearranger]

        poses = [i.xy for i in self.all_items]
        for s, segment in enumerate(new_order):
            new_order[s].place(*poses[s])

        self.engine.player.inventory.items = new_order

        return actions.ItemAction(self.engine.player, self.rearranger)


class OrderPickupHandler(InventoryRearrangeHandler):
    TITLE="Choose pickup order"

    def __init__(self,engine):
        super().__init__(engine,None)
        self.selected_items = engine.player.inventory.items[:]
        self.items = [i for i in self.engine.game_map.items if i.xy == engine.player.xy and not i in engine.player.inventory.items]
        self.inventory_length = len(self.items)

    def on_exit(self):
        pass

    def on_final_item_selected(self):
        items = []
        for i in self.selected_items:
            if i in self.engine.player.inventory.items:
                continue
            items.append(i)
        return actions.PickupAction(self.engine.player, items)


class SelectIndexHandler(AskUserEventHandler):
    """Handles asking the user for an index on the map."""

    def __init__(self, engine: Engine):
        """Sets the cursor to the player when this handler is constructed."""
        super().__init__(engine)
        player = self.engine.player
        engine.mouse_location = player.x, player.y

    def on_render(self, console: tcod.Console) -> None:
        """Highlight the tile under the cursor."""
        super().on_render(console)
        x, y = self.engine.mouse_location
        console.tiles_rgb["bg"][x, y] = color.white
        console.tiles_rgb["fg"][x, y] = color.black

    def ev_keydown(self, event: tcod.event.KeyDown) -> Optional[ActionOrHandler]:
        """Check for key movement or confirmation keys."""
        key = event.sym
        if key in MOVE_KEYS:
            modifier = 1  # Holding modifier keys will speed up key movement.
            if event.mod & (tcod.event.KMOD_LSHIFT | tcod.event.KMOD_RSHIFT):
                modifier *= 5
            if event.mod & (tcod.event.KMOD_LCTRL | tcod.event.KMOD_RCTRL):
                modifier *= 10
            if event.mod & (tcod.event.KMOD_LALT | tcod.event.KMOD_RALT):
                modifier *= 20

            x, y = self.engine.mouse_location
            dx, dy = MOVE_KEYS[key]
            x += dx * modifier
            y += dy * modifier
            # Clamp the cursor index to the map size.
            x = max(0, min(x, self.engine.game_map.width - 1))
            y = max(0, min(y, self.engine.game_map.height - 1))
            self.engine.mouse_location = x, y
            return None
        elif key in CONFIRM_KEYS:
            return self.on_index_selected(*self.engine.mouse_location)
        return super().ev_keydown(event)

    def ev_mousebuttondown(self, event: tcod.event.MouseButtonDown) -> Optional[ActionOrHandler]:
        """Left click confirms a selection."""
        if self.engine.game_map.in_bounds(*event.tile):
            if event.button == 1:
                return self.on_index_selected(*event.tile)
        return super().ev_mousebuttondown(event)

    def on_index_selected(self, x: int, y: int) -> Optional[ActionOrHandler]:
        """Called when an index is selected."""
        raise NotImplementedError()


class LookHandler(SelectIndexHandler):
    """Lets the player look around using the keyboard."""

    def __init__(self, engine, start_cycle=False):
        super().__init__(engine)
        if len(engine.fov_actors) and start_cycle:
            engine.mouse_location = engine.fov_actors[0].xy

    def on_index_selected(self, x: int, y: int) -> MainGameEventHandler:
        """Return to main handler."""
        return MainGameEventHandler(self.engine)

    def ev_keydown(self, event: tcod.event.KeyDown):
        key = event.sym
        modifier = event.mod
        
        if modifier & (tcod.event.KMOD_LSHIFT | tcod.event.KMOD_RSHIFT):
            if key in ALPHA_KEYS and len(self.engine.mouse_things) > ALPHA_KEYS[key]:
                return InspectHandler(self.engine, key, self, 'mouse', self.engine.mouse_location)
            return None

        if key == tcod.event.K_TAB:
            # get current fov actor if any
            if len(self.engine.mouse_things) and self.engine.mouse_things[0] in self.engine.fov_actors:
                i = self.engine.fov_actors.index(self.engine.mouse_things[0])
                i = i+1 if i+1 < len(self.engine.fov_actors) else 0
                actor = self.engine.fov_actors[i]
            else:
                actor = self.engine.player
            self.engine.mouse_location = actor.xy
            return None

        return super().ev_keydown(event)

class SingleRangedAttackHandler(SelectIndexHandler):
    """Handles targeting a single enemy. Only the enemy selected will be affected."""

    def __init__(
        self, engine: Engine, callback: Callable[[Tuple[int, int]], Optional[Action]], anywhere=False
    ):
        super().__init__(engine)

        self.callback = callback
        self.anywhere = anywhere

    def on_index_selected(self, x: int, y: int) -> Optional[Action]:
        return self.callback((x, y))

class SingleProjectileAttackHandler(SelectIndexHandler):
    def __init__(
        self, engine: Engine, callback: Callable[[Tuple[int,int]], Optional[Action]], seeking="anything", walkable=True, thru_tail=True, pathfinder=None
    ):
        super().__init__(engine)
        self.callback = callback
        self.seeking = seeking
        self.walkable=walkable
        self.thru_tail = thru_tail
        self.pathfinder = pathfinder if pathfinder else self.engine.player.ai.get_path_to

    @property
    def path_to_target(self):
        x,y = self.engine.mouse_location
        if self.walkable and not self.engine.game_map.visible[x,y]:
            return None
        return self.pathfinder(x,y,walkable=self.walkable,thru_tail=self.thru_tail)

    def ends_projectile_path(self, px, py):
        return (
            (self.engine.game_map.get_actor_at_location(px,py) and self.seeking in ["actor","anything"]) or
            (self.seeking == "anything" and (px,py) == self.path_to_target[-1]) or
            (not self.thru_tail and self.engine.game_map.get_blocking_entity_at_location(px,py))
        )

    def on_render(self, console: tcod.Console)->None:
        # render the line
        super().on_render(console)
        
        if not self.path_to_target:
            return

        for px,py in self.path_to_target:
            console.tiles_rgb["bg"][px, py] = color.bile
            console.tiles_rgb["fg"][px, py] = color.black
            if self.ends_projectile_path(px,py):
                break

    def on_index_selected(self, x: int, y: int) -> Optional[Action]:
        # select based on the line
        if not self.path_to_target:
            return None
        for px,py in self.path_to_target:
            if self.ends_projectile_path(px,py):
                return self.callback((px,py))


class SingleDrillingProjectileAttackHandler(SingleProjectileAttackHandler):
    def on_index_selected(self, x: int, y: int) -> Optional[Action]:
        if not self.path_to_target:
            return None
        i = min(len(self.path_to_target)-1, self.engine.fov_radius)
        return self.callback(self.path_to_target[i])



class AreaRangedAttackHandler(SelectIndexHandler):
    """Handles targeting an area within a given radius. Any entity within the area will be affected."""

    def __init__(
        self,
        engine: Engine,
        radius: int,
        callback: Callable[[Tuple[int, int]], Optional[Action]],
    ):
        super().__init__(engine)

        self.radius = radius
        self.callback = callback

    def on_render(self, console: tcod.Console) -> None:
        """Highlight the tile under the cursor."""
        super().on_render(console)

        x, y = self.engine.mouse_location
        radius = self.radius

        i = x - radius
        while i <= x+radius:
            j=y-radius
            while j <= y+radius:
                if math.sqrt((x-i)**2 + (y-j)**2) <= radius:
                    console.tiles_rgb["bg"][i, j] = color.white
                    console.tiles_rgb["fg"][i, j] = color.black
                j+=1
            i+=1

    def on_index_selected(self, x: int, y: int) -> Optional[Action]:
        return self.callback((x, y))

class PopupMessage(BaseEventHandler):
    """Display a popup text window."""

    def __init__(self, parent_handler: BaseEventHandler, text: str, vpos = 'center'):
        self.parent = parent_handler
        self.text = text
        self.vpos = vpos

    def on_render(self, console: tcod.Console) -> None:
        """Render the parent and dim the result, then print the message on top."""
        self.parent.on_render(console)
        console.tiles_rgb["fg"] //= 8
        console.tiles_rgb["bg"] //= 8
        y = console.height // 2 if self.vpos == 'center' else 0

        console.print(
            console.width // 2,
            y,
            self.text,
            fg=color.offwhite,
            bg=color.black,
            alignment=tcod.CENTER,
        )

    def ev_keydown(self, event: tcod.event.KeyDown) -> Optional[BaseEventHandler]:
        """Any key returns to the parent handler."""
        return self.parent

class PlayMenuHandler(AskUserEventHandler):
    """ Maybe left-align then pop-out new options as you go? 
    arrange as a dict with (option, sub-optionsOrMethod) tuple """
    
    def __init__(self, engine, parent, options=None, selected=None, header=None):
        super().__init__(engine)
        self.options = [
            ("Continue", self.onContinue),
            ("Options", self.onOptions),
            ("Help", self.onHelp),
            ("Save and Quit", self.onSaveAndQuit)
        ] if not options else options

        self.selected = selected if selected else 0
        self.parent = parent
        self.header = header

    def print_options(self, console):
        y = (console.height // 2) - len(self.options)
        x = console.width // 2

        if self.header:
            console.print(x, y-2, self.header, fg=color.yellow,alignment=tcod.CENTER)

        for i,o in enumerate(self.options):
            bg = (0,100,0) if i == self.selected else None

            if o[0].startswith('Confirm Combat Start'):
                s = o[0]+' (ON)' if self.engine.meta.do_combat_confirm else o[0]+' (OFF)'
            elif o[0].startswith('Tutorial Messages'):
                s = o[0]+' (ON)' if self.engine.meta.tutorials else o[0]+' (OFF)'
            else:
                s = o[0]

            console.print(x,y,s,fg=color.offwhite,bg=bg,alignment=tcod.CENTER)
            y += 2

    def on_render(self, console: tcod.Console) -> None:
        super().on_render(console)

        console.tiles_rgb["fg"] //= 8
        console.tiles_rgb["bg"] //= 8

        self.print_options(console)

    def ev_keydown(self, event: tcod.event.KeyDown) -> Optional[BaseEventHandler]:
        key = event.sym
        if key == tcod.event.K_ESCAPE:
            return self.parent

        if key in CURSOR_Y_KEYS:
            return self.scroll(CURSOR_Y_KEYS[key])

        if key in CONFIRM_KEYS:
            return self.confirm()

    def scroll(self, direction):
        self.selected += direction
        while self.selected >= len(self.options):
            self.selected -= len(self.options)
        while self.selected < 0:
            self.selected += len(self.options)

    def confirm(self):
        return self.options[self.selected][1]()

    def onContinue(self):
        return self.parent

    def onOptions(self):
        options = [
            ("Full Screen",self.onFullScreen),
            ("Confirm Combat Start",self.onCombatConfirm),
            ("Tutorial Messages",self.onTutorialMessages)
        ]
        return PlayMenuHandler(self.engine,self,options,header="OPTIONS")

    def onHelp(self):
        return PopupMessage(self, self.engine.help_text, 'top')

    def onSaveAndQuit(self):
        raise exceptions.QuitToMenu()

    def onFullScreen(self):
        raise exceptions.ToggleFullscreen()

    def onCombatConfirm(self):
        self.engine.meta.do_combat_confirm = not self.engine.meta.do_combat_confirm

    def onTutorialMessages(self):
        self.engine.meta.tutorials = not self.engine.meta.tutorials


class InspectHandler(AskUserEventHandler):
    """For inspecting things"""

    def __init__(self, engine: Engine, key, parent_handler, mode="nearby", mouse_location=(0,0)):
        super().__init__(engine)
        if mode == 'mouse':
            engine.mouse_location = mouse_location

        self.is_tile = False
        key = ALPHA_KEYS[key]

        if mode != 'nearby':
            x,y = mouse_location
            self.thing = thing = engine.mouse_things[key]
            if key == len(engine.mouse_things)-1 and (engine.game_map.visible[x,y] or engine.game_map.explored[x,y] or engine.game_map.mapped[x,y]):
                self.is_tile = True
        elif key >= len(engine.fov_actors):
            dsx,dsy = engine.game_map.downstairs_location
            self.thing = thing = engine.game_map.tiles[dsx,dsy]
            self.is_tile = True
        else:
            self.thing = thing = engine.fov_actors[key]

        if self.is_tile:
            self.title = NAMES[thing[5]]
            self.frame_color = color.grey
            self.flavor = FLAVORS[thing[6]] 

        else:
            self.title = thing.label if hasattr(thing,'ai') or thing.identified else '???'
            self.frame_color = thing._color if hasattr(thing,'ai') else thing.color
            self.flavor = thing.flavor

        self.frame_width = max(len(i) for i in (self.title, range(31)) if i is not None)+4
        if engine.player.x <= 30:
            self.frame_x = 80-self.frame_width-1
        else:
            self.frame_x = 1
        self.frame_y = 1

        self.parent = parent_handler

    def get_frame_height(self, console: tcod.Console) -> int:
        if self.thing is self.engine.player:
            return 3

        inner = console.get_height_rect(
            self.frame_x+1,self.frame_y+1,self.frame_width-2,47,self.flavor
        )+3 if self.flavor else 2

        flavor = inner

        if hasattr(self.thing, 'ai'):
            inner += 1
            if self.thing.name != "Decoy":
                inner += 3
            inner += len(self.thing.statuses)

        elif not self.is_tile and self.thing.identified:
            self.digest_height = console.get_height_rect(
                self.frame_x+10,self.frame_y+1,self.frame_width-11,47-inner,self.thing.edible.description
            )
            inner += self.digest_height

            self.spit_height = console.get_height_rect(
                self.frame_x+10,self.frame_y+1,self.frame_width-11,47-inner,self.thing.spitable.description
            )
            inner += self.spit_height
            inner += 1

            if self.thing.stat:
                self.passive_height = console.get_height_rect(
                    self.frame_x+10,self.frame_y+1,self.frame_width-11,47-inner,f"Passive: +1 to AAAA while in WORD MODE"
                )
                inner += self.passive_height
                inner += 1

        return inner if inner != flavor else inner - 1

    def render_thing_panel(self, console: tcod.Console):
        # print main popup
        console.draw_frame(
            x=self.frame_x,
            y=self.frame_y,
            width=self.frame_width,
            height=self.frame_height,
            title=self.title,
            clear=True,
            fg=self.frame_color,
            bg=(0, 0, 0),
        )

        y = self.frame_y + 1
        x = self.frame_x + 1


        if self.thing is self.engine.player:
            console.print(x,y,"It's you!",fg=color.offwhite)
            return

        if hasattr(self.thing, 'ai'):

            if self.thing.name != "Decoy":
                #print health bar
                console.print(x,y,'HP',fg=color.offwhite)
                for i in range(int(self.thing.max_char)+1):
                    if i <= int(self.thing.char):
                        bg = self.thing._color
                    elif i <= int(self.thing.base_char):
                        bg = color.statue
                    else:
                        bg = color.dark_red

                    console.print(x+4+i,y,' ',fg=None,bg=bg)
                y += 1
                #print move speed
                console.print(x,y,'SPD',fg=color.offwhite)
                for i in range(self.thing.move_speed):
                    console.print(x+4+i,y,D_ARROWS[6],fg=self.thing._color)
                y += 2
                #print ai info
            console.print(x,y,self.thing.ai.description,fg=color.offwhite)
            y += 1
            for status in self.thing.statuses:
                dur = str(status.duration)
                dur = dur if len(dur) < 2 else '!'
                console.print(x,y,f"{status.description.upper()} {dur}",fg=status.color)
                y += 1
            y += 1

        elif not self.is_tile and self.thing.identified:
            #print spit
            console.print(x,y,"Digest:",color.offwhite)
            self.print_multicolor_box(console, x+9,y,self.frame_width-11,self.frame_height-2,self.thing.edible.description_parts)
            y += self.digest_height+1
            #print digest
            console.print(x,y,"Spit:",color.offwhite)
            self.print_multicolor_box(console, x+9,y,self.frame_width-11,self.frame_height-2,self.thing.spitable.description_parts)
            y += self.spit_height+1
            #print passive
            if self.thing.stat:
                console.print(x,y,"Passive:",color.offwhite)
                console.print_box(x+9,y,self.frame_width-11,self.frame_height-2,f"+1 to {self.thing.stat} while in WORD MODE",color.offwhite)
                y += self.passive_height+1
        
        if self.flavor:
            console.print_box(x,y,self.frame_width-2,self.frame_height-2,self.flavor,color.grey)


    def render_menu(self, console: tcod.Console):
        self.frame_height = self.get_frame_height(console)
        self.render_thing_panel(console)


    def on_render(self, console: tcod.Console) -> None:
        super().on_render(console)
        self.render_menu(console)


    def ev_keydown(self, event: tcod.event.KeyDown) -> Optional[MainGameEventHandler]:
        return super().ev_keydown(event)

    def on_exit(self):
        return self.parent




