from __future__ import annotations

from typing import Iterable, Iterator, Optional, TYPE_CHECKING

import numpy as np  # type: ignore
from tcod.console import Console
from tcod.map import compute_fov
import random

from basilisk import color, tile_types
from basilisk.entity import Actor, Item
from basilisk.actions import ActionWithDirection
from basilisk.render_functions import DIRECTIONS, D_ARROWS
from basilisk.components.status_effect import ThirdEyeBlind, Petrified, PetrifEyes, PhasedOut
from basilisk.components.ai import Statue

if TYPE_CHECKING:
    from basilisk.engine import Engine
    from basilisk.entity import Entity


class GameMap:
    def __init__(
        self, engine: Engine, width: int, height: int, floor_number: int, items: Iterable, entities: Iterable[Entity] = (), vowel = None, decoy = None, game_mode = 'default'
    ):
        self.engine = engine
        self.width, self.height = width, height
        self.entities = set(entities)
        self.tiles = np.full((width, height), fill_value=tile_types.wall, order="F")

        self.visible = np.full(
            (width, height), fill_value=False, order="F"
        )  # Tiles the player can currently see
        self.explored = np.full(
            (width, height), fill_value=False, order="F"
        )  # Tiles the player has seen before
        self.mapped = np.full(
            (width, height), fill_value=False, order="F"
        )

        self.downstairs_location = (0, 0)
        self.floor_number = floor_number
        self.item_factories = items
        self.vowel = vowel
        self.decoy = decoy
        self._next_id = 1
        self.game_mode = game_mode

    @property
    def actors(self) -> Iterable[Actor]:
        """Iterate over this maps living actors."""
        return [
            entity
            for entity in self.entities
            if isinstance(entity, Actor) and entity.is_alive
        ]

    @property
    def gamemap(self) -> GameMap:
        return self

    @property
    def items(self) -> Iterator[Item]:
        yield from (entity for entity in self.entities if isinstance(entity, Item))

    @property
    def next_id(self):
        self._next_id += 1
        return self._next_id

    @property
    def boss(self):
        return [a for a in self.actors if a.is_boss][0]

    def bloody_floor(self,x,y):
        if self.tiles[x,y] == tile_types.floor:
            self.tiles[x,y] = tile_types.bloody_floor


    def smellable(self,entity: Entity, super_smell:bool=False):
        dx = entity.x-self.engine.player.x
        dy = entity.y-self.engine.player.y
        distance = max(abs(dx),abs(dy))

        if super_smell:
            return distance <= self.engine.foi_radius
        else:
            return distance <= self.engine.fos_radius


    def make_mapped(self):
        for i,row in enumerate(self.mapped):
            for j, tile in enumerate(row):
                if self.tiles[i,j] not in (tile_types.wall):
                    self.mapped[i,j] = True
                if self.tiles[i,j] == tile_types.down_stairs:
                    self.explored[i,j] = True

    
    def get_blocking_entity_at_location(
        self, location_x: int, location_y: int,
    ) -> Optional[Entity]:
        for entity in self.entities:
            if (
                entity.blocks_movement
                and entity.x == location_x
                and entity.y == location_y
            ):
                return entity

        return None

    def get_actor_at_location(self, x: int, y: int) -> Optional[Actor]:
        for actor in self.actors:
            if actor.x == x and actor.y == y and not actor.is_phased_out:
                return actor

        return None

    def get_item_at_location(self, x: int, y: int) -> Optional[Item]:
        for item in self.items:
            if item.x == x and item.y == y:
                return item

        return None

    def tile_is_walkable(self, x: int, y: int, phasing: bool = False, entities: bool = True) -> bool:
        if not self.in_bounds(x, y):
            return False
        if not self.tiles["walkable"][x, y] and not phasing:
            return False
        if self.get_blocking_entity_at_location(x, y) and entities:
            return False
        return True

    def tile_is_snakeable(self, x: int, y: int, phasing: bool = False) -> bool:
        if not self.in_bounds(x, y):
            return False
        if not self.tiles["snakeable"][x, y] and not phasing:
            return False
        if self.get_blocking_entity_at_location(x,y):
            return False
        return True

    def in_bounds(self, x: int, y: int) -> bool:
        """Return True if x and y are inside of the bounds of this map."""
        return 0 <= x < self.width and 0 <= y < self.height

    def print_intent(self, console: Console, entity: Actor, highlight: bool = False):
        if (
            any(isinstance(s,ThirdEyeBlind) for s in self.engine.player.statuses) or
            entity is self.engine.player or
            not isinstance(entity, Actor) or
            any(isinstance(s,Petrified) for s in entity.statuses) or
            any(isinstance(s,PhasedOut) for s in entity.statuses) or
            (
                any(isinstance(s,PetrifEyes) for s in self.engine.player.statuses) and
                self.visible[entity.x,entity.y]
            )
        ):
            return

        if not any(isinstance(intent, ActionWithDirection) for intent in entity.ai.intent):
            return

        if not self.engine.word_mode:
            self.print_enemy_fom(console,entity)
            return

        x, y = entity.xy

        for intent in entity.ai.intent:
            x += intent.dx
            y += intent.dy

            fg = color.intent_bg if not highlight else color.highlighted_intent_bg
            attacking_player = (x,y) == self.engine.player.xy or (x,y) in [i.xy for i in self.engine.player.inventory.items]
            fgcolor = fg if not attacking_player else color.black
            bgcolor = None if not attacking_player else color.intent_bg

            if self.visible[entity.x, entity.y] or self.visible[x, y]:
                console.print(
                    x=x,
                    y=y,
                    string=D_ARROWS[DIRECTIONS.index((intent.dx,intent.dy))],
                    fg=fgcolor,
                    bg=bgcolor
                )

    def print_enemy_fom(self, console: Console, entity: Actor):
        if not self.visible[entity.x,entity.y] and not self.smellable(entity, True):
            return

        fom = compute_fov(
            self.tiles["transparent"],
            (entity.x,entity.y),
            radius=entity.move_speed,
            light_walls=False
        )

        for x,row in enumerate(fom):
            for y,cel in enumerate(row):
                if cel and self.visible[x,y] and (x != entity.x or y != entity.y):
                    console.tiles_rgb[x,y]['bg'] = color.highlighted_fom

    def print_enemy_fov(self, console: Console, entity: Actor):
        if (
            entity is self.engine.player or
            not isinstance(entity, Actor) or
            (not self.visible[entity.x,entity.y] and not self.smellable(entity, True))
        ):
            return

        fov = compute_fov(
            self.tiles["transparent"],
            (entity.x, entity.y),
            radius=8,
            light_walls=False
        )

        for x,row in enumerate(fov):
            for y,cel in enumerate(row):
                if cel and self.visible[x,y] and (x != entity.x or y != entity.y):
                    console.tiles_rgb[x,y]['bg'] = color.highlighted_fov
                    console.tiles_rgb[x,y]['fg'] = (40,40,40)
                    #console.print(x=x,y=y,string=" ",bg=color.highlighted_fov)


    def print_actor_tile(self,actor,location,console):
        fg = actor.color
        bg = None
        string = actor.char
        x,y = location

        if self.visible[actor.x,actor.y]:
            if actor.is_phased_out:
                bg = color.purple
                fg = color.purple
            elif actor.ai.fov[self.engine.player.x,self.engine.player.y] and actor.name != "Decoy":
                bg = None
                if actor.is_constricted:
                    fg = color.black
                    bg = color.grey

        elif self.smellable(actor, True):
            bg=None

        elif self.smellable(actor):
            string = '?'
            fg = color.yellow
            bg = None

        else:
            return False

        console.print(x=x,y=y,string=string,fg=fg,bg=bg)
        return True

    def print_item_tile(self,item,location,console):
        fg = item.color
        x,y = location

        if item in self.engine.player.inventory.items or item is self.engine.player:
            if not self.tiles['snakeable'][item.x,item.y]:
                fg = color.purple
            elif not self.tiles['walkable'][item.x,item.y]:
                fg = (50,150,255)
            elif self.engine.player.is_shielded or self.engine.player.is_petrified:
                fg = color.grey
            elif self.visible[item.x,item.y]:
                fg = color.player
            else:
                fg = color.player_dark

            if item is self.engine.player and not item.is_alive:
                fg = item.color

        elif not self.visible[item.x,item.y] and self.explored[item.x,item.y]:
            fg = tuple(i//2 for i in fg)
        elif not self.visible[item.x,item.y]:
            return False

        console.print(x,y,item.char,fg=fg)
        return True

    def print_tile(self,entity,location,console):
        if isinstance(entity, Actor) and entity is not self.engine.player:
            return self.print_actor_tile(entity,location,console)
        else:
            return self.print_item_tile(entity,location,console)


    def render(self, console: Console) -> None:
        """
        Renders the map.
 
        If a tile is in the "visible" array, then draw it with the "light" colors.
        If it isn't, but it's in the "explored" array, then draw it with the "dark" colors.
        Otherwise, the default is "SHROUD".
        """
        console.tiles_rgb[0 : self.width, 0 : self.height] = np.select(
            condlist=[self.visible, self.explored, self.mapped],
            choicelist=[self.tiles["light"], self.tiles["dark"], tile_types.MAPPED],
            default=tile_types.SHROUD,
            #default=self.tiles["dark"]
        )

        entities_sorted_for_rendering = sorted(
            self.entities, key=lambda x: x.render_order.value
        )

        for entity in entities_sorted_for_rendering:
            self.print_intent(console, entity)          

        # display entities
        for entity in entities_sorted_for_rendering:
            if isinstance(entity,Actor) and entity is not self.engine.player:
                self.print_actor_tile(entity,entity.xy,console)
            elif entity is self.engine.player or entity in self.engine.player.inventory.items:
                continue
            else:
                self.print_item_tile(entity,entity.xy,console) # player counts as an item

        for i in reversed(self.engine.player.inventory.items): # print in reverse order for stair reasons
            self.print_item_tile(i,i.xy,console)

        self.print_item_tile(self.engine.player,self.engine.player.xy,console)


class GameWorld:
    """
    Holds the settings for the GameMap, and generates new maps when moving down the stairs.
    """

    def __init__(
        self,
        *,
        engine: Engine,
        map_width: int,
        map_height: int,
        current_floor: int=0,
        game_mode: str
    ):
        from basilisk.procgen import generate_item_identities
        self.game_mode = game_mode

        self.items = generate_item_identities()

        self.engine = engine

        self.map_width = map_width
        self.map_height = map_height

        self.current_floor = current_floor

    def generate_floor(self) -> None:
        from basilisk.procgen import generate_dungeon, generate_consumable_testing_ground

        self.current_floor += 1

        has_boss = self.game_mode == 'boss testing'
        mongeese = self.game_mode == 'mongoose testing'
        if self.game_mode in ['consumable testing','boss testing','mongoose testing']:
            self.engine.game_map = generate_consumable_testing_ground(engine=self.engine, items=self.items, has_boss=has_boss, mongeese=mongeese)
            return

        self.engine.game_map = generate_dungeon(
            map_width=self.map_width,
            map_height=self.map_height,
            engine=self.engine,
            floor_number=self.current_floor,
            items=self.items,
        )