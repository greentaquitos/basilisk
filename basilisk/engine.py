from __future__ import annotations

import lzma
import pickle
import os
import time

from typing import TYPE_CHECKING

from tcod.console import Console
from tcod.map import compute_fov

from basilisk import exceptions, render_functions
from basilisk.actions import WaitAction, BumpAction
from basilisk.message_log import MessageLog
from basilisk.components.status_effect import PetrifEyes, Petrified, PhasedOut
import basilisk.color as color
from basilisk.components.ai import Constricted, Statue
from basilisk.render_order import RenderOrder
from basilisk.exceptions import Impossible
from basilisk.components.consumable import TimeReverseConsumable
from basilisk.entity import Actor

if TYPE_CHECKING:
    from basilisk.game_map import GameMap, GameWorld

import utils


class Engine:
    game_map: GameMap
    game_world: GameWorld
 
    def __init__(self, player: Actor, meta, terminal, console):
        self.message_log = MessageLog(self)
        self.mouse_location = (0, 0)
        self.player = player
        self.word_mode = False
        self.turn_count = 0
        self.boss_killed = False
        self.time_turned = False
        self.meta = meta
        self.terminal = terminal
        self.console = console
        self.confirmed_in_combat = False
        self.just_turned_back_time = False
        self.difficulty = meta.difficulty
        self.frames = 0
        self.history = [("start",self.difficulty,0)]

    def log_run(self):
        self.meta.log_run(self.history)

    @property
    def show_instructions(self):
        if not hasattr(self,"_show_instructions"):
            self._show_instructions = self.meta.c_controls
        return self._show_instructions

    @show_instructions.setter
    def show_instructions(self,new_val):
        self._show_instructions = self.meta.c_controls = new_val

    # field of view
    @property
    def fov_radius(self):
        return 8 + self.player.TONG

    # field of smell: detect presence of enemies
    @property
    def fos_radius(self):
        return 0 + self.player.TONG * 2

    # field of identity: detect enemy identity
    @property
    def foi_radius(self):
        return 0 + self.player.TONG

    @property
    def help_text(self):
        return render_functions.full_help_text

    @property
    def in_combat(self):
        for a in self.game_map.actors:
            if a.ai.intent and any(isinstance(i,BumpAction) for i in a.ai.intent) and a.name not in ["Decoy","Basilisk"]:
                return True
        return self.can_see_enemies

    @property
    def can_see_enemies(self):
        return len([a for a in self.fov_actors if not isinstance(a.ai,Statue)]) > 0

    @property
    def an_enemy_is_constricted(self):
        return len([i for i in self.game_map.actors if isinstance(i.ai,Constricted)]) > 0

    @property
    def stairs_visible(self):
        return self.game_map.visible[self.game_map.downstairs_location]

    def turn_back_time(self, turns, turner):
        turn = self.turn_count - turns
        if turn < 1:
            turn = 1

        t = 0.5/turns

        for i in reversed(range(turn,self.turn_count)):
            with open(utils.get_resource(f"snapshot_{i}.sav"), "rb") as f:
                engine = pickle.loads(lzma.decompress(f.read()))
            assert isinstance(engine, Engine)
            engine.game_map._next_id = self.game_map._next_id
            engine.game_map.item_factories = self.game_map.item_factories
            self.game_map = engine.game_map
            self.game_map.engine = self
            self.player = engine.player
            self.animation_beat(t)

        turner.identified = True

        for i in [e for e in self.game_map.entities if e.id == turner.id]:
            if i in self.player.inventory.items:
                i.edible.consume()
                i.edible.snake()
            else:
                i.consume()

        self.check_word_mode()
        self.just_turned_back_time = True


    def save_turn_snapshot(self):
        self.save_as(utils.get_resource(f"snapshot_{self.turn_count}.sav"))
        utils.del_old_snapshots(self.turn_count)

    def check_word_mode(self):
        if len(self.player.inventory.items) < 1:
            self.word_mode = False
            return
        p_word = ''.join([i.char for i in self.player.inventory.items])
        self.word_mode = self.is_valid_word(p_word)
        if self.word_mode:
            self.history.append(('form word',p_word,self.turn_count))

    def is_valid_word(self,word):
        return word in open(utils.get_resource("words.txt")).read().splitlines()

    def handle_enemy_turns(self) -> None:
        enemies = sorted(set(self.game_map.actors) - {self.player}, key=lambda x: x.id)

        if not self.word_mode:
            for entity in enemies:
                if not isinstance(entity.ai.intent[0],WaitAction):
                    entity.ai.clear_intent()

        # enemy pre turns
        for entity in enemies:
            if entity.ai:
                entity.pre_turn()

        # enemy turns
        for entity in enemies:
            if entity.ai:
                # visible enemies during petrifeyes have no intent
                if (
                    any(isinstance(s,PetrifEyes) for s in self.player.statuses) and 
                    self.game_map.visible[entity.x,entity.y] and 
                    not isinstance(entity.ai, Constricted)
                ):
                    entity.ai.clear_intent()
                    continue

                # petrified and phased out enemies have no intent
                if (
                    (
                        any(isinstance(s,Petrified) for s in entity.statuses) or
                        any(isinstance(s,PhasedOut) for s in entity.statuses)
                    ) and
                    not isinstance(entity.ai, Constricted)
                ):
                    entity.ai.clear_intent()
                    continue

                # the rest do their thing
                try: 
                    entity.ai.perform()
                except exceptions.Impossible:
                    pass

                if not self.player.is_alive:
                    return

        # enemy post-turns
        for entity in enemies:
            if entity.ai:
                entity.on_turn()

        # player post-turn
        self.player.on_turn()
        
        self.turn_count += 1
        self.save_turn_snapshot()

    def animation_beat(self,t=0.12,render=True):
        self.mouse_location = (0,0)
        if render:
            self.console.clear()
            self.render(self.console)
        self.terminal.present(self.console,integer_scaling=True,clear_color=(10,10,10))
        time.sleep(t)

    @property
    def fov(self):
        return compute_fov(
            self.game_map.tiles["transparent"],
            (self.player.x, self.player.y),
            radius=self.fov_radius,
        )

    @property
    def fov_actors(self):
        return [actor for actor in 
            sorted(list(self.game_map.actors),key=lambda a:a.id) if
            not actor is self.player and (
                self.game_map.visible[actor.x,actor.y] or 
                self.game_map.smellable(actor,True)
            )
        ]

    @property
    def mouse_things(self):
        entities = [
            e for e in self.game_map.entities if 
                (e.x,e.y) == self.mouse_location and 
                (
                    self.game_map.visible[e.x,e.y] or 
                    (self.game_map.explored[e.x,e.y] and e.render_order == RenderOrder.ITEM) or
                    self.game_map.smellable(e, True)
                )
        ]

        x,y = self.mouse_location
        if self.game_map.visible[x,y] or self.game_map.explored[x,y] or self.game_map.mapped[x,y]:
            entities += [self.game_map.tiles[x,y]]

        return entities



    def update_fov(self) -> None:
        """Recompute the visible area based on the players point of view."""
        self.game_map.visible[:] = self.fov
        # If a tile is "visible" it should be added to "explored".
        self.game_map.explored |= self.game_map.visible

    @property
    def do_turn_count(self):
        for e in self.game_map.items:
            if e.x > 72 and e.y < 5 and self.game_map.explored[e.x,e.y]:
                return False
        for x in range(72,76):
            for y in range(5):
                if self.game_map.visible[x,y]:
                    return False
        return True

    def render(self, console: Console) -> None:
        # all boxes 9 high
        # left box: 20 w (0,41)
        # mid: 40 w (21,41)
        # right: 18 w (62,41)

        self.frames += 1
        if self.frames > 1023:
            self.frames = 0

        self.game_map.render(console)

        render_functions.render_dungeon_level(
            console=console,
            dungeon_level=self.game_world.current_floor,
            location=(76,0),
            word_mode = self.word_mode,
            turn_count = self.turn_count,
            do_turn_count = self.do_turn_count
        )

        render_functions.render_player_drawer(
            console=console,
            location=(77,9),
            player=self.player,
            turn=self.turn_count,
            word_mode=self.word_mode
        )

        # MIDDLE PANEL
        self.message_log.render(console=console, x=21, y=41, width=40, height=9)

        # RIGHT PANEL
        # todo: this includes stats
        render_functions.render_status(console=console,location=(72,41),statuses=self.player.statuses, engine=self)

        # LEFT PANEL
        looking = self.mouse_location != (0,0)
        if looking:
            actor = self.game_map.get_actor_at_location(*self.mouse_location)
            if actor:
                self.game_map.print_enemy_fov(console, actor)
                self.game_map.print_intent(console, actor)
            render_functions.render_names_at_mouse_location(
                console=console, x=0, y=41, engine=self
            )

        elif self.show_instructions:
            render_functions.render_instructions(
                console=console,
                location=(0,41)
            )

        else:
            render_functions.print_fov_actors(console,self.player,(0,41))
            pass


    def save_as(self, filename: str) -> None:
        """Save this Engine instance as a compressed file."""
        meta = self.meta
        terminal = self.terminal
        console = self.console
        self.meta = None
        self.terminal = None
        self.console = None
        save_data = lzma.compress(pickle.dumps(self))
        with open(filename, "wb") as f:
            f.write(save_data)
        self.meta = meta
        self.terminal = terminal
        self.console = console